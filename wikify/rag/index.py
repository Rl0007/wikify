from __future__ import annotations

import time

import frappe

from wikify.rag import chunk, embed, search, store

FTS_COLUMN = "text"


def chunk_rows(chunks: list[chunk.Chunk]) -> list[dict]:
	vectors = embed.embed([item.embed_text for item in chunks])
	return [chunk.chunk_row(item, vector) for item, vector in zip(chunks, vectors, strict=True)]


def refresh_fts_index(table) -> None:
	from lancedb.index import FTS

	if not table.count_rows():
		return
	table.create_index(FTS_COLUMN, config=FTS())


def requeue_rebuild(project: str) -> None:
	from wikify.rag import events

	frappe.cache().set_value(events.pending_key(project), "1", expires_in_sec=events.PENDING_TTL_SECONDS)
	frappe.enqueue("wikify.rag.events.rebuild_pending_project", queue="long", timeout=3600, project=project)


def rebuild_project(project: str) -> dict:
	started = time.monotonic()
	chunks = chunk.chunks_for_project(project)
	table = store.chunks_table(create=True)
	rows = chunk_rows(chunks) if chunks else []
	try:
		table.delete(f"project = {store.sql_literal(project)}")
		if rows:
			table.add(rows)
		refresh_fts_index(table)
	except Exception:
		requeue_rebuild(project)
		raise
	return {
		"chunks": len(chunks),
		"sections": len({item.section for item in chunks}),
		"seconds": round(time.monotonic() - started, 3),
	}


def upsert_sections(section_names: list[str]) -> int:
	names = [name for name in dict.fromkeys(section_names) if name]
	if not names:
		return 0
	chunks = chunk.chunks_for_sections(names)
	table = store.chunks_table(create=True)
	rows = chunk_rows(chunks) if chunks else []
	scoped = ", ".join(store.sql_literal(name) for name in names)
	table.delete(f"section IN ({scoped})")
	if rows:
		table.add(rows)
	refresh_fts_index(table)
	return len(rows)


def upsert_section(section_name: str) -> int:
	return upsert_sections([section_name])


def drop_section(section_name: str) -> None:
	table = store.chunks_table()
	if table is None:
		return
	table.delete(f"section = {store.sql_literal(section_name)}")


def indexed_at(table) -> str | None:
	versions = table.list_versions()
	if not versions:
		return None
	timestamp = versions[-1].get("timestamp")
	return timestamp.isoformat() if timestamp else None


def index_stats(projects: list[str] | search.AclDecision = search.ACL_REQUIRED) -> dict:
	search.assert_acl_decision(projects)
	table = store.chunks_table()
	empty = {"chunks": 0, "sections": 0, "documents": 0, "indexed_at": None, "dim": embed.EMBED_DIM}
	if table is None or projects == []:
		return empty

	query = table.search().select(["section", "source_document"]).limit(None)
	if projects is not search.ALL_PROJECTS:
		allowed = ", ".join(store.sql_literal(name) for name in projects)
		query = query.where(f"project IN ({allowed})")
	rows = query.to_list()
	if not rows:
		return empty
	return {
		"chunks": len(rows),
		"sections": len({row["section"] for row in rows}),
		"documents": len({row["source_document"] for row in rows}),
		"indexed_at": indexed_at(table),
		"dim": embed.EMBED_DIM,
	}
