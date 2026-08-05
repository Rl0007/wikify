# 0.7 Implementation Plan — Tracer-Bullet Slices

Continues the spine (0.2: 10–16 · 0.3: 17–20 · 0.4: 21–25 · 0.5: 26–28 · 0.6: 29–31).
Numbering starts at **32**. Each slice cuts through every layer it touches and ends
demoable on its own.

> Source of truth for behavior is [`01-inspector-rescue.md`](01-inspector-rescue.md) and
> [`02-semantic-retrieval.md`](02-semantic-retrieval.md). This file is the *delivery
> order*.

## Slice map

| # | Slice | Type | Blocked by | Status |
|---|---|---|---|---|
| 32 | Structure guard + inspector adapter — `structure_signature`/`structure_preserved`, `parsers/inspector.py`, `prose_table` lint pattern | AFK | — | ⬜ |
| 33 | Inspector in remediation — candidate + winner rule, short-circuit, settings, DocType Selects, UI labels | AFK | 32 | ⬜ |
| 34 | Retrieval spine — `engine/retrieval/`, `set_wiki_content` funnel, index on generate, reindex API, eval harness | AFK | — | ⬜ |
| 35 | `search_wiki` agent tool — registration, project scoping, `search_sections` docstring, settings UI | HITL | 34 | ⬜ |

**Spine:** two independent tracks. 32 → 33 strictly sequential (33 adopts what 32 can
prove safe — building 33 first would ship the exact silent-flattening failure the spike
found). 34 → 35 strictly sequential (35 queries what 34 populates). The tracks share no
files and can land in either order.

The guiding move: **32 is pure backend and independently valuable** — the structure guard
and the `prose_table` pattern improve artifact detection for *every* parser, inspector or
not, and can ship before any decision about adopting inspector output. **34 is likewise
independently valuable**: the index and funnel are useful the moment they exist, even
before a tool queries them.

Land 32 first. It is the smallest slice, it de-risks 33 entirely, and its guard is the
one piece of this release that protects against a regression the current scoring cannot
see.

---

## Verification

Same protocol as 0.2–0.6: verify each slice against **`pdf.localhost`** (Administrator /
admin) before starting the next; `bench` from the bench root; `bench start` running for
anything touching jobs/realtime; `run-tests --app wikify` stays green throughout
(currently 180 tests across 19 modules).

Standing acceptance fixtures — the two real manuals are the test bed:

- `0pvfokvg4h` "Obstetrics and Gynaecology" (180 pp, `Wiki-Generated`, space
  `0rkkb8s039`, 358 indexable wiki documents) — **the structure-hostile document.**
  pdf-inspector drops headings 132→10 and list items 320→0 on the sampled pages here.
  Any inspector work must be verified against this document specifically.
- `1svt8pm07l` "Nephrology" (398 pp, `Parsed`) — **the structure-friendly document**
  (headings 78→72, lists 326→283). Inspector should be *adopted* on a meaningful number
  of pages here. If it is adopted on neither document, the guard is too tight; if it is
  adopted widely on obg, the guard is broken.
- Unit fixtures: committed synthetic markdown per guard rule + clean controls (the
  fixture-leak rule from f79eb48 applies — no site data in unit tests).

Reference numbers from the 2026-08-05 spike, for comparison during verification:

| | pymupdf4llm | pdf-inspector |
|---|---|---|
| `content_recall`, `review` pages (n=42) | 0.565 | 0.998 |
| `content_recall`, `escalate` pages (n=4) | 0.505 | 0.977 |
| whole-document extract | 258–272 ms/pg | 4.7–7.0 ms/pg |

---

## 32 — Structure guard + inspector adapter

**Demo:** in `bench console`, feed the guard a real pair of markdown strings from the OBG
manual (baseline vs inspector) → it returns `(False, "heading_loss")`. Feed it the
Nephrology equivalent → `(True, "")`. `parsers/inspector.py` returns a 1-based
`{page_no: markdown}` map for a 398-page PDF in under two seconds.

