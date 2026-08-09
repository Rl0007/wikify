# Copyright (c) 2026, BWH and contributors
# For license information, please see license.txt

"""0.4 slice 21 — Section Type labels are identity: duplicate (normalized) labels are
rejected at insert, deduped in the creation API, and merged by the v0_4 patch."""

import frappe
from frappe.tests.utils import FrappeTestCase

from wikify.api.sections import create_section_type
from wikify.patches.v0_4.merge_duplicate_section_types import execute as merge_patch
from wikify.wikify.doctype.section_type.section_type import (
	find_by_normalized_label,
	normalize_label,
)


class TestSectionTypeDedupe(FrappeTestCase):
	def _insert(self, type_name, label, validate=True):
		doc = frappe.get_doc({"doctype": "Section Type", "type_name": type_name, "label": label})
		doc.insert(ignore_permissions=True, ignore_if_duplicate=False) if validate else doc.db_insert()
		self.addCleanup(frappe.db.delete, "Section Type", {"name": type_name})
		return doc

	def test_normalize_label(self):
		self.assertEqual(normalize_label("  Consent   FORMS "), "consent forms")
		self.assertEqual(normalize_label(None), "")

	def test_duplicate_normalized_label_rejected(self):
		self._insert("zz_dedupe_a", "Zz Dedupe Check")
		with self.assertRaises(frappe.DuplicateEntryError):
			self._insert("zz_dedupe_b", "  zz   DEDUPE check ")

	def test_create_section_type_dedupes_on_label(self):
		self._insert("zz_dedupe_a", "Zz Dedupe Check")
		res = create_section_type("Zz Dedupe Variant", label="zz dedupe CHECK")
		self.assertTrue(res["existed"])
		self.assertEqual(res["type_name"], "zz_dedupe_a")

	def test_merge_patch_collapses_duplicates_and_repoints_sections(self):
		# db_insert bypasses validate — simulating rows that predate the constraint.
		self._insert("zz_dedupe_canon", "Zz Dedupe Merge")
		self._insert("t_zzdupe1", "zz dedupe merge", validate=False)

		sd = frappe.get_doc(
			{"doctype": "Source Document", "title": f"Dedupe {frappe.generate_hash(length=6)}"}
		).insert(ignore_permissions=True)
		self.addCleanup(frappe.db.delete, "Source Section", {"source_document": sd.name})
		self.addCleanup(frappe.db.delete, "Source Document", {"name": sd.name})
		sec = frappe.get_doc(
			{
				"doctype": "Source Section",
				"source_document": sd.name,
				"title": "Pointing at the dupe",
				"section_type": "t_zzdupe1",
			}
		).insert(ignore_permissions=True)

		merge_patch()
		self.assertFalse(frappe.db.exists("Section Type", "t_zzdupe1"))
		self.assertEqual(frappe.db.get_value("Source Section", sec.name, "section_type"), "zz_dedupe_canon")
		merge_patch()  # idempotent — nothing left to merge
		self.assertTrue(frappe.db.exists("Section Type", "zz_dedupe_canon"))

	def test_find_by_normalized_label(self):
		self._insert("zz_dedupe_a", "Zz Dedupe Check")
		self.assertEqual(find_by_normalized_label("ZZ DEDUPE CHECK"), "zz_dedupe_a")
		self.assertIsNone(find_by_normalized_label("No Such Label Anywhere"))
