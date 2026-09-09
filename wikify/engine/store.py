from __future__ import annotations

import frappe
from frappe.utils.file_manager import save_file

from wikify.engine.verify.harness import get_verdict


def create_document(
	title: str,
	import_name: str | None = None,
	pdf_url: str | None = None,
	parser: str | None = None,
	project: str | None = None,
) -> str:
	doc = frappe.new_doc("Source Document")
	doc.title = title
	doc.set("import", import_name)
	doc.project = project
	doc.pdf = pdf_url
	doc.parser_used = parser
	doc.status = "Parsed"
	doc.insert(ignore_permissions=True)
	return doc.name


def add_page(
	source_document: str,
	page_no: int,
	kind: str,
	png_bytes: bytes,
	baseline_markdown: str,
) -> str:
	page = frappe.new_doc("Source Page")
	page.source_document = source_document
	page.page_no = page_no
	page.kind = kind
	page.baseline_markdown = baseline_markdown
	page.insert(ignore_permissions=True)

	file_doc = save_file(
		f"page-{page_no:04d}.png",
		png_bytes,
		"Source Page",
		page.name,
		df="image",
		is_private=1,
	)
	page.db_set("image", file_doc.file_url)
	return page.name


def set_page_count(source_document: str, count: int) -> None:
	frappe.db.set_value("Source Document", source_document, "page_count", count)


def get_page_count(source_document: str) -> int | None:
	return frappe.db.get_value("Source Document", source_document, "page_count")


def set_page_scores(page_name: str, score) -> None:
	values = {
		"text_recall": score.text_recall,
		"extra_ratio": score.extra_ratio,
		"composite": score.composite,
		"verdict": score.verdict,
		"notes": "; ".join(score.notes) if score.notes else None,
	}
	if score.table_score is not None:
		values["table_score"] = score.table_score
	if score.judge_score is not None:
		values["judge_score"] = score.judge_score
	frappe.db.set_value("Source Page", page_name, values)


def set_mean_score(source_document: str, mean: float | None) -> None:
	frappe.db.set_value("Source Document", source_document, "mean_score", mean)


def cost_of(metrics: list[dict]) -> float:
	return sum(m["cost"] for m in metrics if m.get("cost"))


def add_page_cost(page_name: str, metrics: list[dict]) -> float:
	cost = cost_of(metrics)
	if cost:
		current = frappe.db.get_value("Source Page", page_name, "llm_cost") or 0
		frappe.db.set_value(
			"Source Page", page_name, "llm_cost", round(current + cost, 6), update_modified=False
		)
	return cost


def add_document_cost(source_document: str, cost: float) -> None:
	if not cost:
		return
	current = frappe.db.get_value("Source Document", source_document, "llm_cost") or 0
	frappe.db.set_value(
		"Source Document", source_document, "llm_cost", round(current + cost, 6), update_modified=False
	)


def get_pages(source_document: str) -> list[dict]:
	return frappe.get_all(
		"Source Page",
		filters={"source_document": source_document},
		fields=["name", "page_no", "kind", "baseline_markdown", "verdict", "composite", "image"],
		order_by="page_no asc",
	)


def set_remediation(
	page_name: str,
	method: str,
	markdown: str,
	score,
	adopted: bool,
	notes: str | None = None,
) -> None:
	frappe.db.set_value(
		"Source Page",
		page_name,
		{
			"remediation_method": method,
			"remediation_markdown": markdown,
			"remediation_composite": score.composite,
			"remediation_adopted": 1 if adopted else 0,
			"remediation_notes": notes,
		},
	)


def set_canonical(page_name: str, markdown: str, composite: float | None, source: str) -> None:
	values = {"canonical_markdown": markdown, "canonical_source": source}
	if composite is not None:
		values["canonical_composite"] = composite
		values["verdict"] = get_verdict(composite)
	frappe.db.set_value("Source Page", page_name, values)
	invalidate_page(page_name)


