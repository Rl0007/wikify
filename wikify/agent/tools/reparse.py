"""Re-parse tools (0.2 Slice 14) — the headline "fix this page from plain English".

  - `use_page_image` — deterministic, no LLM: replace a page's canonical markdown with an
    embed of its rendered image. The literal "just paste the image of the PDF page".
  - `reparse_page` — single-page cleanup/VLM re-parse steered by a plain-English
    `instruction`, adopted as that page's canonical. Runs inline (page-scoped, fast).
  - `reparse_document` — document-wide instruction-steered re-parse. **Expensive →
    confirm-gated**: the loop holds it for a UI confirm card and enqueues the remediate
    job only once the user approves.

Page-scoped edits update the page's canonical, then **propagate** (0.3 Slice 18): when
exactly one deepest section owns the page, its markdown is rebuilt from canonical so the
fix reaches the wiki preview in the same turn — tree untouched. When the page is a
boundary page shared between sections, nothing is silently rewritten; the result string
names the candidates and the follow-up tools, so the model reports honestly instead of
claiming a user-visible fix it didn't make.
"""

from __future__ import annotations

import frappe
from frappe import _

from wikify.agent.context import Ctx
from wikify.agent.registry import Tool


def _pdf_path(source_document: str) -> str | None:
	"""Full path to the document's source PDF (via its Import's attached File)."""
	pdf_url = frappe.db.get_value("Wikify Import", {"source_document": source_document}, "pdf")
	if not pdf_url:
		return None
	file_name = frappe.db.get_value("File", {"file_url": pdf_url}, "name")
	return frappe.get_doc("File", file_name).get_full_path() if file_name else None


def _project_context(ctx: Ctx) -> str:
	if not ctx.project:
		return ""
	return frappe.db.get_value("Wikify Project", ctx.project, "context_prompt") or ""


def _page_no(args: dict) -> int | None:
	raw = args.get("page_no")
	try:
		return int(raw) if raw is not None else None
	except (TypeError, ValueError):
		return None


def _propagate_page(source_document: str, page_no: int) -> str:
	"""Push a fresh page canonical into the owning section (0.3 Slice 18).

	Exactly one deepest covering section → rebuild its markdown and report both layers
	updated. Zero or several → change nothing at the section layer and say exactly what
	is still stale and which tool fixes it.
	"""
	from wikify.engine.sectionize import rebuild_section_markdown, sections_covering_page

	owners = sections_covering_page(source_document, page_no)
	if len(owners) == 1:
		try:
			res = rebuild_section_markdown(owners[0].name)
		except ValueError as e:
			return _(" Section propagation failed ({0}) — the wiki preview is still stale.").format(str(e))
		return _(
			" Propagated into section '{0}' ({1} chars) — the wiki preview now shows it. If this "
			"document has a generated wiki, finish with sync_wiki_page on <{2}>."
		).format(res["title"], res["chars"], owners[0].name)
	if not owners:
		return _(
			" No section's page range covers this page, so the wiki preview is NOT updated. "
			"Use edit_section_content on the right section if the fix must reach the wiki."
		)
	names = ", ".join(f"'{o.title}' <{o.name}>" for o in owners)
	return _(
		" NOT yet visible in the wiki preview — this is a boundary page shared by {0}. "
		"Run rebuild_section_from_pages or edit_section_content on the right one, then "
		"sync_wiki_page if a wiki is generated."
	).format(names)


def _use_page_image(ctx: Ctx, args: dict) -> str:
	from wikify.engine import embed_page_image

	source_document = ctx.default_document(args.get("source_document"))
	page_no = _page_no(args)
	if not source_document:
		return _("No document specified. Open a document or pass `source_document`.")
	if page_no is None:
		return _("Provide the `page_no` to embed as an image.")
	try:
		embed_page_image(source_document, page_no)
	except (ValueError, RuntimeError) as e:
		return _("Couldn't embed the page image: {0}").format(str(e))
	return _("Page {0} now embeds its rendered image as canonical markdown (no re-parse).").format(
		page_no
	) + _propagate_page(source_document, page_no)


