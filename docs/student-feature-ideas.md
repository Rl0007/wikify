# Student-facing feature ideas — CA Final exam prep

Scope: features for a *student* using Wikify against an ICAI corpus. Every idea below is
tied to a module that already exists in this repo. Ideas that would need a new capability
from scratch, or that no existing gate can make safe, are in **§4 Too risky to ship**.

Effort key: **S** ≤ 1 day · **M** 2–4 days · **L** > 1 week.

The governing constraint for everything here:

> A wrong statutory rate rendered next to a verified-looking citation is worse than no
> answer at all. `rag/evidence.py` already encodes this — fuzzy for prose, exact
> character-for-character for digits, `%`, signs and statutory refs. Any new feature that
> puts a *number* in front of a student must pass through that gate or must not ship.

---

## 1. Prerequisites — fix before any student sees this

These are not features. They are live defects that make the student-facing surface unsafe
or unusable, and three of the ideas below depend on them.

### P1. Assessment-year scoping — the highest-severity latent bug

**Today the corpus is one assessment year, so nothing breaks.** The moment a second year's
PDF is imported into the same project, `rag/search.py` will happily retrieve an AY 2023-24
slab table and an AY 2025-26 surcharge threshold into the same `format_context()` block,
and `answer.py` will synthesise one answer over both. **Each citation would verify
individually** — the quotes really are in the sources — so both gates in `evidence.py` pass
and the student is shown a confidently-cited, wrong composite.

Fix: an assessment-year field on `Source Document` (or one project per AY), pushed into the
LanceDB `.where()` clause the same way the ACL pre-filter already is, plus an AY term in the
router prompt. Post-filtering is not acceptable here for the same reason it was not
acceptable for permissions (`docs/rag-explained.md` §6).

Effort: **M**. Risk of *not* doing it: the worst failure this product can produce.

### P2. Ask history is never replayed or displayed

`useRag.js:156` mints a new `sessionId` per ask and sends it as the server-side session
docname, so `recent_turns()` always looks up a name that cannot exist — every ask opens its
own session row and follow-up questions lose their antecedent. Separately, nothing in
`frontend/src/` calls `wikify/api/ask_history.py` at all. Ideas **T1.4** and **T1.5** are
both blocked on this.

Effort: **S** (split the opaque stream token from the server-owned session docname).

### P3. Intermittent false refusal

The reranker occasionally returns 0.0 for every hit, tripping `MIN_RERANK_SCORE = 3.0`
while the correct section sits at rank 2. `answer.below_floor()` already has the
`overrules_rerank` escape hatch, but an all-zero verdict should be treated as *reranker
unavailable* and degrade to fusion order rather than being taken as a verdict. To a student
"I couldn't find this in the wiki" is indistinguishable from "the syllabus doesn't cover
this" — a false refusal teaches the wrong thing.

Effort: **S**.

---

## 2. Tier 1 — ship these first

Ordered by (student value ÷ correctness risk). Every item here is *extraction or
aggregation over already-verified artefacts*, not new generation, which is exactly why they
are first.

### T1.1 — Figure cards (verbatim rate/threshold flashcards)

**For the student:** a deck of revision cards, one per statutory figure in the corpus —
"Surcharge, total income > ₹2 crore → 25%", each showing the verbatim source line, page and
line number, tappable through to the section. Revision material a student can trust to the
character, because no model wrote it.

**Builds on:** `rag/evidence.py` (`FIGURE_PATTERN`, `get_figures`, `locate_quote`,
`resolve_page`) over `Source Section.markdown` / `Source Page.canonical_markdown`. The card
generator can be **pure extraction with zero LLM calls** — `FIGURE_PATTERN` already
identifies the load-bearing tokens, and `locate_quote` already returns the exact line span.

**Effort:** M (extraction + a card UI; the hard machinery exists).

**Main risk:** *coverage*, not correctness — a figure printed inside a table image that
remediation flattened badly will be missing from the deck. That failure is silent and
benign (a missing card), unlike a wrong card. Mitigate by showing the card count per section
so a suspiciously empty chapter is visible. If an LLM is ever used to phrase the card front,
the back must still be the verbatim span and the card must be **discarded, not shown**, when
`locate_quote` returns `found: False`.

### T1.2 — "Show me the page" — citation deep-link to the rendered page image

**For the student:** every citation opens the actual rendered PDF page (`Source Page.image`,
150 DPI — the snapshot the models themselves saw) with the quoted line highlighted. This is
the difference between *being told* a rate and *checking* it in seconds. For CA prep, where
students are trained to cite the source, this is the feature that makes the product
defensible in a study group.

**Builds on:** `Source Page.image`, plus the span data `evidence.attach_verified_quotes`
already attaches to every citation (`quote_page_no`, `quote_page_line_start/end`,
`quote_page_approximate`).

