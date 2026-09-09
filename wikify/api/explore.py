from __future__ import annotations

import frappe
from frappe.query_builder.functions import Count

from wikify.api.permission import assert_readable, hidden_documents

UNTAGGED = "__untagged__"


def _scope(source_document: str | None) -> dict:
	return {"source_document": source_document} if source_document else {}


def _docs_in_project(project: str | None) -> list[str] | None:
	if not project:
		return None
	return frappe.get_all("Source Document", filters={"project": project}, pluck="name")


def _counts(scope: list | None) -> dict[str, int]:
	table = frappe.qb.DocType("Source Section")
	query = (
		frappe.qb.from_(table)
		.select(table.section_type, Count(table.name).as_("count"))
		.groupby(table.section_type)
	)
	if scope:
		field, op, value = scope
		column = getattr(table, field)
		if op == "in":
			query = query.where(column.isin(value))
		elif op == "not in":
			query = query.where(column.notin(value))
		else:
			query = query.where(column == value)
	rows = query.run(as_dict=True)
	return {r["section_type"] or UNTAGGED: r["count"] for r in rows}


@frappe.whitelist()
def type_summary(source_document: str | None = None, project: str | None = None) -> list[dict]:
	assert_readable(project, source_document)
	doc_scope = _docs_in_project(project)
	if source_document:
		counts = _counts(["source_document", "=", source_document])
	elif doc_scope is not None:
		counts = _counts(["source_document", "in", doc_scope]) if doc_scope else {}
	else:
		hidden = hidden_documents()
		counts = _counts(["source_document", "not in", hidden] if hidden else None)

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
	assert_readable(project, source_document)
	filters = _scope(source_document)
	filters["section_type"] = ["is", "not set"] if section_type == UNTAGGED else section_type

	if not source_document:
		doc_scope = _docs_in_project(project)
		if doc_scope is not None:
			if not doc_scope:
				return []
			filters["source_document"] = ["in", doc_scope]
		elif hidden := hidden_documents():
			filters["source_document"] = ["not in", hidden]

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
