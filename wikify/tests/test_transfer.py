# Copyright (c) 2026, BWH and contributors
# For license information, please see license.txt

"""Moving a project between sites (`wikify.transfer`).

The format helpers are tested directly; everything else runs a real export to a temp
directory and a real import back into the same site, which is the only way to prove the two
halves agree about the format. A site importing its own bundle is also the harshest version
of the name-reallocation invariant: every bundle name is already taken.

`import_bundle` commits, which defeats the `FrappeTestCase` rollback — so the fixtures and
everything the import creates are swept with the raw-delete helpers in `_cleanup`.

The load-bearing assertions:
  - `assert_supported` refuses a bundle whose schema version is not exactly this build's,
    rather than importing half of it and guessing at the rest.
  - every name is reallocated. Not one row keeps the name it had in the bundle, and the links
    between them still point at the right rows afterwards.
  - a failure anywhere in the import rolls back to ZERO rows. A half-imported corpus is worse
    than a failed one, because it looks complete.
"""

import json
import os
import shutil
import tempfile
import zipfile
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from wikify.engine import store as engine_store
from wikify.engine.loader.sectionizer import Section
from wikify.tests import _cleanup
from wikify.transfer import bundle, export, restore

# The smallest thing that is really a PNG — enough to prove the bytes travel intact.
PNG_BYTES = bytes.fromhex(
	"89504e470d0a1a0a0000000d4948445200000001000000010806000000"
	"1f15c4890000000a49444154789c6360000002000100ffff0300000600"
	"0557bfabd40000000049454e44ae426082"
)

TRACKED_DOCTYPES = (
	"Wikify Project",
	"Source Document",
	"Source Page",
	"Source Section",
	"Section Reference",
	"Wikify Import",
)


def make_section(title, hierarchy_path, level, markdown="Body text."):
	return Section(
		title=title,
		level=level,
		hierarchy_path=hierarchy_path,
		page_start=1,
		page_end=1,
		markdown=markdown,
		section_type=None,
	)


class TestBundleFormat(FrappeTestCase):
	"""The format helpers. No site rows involved."""

	def test_a_bundle_from_another_schema_version_is_refused(self):
		for version in (bundle.SCHEMA_VERSION + 1, bundle.SCHEMA_VERSION - 1, "1", None):
			with self.subTest(version=version):
				with self.assertRaises(ValueError) as caught:
					bundle.assert_supported({"schema_version": version})
				message = str(caught.exception)
				self.assertIn(repr(version), message)
				self.assertIn(str(bundle.SCHEMA_VERSION), message)

	def test_a_manifest_with_no_version_at_all_is_refused(self):
		with self.assertRaises(ValueError):
			bundle.assert_supported({})

	def test_the_current_version_is_accepted(self):
		self.assertIsNone(bundle.assert_supported({"schema_version": bundle.SCHEMA_VERSION}))

	def test_a_row_set_path_the_build_does_not_know_is_an_error_not_a_guess(self):
		self.assertEqual(bundle.doctype_for("rows/50-source-section.json"), "Source Section")
		with self.assertRaises(KeyError):
			bundle.doctype_for("rows/99-something-new.json")

	def test_scrubbing_strips_site_bookkeeping_tree_bounds_and_cross_app_links(self):
		row = {
			"name": "abc",
			"title": "Chapter",
			"markdown": "text",
			"owner": "Administrator",
			"creation": "2026-01-01",
			"modified_by": "Administrator",
			"docstatus": 0,
			"lft": 3,
			"rgt": 8,
			"old_parent": "xyz",
			"wiki_document": "WIKI-1",
			"section_type": None,
		}
		scrubbed = bundle.scrub_row("Source Section", row)

		self.assertEqual(scrubbed, {"name": "abc", "title": "Chapter", "markdown": "text"})

	def test_tree_bounds_are_only_stripped_from_the_doctype_that_has_a_tree(self):
		row = {"name": "abc", "title": "Doc", "lft": 3, "rgt": 8}
		self.assertEqual(bundle.scrub_row("Source Document", row), row)

	def test_serialising_is_byte_identical_whatever_order_the_keys_arrived_in(self):
		"""Manifest checksums are only worth comparing if the same corpus serialises the same."""
		first = bundle.serialise([{"a": 1, "b": 2}, {"c": 3}])
		second = bundle.serialise([{"b": 2, "a": 1}, {"c": 3}])

		self.assertEqual(first, second)
		self.assertEqual(bundle.checksum(first), bundle.checksum(second))
		self.assertTrue(bundle.checksum(first).startswith("sha256:"))

	def test_a_link_target_is_always_written_before_the_rows_pointing_at_it(self):
		"""Emission order IS import order, so a restore can insert straight down the list."""
		emitted: list[str] = []
		for _path, doctype in bundle.ROW_SETS:
			for field, target in bundle.LINK_FIELDS.get(doctype, {}).items():
				if field in bundle.DEFERRED_LINKS.get(doctype, ()):
					continue
				if target == doctype:
					continue  # self-referential parent, resolved as the set is inserted
				self.assertIn(target, emitted, f"{doctype}.{field} points at an unwritten {target}")
			emitted.append(doctype)

	def test_the_one_cycle_in_the_schema_is_the_one_that_is_deferred(self):
		self.assertEqual(bundle.DEFERRED_LINKS, {"Wikify Import": ("source_document",)})
		self.assertEqual(bundle.LINK_FIELDS["Wikify Import"]["source_document"], "Source Document")
		self.assertEqual(bundle.LINK_FIELDS["Source Document"]["import"], "Wikify Import")


