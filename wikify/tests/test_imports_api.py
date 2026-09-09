# Copyright (c) 2026, BWH and contributors
# For license information, please see license.txt

"""Batch import entry point — `start_imports` creates one Wikify Import per uploaded PDF
and enqueues a parse job for each, in order, on a single project.

`frappe.enqueue` is patched throughout: `run-tests` has no queue redis, and we only care
that the jobs were handed off with the right arguments.
"""

import json
from unittest.mock import patch

import fitz
import frappe
from frappe.tests.utils import FrappeTestCase

from wikify.api import imports as imports_api
from wikify.seed import seed_uncategorized_project


def make_file(file_name: str) -> str:
	"""A real File row (and a real file on disk), returning its url.

	`start_imports` refuses a url with no readable File behind it, so a fixture url has to
	be one that actually exists rather than a plausible-looking string.
	"""
	document = fitz.open()
	document.new_page().insert_text((72, 90), file_name, fontsize=12)
	uploaded = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": f"{frappe.generate_hash(length=6)}-{file_name}",
			"content": document.tobytes(),
			"is_private": 1,
		}
	).insert(ignore_permissions=True)
	return uploaded.file_url


def _files(n: int) -> list[dict]:
	return [{"file_url": make_file(f"doc{i}.pdf"), "title": f"Doc {i}"} for i in range(n)]


class TestImportsApi(FrappeTestCase):
	def setUp(self):
		# FrappeTestCase rolls back per class, not per test, and `project_name` is unique —
		# so each test needs its own project.
		self.project = frappe.get_doc(
			{
				"doctype": "Wikify Project",
				"project_name": f"Batch Upload Test {frappe.generate_hash(length=6)}",
			}
		).insert(ignore_permissions=True)

	def test_batch_creates_one_import_per_file_in_order(self):
		files = _files(3)
		with patch.object(frappe, "enqueue") as enqueue:
			names = imports_api.start_imports(files, project=self.project.name)

		self.assertEqual(len(names), 3)
		for i, name in enumerate(names):
			imp = frappe.get_doc("Wikify Import", name)
			self.assertEqual(imp.import_title, f"Doc {i}")
			self.assertEqual(imp.pdf, files[i]["file_url"])
			self.assertEqual(imp.project, self.project.name)
			self.assertEqual(imp.status, "Queued")

		self.assertEqual(enqueue.call_count, 3)
		for i, call in enumerate(enqueue.call_args_list):
			self.assertEqual(call.args[0], "wikify.jobs.parse.run")
			self.assertEqual(call.kwargs["queue"], "long")
			self.assertEqual(call.kwargs["import_name"], names[i])

	def test_import_count_tracks_the_batch(self):
		with patch.object(frappe, "enqueue"):
			imports_api.start_imports(_files(3), project=self.project.name)
		count = frappe.db.get_value("Wikify Project", self.project.name, "import_count")
		self.assertEqual(count, 3)

	def test_defaults_to_the_uncategorized_project(self):
		default = seed_uncategorized_project()
		with patch.object(frappe, "enqueue"):
			names = imports_api.start_imports(_files(2))
		for name in names:
			self.assertEqual(frappe.db.get_value("Wikify Import", name, "project"), default)

	def test_json_string_payload_parses(self):
		"""The v2 API can hand `files` over as a JSON string."""
		with patch.object(frappe, "enqueue"):
			names = imports_api.start_imports(json.dumps(_files(2)), project=self.project.name)
		self.assertEqual(len(names), 2)

	def test_blank_title_falls_back_to_the_filename(self):
		file_url = make_file("handbook.pdf")
		with patch.object(frappe, "enqueue"):
			names = imports_api.start_imports(
				[{"file_url": file_url, "title": ""}],
				project=self.project.name,
			)
		expected = file_url.rsplit("/", 1)[-1].removesuffix(".pdf")
		self.assertEqual(frappe.db.get_value("Wikify Import", names[0], "import_title"), expected)

	def test_empty_batch_is_rejected(self):
		with patch.object(frappe, "enqueue"), self.assertRaises(frappe.ValidationError):
			imports_api.start_imports([], project=self.project.name)

	def test_oversized_batch_is_rejected(self):
		files = _files(imports_api.MAX_BATCH + 1)
		with patch.object(frappe, "enqueue") as enqueue:
			with self.assertRaises(frappe.ValidationError):
				imports_api.start_imports(files, project=self.project.name)
			# Rejected up front — nothing was created.
			enqueue.assert_not_called()
		self.assertEqual(
			frappe.db.count("Wikify Import", {"project": self.project.name}),
			0,
		)

	def test_single_import_still_works(self):
		with patch.object(frappe, "enqueue") as enqueue:
			name = imports_api.start_import(make_file("one.pdf"), "One", project=self.project.name)
		self.assertIsInstance(name, str)
		self.assertEqual(frappe.db.get_value("Wikify Import", name, "import_title"), "One")
		self.assertEqual(enqueue.call_count, 1)

	def test_file_without_a_url_is_rejected(self):
		with patch.object(frappe, "enqueue") as enqueue:
			with self.assertRaises(frappe.ValidationError):
				imports_api.start_imports(
					[{"file_url": make_file("ok.pdf"), "title": "Ok"}, {"title": "No URL"}],
					project=self.project.name,
				)
			enqueue.assert_not_called()
		self.assertEqual(frappe.db.count("Wikify Import", {"project": self.project.name}), 0)

	def test_a_url_with_no_file_behind_it_is_rejected(self):
		"""The url is written to the Import and a worker parses whatever it points at."""
		with patch.object(frappe, "enqueue") as enqueue:
			with self.assertRaises(frappe.PermissionError):
				imports_api.start_imports(
					[{"file_url": "/private/files/never-uploaded.pdf", "title": "Theirs"}],
					project=self.project.name,
				)
			enqueue.assert_not_called()
		self.assertEqual(frappe.db.count("Wikify Import", {"project": self.project.name}), 0)

	def test_one_unreadable_file_rejects_the_whole_batch(self):
		files = [*_files(2), {"file_url": "/private/files/never-uploaded.pdf", "title": "Theirs"}]
		with patch.object(frappe, "enqueue") as enqueue:
			with self.assertRaises(frappe.PermissionError):
				imports_api.start_imports(files, project=self.project.name)
			enqueue.assert_not_called()
		self.assertEqual(frappe.db.count("Wikify Import", {"project": self.project.name}), 0)

	def test_another_users_private_file_is_not_importable(self):
		"""The finding this guard exists for: naming someone else's attachment by url."""
		file_url = make_file("private-to-admin.pdf")
		outsider = frappe.get_doc(
			{
				"doctype": "User",
				"email": f"outsider-{frappe.generate_hash(length=6)}@example.com",
				"first_name": "Outsider",
			}
		).insert(ignore_permissions=True)

		frappe.set_user(outsider.name)
		try:
			with self.assertRaises(frappe.PermissionError):
				imports_api.assert_readable_file(file_url)
		finally:
			frappe.set_user("Administrator")
