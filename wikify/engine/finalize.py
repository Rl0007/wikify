"""Document-level finalize pass — persist furniture removal into canonical markdown.

The per-page `cleanup` / VLM remediation makes each page good in isolation, but running
page furniture (the repeated document-title banner, the doc-code / version / date line,
the prepared-by / approved-by sign-off footer) survives in each page's canonical
markdown. The deterministic boilerplate stripper (`loader.cleanup.clean_pages`) already
removes it — but only transiently, at sectionize time, to build the section tree; the
stored `canonical_markdown` (what the review split-pane shows) keeps the furniture.

Boilerplate detection is **document-global** (it needs every page to see what recurs),
so it belongs in a whole-document pass, not the per-page remediation. This pass runs
`clean_pages` over the doc and writes the stripped result back to each page's canonical
markdown, so the review view matches what the sections / wiki already get. It is purely
deterministic — no LLM, no PDF re-render (`pdf_path` is only the section rebuild's
embedded-ToC level map, mirroring `remediate_pdf`).

Cross-page table merging was considered here too, but on real manuals the deterministic
`stitch_cross_page_tables` (in remediation) already handles the clean cases and genuine
splits are rare — so it's deferred until a corpus actually needs it.
"""

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
	"""Strip running furniture from every page's canonical markdown (persisting what the
	section build already does transiently), then rebuild the section tree.

	Returns a summary dict. `progress_cb(done, total)` and `stage_cb(label)` are optional
	streaming hooks for the finalize job's live progress.
	"""
	# Imported here to avoid a cycle (sectionize -> classify -> ...; __init__ -> finalize).
	from wikify.engine.sectionize import rebuild_and_classify

	if stage_cb:
		stage_cb("Removing running furniture")

	pages = store.get_finalize_pages(source_document)
	name_by_page = {p["page_no"]: p["name"] for p in pages}
	original = {p["page_no"]: p["markdown"] for p in pages}

	cleaned = dict(clean_pages([(p["page_no"], p["markdown"]) for p in pages]))

	pages_changed = 0
	total = len(pages)
	# The tree is rebuilt wholesale below, so invalidating page by page here would queue a
	# redundant propagation pass over work that is about to be replaced.
	with events.suspended_indexing():
		for i, pno in enumerate(sorted(cleaned)):
			md = cleaned[pno]
			# Persist only real changes, and never blank a page that had content (a deterministic
			# strip can't lose more than the detected furniture, but this is a cheap backstop).
			if md.strip() != original[pno].strip() and md.strip():
				store.set_canonical_markdown(name_by_page[pno], md)
				pages_changed += 1
			if progress_cb:
				progress_cb(i + 1, total)

	# Rebuild the section tree over the now-furniture-free canonical markdown.
	n_sections = rebuild_and_classify(source_document, pdf_path, stage_cb)

	return {"pages": total, "pages_changed": pages_changed, "sections": n_sections}
