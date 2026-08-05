# 0.7 · pdf-inspector as a Deterministic Rescue Candidate

> Behavior spec. Delivery order lives in [`IMPLEMENTATION-PLAN.md`](IMPLEMENTATION-PLAN.md).

## 1.1 The shape of the change

Today `engine/remediate.py` builds a candidate list per page and adopts the best:

```
candidates = {vlm, cleanup}        # both cost money, both call a model
winner     = _pick_winner(...)     # vlm > cleanup, else keep baseline
```

0.7 adds a third candidate that is free, deterministic, local, and ~5 ms/page:

```
candidates = {inspector, vlm, cleanup}
```

`pymupdf4llm` remains the baseline parser. `Source Document.parser_used` still reads
`pymupdf4llm`. Nothing in `engine/__init__.py::parse_pdf` changes.

Two behavioral wins, in order of value:

1. **Quality** — on pages the harness flags, inspector's text recovery is near-perfect
   where the baseline collapses (`review`: 0.565 → 0.998; `escalate`: 0.505 → 0.977).
2. **Cost** — because it is computed *before* the paid candidates, a page it already
   wins outright can skip the VLM and cleanup calls entirely (§1.5).

## 1.2 The adapter — `wikify/engine/parsers/inspector.py`

Mirrors the shape of `parsers/pymupdf.py`, but document-scoped rather than page-scoped,
because per-page calls are catastrophically slower (measured: 1170 ms/page single-call
vs 4.7–7.0 ms/page whole-document — `extract_pages_markdown` recomputes document-wide
font statistics on every invocation).

```python
NAME = "inspector"

def parse_document(pdf_path: str) -> dict[int, str]:
    """1-based page number → markdown, for the whole document in one pass."""
```

Implementation notes that are easy to get wrong:

- `pdf_inspector.extract_pages_markdown(path)` returns a `PagesExtractionResult`, not a
  list. It has **no `len()`**. Pages are at `.pages`, each a `PageMarkdown` with
  `.page`, `.markdown`, `.needs_ocr`, `.ocr_reason`.
- **`PageMarkdown.page` is 0-based** (verified: `0..179` for a 180-page document). The
  optional `pages=` argument is also 0-based. The adapter converts once, at the seam:
  `{pg.page + 1: pg.markdown for pg in result.pages}`. Every caller above the adapter
  works in Frappe's 1-based page space.
- The result object also exposes `is_complex`, `pages_needing_ocr`, `pages_with_tables`,
  `pages_with_columns`. Not used in 0.7; noted so a future slice doesn't rediscover them.
- Wrap the whole call in `try/except` and return `{}` on failure. A missing wheel, an
  encrypted PDF, or a panic in the Rust core must degrade to "no inspector candidate",
  never abort a remediation run.

Document-level classification (`detect_pdf` / `classify_pdf`) is **not** adopted in 0.7.
Both spike documents returned `text_based` at confidence 1.00 with zero pages needing
OCR, so the corpus does not exercise it and there is nothing to validate a replacement
of `pdf_utils.classify_page` against. Left for a later release with a scanned corpus.

## 1.3 The structure guard — the load-bearing part of this spec

Inspector's `content_recall` is 0.997. Its structural fidelity is **document-dependent
and fails silently.** Measured over 60 sampled pages per document:

| | obg | neph |
|---|---|---|
| headings, baseline → inspector | 132 → **10** | 78 → 72 |
| pages with any `#` | 38/60 → **4/60** | 32/60 → 32/60 |
| list items | 320 → **0** | 326 → 283 |
| table rows | 35 → **220** | 359 → 198 |

On OBG it deletes essentially every heading, emits **zero** list items, and converts
prose into wide pseudo-tables. `engine/sectionize.py` splits documents on `#` headings
(`loader/sectionizer.py:63-151`); adopting that output would flatten the section tree
while every score in the UI went *up*. `parser_artifacts` does not catch it — its
patterns are tuned to pymupdf4llm's signatures.

So structure becomes an explicit, deterministic adoption gate.

**New in `wikify/engine/verify/deterministic.py`** (pure stdlib, no frappe, consistent
with the rest of that module):

