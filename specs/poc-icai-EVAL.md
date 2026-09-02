# ICAI exam-prep evaluation — real CA corpus, measured

**Document under test:** Source Document `fu7dv1aoh0` — "86805bos-aps1326-final-slmr-p4"
(ICAI *SARANSH* Last Mile Referencer, Final Course Paper 4, Direct Tax Laws & International
Taxation), 236 pages, project **`PRJ-2026-00002`** ("Icai"), import `IMP-2026-00002`.
Ground truth PDF: `sites/wikify.localhost/private/files/86805bos-aps1326-final-slmr-p4.pdf`.

Run on `wikify.localhost`, 2026-08-10, via `wikify.api.rag.ask(project="PRJ-2026-00002")`
(`rerank=True`) plus three questions driven through the real `/wikify/ask` UI.

---

## 1. Index stats

```
from wikify.rag import index
index.rebuild_project("PRJ-2026-00002")
→ {'chunks': 65, 'sections': 44, 'seconds': 17.531}

index.index_stats("PRJ-2026-00002")
→ {'chunks': 65, 'sections': 44, 'documents': 1,
   'indexed_at': '2026-08-10T07:52:11', 'dim': 256}
```

The indexing itself works. What it indexed is the problem:

| Measure | Value |
|---|---|
| PDF pages | 236 |
| `Source Page` rows (parsed, with markdown) | 236 (`text` 235, `visual` 1) |
| Total parsed page markdown | **528,336 chars** |
| `Source Section` rows | **44** |
| Highest `page_end` on any section | **19** |
| Total section markdown (= everything retrievable) | **34,200 chars — 6.4 % of the parsed document** |
| Sections with *empty* markdown, still indexed | **15 / 44 (34 %)** |
| Chunks | 65 |

**The single dominant fact: sectioning stopped at page 19.** Pages 20–236 were parsed —
their markdown is sitting in `Source Page.canonical_markdown` — but no `Source Section`
covers them, and `rag/chunk.py` builds chunks from sections only. So **93.6 % of this
document is invisible to retrieval.** Everything below is measured against the 6.4 % that
is actually reachable (Basic Concepts + the first two pages of Residential Status).

---

## 2. Golden questions — 12 CA revision questions, verdicts

Verdict key: **CORRECT** = statutorily right and matches the PDF · **PARTIAL** = right shape,
one or more material errors · **WRONG** = confidently wrong or falsely refused ·
**NO-COVERAGE** = the answer is in the PDF but not in the index.

### G1 — "What is the surcharge rate for total income above 2 crore?"
- **Expected** (p.11, both regimes; also p.12 firm, p.13 co-op, p.15 company):
  default regime u/s 115BAC → TI *excluding* 111A/112/112A income > ₹2 cr = **25 %**,
  dividend/CG capped at **15 %**, TI *including* such income > ₹2 cr otherwise = **15 %**.
  Optional regime → > ₹2 cr but ≤ ₹5 cr = **25 %**, > ₹5 cr = **37 %**, residual = **15 %**.
- **Got:** all of the above, both regimes, with the including/excluding distinction intact,
  plus correct firm (12 % > ₹1 cr), company (7 % / 12 %) and co-op (7 % / 12 %, 10 % under
  115BAD/BAE) rates.
- **Sources:** `II. SURCHARGE` p.11, `BASIC CONCEPTS` p.15, `Firm/LLP/Local Authority` p.12.
- **Verdict: CORRECT.** This is the hardest table in the reachable range and it came back right.

### G2 — "What are the five heads of income?"
- **Expected** (p.5): Salaries · Income from house property · Profits and gains of business
  or profession · Capital gains · Income from other sources.
- **Got:** exactly those five, cited to `CLASSIFICATION OF INCOME` / `HEADS OF INCOME`, p.5.
- **Verdict: CORRECT.**

### G3 — "What is the difference between previous year and assessment year?"
- **Expected** (p.5): PY = financial year immediately preceding the AY (special rule for
  newly set-up business); AY = 12 months commencing 1st April; income earned in PY is taxed
  in the following AY; plus the five exceptions where PY income is assessed in the PY itself.
