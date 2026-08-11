"""Read / context tools — reuse the existing whitelisted `api/` read seams.

Slice 12 shipped `read_tree`; slice 13 adds `read_section`, `read_page`,
`list_section_types`, and `search_sections`. Handlers default `source_document` to the
attached document (`ctx.default_document`) so the user rarely names ids.
"""

from __future__ import annotations

import re
from difflib import get_close_matches

import frappe
from frappe import _

from wikify.agent.context import Ctx
from wikify.agent.registry import Tool

# Keep tool results bounded — the model can ask for a narrower slice if it needs more.
_BODY_LIMIT = 6000
_WORD_SEPARATOR = re.compile(r"[^a-z0-9]+")


def _truncate(text: str) -> str:
	text = text or ""
	return text if len(text) <= _BODY_LIMIT else text[:_BODY_LIMIT] + "\n… (truncated)"


def render_tree(source_document: str) -> str:
	"""The Source Section tree as compact, indented text (shared with context.py)."""
	from wikify.api.sections import get_tree

	roots = get_tree(source_document)
	if not roots:
		return _("Document {0} has no sections yet.").format(source_document)

	lines: list[str] = []

	def walk(node: dict, depth: int) -> None:
		indent = "  " * depth
		pages = ""
		start, end = node.get("page_start"), node.get("page_end")
		if start:
			pages = f" [p.{start}]" if not end or end == start else f" [p.{start}-{end}]"
		stype = f" ({node['section_type']})" if node.get("section_type") else ""
		lines.append(f"{indent}- {node.get('title') or '(untitled)'}{stype}{pages} <{node['name']}>")
		for child in node.get("children", []):
			walk(child, depth + 1)

	for root in roots:
		walk(root, 1)
	return "\n".join(lines)


def _read_tree(ctx: Ctx, args: dict) -> str:
	"""Return the Source Section tree as compact, indented text."""
	source_document = ctx.default_document(args.get("source_document"))
	if not source_document:
		return _(
			"No document specified. Ask the user which Source Document to read, or have "
			"them open one (its tree will be attached automatically)."
		)
	return f"Section tree of {source_document}:\n{render_tree(source_document)}"


def _read_section(ctx: Ctx, args: dict) -> str:
	"""A section's markdown + metadata."""
	name = args.get("name")
	if not name:
		return _("Provide the section `name` (the id shown in <angle brackets> in the tree).")
	row = frappe.db.get_value(
		"Source Section",
		name,
		["title", "section_type", "hierarchy_path", "page_start", "page_end", "include_in_wiki", "markdown"],
		as_dict=True,
	)
	if not row:
		return _("Section {0} not found.").format(name)
	pages = (
		f"{row.page_start}-{row.page_end}"
		if row.page_end and row.page_end != row.page_start
		else row.page_start
	)
	meta = [
		f"Title: {row.title}",
		f"Type: {row.section_type or '(untagged)'}",
		f"Path: {row.hierarchy_path or row.title}",
		f"Pages: {pages or '—'}",
		f"Included in wiki: {'yes' if row.include_in_wiki else 'no'}",
	]
	return "\n".join(meta) + "\n\n" + _truncate(row.markdown or "(no body)")


def _read_page(ctx: Ctx, args: dict) -> str:
	"""A page's canonical markdown + verdict + scores."""
	source_document = ctx.default_document(args.get("source_document"))
	page_no = args.get("page_no")
	if not source_document:
		return _("No document specified. Open a document or pass `source_document`.")
	if page_no is None:
		return _("Provide the `page_no` to read.")
	row = frappe.db.get_value(
		"Source Page",
		{"source_document": source_document, "page_no": int(page_no)},
		["kind", "verdict", "composite", "canonical_source", "canonical_markdown", "baseline_markdown"],
		as_dict=True,
	)
	if not row:
		return _("Page {0} of {1} not found.").format(page_no, source_document)
	body = row.canonical_markdown or row.baseline_markdown or "(no markdown yet)"
	meta = [
		f"Page {page_no} of {source_document}",
		f"Kind: {row.kind or '—'}",
		f"Verdict: {row.verdict or '—'}  Composite: {row.composite if row.composite is not None else '—'}",
		f"Canonical source: {row.canonical_source or 'baseline'}",
	]
	return "\n".join(meta) + "\n\n" + _truncate(body)