### What to build

- `engine/verify/deterministic.py`: `structure_signature`, `structure_preserved`, and the
  three threshold constants with the measurement recorded in a comment (§1.3).
- `engine/parsers/inspector.py`: `NAME`, `parse_document(pdf_path) -> dict[int, str]`.
  **`PageMarkdown.page` is 0-based** — convert at this seam and nowhere else. Return `{}`
  on any exception or missing module.
- `engine/lint.py`: `prose_table` pattern in `table_artifacts` (> 6 cells, mean non-empty
  cell length > ~40 chars).
- `pyproject.toml`: `pdf-inspector>=0.2.6`.
- Tests per §1.9 (`test_structure_guard.py`, `test_inspector_adapter.py`, `test_lint.py`
  extension).

### Acceptance criteria

- `parse_document` on Nephrology (398 pp) completes in < 3 s and returns 398 entries
  keyed 1–398, with entry `1` matching page 1 of the PDF (off-by-one is the failure mode
  this criterion exists to catch).
- Over the OBG sample, `structure_preserved` rejects on the order of half the pages;
  over Nephrology, it rejects roughly one in eight. Exact counts will move with sampling —
  what must hold is the **direction**: obg rejection rate is several times Nephrology's.
- Uninstalling `pdf_inspector` leaves `run-tests` green.
- `prose_table` does not fire on any existing `Source Section` that is currently lint-clean
  (check against the live site before committing the threshold).

---

## 33 — Inspector in remediation

**Demo:** run remediation over Nephrology with `bench start` up → the log stream shows
pages adopted with method `inspector` at zero cost, and the import's total `llm_cost` is
materially below a pre-0.7 run of the same document. Run it over OBG → inspector is
attempted and mostly rejected, with `heading_loss` visible in the page's remediation
notes, and the section tree is unchanged.

### What to build

- `engine/remediate.py`: document-level `inspector.parse_document` before the page loop;
  candidate construction (non-visual, non-empty, guard-gated); `_pick_winner` extension
  preserving the existing vlm/cleanup rule; short-circuit per §1.5.
- `Wikify Settings`: `inspector_enabled`, `inspector_short_circuit` (both Check, default
  1) + the settings UI fields.
- `Source Page`: add `inspector` to the `remediation_method` and `canonical_source`
  Selects. `bench --site pdf.localhost migrate`.
- Frontend: `inspector` label case wherever `canonical_source` / `remediation_method` are
  rendered.
- Tests per §1.9 — including the **guard-rejects-higher-composite** case, which is the
  regression that protects the section tree.

### Acceptance criteria

- On Nephrology, `inspector` is adopted on a non-trivial share of remediation targets and
  `canonical_mean` does not regress versus a pre-0.7 run.
- On OBG, section tree shape (`_tree_shape`-style comparison: names + parentage) is
  **identical** before and after enabling the feature. This is the criterion that proves
  the guard works end-to-end.
- Measured LLM-call saving on a full `scope="all"` Nephrology run is recorded in the
  slice's verification note — an actual number, not an estimate. This is the slice's
  headline result.
