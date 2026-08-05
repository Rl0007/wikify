# 0.7 · Semantic Retrieval over Generated Wiki Pages

> Behavior spec. Delivery order lives in [`IMPLEMENTATION-PLAN.md`](IMPLEMENTATION-PLAN.md).

## 2.1 The gap, measured

`agent/tools/read.py:132-173` is the only cross-document search in the product. It
filters `Source Section` by `section_type`, then does a Python `in` substring test
against `title` and `hierarchy_path`. **It never reads a body.**

Measured against the real OBG wiki (358 documents, 180 generated questions):

| Arm | recall@1 | recall@5 | recall@10 | MRR@10 |
|---|---|---|---|---|
| substring (today) | 0.000 | 0.000 | **0.000** | 0.000 |
| full-text (tantivy) | — | — | 0.611 | 0.417 |
| **vector (bge-small)** | — | — | **0.794** | **0.534** |
| hybrid, default RRF | — | — | 0.728 | 0.528 |

Zero on all 180. That is not a tuning problem: the tool asks whether the *entire query
string* is a substring of a title, and a real question never is.

By query type — the reason this release picks vector over full-text:

| Query type | FTS | Vector | Δ |
|---|---|---|---|
| keyword | 0.883 | 0.917 | +0.03 |
| **semantic** (paraphrased) | 0.433 | **0.750** | **+0.32** |
| **vague** | 0.517 | **0.717** | **+0.20** |

Keyword retrieval is already a solved problem here — `frappe-wiki` ships
`WikiSQLiteSearch` (FTS5 over `Wiki Document.title` + `content`, registered via the
`sqlite_search` hook in `wiki/hooks.py:29`, live at `sites/<site>/wiki_search.db`). 0.7
does not duplicate it. It adds the axis FTS structurally cannot serve.

**Caveats carried forward honestly.** The gold set is synthetic — an LLM wrote each
question while looking at its target page, which inflates absolute recall for every arm;
the *gaps between arms* are the trustworthy signal. The embedding model in the spike was
local `bge-small-en-v1.5` because no embedding key exists on the bench, so 0.794 is a
**lower bound** for the hosted model this spec adopts.

## 2.2 Scope

Index target is **`Wiki Document`** — the generated wiki pages, chosen by the product
owner. Not `Source Section`, and not the hard-deprecated `Wiki Page`.

Consequence to accept knowingly: content is only searchable *after* wiki generation. An
import sitting in `Review` has nothing in the index. `Source Section` carries richer
filter metadata (`page_start`/`page_end`, `section_type`) that this choice forgoes; the
back-link is preserved (`Source Section.wiki_document`) so a later slice can join or
switch targets without a migration.

## 2.3 The seam — `wikify/engine/retrieval/`

New package, deliberately narrow, mirroring how `engine/store.py` isolates the ORM:

| Module | Responsibility |
|---|---|
| `retrieval/embed.py` | text → vectors via litellm. Batching, truncation, model/key resolution. |
| `retrieval/index.py` | LanceDB connection, schema, upsert, delete, search, optimize. |
| `retrieval/__init__.py` | The only public surface: `index_wiki_space`, `remove_document`, `search`, `is_configured`. |

Nothing outside this package imports `lancedb` or `litellm.embedding`.

### Storage

Embedded LanceDB, one directory per site:

```
sites/<site>/private/files/lance/
```

Resolved with `frappe.get_site_path("private", "files", "lance")` — per-site isolation
for free, inside the private files tree so it is never web-served, and it rides existing
backup/permission conventions. No server process, no new port, no daemon.

Table `wiki_chunks`, one row per chunk:

| Column | Type | Notes |
|---|---|---|
| `id` | str | `f"{wiki_document}::{chunk_ix}"` — stable, so re-index is an upsert |
| `wiki_document` | str | the Frappe docname; the retrieval unit |
| `source_document` | str | for project/document scoping |
| `project` | str | filter axis for the agent tool |
| `title` | str | returned for display |
| `route` | str | returned so the agent can link |
| `chunk_ix` | int | ordinal within the page |
| `text` | str | the chunk body |
| `vector` | vector(N) | N from the configured model |