def set_canonical_mean(source_document: str, mean: float | None) -> None:
	frappe.db.set_value("Source Document", source_document, "canonical_mean", mean)


def get_page_image(page_name: str) -> str | None:
	return frappe.db.get_value("Source Page", page_name, "image")


def get_canonical_composites(source_document: str) -> list[float | None]:
	rows = frappe.get_all(
		"Source Page",
		filters={"source_document": source_document},
		fields=["canonical_composite", "composite"],
		order_by="page_no asc",
	)
	return [
		r["canonical_composite"] if r["canonical_composite"] is not None else r["composite"] for r in rows
	]


def set_canonical_markdown(page_name: str, markdown: str) -> None:
	frappe.db.set_value(
		"Source Page",
		page_name,
		{"canonical_markdown": markdown, "canonical_composite": 0, "verdict": ""},
	)
	invalidate_page(page_name)


def invalidate_page(page_name: str) -> None:
	from wikify.rag import events

	events.page_content_changed(page_name)


def get_finalize_pages(source_document: str) -> list[dict]:
	pages = frappe.get_all(
		"Source Page",
		filters={"source_document": source_document},
		fields=["name", "page_no", "canonical_markdown", "baseline_markdown"],
		order_by="page_no asc",
	)
	for p in pages:
		p["markdown"] = p["canonical_markdown"] or p["baseline_markdown"] or ""
	return pages


def get_canonical_pages(source_document: str) -> list[tuple[int, str]]:
	pages = frappe.get_all(
		"Source Page",
		filters={"source_document": source_document},
		fields=["page_no", "canonical_markdown", "baseline_markdown"],
		order_by="page_no asc",
	)
	return [(p["page_no"], p["canonical_markdown"] or p["baseline_markdown"] or "") for p in pages]


def get_section_span(name: str) -> dict | None:
	return frappe.db.get_value(
		"Source Section",
		name,
		["name", "source_document", "title", "page_start", "page_end", "lft", "rgt", "wiki_document"],
		as_dict=True,
	)


def get_section_spans(source_document: str) -> list[dict]:
	return frappe.get_all(
		"Source Section",
		filters={"source_document": source_document},
		fields=["name", "title", "page_start", "page_end", "lft", "rgt", "is_group", "wiki_document"],
		order_by="lft asc",
	)


def lint_json(markdown: str) -> str | None:
	import json

	from wikify.engine.lint import lint_markdown

	try:
		issues = lint_markdown(markdown or "")
	except Exception:
		frappe.log_error(title="wikify: markdown lint failed")
		return None
	return json.dumps(issues) if issues else None


def lint_count(lint_issues: str | None) -> int:
	import json

	try:
		return len(json.loads(lint_issues)) if lint_issues else 0
	except Exception:
		return 0


def set_section_markdown(
	name: str, markdown: str, *, update_modified: bool = True, extra_values: dict | None = None
) -> None:
	from wikify.engine.refs import extract_references
	from wikify.rag import events

	values = {"markdown": markdown, "lint_issues": lint_json(markdown), **(extra_values or {})}
	frappe.db.set_value("Source Section", name, values, update_modified=update_modified)
	extract_references(frappe.db.get_value("Source Section", name, "source_document"), [name])
	# `set_value` fires no doc_event, so the reindex hook on Source Section never sees this
	# write — the index would keep serving the text this call just replaced.
	events.section_content_changed([name])


def get_section_taxonomy() -> list[str]:
	return frappe.get_all("Section Type", pluck="type_name", order_by="is_other asc, creation asc")


def get_sections_to_classify(source_document: str) -> list[dict]:
	return frappe.get_all(
		"Source Section",
		filters={"source_document": source_document},
		fields=["name", "title", "markdown"],
		order_by="lft asc",
	)


def set_section_type(name: str, section_type: str | None) -> None:
	frappe.db.set_value("Source Section", name, "section_type", section_type, update_modified=False)


