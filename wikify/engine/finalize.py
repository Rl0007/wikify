from __future__ import annotations

from collections.abc import Callable

from wikify.engine import store
from wikify.engine.loader.cleanup import clean_pages
from wikify.rag import events


def finalize_document(
	source_document: str,
	pdf_path: str,
	progress_cb: Callable[[int, int], None] | None = None,
	stage_cb: Callable[[str], None] | None = None,
) -> dict:
	from wikify.engine.sectionize import rebuild_and_classify

	if stage_cb:
		stage_cb("Removing running furniture")

	pages = store.get_finalize_pages(source_document)
	name_by_page = {p["page_no"]: p["name"] for p in pages}
	original = {p["page_no"]: p["markdown"] for p in pages}

	cleaned = dict(clean_pages([(p["page_no"], p["markdown"]) for p in pages]))

	pages_changed = 0
	total = len(pages)
	with events.suspended_indexing():
		for i, pno in enumerate(sorted(cleaned)):
			md = cleaned[pno]
			if md.strip() != original[pno].strip() and md.strip():
				store.set_canonical_markdown(name_by_page[pno], md)
				pages_changed += 1
			if progress_cb:
				progress_cb(i + 1, total)

	n_sections = rebuild_and_classify(source_document, pdf_path, stage_cb)

	return {"pages": total, "pages_changed": pages_changed, "sections": n_sections}