### Chunking

1800 characters, 200 overlap — the spike's configuration, which produced 396 chunks from
358 documents (most wiki pages fit in one chunk; median content length is 368 chars).
Split on paragraph boundaries where possible, hard-split only when a paragraph exceeds
the window.

A query returns **documents, not chunks**: score each `wiki_document` by the max score
over its chunks, deduplicate, then rank. Retrieving the same page three times because it
chunked three ways is a bug, not a result.

Skip documents with `is_group = 1` and trivial bodies (< 400 characters) — these are ToC
and grouping nodes whose content is a generated index. The spike skipped 25 of 383 nodes
this way. Log the skip count; never skip silently.

### Embeddings

`litellm.embedding` (v1.83.7 on the bench, confirmed present — `litellm` is already a
declared dependency, so this adds **no new Python package**).

| Setting | Default |
|---|---|
| `Wikify Settings.embedding_model` | `openai/text-embedding-3-small` |
| `Wikify Settings.embedding_api_key` | Password, empty |

Key resolution follows the existing `engine/settings.py:23-82` ladder exactly (Settings
password → `site_config` → env → `apps/wikify/.env`) so operators configure this the way
they already configure `OPENROUTER_KEY`. **OpenRouter has no embeddings endpoint** — this
is a separate key and the settings UI must say so plainly.

Batch inputs (the LanceDB agent guidance is explicit that row-at-a-time ingestion is the
wrong shape). Cost is negligible: ~400 chunks per manual against
`text-embedding-3-small` is a fraction of a cent.

Vector dimension is read from the first embedding response rather than hardcoded, so
swapping models is a reindex and not a code change. A dimension change against an
existing table is a rebuild, not a migration — see §2.7.

### Fork discipline — non-negotiable

Frappe's RQ workers fork per job, and LanceDB's Rust core is multithreaded internally.
Forking a process that has already initialised Lance's thread pool is the documented way
to deadlock it.

Therefore: **the connection is opened lazily inside the job body, never at module
import.** `index.py` holds a module-level `_db = None` and a `_connect()` that populates
it on first use. No `lancedb.connect()` at import time, no connection cached on a
long-lived object that predates the fork. A spike subprocess opening a fresh connection
and querying worked cleanly (0.96 s) — one observation, not a stress test, which is
exactly why the discipline is a rule rather than a hope.

Writes are single-writer by construction: only the generate/reindex job writes. Readers
(web workers serving the agent tool) are concurrent and safe under Lance's MVCC. Call
`table.optimize()` after an ingest run to compact fragments.

## 2.4 Keeping the index in sync

`Wiki Document.content` is currently written from **five** places in
`engine/generate.py`:

- `_upsert_wiki_document:35` (assigns `doc.content` at `:59`)
- the two-pass link rewrite — direct `frappe.db.set_value` at `:241` and `:261`
- `sync_section:352` — the 0.3 per-section push
- the deletion sweep at `:170` (`frappe.delete_doc`)

Five writers is four too many to hook individually. 0.7 consolidates them behind one
funnel, the same move 0.6 made for `Source Section.markdown`
(`store.set_section_markdown`, `store.py:280-290`) and 0.5 made for reference extraction:

```python
# wikify/engine/store.py
def set_wiki_content(name: str, content: str, *, update_modified: bool = False) -> None:
    """THE write funnel for `Wiki Document.content` (0.7) — every content write goes
    through here so the retrieval index always reflects the stored body."""
```

**Indexing does not ride the funnel synchronously.** Embedding is a network call; a
generation run that writes 358 pages must not make 358 of them inline. Instead:

- the funnel marks the document dirty (in-run set, or `Wiki Document.modified`),
- `jobs/generate.py::run` calls `retrieval.index_wiki_space(...)` **once** at the end,
  after generation completes, batching every changed page,
