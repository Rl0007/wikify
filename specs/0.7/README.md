# Wikify 0.7 — Deterministic Rescue & Semantic Retrieval

Two measured gaps, one release. Both were validated by offline spikes against the live
corpus on **2026-08-05** before a line of this spec was written; every threshold below
comes from those runs, not from intuition.

**Gap 1 — remediation pays an LLM to fix what a free parser already fixes.**
`jobs/parse.py:74-76` sends *every* page through the remediation pass, so a 398-page
manual costs ~800 model calls. Yet on the pages the harness flags `review`/`escalate`,
a purely deterministic Rust parser (`pdf-inspector`) recovers the text almost perfectly
— mean `content_recall` **0.565 → 0.998** on `review` pages, **0.505 → 0.977** on
`escalate` — at ~5 ms/page and zero cost. The expensive VLM pass is being spent on
pages a free candidate could win outright.

**Gap 2 — nothing in the product can find content by meaning.**
`agent/tools/read.py:158-163` substring-matches the query against `title` and
`hierarchy_path` and never reads a body. Measured over 180 generated questions against
the real OBG wiki, it scored **recall@10 = 0.000 on all 180**. Not "weak" — zero. A
chat agent over a wiki cannot currently locate a page by what it says.

**0.7 closes both**: `pdf-inspector` joins remediation as a free, deterministic
*candidate* (never the baseline) behind a structure guard, and short-circuits the LLM
passes when it already wins; and a LanceDB vector index over generated wiki pages gives
the agent real semantic retrieval.

## Spec index

