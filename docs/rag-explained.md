# RAG, explained — and how Wikify's actually works

A from-scratch explanation, written against the real system on `wikify.localhost`.
Every number here was measured, not estimated.

---

## 1. The problem RAG solves

A language model only knows what it read during training. It has never seen your ICAI
booklet. There are three ways to fix that:

| Approach | Why it fails here |
|---|---|
| Retrain the model on your documents | Expensive, slow, and it still can't tell you **where** an answer came from |
| Paste the whole document into every question | Your ICAI PDF is **528,336 characters**. Too big, too slow, and models get measurably worse at finding details in a huge pile |
| **Retrieve only the relevant bits, then ask** | ← this is RAG |

**RAG = Retrieval-Augmented Generation.** It is genuinely only two steps:

1. **Retrieve** — find the handful of passages that could answer this question.
2. **Generate** — hand *only* those passages to the model and say "answer using these,
   and cite them."

Everything else in this document is engineering to make step 1 actually work. **Step 1 is
where RAG systems live or die.** If retrieval hands over the wrong pages, no model on
earth can save the answer.

---

## 2. Three different ways to "find" — and when each fails

This is the part most people skip, and it's the part that matters.

### Vector search (semantic)

Text is converted into a list of numbers — a **vector** — that encodes *meaning*. Similar
meanings land close together in that space, so you find answers by measuring distance.

Real example from this codebase: searching *"who works on the UI"* correctly returned a
chunk about a **Frontend Engineer (Vue, Vite)** — sharing **not one word** with the query.

- **Good at:** paraphrase, synonyms, fuzzy "tell me about…" questions.
- **Fails at:** exact tokens. It may happily blur `194-I` and `194-J`. For tax law that is
  not a small error.
- **Also fails at:** completeness. It returns the top *k* most similar. It has no idea
  whether the right answer is 3 items or 300.

### Keyword search (BM25 / full-text)

Classic text matching, the way search engines worked before embeddings.

- **Good at:** `115BAC`, `₹2,40,000`, section numbers, proper nouns, acronyms.
- **Fails at:** paraphrase. Asking "how much extra tax do the rich pay" won't match a
  heading that says "SURCHARGE".

### Metadata filter

Not search at all — a database `WHERE` clause.
`section_type = 'job_description'` → returns **every** matching section, guaranteed.

- **Good at:** completeness. Exhaustive by construction.
- **Fails at:** anything requiring understanding of meaning.

### Why this matters — the measured proof

Someone asks: *"give me all the job descriptions across all the documents."*

| Method | Found | Documents covered | Recall |
|---|---|---|---|
| Naive top-8 vector search | 8 sections (only 6 correct) | 4 of 5 | **40%** |
| Metadata filter | **15 sections** | **5 of 5** | **100%** |

Vector search **missed 9 of 15 sections and an entire document** — and, worse, reported no
sign that anything was missing. It looked confident.

> **The core insight:** "give me all X" is an *exhaustive* question. It's a database query
> wearing a search query's clothes. Answering it with similarity is guessing.
> Similarity is for "tell me about Y".

The `/rag-lab` page exists purely to make this visible — it runs both and highlights what
naive retrieval missed.

### Hybrid + RRF

In practice you run vector **and** keyword together and fuse the two ranked lists with
**Reciprocal Rank Fusion** — a simple rule that rewards documents ranking well in either
list. Nobody serious ships pure vector search.

⚠️ **Gotcha we hit:** RRF scores are derived from *rank position*, not confidence. An
on-topic and a nonsense query can produce nearly identical RRF scores. So you **cannot**
use an RRF score as a "is this relevant enough to answer?" threshold. We learned this the
hard way and moved the refusal check onto the reranker instead.

---

## 3. The ingestion pipeline (document → searchable)

