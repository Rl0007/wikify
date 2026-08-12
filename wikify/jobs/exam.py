"""Background job for a newly uploaded question paper: extract, then map.

Runs on the long queue like the rest of the pipeline. Extraction is several LLM calls per
paper and takes tens of seconds, which is far past what a request should hold open — and the
`Wikify Exam Paper.status` field already gives the SPA something to poll.
"""

from __future__ import annotations

import frappe

from wikify.exam import extract as exam_extract
from wikify.exam import map as exam_map


def run(paper: str, remap: bool = True) -> dict:
	"""Extract a paper's questions and re-map the project's topics.

	The mapping pass covers the whole project rather than just this paper: topic scores are
	relative, so a new paper changes where every other topic sits in the ranking. Mapping is
	free — local embeddings — so there is no reason to be clever about scoping it.
	"""
	report = exam_extract.extract_paper(paper)
	frappe.db.commit()

	if remap:
		project = frappe.db.get_value("Wikify Exam Paper", paper, "project")
		paper_doc = frappe.get_doc("Wikify Exam Paper", paper)
		paper_doc.db_set(
			{"status": "Mapping", "stage_label": "Mapping questions to topics", "stage_progress": 95}
		)
		frappe.db.commit()
		try:
			report["mapping"] = exam_map.map_project(project)
			paper_doc.db_set({"status": "Mapped", "stage_label": None, "stage_progress": 100})
		except Exception:
			# Extraction already succeeded and its questions are stored; a mapping failure
			# should not present as though the upload was lost.
			paper_doc.db_set({"status": "Extracted", "error": frappe.get_traceback()})
			raise
		frappe.db.commit()

	return report
