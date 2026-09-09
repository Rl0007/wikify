from __future__ import annotations

import json

import frappe
from frappe import _
from frappe.query_builder import Order
from frappe.query_builder.functions import Coalesce

from wikify.engine import store
from wikify.rag import events


@frappe.whitelist()
def get_tree(source_document: str) -> list[dict]:
	rows = frappe.get_all(
		"Source Section",
		filters={"source_document": source_document},
		fields=[
			"name",
			"parent_source_section",
			"title",
			"is_group",
			"level",
			"section_type",
			"hierarchy_path",
			"page_start",
			"page_end",
			"sort_order",
			"markdown",
			"lint_issues",
		],
		order_by="lft asc",
	)

	for r in rows:
		r["lint_count"] = store.lint_count(r.pop("lint_issues", None))
	by_name = {r["name"]: {**r, "children": []} for r in rows}
	roots: list[dict] = []
	for r in rows:
		node = by_name[r["name"]]
		parent = r["parent_source_section"]
		if parent and parent in by_name:
			by_name[parent]["children"].append(node)
		else:
			roots.append(node)
	return roots


def _rebuild_tree(source_document: str) -> None:
	table = frappe.qb.DocType("Source Section")

	def children_of(parent: str | None) -> list[str]:
		q = frappe.qb.from_(table).where(table.source_document == source_document)
		if parent is None:
			q = q.where((table.parent_source_section == "") | table.parent_source_section.isnull())
		else:
			q = q.where(table.parent_source_section == parent)
		return (
			q.orderby(Coalesce(table.sort_order, 0), order=Order.asc)
			.orderby(table.name, order=Order.asc)
			.select(table.name)
		).run(pluck="name")

	def walk(name: str, left: int, level: int, ancestry: list[str]) -> int:
		title = frappe.db.get_value("Source Section", name, "title") or ""
		path = [*ancestry, title]
		kids = children_of(name)
		right = left + 1
		for kid in kids:
			right = walk(kid, right, level + 1, path)
		frappe.db.set_value(
			"Source Section",
			name,
			{
				"lft": left,
				"rgt": right,
				"level": level,
				"hierarchy_path": " > ".join(path),
				"is_group": 1 if kids else 0,
			},
			update_modified=False,
		)
		return right + 1

	right = 1
	for root in children_of(None):
		right = walk(root, right, 1, [])

	events.document_structure_changed(source_document)


def _subtree_names(name: str) -> tuple[str, list[str]]:
	sec = frappe.db.get_value("Source Section", name, ["source_document", "lft", "rgt"], as_dict=True)
	if not sec:
		frappe.throw(_("Section {0} not found.").format(name))
	names = frappe.get_all(
		"Source Section",
		filters={
			"source_document": sec.source_document,
			"lft": [">=", sec.lft],
			"rgt": ["<=", sec.rgt],
		},
		pluck="name",
	)
	return sec.source_document, names


@frappe.whitelist(methods=["POST"])
def create_section_type(
	type_name: str, label: str | None = None, description: str | None = None, color: str | None = None
) -> dict:
	from wikify.wikify.doctype.section_type.section_type import find_by_normalized_label

	key = frappe.scrub((type_name or "").strip()).strip("_")
	if not key:
		frappe.throw(_("Provide a type name."))
	if frappe.db.exists("Section Type", key):
		return {"ok": True, "type_name": key, "existed": True}
	canonical = find_by_normalized_label(label or type_name)
	if canonical:
		return {"ok": True, "type_name": canonical, "existed": True}
	doc = frappe.new_doc("Section Type")
	doc.type_name = key
	doc.label = (label or type_name).strip()
	doc.description = (description or "").strip() or None
	doc.color = (color or "").strip() or None
	doc.insert(ignore_permissions=True)
	return {"ok": True, "type_name": key, "existed": False}


@frappe.whitelist(methods=["POST"])
def reorder_section(
	name: str, new_parent: str | None = None, new_index: int = 0, siblings: str | list | None = None
) -> dict:
	new_parent = new_parent or None
	sec = frappe.db.get_value("Source Section", name, ["source_document", "lft", "rgt"], as_dict=True)
	if not sec:
		frappe.throw(_("Section {0} not found.").format(name))

	if new_parent:
		parent = frappe.db.get_value("Source Section", new_parent, ["source_document", "lft"], as_dict=True)
		if not parent or parent.source_document != sec.source_document:
			frappe.throw(_("New parent must belong to the same document."))
		if sec.lft <= parent.lft <= sec.rgt:
			frappe.throw(_("Can't move a section into its own subtree."))

	frappe.db.set_value("Source Section", name, "parent_source_section", new_parent)

	if isinstance(siblings, str):
		siblings = json.loads(siblings)
	for idx, sib in enumerate(siblings or []):
		frappe.db.set_value("Source Section", sib, "sort_order", idx, update_modified=False)

	_rebuild_tree(sec.source_document)
	return {"ok": True}