```
PDF  (236 pages, 528,336 chars)
  │
  │  1. PARSE
  │     Each page → markdown. Text-layer pages parse cheaply; diagram-heavy
  │     pages go to a vision model. Stored on Source Page.canonical_markdown
  ▼
Source Page × 236
  │
  │  2. SECTION
  │     Pages → a *tree* of meaningful units: "Basic Concepts" → "Rates of
  │     Tax" → "Surcharge". Each carries a title, page range, and type.
  ▼
Source Section × 296          ← 44 before we fixed the page-19 bug
  │
  │  3. CHUNK
  │     Sections → ~1,200-character pieces with ~150 overlap, split on
  │     heading/paragraph boundaries. Each piece remembers its parent.
  ▼
Chunk × 648
  │
  │  4. EMBED
  │     Each chunk → a 256-number vector (model2vec potion-base-8M).
  │     Runs locally. No API key. No GPU. Free.
  ▼
  │  5. INDEX
  │     Vectors + text + metadata → LanceDB: an embedded database that is
  │     just a directory on disk. No server to run.
  ▼
sites/wikify.localhost/private/files/wikify_lance
```

### Why chunk at all?

Two reasons. Precision — a 40-page section is mostly irrelevant to any one question, and
embedding it produces a vague "average" vector that matches nothing well. And limits —
models have a finite context window.

**But** chunks are bad to *read* (they start mid-thought). So we use **parent-document
retrieval**: search the small chunks for precision, then hand the model the **full parent
section** for context. Best of both.

---

## 4. The query pipeline (question → cited answer)

```
"what is the TDS rate on rent under section 194-I"
  │
  │  1. ROUTE  — a cheap LLM call
  │     Is this exhaustive ("all X" → filter) or semantic ("about Y" → search)?
  │     Also rewrites follow-ups: "what about the second one?" → standalone.
  │     ➜ shown in the UI as the route badge, with its reason in plain words
  ▼
  │  2. RETRIEVE
  │     Vector + keyword, fused by RRF. Metadata and permissions are applied
  │     as a WHERE clause *inside* the query — a pre-filter, never a
  │     post-filter (see §6).
  ▼
  │  3. RERANK  — a second, cheap LLM pass
  │     Fetch ~50 candidates, score each against the question, keep the best 8.
  │     Biggest single quality jump after hybrid search.
  ▼
  │  4. EXPAND
  │     Swap each chunk for its full parent section.
  ▼
  │  5. SYNTHESISE
  │     "Answer using ONLY these excerpts. Cite each claim. If they don't
  │      contain the answer, say so."
  ▼
Answer + [1][2][3] + page numbers + cost
```

