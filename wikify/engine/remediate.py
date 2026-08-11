"""Remediation pass — ported from the POC `pipeline.remediate_document`.

Every target page gets the **vlm** (image) re-parse — 0.4 slice 22 removed the
recall-gated routing, so a subtly mangled table on a "good enough" page can't dodge the
pass. Text pages additionally get the cheap **cleanup** (text model) variant, so
adoption picks best-of-three {baseline, cleanup, vlm}:
  - cleanup is adopt-eligible when it preserves content (recall within tolerance — a
    small drop is the intended furniture removal);
  - vlm is adopt-eligible when it beats the baseline composite;
  - vlm wins over an eligible cleanup only when it also scores at least as high.
Under all of that sit two floors (`pick_winner`): nothing dramatically below the best read
of the page is adopted, and a near-empty candidate never displaces one carrying content.
Every candidate — baseline included — clears the same diagram gate before it is scored, so
a block rejected on one route cannot ride in on another.
Always-run ≠ always-adopt. The adopted (or baseline) markdown becomes each page's
**canonical** markdown; cross-page tables are then stitched, the doc's canonical mean is
recomputed, and per-page LLM spend lands on `Source Page.llm_cost` /
`Source Document.llm_cost`.

I/O-boundary changes vs the POC: persistence goes through `store` (the Frappe ORM seam)
instead of `loader/graph`; pages are re-rendered from the PDF via `pdf_utils` instead of
read off disk. The routing + adoption rules are unchanged. **Sequential** (not the POC's
ThreadPoolExecutor) — the Frappe ORM writes are not thread-safe; a dev-tool remediate
over a manual is fast enough serial.
"""

from __future__ import annotations

import re
from collections.abc import Callable

import fitz  # PyMuPDF

from wikify.engine import diagrams, llm, pdf_utils, regions, settings, store
from wikify.engine.loader.cleanup_llm import clean_markdown
from wikify.engine.loader.table_stitch import stitch_cross_page_tables
from wikify.engine.parsers import vlm
from wikify.engine.sectionize import rebuild_and_classify
from wikify.engine.verify import deterministic as det
from wikify.engine.verify import score_page

# Two floors under adoption. A page had `# TIE TIT Molo` — ten characters of OCR noise — made
# canonical at composite 0.058, because cleanup's own eligibility test only asks whether recall
# regressed, and recall against an equally empty baseline had not. Neither floor replaces the
# per-method rules; both sit under them, so a candidate can only ever become canonical if it is
# also a competitive read of the page.
ADOPTION_COMPOSITE_RATIO = 0.9
MIN_CANONICAL_CHARS = 40
_IMAGE_EMBED_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_MARKUP_RE = re.compile(r"[\s#*_>|`~\-]+")


def content_chars(markdown: str) -> int:
	"""How much the reader actually gets: markdown furniture and image embeds removed, so a
	crop-and-nothing-else page does not read as content."""
	return len(_MARKUP_RE.sub("", _IMAGE_EMBED_RE.sub("", markdown or "")))


def with_page_crop(markdown: str, page_image: str | None) -> str:
	"""A near-empty page keeps its crop. An image the reader can read beats a blank page."""
	if not page_image or "![" in markdown or content_chars(markdown) >= MIN_CANONICAL_CHARS:
		return markdown
	return f"{markdown}\n\n![Source page]({page_image})".strip()


def pick_winner(candidates: list[tuple], baseline_composite: float, baseline_markdown: str) -> tuple | None:
	"""Best adopt-eligible candidate: vlm when it also matches/beats cleanup's composite
	(cleanup's composite is depressed by intended furniture removal, so a tie goes to
	vlm), else the eligible cleanup, else None (keep baseline)."""
	best_composite = max([baseline_composite or 0.0, *(c[2].composite for c in candidates)])
	best_chars = max([content_chars(baseline_markdown), *(content_chars(c[1]) for c in candidates)])
	eligible = [
		c
		for c in candidates
		if c[3]
		and c[2].composite >= best_composite * ADOPTION_COMPOSITE_RATIO
		and (content_chars(c[1]) >= MIN_CANONICAL_CHARS or content_chars(c[1]) >= best_chars)
	]
	vlm_candidate = next((c for c in eligible if c[0] == "vlm"), None)
	cleanup_candidate = next((c for c in eligible if c[0] == "cleanup"), None)
	if vlm_candidate and (
		not cleanup_candidate or vlm_candidate[2].composite >= cleanup_candidate[2].composite
	):
		return vlm_candidate
	return cleanup_candidate