def _list_section_types(ctx: Ctx, args: dict) -> str:
	"""The taxonomy (Section Types: name, label, description, color)."""
	types = frappe.get_all(
		"Section Type",
		fields=["type_name", "label", "description", "color", "is_other"],
		order_by="is_other asc, creation asc",
	)
	if not types:
		return _("No Section Types defined yet.")
	lines = ["Section Types (taxonomy):"]
	for t in types:
		desc = f" — {t.description}" if t.description else ""
		lines.append(f"- {t.type_name} ({t.label or t.type_name}){desc}")
	return "\n".join(lines)


def search_terms(text: str) -> list[str]:
	"""Lowercase alphanumeric tokens with a trailing plural `s` dropped.

	Applied to BOTH sides of the comparison, so the way a user asks ("job descriptions")
	still matches the way titles are stored ("Job Description — Staff Nurse").
	"""
	# ponytail: only the trailing-`s` plural is folded, reach for a real stemmer if
	# `-ies`/`-es` mismatches start costing recall
	terms = []
	for token in _WORD_SEPARATOR.split((text or "").lower()):
		if token:
			terms.append(token[:-1] if len(token) > 3 and token.endswith("s") else token)
	return terms


def matches_terms(section: dict, terms: list[str]) -> bool:
	"""True when every query term appears in the section's path/title (normalised)."""
	haystack = " ".join(search_terms(f"{section.get('hierarchy_path') or ''} {section.get('title') or ''}"))
	return all(term in haystack for term in terms)


def format_section_groups(groups: list[dict]) -> str:
	lines: list[str] = []
	for group in groups:
		lines.append(f"# {group['doc_title']} ({group['source_document']})")
		for section in group["sections"]:
			pages = f" [p.{section['page_start']}-{section['page_end']}]" if section.get("page_start") else ""
			lines.append(f"  - {section['hierarchy_path'] or section['title']}{pages} <{section['name']}>")
	return "\n".join(lines)


def describe_scope(project: str | None, source_document: str | None) -> str:
	if source_document:
		return _("document {0}").format(source_document)
	if project:
		return _("project {0}").format(project)
	return _("any project")


def unknown_type_hint(section_type: str) -> str:
	"""Message for a `section_type` that is not in the taxonomy, with the near matches.

	Distinguishing this from "the type exists but is empty" is the whole point: a model
	that guesses `job_description` must learn the key is wrong, not that the content is
	missing.
	"""
	known = frappe.get_all("Section Type", fields=["type_name", "label", "description"])
	terms = search_terms(section_type)
	scored = []
	for row in known:
		haystack = " ".join(search_terms(f"{row.type_name} {row.label or ''} {row.description or ''}"))
		hits = sum(1 for term in terms if term in haystack)
		if hits:
			scored.append((hits, row.type_name))
	names = [row.type_name for row in known]
	near = [entry[1] for entry in sorted(scored, reverse=True)] or get_close_matches(
		section_type, names, n=5, cutoff=0.4
	)
	if near:
		return _(
			"'{0}' is not a Section Type in this taxonomy — so this is NOT evidence that the "
			"content is missing. Closest existing types: {1}. Retry with one of those, or call "
			"list_section_types for the full taxonomy."
		).format(section_type, ", ".join(near[:5]))
	return _(
		"'{0}' is not a Section Type in this taxonomy — this says nothing about whether the "
		"content exists. Call list_section_types and retry with a real type."
	).format(section_type)