```python
def structure_signature(markdown: str) -> dict:
    """Counts of the markdown structure the downstream pipeline depends on:
    {"headings", "list_items", "table_rows", "table_seps"}."""

def structure_preserved(base_md: str, cand_md: str) -> tuple[bool, str]:
    """Whether `cand_md` keeps the document structure `base_md` established.
    Returns (ok, reason) — reason is '' when ok, else a short cause for the notes."""
```

`structure_preserved` rejects on any of three rules, each calibrated on the table above:

| Rule | Condition | Rationale |
|---|---|---|
| `heading_loss` | base has ≥1 heading and candidate has `< 0.5 ×` base headings | sectionizer input; obg drops 132→10 |
| `list_wipe` | base has ≥3 list items and candidate has 0 | obg drops 320→0 across the sample |
| `table_inflation` | candidate table rows `> 3 ×` base table rows **and** base has ≥1 list item | prose-scrambled-into-table; obg 35→220 |

Measured rejection behaviour with these exact thresholds, over the 60-page samples:

| | heading_loss | list_wipe | table_inflation |
|---|---|---|---|
| obg | 36/60 | 27/60 | 3/60 |
| neph | 7/60 | 6/60 | 1/60 |

That is the discrimination the guard exists to provide: it blocks the document where
inspector destroys structure and admits the one where it does not. The thresholds are
constants at the top of the module with the measurement in a comment, so a future
corpus can retune them against evidence rather than taste.

**Additionally**, add a `prose_table` pattern to `engine/lint.py`'s `table_artifacts`
(consumed by `deterministic.parser_artifacts`): a table row with `> 6` cells whose mean
non-empty cell length exceeds ~40 characters is prose that has been scrambled into a
table. This closes the blind spot generally, not just for this parser, and rides the
existing composite penalty (`harness.py:107-110`, `composite *= 0.7`).

## 1.4 Candidate construction and the winner rule

In `engine/remediate.py::remediate_pdf`, before the page loop:

```python
inspector_pages = inspector.parse_document(pdf_path)   # {} on any failure
```

Inside the loop, for each target page, **before** the vlm/cleanup calls:

```python
ins_md = inspector_pages.get(p["page_no"], "")
```

An inspector candidate is constructed only when **all** of:

- `ins_md` is non-empty, and
- `kind != "visual"` — inspector has no image understanding, and scoring a visual page
  requires a paid judge call (`VISUAL_WEIGHTS` is judge-dominant, `config.py:33`), which
  would invert the cost saving this candidate exists to produce.

It is **adopt-eligible** when both:

- `ins_ps.composite > base_ps.composite` (same bar the vlm candidate clears), and
- `structure_preserved(base_md, ins_md)` returns ok.

When the guard rejects, the candidate is still appended with `adopt_eligible=False` and
its rejection reason flows into `store.set_remediation`'s `notes`, so the review UI
shows *why* a high-scoring candidate was not taken. Silent rejection would be worse than
no guard.

