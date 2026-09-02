# Copyright (c) 2026, BWH and contributors
# For license information, please see license.txt

"""Recovering an Import whose background job died: the stale error is cleared when a
new run starts, `retry_import` restarts a failed or stuck import, and the scheduled
`fail_stuck_imports` fails one whose progress stopped.

The progress/log helpers are patched out throughout — they `frappe.db.commit()`, which
would break the test rollback and leak rows into the site.
"""

import tempfile
from pathlib import Path
from unittest.mock import DEFAULT, patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, now_datetime
from frappe.utils.file_manager import save_file

from wikify import tasks
from wikify.api import imports as imports_api
from wikify.engine import store
from wikify.jobs import generate as generate_job
from wikify.jobs import remediate as remediate_job
from wikify.tests.test_parse_pipeline import _make_sample_pdf

OLD_ERROR = "Traceback (most recent call last):\n  RuntimeError: from a run in August"


def _without_realtime(module):
	helpers = {name: DEFAULT for name in ("publish_progress", "log") if hasattr(module, name)}
	return patch.multiple(module, **helpers)


class TestJobRecovery(FrappeTestCase):
	def setUp(self):
		self.project = frappe.get_doc(
			{
				"doctype": "Wikify Project",
				"project_name": f"Job Recovery {frappe.generate_hash(length=6)}",
			}
		).insert(ignore_permissions=True)

	def _import(self, status: str, error: str | None = OLD_ERROR, with_document: bool = False) -> str:
		imp = frappe.get_doc(
			{
				"doctype": "Wikify Import",
				"import_title": "Handbook",
				"project": self.project.name,
				"pdf": "/private/files/handbook.pdf",
				"status": status,
				"error": error,
				"source_document": store.create_document("Handbook") if with_document else None,
			}
		).insert(ignore_permissions=True)
		return imp.name

	def _age(self, import_name: str, minutes: int) -> None:
		frappe.db.set_value(
			"Wikify Import",
			import_name,
			"modified",
			add_to_date(now_datetime(), minutes=-minutes),
			update_modified=False,
		)

	def _read(self, import_name: str) -> dict:
		return frappe.db.get_value("Wikify Import", import_name, ["status", "error"], as_dict=True)

	def test_remediate_job_clears_the_error_of_an_earlier_run(self):
		import_name = self._import("Review", with_document=True)
		path = Path(tempfile.mkdtemp()) / "handbook.pdf"
		_make_sample_pdf(str(path))
		pdf = save_file("handbook.pdf", path.read_bytes(), "Wikify Import", import_name, is_private=1)
		frappe.db.set_value("Wikify Import", import_name, "pdf", pdf.file_url)
		summary = {"targets": 1, "adopted": 1, "canonical_mean": 0.9, "sections": 2, "cost": 0}

		with (
			_without_realtime(remediate_job),
			patch.object(remediate_job, "remediate_pdf", return_value=summary),
		):
			remediate_job.run(import_name, scope="flagged")

		self.assertIsNone(self._read(import_name).error)

	def test_generate_job_clears_the_error_of_an_earlier_run(self):
		import_name = self._import("Graphed", with_document=True)
		result = {"space": "Handbook", "space_route": "handbook", "pages": 2, "groups": 1}
		result.update({"deleted": 0, "links": 0})

		with (
			_without_realtime(generate_job),
			patch.object(generate_job, "generate_wiki", return_value=result),
		):
			generate_job.run(import_name, wiki_space="Handbook")

		row = self._read(import_name)
		self.assertIsNone(row.error)
		self.assertEqual(row.status, "Completed")

	def test_retry_hands_a_failed_import_back_to_review(self):
		import_name = self._import("Failed", with_document=True)

		with _without_realtime(imports_api), patch.object(frappe, "enqueue") as enqueue:
			self.assertEqual(imports_api.retry_import(import_name), import_name)
			enqueue.assert_not_called()

		row = self._read(import_name)
		self.assertEqual(row.status, "Review")
		self.assertIsNone(row.error)

	def test_retry_re_enqueues_the_parse_when_nothing_was_parsed(self):
		import_name = self._import("Failed")

		with _without_realtime(imports_api), patch.object(frappe, "enqueue") as enqueue:
			imports_api.retry_import(import_name)

		self.assertEqual(enqueue.call_args.args[0], "wikify.jobs.parse.run")
		self.assertEqual(enqueue.call_args.kwargs["import_name"], import_name)
		self.assertIsNone(self._read(import_name).error)

	def test_retry_recovers_an_import_whose_worker_was_lost(self):
		import_name = self._import("Remediating", with_document=True)
		self._age(import_name, tasks.STALE_MINUTES + 5)

		with _without_realtime(imports_api), patch.object(frappe, "enqueue"):
			imports_api.retry_import(import_name)

		self.assertEqual(self._read(import_name).status, "Review")

	def test_retry_refuses_a_job_that_is_still_running(self):
		import_name = self._import("Remediating", with_document=True)

		with _without_realtime(imports_api), patch.object(frappe, "enqueue") as enqueue:
			with self.assertRaises(frappe.ValidationError):
				imports_api.retry_import(import_name)
			enqueue.assert_not_called()

		self.assertEqual(self._read(import_name).status, "Remediating")

	def test_retry_refuses_a_completed_import(self):
		import_name = self._import("Completed", error=None, with_document=True)

		with _without_realtime(imports_api), self.assertRaises(frappe.ValidationError):
			imports_api.retry_import(import_name)

	def test_a_running_import_with_no_progress_is_failed(self):
		import_name = self._import("Remediating", error=None, with_document=True)
		self._age(import_name, tasks.STALE_MINUTES + 5)

		with _without_realtime(tasks):
			tasks.fail_stuck_imports()

		row = self._read(import_name)
		self.assertEqual(row.status, "Failed")
		self.assertIn("worker running this import was lost", row.error)

	def test_a_slow_job_still_reporting_progress_is_left_alone(self):
		import_name = self._import("Remediating", error=None, with_document=True)
		self._age(import_name, tasks.STALE_MINUTES - 5)

		with _without_realtime(tasks):
			tasks.fail_stuck_imports()

		self.assertEqual(self._read(import_name).status, "Remediating")

	def test_an_import_at_rest_is_left_alone(self):
		import_name = self._import("Review", error=None, with_document=True)
		self._age(import_name, tasks.STALE_MINUTES * 4)

		with _without_realtime(tasks):
			tasks.fail_stuck_imports()

		self.assertEqual(self._read(import_name).status, "Review")