def replace_sections(source_document: str, sections) -> int:
	from wikify.rag import events

	parent_paths = {tuple(s.hierarchy_path[:-1]) for s in sections if len(s.hierarchy_path) > 1}
	path_to_name: dict[tuple[str, ...], str] = {}
	suspended_by_caller = events.indexing_suspended()
	# A random name, the way `frappe.database.savepoint` does it: re-declaring a name destroys
	# the outer savepoint holding it. Frappe's own helper is not reused because it swallows the
	# exception instead of re-raising, and the caller must still see the failure.
	save_point = f"wikify_replace_sections_{frappe.generate_hash(length=8)}"
	# Nothing inside this block may commit: a commit releases the savepoint, and the rollback
	# below would then fail with "savepoint does not exist". That rules out threading
	# `jobs._util.publish_progress` (which commits) into the insert loop.
	frappe.db.savepoint(save_point)
	with events.suspended_indexing():
		try:
			frappe.db.delete("Source Section", {"source_document": source_document})
			for idx, sec in enumerate(sections):
				doc = frappe.new_doc("Source Section")
				doc.source_document = source_document
				doc.parent_source_section = path_to_name.get(tuple(sec.hierarchy_path[:-1]))
				doc.is_group = 1 if tuple(sec.hierarchy_path) in parent_paths else 0
				doc.title = sec.title
				doc.section_type = sec.section_type
				doc.level = sec.level
				doc.hierarchy_path = " > ".join(sec.hierarchy_path)
				doc.page_start = sec.page_start
				doc.page_end = sec.page_end
				doc.sort_order = idx
				doc.markdown = sec.markdown
				doc.insert(ignore_permissions=True)
				path_to_name[tuple(sec.hierarchy_path)] = doc.name
		except Exception:
			# The old tree is what the index still holds, so restoring it needs no rebuild.
			frappe.db.rollback(save_point=save_point)
			raise
	frappe.db.release_savepoint(save_point)

	project = (
		None if suspended_by_caller else frappe.db.get_value("Source Document", source_document, "project")
	)
	# Success only. A document outside any project has nothing to scope an index to; it becomes
	# searchable when it is assigned to one (which reindexes then).
	if project:
		events.queue_project_rebuild(project)

	from wikify.engine.refs import extract_references

	extract_references(source_document)
	return len(sections)


def get_sections_for_wiki(source_document: str) -> list[dict]:
	return frappe.get_all(
		"Source Section",
		filters={"source_document": source_document},
		fields=[
			"name",
			"parent_source_section",
			"title",
			"is_group",
			"markdown",
			"page_start",
			"page_end",
			"sort_order",
			"include_in_wiki",
			"wiki_document",
			"lint_issues",
		],
		order_by="lft asc",
	)


def set_section_wiki_document(name: str, wiki_document: str | None) -> None:
	frappe.db.set_value("Source Section", name, "wiki_document", wiki_document, update_modified=False)


def set_document_wiki(
	source_document: str, wiki_space: str, wiki_root_group: str, status: str | None = None
) -> None:
	values = {"wiki_space": wiki_space, "wiki_root_group": wiki_root_group}
	if status:
		values["status"] = status
	frappe.db.set_value("Source Document", source_document, values)


def get_section_bodies(source_document: str, names: list[str] | None = None) -> list[dict]:
	filters: dict = {"source_document": source_document}
	if names is not None:
		filters["name"] = ["in", names]
	return frappe.get_all("Source Section", filters=filters, fields=["name", "markdown"], order_by="lft asc")


def replace_references(
	source_document: str, rows: list[dict], from_sections: list[str] | None = None
) -> None:
	filters: dict = {"source_document": source_document}
	if from_sections is not None:
		filters["from_section"] = ["in", from_sections]
	frappe.db.delete("Section Reference", filters)
	for row in rows:
		doc = frappe.new_doc("Section Reference")
		doc.update(row)
		doc.insert(ignore_permissions=True)
