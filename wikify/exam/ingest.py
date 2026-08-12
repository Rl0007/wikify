"""Registering a question paper PDF as a `Wikify Exam Paper`.

Kept apart from `extract` because registering a paper is bookkeeping — copy the file in,
record which session it is — while extraction is the part that costs money and can fail.
Splitting them means a failed extraction can be re-run without re-uploading anything.
"""

from __future__ import annotations

import os

import frappe
from frappe.utils.file_manager import save_file


def find_paper(project: str, exam_month: str, exam_year: int, paper_code: str | None) -> str | None:
	"""An existing paper for this session, if one was already registered.

	Identity is (project, month, year, paper code) rather than the filename: ICAI reissues
	the same session under new BoS numbers, and matching on filename would register the
	same exam twice and double every count in the heatmap.
	"""
	filters = {"project": project, "exam_month": exam_month, "exam_year": exam_year}
	if paper_code:
		filters["paper_code"] = paper_code
	existing = frappe.get_all("Wikify Exam Paper", filters=filters, pluck="name", limit=1)
	return existing[0] if existing else None


def register_paper(
	pdf_path: str,
	*,
	project: str,
	exam_month: str,
	exam_year: int,
	assessment_year: str | None = None,
	paper_code: str | None = None,
	paper_title: str | None = None,
	source_url: str | None = None,
) -> dict:
	"""Create (or update) the paper for a session and attach its PDF. Idempotent."""
	if not os.path.isfile(pdf_path):
		frappe.throw(f"No PDF at '{pdf_path}'.")

	title = paper_title or f"{exam_month} {exam_year} — {paper_code or 'Direct Tax'}"
	name = find_paper(project, exam_month, exam_year, paper_code)
	values = {
		"paper_title": title,
		"project": project,
		"paper_code": paper_code,
		"exam_month": exam_month,
		"exam_year": exam_year,
		"assessment_year": assessment_year,
		"source_url": source_url,
	}

	if name:
		document = frappe.get_doc("Wikify Exam Paper", name)
		document.update(values)
		document.save()
		reused = True
	else:
		document = frappe.get_doc({"doctype": "Wikify Exam Paper", **values}).insert()
		reused = False

	if not document.source_pdf:
		with open(pdf_path, "rb") as handle:
			file_doc = save_file(
				os.path.basename(pdf_path), handle.read(), document.doctype, document.name, is_private=1
			)
		document.db_set("source_pdf", file_doc.file_url)

	return {"paper": document.name, "title": title, "reused": reused}
