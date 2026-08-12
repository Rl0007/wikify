# Copyright (c) 2026, BWH and contributors
# For license information, please see license.txt

"""Registering a question paper (`wikify.exam.ingest`).

Bookkeeping only — no LLM, no parsing. The PDF is a temp file rather than one of the real
ICAI papers because nothing here reads a single byte of it; it is copied in and attached.

The load-bearing assertion: a paper's identity is (project, month, year, paper code), NOT
its filename. ICAI reissues the same session under new BoS numbers, and registering that as
a second paper would double every count in the heatmap.
"""

import os
import shutil
import tempfile

import frappe
from frappe.tests.utils import FrappeTestCase

from wikify.exam import ingest


def one_page_pdf() -> bytes:
	"""A genuine one-page PDF.

	Frappe runs every uploaded `.pdf` through pypdf looking for embedded JavaScript, so a
	hand-written stub is rejected before it reaches the paper row. Nothing in this module
	reads the contents.
	"""
	import fitz

	with fitz.open() as document:
		page = document.new_page()
		page.insert_text((72, 72), "Question 1")
		return document.tobytes()


PDF_BYTES = one_page_pdf()


class TestRegisterPaper(FrappeTestCase):
	def setUp(self):
		suffix = frappe.generate_hash(length=8)
		self.project = frappe.get_doc(
			{"doctype": "Wikify Project", "project_name": f"Exam Ingest Test {suffix}"}
		).insert(ignore_permissions=True)
		self.directory = tempfile.mkdtemp(prefix="wikify_exam_ingest_")
		self.addCleanup(shutil.rmtree, self.directory, ignore_errors=True)

	def write_pdf(self, file_name: str) -> str:
		path = os.path.join(self.directory, file_name)
		with open(path, "wb") as handle:
			handle.write(PDF_BYTES)
		return path

	def register(self, file_name="88214bos-nov23.pdf", **overrides):
		values = {
			"project": self.project.name,
			"exam_month": "November",
			"exam_year": 2023,
			"paper_code": "Paper 7",
			"assessment_year": "2024-25",
		}
		values.update(overrides)
		return ingest.register_paper(self.write_pdf(file_name), **values)

	def test_a_new_session_is_registered_with_its_pdf_attached(self):
		report = self.register()

		self.assertFalse(report["reused"])
		document = frappe.get_doc("Wikify Exam Paper", report["paper"])
		self.assertEqual(document.project, self.project.name)
		self.assertEqual(document.exam_month, "November")
		self.assertEqual(document.exam_year, 2023)
		self.assertEqual(document.assessment_year, "2024-25")
		self.assertTrue(document.source_pdf)
		with open(frappe.get_site_path(document.source_pdf.lstrip("/")), "rb") as handle:
			self.assertEqual(handle.read(), PDF_BYTES)

	def test_the_same_session_under_a_new_bos_number_is_the_same_paper(self):
		"""Identity is the session, not the filename — otherwise the heatmap doubles."""
		first = self.register("88214bos-nov23.pdf")

		second = self.register("99999bos-nov23-reissued.pdf")

		self.assertTrue(second["reused"])
		self.assertEqual(second["paper"], first["paper"])
		self.assertEqual(frappe.db.count("Wikify Exam Paper", {"project": self.project.name}), 1)

	def test_a_different_session_is_a_different_paper(self):
		first = self.register(exam_month="November", exam_year=2023)
		second = self.register(exam_month="May", exam_year=2024, paper_code="Paper 4")

		self.assertNotEqual(first["paper"], second["paper"])
		self.assertEqual(frappe.db.count("Wikify Exam Paper", {"project": self.project.name}), 2)

	def test_the_same_session_in_another_project_is_another_paper(self):
		other = frappe.get_doc(
			{
				"doctype": "Wikify Project",
				"project_name": f"Exam Ingest Other {frappe.generate_hash(length=8)}",
			}
		).insert(ignore_permissions=True)

		first = self.register()
		second = self.register(project=other.name)

		self.assertNotEqual(first["paper"], second["paper"])

	def test_re_registering_updates_the_metadata_without_re_attaching_the_pdf(self):
		first = self.register()
		original_pdf = frappe.db.get_value("Wikify Exam Paper", first["paper"], "source_pdf")

		self.register(assessment_year="2025-26", source_url="https://icai.org/paper.pdf")

		document = frappe.get_doc("Wikify Exam Paper", first["paper"])
		self.assertEqual(document.assessment_year, "2025-26")
		self.assertEqual(document.source_url, "https://icai.org/paper.pdf")
		self.assertEqual(document.source_pdf, original_pdf)

	def test_a_title_is_derived_from_the_session_when_none_is_given(self):
		report = self.register()
		self.assertEqual(report["title"], "November 2023 — Paper 7")
		self.assertEqual(
			frappe.db.get_value("Wikify Exam Paper", report["paper"], "paper_title"),
			"November 2023 — Paper 7",
		)

	def test_an_explicit_title_wins(self):
		report = self.register(paper_title="ICAI CA Final DT — Nov 2023")
		self.assertEqual(report["title"], "ICAI CA Final DT — Nov 2023")

	def test_a_pdf_that_is_not_there_is_refused_before_anything_is_created(self):
		before = frappe.db.count("Wikify Exam Paper")

		with self.assertRaises(frappe.ValidationError):
			ingest.register_paper(
				os.path.join(self.directory, "missing.pdf"),
				project=self.project.name,
				exam_month="November",
				exam_year=2023,
			)

		self.assertEqual(frappe.db.count("Wikify Exam Paper"), before)

	def test_find_paper_matches_on_the_session_key(self):
		report = self.register()

		self.assertEqual(ingest.find_paper(self.project.name, "November", 2023, "Paper 7"), report["paper"])
		self.assertIsNone(ingest.find_paper(self.project.name, "November", 2023, "Paper 4"))
		self.assertIsNone(ingest.find_paper(self.project.name, "May", 2023, "Paper 7"))
