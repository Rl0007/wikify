from __future__ import annotations

import frappe
from frappe import _


def _document_graph(source_document: str) -> tuple[list[dict], list[dict]]:
	sd = frappe.db.get_value("Source Document", source_document, ["title", "page_count"], as_dict=True)
	if not sd:
		frappe.throw(_("Source Document {0} not found.").format(source_document))
	sections = frappe.get_all(
		"Source Section",
		filters={"source_document": source_document},
		fields=["name", "title", "section_type", "parent_source_section", "page_start", "page_end"],
		order_by="lft asc",
	)

	edges: list[dict] = []
	for s in sections:
		if s.parent_source_section:
			edges.append({"src": s.name, "dst": s.parent_source_section, "rel": "PART_OF", "weight": 1})
		else:
			edges.append({"src": source_document, "dst": s.name, "rel": "HAS_SECTION", "weight": 1})

	ref_edges: dict[tuple[str, str], dict] = {}
	for r in frappe.get_all(
		"Section Reference",
		filters={"source_document": source_document},
		fields=["from_section", "to_section", "anchor_text", "occurrences"],
	):
		edge = ref_edges.setdefault(
			(r.from_section, r.to_section),
			{"src": r.from_section, "dst": r.to_section, "rel": "REFERENCES", "weight": 0, "anchors": []},
		)
		edge["weight"] += r.occurrences or 1
		edge["anchors"].append(r.anchor_text)
	edges.extend(ref_edges.values())

	degree: dict[str, int] = {}
	for e in ref_edges.values():
		degree[e["src"]] = degree.get(e["src"], 0) + e["weight"]
		degree[e["dst"]] = degree.get(e["dst"], 0) + e["weight"]

	nodes: list[dict] = [
		{"id": source_document, "kind": "document", "label": sd.title, "pages": sd.page_count}
	]
	for s in sections:
		span = (s.page_end - s.page_start + 1) if s.page_start and s.page_end else 0
		nodes.append(
			{
				"id": s.name,
				"kind": "section",
				"label": s.title,
				"section_type": s.section_type,
				"doc": source_document,
				"page_start": s.page_start,
				"page_end": s.page_end,
				"span": span,
				"degree": degree.get(s.name, 0),
			}
		)
	return nodes, edges


def _types_meta(nodes: list[dict]) -> list[dict]:
	counts: dict[str, int] = {}
	for n in nodes:
		if n.get("section_type"):
			counts[n["section_type"]] = counts.get(n["section_type"], 0) + 1
	if not counts:
		return []
	types = frappe.get_all(
		"Section Type",
		filters={"name": ["in", list(counts)]},
		fields=["name", "label", "color"],
		order_by="creation asc",
	)
	return [{**t, "count": counts[t.name]} for t in types]


@frappe.whitelist()
def get_document_graph(source_document: str) -> dict:
	nodes, edges = _document_graph(source_document)
	return {
		"nodes": nodes,
		"edges": edges,
		"meta": {
			"types": _types_meta(nodes),
			"documents": [{"id": source_document, "label": nodes[0]["label"]}],
		},
	}


@frappe.whitelist()
def get_project_graph(project: str) -> dict:
	if not frappe.db.exists("Wikify Project", project):
		frappe.throw(_("Project {0} not found.").format(project))
	docs = frappe.get_all(
		"Source Document", filters={"project": project}, pluck="name", order_by="creation asc"
	)
	nodes: list[dict] = []
	edges: list[dict] = []
	documents: list[dict] = []
	for name in docs:
		doc_nodes, doc_edges = _document_graph(name)
		nodes.extend(doc_nodes)
		edges.extend(doc_edges)
		documents.append({"id": name, "label": doc_nodes[0]["label"]})
	return {
		"nodes": nodes,
		"edges": edges,
		"meta": {"types": _types_meta(nodes), "documents": documents},
	}