- **Got:** all of it, including the newly-set-up-business proviso and all five exceptions
  (shipping business of non-resident, persons leaving India, discontinued business,
  AoP/BoI/AJP formed for a particular event, person likely to transfer property to avoid tax).
- **Verdict: CORRECT.**

### G4 — "Who is a person under section 2(31)?"
- **Expected** (p.6): **seven** categories — Individual, HUF, Company, Firm, AoP/BoI,
  Local Authority, Artificial Juridical Person.
- **Got — and this is the finding:** the answer is **unstable across identical runs.**
  - API run (`wikify.api.rag.ask`): *"under section 2(31), a 'person' includes the following
    **8 categories**"* — the seven correct ones **plus "8. Person (as a general category)"**.
  - UI run (`/wikify/ask`, screenshot `docs/implementation/media/icai-ask-person-231.png`):
    the correct seven, closing with *"There are **7 categories** of persons under section
    2(31)."*
- **Why:** the source page is a mind-map. The flowchart flattener dropped the root node
  ("Person") into the same bullet list as its children — `Source Section` `8htm8fpj94`
  literally stores **eight** bullets in scrambled order, with `Person` sitting between
  `HUF` and `Local Authority`. Whether the answer is right depends on whether the model
  happens to notice that one of the eight bullets is the category name itself.
- **Verdict: WRONG.** Not because it is always wrong, but because it is a coin-flip on a
  definitional enumeration. A student writing "eight persons u/s 2(31)" loses the mark, and
  nothing in the UI signals which run they got. The fix is upstream: the section data is
  corrupt, so the answer cannot be trusted even when it is right.

### G5 — "What is the rate of health and education cess?"
- **Expected** (p.6): HEC **@ 4 %** on income-tax **+** surcharge, **−** rebate u/s 87A if
  applicable.
- **Got:** exactly that.
- **Verdict: CORRECT.**

### G6 — "What is the rebate under section 87A for a resident individual under the default regime?"
- **Expected** (p.17): TI ≤ ₹7 lakh → lower of income-tax or **₹25,000**; the marginal-relief
  computation (A = TI − ₹7 lakh, B = tax on TI, rebate = B − A if B > A); rebate capped at
  pre-rebate tax; allowed before HEC; **not** available against 10 % LTCG u/s 112A.
- **Got:** all five points, correct figures.
- **Verdict: CORRECT.**

### G7 — "What is the surcharge rate for a firm or LLP whose total income exceeds 1 crore?"
- **Expected** (p.12): **12 %** of income-tax.
- **Got:** 12 %, cited to `Firm/LLP/Local Authority` p.12.
- **Verdict: CORRECT.**

### G8 — "What are the income tax slab rates under the default regime u/s 115BAC(1A)?"
- **Expected** (p.7): Upto ₹3,00,000 NIL · > 3–7 L: 5 % of TI > ₹3 L · > 7–10 L: ₹20,000 +
  10 % · > 10–12 L: ₹50,000 + 15 % · > 12–15 L: ₹80,000 + 20 % · > 15 L: ₹1,40,000 + 30 %.
- **Got:** `refused: True` — *"I couldn't find this in the wiki."*
- **Why:** retrieval succeeded. Re-running `rag.search` for the same query returns
  `I. INCOME TAX RATES` (p.7 — the exact table) at **rank 2 in hybrid, rank 4 in vector,
  rank 2 in FTS**. The refusal comes from `answer.below_floor`: the LLM reranker returned
  **`rerank_score = 0.0` for all eight hits**, below `MIN_RERANK_SCORE = 3.0`, so `ask()`
  refused. On other questions in the same batch the same reranker returned 10.0. This is a
  silent, intermittent reranker failure that presents to the student as "the document
  doesn't say".
- **Verdict: WRONG (false refusal).** The single most-asked slab table in the syllabus,
  present in the index, unreachable through `/ask`.

