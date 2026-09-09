from unittest.mock import patch

import frappe
import frappe.model.document
from frappe.tests.utils import FrappeTestCase

from wikify.agent.context import Ctx
from wikify.agent.registry import build_default_registry
from wikify.agent.tools import tree as tt
from wikify.api import sections as api
from wikify.engine import store
from wikify.engine.loader.sectionizer import Section


def _sec(title, level, path, p_start, p_end, markdown=None):
	return Section(
		title=title,
		level=level,
		hierarchy_path=path,
		page_start=p_start,
		page_end=p_end,
		markdown=markdown if markdown is not None else f"body of {title}",
	)


class TestSectionSurgery(FrappeTestCase):
	def setUp(self):
		self.sd = frappe.get_doc({"doctype": "Source Document", "title": "Surgery Test"}).insert(
			ignore_permissions=True
		)
		store.replace_sections(
			self.sd.name,
			[
				_sec("1. Alpha", 1, ["1. Alpha"], 1, 2, "alpha intro\n\n## Part Two\n\npart two body"),
				_sec("1.1 Child", 2, ["1. Alpha", "1.1 Child"], 1, 1),
				_sec("2. Beta", 1, ["2. Beta"], 3, 3, "beta body"),
				_sec("3. Gamma", 1, ["3. Gamma"], 4, 4, "gamma body"),
			],
		)
		self.ctx = Ctx(session="test", user="Administrator", source_document=self.sd.name)

	def _rows(self):
		return {
			r.title: r
			for r in frappe.get_all(
				"Source Section",
				filters={"source_document": self.sd.name},
				fields=[
					"name",
					"title",
					"markdown",
					"lft",
					"rgt",
					"level",
					"parent_source_section",
					"is_group",
					"hierarchy_path",
					"sort_order",
					"page_start",
					"page_end",
					"include_in_wiki",
				],
			)
		}

	def _name(self, title):
		return self._rows()[title].name

	def _assert_tree_invariants(self):
		rows = list(self._rows().values())
		bounds = sorted((r.lft, r.rgt) for r in rows)
		seen = set()
		for left, right in bounds:
			self.assertLess(left, right)
			for b in (left, right):
				self.assertNotIn(b, seen)
				seen.add(b)
		by_name = {r.name: r for r in rows}
		for r in rows:
			p = by_name.get(r.parent_source_section)
			if p:
				self.assertTrue(p.lft < r.lft and r.rgt < p.rgt)
				self.assertEqual(r.level, p.level + 1)
				self.assertTrue(r.hierarchy_path.startswith(p.hierarchy_path + " > "))

	def test_create_section_at_top_and_under_parent(self):
		api.create_section(self.sd.name, "4. Delta", markdown="delta body")
		rows = self._rows()
		self.assertIn("4. Delta", rows)
		self.assertIsNone(rows["4. Delta"].parent_source_section)
		self.assertEqual(rows["4. Delta"].include_in_wiki, 1)

		api.create_section(self.sd.name, "1.2 New", parent=self._name("1. Alpha"), index=0)
		rows = self._rows()
		self.assertEqual(rows["1.2 New"].parent_source_section, rows["1. Alpha"].name)
		self.assertEqual(rows["1.2 New"].level, 2)
		self.assertLess(rows["1.2 New"].lft, rows["1.1 Child"].lft)
		self._assert_tree_invariants()

	def test_create_section_validates(self):
		with self.assertRaises(frappe.ValidationError):
			api.create_section(self.sd.name, "  ")
		other = frappe.get_doc({"doctype": "Source Document", "title": "Other"}).insert(
			ignore_permissions=True
		)
		with self.assertRaises(frappe.ValidationError):
			api.create_section(other.name, "X", parent=self._name("1. Alpha"))

	def test_split_at_missing_heading_fails_loudly(self):
		before = self._rows()["1. Alpha"].markdown
		with self.assertRaises(frappe.ValidationError) as cm:
			api.split_section(self._name("1. Alpha"), "No Such Heading")
		self.assertIn("No Such Heading", str(cm.exception))
		self.assertEqual(self._rows()["1. Alpha"].markdown, before)

	def test_split_creates_sibling_after_original(self):
		res = api.split_section(self._name("1. Alpha"), "Part Two")
		rows = self._rows()
		self.assertEqual(res["new_title"], "Part Two")
		self.assertEqual(rows["1. Alpha"].markdown, "alpha intro")
		self.assertTrue(rows["Part Two"].markdown.startswith("## Part Two"))
		self.assertIn("part two body", rows["Part Two"].markdown)
		self.assertEqual(rows["Part Two"].parent_source_section, rows["1. Alpha"].parent_source_section)
		self.assertLess(rows["1. Alpha"].rgt, rows["Part Two"].lft)
		self.assertLess(rows["Part Two"].rgt, rows["2. Beta"].lft)
		self.assertEqual(
			(rows["Part Two"].page_start, rows["Part Two"].page_end),
			(rows["1. Alpha"].page_start, rows["1. Alpha"].page_end),
		)
		self._assert_tree_invariants()

	def test_split_accepts_heading_with_hashes(self):
		res = api.split_section(self._name("1. Alpha"), "## Part Two", new_title="Custom")
		self.assertEqual(res["new_title"], "Custom")

	def test_merge_rejects_non_siblings(self):
		with self.assertRaises(frappe.ValidationError):
			api.merge_sections([self._name("1.1 Child"), self._name("2. Beta")])
		with self.assertRaises(frappe.ValidationError):
			api.merge_sections([self._name("2. Beta")])
		with self.assertRaises(frappe.ValidationError):
			api.merge_sections([self._name("2. Beta"), "nonexistent"])

	def test_merge_concats_reparents_and_deletes_husks(self):
		beta, gamma, child = self._name("2. Beta"), self._name("3. Gamma"), self._name("1.1 Child")
		api.reorder_section(child, new_parent=gamma, new_index=0, siblings=[child])
		res = api.merge_sections([beta, gamma])
		self.assertEqual(res["name"], beta)
		rows = self._rows()
		self.assertNotIn("3. Gamma", rows)
		self.assertEqual(rows["2. Beta"].markdown, "beta body\n\ngamma body")
		self.assertEqual(rows["1.1 Child"].parent_source_section, beta)
		self.assertEqual((rows["2. Beta"].page_start, rows["2. Beta"].page_end), (3, 4))
		self.assertEqual(rows["2. Beta"].is_group, 1)
		self._assert_tree_invariants()

	def test_delete_tool_is_confirm_gated(self):
		reg = build_default_registry()
		self.assertTrue(reg["delete_section"].confirm)
		self.assertTrue(reg["delete_section"].mutates)

	def test_tool_wrappers_report_next_steps(self):
		out = tt._create_section(self.ctx, {"title": "5. Epsilon"})
		self.assertIn("regenerate_wiki", out)
		out = tt._split_section(self.ctx, {"name": self._name("1. Alpha"), "at_heading": "Part Two"})
		self.assertIn("new sibling 'Part Two'", out)
		out = tt._merge_sections(self.ctx, {"names": [self._name("2. Beta"), self._name("3. Gamma")]})
		self.assertIn("Merged 1 section(s) into '2. Beta'", out)
		out = tt._delete_section(self.ctx, {"name": self._name("5. Epsilon")})
		self.assertIn("Deleted '5. Epsilon'", out)


