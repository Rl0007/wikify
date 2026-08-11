"""Retrieval core for the RAG POC — chunking, embedding, LanceDB storage and search.

Layout mirrors the frozen contract in `specs/poc-rag-CONTRACT.md`:

- `embed`  — model2vec static embeddings (256-dim, numpy-only, no API key).
- `store`  — the LanceDB connection + the single `chunks` table (explicit pyarrow schema).
- `chunk`  — Source Section → Chunk, with contextual-retrieval prefixes on `embed_text`.
- `index`  — build / refresh / drop index rows for a project or a single section.
- `search` — vector | fts | hybrid (native RRF) | filter (exhaustive), then parent expansion.

Nothing here is imported at app boot: `lancedb` and `model2vec` are heavy, so submodules
are imported by the API/job layers on demand.
"""
