# Copyright (c) 2026, BWH and contributors
# For license information, please see license.txt

import tempfile
from pathlib import Path
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from wikify.engine import classify, finalize_document, parse_pdf
from wikify.tests.test_parse_pipeline import _make_sample_pdf

_BANNER = "## **PROCEDURE MANUAL - SAMPLE**"
_DOCCODE = "> **MAN/OBG/001 Ver.:06 Pg 1 of 3**"
_FOOTER = "|**Prepared by - Dr. A**|**Issued by: QMC**|**Approved by - Dr. B**|\n|---|---|---|"


class TestFinalizePersistsFurnitureRemoval(FrappeTestCase):
	"""finalize_document strips running furniture from canonical markdown and persists it
	(the section build already did so transiently; this makes the review view match)."""

	def _parse_with_furniture(self) -> tuple[str, str, list[dict]]:
		path = Path(tempfile.mkdtemp()) / "sample.pdf"
		_make_sample_pdf(str(path))
		with patch.object(classify, "classify_section", return_value="other"):
			sd = parse_pdf(str(path), title="Finalize Test")

		pages = frappe.get_all(
			"Source Page",
			filters={"source_document": sd},
			fields=["name", "page_no", "baseline_markdown"],
			order_by="page_no asc",
		)
		# Seed each page's canonical markdown with furniture wrapping a deterministic body
		# marker — the state the per-page cleanup leaves behind (banner recurs on every
		# page; the sign-off footer is structural furniture).
		for p in pages:
			body = f"## {p['page_no']}. Section\nBODY-{p['page_no']} real content here"
			canonical = f"{_BANNER}\n{_DOCCODE}\n\n{body}\n\n{_FOOTER}"
			frappe.db.set_value("Source Page", p["name"], "canonical_markdown", canonical)
		return sd, str(path), pages

	def test_finalize_strips_and_persists_furniture(self):
		sd, pdf_path, pages = self._parse_with_furniture()

		with patch.object(classify, "classify_section", return_value="other"):
			result = finalize_document(sd, pdf_path)

		self.assertEqual(result["pages_changed"], len(pages))
		canon = frappe.get_all(
			"Source Page",
			filters={"source_document": sd},
			fields=["canonical_markdown"],
			order_by="page_no asc",
		)
		for i, row in enumerate(canon, start=1):
			md = row["canonical_markdown"]
			self.assertNotIn("PROCEDURE MANUAL", md)  # recurring banner gone
			self.assertNotIn("MAN/OBG", md)  # doc-code line gone
			self.assertNotIn("Prepared by", md)  # sign-off footer gone
			self.assertNotIn("|---|---|---|", md)  # its orphaned separator gone
			self.assertIn(f"BODY-{i} real content", md)  # real body preserved

	def test_finalize_is_idempotent(self):
		sd, pdf_path, _ = self._parse_with_furniture()
		with patch.object(classify, "classify_section", return_value="other"):
			finalize_document(sd, pdf_path)
			# Second run finds nothing left to strip.
			result2 = finalize_document(sd, pdf_path)
		self.assertEqual(result2["pages_changed"], 0)