- `inspector_short_circuit = 0` reproduces 0.6 cost behaviour exactly.
- Short-circuit never fires on a `visual` page (assert, don't assume).
- Every rejected-but-scoring candidate leaves its reason in `Source Page.remediation_notes`.

---

## 34 — Retrieval spine

**Demo:** `bench --site pdf.localhost execute wikify.api.retrieval.reindex --kwargs "{'wiki_space': '0rkkb8s039'}"`
→ ~358 documents embedded and indexed in ~100 s → a console query for *"heavy bleeding
after delivery"* returns the postpartum haemorrhage page in the top 3, which the current
substring search cannot do at any k.

### What to build

- `engine/retrieval/{__init__,embed,index}.py` per §2.3 — lazy `_connect()`, **no
  module-import-time connection** (fork discipline).
- `Wikify Settings`: `embedding_model` (default `openai/text-embedding-3-small`),
  `embedding_api_key` (Password), with help text stating this is *not* the OpenRouter key.
- `store.set_wiki_content` funnel; route all five `engine/generate.py` write sites through
  it (`_upsert_wiki_document:35/:59`, `:241`, `:261`, `sync_section:352`) and wire the
  delete sweep at `:170` to `remove_document`.
- `jobs/generate.py::run`: batched `index_wiki_space` as a post-step, exception-wrapped.
- `api/retrieval.py`: whitelisted `reindex(wiki_space)` enqueuing a rebuild.
- `wikify/tests/evals/retrieval/` per §2.8, with the gold set committed.
- Unit tests: chunking, max-score dedup, `is_configured()` false paths, funnel coverage.

### Acceptance criteria

- With no `embedding_api_key`, wiki generation completes normally and logs one skip line.
  **Verify this before verifying the happy path** — degradation is the risk, indexing is
  the easy part.
- Reindex of `0rkkb8s039` completes; on-disk size is single-digit MB; `table.optimize()`
  runs.
- `run_retrieval_eval` reports recall@10 ≥ 0.70 against the committed gold set. The spike
  measured 0.794 with a *local* model; a hosted model under-performing that bar means
  something is wrong with chunking or dedup, not with the premise.
- Substring-baseline recall is re-measured and reported alongside, to keep the comparison
  honest as the corpus changes.
- Regenerating a wiki twice does not duplicate rows (`id` upsert is stable).
- Deleting a section that owns a wiki document removes its rows from the index.

---

## 35 — `search_wiki` agent tool

**Demo:** in the chat panel on a generated wiki, ask *"where does this manual cover
managing a patient with heavy post-delivery bleeding?"* → the agent calls `search_wiki`,
gets the right page, and answers with a link — with no attachment chip and no mention of
the page's title in the question.

### What to build

- `agent/tools/read.py`: `search_wiki(query, project=None, limit=5)` registered with the
  other read tools; results capped at `_BODY_LIMIT`; excerpt + `route` returned.
- `search_sections` docstring line pointing at `search_wiki` for content questions.
- Unconfigured path returns a clear, actionable message the model can relay.
- Extend `wikify/tests/evals/` with a scenario asserting `"search_wiki" in
  turn["tools_used"]` for a content question — tool *selection* is half the fix, since the
  0.000 baseline is partly the model reaching for the wrong instrument.

### Acceptance criteria

- A content question with no attachment chip is answered from the right page.
- A type-filter question (*"show me all the Procedure sections"*) still routes to
  `search_sections`, not `search_wiki`.
- With retrieval unconfigured, the agent says so plainly instead of hallucinating or
  silently returning nothing.
- Tool results respect `_BODY_LIMIT`; a broad query cannot blow the context budget.

---

## Out of scope, recorded so it is not mistaken for fallout

- **`engine/images.py:71-103` Case 1 is already dead code on this corpus.** A full
  578-page scan of both manuals found pymupdf4llm's
  `==> picture [W x H] intentionally omitted <==` marker fires **0 times**. Pre-existing,
  unrelated to 0.7, worth its own slice.
- **`pymupdf4llm` is silently invoking Tesseract OCR** on hundreds of pages in both
  manuals (visible on stderr during any parse), which is where its ~270 ms/page goes. Not
  addressed here; a likely source of further speed-up.
- **Undeclared dependencies:** `requests` (`engine/llm.py:17`) and `frappe-wiki`
  (`engine/generate.py`, `api/wiki.py`) are imported but absent from `pyproject.toml`.
- **`remediation_workers` / `classify_workers`** settings are stored and shown in the UI
  but never read — both passes are deliberately sequential
  (`engine/remediate.py:18-20`, `engine/classify.py:6-12`).
- **Hybrid search and reranking** — deferred until there is a tuned weight and a
  measurement showing it beats pure vector (§2.5).
- **Document-level PDF classification** via `detect_pdf` — deferred until there is a
  scanned corpus to validate it against (§1.2).