**Effort:** M — frontend-heavy. The backend already returns everything needed.

**Main risk:** near zero. It is display of existing data. The one honesty requirement:
when `quote_page_approximate` is true, `resolve_page` deliberately abstained, so the UI must
say "somewhere in pp. 10–12" and must **not** highlight a guessed line. Rendering an
approximate page as exact would undo the whole point of the module.

### T1.3 — Refusal log → syllabus-gap queue

**For the student (and the operator):** "you asked 14 questions the corpus can't answer —
here they are, clustered." For the student it converts a dead end into a to-do list; for
whoever runs the product it is a ranked, evidence-backed list of which ICAI chapters to
import next.

**Builds on:** `rag/history.py` — `Wikify Ask Session` / `Wikify Ask Message` already store
the refusal flag, the route, and the citations per turn, and `route_intent` is indexed. Pure
aggregation, no LLM.

**Effort:** S (given P2).

**Main risk:** none of consequence. Worth noting that with only 2 DT chapters indexed, this
list will initially be enormous — present it as "outside the indexed corpus", never as
"outside the syllabus".

### T1.4 — Personal weak-spot / study-log report

**For the student:** what they actually asked about, rolled up to level-1/2 ancestors in the
`Source Section` nested set (the same rollup axis the exam heatmap track already settled on),
plus which sections they keep re-reading and which questions they re-asked. A study log they
did not have to keep.

**Builds on:** `rag/history.py` (citations are stored per answer turn, so section → topic
rollup is a join, not an inference) + the existing nested-set ancestor query.

**Effort:** S–M (given P2).

**Main risk:** low. It reports behaviour, not facts. Keep it descriptive — "you asked about
capital gains 9 times" — and resist the urge to turn it into a mastery score, which would be
an unfounded claim (see **§4.3**).

### T1.5 — High-yield ranking that is honest about corpus coverage

**For the student:** the in-flight past-paper heatmap tells them which topics carry marks.
Join it against retrieval coverage and it also tells them **which high-yield topics this
corpus cannot teach them** — "Transfer Pricing: 42 marks over 8 papers, no material
indexed." That second half is what turns a chart into a study plan.

**Builds on:** the exam-analysis track (topic rollup, marks × 0.9^age) + `rag.search`
(local, free — no `answer.ask()` per question, per the design decision already recorded).

**Effort:** S on top of the heatmap.

**Main risk:** the corpus is currently two DT chapters, so most questions will map to
nothing. That is expected, not a mapping bug — but a student reading a rank order without
the absolute marks will draw the wrong conclusion. Show raw marks, question count and years
present alongside the weighted score; never collapse to one opaque number.

---

## 3. Tier 2 — ship with explicit guardrails

### T2.1 — Past-paper question → "study this" links (retrieval only, no generated answer)

**For the student:** open a past paper, tap a question, jump straight to the sections it
draws on. No generated answer at all.

**Builds on:** the extracted question set + `rag.search.search()`. Note the strong signal
already identified: the ICAI PDFs are *Suggested Answers*, which cite exact sections — use
those citations as the mapping key ahead of retrieval similarity.

**Effort:** S given the heatmap work.

**Main risk:** low *because nothing is asserted*. A mis-mapped question wastes a minute; it
cannot state a wrong rate. Guardrail: show retrieval confidence and let the mapping be
visibly wrong rather than silently authoritative.

**Non-correctness risk:** the ICAI IPR notice forbids reproducing their material. Ship the
derived mapping and keep verbatim question text behind a toggle (or absent) until that is
cleared. This is a legal call, not an engineering one.

### T2.2 — Exhaustive revision checklists

**For the student:** "every exemption section in the indexed corpus" as a tickable revision
checklist, using `mode="filter"` — which returns *all* matches rather than a top-k guess.
This is the one retrieval mode that can honestly claim completeness, and it is the product's
own thesis.

**Builds on:** `search.py` filter mode + `Section Type` taxonomy + `api/explore.py`
(`type_summary`, `sections_by_type`).

**Effort:** S.

