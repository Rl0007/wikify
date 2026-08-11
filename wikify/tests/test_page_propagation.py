# Copyright (c) 2026, BWH and contributors
# For license information, please see license.txt

"""Page → section → index propagation: a fixed page must not leave stale copies behind.

`Source Section.markdown` is a *copy* of its pages' canonical markdown, so re-remediating
a page used to leave every section built from it — and every chunk indexed from those
sections — serving the old text until somebody re-sectioned the whole document by hand.
These cover the doc_event that closes it: change detection, per-document coalescing (so a
bulk write queues one job, not one per page), the rebuild itself, the explicit re-index
(`rebuild_section_markdown` writes via `frappe.db.set_value`, which fires no doc_event),
and the requeue when a pass dies half-done.
"""

from contextlib import contextmanager
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from wikify.engine import store
from wikify.engine.loader.sectionizer import Section
from wikify.rag import events
from wikify.tests import _cleanup


@contextmanager
def doc_events_live():
	"""Let the handler run: `indexing_suspended()` gates out `in_test` by design."""
	frappe.flags.in_test = False
	try:
		yield
	finally:
		frappe.flags.in_test = True


def add_page(source_document: str, page_no: int, canonical: str) -> str:
	"""A Source Page row without the rendered-PNG File (File inserts trip the test-mode
	global-search assertion in this environment; propagation never touches the image)."""
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
		# Alpha owns pages 1-2 exclusively; Beta and Gamma share boundary page 3.
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
		page = frappe.get_doc("Source Page", self.pages[page_no])
		page.canonical_markdown = markdown
		page.save(ignore_permissions=True)

	def test_a_changed_page_marks_itself_dirty_and_queues_one_pass(self):
		with doc_events_live(), patch("frappe.enqueue") as enqueue:
			self.save_canonical(1, "canonical page 1 — surcharge slabs repaired")

		self.assertEqual(enqueue.call_args.args[0], "wikify.rag.events.propagate_dirty_pages")
		self.assertEqual(enqueue.call_args.kwargs["source_document"], self.source_document.name)
		self.assertEqual(enqueue.call_args.kwargs["queue"], "long")
		self.assertEqual(events.take_dirty_pages(self.source_document.name), [1])

	def test_a_save_that_leaves_canonical_markdown_alone_queues_nothing(self):
		with doc_events_live(), patch("frappe.enqueue") as enqueue:
			page = frappe.get_doc("Source Page", self.pages[1])
			page.kind = "mixed"
			page.save(ignore_permissions=True)

		enqueue.assert_not_called()
		self.assertEqual(events.take_dirty_pages(self.source_document.name), [])

	def test_a_bulk_page_write_queues_one_pass_not_one_job_per_page(self):
		"""A parse writes hundreds of pages; one job each would bury the long queue."""
		with doc_events_live(), patch("frappe.enqueue") as enqueue:
			for page_no in range(1, 5):
				self.save_canonical(page_no, f"canonical page {page_no} — rewritten")
			# The same page saved repeatedly still costs one pass, and one rebuild.
			self.save_canonical(1, "canonical page 1 — rewritten twice")

		self.assertEqual(enqueue.call_count, 1)
		self.assertEqual(events.take_dirty_pages(self.source_document.name), [1, 2, 3, 4])

	def test_a_new_page_queues_nothing_because_no_section_copied_it_yet(self):
		with doc_events_live(), patch("frappe.enqueue") as enqueue:
			add_page(self.source_document.name, 5, "canonical page 5")

		enqueue.assert_not_called()

	def test_the_pass_rebuilds_every_section_covering_a_changed_page(self):
		marker = "SURCHARGE-MARKER-42"
		store.set_canonical_markdown(self.pages[3], f"canonical page 3 {marker}")
		frappe.cache().hset(events.DIRTY_PAGES_HASH, f"{self.source_document.name}:3", 1)

		events.propagate_dirty_pages(self.source_document.name)

		# Page 3 is a boundary page: both owners carry the fix, Alpha (pages 1-2) does not.
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
		"""A 30-page bulk write went stale this way before the tail re-arm existed.

		A writer that finds the marker set enqueues nothing — it banks on the queued pass
		picking its pages up. If it marks them *after* that pass has already claimed, its
		pages sit in the hash with no job behind them and never propagate.
		"""
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
		"""`cache.get_value` answers from process-local memory, so the writer that set the
		marker never saw the worker clear it and coalesced later edits into a dead job."""
		cache = frappe.cache()
		key = events.page_propagation_key(self.source_document.name)
		cache.set_value(key, "1", expires_in_sec=events.PENDING_TTL_SECONDS)
		# What the worker's `delete_value` does to redis, without touching this process's
		# local cache — the exact split that stranded a 30-page bulk write.
		cache.unlink(cache.make_key(key))

		with doc_events_live(), patch("frappe.enqueue") as enqueue:
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
		"""A worker dying mid-pass used to leave section text new and the index old, silently."""
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
		"""`set_section_markdown` fires no doc_event, so the reindex hook never sees it.

		One call, not one per section: each `upsert_sections` rebuilds the whole FTS index.
		"""
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
