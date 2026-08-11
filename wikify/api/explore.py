"""Whitelisted APIs for the Explore screens (Slice 6).

The headline query — *"all job descriptions across all PDFs"* — is a metadata filter
on `Source Section.section_type`, not a fuzzy search. `type_summary` drives the filter
chips / type rail (counts incl. the `other` catch-all and an `untagged` bucket);
`sections_by_type` returns the matching sections grouped by Source Document with
page-range provenance. Both work globally (no `source_document`) or per-document.

Mirrors the POC `graph.section_type_counts` / `graph.sections_by_type`.
"""

from __future__ import annotations

import frappe
from frappe.query_builder.functions import Count

UNTAGGED = "__untagged__"  # sentinel for sections classification hasn't reached yet


def _scope(source_document: str | None) -> dict:
	return {"source_document": source_document} if source_document else {}


def _docs_in_project(project: str | None) -> list[str] | None:
	"""Source Document names in a project, or None when no project filter is applied.

	Returns `[]` (not None) when the project owns no documents yet, so callers scope to
	an empty set instead of falling through to "all documents".
	"""
	if not project:
		return None
	return frappe.get_all("Source Document", filters={"project": project}, pluck="name")


def _counts(scope: list | None) -> dict[str, int]:
	"""Section counts grouped by type, keyed by type (untagged → sentinel). `scope` is an
	optional `[fieldname, operator, value]` clause on `source_document`."""
	table = frappe.qb.DocType("Source Section")
	query = (
		frappe.qb.from_(table)
		.select(table.section_type, Count(table.name).as_("count"))
		.groupby(table.section_type)
	)
	if scope:
		field, op, value = scope
		column = getattr(table, field)
		query = query.where(column.isin(value) if op == "in" else column == value)
	rows = query.run(as_dict=True)
	return {r["section_type"] or UNTAGGED: r["count"] for r in rows}


@frappe.whitelist()
def type_summary(source_document: str | None = None, project: str | None = None) -> list[dict]:
	"""Per-type section counts for the chips/rail (global, per-project, or per-doc).

	Returns every Section Type in display order with its count (incl. zero, so chips are
	stable), then an `untagged` bucket appended only when some section has no type yet.
	"""
	doc_scope = _docs_in_project(project)
	if source_document:
		counts = _counts(["source_document", "=", source_document])
	elif doc_scope is not None:
		counts = _counts(["source_document", "in", doc_scope]) if doc_scope else {}
	else:
		counts = _counts(None)

	types = frappe.get_all(
		"Section Type",
		fields=["type_name", "label", "color", "is_other"],
		order_by="is_other asc, creation asc",
	)
	summary = [
		{
			"type_name": t["type_name"],
			"label": t["label"] or t["type_name"],
			"color": t["color"] or "#9ca3af",
			"is_other": t["is_other"],
			"count": counts.get(t["type_name"], 0),
		}
		for t in types
	]
	if counts.get(UNTAGGED):
		summary.append(
			{
				"type_name": UNTAGGED,
				"label": "Untagged",
				"color": "#cbd5e1",
				"is_other": 0,
				"count": counts[UNTAGGED],
			}
		)
	return summary


@frappe.whitelist()
def sections_by_type(
	section_type: str, source_document: str | None = None, project: str | None = None
) -> list[dict]:
	"""Sections of one type, grouped by Source Document (the cross-document headline).

	Each group: `{source_document, doc_title, sections: [...]}`, ordered by document
	title then tree position. `section_type` may be the `untagged` sentinel. Scope to a
	single document (`source_document`) or a project (`project`), else spans all docs.
	"""
	filters = _scope(source_document)
	filters["section_type"] = ["is", "not set"] if section_type == UNTAGGED else section_type

	if not source_document:
		doc_scope = _docs_in_project(project)
		if doc_scope is not None:
			if not doc_scope:
				return []
			filters["source_document"] = ["in", doc_scope]

	rows = frappe.get_all(
		"Source Section",
		filters=filters,
		fields=[
			"name",
			"source_document",
			"title",
			"hierarchy_path",
			"level",
			"page_start",
			"page_end",
		],
		order_by="source_document asc, lft asc",
	)
	if not rows:
		return []

	docs = {
		d["name"]: d
		for d in frappe.get_all(
			"Source Document",
			filters={"name": ["in", list({r["source_document"] for r in rows})]},
			fields=["name", "title", "import"],
		)
	}

	groups: dict[str, dict] = {}
	for r in rows:
		sd = r["source_document"]
		doc = docs.get(sd, {})
		group = groups.setdefault(
			sd,
			{
				"source_document": sd,
				"doc_title": doc.get("title") or sd,
				"import_name": doc.get("import"),
				"sections": [],
			},
		)
		group["sections"].append(r)
	return sorted(groups.values(), key=lambda g: (g["doc_title"] or "").lower())
