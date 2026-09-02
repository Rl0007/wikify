"""Persistence seam — replaces the POC's `loader/graph.py` (SQLite) with the
Frappe ORM. The pipeline calls these helpers instead of touching DocTypes
directly, keeping the ported logic diff-minimal against the POC.

Slice 1b surface: `create_document`, `add_page`, `set_page_count`. Slice 2 adds
`set_page_scores` + `set_mean_score`. Sections and canonical selection land later.
"""

from __future__ import annotations

import frappe
from frappe.utils.file_manager import save_file


def create_document(
	title: str,
	import_name: str | None = None,
	pdf_url: str | None = None,
	parser: str | None = None,
	project: str | None = None,
) -> str:
	"""Create a Source Document and return its name."""
	doc = frappe.new_doc("Source Document")
	doc.title = title
	doc.set("import", import_name)  # 'import' is a Python keyword — set by string
	doc.project = project  # denormalized from the Import for project-scoped Explore
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
	"""Create a Source Page row + attach its rendered PNG as a private File."""
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
	"""Write a `PageScore` (verify.harness) onto a Source Page row.

	`table_score` / `judge_score` are `None` when there's no table / the page wasn't
	judged. Frappe Float columns are NOT NULL (default 0), so those keys are omitted
	rather than written — the row keeps 0.0 and the UI reads 0 as "n/a" (the harness
	`notes` carry a genuine table-miss). Rows are recreated on every parse, so 0.0
	never lingers as a stale value.
	"""
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


# --- 0.4 Slice 22: LLM cost accounting ---


def cost_of(metrics: list[dict]) -> float:
	"""Total USD cost across an `llm.get_metrics()` buffer."""
	return sum(m["cost"] for m in metrics if m.get("cost"))


def add_page_cost(page_name: str, metrics: list[dict]) -> float:
	"""Accumulate the LLM spend in `metrics` onto a page. Returns the added cost (USD)."""
	cost = cost_of(metrics)
	if cost:
		current = frappe.db.get_value("Source Page", page_name, "llm_cost") or 0
		frappe.db.set_value(
			"Source Page", page_name, "llm_cost", round(current + cost, 6), update_modified=False
		)
	return cost


def add_document_cost(source_document: str, cost: float) -> None:
	"""Accumulate LLM spend (USD) onto a document's running total."""
	if not cost:
		return
	current = frappe.db.get_value("Source Document", source_document, "llm_cost") or 0
	frappe.db.set_value(
		"Source Document", source_document, "llm_cost", round(current + cost, 6), update_modified=False
	)


# --- Slice 3: remediation + canonical selection ---


