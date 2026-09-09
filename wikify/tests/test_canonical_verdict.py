# Copyright (c) 2026, BWH and contributors
# For license information, please see license.txt
import frappe
from frappe.tests.utils import FrappeTestCase

from wikify.engine import settings, store
from wikify.engine.verify import PageScore
from wikify.patches.v1_0.backfill_canonical_verdict import execute as backfill_canonical_verdict


def _page_score(composite: float, verdict: str) -> PageScore:
	return PageScore(
		page_no=1,
		text_recall=0.0,
		extra_ratio=0.0,
		table_score=None,
		judge_score=None,
		composite=composite,
		verdict=verdict,
	)


class TestCanonicalVerdict(FrappeTestCase):
	def setUp(self):
		self.source_document = frappe.get_doc(
			{"doctype": "Source Document", "title": "Verdict Test", "page_count": 3}
		).insert(ignore_permissions=True)
		self.pass_threshold = float(settings.get("pass_threshold"))
		self.escalate_threshold = float(settings.get("escalate_threshold"))

	def add_page(self, page_no: int, baseline_composite: float) -> str:
		page = frappe.new_doc("Source Page")
		page.source_document = self.source_document.name
		page.page_no = page_no
		page.kind = "text"
		page.baseline_markdown = f"baseline page {page_no}"
		page.insert(ignore_permissions=True)
		store.set_page_scores(page.name, _page_score(baseline_composite, "review"))
		return page.name

	def verdict_of(self, page_name: str) -> str:
		return frappe.db.get_value("Source Page", page_name, "verdict")

	def test_adopted_remediation_moves_the_verdict_off_the_baseline(self):
		page_name = self.add_page(1, 0.0)
		self.assertEqual(self.verdict_of(page_name), "review")

		store.set_canonical(page_name, "remediated body", self.pass_threshold + 0.05, "vlm")

		self.assertEqual(self.verdict_of(page_name), "pass")
		row = frappe.db.get_value(
			"Source Page", page_name, ["composite", "canonical_composite"], as_dict=True
		)
		self.assertEqual(row.composite, 0.0)
		self.assertAlmostEqual(row.canonical_composite, self.pass_threshold + 0.05, places=3)

	def test_a_genuinely_bad_canonical_still_flags(self):
		bad = self.add_page(1, 0.999)
		store.set_canonical(bad, "still mangled", self.escalate_threshold - 0.1, "vlm")
		self.assertEqual(self.verdict_of(bad), "review")

		middling = self.add_page(2, 0.999)
		store.set_canonical(middling, "partly fixed", self.escalate_threshold + 0.01, "cleanup")
		self.assertEqual(self.verdict_of(middling), "escalate")

	def test_canonical_without_a_composite_leaves_the_verdict_alone(self):
		page_name = self.add_page(1, 0.999)
		frappe.db.set_value("Source Page", page_name, "verdict", "pass")

		store.set_canonical(page_name, "![Page 1](/files/page-0001.png)", None, "image")

		self.assertEqual(self.verdict_of(page_name), "pass")

	def test_backfill_repairs_stale_verdicts_and_is_idempotent(self):
		stale = self.add_page(1, 0.0)
		store.set_canonical(stale, "remediated body", self.pass_threshold + 0.05, "vlm")
		frappe.db.set_value("Source Page", stale, "verdict", "review", update_modified=False)
		genuinely_bad = self.add_page(2, 0.2)
		store.set_canonical(genuinely_bad, "still mangled", 0.4, "vlm")
		never_remediated = self.add_page(3, 0.999)
		frappe.db.set_value("Source Page", never_remediated, "verdict", "pass", update_modified=False)

		moved = backfill_canonical_verdict()

		self.assertEqual(self.verdict_of(stale), "pass")
		self.assertEqual(self.verdict_of(genuinely_bad), "review")
		self.assertEqual(self.verdict_of(never_remediated), "pass")
		self.assertGreaterEqual(moved.get("pass", 0), 1)

		self.assertEqual(backfill_canonical_verdict(), {})
		self.assertEqual(self.verdict_of(stale), "pass")


class TestUnscoredCanonicalMarkdown(FrappeTestCase):
	def setUp(self):
		self.source_document = frappe.get_doc(
			{"doctype": "Source Document", "title": "Unscored Edit Test", "page_count": 1}
		).insert(ignore_permissions=True)
		self.page = frappe.new_doc("Source Page")
		self.page.source_document = self.source_document.name
		self.page.page_no = 1
		self.page.kind = "text"
		self.page.baseline_markdown = "baseline page 1"
		self.page.insert(ignore_permissions=True)
		store.set_canonical(self.page.name, "verified body", 0.99, "vlm")

	def row(self):
		return frappe.db.get_value(
			"Source Page",
			self.page.name,
			["verdict", "canonical_composite", "canonical_source", "canonical_markdown"],
			as_dict=True,
		)

	def test_a_rewrite_drops_the_verdict_it_no_longer_describes(self):
		self.assertEqual(self.row().verdict, "pass")

		store.set_canonical_markdown(self.page.name, "hand-edited body")

		row = self.row()
		self.assertEqual(row.canonical_markdown, "hand-edited body")
		self.assertFalse(row.verdict)
		self.assertEqual(row.canonical_composite, 0)

	def test_provenance_survives_the_rewrite(self):
		store.set_canonical_markdown(self.page.name, "hand-edited body")
		self.assertEqual(self.row().canonical_source, "vlm")

	def test_the_backfill_does_not_rebadge_an_unscored_page(self):
		store.set_canonical_markdown(self.page.name, "hand-edited body")
		backfill_canonical_verdict()
		self.assertFalse(self.row().verdict)
