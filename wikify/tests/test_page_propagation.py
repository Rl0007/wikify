# Copyright (c) 2026, BWH and contributors
# For license information, please see license.txt
from contextlib import contextmanager
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from wikify.api import sections as sections_api
from wikify.engine import store
from wikify.engine.loader.sectionizer import Section
from wikify.engine.verify.harness import PageScore
from wikify.rag import events
from wikify.tests import _cleanup


@contextmanager
def invalidation_live():
	frappe.flags.in_test = False
	try:
		yield
	finally:
		frappe.flags.in_test = True


def _score(composite: float) -> PageScore:
	return PageScore(
		page_no=1,
		text_recall=composite,
		extra_ratio=0.0,
		table_score=None,
		judge_score=None,
		composite=composite,
		verdict="pass",
		notes=[],
	)


def add_page(source_document: str, page_no: int, canonical: str) -> str:
	page = frappe.new_doc("Source Page")
	page.source_document = source_document
	page.page_no = page_no
	page.kind = "text"
	page.baseline_markdown = f"baseline page {page_no}"
	page.canonical_markdown = canonical
	page.insert(ignore_permissions=True)
	return page.name


class TestPagePropagation(FrappeTestCase):
	def setUp(self):
		self.source_document = frappe.get_doc(
			{"doctype": "Source Document", "title": "Propagation Test", "page_count": 4}
		).insert(ignore_permissions=True)
		self.pages = {
			page_no: add_page(self.source_document.name, page_no, f"canonical page {page_no}")
			for page_no in range(1, 5)
		}
		store.replace_sections(
			self.source_document.name,
			[
				Section("1. Alpha", 1, ["1. Alpha"], 1, 2, "stale alpha body"),
				Section("2. Beta", 1, ["2. Beta"], 3, 3, "stale beta body"),
				Section("3. Gamma", 1, ["3. Gamma"], 3, 4, "stale gamma body"),
			],
		)
		self.clear_markers()

	def tearDown(self):
		self.clear_markers()
		_cleanup._delete_document_rows(self.source_document.name)
		# nosemgrep
		frappe.db.commit()

	def clear_markers(self):
		frappe.cache().delete_value(events.page_propagation_key(self.source_document.name))
		events.take_dirty_pages(self.source_document.name)

	def section_named(self, title: str) -> str:
		return frappe.db.get_value(
			"Source Section", {"source_document": self.source_document.name, "title": title}, "name"
		)

	def markdown_of(self, title: str) -> str:
		return frappe.db.get_value("Source Section", self.section_named(title), "markdown")

	def save_canonical(self, page_no: int, markdown: str) -> None:
		store.set_canonical_markdown(self.pages[page_no], markdown)

	def test_a_changed_page_marks_itself_dirty_and_queues_one_pass(self):
		with invalidation_live(), patch("frappe.enqueue") as enqueue:
			self.save_canonical(1, "canonical page 1 — surcharge slabs repaired")

		self.assertEqual(enqueue.call_args.args[0], "wikify.rag.events.propagate_dirty_pages")
		self.assertEqual(enqueue.call_args.kwargs["source_document"], self.source_document.name)
		self.assertEqual(enqueue.call_args.kwargs["queue"], "long")
		self.assertEqual(events.take_dirty_pages(self.source_document.name), [1])

	def test_adopting_a_remediation_invalidates_the_page_it_rewrote(self):
		with invalidation_live(), patch("frappe.enqueue") as enqueue:
			store.set_canonical(self.pages[2], "canonical page 2 — repaired", 0.94, "vlm")

		self.assertEqual(enqueue.call_args.args[0], "wikify.rag.events.propagate_dirty_pages")
		self.assertEqual(events.take_dirty_pages(self.source_document.name), [2])

	def test_a_write_that_leaves_canonical_markdown_alone_queues_nothing(self):
		with invalidation_live(), patch("frappe.enqueue") as enqueue:
			store.set_page_scores(self.pages[1], _score(0.91))

		enqueue.assert_not_called()
		self.assertEqual(events.take_dirty_pages(self.source_document.name), [])

	def test_a_bulk_page_write_queues_one_pass_not_one_job_per_page(self):
		with invalidation_live(), patch("frappe.enqueue") as enqueue:
			for page_no in range(1, 5):
				self.save_canonical(page_no, f"canonical page {page_no} — rewritten")
			self.save_canonical(1, "canonical page 1 — rewritten twice")

		self.assertEqual(enqueue.call_count, 1)
		self.assertEqual(events.take_dirty_pages(self.source_document.name), [1, 2, 3, 4])

	def test_a_pass_that_rebuilds_the_tree_itself_suspends_page_invalidation(self):
		with invalidation_live(), patch("frappe.enqueue") as enqueue:
			with events.suspended_indexing():
				for page_no in range(1, 5):
					self.save_canonical(page_no, f"canonical page {page_no} — bulk")

		enqueue.assert_not_called()
		self.assertEqual(events.take_dirty_pages(self.source_document.name), [])

	def test_a_new_page_queues_nothing_because_no_section_copied_it_yet(self):
		with invalidation_live(), patch("frappe.enqueue") as enqueue:
			add_page(self.source_document.name, 5, "canonical page 5")

		enqueue.assert_not_called()

	def test_the_pass_rebuilds_every_section_covering_a_changed_page(self):
		marker = "SURCHARGE-MARKER-42"
		store.set_canonical_markdown(self.pages[3], f"canonical page 3 {marker}")
		frappe.cache().hset(events.DIRTY_PAGES_HASH, f"{self.source_document.name}:3", 1)

		events.propagate_dirty_pages(self.source_document.name)

		self.assertIn(marker, self.markdown_of("2. Beta"))
		self.assertIn(marker, self.markdown_of("3. Gamma"))
		self.assertNotIn(marker, self.markdown_of("1. Alpha"))
		self.assertEqual(events.take_dirty_pages(self.source_document.name), [])

	def test_the_pass_clears_its_marker_before_working_so_a_mid_run_edit_is_never_lost(self):
		frappe.cache().set_value(
			events.page_propagation_key(self.source_document.name),
			"1",
			expires_in_sec=events.PENDING_TTL_SECONDS,
		)
		frappe.cache().hset(events.DIRTY_PAGES_HASH, f"{self.source_document.name}:1", 1)

		events.propagate_dirty_pages(self.source_document.name)

		self.assertFalse(frappe.cache().get_value(events.page_propagation_key(self.source_document.name)))

	def test_a_write_that_lost_the_claim_race_is_re_armed_not_stranded(self):
		frappe.cache().hset(events.DIRTY_PAGES_HASH, f"{self.source_document.name}:1", 1)

		def late_writer(source_document, pages):
			events.mark_pages_dirty(source_document, [4])

		with (
			patch("wikify.rag.events.propagate_pages", side_effect=late_writer),
			patch("frappe.enqueue") as enqueue,
		):
			events.propagate_dirty_pages(self.source_document.name)

		self.assertEqual(enqueue.call_args.args[0], "wikify.rag.events.propagate_dirty_pages")
		self.assertEqual(events.take_dirty_pages(self.source_document.name), [4])

	def test_a_marker_the_worker_already_cleared_does_not_swallow_the_next_change(self):
		cache = frappe.cache()
		key = events.page_propagation_key(self.source_document.name)
		cache.set_value(key, "1", expires_in_sec=events.PENDING_TTL_SECONDS)
		cache.unlink(cache.make_key(key))

		with invalidation_live(), patch("frappe.enqueue") as enqueue:
			self.save_canonical(1, "canonical page 1 — after the worker finished")

		self.assertEqual(enqueue.call_args.args[0], "wikify.rag.events.propagate_dirty_pages")

	def test_a_claim_only_takes_the_document_it_names(self):
		other = "some-other-document"
		events.mark_pages_dirty(self.source_document.name, [1, 2])
		events.mark_pages_dirty(other, [9])
		try:
			self.assertEqual(events.take_dirty_pages(self.source_document.name), [1, 2])
			self.assertEqual(events.dirty_page_fields(other), [f"{other}:9"])
		finally:
			events.take_dirty_pages(other)

	def test_a_failed_pass_requeues_the_pages_it_claimed(self):
		frappe.cache().hset(events.DIRTY_PAGES_HASH, f"{self.source_document.name}:1", 1)

		with (
			patch("wikify.engine.sectionize.rebuild_section_markdown", side_effect=RuntimeError("died")),
			patch("frappe.enqueue") as enqueue,
			self.assertRaises(RuntimeError),
		):
			events.propagate_dirty_pages(self.source_document.name)

		self.assertEqual(enqueue.call_args.args[0], "wikify.rag.events.propagate_dirty_pages")
		self.assertTrue(frappe.cache().get_value(events.page_propagation_key(self.source_document.name)))
		self.assertEqual(events.take_dirty_pages(self.source_document.name), [1])

	def test_rebuilt_sections_are_pushed_into_the_index_in_one_batch(self):
		with patch("wikify.rag.index.upsert_sections") as upsert:
			events.reindex_sections("PRJ-TEST", ["SEC-A", "SEC-B"])

		upsert.assert_called_once_with(["SEC-A", "SEC-B"])

	def test_a_wide_fanout_queues_one_project_rebuild_instead_of_a_burst_of_upserts(self):
		many = [f"SEC-{n}" for n in range(events.PROJECT_REBUILD_FANOUT + 1)]

		with (
			patch("wikify.rag.index.upsert_sections") as upsert,
			patch("wikify.rag.events.queue_project_rebuild") as queue_project,
		):
			events.reindex_sections("PRJ-TEST", many)

		upsert.assert_not_called()
		queue_project.assert_called_once_with("PRJ-TEST")

	def test_a_document_outside_any_project_reindexes_nothing(self):
		with patch("wikify.rag.index.upsert_sections") as upsert:
			events.reindex_sections(None, ["SEC-A"])

		upsert.assert_not_called()


