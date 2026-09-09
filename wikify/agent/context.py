from __future__ import annotations

from dataclasses import dataclass, field

import frappe

_BODY_LIMIT = 4000


@dataclass
class Ctx:
	session: str
	user: str
	project: str | None = None
	source_document: str | None = None
	attachments: list[dict] = field(default_factory=list)
	approved: set[str] = field(default_factory=set)

	def default_document(self, explicit: str | None = None) -> str | None:
		if explicit and frappe.db.exists("Source Document", explicit):
			return explicit
		return self.source_document

	def default_project(self, explicit: str | None = None) -> str | None:
		if not explicit:
			return self.project
		if not frappe.has_permission("Wikify Project", ptype="read"):
			return self.project
		matched = frappe.get_list(
			"Wikify Project",
			or_filters={"name": explicit, "project_name": explicit},
			pluck="name",
			limit=1,
		)
		return matched[0] if matched else self.project

	def default_import(self, explicit: str | None = None) -> str | None:
		source_document = self.default_document(explicit)
		if not source_document:
			return None
		return frappe.db.get_value("Wikify Import", {"source_document": source_document}, "name")


@dataclass
class ResolvedContext:
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
	from wikify.agent.tools.read import render_tree

	return render_tree(source_document)


def resolve_attachments(attachments: list[dict] | None) -> ResolvedContext:
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
