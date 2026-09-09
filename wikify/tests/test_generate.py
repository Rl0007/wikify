import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils.nestedset import get_descendants_of

from wikify.engine import generate_wiki, preview_wiki, store
from wikify.engine.loader.sectionizer import Section
from wikify.engine.loader.wiki import rewrite_page_refs, slugify
from wikify.tests import _cleanup


def _sec(title, level, path, p_start, p_end, markdown=None):
	return Section(
		title=title,
		level=level,
		hierarchy_path=path,
		page_start=p_start,
		page_end=p_end,
		markdown=markdown if markdown is not None else f"body of {title}",
	)


class TestWikiGenerate(FrappeTestCase):
	def setUp(self):
		self._spaces: set[str] = set()
		self.sd = frappe.get_doc({"doctype": "Source Document", "title": "Gen Test", "page_count": 5}).insert(
			ignore_permissions=True
		)
		store.replace_sections(
			self.sd.name,
			[
				_sec("1. Intro", 1, ["1. Intro"], 1, 3, "overview"),
				_sec("1.1 Purpose", 2, ["1. Intro", "1.1 Purpose"], 1, 1),
				_sec(
					"1.2 Scope",
					2,
					["1. Intro", "1.2 Scope"],
					2,
					2,
					"In scope. See page 5 for details. Williams p820 is external.",
				),
				_sec("2. Appendix", 1, ["2. Appendix"], 4, 5, "appendix body"),
			],
		)
		self._space_route = "gen-test-" + frappe.generate_hash(length=8)

	def tearDown(self):
		for space in self._spaces:
			root = frappe.db.get_value("Wiki Space", space, "root_group")
			if root:
				names = frappe.get_all(
					"Wiki Document",
					filters={
						"name": [
							"in",
							[root, *get_descendants_of("Wiki Document", root, ignore_permissions=True)],
						]
					},
					order_by="lft desc",
					pluck="name",
				)
				for n in names:
					frappe.delete_doc("Wiki Document", n, ignore_permissions=True, force=True)
			frappe.delete_doc("Wiki Space", space, ignore_permissions=True, force=True)
		_cleanup._delete_document_rows(self.sd.name)
		# nosemgrep
		frappe.db.commit()

	def _generate(self, **kwargs):
		if "wiki_space" not in kwargs and "new_space" not in kwargs:
			kwargs["new_space"] = {"space_name": "Gen Test Wiki", "route": self._space_route}
		res = generate_wiki(self.sd.name, **kwargs)
		self._spaces.add(res["space"])
		return res

	def _docs_under(self, root_group):
		names = [root_group, *get_descendants_of("Wiki Document", root_group, ignore_permissions=True)]
		return {
			r.name: r
			for r in frappe.get_all(
				"Wiki Document",
				filters={"name": ["in", names]},
				fields=[
					"name",
					"title",
					"is_group",
					"route",
					"content",
					"parent_wiki_document",
					"sort_order",
					"is_published",
				],
			)
		}

	def _by_title(self, root_group):
		return {r.title: r for r in self._docs_under(root_group).values()}

	def test_structure_mirrors_tree(self):
		res = self._generate()
		self.assertEqual(res["pages"], 3)
		self.assertEqual(res["groups"], 1)

		docs = self._by_title(res["root_group"])
		root = docs["Gen Test"]
		self.assertEqual(root.is_group, 1)
		self.assertEqual(root.route, f"{self._space_route}/{slugify('Gen Test')}")

		intro = docs["1. Intro"]
		self.assertEqual(intro.is_group, 1)
		self.assertEqual(intro.parent_wiki_document, root.name)

		purpose, scope = docs["1.1 Purpose"], docs["1.2 Scope"]
		self.assertEqual(purpose.is_group, 0)
		self.assertEqual(purpose.parent_wiki_document, intro.name)
		self.assertLess(purpose.sort_order, scope.sort_order)
		self.assertEqual(scope.route, f"{intro.route}/{slugify('1.2 Scope')}")
		self.assertEqual(docs["2. Appendix"].parent_wiki_document, root.name)
		self.assertTrue(all(d.is_published for d in docs.values()))

	def test_back_links_and_space_persisted(self):
		res = self._generate()
		secs = {
			s.title: s.wiki_document
			for s in frappe.get_all(
				"Source Section",
				filters={"source_document": self.sd.name},
				fields=["title", "wiki_document"],
			)
		}
		self.assertTrue(all(secs.values()))
		sd = frappe.db.get_value(
			"Source Document", self.sd.name, ["wiki_space", "wiki_root_group", "status"], as_dict=True
		)
		self.assertEqual(sd.wiki_space, res["space"])
		self.assertEqual(sd.wiki_root_group, res["root_group"])
		self.assertEqual(sd.status, "Wiki-Generated")

	def test_internal_page_ref_becomes_link_external_stays_text(self):
		res = self._generate()
		self.assertEqual(res["links"], 1)
		docs = self._by_title(res["root_group"])
		scope_content = docs["1.2 Scope"].content
		appendix_route = docs["2. Appendix"].route
		self.assertIn(f"](/{appendix_route})", scope_content)
		self.assertIn("Williams p820", scope_content)
		self.assertNotIn("](/", docs["2. Appendix"].content or "")

	def test_rewrite_helper_pure(self):
		md = "See page 3 and p.99 and Williams p820."
		out, n = rewrite_page_refs(md, page_count=10, route_for_page=lambda x: f"r/{x}")
		self.assertEqual(n, 1)
		self.assertIn("](/r/3)", out)
		self.assertIn("Williams p820", out)

	def test_regenerate_updates_in_place(self):
		res1 = self._generate()
		first_purpose = frappe.db.get_value(
			"Source Section", {"title": "1.1 Purpose", "source_document": self.sd.name}, "wiki_document"
		)
		count1 = len(self._docs_under(res1["root_group"]))

		res2 = generate_wiki(self.sd.name, wiki_space=res1["space"])
		self.assertEqual(res1["root_group"], res2["root_group"])
		again_purpose = frappe.db.get_value(
			"Source Section", {"title": "1.1 Purpose", "source_document": self.sd.name}, "wiki_document"
		)
		self.assertEqual(first_purpose, again_purpose)
		self.assertEqual(len(self._docs_under(res2["root_group"])), count1)

	def test_regenerate_drops_excluded_section(self):
		res1 = self._generate()
		appendix = frappe.db.get_value(
			"Source Section", {"title": "2. Appendix", "source_document": self.sd.name}, "name"
		)
		appendix_wiki = frappe.db.get_value("Source Section", appendix, "wiki_document")
		self.assertTrue(frappe.db.exists("Wiki Document", appendix_wiki))

		frappe.db.set_value("Source Section", appendix, "include_in_wiki", 0)
		res2 = generate_wiki(self.sd.name, wiki_space=res1["space"])
		self.assertFalse(frappe.db.exists("Wiki Document", appendix_wiki))
		self.assertIn(res2["deleted"], (1,))
		self.assertIsNone(frappe.db.get_value("Source Section", appendix, "wiki_document"))
		self.assertNotIn("2. Appendix", self._by_title(res1["root_group"]))

	def test_regenerate_after_rename_updates_route(self):
		res1 = self._generate()
		purpose = frappe.db.get_value(
			"Source Section", {"title": "1.1 Purpose", "source_document": self.sd.name}, "name"
		)
		frappe.db.set_value("Source Section", purpose, "title", "1.1 Goals")
		res2 = generate_wiki(self.sd.name, wiki_space=res1["space"])
		docs = self._by_title(res2["root_group"])
		self.assertIn("1.1 Goals", docs)
		self.assertTrue(docs["1.1 Goals"].route.endswith(slugify("1.1 Goals")))

	def test_preview_projects_included_tree(self):
		pv = preview_wiki(self.sd.name)
		self.assertEqual(pv["pages"], 3)
		self.assertEqual(pv["groups"], 1)
		self.assertEqual(pv["excluded"], 0)
		titles = [n["title"] for n in pv["tree"]]
		self.assertEqual(titles, ["1. Intro", "2. Appendix"])
		intro = pv["tree"][0]
		self.assertEqual(len(intro["children"]), 2)