| Doc | Covers |
|---|---|
| [`01-inspector-rescue.md`](01-inspector-rescue.md) | The `pdf-inspector` adapter, document-level batch extraction, the structure-preservation guard, candidate eligibility + winner rules, LLM short-circuit, artifact patterns, settings, degradation. |
| [`02-semantic-retrieval.md`](02-semantic-retrieval.md) | The LanceDB seam, wiki-page chunking + embedding via litellm, the `set_wiki_content` write funnel, reindex job, the agent search tool, fork discipline, degradation, and the retrieval eval harness. |
| [`IMPLEMENTATION-PLAN.md`](IMPLEMENTATION-PLAN.md) | Tracer-bullet slices **32–35** (continuing 0.6's numbering), delivery order, per-slice Verify steps against `pdf.localhost`. |

## The evidence (spike, 2026-08-05)

Both spikes ran offline against `files/Obstetrics and Gynaecology.pdf` (180 pp) and
`files/Nephrology.pdf` (398 pp), scored with the project's own
`engine/verify/deterministic.py`. Raw data and reports are **not** checked in; the
numbers that matter are reproduced here and in the two specs.

**Parser A/B** — 120 pages, 60 per document, stratified by `lab.db` verdict, 0 failures
either arm:

| Metric (pooled, n=120) | pymupdf4llm | pdf-inspector |
|---|---|---|
| `content_recall` mean | 0.666 | **0.997** |
| `extra_ratio` mean | 0.318 | **0.011** |
| pages with `parser_artifacts` | 34% | 0% |
| whole-document extract | 258–272 ms/pg | **4.7–7.0 ms/pg** |

**But structure is document-dependent and fails silently.** Same 120 pages, counting
markdown structure markers:

| | obg: pymupdf4llm → pdf-inspector | neph: pymupdf4llm → pdf-inspector |
|---|---|---|
| headings | 132 → **10** | 78 → 72 |
| pages with any `#` | 38/60 → **4/60** | 32/60 → 32/60 |
| list items | 320 → **0** | 326 → 283 |
| table rows | 35 → **220** | 359 → 198 |

On the OBG manual `pdf-inspector` emits almost no headings, **zero** list items, and
scrambles prose into wide pseudo-tables. `parser_artifacts` scores that 0% because its
patterns are tuned to pymupdf4llm's failure signatures and are blind to this one. That
is the entire reason for §1.3's structure guard, and the reason `pdf-inspector` must
never become the baseline parser.

**Retrieval** — 358 wiki documents from space `0rkkb8s039`, 180 LLM-generated questions
(60 docs × keyword/semantic/vague), local `bge-small-en-v1.5` embeddings:

| Arm | recall@10 | MRR@10 |
|---|---|---|
| substring (today) | **0.000** | 0.000 |
| full-text (tantivy) | 0.611 | 0.417 |
| **vector** | **0.794** | **0.534** |
| hybrid, default RRF | 0.728 | 0.528 |

By query type, vector's margin over FTS is +0.03 on keyword, **+0.32 on semantic**,
**+0.20 on vague** — the gap appears exactly where a chat agent lives.

## Principles locked (2026-08-05)

1. **`pdf-inspector` is a candidate, never the baseline.** `pymupdf4llm` stays the
   baseline parser and the sole owner of `Source Document.parser_used`. Inspector
   output is only ever adopted per-page, through the existing best-of-N adoption in
   `engine/remediate.py`, and only when it wins on score *and* passes the structure
   guard. The section tree is built from headings; a parser that scores 0.997 on recall
   while deleting every heading would silently flatten the product.
2. **Free candidates run first and can pre-empt paid ones.** Inspector costs nothing
   and takes ~5 ms/page. It is computed before the VLM/cleanup calls, and when it
   already clears `pass_threshold` the paid passes are skipped entirely. This is the
   cost win; adoption quality is unchanged either way.
3. **Structure is a first-class adoption criterion.** Recall answers "did we keep the
   words". It cannot answer "did we keep the document". 0.7 adds a deterministic
   structure signature to `verify/deterministic.py` and makes it a hard gate — measured
   to reject 36/60 bad OBG adoptions while passing 53/60 on Nephrology.
4. **Retrieval is derived and disposable.** The LanceDB index is a projection of
   `Wiki Document.content`, fully rebuildable from Frappe at any time. It is never a
   source of truth, it is never migrated, and losing the directory costs one reindex.
   Same stance `Section Reference` takes toward section markdown.
5. **Absent configuration degrades, never breaks.** No embedding key → indexing is
   skipped and the search tool reports itself unconfigured; wiki generation is
   unaffected. `pdf-inspector` failing to import or throwing → the candidate is simply
   absent and remediation proceeds exactly as it does today.
6. **Ship pure vector, not hybrid.** The spike measured default equal-weight RRF hybrid
   *losing* to plain vector (0.728 vs 0.794) because the weak FTS ranking drags the
   fusion down. Hybrid is deferred until there is a measured reason and a tuned weight.

## Decisions (confirmed 2026-08-05)

- **Placement:** new `specs/0.7/`, slices **32–35**, continuing 0.6 (29–31). No
  dependency on 0.5's graph surface.
- **Inspector scope:** runs on remediation targets only — never in `parse_pdf`'s page
  loop, never on `visual` pages (it has no image understanding and would force a paid
  judge call to score).
- **Batch, don't page.** `extract_pages_markdown` recomputes document-wide font stats
  per call: 1170 ms for a single page versus 4.7–7.0 ms/page extracted whole-document.
  Extraction happens once per `remediate_pdf` run, before the page loop.
- **Index target:** `Wiki Document` (generated wiki pages), not `Source Section` —
  chosen by the product owner. `Wiki Page` is hard-deprecated and out of scope.
- **Embeddings:** hosted via `litellm.embedding` (already a dependency, v1.83.7 on the
  bench). Default `openai/text-embedding-3-small`. OpenRouter has no embeddings
  endpoint, so this needs its own key in `Wikify Settings`.
- **Storage:** LanceDB embedded, one directory per site under
  `sites/<site>/private/files/lance/`. No server process, no new port.
- **`images.py` is out of scope but flagged.** A full 578-page scan found pymupdf4llm's
  `==> picture [W x H] intentionally omitted <==` marker fires **0 times** on either
  document, so `engine/images.py:71-103` Case 1 is already dead code on this corpus.
  That is a pre-existing defect, unrelated to this release; it is recorded here so it
  is not mistaken for 0.7 fallout.

## Conventions (unchanged)

Same as [`../0.6/README.md`](../0.6/README.md): backend per the `frappe-app-dev` skill,
engine work behind the `store.py` seam, thin whitelisted APIs, frontend frappe-ui v1 +
semantic tokens, verify every slice against `pdf.localhost` before the next, work
directly on `main`.
