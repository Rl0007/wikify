"""Per-turn context for tool handlers + attachment resolution.

Attachments are the "@-mention / file chips" the panel sends with each `run()` —
`[{type, name, label}]` where `type ∈ {project, document, page, section}`. Slice 13
resolves them into:
  - **scoping defaults** (`Ctx.project` / `Ctx.source_document`) so tools rarely need ids,
  - a **bounded context block** prepended to the turn (tree outline + the focused item's
    body) — the agent pulls more via the `read_*` tools on demand (Builder's
    skeleton-context idea),
  - the attached **project's `context_prompt`** (injected into the system prompt by the
    loop).

Resolution is best-effort: a stale/deleted attachment is skipped, never fatal.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import frappe

# Keep the prepended block bounded — the agent reads more on demand via read_* tools.
_BODY_LIMIT = 4000


@dataclass
class Ctx:
	"""What a tool handler needs: the session it runs in + scoping defaults."""

	session: str
	user: str
	project: str | None = None
	source_document: str | None = None
	attachments: list[dict] = field(default_factory=list)
	# Names of confirm-gated tools the user approved for this turn (Slice 14).
	approved: set[str] = field(default_factory=set)

	def default_document(self, explicit: str | None = None) -> str | None:
		"""A tool's `source_document` arg, falling back to the attached document.

		Models sometimes echo a display label ("Title (id)") instead of the bare id; an
		explicit value that doesn't resolve to a real Source Document falls back to the
		attached one rather than failing the lookup.
		"""
		if explicit and frappe.db.exists("Source Document", explicit):
			return explicit
		return self.source_document

	def default_project(self, explicit: str | None = None) -> str | None:
		"""A tool's `project` arg, resolved from a display name, falling back to the scope.

		Same failure `default_document` guards, one field over: the user says "the Demo
		Corpus project" and the model passes that title, but the id is `PRJ-2026-00003`.
		An unresolved title scoped the query to zero documents, which the model then read
		as "this content does not exist".
		"""
		if not explicit:
			return self.project
		if frappe.db.exists("Wikify Project", explicit):
			return explicit
		return frappe.db.get_value("Wikify Project", {"project_name": explicit}, "name") or self.project

	def default_import(self, explicit: str | None = None) -> str | None:
		"""The Wikify Import owning the (resolved) document — needed by pipeline jobs.

		The reclassify / regenerate / reparse-document tools enqueue existing jobs keyed
		by `import_name`; the agent works in `source_document` terms, so resolve the link.
		"""
		source_document = self.default_document(explicit)
		if not source_document:
			return None
		return frappe.db.get_value("Wikify Import", {"source_document": source_document}, "name")


@dataclass
class ResolvedContext:
	"""The outcome of resolving a turn's attachments."""

	project: str | None = None
	source_document: str | None = None
	project_context: str = ""
	block: str = ""


def _truncate(text: str) -> str:
	text = text or ""
	return (
		text
		if len(text) <= _BODY_LIMIT
		else text[:_BODY_LIMIT] + "\n… (truncated — use read_* tools for more)"
	)


def _tree_outline(source_document: str) -> str:
	"""A compact indented outline of a document's section tree (titles + types + pages)."""
	from wikify.agent.tools.read import render_tree

	return render_tree(source_document)


def resolve_attachments(attachments: list[dict] | None) -> ResolvedContext:
	"""Expand the chips into scoping defaults + a bounded context block.

	The most specific attachment wins for the tool defaults (a page/section pins its
	document); the project is taken from an explicit project chip or derived from the
	attached document. Order of the rendered block follows project → document → page →
	section so the focused item lands last (closest to the user's question).
	"""
	resolved = ResolvedContext()
	if not attachments:
		return resolved

	sections: list[str] = []

	for att in attachments:
		atype = att.get("type")
		name = att.get("name")
		if not name:
			continue
		if atype == "project":
			block = _render_project(name)
			if block:
				resolved.project = resolved.project or name
				sections.append(block)
		elif atype == "document":
			block = _render_document(name)
			if block:
				resolved.source_document = resolved.source_document or name
				sections.append(block)
		elif atype == "page":
			block, sd = _render_page(name)
			if block:
				resolved.source_document = resolved.source_document or sd
				sections.append(block)
		elif atype == "section":
			block, sd = _render_section(name, view=att.get("view"))
			if block:
				resolved.source_document = resolved.source_document or sd
				sections.append(block)

	# Derive the project from the attached document when no explicit project chip.
	if not resolved.project and resolved.source_document:
		resolved.project = frappe.db.get_value("Source Document", resolved.source_document, "project")

	if resolved.project:
		resolved.project_context = (
			frappe.db.get_value("Wikify Project", resolved.project, "context_prompt") or ""
		)

	sections = [s for s in sections if s]
	if sections:
		resolved.block = (
			"The user is currently looking at the following (attached context). Use it to "
			"answer without asking for ids; pull more detail with the read_* tools as needed.\n\n"
			+ "\n\n".join(sections)
		)
	return resolved


def _render_project(name: str) -> str:
	row = frappe.db.get_value("Wikify Project", name, ["project_name", "description"], as_dict=True)
	if not row:
		return ""
	lines = [f"## Project: {row.project_name or name}"]
	if row.description:
		lines.append(row.description)
	return "\n".join(lines)


def _render_document(name: str) -> str:
	title = frappe.db.get_value("Source Document", name, "title")
	if title is None:
		return ""
	outline = _tree_outline(name)
	return f"## Document: {title or name} <{name}>\n{outline}"


def _render_page(name: str) -> tuple[str, str | None]:
	row = frappe.db.get_value(
		"Source Page",
		name,
		["source_document", "page_no", "verdict", "canonical_markdown", "baseline_markdown"],
		as_dict=True,
	)
	if not row:
		return "", None
	body = row.canonical_markdown or row.baseline_markdown or "(no markdown yet)"
	header = f"## Page {row.page_no} of {row.source_document} (verdict: {row.verdict or '—'})"
	return f"{header}\n{_truncate(body)}", row.source_document


def _render_section(name: str, view: str | None = None) -> tuple[str, str | None]:
	row = frappe.db.get_value(
		"Source Section",
		name,
		["source_document", "title", "section_type", "hierarchy_path", "markdown", "lint_issues"],
		as_dict=True,
	)
	if not row:
		return "", None
	stype = f" — type: {row.section_type}" if row.section_type else ""
	header = f"## Section: {row.hierarchy_path or row.title}{stype}"
	if view == "wiki":
		# Wiki-tab preview (0.6 Slice 29): the user sees the rendered page, not raw
		# markdown — bias the agent toward visible formatting/structure problems.
		header += (
			"\nThe user is reading this section as a rendered wiki page (Wiki tab preview) — "
			"they see the final formatted output, not raw markdown. Formatting and structure "
			"problems are what they can see."
		)
	lint = _lint_line(row.lint_issues)
	if lint:
		header += f"\n{lint}"
	return f"{header}\n{_truncate(row.markdown or '(no body)')}", row.source_document


def _lint_line(lint_issues: str | None) -> str:
	"""Stored lint issues as one context line (0.6 Slice 31), so 'fix this page' needs
	no diagnosis round-trip. Empty string when clean."""
	import json

	try:
		issues = json.loads(lint_issues) if lint_issues else []
	except Exception:
		issues = []
	if not issues:
		return ""
	listed = "; ".join(
		f"{i['message']} (line {i['line']})" if i.get("line") else i["message"] for i in issues
	)
	return (
		f"Markdown lint: {listed}. These render broken on the wiki page — "
		"fix with edit_section_content unless the user asks otherwise."
	)
