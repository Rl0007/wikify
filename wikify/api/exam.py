"""Whitelisted endpoints for the past-paper analysis page.

Read endpoints respect the caller's project permission the same way the rest of the app
does. The two write endpoints — extraction and mapping — are System Manager only: both
spend money and rewrite every question in a project, which is not something a reader of one
project should be able to trigger.
"""

from __future__ import annotations

from collections import Counter

import frappe

from wikify.exam import extract as exam_extract
from wikify.exam import ingest as exam_ingest
from wikify.exam import map as exam_map
from wikify.exam import score as exam_score


def assert_can_read(project: str) -> None:
	"""Refuse a project the caller cannot read.

	Checked explicitly rather than relying on the child queries: the heatmap aggregates
	across a whole project, so a caller who cannot open the project should not be able to
	learn its shape through counts either.
	"""
	if not project:
		frappe.throw("A project is required.")
	if not frappe.has_permission("Wikify Project", doc=project, ptype="read"):
		raise frappe.PermissionError(f"Not permitted to read project '{project}'.")


@frappe.whitelist()
def heatmap(project: str, sources: str | list | None = None) -> dict:
	"""The topic-by-year matrix, ranked topics, and the gaps — scoped to `sources`.

	`sources` is a list of `Source Document` names (or a JSON string of one, since a GET
	carries it as text). Omit it for every source in the project.
	"""
	assert_can_read(project)
	scoped = frappe.parse_json(sources) if isinstance(sources, str) else sources
	return exam_score.matrix(project, sources=scoped or None)


@frappe.whitelist()
def sources(project: str) -> list[dict]:
	"""The study-material documents a heatmap can be scoped to, with their section counts."""
	assert_can_read(project)
	documents = frappe.get_all(
		"Source Document",
		filters={"project": project},
		fields=["name", "title", "page_count", "status"],
		order_by="title asc",
	)
	if not documents:
		return []
	# Counted in Python rather than with a SQL aggregate: Frappe rejects function strings in
	# `fields`, and a project holds a handful of documents, so pulling the column and
	# tallying it costs nothing.
	from collections import Counter

	by_document = Counter(
		frappe.get_all(
			"Source Section",
			filters={"source_document": ["in", [row["name"] for row in documents]]},
			pluck="source_document",
		)
	)
	for row in documents:
		row["sections"] = by_document.get(row["name"], 0)
	return documents


@frappe.whitelist()
def papers(project: str) -> list[dict]:
	"""Every registered paper for a project, newest sitting first."""
	assert_can_read(project)
	papers = frappe.get_all(
		"Wikify Exam Paper",
		filters={"project": project},
		fields=[
			"name",
			"paper_title",
			"paper_code",
			"exam_month",
			"exam_year",
			"assessment_year",
			"status",
			"stage_label",
			"stage_progress",
			"question_count",
			"total_marks",
			"attempted_marks",
			"source_pdf",
			"error",
		],
		order_by="exam_year desc, creation desc",
	)
	if not papers:
		return []

	# How many of each paper's questions actually carry a topic mapping. Derived, not read
	# from `status`: mapping runs across the whole project, so a paper extracted in one run
	# gets mapped by a later one and its stored status goes stale. Seven papers read
	# "Extracted" while their questions were in fact mapped, which is a status that lies.
	mapped = Counter(
		frappe.get_all(
			"Wikify Exam Question",
			filters={"project": project, "mapping_status": "Mapped"},
			pluck="exam_paper",
		)
	)
	for paper in papers:
		paper["mapped_questions"] = mapped.get(paper["name"], 0)
		paper["state"] = paper_state(paper)
	return papers


def paper_state(paper: dict) -> str:
	"""A plain-language state for one paper, derived from what is actually true.

	`Wikify Exam Paper.status` is pipeline vocabulary — "Extracted", "Mapped" — and it is
	both jargon and unreliable. This collapses it into something a reader can act on.
	"""
	if paper.get("status") == "Failed":
		return "failed"
	if paper.get("status") in ("Extracting", "Mapping", "Draft"):
		return "working"
	if not paper.get("question_count"):
		return "empty"
	if not paper.get("mapped_questions"):
		return "unmapped"
	return "ready"


@frappe.whitelist()
def topic_questions(project: str, topic: str, year: int | None = None) -> dict:
	"""The questions behind one heatmap cell, and the sections that teach them.

	`topic` is the normalised title key the matrix uses as a row identity, not a section
	name — the same chapter can exist at several places in the section tree, and the row a
	student clicked covers all of them.
	"""
	assert_can_read(project)

	filters = {"project": project, "mapping_status": "Mapped"}
	if year:
		filters["exam_year"] = frappe.utils.cint(year)
	questions = frappe.get_all(
		"Wikify Exam Question",
		filters=filters,
		fields=[
			"name",
			"exam_paper",
			"question_no",
			"question_kind",
			"marks",
			"exam_year",
			"assessment_year",
			"is_compulsory",
			"page_no",
			"question_text",
			"statutory_refs",
		],
		order_by="exam_year desc, question_no asc",
	)
	if not questions:
		return {"topic": topic, "questions": [], "sections": []}

	links = frappe.get_all(
		"Question Topic Link",
		filters={"parent": ["in", [row["name"] for row in questions]]},
		fields=["parent", "topic_title", "topic_section", "section", "score", "rank", "method"],
		order_by="rank asc",
	)

	wanted = exam_score.topic_key(topic)
	matched: list[dict] = []
	sections: set[str] = set()
	for row in questions:
		hits = [
			link
			for link in links
			if link["parent"] == row["name"] and exam_score.topic_key(link["topic_title"]) == wanted
		]
		if not hits:
			continue
		row["match"] = hits[0]
		matched.append(row)
		sections.update(link["section"] for link in hits if link.get("section"))

	readables = (
		frappe.get_all(
			"Source Section",
			filters={"name": ["in", sorted(sections)]},
			fields=[
				"name",
				"title",
				"hierarchy_path",
				"page_start",
				"page_end",
				"wiki_document",
				"source_document",
			],
			order_by="page_start asc",
		)
		if sections
		else []
	)

	# Resolve each section's study-material PDF so the UI can deep-link to the page that
	# teaches it. One query for the documents rather than a lookup per section: a topic can
	# span dozens of sections and they nearly all come from the same document.
	document_names = sorted({row["source_document"] for row in readables if row.get("source_document")})
	pdfs = (
		{
			row["name"]: row["pdf"]
			for row in frappe.get_all(
				"Source Document", filters={"name": ["in", document_names]}, fields=["name", "pdf"]
			)
		}
		if document_names
		else {}
	)
	for row in readables:
		row["pdf"] = pdfs.get(row.get("source_document"))

	return {"topic": topic, "questions": matched, "sections": readables}


