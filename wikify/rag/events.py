from __future__ import annotations

from contextlib import contextmanager

import frappe
from frappe.utils.data import cint

# Safety net only — the job clears its own marker; this stops a crashed worker from wedging
# a project's index permanently.
PENDING_TTL_SECONDS = 1800

DIRTY_PAGES_HASH = "wikify_rag_dirty_pages"

# Past this many sections in one pass, one project rebuild beats a scoped `upsert_sections`:
# the batch re-embeds every section it touches anyway, so at some width re-embedding the whole
# project costs the same and leaves the index consistent in one commit instead of two.
PROJECT_REBUILD_FANOUT = 10


def pending_key(project: str) -> str:
	return f"wikify_rag_reindex_pending:{project}"


def page_propagation_key(source_document: str) -> str:
	return f"wikify_rag_page_propagation_pending:{source_document}"


def pass_already_queued(key: str) -> bool:
	# `cache.get_value` answers from `frappe.local.cache` once it has read a key, so a writer
	# that set the marker keeps reading its own stale "1" long after the worker cleared it.
	# `exists` is the only read here that goes to redis, which is where the worker clears it.
	return bool(frappe.cache().exists(key))


def indexing_suspended() -> bool:
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
	if indexing_suspended():
		return
	project = frappe.db.get_value("Source Document", doc.source_document, "project")
	if not project:
		return
	queue_project_rebuild(project)


def queue_project_rebuild(project: str) -> None:
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
	from wikify.rag.index import rebuild_project

	frappe.cache().delete_value(pending_key(project))
	rebuild_project(project)
	# background reindex — the job owns its transaction
	# nosemgrep
	frappe.db.commit()


def section_content_changed(section_names: list[str]) -> None:
	if indexing_suspended() or not section_names:
		return

	sections = frappe.get_all(
		"Source Section",
		filters={"name": ["in", section_names]},
		fields=["name", "source_document"],
	)
	documents = {row.source_document for row in sections if row.source_document}
	if not documents:
		return
	projects = {
		row.name: row.project
		for row in frappe.get_all(
			"Source Document", filters={"name": ["in", list(documents)]}, fields=["name", "project"]
		)
	}

	by_project: dict[str, list[str]] = {}
	for row in sections:
		project = projects.get(row.source_document)
		if project:
			by_project.setdefault(project, []).append(row.name)
	for project, names in by_project.items():
		reindex_sections(project, names)


def document_structure_changed(source_document: str) -> None:
	if indexing_suspended() or not source_document:
		return
	project = frappe.db.get_value("Source Document", source_document, "project")
	if project:
		queue_project_rebuild(project)


def page_content_changed(page_name: str) -> None:
	# This was a `Source Page.on_update` doc_event until 0.7 and therefore almost never ran:
	# every real write to `canonical_markdown` goes through `store.set_canonical*`, which use
	# `frappe.db.set_value` and fire no doc_event. Only the ORM saves in the tests saw it.
	if indexing_suspended():
		return
	page = frappe.db.get_value("Source Page", page_name, ["source_document", "page_no"], as_dict=True)
	if not page or not page.source_document:
		return
	mark_pages_dirty(page.source_document, [cint(page.page_no)])
	queue_page_propagation_pass(page.source_document, after_commit=True)


@contextmanager
def suspended_indexing():
	was_suspended = frappe.flags.wikify_skip_reindex
	frappe.flags.wikify_skip_reindex = True
	try:
		yield
	finally:
		frappe.flags.wikify_skip_reindex = was_suspended


def mark_pages_dirty(source_document: str, pages: list[int]) -> None:
	cache = frappe.cache()
	for page_no in pages:
		cache.hset(DIRTY_PAGES_HASH, f"{source_document}:{page_no}", 1)


def dirty_page_fields(source_document: str) -> list[str]:
	prefix = f"{source_document}:"
	fields = [
		field.decode() if isinstance(field, bytes) else field
		for field in frappe.cache().hgetall(DIRTY_PAGES_HASH)
	]
	return [field for field in fields if field.startswith(prefix)]


def take_dirty_pages(source_document: str) -> list[int]:
	claimed = dirty_page_fields(source_document)
	if claimed:
		frappe.cache().hdel(DIRTY_PAGES_HASH, claimed)
	return sorted({cint(field.split(":")[-1]) for field in claimed})


def queue_page_propagation_pass(source_document: str, after_commit: bool = False) -> None:
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
	mark_pages_dirty(source_document, pages)
	queue_page_propagation_pass(source_document)


def propagate_dirty_pages(source_document: str) -> None:
	frappe.cache().delete_value(page_propagation_key(source_document))
	pages = take_dirty_pages(source_document)
	if not pages:
		return
	try:
		propagate_pages(source_document, pages)
	finally:
		if dirty_page_fields(source_document):
			queue_page_propagation_pass(source_document)


def propagate_pages(source_document: str, pages: list[int]) -> None:
	from wikify.engine.sectionize import rebuild_section_markdown, sections_covering_page

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
	# background reindex — the job owns its transaction
	# nosemgrep
	frappe.db.commit()


def reindex_sections(project: str | None, section_names: list[str]) -> None:
	from wikify.rag import index

	if not project:
		return
	if len(section_names) > PROJECT_REBUILD_FANOUT:
		queue_project_rebuild(project)
		return
	index.upsert_sections(section_names)