**Main risk:** **scope confusion is the whole risk.** "Complete" means complete *within the
indexed corpus*, and a student revising from a checklist will read it as complete within the
Act. The count must always be phrased against the corpus ("15 sections across the 2 indexed
chapters"), and the checklist header must name the documents it swept. Also blocked in
practice on a real data gap: ICAI #1 currently has **0 classified sections**, so the
`section_type` axis this depends on is empty for the main corpus.

### T2.3 — Amendment / year-on-year figure diff

**For the student:** the single most valuable thing you can give a CA Final candidate — "what
changed this year". When a new AY's PDF is imported, align sections by `hierarchy_path` /
`section_type` and diff their figures.

**Builds on:** `evidence.get_figures` — exact, order-preserving, already normalises ₹ vs Rs
and digit separators so `Rs. 1,00,000` and `₹1,00,000` compare equal while 4% and 6% do not.
This is precisely the comparison the module was built for.

**Effort:** L. The diff is easy; **section alignment across two differently-parsed PDFs is
the hard part**, and it is where this will actually fail.

**Main risk:** a **false negative is dangerous** — silently reporting "no change" for a
section that was misaligned lets a student revise a superseded rate. Guardrails: report only
pairs aligned with high confidence, list unaligned sections explicitly as "could not
compare" rather than omitting them, and present both figures as verbatim quotes with their
own pages side by side without labelling either "current". Depends on **P1**.

### T2.4 — Agent-traced computations, as a citation chain (not as arithmetic)

**For the student:** "how is tax on ₹80L computed?" walked hop by hop — slab table →
surcharge → marginal relief → cess — with each hop a verbatim, verified quote from a
different section. The existing agent loop already does genuine multi-hop
(`semantic_search` → `read_section` → search again).

**Builds on:** `wikify/agent/` loop + tools, `evidence.attach_verified_quotes`.

**Effort:** M–L.

**Main risk:** **high, and it sits exactly on the line.** This is only shippable if the agent
is forbidden from doing arithmetic. It may lay out the *rule chain* with citations and leave
every computation to the student. The moment it prints a computed rupee figure, that number
has no gate behind it (see **§4.2**) while every quote around it renders as verified —
manufactured confidence in its purest form. Ship the chain; do not ship the total. If
product pressure demands the total, it belongs in §4 until a deterministic engine exists.

---

## 4. Too risky to ship

### 4.1 Auto-grading a student's written answer out of marks

**Why not:** there is no ground truth for marks anywhere in this system, and no gate that
can supply one. `evidence.py` can verify that a quote exists in a source; it cannot verify
that an answer is *complete*, that a missed point was worth 3 marks, or that an examiner
would accept the reasoning. The output would be an authoritative-looking number with nothing
behind it, and students optimise hard against whatever number you show them.

**Ship instead:** a *figure diff* — "your answer used 30%; the cited section says 25%
(p. 76, line 11)". That is a claim the existing exact-equality gate can actually stand
behind, and it is arguably more useful than a score.

### 4.2 LLM-driven tax computation

**Why not:** computing a liability needs arithmetic, applicability logic, chronology and
regime optionality. Not one of those is covered by either gate — both gates check *quotes*,
and arithmetic produces no quote to check. Worse, a wrong total would be surrounded by
correctly-verified citations for each input rate, so every trust signal in the UI would be
green. That is the exact failure mode `evidence.py` exists to prevent, re-introduced one
layer up.

**Precondition to revisit:** a deterministic rule engine that computes from figures
extracted by **T1.1** (which are verbatim and page-pinned), with the LLM restricted to
selecting *which* rule applies and the arithmetic done in Python — plus a golden test set of
worked ICAI examples in `rag/eval.py` style before it goes near a student.

### 4.3 Predicting what will be asked next exam

**Why not:** the heatmap measures history; a "78% likely" badge is unfalsifiable and will
cause students to drop topics. The weighting formula (marks × 0.9^age) is a *display*
heuristic that was deliberately kept alongside raw marks precisely so it would not be read
as a prediction. Turning it into one discards that design decision. Mastery scores in
**T1.4** fail for the same reason.

### 4.4 Falling back to the model's own knowledge when the corpus refuses

**Why not:** honest refusal is the product's core promise, and this deletes it. With two
chapters indexed the fallback would fire on most questions, so in practice the product would
become an ungrounded chatbot wearing a citation UI. Note that `answer()` already refuses
correctly when no key is configured — do not add a "best effort" mode next to it.

### 4.5 Re-serving ICAI question text and suggested answers

**Why not:** correctness is fine here; the problem is the IPR notice on ICAI's archive page,
which forbids reproducing or storing their material without written permission. Derived
statistics are a different thing from re-serving the text. Flagged to the user 2026-08-11;
still their call. Until it is cleared, ship counts, mappings and topic rollups — not verbatim
question text.

---

## 5. Recommended order

1. **P1** (AY scoping) — do this before a second year's PDF is ever imported.
2. **P2 + P3** — cheap, and they unblock T1.3 / T1.4 and stop the false refusals.
3. **T1.2** (page-image deep link) — highest trust-per-day-of-work, near-zero risk.
4. **T1.1** (figure cards) — the first genuinely new study artefact, still zero-generation.
5. **T1.5 + T2.1** — ride the heatmap track while it is warm.
6. **T1.3 / T1.4** — cheap once P2 lands.
7. **T2.2**, then **T2.3** — T2.2 needs classification run on ICAI #1 first; T2.3 needs P1.
8. **T2.4** — only with the no-arithmetic rule enforced in the system prompt *and* checked
   in a test.