@frappe.whitelist(methods=["POST"])
def move_section(name: str, new_parent: str | None = None, new_index: int | None = None) -> dict:
	new_parent = new_parent or None
	sec = frappe.db.get_value("Source Section", name, ["source_document", "lft", "rgt"], as_dict=True)
	if not sec:
		frappe.throw(_("Section {0} not found.").format(name))

	if new_parent:
		parent = frappe.db.get_value(
			"Source Section", new_parent, ["source_document", "lft", "rgt"], as_dict=True
		)
		if not parent or parent.source_document != sec.source_document:
			frappe.throw(_("New parent must belong to the same document."))
		if sec.lft <= parent.lft <= sec.rgt:
			frappe.throw(_("Can't move a section into its own subtree."))

	table = frappe.qb.DocType("Source Section")
	q = table.source_document == sec.source_document
	q = q & (
		(table.parent_source_section == new_parent)
		if new_parent
		else ((table.parent_source_section == "") | table.parent_source_section.isnull())
	)
	siblings = (
		frappe.qb.from_(table)
		.where(q & (table.name != name))
		.orderby(Coalesce(table.sort_order, 0), order=Order.asc)
		.orderby(table.name, order=Order.asc)
		.select(table.name)
	).run(pluck="name")

	idx = len(siblings) if new_index is None else max(0, min(int(new_index), len(siblings)))
	siblings.insert(idx, name)

	frappe.db.set_value("Source Section", name, "parent_source_section", new_parent)
	for order, sib in enumerate(siblings):
		frappe.db.set_value("Source Section", sib, "sort_order", order, update_modified=False)
	_rebuild_tree(sec.source_document)
	return {"ok": True, "index": idx}


@frappe.whitelist(methods=["POST"])
def set_section_type(name: str, section_type: str | None = None) -> dict:
	if not frappe.db.exists("Source Section", name):
		frappe.throw(_("Section {0} not found.").format(name))
	section_type = (section_type or "").strip() or None
	if section_type and not frappe.db.exists("Section Type", section_type):
		frappe.throw(_("Unknown Section Type {0}.").format(section_type))
	frappe.db.set_value("Source Section", name, "section_type", section_type, update_modified=False)
	events.section_content_changed([name])
	return {"ok": True, "section_type": section_type}


@frappe.whitelist(methods=["POST"])
def rename_section(name: str, title: str) -> dict:
	title = (title or "").strip()
	if not title:
		frappe.throw(_("Title can't be empty."))
	source_document = frappe.db.get_value("Source Section", name, "source_document")
	frappe.db.set_value("Source Section", name, "title", title)
	_rebuild_tree(source_document)
	return {"ok": True}


@frappe.whitelist(methods=["POST"])
def toggle_include(name: str, include: bool | int | str) -> dict:
	include = 1 if frappe.parse_json(include) else 0
	_, names = _subtree_names(name)
	frappe.db.set_value(
		"Source Section", {"name": ["in", names]}, "include_in_wiki", include, update_modified=False
	)
	return {"ok": True, "count": len(names)}


@frappe.whitelist(methods=["POST"])
def delete_section(name: str) -> dict:
	from wikify.engine.refs import extract_references

	source_document, names = _subtree_names(name)
	frappe.db.delete("Source Section", {"name": ["in", names]})
	_rebuild_tree(source_document)
	extract_references(source_document)
	return {"ok": True, "deleted": len(names)}


@frappe.whitelist(methods=["POST"])
def create_section(
	source_document: str,
	title: str,
	parent: str | None = None,
	is_group: bool | int | str = 0,
	markdown: str = "",
	section_type: str | None = None,
	index: int | None = None,
) -> dict:
	title = (title or "").strip()
	if not title:
		frappe.throw(_("Title can't be empty."))
	if not frappe.db.exists("Source Document", source_document):
		frappe.throw(_("Source Document {0} not found.").format(source_document))
	if parent:
		prow = frappe.db.get_value("Source Section", parent, "source_document")
		if prow != source_document:
			frappe.throw(_("Parent must belong to the same document."))
	section_type = (section_type or "").strip() or None
	if section_type and not frappe.db.exists("Section Type", section_type):
		frappe.throw(_("Unknown Section Type {0}.").format(section_type))

	doc = frappe.new_doc("Source Section")
	doc.source_document = source_document
	doc.parent_source_section = parent
	doc.title = title
	doc.is_group = 1 if frappe.parse_json(is_group) else 0
	doc.markdown = markdown or ""
	doc.section_type = section_type
	doc.include_in_wiki = 1
	doc.sort_order = 10**6
	doc.insert(ignore_permissions=True)

	if index is not None:
		move_section(doc.name, new_parent=parent, new_index=int(index))
	else:
		_rebuild_tree(source_document)
	from wikify.engine.refs import extract_references

	extract_references(source_document, [doc.name])
	return {"ok": True, "name": doc.name}