def get_pages(source_document: str) -> list[dict]:
	"""Pages of a doc (ordered) with the fields remediation needs to route + re-score.

	The canonical fields ride along so a scoped run can leave the pages it doesn't
	touch on the canonical they already carry.
	"""
	return frappe.get_all(
		"Source Page",
		filters={"source_document": source_document},
		fields=[
			"name",
			"page_no",
			"kind",
			"baseline_markdown",
			"verdict",
			"composite",
			"canonical_markdown",
			"canonical_composite",
			"canonical_source",
		],
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
	"""Record a page's remediation attempt + its score + whether it was adopted."""
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
	"""Write a page's canonical (best-per-page) markdown + its composite + provenance."""
	values = {"canonical_markdown": markdown, "canonical_source": source}
	if composite is not None:
		values["canonical_composite"] = composite
	frappe.db.set_value("Source Page", page_name, values)


def set_canonical_mean(source_document: str, mean: float | None) -> None:
	frappe.db.set_value("Source Document", source_document, "canonical_mean", mean)


def get_page_image(page_name: str) -> str | None:
	"""The file URL of a page's rendered PNG (attached at parse time), or None."""
	return frappe.db.get_value("Source Page", page_name, "image")


def get_canonical_composites(source_document: str) -> list[float | None]:
	"""Each page's canonical composite (falling back to baseline composite) for the doc."""
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
	"""Overwrite a page's canonical markdown in place (furniture-strip finalize), leaving
	its composite + provenance untouched."""
	frappe.db.set_value("Source Page", page_name, "canonical_markdown", markdown)


def get_finalize_pages(source_document: str) -> list[dict]:
	"""Pages for the document-level finalize pass: name + page_no + the current best
	markdown (canonical where remediation adopted something, else baseline). The finalize
	pass needs `name` to write the reconciled markdown back via `set_canonical`."""
	pages = frappe.get_all(
		"Source Page",
		filters={"source_document": source_document},
		fields=["name", "page_no", "canonical_markdown", "baseline_markdown"],
		order_by="page_no asc",
	)
	for p in pages:
		p["markdown"] = p["canonical_markdown"] or p["baseline_markdown"] or ""
	return pages


# --- Slice 4: sectionize → Source Section tree ---


def get_canonical_pages(source_document: str) -> list[tuple[int, str]]:
	"""(page_no, markdown) for sectionizing — canonical markdown where remediation
	adopted something, else the baseline. At parse time canonical is unset, so this
	falls back to baseline; after a remediate pass it reflects the adopted output
	(so the rebuilt tree never reverts to empty/pre-cleanup text)."""
	pages = frappe.get_all(
		"Source Page",
		filters={"source_document": source_document},
		fields=["page_no", "canonical_markdown", "baseline_markdown"],
		order_by="page_no asc",
	)
	return [(p["page_no"], p["canonical_markdown"] or p["baseline_markdown"] or "") for p in pages]


# --- 0.3 Slice 18: single-section rebuild ---


def get_section_span(name: str) -> dict | None:
	"""One section's identity + page range + tree interval (the rebuild seam's input)."""
	return frappe.db.get_value(
		"Source Section",
		name,
		["name", "source_document", "title", "page_start", "page_end", "lft", "rgt", "wiki_document"],
		as_dict=True,
	)


def get_section_spans(source_document: str) -> list[dict]:
	"""Every section's page range + tree interval, ordered by tree position."""
	return frappe.get_all(
		"Source Section",
		filters={"source_document": source_document},
		fields=["name", "title", "page_start", "page_end", "lft", "rgt", "is_group", "wiki_document"],
		order_by="lft asc",
	)


def lint_json(markdown: str) -> str | None:
	"""`engine.lint` issues as a JSON string (None when clean) — the stored
	`lint_issues` shape. Lint must never block a write: a crash degrades to None
	plus an error-log entry (0.6 principle: lint is derived, saves are sacred)."""
	import json

	from wikify.engine.lint import lint_markdown

	try:
		issues = lint_markdown(markdown or "")
	except Exception:
		frappe.log_error(title="wikify: markdown lint failed")
		return None
	return json.dumps(issues) if issues else None


def lint_count(lint_issues: str | None) -> int:
	"""Issue count from a stored `lint_issues` JSON string — the badge number."""
	import json

	try:
		return len(json.loads(lint_issues)) if lint_issues else 0
	except Exception:
		return 0


def set_section_markdown(
	name: str, markdown: str, *, update_modified: bool = True, extra_values: dict | None = None
) -> None:
	"""THE write funnel for `Source Section.markdown` (0.6) — every raw markdown write
	goes through here so `lint_issues` always reflects the stored body, and (0.5) the
	section's outgoing `Section Reference` rows follow the new content. Document-path
	writes (`doc.insert`/`doc.save`, e.g. `replace_sections`) are covered by the
	controller / an explicit full-document extract instead. `extra_values` keeps
	multi-field callers on a single UPDATE (merge writes page ranges alongside)."""
	from wikify.engine.refs import extract_references

	values = {"markdown": markdown, "lint_issues": lint_json(markdown), **(extra_values or {})}
	frappe.db.set_value("Source Section", name, values, update_modified=update_modified)
	extract_references(frappe.db.get_value("Source Section", name, "source_document"), [name])


# --- Slice 6: classification ---


def get_section_taxonomy() -> list[str]:
	"""Ordered `type_name`s from the Section Type master (the classifier's label set)."""
	return frappe.get_all("Section Type", pluck="type_name", order_by="is_other asc, creation asc")


def get_sections_to_classify(source_document: str) -> list[dict]:
	"""Each section's (name, title, markdown) — the input the classifier needs."""
	return frappe.get_all(
		"Source Section",
		filters={"source_document": source_document},
		fields=["name", "title", "markdown"],
		order_by="lft asc",
	)


def set_section_type(name: str, section_type: str | None) -> None:
	frappe.db.set_value("Source Section", name, "section_type", section_type, update_modified=False)


def replace_sections(source_document: str, sections) -> int:
	"""Rebuild a doc's Source Section tree from ordered `sectionizer.Section`s.

	Replaces the existing tree wholesale (mirrors the POC's `_store_sections`):
	clear, then insert in document order, resolving each section's parent by its
	hierarchy path (the parent always precedes it). NestedSet manages `lft`/`rgt`;
	`is_group` is set for any section another section nests under. Returns the count.
	"""
	# Full rebuild: raw-delete this doc's sections (other docs' subtrees are
	# independent number-spaces, so NestedSet stays consistent without a global rebuild).
	frappe.db.delete("Source Section", {"source_document": source_document})

	parent_paths = {tuple(s.hierarchy_path[:-1]) for s in sections if len(s.hierarchy_path) > 1}
	path_to_name: dict[tuple[str, ...], str] = {}
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

	# 0.5: the tree (and its page spans) just changed wholesale — rebuild the
	# document's reference edges against it. Runs after all inserts so every
	# "page N" target can resolve.
	from wikify.engine.refs import extract_references

	extract_references(source_document)
	return len(sections)


# --- Slice 7: wiki generation ---


def get_sections_for_wiki(source_document: str) -> list[dict]:
	"""Every Source Section (ordered by tree position) with the fields wiki generation
	needs. Returns all rows — the caller filters on `include_in_wiki` and resolves
	parents — so excluded/removed sections can also be cleaned up on regeneration."""
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


# --- 0.5 Slice 26: Section Reference rows (see engine/refs.py) ---


def get_section_bodies(source_document: str, names: list[str] | None = None) -> list[dict]:
	"""(name, markdown) of a document's sections — all of them, or just `names` — the
	from-side input of reference extraction."""
	filters: dict = {"source_document": source_document}
	if names is not None:
		filters["name"] = ["in", names]
	return frappe.get_all("Source Section", filters=filters, fields=["name", "markdown"], order_by="lft asc")


def replace_references(
	source_document: str, rows: list[dict], from_sections: list[str] | None = None
) -> None:
	"""Wipe and re-insert a document's `Section Reference` rows (all of them, or just
	the ones *outgoing from* `from_sections`). Raw delete + insert — rows are derived
	data with no lifecycle of their own."""
	filters: dict = {"source_document": source_document}
	if from_sections is not None:
		filters["from_section"] = ["in", from_sections]
	frappe.db.delete("Section Reference", filters)
	for row in rows:
		doc = frappe.new_doc("Section Reference")
		doc.update(row)
		doc.insert(ignore_permissions=True)
