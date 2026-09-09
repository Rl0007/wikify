from __future__ import annotations

import fitz

from wikify.engine import diagrams, llm, pdf_utils, regions, remediate, settings, store
from wikify.engine.loader.cleanup_llm import clean_markdown
from wikify.engine.parsers import vlm
from wikify.engine.verify import score_page


def _page_row(source_document: str, page_no: int) -> dict:
	pages = store.get_pages(source_document)
	for p in pages:
		if p["page_no"] == page_no:
			return p
	raise ValueError(f"Page {page_no} of {source_document} not found.")


def reparse_page(
	source_document: str,
	pdf_path: str,
	page_no: int,
	method: str | None = None,
	instruction: str = "",
	project_context: str = "",
) -> dict:
	if not llm.has_openrouter():
		raise RuntimeError("OpenRouter key not set — re-parsing needs cloud models.")

	page = _page_row(source_document, page_no)
	judge_all = bool(settings.get("judge_all_pages"))
	dpi = int(settings.get("render_dpi"))
	base_md = page["baseline_markdown"] or ""

	with fitz.open(str(pdf_path)) as doc:
		fpage = doc[page_no - 1]
		gt = fpage.get_text("text")
		shape_hint = regions.shape_hint(regions.find_regions(fpage))
		data_url = pdf_utils.png_to_data_url(pdf_utils.render_png(fpage, dpi=dpi))

	kind = page["kind"]
	if method not in ("cleanup", "vlm"):
		method = "vlm"
	use_judge = judge_all or kind == "visual"
	img = data_url if use_judge else None

	llm.reset_metrics()
	new_md = (
		vlm.parse_page_image(
			data_url, project_context=project_context, instruction=instruction, shape_hint=shape_hint
		)
		if method == "vlm"
		else clean_markdown(base_md, project_context=project_context, instruction=instruction)
	)
	page_image = store.get_page_image(page["name"]) or ""
	new_md, diagram_notes = diagrams.remove_unverified_diagrams(new_md, page_image)
	new_md = remediate.with_page_crop(new_md, page_image)
	new_ps = score_page(page_no, new_md, gt, image_data_url=img, use_judge=use_judge, page_kind=kind)
	notes = "; ".join([*new_ps.notes, *diagram_notes]) or None

	store.set_remediation(page["name"], method, new_md, new_ps, adopted=True, notes=notes)
	store.set_canonical(page["name"], new_md, new_ps.composite, method)
	_recompute_canonical_mean(source_document)
	store.add_document_cost(source_document, store.add_page_cost(page["name"], llm.get_metrics()))

	return {
		"page_no": page_no,
		"method": method,
		"composite": new_ps.composite,
		"verdict": new_ps.verdict,
		"chars": len(new_md),
	}


def embed_page_image(source_document: str, page_no: int) -> dict:
	page = _page_row(source_document, page_no)
	image_url = store.get_page_image(page["name"])
	if not image_url:
		raise ValueError(f"Page {page_no} has no rendered image to embed.")
	markdown = f"![Page {page_no}]({image_url})"
	store.set_canonical(page["name"], markdown, None, "image")
	_recompute_canonical_mean(source_document)
	return {"page_no": page_no, "image_url": image_url}


def _recompute_canonical_mean(source_document: str) -> None:
	rows = store.get_canonical_composites(source_document)
	comps = [c for c in rows if c is not None]
	store.set_canonical_mean(source_document, round(sum(comps) / len(comps), 3) if comps else None)