def _reparse_page(ctx: Ctx, args: dict) -> str:
	from wikify.engine import reparse_page

	source_document = ctx.default_document(args.get("source_document"))
	page_no = _page_no(args)
	if not source_document:
		return _("No document specified. Open a document or pass `source_document`.")
	if page_no is None:
		return _("Provide the `page_no` to re-parse.")
	pdf_path = _pdf_path(source_document)
	if not pdf_path:
		return _("Couldn't locate the source PDF for {0}.").format(source_document)
	method = args.get("method")
	if method not in (None, "cleanup", "vlm"):
		return _("`method` must be 'cleanup' or 'vlm' (or omit for vlm, the default).")
	try:
		res = reparse_page(
			source_document,
			pdf_path,
			page_no,
			method=method,
			instruction=args.get("instruction") or "",
			project_context=_project_context(ctx),
		)
	except (ValueError, RuntimeError) as e:
		return _("Re-parse failed: {0}").format(str(e))
	return _("Re-parsed page {0} via {1} (composite {2}, {3} chars). Canonical updated.").format(
		res["page_no"], res["method"], res["composite"], res["chars"]
	) + _propagate_page(source_document, page_no)


def _reparse_document(ctx: Ctx, args: dict) -> str:
	import_name = ctx.default_import(args.get("source_document"))
	if not import_name:
		return _("No document specified. Open a document or pass `source_document`.")
	instruction = (args.get("instruction") or "").strip()
	frappe.enqueue(
		"wikify.jobs.remediate.run",
		queue="long",
		timeout=3600,
		import_name=import_name,
		scope="all",
		instruction=instruction,
	)
	hint = f" steered by: {instruction}" if instruction else ""
	return _(
		"Enqueued a document-wide re-parse of {0}{1}. Progress streams on the import; the "
		"tree and pages refresh when it finishes."
	).format(import_name, hint)


TOOLS = [
	Tool(
		name="use_page_image",
		side="server",
		description=(
			"Deterministically replace a page's canonical markdown with an embed of its "
			"rendered image (no LLM). Use when the user wants the page shown as an image rather "
			"than transcribed/diagrammed. Defaults to the attached document."
		),
		parameters={
			"type": "object",
			"properties": {
				"source_document": {"type": "string", "description": "Omit to use the attached document."},
				"page_no": {"type": "integer", "description": "1-based page number."},
			},
			"required": ["page_no"],
		},
		handler=_use_page_image,
		mutates=True,
	),
	Tool(
		name="reparse_page",
		side="server",
		description=(
			"Re-parse a single page, steered by a plain-English instruction (e.g. 'keep the "
			"table as a real markdown table', 'don't make this a mermaid diagram'). method "
			"forces 'cleanup' (text) or 'vlm' (from the image); omit for vlm (the default). The result "
			"becomes the page's canonical markdown. Defaults to the attached document."
		),
		parameters={
			"type": "object",
			"properties": {
				"source_document": {"type": "string", "description": "Omit to use the attached document."},
				"page_no": {"type": "integer", "description": "1-based page number."},
				"method": {
					"type": "string",
					"enum": ["cleanup", "vlm"],
					"description": "Force a method; omit for vlm (the default).",
				},
				"instruction": {"type": "string", "description": "Plain-English steering for the re-parse."},
			},
			"required": ["page_no"],
		},
		handler=_reparse_page,
		mutates=True,
	),
	Tool(
		name="reparse_document",
		side="server",
		description=(
			"Re-parse the WHOLE document, steered by a plain-English instruction. Expensive — "
			"this runs cleanup/VLM over every page and rebuilds the tree, so MANUAL TREE EDITS "
			"AND SECTION-LEVEL CONTENT EDITS ARE LOST. The user must confirm before it runs."
		),
		parameters={
			"type": "object",
			"properties": {
				"source_document": {"type": "string", "description": "Omit to use the attached document."},
				"instruction": {"type": "string", "description": "Plain-English steering for the re-parse."},
			},
		},
		handler=_reparse_document,
		confirm=True,
	),
]
