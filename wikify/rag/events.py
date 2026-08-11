"""Keep sections and the retrieval index in step with edits upstream of them.

The content chain is `Source Page.canonical_markdown` → `Source Section.markdown` →
chunks → index, and every link is a **copy**, so a fix that stops at one link leaves the
ones after it serving the old text. Two handlers close it:

- `queue_reindex` (`Source Section`) — a section edit makes the project's index stale.
- `queue_page_propagation` (`Source Page`) — a page fix makes every section built from
  that page stale, and therefore the index too. Without it a re-remediated page is
  correct in Page Review while a corrupted copy of the same table keeps being retrieved,
  until somebody re-sections the whole document by hand.

Both coalesce, because a bulk pass writes hundreds of rows in one transaction and one job
per row would bury the long queue: the first change marks a pending key and enqueues one
job, further changes while that job is queued enqueue nothing. Each job clears its marker
*before* it starts work, so a change landing mid-run queues the next pass and is never
lost (the failure mode is one redundant pass, never stale content).
"""

from __future__ import annotations

import frappe
from frappe.utils.data import cint

# Safety net only — the job clears its own marker; this stops a crashed worker from
# wedging a project's index permanently.
PENDING_TTL_SECONDS = 1800

# Redis hash accumulating the pages whose canonical markdown changed since the last
# propagation pass, keyed `<source_document>:<page_no>`. A set, not a counter, so a page
# saved ten times costs one rebuild.
DIRTY_PAGES_HASH = "wikify_rag_dirty_pages"

# Past this many sections in one pass, one project rebuild beats a scoped `upsert_sections`:
# the batch still re-embeds every section it touches, so at some width re-embedding the whole
# project costs the same and leaves the index consistent in one commit instead of two.
PROJECT_REBUILD_FANOUT = 10

# ponytail: coalescing rebuilds the WHOLE project, so a one-word title fix re-embeds every
# chunk in it; switch to per-section `upsert_section` (keyed by a per-section marker) once
# a project's rebuild stops fitting comfortably in the long queue's timeout.


def pending_key(project: str) -> str:
	return f"wikify_rag_reindex_pending:{project}"


def page_propagation_key(source_document: str) -> str:
	return f"wikify_rag_page_propagation_pending:{source_document}"


def pass_already_queued(key: str) -> bool:
	"""Is a job holding this marker right now?

	`cache.get_value` answers from `frappe.local.cache` once it has read a key, so a writer
	that set the marker keeps reading its own stale "1" long after the worker cleared it —
	and then coalesces every later change into a job that already finished. `exists` is the
	only read here that goes to redis, which is where the worker actually clears it.
	"""
	return bool(frappe.cache().exists(key))


def indexing_suspended() -> bool:
	"""Bulk/system contexts index once at the end, not row by row."""
	flags = frappe.flags
	return bool(
		flags.get("wikify_skip_reindex")
		or flags.in_install
		or flags.in_migrate
		or flags.in_patch
		or flags.in_import
		or flags.in_test
	)


def queue_reindex(doc, method: str | None = None) -> None:
	"""`Source Section` doc_event handler — coalesce a project rebuild onto the long queue."""
	if indexing_suspended():
		return
	project = frappe.db.get_value("Source Document", doc.source_document, "project")
	# A document outside any project has nothing to scope an index to; it becomes
	# searchable when it is assigned to one (which reindexes then).
	if not project:
		return
	queue_project_rebuild(project)


def queue_project_rebuild(project: str) -> None:
	"""Mark the project pending and enqueue one rebuild; a no-op while one is already queued."""
	key = pending_key(project)
	if pass_already_queued(key):
		return
	frappe.cache().set_value(key, "1", expires_in_sec=PENDING_TTL_SECONDS)
	frappe.enqueue(
		"wikify.rag.events.rebuild_pending_project",
		queue="long",
		timeout=3600,
		enqueue_after_commit=True,
		project=project,
	)


def rebuild_pending_project(project: str) -> None:
	"""Clear the pending marker, then rebuild — edits during the run queue the next pass."""
	from wikify.rag.index import rebuild_project

	frappe.cache().delete_value(pending_key(project))
	rebuild_project(project)
	frappe.db.commit()


def queue_page_propagation(doc, method: str | None = None) -> None:
	"""`Source Page` doc_event handler — coalesce a section rebuild onto the long queue."""
	if indexing_suspended():
		return
	# A page created just now was never copied into a section, so nothing downstream of it
	# can be stale; `has_value_changed` reads any insert as a change, hence the explicit skip.
	if doc.get_doc_before_save() is None:
		return
	if not doc.has_value_changed("canonical_markdown"):
		return

	mark_pages_dirty(doc.source_document, [cint(doc.page_no)])
	queue_page_propagation_pass(doc.source_document, after_commit=True)