def _search_sections(ctx: Ctx, args: dict) -> str:
	"""Explore-style cross-document lookup, reusing `api.explore.sections_by_type`.

	With `section_type`, returns the matching sections grouped by document (optionally
	scoped to `project` / the attached `source_document`). Without a type, lists the
	taxonomy counts so the model can pick a type to drill into.

	`query` is a NARROWING HINT, not a hard filter: when it matches nothing the full set
	is returned with a note. The tool's contract is "find me the sections" — silently
	returning nothing because the user's wording differs from the stored titles reads to
	the model as "that type is empty" and it then states that as fact.
	"""
	from wikify.api.explore import UNTAGGED, sections_by_type, type_summary

	section_type = args.get("section_type")
	requested_project = args.get("project")
	project = ctx.default_project(requested_project)
	source_document = ctx.default_document(args.get("source_document"))
	query = (args.get("query") or "").strip()
	scope = describe_scope(project, source_document)
	if requested_project and not project:
		scope = _("{0} ('{1}' matches no project, so the search was NOT scoped to it)").format(
			scope, requested_project
		)

	if not section_type:
		summary = type_summary(source_document=source_document, project=project)
		lines = ["Available section types (pass `section_type` to drill in):"]
		lines += [f"- {s['type_name']}: {s['count']}" for s in summary if s["count"]]
		if len(lines) == 1:
			return _("No tagged sections in {0} yet.").format(scope)
		return "\n".join(lines)

	if section_type != UNTAGGED and not frappe.db.exists("Section Type", section_type):
		return unknown_type_hint(section_type)

	groups = sections_by_type(section_type, source_document=source_document, project=project)
	available = sum(len(group["sections"]) for group in groups)
	if not available:
		summary = type_summary(source_document=source_document, project=project)
		populated = ", ".join(f"{s['type_name']}: {s['count']}" for s in summary if s["count"])
		message = _("'{0}' exists in the taxonomy but has no sections in {1}.").format(section_type, scope)
		return f"{message} " + (
			_("Types that do have sections here: {0}.").format(populated)
			if populated
			else _("No section in this scope carries any type yet.")
		)

	if not query:
		header = _("{0} section(s) of type {1} in {2}:").format(available, section_type, scope)
		return f"{header}\n{format_section_groups(groups)}"

	terms = search_terms(query)
	narrowed = [
		{**group, "sections": [row for row in group["sections"] if matches_terms(row, terms)]}
		for group in groups
	]
	narrowed = [group for group in narrowed if group["sections"]]
	matched = sum(len(group["sections"]) for group in narrowed)
	if not matched:
		header = _(
			"Type {0} has {1} section(s) in {2}, but none of their titles or paths contain "
			'"{3}" — the query filter was IGNORED (it is a hint, not proof of absence). '
			"All {1} are listed below:"
		).format(section_type, available, scope, query)
		return f"{header}\n{format_section_groups(groups)}"
	header = _('{0} of {1} section(s) of type {2} in {3} match "{4}":').format(
		matched, available, section_type, scope, query
	)
	return f"{header}\n{format_section_groups(narrowed)}"


def _read_rendered_preview(ctx: Ctx, args: dict) -> str:
	"""What the user's wiki preview actually renders for a section (0.3 Slice 17).

	Reuses `api.wiki.render_section_preview` — the same content pipeline (empty-group
	rollup + page-ref resolution) the preview and wiki generation use. This is the
	verification tool after any content mutation: it reads the layer the user sees,
	not the layer that was just written.
	"""
	from wikify.api.wiki import render_section_preview

	name = args.get("name")
	if not name:
		return _("Provide the section `name`.")
	if not frappe.db.exists("Source Section", name):
		return _("Section {0} not found.").format(name)
	res = render_section_preview(name)
	meta = [
		f"Preview of: {' > '.join(res['breadcrumb'])}",
		f"Included in wiki: {'yes' if res['include_in_wiki'] else 'no'}",
		f"Page refs resolved: {res['page_refs_resolved']}",
		"",
		"Rendered markdown (what the user sees):",
	]
	return "\n".join(meta) + "\n" + _truncate(res["markdown"] or "(empty page)")