- `generate.sync_section` (the single-page path, already interactive and already slow)
  indexes that one document inline,
- the deletion sweep calls `retrieval.remove_document(name)`.

A new `wikify.api.retrieval.reindex` whitelisted method enqueues a full rebuild for a
space, for operator recovery and after an `embedding_model` change.

Index build cost, measured: 57.6 s for 358 documents including model load, extrapolating
to ~100 s for a 398-page manual. On-disk 1.35 MB, extrapolating to ~3 MB. Query latency
7.3 ms. None of this needs a progress bar.

## 2.5 The agent tool

New tool in `agent/tools/read.py`, registered alongside the existing read tools:

```
search_wiki(query: str, project: str | None = None, limit: int = 5)
```

Returns ranked `{wiki_document, title, route, score, excerpt}`. Results are capped at the
module's existing `_BODY_LIMIT` (6000 chars, `read.py:17`) — the same budget discipline
every other read tool follows.

`search_sections` is **kept, not replaced**. It answers a genuinely different question
("show me every section of type X"), which is a metadata filter and correct as designed
(`api/explore.py:3-7`). Its docstring gains a line pointing at `search_wiki` for content
questions, so the model stops reaching for the wrong instrument — the 0.000 measurement
is substantially a tool-selection failure as well as a capability gap.

Pure vector search. **Not hybrid.** The spike measured default equal-weight RRF hybrid
losing to plain vector (0.728 vs 0.794): hybrid wins on keyword queries but the weak FTS
ranking drags fusion down on exactly the semantic and vague queries the agent actually
issues. Hybrid returns when there is a tuned weight and a measurement to justify it.

## 2.6 Degradation

| Condition | Behaviour |
|---|---|
| no `embedding_api_key` | `is_configured()` false → indexing skipped with one log line; `search_wiki` returns "semantic search is not configured" |
| `lancedb` not importable | same as above; nothing else in the app imports it |
| embedding call fails mid-index | that batch is logged and skipped; generation still completes |
| index directory missing/corrupt | `search_wiki` reports the index is unbuilt and names the reindex action |
| space never generated | empty result, not an error |

**Wiki generation must never fail because retrieval failed.** Indexing is a post-step
wrapped so its exceptions are logged, not raised. The index is derived and disposable
(README principle 4) — losing it costs one reindex.

## 2.7 Reindex, not migrate

The index is a projection. There is no schema migration path and there should not be:
changing `embedding_model`, changing the chunk window, or a corrupt directory are all
resolved by dropping the table and rebuilding from Frappe.

`api.retrieval.reindex(wiki_space)` enqueues exactly that. Changing `embedding_model` in
`Wikify Settings` must warn that existing vectors become invalid until a reindex runs —
a table containing two models' vectors returns silently garbage rankings, which is the
worst failure mode available here.

## 2.8 Evaluation

Retrieval quality is a number, so 0.7 keeps measuring it. Port the spike harness into
`wikify/tests/evals/retrieval/`, alongside the existing live agent evals (which are
deliberately excluded from `run-tests` because they cost real tokens —
`tests/evals/__init__.py:1-17`):

- `build_gold_set.py` — samples N wiki documents, generates keyword/semantic/vague
  questions via OpenRouter, **caches to a committed JSON** so reruns are free and
  comparable across changes. The cached gold set is the artifact; regenerating it resets
  the baseline.
- `run_retrieval_eval.py` — recall@{1,3,5,10} and MRR@10 per arm and per query type.
- Run manually, matching the existing convention:

```bash
bench --site pdf.localhost execute wikify.tests.evals.retrieval.run
```

The existing eval harness is boolean pass/fail (`scenarios.py:26`); this one is numeric
and reports a table. It is a tracked metric, not a gate — the acceptance bar for Slice 34
is stated in the plan.

Unit tests (hermetic, embeddings mocked): chunking boundaries and overlap; max-score
document dedup across chunks; `is_configured()` false paths; `set_wiki_content` funnel
coverage; `remove_document` on the delete sweep.