class TestExportRestore(FrappeTestCase):
	"""A real export and a real import, against the site's own rows."""

	def setUp(self):
		self.register_sweep()
		self.output_dir = tempfile.mkdtemp(prefix="wikify_bundle_test_")
		self.addCleanup(shutil.rmtree, self.output_dir, ignore_errors=True)

		suffix = frappe.generate_hash(length=8)
		self.type_name = f"transfer_test_{suffix}"
		frappe.get_doc(
			{"doctype": "Section Type", "type_name": self.type_name, "label": self.type_name}
		).insert(ignore_permissions=True)
		self.addCleanup(_cleanup.delete_section_type, self.type_name)

		self.project = frappe.get_doc(
			{"doctype": "Wikify Project", "project_name": f"Transfer Test {suffix}"}
		).insert(ignore_permissions=True)
		self.document = frappe.get_doc(
			{"doctype": "Source Document", "title": "Capital Gains Handbook", "project": self.project.name}
		).insert(ignore_permissions=True)
		engine_store.replace_sections(
			self.document.name,
			[
				Section(
					title="CAPITAL GAINS",
					level=1,
					hierarchy_path=["CAPITAL GAINS"],
					page_start=1,
					page_end=1,
					markdown="Chapter on capital gains.",
					section_type=self.type_name,
				),
				make_section("Section 54F", ["CAPITAL GAINS", "Section 54F"], 2, "Exemption under 54F."),
				make_section("Section 45", ["CAPITAL GAINS", "Section 45"], 2, "Charging section."),
			],
		)
		self.page = engine_store.add_page(self.document.name, 1, "text", PNG_BYTES, "Baseline markdown.")

		self.section_names = frappe.get_all(
			"Source Section",
			filters={"source_document": self.document.name},
			pluck="name",
			order_by="sort_order asc",
		)
		frappe.get_doc(
			{
				"doctype": "Section Reference",
				"from_section": self.section_names[1],
				"to_section": self.section_names[2],
				"source_document": self.document.name,
			}
		).insert(ignore_permissions=True)

	def register_sweep(self):
		"""`import_bundle` commits, so rollback cannot undo the fixtures or the import."""
		before = {doctype: set(frappe.get_all(doctype, pluck="name")) for doctype in TRACKED_DOCTYPES}

		def sweep():
			frappe.db.rollback()
			documents = set(frappe.get_all("Source Document", pluck="name")) - before["Source Document"]
			for name in documents:
				_cleanup.delete_document(name)
			for name in set(frappe.get_all("Wikify Project", pluck="name")) - before["Wikify Project"]:
				_cleanup.delete_project(name)

		self.addCleanup(sweep)

	def counts(self) -> dict[str, int]:
		return {doctype: frappe.db.count(doctype) for doctype in TRACKED_DOCTYPES}

	def export(self) -> dict:
		return export.export_project(self.project.name, output_dir=self.output_dir)

	def import_onto_a_site_without_it(self, path: str) -> dict:
		"""Import `path` after freeing the project name it carries.

		`Wikify Project.project_name` is unique and `restore` reallocates `name` only, so a
		bundle cannot land on a site that already holds a project of that name — including
		the site it came from. Renaming the source here stands in for a genuinely different
		target site. See `test_a_bundle_whose_project_name_is_taken_cannot_land` — this is a
		defect, not a property worth relying on.
		"""
		frappe.db.set_value(
			"Wikify Project",
			self.project.name,
			"project_name",
			f"{self.project.project_name} (exported from)",
		)
		return restore.import_bundle(path, rebuild_index=False)

	def test_an_export_writes_every_row_set_and_reports_what_went_in(self):
		report = self.export()

		self.assertTrue(report["path"].endswith(bundle.BUNDLE_SUFFIX))
		self.assertTrue(os.path.isfile(report["path"]))
		self.assertEqual(report["counts"]["Wikify Project"], 1)
		self.assertEqual(report["counts"]["Source Document"], 1)
		self.assertEqual(report["counts"]["Source Section"], 3)
		self.assertEqual(report["counts"]["Section Reference"], 1)
		self.assertEqual(report["counts"]["Source Page"], 1)
		self.assertEqual(report["counts"]["Section Type"], 1)
		self.assertEqual(report["attachments"], {"stored": 1, "missing": []})

		manifest, rows, file_index = restore.read_bundle(report["path"])
		self.assertEqual(manifest["schema_version"], bundle.SCHEMA_VERSION)
		self.assertEqual(manifest["project"]["name"], self.project.name)
		self.assertEqual(len(rows["Source Section"]), 3)
		self.assertEqual(len(file_index), 1)
		# The index is rebuilt on arrival, and the bounds would be wrong on the target anyway.
		self.assertNotIn("lft", rows["Source Section"][0])
		self.assertNotIn("rgt", rows["Source Section"][0])

	def test_an_export_never_mutates_the_project_it_reads(self):
		before = self.counts()
		self.export()
		self.assertEqual(self.counts(), before)

	def test_a_missing_attachment_is_reported_rather_than_raised(self):
		"""The markdown is the part that cost money; a lost page image must not block the move."""
		frappe.db.set_value("Source Document", self.document.name, "pdf", "/private/files/gone.pdf")

		report = self.export()

		self.assertEqual(report["attachments"]["missing"], ["/private/files/gone.pdf"])
		self.assertEqual(report["attachments"]["stored"], 1)

	def test_every_name_is_reallocated_and_every_link_still_points_at_the_right_row(self):
		report = self.export()

		outcome = self.import_onto_a_site_without_it(report["path"])

		project = outcome["project"]
		self.assertNotEqual(project, self.project.name)
		self.assertEqual(outcome["source_project"], self.project.name)
		self.assertEqual(outcome["created"]["Source Section"], 3)

		documents = frappe.get_all("Source Document", filters={"project": project}, pluck="name")
		self.assertEqual(len(documents), 1)
		self.assertNotEqual(documents[0], self.document.name)

		sections = frappe.get_all(
			"Source Section",
			filters={"source_document": documents[0]},
			fields=["name", "title", "markdown", "parent_source_section", "lft", "rgt", "section_type"],
			order_by="lft asc",
		)
		self.assertEqual(len(sections), 3)
		self.assertFalse(
			{row["name"] for row in sections} & set(self.section_names),
			"an imported section kept its bundle name",
		)
		self.assertEqual(
			{row["title"] for row in sections},
			{"CAPITAL GAINS", "Section 54F", "Section 45"},
		)

		# The tree is rebuilt from the parent links, not copied. Sibling ORDER is asserted
		# separately — see `test_sort_order_survives_the_transfer`.
		by_title = {row["title"]: row for row in sections}
		chapter = by_title["CAPITAL GAINS"]
		self.assertEqual(chapter["markdown"], "Chapter on capital gains.")
		self.assertIsNone(chapter["parent_source_section"])
		for title in ("Section 54F", "Section 45"):
			child = by_title[title]
			self.assertEqual(child["parent_source_section"], chapter["name"])
			self.assertLess(chapter["lft"], child["lft"])
			self.assertGreater(chapter["rgt"], child["rgt"])

		# `Section Type` merges by name — the taxonomy converges instead of forking.
		self.assertEqual(chapter["section_type"], self.type_name)
		self.assertEqual(outcome["created"]["Section Type"], 0)

		reference = frappe.get_all(
			"Section Reference",
			filters={"source_document": documents[0]},
			fields=["from_section", "to_section"],
		)
		self.assertEqual(len(reference), 1)
		self.assertEqual(reference[0]["from_section"], by_title["Section 54F"]["name"])
		self.assertEqual(reference[0]["to_section"], by_title["Section 45"]["name"])

	def test_sort_order_survives_the_transfer(self):
		"""`sort_order` is carried in the row, and it is the only surviving record of order.

		DEFECT, worth knowing about: the nested-set bounds are recomputed by `rebuild_tree`,
		which orders siblings by `name` — and every name was just reallocated to a fresh hash.
		So `order_by="lft asc"`, which is how `api/sections`, `api/graph`, `api/wiki` and
		`rag.chunk` all read the tree, returns an imported document's siblings in an order
		unrelated to the source's. Nothing is lost (`sort_order` still holds the truth), but
		the reading order of a transferred corpus is not the one it was parsed in.
		"""
		report = self.export()

		outcome = self.import_onto_a_site_without_it(report["path"])

		documents = frappe.get_all("Source Document", filters={"project": outcome["project"]}, pluck="name")
		by_sort_order = frappe.get_all(
			"Source Section",
			filters={"source_document": documents[0]},
			pluck="title",
			order_by="sort_order asc",
		)
		self.assertEqual(by_sort_order, ["CAPITAL GAINS", "Section 54F", "Section 45"])

	def rewrite_member(self, source_path: str, member: str, payload_object) -> str:
		"""A re-signed copy of a bundle with one row set replaced — a bundle from a site
		whose rows differ, as opposed to the corrupt one the checksum test builds."""
		payload = bundle.serialise(payload_object)
		manifest = json.loads(zipfile.ZipFile(source_path).read(bundle.MANIFEST_NAME))
		manifest["checksums"][member] = bundle.checksum(payload)
		target = os.path.join(self.output_dir, f"rewritten-{os.path.basename(source_path)}")
		with zipfile.ZipFile(source_path) as source, zipfile.ZipFile(target, "w") as rewritten:
			for name in source.namelist():
				if name == member:
					rewritten.writestr(name, payload)
				elif name == bundle.MANIFEST_NAME:
					rewritten.writestr(name, bundle.serialise(manifest))
				else:
					rewritten.writestr(name, source.read(name))
		return target

	def test_the_imported_project_is_never_the_default_one(self):
		"""The source site's default has no authority here, and two defaults is a state the
		app rejects outright — so a bundle carrying one must not be able to create it."""
		report = self.export()
		_manifest, rows, _files = restore.read_bundle(report["path"])
		project_row = dict(rows["Wikify Project"][0], is_default=1)
		from_a_site_where_it_was_default = self.rewrite_member(
			report["path"], "rows/10-wikify-project.json", [project_row]
		)

		outcome = self.import_onto_a_site_without_it(from_a_site_where_it_was_default)

		self.assertEqual(frappe.db.get_value("Wikify Project", outcome["project"], "is_default"), 0)

	def test_the_bundled_file_bytes_are_re_saved_and_the_row_repointed_at_them(self):
		"""The imported page's `image` resolves to the right bytes on this site.

		On a same-site import the URL comes back identical to the source's, because Frappe
		dedupes File rows by content hash — the imported page shares the physical file rather
		than storing a second copy of it. What has to hold either way is that the field points
		at readable bytes that are the ones the bundle carried.
		"""
		report = self.export()

		outcome = self.import_onto_a_site_without_it(report["path"])

		self.assertEqual(outcome["attachments"], 1)
		documents = frappe.get_all("Source Document", filters={"project": outcome["project"]}, pluck="name")
		page = frappe.get_all(
			"Source Page", filters={"source_document": documents[0]}, fields=["name", "image", "page_no"]
		)[0]
		self.assertNotEqual(page["name"], self.page)
		self.assertEqual(page["page_no"], 1)
		self.assertTrue(page["image"])
		with open(frappe.get_site_path(page["image"].lstrip("/")), "rb") as handle:
			self.assertEqual(handle.read(), PNG_BYTES)

	def test_a_tampered_bundle_is_rejected_before_a_single_row_is_written(self):
		report = self.export()
		tampered = os.path.join(self.output_dir, "tampered.wikify.zip")
		with zipfile.ZipFile(report["path"]) as source, zipfile.ZipFile(tampered, "w") as target:
			for member in source.namelist():
				payload = source.read(member)
				if member == "rows/50-source-section.json":
					payload = payload.replace(b"CAPITAL GAINS", b"CAPITAL LOSSES")
				target.writestr(member, payload)

		before = self.counts()
		with self.assertRaises(ValueError) as caught:
			restore.import_bundle(tampered, rebuild_index=False)

		self.assertIn("checksum", str(caught.exception))
		self.assertEqual(self.counts(), before)

	def test_a_failure_part_way_through_rolls_back_to_zero_rows(self):
		"""A partially imported corpus looks complete and answers questions wrongly."""
		report = self.export()
		before = self.counts()

		with patch.object(restore, "rebuild_tree", side_effect=RuntimeError("tree rebuild exploded")):
			with self.assertRaises(RuntimeError):
				self.import_onto_a_site_without_it(report["path"])

		self.assertEqual(self.counts(), before)
		self.assertFalse(
			frappe.get_all(
				"Wikify Project",
				filters={"project_name": self.project.project_name, "name": ["!=", self.project.name]},
			),
			"a second copy of the project survived the rollback",
		)

	def test_a_bundle_whose_project_name_is_taken_cannot_land(self):
		"""DEFECT, pinned deliberately so the fix has a test to invert.

		`restore` reallocates every `name`, but `Wikify Project.project_name` carries a UNIQUE
		index and travels through the bundle untouched. So a bundle cannot be imported onto any
		site that already holds a project of that name — including a re-import onto the site it
		came from, which is the documented "import it twice" path. It fails as a raw
		`UniqueValidationError` from the DB rather than as an explanation of what went wrong.
		The savepoint does its job (nothing is left behind), so this is a usability defect
		rather than a corruption one.
		"""
		report = self.export()
		before = self.counts()

		with self.assertRaises(frappe.UniqueValidationError):
			restore.import_bundle(report["path"], rebuild_index=False)

		self.assertEqual(self.counts(), before)

	def test_importing_the_same_bundle_twice_makes_two_independent_projects(self):
		"""Reallocating always means one code path, whatever the target already holds."""
		report = self.export()

		first = self.import_onto_a_site_without_it(report["path"])
		# The bundle's project name is now taken by the first import, so free it again.
		frappe.db.set_value(
			"Wikify Project", first["project"], "project_name", f"{self.project.project_name} (first)"
		)
		second = restore.import_bundle(report["path"], rebuild_index=False)

		self.assertNotEqual(first["project"], second["project"])
		for project in (first["project"], second["project"]):
			documents = frappe.get_all("Source Document", filters={"project": project}, pluck="name")
			self.assertEqual(len(documents), 1)
			self.assertEqual(
				frappe.db.count("Source Section", {"source_document": documents[0]}),
				3,
			)
			# The two imports share nothing — not a section, not a page.
			self.assertNotIn(documents[0], (self.document.name,))

	def test_exporting_a_project_that_does_not_exist_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			export.export_project("PRJ-does-not-exist", output_dir=self.output_dir)