@frappe.whitelist(methods=["POST"])
def split_section(name: str, at_heading: str, new_title: str | None = None) -> dict:
	sec = frappe.db.get_value(
		"Source Section",
		name,
		[
			"source_document",
			"parent_source_section",
			"title",
			"markdown",
			"page_start",
			"page_end",
			"section_type",
			"include_in_wiki",
			"sort_order",
		],
		as_dict=True,
	)
	if not sec:
		frappe.throw(_("Section {0} not found.").format(name))

	want = (at_heading or "").strip().lstrip("#").strip().lower()
	if not want:
		frappe.throw(_("Provide the heading to split at."))
	lines = (sec.markdown or "").splitlines()
	split_at = next(
		(
			i
			for i, line in enumerate(lines)
			if line.lstrip().startswith("#") and line.lstrip().lstrip("#").strip().lower() == want
		),
		None,
	)
	if split_at is None:
		frappe.throw(
			_("No heading matching '{0}' found in '{1}' — nothing was split.").format(at_heading, sec.title)
		)

	head = "\n".join(lines[:split_at]).strip()
	tail = "\n".join(lines[split_at:]).strip()
	title = (new_title or "").strip() or lines[split_at].lstrip().lstrip("#").strip()

	new = frappe.new_doc("Source Section")
	new.source_document = sec.source_document
	new.parent_source_section = sec.parent_source_section
	new.title = title
	new.markdown = tail
	new.page_start = sec.page_start
	new.page_end = sec.page_end
	new.section_type = sec.section_type
	new.include_in_wiki = sec.include_in_wiki
	new.sort_order = (sec.sort_order or 0) + 1
	new.insert(ignore_permissions=True)

	store.set_section_markdown(name, head, update_modified=False)

	sib_filters = {"source_document": sec.source_document}
	sib_filters["parent_source_section"] = sec.parent_source_section or ["is", "not set"]
	siblings = frappe.get_all(
		"Source Section", filters=sib_filters, order_by="sort_order asc, name asc", pluck="name"
	)
	move_section(
		new.name,
		new_parent=sec.parent_source_section,
		new_index=siblings.index(name) + 1 if name in siblings else None,
	)
	from wikify.engine.refs import extract_references

	extract_references(sec.source_document)
	return {"ok": True, "name": name, "new_name": new.name, "new_title": title}


@frappe.whitelist(methods=["POST"])
def merge_sections(names: list | str) -> dict:
	if isinstance(names, str):
		names = frappe.parse_json(names)
	names = [n for n in (names or []) if n]
	if len(names) < 2:
		frappe.throw(_("Pass at least two section ids to merge."))

	rows = {
		r.name: r
		for r in frappe.get_all(
			"Source Section",
			filters={"name": ["in", names]},
			fields=[
				"name",
				"source_document",
				"parent_source_section",
				"title",
				"markdown",
				"page_start",
				"page_end",
				"lft",
			],
		)
	}
	missing = [n for n in names if n not in rows]
	if missing:
		frappe.throw(_("Section(s) not found: {0}").format(", ".join(missing)))
	docs = {r.source_document for r in rows.values()}
	parents = {r.parent_source_section or "" for r in rows.values()}
	if len(docs) > 1 or len(parents) > 1:
		frappe.throw(_("Sections must be siblings (same document and same parent) to merge."))

	survivor = rows[names[0]]
	ordered = sorted(rows.values(), key=lambda r: r.lft)
	merged_md = "\n\n".join((r.markdown or "").strip() for r in ordered if (r.markdown or "").strip())
	starts = [r.page_start for r in ordered if r.page_start]
	ends = [r.page_end for r in ordered if r.page_end]

	husks = [n for n in names if n != survivor.name]
	for husk in husks:
		frappe.db.set_value(
			"Source Section",
			{"parent_source_section": husk},
			"parent_source_section",
			survivor.name,
			update_modified=False,
		)
	frappe.db.delete("Source Section", {"name": ["in", husks]})
	store.set_section_markdown(
		survivor.name,
		merged_md,
		update_modified=False,
		extra_values={
			"page_start": min(starts) if starts else survivor.page_start,
			"page_end": max(ends) if ends else survivor.page_end,
		},
	)
	_rebuild_tree(survivor.source_document)
	from wikify.engine.refs import extract_references

	extract_references(survivor.source_document)
	return {"ok": True, "name": survivor.name, "merged": len(husks)}


@frappe.whitelist(methods=["POST"])
def build_graph(import_name: str) -> dict:
	imp = frappe.get_doc("Wikify Import", import_name)
	if not imp.source_document:
		frappe.throw(_("Nothing to graph — parse hasn't produced a document yet."))
	imp.db_set("status", "Graphed")
	frappe.db.set_value("Source Document", imp.source_document, "status", "Graphed")
	from wikify.engine.refs import extract_references

	extract_references(imp.source_document)
	return {"status": "Graphed"}