class TestReplaceSectionsIsAtomic(FrappeTestCase):
	def setUp(self):
		self.source_document = frappe.get_doc(
			{"doctype": "Source Document", "title": "Atomic Rebuild"}
		).insert(ignore_permissions=True)
		store.replace_sections(
			self.source_document.name,
			[
				_sec("1. Alpha", 1, ["1. Alpha"], 1, 1),
				_sec("2. Beta", 1, ["2. Beta"], 2, 2),
				_sec("3. Gamma", 1, ["3. Gamma"], 3, 3),
			],
		)

	def titles(self) -> list[str]:
		return [
			row.title
			for row in frappe.get_all(
				"Source Section",
				filters={"source_document": self.source_document.name},
				fields=["title"],
				order_by="lft asc",
			)
		]

	def test_an_insert_that_throws_leaves_the_previous_tree_standing(self):
		replacement = [
			_sec("A. New", 1, ["A. New"], 1, 1),
			_sec("B. New", 1, ["B. New"], 2, 2),
			_sec("C. New", 1, ["C. New"], 3, 3),
		]
		real_insert = frappe.model.document.Document.insert

		def insert_but_fail_on_the_third(self, *args, **kwargs):
			if self.doctype == "Source Section" and self.title == "C. New":
				raise frappe.ValidationError("insert blew up mid-rebuild")
			return real_insert(self, *args, **kwargs)

		with patch.object(frappe.model.document.Document, "insert", insert_but_fail_on_the_third):
			with self.assertRaises(frappe.ValidationError):
				store.replace_sections(self.source_document.name, replacement)

		self.assertEqual(self.titles(), ["1. Alpha", "2. Beta", "3. Gamma"])

	def test_a_successful_rebuild_still_replaces_the_tree(self):
		store.replace_sections(
			self.source_document.name,
			[_sec("A. New", 1, ["A. New"], 1, 1), _sec("B. New", 1, ["B. New"], 2, 2)],
		)
		self.assertEqual(self.titles(), ["A. New", "B. New"])