def _read_wiki_page(ctx: Ctx, args: dict) -> str:
	"""The GENERATED Wiki Document content for a section (0.3 Slice 19) — detects drift
	between the section (preview) and the live wiki page."""
	name = args.get("name")
	if not name:
		return _("Provide the section `name`.")
	sec = frappe.db.get_value("Source Section", name, ["title", "markdown", "wiki_document"], as_dict=True)
	if not sec:
		return _("Section {0} not found.").format(name)
	if not sec.wiki_document or not frappe.db.exists("Wiki Document", sec.wiki_document):
		return _(
			"'{0}' has no generated wiki page yet. The wiki preview still works; generate the "
			"wiki (or regenerate_wiki) to publish it."
		).format(sec.title)
	wd = frappe.db.get_value(
		"Wiki Document", sec.wiki_document, ["content", "route", "modified"], as_dict=True
	)
	drift = (wd.content or "").strip() != (sec.markdown or "").strip()
	meta = [
		f"Generated wiki page for '{sec.title}' — route /{wd.route} (last written {wd.modified})",
		(
			"NOTE: differs from the section's current content — stale; sync_wiki_page will update it."
			if drift
			else "Matches the section's current content."
		),
		"",
	]
	return "\n".join(meta) + _truncate(wd.content or "(empty)")


TOOLS = [
	Tool(
		name="read_tree",
		side="server",
		description=(
			"Read the Source Section tree (titles, section types, page ranges, hierarchy, "
			"and each section's id) of a document. Defaults to the document the user is "
			"currently looking at."
		),
		parameters={
			"type": "object",
			"properties": {
				"source_document": {
					"type": "string",
					"description": "Source Document name. Omit to use the attached document.",
				}
			},
		},
		handler=_read_tree,
	),
	Tool(
		name="read_section",
		side="server",
		description="Read one section's markdown body and metadata. Pass the section id (shown in <angle brackets> in the tree).",
		parameters={
			"type": "object",
			"properties": {
				"name": {"type": "string", "description": "Source Section id."},
			},
			"required": ["name"],
		},
		handler=_read_section,
	),
	Tool(
		name="read_page",
		side="server",
		description="Read a page's canonical markdown, verdict, and scores. Defaults to the attached document.",
		parameters={
			"type": "object",
			"properties": {
				"source_document": {
					"type": "string",
					"description": "Source Document name. Omit to use the attached document.",
				},
				"page_no": {"type": "integer", "description": "1-based page number."},
			},
			"required": ["page_no"],
		},
		handler=_read_page,
	),
	Tool(
		name="read_rendered_preview",
		side="server",
		description=(
			"Read what the user's wiki preview renders for a section — the content after "
			"empty-group rollup and page-ref resolution. ALWAYS verify content fixes with this "
			"(it reads the layer the user sees), never with read_page alone."
		),
		parameters={
			"type": "object",
			"properties": {
				"name": {"type": "string", "description": "Source Section id."},
			},
			"required": ["name"],
		},
		handler=_read_rendered_preview,
	),
	Tool(
		name="read_wiki_page",
		side="server",
		description=(
			"Read the GENERATED wiki page (Wiki Document) for a section, with a staleness note "
			"when it differs from the section's current content. Use to check whether a content "
			"fix still needs sync_wiki_page."
		),
		parameters={
			"type": "object",
			"properties": {
				"name": {"type": "string", "description": "Source Section id."},
			},
			"required": ["name"],
		},
		handler=_read_wiki_page,
	),
	Tool(
		name="list_section_types",
		side="server",
		description="List the Section Type taxonomy (the available tags) with labels and descriptions.",
		parameters={"type": "object", "properties": {}},
		handler=_list_section_types,
	),
	Tool(
		name="search_sections",
		side="server",
		description=(
			"Find sections across documents by Section Type (Explore-style). Optionally scope "
			"to a project or the attached document, and narrow with a title/path `query`. "
			"Omit section_type to list the available types with counts. The reply always "
			"states how many sections the type has in scope — read that count before "
			"concluding anything is absent."
		),
		parameters={
			"type": "object",
			"properties": {
				"section_type": {"type": "string", "description": "Section Type to filter by."},
				"query": {
					"type": "string",
					"description": (
						"Optional narrowing hint matched against title/path. It never hides the "
						"type's sections — when it matches none, all of them are returned."
					),
				},
				"project": {"type": "string", "description": "Optional Wikify Project to scope to."},
				"source_document": {"type": "string", "description": "Optional single-document scope."},
			},
		},
		handler=_search_sections,
	),
]