### G9 — "What are the rates of income tax for a co-operative society?"
- **Expected** (p.12–13): default slabs ≤ ₹10,000 → 10 %; > 10,000 ≤ 20,000 → ₹1,000 + 20 %;
  > 20,000 → ₹3,000 + 30 %. 115BAD → 22 %. 115BAE → 15 % on manufacturing income, 22 % on
  other income. Surcharge: nil ≤ ₹1 cr, 7 % > 1 cr ≤ 10 cr, 12 % > 10 cr; 10 % flat under
  115BAD/BAE.
- **Got:** every figure above, in a clean table, correctly attributed.
- **Verdict: CORRECT.** Note this answer is assembled from a *pipe table* (p.12) plus prose
  notes (p.13) — the shape that works.

### G10 — "How is the residential status of an individual determined?"
- **Expected** (p.18): stay ≥ 182 days in RPY → resident. Else, stay ≥ 60 days in RPY **and**
  ≥ 365 days in 4 IPPYs → resident. The 60-day limb is **extended to 182 days** for an Indian
  citizen leaving India for employment / as crew of an Indian ship. For an Indian
  citizen/PIO **visiting** India whose non-foreign-source income > ₹15 lakh, the limb becomes
  **120 days + 365 days in 4 IPPYs** (and such a person is RNOR). ROR/RNOR split: NR in 9 of
  10 IPPYs, or ≤ 729 days in 7 IPPYs → RNOR. Deemed resident u/s 6(1A) → always RNOR.
- **Got:** the 182-day test, the ROR/RNOR split, and s.6(1A) deemed residence all correct —
  but it applies the **120-day + 365-day test to the "left India for employment" branch**,
  which is the *visiting Indian citizen/PIO* branch, and it drops the **> ₹15 lakh income**
  precondition on that test.
- **Why:** p.18 is a pure decision-tree image. The section markdown is a flat list of the
  flowchart's node labels with the Yes/No edges stripped, so the branch each condition hangs
  off is unrecoverable. The model guessed the wiring.
- **Verdict: PARTIAL — and dangerous.** Residential status is a scoring question in every
  attempt; mis-wiring the 120-day test is a standard exam trap.

### G11 — "What are the provisions for TDS on rent under section 194-I?"
- **Expected:** pp.154–156 (chapter "TDS, TCS and Advance Tax", index entry p.144).
- **Got:** *"The excerpts provided do not contain any information about Section 194-I… The
  topic of TDS, TCS and Advance Tax is listed in the index [8]."*
- **Verdict: NO-COVERAGE.** Honest, and the citation of the INDEX page is a nice touch —
  but the content exists in the PDF and simply was never sectioned.

### G12 — "Explain transfer pricing and arm's length price under section 92C."
- **Expected:** pp.191–207 (chapter "Transfer Pricing", index entry p.191).
- **Got:** correctly says the excerpts don't cover it and points at the index entry for
  Transfer Pricing at p.191.
- **Verdict: NO-COVERAGE.**

### Scorecard

| Verdict | Count | Questions |
|---|---|---|
| CORRECT | **7** | G1, G2, G3, G5, G6, G7, G9 |
| PARTIAL | 1 | G10 |
| WRONG | 2 | G4 (unstable — fabricated 8th person in the API run), G8 (false refusal) |
| NO-COVERAGE | 2 | G11, G12 |

**7/10 on answerable questions; 7/12 overall.** Median latency 13.6 s
(range 3.9 s – 45.9 s; the 45.9 s outlier was a cold embedding-model load).

---

## 3. The table question — are rate tables correct now?

**Mostly yes, and the specific bug in `poc-visual-fidelity-PLAN.md` §Failure 2 is fixed —
but a corrupted duplicate of the same table is still sitting in the index.**

What is actually stored today:

1. **`Source Page` p.11 `canonical_markdown` — FIXED.** The surcharge grid is now two proper
   pipe tables, one per regime, each row binding a slab to its rate. Only the top-level
   *regime split* is mermaid, which is a genuine 2-way branch and the correct use of a
   flowchart.