Real output from this exact query (previously **impossible** — page 154 wasn't indexed):

```
HTTP 200 · 17.3s · $0.0024
  2%  — plant & machinery or equipment [3]
  10% — land, building, furniture or fittings [3]
  threshold > ₹2,40,000 in a F.Y. [3]
```

---

## 5. Design choices that aren't standard RAG

**Contextual retrieval.** Before embedding, each chunk is prefixed with
`document title › section path`. A chunk reading *"10% for land and building"* is
meaningless in isolation; with its breadcrumb it becomes findable. Anthropic measured
~35% fewer retrieval failures from this technique. Most teams pay an LLM call per chunk to
generate that context — we got it free because the section tree already existed.

Crucially the prefix goes into `embed_text` (what gets embedded) and **not** `text` (what
gets displayed). The user never sees it.

**Filter-first routing.** §2's thesis, implemented.

**Page-level citations.** Most RAG can cite a *document*. Ours cites `p. 154`, because
sections carry page ranges. That's what makes an answer checkable against the source PDF.

**Honest refusal.** Below a confidence bar it says "I couldn't find this in the wiki"
rather than inventing an answer. A confident wrong answer about a tax rate is worse than
no answer.

**Permission-aware retrieval.** See §6 — it's the subtlest thing here.

---

## 6. Two traps worth understanding

### Pre-filter vs post-filter (a real security bug we fixed)

```python
# WRONG — post-filter
hits = search(query, limit=10)
hits = [h for h in hits if h.project in allowed]   # may return 0 of 10

# RIGHT — pre-filter
hits = search(query, limit=10, where="project IN (...)")
```

Post-filtering searches everything and *then* removes what you can't see — so you might
get 10 results, discard 9, and show 1. Worse, forbidden content passed through the
process. Pre-filtering means the database never considers it.

We shipped a real fail-open bug here: the "no ACL restriction" sentinel was `None`, which
is *also* what a forgotten argument looks like. Calling `answer()` with default arguments
leaked **15 citations** to a user with no read permission. Fix: make "search everything"
a distinct object that you must pass deliberately, and reject `None` outright.

> **Lesson:** never let "I forgot" and "I meant to allow everything" be spelled the same
> way.

### Fuzzy matching on numbers

When verifying that a quoted citation really appears in the source, fuzzy matching is
right for prose and **catastrophic** for numbers. We measured: a flipped `(+)` → `(-)`
sign scored **0.85 similarity** and passed as verified. A wrong digit failed only by
threshold luck.

For exam prep, a citation that *renders as verified* while containing a wrong rate is
worse than no citation — it manufactures trust. Fix: fuzzy for prose, **exact
character-for-character** for digits, percentages, currency, signs, and statutory
references. A digit is either right or it isn't; there is no "85% right".

---

## 7. Where everything lives

| Concept | File |
|---|---|
| Text → vectors | `wikify/rag/embed.py` |
| LanceDB connection + schema | `wikify/rag/store.py` |
| Sections → chunks (+ contextual prefix) | `wikify/rag/chunk.py` |
| Build / refresh the index | `wikify/rag/index.py` |
| The four search modes + RRF + ACL | `wikify/rag/search.py` |
| Intent routing + query rewriting | `wikify/rag/router.py` |
| Synthesis, citations, refusal | `wikify/rag/answer.py` |
| Citation verification | `wikify/rag/evidence.py` |
| Whitelisted endpoints | `wikify/api/rag.py` |
| Quality measurement | `wikify/rag/eval.py` |
| Ask UI / proof UI | `frontend/src/pages/AskWiki.vue`, `RagLab.vue` |

**Four search modes** in `search.py`:

| Mode | What it does | Use for |
|---|---|---|
| `vector` | Pure semantic similarity | "tell me about…" |
| `fts` | Keyword / BM25 | exact codes, section numbers |
| `hybrid` | Both, fused by RRF | the sensible default |
| `filter` | Metadata `WHERE`, **no top-k limit** | "give me ALL X" |

---

## 8. What measured quality looks like

You cannot improve what you don't measure. `rag/eval.py` runs 12 golden questions with
known-correct answers and reports:

- **recall@k** — of the sources that *should* have been found, how many were?
- **precision@k** — of what was returned, how much was actually relevant?
- **completeness** — did we return **all** expected sources? (the one that matters for
  "all X")

Current results on the demo corpus:

| | naive | routed |
|---|---|---|
| Mean recall | 66% | **87–90%** |
| Completeness | 45% | **73–82%** |

⚠️ Routed numbers are a **range**, not a point, because routing is an LLM call and
therefore non-deterministic. Quote it honestly.

On the real ICAI document, graded against the source PDF: **7 of 12 correct**.

⚠️ **That figure is stale and is not a claim about the system as it stands.** It was
graded before the coverage, sectioning and reranker fixes landed, and both failure modes
it turned on have been worked on since: a false refusal when the reranker misfired (the
refusal now needs two legs to agree, so a below-floor rerank is overruled by a strong
embedding match), and a fabricated category caused by a mind-map diagram being flattened
into a bullet list — the structure was lost at *parse* time, long before retrieval ran.
Whether the grade moved is unmeasured; do not quote 7/12 as current, and do not assume it
improved either.

Re-grading needs one thing this repo does not yet carry: **the twelve questions
themselves are not written down anywhere.** They were asked by hand against the source
PDF and only the score survived, so the run cannot be reproduced or compared against.
Whoever re-grades should record the question set the way `rag/eval.py` records the golden
questions — transcribed literally, so the next person can re-run it instead of re-inventing
it — and this is a fair illustration of why that file argues for transcribed ground truth
in the first place.

> **Lesson:** most RAG quality problems are actually **ingestion** problems. Our single
> biggest win today wasn't a retrieval tweak — it was discovering that a 168-character
> heading exceeded a 140-character database column, which threw an exception mid-loop and
> silently dropped every section after page 19. That one bug hid **93.6% of the document**
> from search. Coverage went from 6.4% to 99.8% with no re-parsing and zero LLM cost.

---

## 9. The one-paragraph version

Slice documents into meaningful pieces. Store each piece as coordinates in "meaning space"
alongside its metadata — page numbers, type, permissions. When a question arrives, work out
what *kind* of question it is, fetch the right pieces (by meaning, by keyword, or by
database filter), hand **only** those to the model, and make it cite them. The thing that
separates a good RAG system from a demo is knowing when **not** to use the clever
similarity search — and measuring yourself honestly enough to find out.