**`_pick_winner` changes minimally.** The existing vlm-over-cleanup rule and its
tie-to-vlm rationale (cleanup's composite is depressed by intended furniture removal)
are preserved exactly. Inspector is layered on top:

```
winner = existing vlm/cleanup resolution        # unchanged
if inspector eligible and (no winner or inspector.composite >= winner.composite):
    winner = inspector
```

Ties go to inspector: it is deterministic and free, so given equal scores it is the
better artifact to persist. Note this only affects *which* candidate is stored — by the
time both exist, both have already been paid for. The saving comes from §1.5.

## 1.5 Short-circuit — where the money is saved

Today every remediation target costs a VLM call, and non-visual pages cost a cleanup
call on top (`remediate.py:109-135`). On a 398-page manual with `scope="all"`, that is
~800 model calls.

When the inspector candidate already clears the pass bar, the paid candidates are
pointless. Guarded by a new setting:

```
Wikify Settings.inspector_short_circuit  (Check, default 1)
```

When enabled, and for a page where **all** of the following hold, the vlm and cleanup
calls are skipped entirely and the inspector candidate is adopted directly:

- `kind != "visual"`
- inspector candidate exists and is adopt-eligible (score **and** structure guard)
- `ins_ps.composite >= settings.pass_threshold` (0.90 by default)

The page's `remediation_method` records `inspector`; `notes` records
`short-circuit: skipped vlm/cleanup`. `llm.get_metrics()` for that page is empty, so
`Source Page.llm_cost` lands at 0 — which is the honest number and makes the saving
directly visible in the existing cost UI.

Expected impact, from the spike: inspector reaches ≥0.90 composite on the large majority
of `review`-verdict pages (mean 0.998 there, n=42). Those pages currently consume two
model calls each and would consume none. The exact saving is corpus-dependent and is a
**measured acceptance criterion** of Slice 33, not a claim this spec makes in advance.

Short-circuit is deliberately conservative: it never applies to visual pages, never
applies when the structure guard fires, and never applies below `pass_threshold`. A page
that inspector improves but does not fully rescue still gets the full paid treatment,
and the best of all three wins.

## 1.6 Data model

Additive only. No migration of existing rows.

| DocType | Field | Change |
|---|---|---|
| `Source Page` | `remediation_method` | Select — add `inspector` to `'' / cleanup / vlm` |
| `Source Page` | `canonical_source` | Select — add `inspector` to `'' / baseline / cleanup / vlm / image` |
| `Wikify Settings` | `inspector_enabled` | **new** Check, default `1` — master switch for the candidate |
| `Wikify Settings` | `inspector_short_circuit` | **new** Check, default `1` — §1.5 gating |

`Source Document.parser_used` is untouched and continues to read `pymupdf4llm`. The
baseline is the baseline.

Frontend: wherever `canonical_source` / `remediation_method` are rendered as labels
(page review split-pane, remediation badges), add the `inspector` case. It should read
as a first-class method, not an unknown value falling through to a default.

## 1.7 Dependency

```toml
pdf-inspector>=0.2.6   # deterministic Rust PDF→markdown; rescue candidate only
```

- MIT, pure Rust core (`lopdf`), **no ML models, no torch, no ONNX**, fully offline.
- Ships `cp38-abi3` wheels for macOS arm64/x86_64, manylinux aarch64/x86_64, win_amd64 —
  abi3 means the 3.8 wheel installs on the bench's Python 3.14. Verified installed and
  working on this bench.
- Released 2026-07-31 at v0.2.6. **This is a young library.** That immaturity is the
  reason it enters as a guarded candidate rather than the baseline, and the reason
  §1.2's failure path degrades silently.

While specifying this, note that `requests` is imported by `engine/llm.py:17` but
declared nowhere, and `frappe-wiki` is a hard runtime dependency of `engine/generate.py`
that is likewise undeclared. Out of scope for 0.7, worth a follow-up.

## 1.8 Degradation

| Condition | Behaviour |
|---|---|
| `pdf_inspector` not importable | candidate absent; remediation identical to 0.6 |
| `extract_pages_markdown` raises | `parse_document` returns `{}`; candidate absent |
| page missing from the map | candidate absent for that page only |
| `inspector_enabled = 0` | candidate never constructed |
| `inspector_short_circuit = 0` | candidate still competes, paid passes always run |
| structure guard fires | candidate ineligible, reason recorded in `notes` |

In every row, the fallback is current behaviour. There is no state in which enabling
this feature can make a document fail to remediate.

## 1.9 Tests

Unit (`wikify/tests/`, hermetic, no network — LLM calls mocked as in
`test_remediate_pipeline.py`):

- `test_structure_guard.py` — `structure_signature` counts on committed synthetic
  markdown; each of the three `structure_preserved` rules fires on a crafted case and
  stays quiet on a clean one; a control where candidate structure is *richer* passes.
- `test_inspector_adapter.py` — 1-based conversion of a stubbed `PagesExtractionResult`;
  `{}` on raised exception; `{}` when the module is absent.
- `test_remediate_pipeline.py` (extend) — inspector candidate adopted when it wins and
  preserves structure; **rejected despite a higher composite** when the guard fires
  (the regression that matters most); short-circuit skips vlm/cleanup and records zero
  cost; short-circuit never fires on `kind="visual"`.
- `test_lint.py` (extend) — `prose_table` fires on a wide long-celled table, stays quiet
  on a normal one.

Live acceptance is per-slice in the plan, against `pdf.localhost`.
