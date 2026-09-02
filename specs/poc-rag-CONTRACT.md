# RAG POC — frozen interface contract

Every agent builds against this. **Do not change a signature without updating this file
and saying so in your report.** Backend and frontend are built in parallel against it.

## Verified environment facts (already probed — do not re-probe)

- Bench root: `/Users/deathstarconsole/company_projects/frappe/develop` (run `bench` here).
- Site: **`wikify.localhost`**, login **Administrator / Frappe@123**. Apps: `frappe`, `wikify`, `wiki`.
- Python 3.14. `lancedb==0.36.0` and `model2vec` are **already installed** in `env/bin/python`
  and pinned in `apps/wikify/pyproject.toml`.
- Embedding model: `minishlab/potion-base-8M` via `model2vec.StaticModel.from_pretrained`,
  **256 dims**, already downloaded to the HF cache. numpy-only, no torch, no API key.
- LanceDB verified working for: vector search, `create_index(config=FTS())` full-text,
  `.where("col='x'")` metadata filters, and `search(query_type='hybrid')` (RRF-fused).
  Note `create_fts_index` is deprecated — use `create_index(config=FTS())`.

## Python layer — `wikify/rag/`

```python
# rag/embed.py
def embed(texts: list[str]) -> list[list[float]]      # batched, 256-dim
def embed_one(text: str) -> list[float]
EMBED_DIM: int = 256

# rag/store.py
def connect()                                          # lancedb at sites/<site>/private/files/wikify_lance
def chunks_table(create: bool = False)                 # the single `chunks` table

# rag/chunk.py
@dataclass
class Chunk:
    id: str                # f"{section}::{ordinal}"
    section: str           # Source Section name  (parent-document retrieval anchor)
    source_document: str
    project: str
    text: str              # stored text, shown to the user
    embed_text: str        # contextual-retrieval text: "<doc title> › <hierarchy path>\n\n" + text
    section_type: str | None
    hierarchy_path: str
    page_start: int
    page_end: int
    wiki_route: str | None # deep link when Source Section.wiki_document is set

def chunks_for_section(section_name: str) -> list[Chunk]
def chunks_for_project(project: str) -> list[Chunk]

# rag/index.py
def rebuild_project(project: str) -> dict            # {"chunks": n, "sections": n, "seconds": f}
def upsert_section(section_name: str) -> int
def drop_section(section_name: str) -> None
def index_stats(project: str | None = None) -> dict  # {"chunks","sections","documents","indexed_at","dim"}

# rag/search.py
@dataclass
class Hit:
    chunk_id: str; section: str; source_document: str; document_title: str
    title: str; text: str; section_type: str | None; hierarchy_path: str
    page_start: int; page_end: int; wiki_route: str | None
    score: float; vector_rank: int | None; fts_rank: int | None; rerank_score: float | None

def search(query, *, project=None, source_document=None, section_type=None,
           limit=8, mode="hybrid", rerank=False) -> list[Hit]
    # mode: "vector" | "fts" | "hybrid" (RRF) | "filter" (exhaustive, returns ALL matches)

# rag/router.py
@dataclass
class Route:
    intent: str            # "exhaustive" | "semantic" | "hybrid"
    section_type: str | None
    query: str             # rewritten standalone query
    reason: str            # one line, SHOWN IN THE UI
def route(question: str, project: str | None, history: list | None = None) -> Route

# rag/answer.py
def answer(question, *, project=None, history=None, rerank=True) -> dict
    # {"answer": md_with_[1]_citations, "citations": [Hit...], "route": Route, "refused": bool}
```

## Whitelisted API — `wikify/api/rag.py`

All take/return plain JSON. All respect permissions (see ACL below).

| Method | Args | Returns |
|---|---|---|
| `wikify.api.rag.search` | `query, project=None, source_document=None, section_type=None, limit=8, mode="hybrid", rerank=False` | `{"hits": [Hit-as-dict], "route": {...}, "took_ms": int, "mode": str}` |
| `wikify.api.rag.ask` | `question, project=None, session=None, rerank=True` | `{"answer","citations","route","refused","took_ms"}` |
| `wikify.api.rag.index_status` | `project=None` | `{"chunks","sections","documents","indexed_at","dim","stale": bool}` |
| `wikify.api.rag.reindex` | `project` | enqueues on `long` queue → `{"job": name}` |
| `wikify.api.rag.compare` | `query, project=None` | `{"naive": [Hit], "routed": [Hit], "route": {...}}` — **the demo punchline** |

**Streaming:** `ask` also publishes realtime events on `wikify_rag_answer` with
`{"session","delta"|"citations"|"route"|"done"}`, mirroring how `agent/loop.py` streams.
Sources are published **before** the answer deltas.

**ACL:** every read path filters to projects the user can read
(`frappe.has_permission("Wikify Project", doc=project)`); `search()` takes an internal
`allowed_projects` pre-filter applied **inside the LanceDB `.where()`**, never post-hoc.

## Agent tool

`wikify/agent/tools/retrieve.py` exports `TOOLS` with a `semantic_search` `Tool`
(side `server`, `confirm=False`, `mutates=False`), registered by adding the module to
`build_default_registry()` in `agent/registry.py`. This gives multi-hop retrieval inside
the existing loop for free.

## Frontend — routes under the `/wikify` SPA

- `/ask` → `AskWiki.vue`: question box, streamed answer, **sources rendered before the
  answer**, citation chips `[1]` that scroll to the source card and deep-link to the
  wiki route / section.
- `/ask` shows a **route badge** (`exhaustive` / `semantic` / `hybrid`) with `route.reason`
  — the routing decision must be *visible*, it is the core thesis.
- `/rag-lab` → `RagLab.vue`: the visual proof surface. Side-by-side **naive top-k vs routed
  hybrid** for a query (`api.rag.compare`), per-hit score bars, vector-rank vs fts-rank
  badges, and the eval scoreboard (recall@k per golden question).
- Index status card (chunk/section/document counts, dim, stale flag, Reindex button).
- frappe-ui v1 + `useCall`/`useList`/`useDoc` (v3, **not** `createResource`).
  Semantic tokens only (`bg-surface-*`, `text-ink-*`, `border-outline-*`).

## Wiki-embedded surface — DEFERRED

The in-wiki chat launcher is **out of scope for this POC** (user's call). Do not build it.
The `wiki` app stays installed and the Wiki Space still gets generated, because citations
deep-link to live wiki routes via `Source Section.wiki_document`.

## House rules (non-negotiable)

- Full variable names (`source_document`, not `sd`). No leading-underscore functions.
- No `{"success": True}` envelopes — `frappe.throw` on error, return data on success.
- `frappe.get_all` with all needed fields; **never** `get_doc` in a loop.
- No raw SQL. `frappe.parse_json`, `cint/cstr/flt` from `frappe.utils.data`.
- Comments explain **why**, never what. Deliberate shortcuts get `# ponytail: <limit>, <trigger>`.
- Ruff: line 110, tabs, double quotes. Biome for JS: tabs, single quotes.
- async/await only on the frontend.
