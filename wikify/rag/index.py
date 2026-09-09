"""Building and refreshing the LanceDB `chunks` table.

Every write is delete-then-add scoped by `project` or `section`, which makes rebuilds
idempotent and disposes of orphans (sections deleted in the tree review, or documents
moved to another project) without a separate reconciliation pass.
"""

from __future__ import annotations

import time

import frappe

from wikify.rag import chunk, embed, search, store

FTS_COLUMN = "text"


def chunk_rows(chunks: list[chunk.Chunk]) -> list[dict]:
	"""Chunks → LanceDB rows, embedding the whole batch in one `encode` call.

	The row shape lives next to the dataclass in `chunk.chunk_row`: LanceDB accepts a row
	that omits a declared column and stores it null rather than raising, so a field added to
	the schema but forgotten here reads back empty on every chunk instead of failing loudly.
	"""
	vectors = embed.embed([item.embed_text for item in chunks])
	return [chunk.chunk_row(item, vector) for item, vector in zip(chunks, vectors, strict=True)]


def refresh_fts_index(table) -> None:
	"""(Re)create the full-text index over `text`.

	Rebuilt after every write because an FTS index only covers the rows present when it was
	built; searching then falls back to a flat scan for the rest, which scores differently.
	# ponytail: full FTS rebuild per write is fine at POC corpus size, move to an
	# incremental `optimize()` once a project exceeds ~100k chunks.
	# ponytail: two rebuilds racing overwrite each other's index, so the loser's rows stay
	# FTS-invisible until the next write; serialise on a per-table lock once more than one
	# project can be rebuilt at a time.
	"""
	from lancedb.index import FTS

	if not table.count_rows():
		return
	table.create_index(FTS_COLUMN, config=FTS())


def requeue_rebuild(project: str) -> None:
	"""Hand a half-finished rebuild back to the coalescing queue.

	The delete and the add are separate LanceDB commits, so a worker that dies between them
	leaves the project with zero rows — silently unsearchable, with no pending marker and
	nothing queued, until somebody happens to edit a section. Re-arming the marker together
	with the job keeps `events.py`'s invariant (marker set means a rebuild is queued).
	# ponytail: no backoff and no attempt cap, so a project that fails deterministically
	# re-queues forever; add a retry counter if a rebuild ever fails for a non-transient reason.
	"""
	from wikify.rag import events

	frappe.cache().set_value(events.pending_key(project), "1", expires_in_sec=events.PENDING_TTL_SECONDS)
	frappe.enqueue("wikify.rag.events.rebuild_pending_project", queue="long", timeout=3600, project=project)


def rebuild_project(project: str) -> dict:
	"""Re-index a whole project from scratch. Idempotent; orphaned rows are dropped."""
	started = time.monotonic()
	chunks = chunk.chunks_for_project(project)
	table = store.chunks_table(create=True)
	# Embed before deleting anything: embedding is the slow step and the one most likely to
	# die, and doing it first keeps the window where the project has no rows to two adjacent
	# LanceDB commits instead of spanning the whole encode.
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
	"""Re-index a batch of sections (after an edit/split/merge, or a page propagation pass).

	One delete, one add and one FTS rebuild for the whole batch. Done per section, a
	ten-section propagation pass rebuilt the entire full-text index ten times and re-read
	every page of the document ten times over — the cost was in the fan-out, not the work.
	Returns the chunk count written. Sized for the handful of sections one edit or one
	propagation pass touches — `events.PROJECT_REBUILD_FANOUT` sends anything wider to a
	whole-project rebuild rather than building an unbounded `IN (...)` clause here.
	"""
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
	"""Re-index one section. Returns the chunk count written."""
	return upsert_sections([section_name])


def drop_section(section_name: str) -> None:
	table = store.chunks_table()
	if table is None:
		return
	table.delete(f"section = {store.sql_literal(section_name)}")


def indexed_at(table) -> str | None:
	"""Timestamp of the table's latest version — 'when was this index last written'."""
	versions = table.list_versions()
	if not versions:
		return None
	timestamp = versions[-1].get("timestamp")
	return timestamp.isoformat() if timestamp else None


def index_stats(projects: list[str] | search.AclDecision = search.ACL_REQUIRED) -> dict:
	"""Counts for the index-status card, scoped to `projects`.

	Takes the same ACL decision `search()` does, and for the same reason: this reads the
	chunk table, so an omitted argument would report how much content the reader cannot see.
	It used to default to `None` — the exact permissive default the `AclDecision` sentinels
	were introduced to make impossible. `ALL_PROJECTS` is how a background caller opts out.

	One scan for the whole scope, aggregated in Python: asking per project meant a fresh
	LanceDB connection and a full table read for every project the reader could see, which
	is the same rows re-scanned N times to produce one card.
	"""
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