2. **`Source Section` `8icml5lu5p` "II. SURCHARGE" (p.11) — FIXED.** Same two clean pipe
   tables. This is what G1 answered from, and G1 was right.
3. **`Source Section` `8ic5pbjtth` "AMT liability not attracted" (pp.10–11) — STILL MERMAID.**
   It carries a *second, stale copy* of the entire p.11 surcharge grid as a flowchart with
   `F → F1…F5` slabs and `G → G1…G5` rates. Better than the state recorded in the plan — it
   now emits binding edges `F1 --> G1 … F5 --> G5`, so the row↔rate correspondence is
   technically recoverable — but the reader has to trace 40 edges to reconstruct 5 rows.

So the index contains **two copies of the surcharge table, one clean and one flowchart-
encoded**, and both are retrieved for a surcharge question — in the UI run they came back as
sources #3 and #2 respectively (screenshot `docs/implementation/media/icai-ask-surcharge.png`).
G1 was answered correctly, but that is the model choosing the better of two copies, not a
guarantee. Two failure modes remain live:

- **Stale duplicates.** Re-remediating a page does not invalidate section markdown that was
  built from the old page output. A section spanning pp.10–11 kept the pre-fix mermaid.
- **Diagrams that are *not* tables are still lossy.** G4 (mind-map → list, root node
  promoted to a member) and G10 (decision tree → flat node list, edges stripped) are both
  *correctness* failures caused by flattening a 2-D structure. Fixing tables did not fix
  flowcharts, and for CA material flowcharts are half the book: 38 pages emit a mermaid block.

**Verdict on the headline question:** rate tables in the reachable range are **correct where
they were re-remediated into pipe tables** (G1, G7, G9 all right, every figure checked
against the PDF). The table-as-flowchart corruption is no longer the top risk. The top risk
is now **coverage** (§1) and **flowchart flattening on definitions and decision trees** (G4,
G10).

---

## 4. Is sectioning quality itself a blocker?

**Yes — it is the blocker, and not for the reason the brief anticipated.**

The brief flagged poor titles. Titles are indeed poor: four cover-page fragments
(`सरंस्` — a mojibake of *SARANSH*, `LAST MILE REFERENCER FOR`, `FINAL COURSE`, `PAPER 4`),
five separate sections all titled `BASIC CONCEPTS` (they are running page headers, not
sections), and `© The Institute of Chartered Accountants of India` as a section. But titles
turned out to matter less than expected: chunks embed `"<doc title> › <hierarchy path>\n\n"
+ text`, so the *body* carries retrieval, and G1/G3/G6/G9 all landed on the right section
despite junk titles.

Three sectioning defects that *do* break retrieval, in order of severity:

1. **Coverage collapse at page 19 (fatal).** 44 sections for 236 pages is not "thin" — it is
   19 pages sectioned and 217 pages dropped. 93.6 % of the parsed text has no section and
   therefore no chunk. Every question outside Basic Concepts + the first 2 pages of
   Residential Status is unanswerable (G11, G12), and the corpus can only ever score on the
   syllabus's first chapter. **Nothing else on this list matters until this is fixed.**
2. **34 % of sections are empty shells.** 15 of 44 sections have empty `markdown` and are
   still embedded and indexed — including
   `Concessional tax rates under the default tax regime under section 115BAC(1A)` (p.8),
   which has a title matching G8 almost word-for-word and therefore **ranks #1 in vector,
   hybrid and FTS for that query while carrying 76 characters of text (just the hierarchy
   header)**. Empty sections outrank real content by title similarity and burn a slot in
   every top-8. They should not be indexed at all.
3. **Running page headers become sections, and real sections absorb the wrong pages.**
   `BASIC CONCEPTS` appears as five distinct sections (pp.8, 9, 13–14, 15, 17) because the
   header is repeated on every page of the chapter. Conversely `AMT liability not attracted`
   swallowed the whole of p.11's surcharge table, and `Previous year` swallowed p.6's
   general-rule/exceptions block. Boundaries are drawn on typographic prominence, not
   document structure — the ICAI booklet's real structure (the INDEX page 4, which the
   parser *did* capture as a section) was never used to drive segmentation.

