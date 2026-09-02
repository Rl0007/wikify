# POC — LanceDB vector search + wiki chatbot

Delivers POC-2 (`specs/poc-2-sectioning-retrieval.md`) as a shippable demo on
`wikify.localhost`. Each numbered line is one task.

**Thesis (from the spec, unchanged):** "all X" is a **metadata filter**, not a
similarity guess. Vector search is the fuzzy leg only. The demo's punchline is showing
naive top-k RAG under-recalling where the routed hybrid does not.

## Phase 0 — retrieval foundation (`wikify/rag/`)

1. Probe + pin deps: `lancedb`, embedding backend, on py3.14 bench env; add to `pyproject.toml`.
2. `rag/store.py` — LanceDB connection at `sites/<site>/private/files/wikify_lance`, table schema, per-site isolation.
3. `rag/embed.py` — pluggable embedder (local static model default, OpenAI-compatible remote fallback), batched, dim advertised by provider.
4. `rag/chunk.py` — `Source Section` → chunks: sub-chunk oversized markdown, keep `parent_section` id (parent-document retrieval), carry `hierarchy_path`/`page_start`/`page_end`/`section_type` as metadata.
5. `rag/chunk.py` — contextual-retrieval prefix: prepend a one-line document+hierarchy context to each chunk's *embedded* text (not the stored text).
6. `rag/index.py` — build/refresh a project's index; upsert per section; delete orphans; idempotent re-runs.
7. `rag/index.py` — FTS index on chunk text (LanceDB native full-text) alongside the vector column.
8. `hooks.py` — reindex on `Source Section` after_insert/on_update/on_trash, enqueued on the `long` queue; index invalidation on document delete.
9. `rag/search.py` — hybrid search: vector + FTS fused with Reciprocal Rank Fusion, metadata **pre-filters** (project / source_document / section_type), returns chunks with citations.
10. `rag/search.py` — parent-expansion: dedupe hits back to parent sections and return the full section body for synthesis.

## Phase 1 — routing, API, agent tool

11. `rag/router.py` — cheap-model intent classifier: `exhaustive` → metadata filter (return **all** matches), `semantic` → hybrid top-k, `hybrid` → filter + vector.
12. `api/rag.py` — whitelisted `search(query, project, filters)` returning hits + scores + routes.
13. `api/rag.py` — whitelisted `ask(question, project)` — retrieve → synthesize → answer with inline citations; streams over realtime like the existing agent does.
14. `rag/answer.py` — grounded synthesis prompt + refusal guardrail ("not in the wiki") when top score is below threshold.
15. `agent/tools/retrieve.py` — `semantic_search` Tool registered in `build_default_registry()`, so the existing agent panel gains retrieval without loop changes.

## Phase 2 — surfaces

16. SPA page `AskWiki.vue` + route + `useCall` composable: question box, streamed answer, source cards.
17. Citation chips deep-link to the Source Section (SPA) and, when `wiki_document` is set, to the live wiki route.
18. Install `wiki` app on `wikify.localhost`, generate a Wiki Space from the demo corpus (existing `jobs/generate.py`).
19. ~~Wiki-embedded chat launcher~~ — **deferred by the user; not in this POC.** The wiki
    app stays installed and the space still gets generated, because citations deep-link to
    live wiki routes.

## Phase 3 — proof

20. Seed a demo corpus (3–5 PDFs with overlapping typed sections) so retrieval has real recall to demonstrate.
21. `tests/test_rag_eval.py` + a `bench execute` harness: golden questions, recall@k for naive top-k vs routed hybrid.
22. Unit tests: chunking boundaries, RRF fusion order, filter correctness, refusal guardrail.
23. Integration test: index → search → ask on the real DB (rolled back).
24. Browser pass on `wikify.localhost` (Administrator/Frappe@123) via agent-browser; screenshots.
25. `docs/` demo script + update `CLAUDE.md` gotchas.