@frappe.whitelist(methods=["POST"])
def extract_paper(paper: str) -> dict:
	"""Extract questions from a registered paper's PDF. Costs LLM spend."""
	frappe.only_for("System Manager")
	return exam_extract.extract_paper(paper)


@frappe.whitelist(methods=["POST"])
def map_questions(project: str) -> dict:
	"""(Re)map every extracted question in a project onto corpus topics."""
	frappe.only_for("System Manager")
	return exam_map.map_project(project)


@frappe.whitelist(methods=["POST"])
def upload_paper(
	project: str,
	file_url: str,
	exam_month: str,
	exam_year: int,
	assessment_year: str | None = None,
	paper_code: str | None = None,
	paper_title: str | None = None,
) -> dict:
	"""Register an uploaded question paper and queue extraction + mapping.

	Returns as soon as the paper row exists, with the job queued. Extraction is several LLM
	calls and runs for tens of seconds; holding the request open for it would time out the
	browser and leave the caller unable to tell a slow paper from a failed one. The SPA polls
	`Wikify Exam Paper.status` instead.
	"""
	frappe.only_for("System Manager")
	assert_can_read(project)

	registered = exam_ingest.register_paper(
		paper_path(file_url),
		project=project,
		exam_month=exam_month,
		exam_year=frappe.utils.cint(exam_year),
		assessment_year=assessment_year,
		paper_code=paper_code,
		paper_title=paper_title,
	)
	frappe.db.set_value("Wikify Exam Paper", registered["paper"], "status", "Extracting")
	frappe.enqueue(
		"wikify.jobs.exam.run",
		queue="long",
		timeout=1800,
		paper=registered["paper"],
	)
	return registered


def paper_path(file_url: str) -> str:
	"""Resolve an uploaded `file_url` to a path inside this site, refusing anything else.

	`file_url` comes from the client and is treated as hostile: without the containment check
	a crafted `../../` would hand an arbitrary host file to the extractor.
	"""
	import os

	site_root = os.path.realpath(frappe.get_site_path())
	path = os.path.realpath(frappe.get_site_path(file_url.lstrip("/")))
	if not path.startswith(site_root + os.sep):
		frappe.throw("The paper must be a file uploaded to this site.")
	if not os.path.isfile(path):
		frappe.throw(f"No file found at '{file_url}'.")
	if not path.lower().endswith(".pdf"):
		frappe.throw("Question papers must be PDFs.")
	return path


@frappe.whitelist(methods=["POST"])
def delete_paper(paper: str) -> None:
	"""Remove a paper and every question extracted from it."""
	frappe.only_for("System Manager")
	project = frappe.db.get_value("Wikify Exam Paper", paper, "project")
	assert_can_read(project)
	exam_extract.remove_questions(paper)
	frappe.delete_doc("Wikify Exam Paper", paper)


@frappe.whitelist(methods=["POST"])
def reextract_paper(paper: str) -> dict:
	"""Re-run extraction and mapping for an existing paper.

	The route back from a bad extraction. Queued rather than run inline for the same reason
	as the upload: it is minutes of LLM calls, and a paper stuck mid-run should show as
	`Extracting` rather than as a dead request.
	"""
	frappe.only_for("System Manager")
	project = frappe.db.get_value("Wikify Exam Paper", paper, "project")
	assert_can_read(project)
	frappe.db.set_value("Wikify Exam Paper", paper, {"status": "Extracting", "error": None})
	frappe.enqueue("wikify.jobs.exam.run", queue="long", timeout=1800, paper=paper)
	return {"paper": paper, "queued": True}


@frappe.whitelist()
def paper_detail(paper: str) -> dict:
	"""One paper with its questions — the evidence behind its extracted mark totals."""
	project = frappe.db.get_value("Wikify Exam Paper", paper, "project")
	assert_can_read(project)
	document = frappe.get_doc("Wikify Exam Paper", paper)
	questions = frappe.get_all(
		"Wikify Exam Question",
		filters={"exam_paper": paper},
		fields=[
			"name",
			"question_no",
			"part_label",
			"question_kind",
			"marks",
			"is_compulsory",
			"page_no",
			"mapping_status",
			"statutory_refs",
			"question_text",
		],
		order_by="part_label asc, question_no asc",
	)
	return {"paper": document.as_dict(), "questions": questions}