Cheap wins, in order:
- Section pp.20–236 at all — the page markdown is already there, this is a re-run of the
  sectioner, not a re-parse.
- Skip empty sections in `chunks_for_project` (or drop them at `index.rebuild_project`).
- Use the parsed INDEX page as a chapter map to seed top-level section boundaries; suppress
  running headers that repeat on ≥ 3 consecutive pages from becoming section titles.
- Invalidate/rebuild section markdown when a constituent page is re-remediated (kills the
  stale-mermaid duplicate in §3).

---

## 5. Other findings

- **The reranker fails silently to 0.0 and is read as "no answer".** `answer.below_floor`
  refuses when `max(rerank_score) < 3.0`, and an all-zero rerank response is
  indistinguishable from genuine irrelevance. For exam prep a false "the document doesn't
  say this" about the 115BAC slab table is nearly as damaging as a wrong rate. Suggest:
  treat an all-zero rerank as *reranker unavailable* and fall back to the fusion ranking,
  rather than refusing.
- **The router chose `semantic` for all 12 questions**, including the two that read as
  exhaustive lookups. Consistent with the deliberate bias recorded in
  `.claude/memory/scratch.md`; no observed harm on this corpus.
- **Citations are honest.** Both no-coverage answers named the INDEX page and the printed
  page number of the missing chapter rather than confabulating. That behaviour is the
  strongest thing in this evaluation.
- **The `/ask` UI works end to end** against the real corpus: route badge, source cards with
  `vec #n` / `fts #n` / `rerank` badges, per-source page numbers, and inline `[n]` citations.
  Three questions driven through the live UI as Administrator:
  - `docs/implementation/media/icai-ask-surcharge.png` — G1, correct, sources ranked
    `BASIC CONCEPTS` p.15 (rerank 10.0) then the stale-mermaid `AMT liability not attracted`
    (rerank 0.0) then `II. SURCHARGE` p.11.
  - `docs/implementation/media/icai-ask-slabs.png` — G8, the false refusal reproduced in the
    UI: *"Not in this wiki"*, **Sources 0**, 21.3 s.
  - `docs/implementation/media/icai-ask-person-231.png` — G4, correct in this run
    (7 categories), but note the top two source cards are `BASIC CONCEPTS` p.9 (about the
    Agnipath Scheme and s.80JJAA) and `Conditions … concessional rates of tax` p.8, both
    `rerank 0.0` — the actual `PERSON [SECTION 2(31)]` section is ranked 6th. Ranking is
    weak even when the final answer lands.
- **Answers are slow enough to notice:** 18–22 s to a finished answer in the UI, with the
  route badge appearing first and sources streaming before the prose. Acceptable for a
  demo, not for a revision session.
- Transient `"Couldn't reach the server — the request didn't complete."` was observed once
  during the UI run (the dev server was being restarted by concurrent work); the `Retry`
  button recovered cleanly.

---

## 6. Demo-readiness verdict for CA students

**Not demo-ready as a study app. Demo-ready as a scripted 5-minute proof of the pipeline.**

- It answers correctly and with real citations on 7 of 10 answerable questions, and it gets
  the surcharge table — the hardest thing in the reachable range — exactly right, including
  the including/excluding-dividend distinction that trips students up. That is a genuine result.
- But a student would hit the wall on their second question. The app can only speak about
  chapter 1 of a 14-chapter referencer; ask about TDS, capital gains, transfer pricing or
  BEPS and it says "not in the wiki" — correctly, and uselessly.
- And two of the answers it *does* give are wrong in exactly the way that matters: a
  fabricated eighth "person" u/s 2(31), and a mis-wired 120-day residential-status test.
  Both come from flowchart flattening, not from the LLM.

Blocking list before this can be shown to a real CA aspirant: (1) section pp.20–236,
(2) stop indexing empty sections, (3) stop refusing on an all-zero rerank, (4) preserve
edges when converting decision trees, or keep the page crop and let the student look.