class TestSectionInvalidation(FrappeTestCase):
	def setUp(self):
		self.project = frappe.get_doc(
			{
				"doctype": "Wikify Project",
				"project_name": f"Section Invalidation {frappe.generate_hash(length=6)}",
			}
		).insert(ignore_permissions=True)
		self.source_document = frappe.get_doc(
			{
				"doctype": "Source Document",
				"title": "Section Invalidation",
				"page_count": 2,
				"project": self.project.name,
			}
		).insert(ignore_permissions=True)
		add_page(self.source_document.name, 1, "canonical page 1")
		store.replace_sections(
			self.source_document.name,
			[
				Section("1. Alpha", 1, ["1. Alpha"], 1, 1, "stale alpha body"),
				Section("2. Beta", 1, ["2. Beta"], 1, 1, "stale beta body"),
			],
		)
		frappe.cache().delete_value(events.pending_key(self.project.name))

	def tearDown(self):
		frappe.cache().delete_value(events.pending_key(self.project.name))
		_cleanup._delete_document_rows(self.source_document.name)
		# nosemgrep
		frappe.db.commit()

	def section_named(self, title: str) -> str:
		return frappe.db.get_value(
			"Source Section", {"source_document": self.source_document.name, "title": title}, "name"
		)

	def test_a_markdown_edit_reindexes_the_section_it_rewrote(self):
		section = self.section_named("1. Alpha")
		with invalidation_live(), patch("wikify.rag.index.upsert_sections") as upsert:
			store.set_section_markdown(section, "surcharge slabs, repaired")

		upsert.assert_called_once_with([section])

	def test_retagging_a_section_reindexes_it(self):
		section = self.section_named("2. Beta")
		with invalidation_live(), patch("wikify.rag.index.upsert_sections") as upsert:
			sections_api.set_section_type(section, None)

		upsert.assert_called_once_with([section])

	def test_a_rename_rebuilds_the_project_because_it_moves_every_hierarchy_path(self):
		section = self.section_named("1. Alpha")
		with invalidation_live(), patch("frappe.enqueue") as enqueue:
			sections_api.rename_section(section, "1. Alpha (revised)")

		self.assertEqual(enqueue.call_args.args[0], "wikify.rag.events.rebuild_pending_project")
		self.assertEqual(enqueue.call_args.kwargs["project"], self.project.name)

	def test_a_bulk_pass_that_rebuilds_the_tree_itself_reindexes_nothing_per_section(self):
		section = self.section_named("1. Alpha")
		with invalidation_live(), patch("wikify.rag.index.upsert_sections") as upsert:
			with events.suspended_indexing():
				store.set_section_markdown(section, "written inside a bulk pass")

		upsert.assert_not_called()

	def test_a_section_outside_any_project_reindexes_nothing(self):
		orphan = frappe.get_doc(
			{"doctype": "Source Document", "title": "No Project", "page_count": 1}
		).insert(ignore_permissions=True)
		store.replace_sections(orphan.name, [Section("1. Orphan", 1, ["1. Orphan"], 1, 1, "body")])
		section = frappe.db.get_value("Source Section", {"source_document": orphan.name}, "name")

		with invalidation_live(), patch("wikify.rag.index.upsert_sections") as upsert:
			store.set_section_markdown(section, "still outside any project")

		upsert.assert_not_called()
		_cleanup._delete_document_rows(orphan.name)