def remediate_pdf(
	source_document: str,
	pdf_path: str,
	scope: str = "all",
	project_context: str = "",
	instruction: str = "",
	progress_cb: Callable[[int, int], None] | None = None,
	page_cb: Callable[..., None] | None = None,
	stage_cb: Callable[[str], None] | None = None,
) -> dict:
	"""Route + re-score + adopt per page → write canonical markdown + canonical mean.

	`scope='all'` cleans every page (uniform, furniture-free markdown); `scope='flagged'`
	only touches non-`pass` pages. Returns a summary dict. `progress_cb(done, total)` and
	`page_cb(page_no, total, method, adopted, base_composite, new_composite, metrics)` are
	optional streaming hooks the remediate job uses for live progress + log lines.
	`instruction` (0.2 Slice 14) steers the cleanup/VLM re-parse with a one-off
	plain-English rule on top of the project context (blank = v0.1 behavior).
	"""
	if not llm.has_openrouter():
		raise RuntimeError("OpenRouter key not set — remediation needs cloud models.")

	pdf_path = str(pdf_path)
	judge_all = bool(settings.get("judge_all_pages"))
	recall_tol = float(settings.get("cleanup_recall_tolerance"))
	dpi = int(settings.get("render_dpi"))

	pages = store.get_pages(source_document)
	targets = pages if scope == "all" else [p for p in pages if p["verdict"] != "pass"]
	total = len(targets)

	# Canonical defaults to each page's baseline; adopted remediations override below.
	canon_md = {p["page_no"]: p["baseline_markdown"] or "" for p in pages}
	canon_comp = {p["page_no"]: p["composite"] for p in pages}
	canon_src = {p["page_no"]: "baseline" for p in pages}

	with fitz.open(pdf_path) as doc:
		# Running furniture (banners, doc-code/date stamps, 'Page X of Y', prepared/issued/
		# approved footers) recurs across pages. Cleanup is meant to strip it, but that drops
		# its words from the page — so adoption scores cleanup recall against a furniture-free
		# ground truth, else removing furniture looks like content loss and good cleanups get
		# rejected (canonical falls back to the raw, artifact-laden baseline).
		furniture = det.find_furniture_lines([doc[p["page_no"] - 1].get_text("text") for p in pages])

		doc_cost = 0.0
		for i, p in enumerate(targets):
			page = doc[p["page_no"] - 1]
			gt = page.get_text("text")
			kind = p["kind"]
			page_image = store.get_page_image(p["name"]) or ""
			data_url = pdf_utils.png_to_data_url(pdf_utils.render_png(page, dpi=dpi))

			use_judge = judge_all or kind == "visual"
			img = data_url if use_judge else None
			llm.reset_metrics()
			# Every candidate — baseline included — clears the same diagram gate before it is
			# scored, so a block that would be rejected on one route cannot survive on another.
			candidates: list[tuple] = []  # (method, markdown, PageScore, adopt_eligible)
			errors: list[str] = []
			base_md, base_notes = diagrams.remove_unverified_diagrams(
				p["baseline_markdown"] or "", page_image
			)
			errors.extend(base_notes)
			base_ps = score_page(
				p["page_no"], base_md, gt, image_data_url=img, use_judge=use_judge, page_kind=kind
			)

			# Every page gets the vlm pass; text pages also get the cheap cleanup variant.
			# A single page's model call failing (rate limit, billing, transient) must not
			# abort the whole pass — note it, try the other candidate / keep baseline, move on.
			try:
				vlm_md = vlm.parse_page_image(
					data_url,
					project_context=project_context,
					instruction=instruction,
					shape_hint=regions.shape_hint(regions.find_regions(page)),
				)
				# Nothing unverified is stored: a diagram that will not parse after repair, or one
				# carrying the signature of a table flattened into dangling branches, is dropped in
				# favour of the page image before it can be scored, adopted or read.
				vlm_md, diagram_notes = diagrams.remove_unverified_diagrams(vlm_md, page_image)
				errors.extend(diagram_notes)
				vlm_ps = score_page(
					p["page_no"], vlm_md, gt, image_data_url=img, use_judge=use_judge, page_kind=kind
				)
				candidates.append(("vlm", vlm_md, vlm_ps, vlm_ps.composite > base_ps.composite))
			except Exception as e:
				errors.append(f"vlm failed: {e}")
			if kind != "visual":
				try:
					clean_md, clean_notes = diagrams.remove_unverified_diagrams(
						clean_markdown(base_md, project_context=project_context, instruction=instruction),
						page_image,
					)
					errors.extend(clean_notes)
					clean_ps = score_page(
						p["page_no"], clean_md, gt, image_data_url=img, use_judge=use_judge, page_kind=kind
					)
					# Adopt-eligible unless real content was lost. Recall is measured against
					# the furniture-stripped ground truth, so stripping running headers/footers
					# (cleanup's job) doesn't count against it — only dropped substantive text does.
					base_cr = det.content_recall(gt, base_md, furniture)
					new_cr = det.content_recall(gt, clean_md, furniture)
					candidates.append(("cleanup", clean_md, clean_ps, new_cr >= base_cr - recall_tol))
				except Exception as e:
					errors.append(f"cleanup failed: {e}")

			winner = pick_winner(candidates, base_ps.composite, base_md)
			best_composite = max([base_ps.composite, *(c[2].composite for c in candidates)])
			if best_composite < float(settings.get("escalate_threshold")):
				errors.append(
					f"every candidate scored below the escalate threshold (best {best_composite}) — "
					"kept the best available read; this page needs a human"
				)
			# Record the adopted candidate; when nothing is adopted, record the vlm attempt
			# (the expensive audit trail) so the review UI shows what was tried and why not.
			record = (
				winner
				or next((c for c in candidates if c[0] == "vlm"), None)
				or (candidates[0] if candidates else None)
			)
			if record:
				method, new_md, new_ps, _ = record
				adopted = winner is not None
				notes = "; ".join([*(new_ps.notes or []), *errors]) or None
				store.set_remediation(p["name"], method, new_md, new_ps, adopted, notes)
				new_composite = new_ps.composite
			else:
				method, adopted, new_composite = "vlm", False, base_ps.composite
				store.set_remediation(p["name"], "vlm", "", base_ps, False, "; ".join(errors) or None)

			if adopted:
				canon_md[p["page_no"]] = record[1]
				canon_comp[p["page_no"]] = new_composite
				canon_src[p["page_no"]] = method
			else:
				canon_md[p["page_no"]] = base_md
			canon_md[p["page_no"]] = with_page_crop(canon_md[p["page_no"]], page_image)

			doc_cost += store.add_page_cost(p["name"], llm.get_metrics())

			if page_cb:
				page_cb(
					p["page_no"],
					total,
					method,
					adopted,
					base_ps.composite,
					new_composite,
					llm.get_metrics(),
				)
			if progress_cb:
				progress_cb(i + 1, total)

	# Stitch cross-page tables over the canonical set, then persist canonical per page.
	stitched = dict(stitch_cross_page_tables([(p["page_no"], canon_md[p["page_no"]]) for p in pages]))
	for p in pages:
		pno = p["page_no"]
		store.set_canonical(p["name"], stitched[pno], canon_comp[pno], canon_src[pno])

	comps = [c for c in canon_comp.values() if c is not None]
	canonical_mean = round(sum(comps) / len(comps), 3) if comps else None
	store.set_canonical_mean(source_document, canonical_mean)

	# Rebuild the section tree over the now-canonical (adopted) markdown, then re-tag.
	# Classify spend isn't attributable to a single page — it lands on the doc total only.
	llm.reset_metrics()
	n_sections = rebuild_and_classify(source_document, pdf_path, stage_cb, project_context=project_context)
	doc_cost += store.cost_of(llm.get_metrics())
	store.add_document_cost(source_document, doc_cost)

	adopted_count = sum(1 for src in canon_src.values() if src != "baseline")
	return {
		"targets": total,
		"adopted": adopted_count,
		"canonical_mean": canonical_mean,
		"sections": n_sections,
		"cost": round(doc_cost, 6),
	}