def mark_pages_dirty(source_document: str, pages: list[int]) -> None:
	"""One hash field per page, so two writers marking different pages can't clobber each
	other the way a read-modify-write of a per-document list would."""
	cache = frappe.cache()
	for page_no in pages:
		cache.hset(DIRTY_PAGES_HASH, f"{source_document}:{page_no}", 1)


def dirty_page_fields(source_document: str) -> list[str]:
	"""The hash fields waiting to be propagated for one document (peek, no claim)."""
	prefix = f"{source_document}:"
	fields = [
		field.decode() if isinstance(field, bytes) else field
		for field in frappe.cache().hgetall(DIRTY_PAGES_HASH)
	]
	return [field for field in fields if field.startswith(prefix)]


def take_dirty_pages(source_document: str) -> list[int]:
	"""Claim (read and clear) the page numbers accumulated for one document."""
	claimed = dirty_page_fields(source_document)
	if claimed:
		frappe.cache().hdel(DIRTY_PAGES_HASH, claimed)
	return sorted({cint(field.split(":")[-1]) for field in claimed})


def queue_page_propagation_pass(source_document: str, after_commit: bool = False) -> None:
	"""Mark the document pending and enqueue one pass; a no-op while one is already queued.

	`after_commit` for the doc_event path only: the pass reads the page back from the
	database, so it must not start before the write that triggered it is committed.
	"""
	key = page_propagation_key(source_document)
	if pass_already_queued(key):
		return
	frappe.cache().set_value(key, "1", expires_in_sec=PENDING_TTL_SECONDS)
	frappe.enqueue(
		"wikify.rag.events.propagate_dirty_pages",
		queue="long",
		timeout=3600,
		enqueue_after_commit=after_commit,
		source_document=source_document,
	)


def requeue_page_propagation(source_document: str, pages: list[int]) -> None:
	"""Hand a half-finished propagation pass back to the queue (mirrors `index.requeue_rebuild`).

	The markdown rebuild and the re-index are separate commits, so a worker dying between
	them leaves a section holding the new text while the index still serves chunks of the
	old — a mismatch nothing else would ever notice. Re-arming the claimed pages together
	with the marker keeps the invariant "marker set means a pass is queued".
	# ponytail: no backoff and no attempt cap, mirroring `index.requeue_rebuild`; add a
	# retry counter if a propagation ever fails for a non-transient reason.
	"""
	mark_pages_dirty(source_document, pages)
	queue_page_propagation_pass(source_document)


def propagate_dirty_pages(source_document: str) -> None:
	"""Claim the pages changed since the last pass and propagate them (the queued entry point)."""
	frappe.cache().delete_value(page_propagation_key(source_document))
	pages = take_dirty_pages(source_document)
	if not pages:
		return
	try:
		propagate_pages(source_document, pages)
	finally:
		# A writer that marked pages dirty while this pass was already queued deliberately
		# enqueued nothing — it relies on this pass to pick them up. If it lost the race
		# against the claim above, its pages sit in the hash with no job behind them and go
		# stale forever (observed on a 30-page bulk write). Re-arm instead of stranding them.
		if dirty_page_fields(source_document):
			queue_page_propagation_pass(source_document)


def propagate_pages(source_document: str, pages: list[int]) -> None:
	"""Rebuild the sections covering `pages`, then push them into the index."""
	from wikify.engine.sectionize import rebuild_section_markdown, sections_covering_page

	# ponytail: one covering-section query per dirty page — fine for the handful an edit or a
	# page-scoped re-parse touches; batch into a single range query if one pass ever carries
	# a whole document's worth of pages.
	section_names = []
	for page_no in pages:
		for section in sections_covering_page(source_document, page_no):
			if section.name not in section_names:
				section_names.append(section.name)
	if not section_names:
		return

	project = frappe.db.get_value("Source Document", source_document, "project")
	try:
		for section_name in section_names:
			rebuild_section_markdown(section_name)
		reindex_sections(project, section_names)
	except Exception:
		requeue_page_propagation(source_document, pages)
		raise
	frappe.db.commit()


def reindex_sections(project: str | None, section_names: list[str]) -> None:
	"""Push rebuilt sections into the retrieval index.

	`rebuild_section_markdown` writes through `frappe.db.set_value`, which fires no
	doc_event — the `Source Section` reindex hook never sees it, so the index has to be
	told here explicitly.
	"""
	from wikify.rag import index

	# A document outside any project has nothing to scope an index to; it becomes
	# searchable when it is assigned to one (which reindexes then).
	if not project:
		return
	if len(section_names) > PROJECT_REBUILD_FANOUT:
		queue_project_rebuild(project)
		return
	index.upsert_sections(section_names)
