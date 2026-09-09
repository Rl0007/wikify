import tempfile
from pathlib import Path
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from wikify.engine import parse_pdf, remediate_pdf
from wikify.engine.loader.cleanup import clean_pages, strip_outer_markdown_fence
from wikify.engine.loader.sectionizer import MAX_TITLE_LENGTH, running_header_titles, sectionize
from wikify.rag.chunk import build_chunks
from wikify.tests.test_parse_pipeline import _make_sample_pdf
from wikify.tests.test_remediate_pipeline import _MERMAID, _fake_chat


class TestStripMarkdownFence(FrappeTestCase):
	def test_strips_outer_markdown_fence(self):
		table = "| a | b |\n|---|---|\n| 1 | 2 |"
		self.assertEqual(strip_outer_markdown_fence(f"```markdown\n{table}\n```"), table)

	def test_drops_commentary_after_the_fence(self):
		table = "| a | b |\n|---|---|\n| 1 | 2 |"
		wrapped = f"```markdown\n{table}\n```\n\nThe table is part of the manual."
		self.assertEqual(strip_outer_markdown_fence(wrapped), table)

	def test_strips_untagged_and_md_fences(self):
		self.assertEqual(strip_outer_markdown_fence("```\n# Title\n```"), "# Title")
		self.assertEqual(strip_outer_markdown_fence("```md\n# Title\n```"), "# Title")

	def test_leaves_plain_markdown_untouched(self):
		md = "# Title\n\n| a | b |\n|---|---|"
		self.assertEqual(strip_outer_markdown_fence(md), md)

	def test_leaves_mermaid_block_untouched(self):
		page = '```mermaid\nflowchart TD\nA["x"] --> B["y"]\n```'
		self.assertEqual(strip_outer_markdown_fence(page), page)

	def test_does_not_strip_when_inner_fence_present(self):
		wrapped = "```markdown\n# Title\n```mermaid\nflowchart TD\n```\n```"
		self.assertEqual(strip_outer_markdown_fence(wrapped), wrapped)


class TestSectionizer(FrappeTestCase):
	def test_numbered_headings_nest_by_their_number(self):
		pages = [
			(1, "## 1. Introduction\nbody\n## 1.1 Scope\nscope body"),
			(2, "## 2. Procedures\nproc body"),
		]
		secs = sectionize(pages)
		titles = [(s.title, s.level) for s in secs]
		self.assertEqual(titles, [("1. Introduction", 1), ("1.1 Scope", 2), ("2. Procedures", 1)])
		scope = secs[1]
		self.assertEqual(scope.hierarchy_path, ["1. Introduction", "1.1 Scope"])
		self.assertEqual(secs[2].page_start, 2)

	def test_out_of_sequence_numbered_heading_is_demoted(self):
		pages = [(1, "## 1. First\na\n## 2. Second\nb\n## 1. Eclampsia\nc")]
		secs = sectionize(pages)
		levels = {s.title: s.level for s in secs}
		self.assertEqual(levels["1. First"], 1)
		self.assertEqual(levels["2. Second"], 1)
		self.assertEqual(levels["1. Eclampsia"], 2)

	def test_repeated_numbered_running_header_is_merged(self):
		pages = [
			(1, "## 3. Surgery\nintro"),
			(2, "## 3. Surgery\nmore on page two"),
			(3, "## 3. Surgery\nstill more"),
		]
		secs = sectionize(pages)
		self.assertEqual(len(secs), 1)
		self.assertEqual(secs[0].page_start, 1)
		self.assertEqual(secs[0].page_end, 3)
		self.assertIn("more on page two", secs[0].markdown)

	def test_content_before_any_heading_becomes_preamble(self):
		secs = sectionize([(1, "just some text\nno headings here")])
		self.assertEqual(len(secs), 1)
		self.assertEqual(secs[0].title, "Preamble")
		self.assertEqual(secs[0].level, 1)

	def test_emphasis_is_stripped_from_heading_titles(self):
		pages = [
			(1, "## _**Verbal Orders**_\nbody"),
			(2, "## **2. Procedures**\np\n## **2.1 Scope**\nq"),
		]
		secs = sectionize(pages)
		by_title = {s.title: s.level for s in secs}
		self.assertIn("Verbal Orders", by_title)
		self.assertEqual(by_title["2. Procedures"], 1)
		self.assertEqual(by_title["2.1 Scope"], 2)
		self.assertFalse(any("*" in s.title or s.title.startswith("_") for s in secs))

	def test_clean_pages_strips_varying_boilerplate_before_sectionizing(self):
		pages = [(1, "## 1. Intro\nbody\nPg 1 of 2"), (2, "## 2. Next\nbody\nPg 2 of 2")]
		secs = sectionize(clean_pages(pages))
		self.assertTrue(all("Pg" not in s.markdown for s in secs))
		self.assertEqual([s.title for s in secs], ["1. Intro", "2. Next"])

	def test_clean_pages_strips_signoff_footer_block(self):
		footer = "|**Prepared by - Dr. A**|**Issued by: QMC**|**Approved by - Dr. B**|\n|---|---|---|"
		pages = [(1, f"## 1. Intro\nreal body\n{footer}"), (2, f"## 2. Next\nmore body\n{footer}")]
		cleaned = dict(clean_pages(pages))
		for md in cleaned.values():
			self.assertNotIn("Prepared by", md)
			self.assertNotIn("Issued by", md)
			self.assertNotIn("|---|---|---|", md)
		self.assertIn("real body", cleaned[1])
		self.assertIn("more body", cleaned[2])

	def test_title_is_clipped_to_the_storable_length(self):
		long_title = "DETERMINATION OF RESIDENTIAL STATUS OF " + "HINDU UNDIVIDED FAMILY " * 8
		secs = sectionize([(1, f"## {long_title}\nbody")])
		self.assertLessEqual(len(secs[0].title), MAX_TITLE_LENGTH)
		self.assertTrue(secs[0].title.endswith("…"))
		self.assertTrue(secs[0].title.startswith("DETERMINATION OF RESIDENTIAL STATUS"))
		self.assertNotIn(" …", secs[0].title)

	def test_clipped_title_fits_the_data_column(self):
		self.assertEqual(MAX_TITLE_LENGTH, frappe.db.VARCHAR_LEN)

	def test_long_titled_sections_all_reach_the_database(self):
		pages = [
			(1, "## Chapter One\nfirst"),
			(2, f"## {'X' * 200}\nsecond"),
			(3, "## Chapter Three\nthird"),
		]
		secs = sectionize(pages)
		self.assertEqual(len(secs), 3)
		self.assertEqual(secs[-1].title, "Chapter Three")
		self.assertEqual(max(s.page_end for s in secs), 3)

	def test_running_page_header_opens_one_section_not_one_per_page(self):
		pages = [
			(1, "# TRANSFER PRICING\n## Arm's Length Price\nalp body"),
			(2, "# TRANSFER PRICING\nmore alp body"),
			(3, "# TRANSFER PRICING\nstill more alp body"),
		]
		secs = sectionize(pages)
		self.assertEqual([s.title for s in secs], ["TRANSFER PRICING", "Arm's Length Price"])
		alp = secs[1]
		self.assertEqual(alp.page_end, 3)
		self.assertIn("still more alp body", alp.markdown)
		self.assertNotIn("TRANSFER PRICING", alp.markdown)

	def test_heading_repeated_mid_page_is_not_a_running_header(self):
		pages = [
			(page, f"## Chapter {page}\nlead in\nfiller\nfiller\n## Notes\nnote body") for page in (1, 2, 3)
		]
		self.assertNotIn("Notes", running_header_titles(pages))
		self.assertEqual(len([s for s in sectionize(pages) if s.title == "Notes"]), 3)

	def test_heading_on_two_pages_is_not_a_running_header(self):
		pages = [(1, "# Intro\na"), (2, "# Intro\nb")]
		self.assertEqual(running_header_titles(pages), set())

	def test_clean_pages_keeps_data_row_mentioning_approved_by_once(self):
		table = "| Step | Status |\n|---|---|\n| Reviewed and approved by committee | done |"
		pages = [(1, f"## 1. Audit\n{table}")]
		cleaned = dict(clean_pages(pages))
		self.assertIn("approved by committee", cleaned[1])
		self.assertIn("|---|---|", cleaned[1])


class TestEmptySectionsAreFlagged(FrappeTestCase):
	def test_section_without_markdown_is_chunked_as_title_only(self):
		rows = [
			{
				"name": "SEC-EMPTY",
				"source_document": "SD-1",
				"title": "Concessional tax rates under section 115BAC(1A)",
				"hierarchy_path": "Basic Concepts",
				"markdown": "",
				"page_start": 8,
				"page_end": 8,
			},
			{
				"name": "SEC-BODY",
				"source_document": "SD-1",
				"title": "Conditions",
				"hierarchy_path": "Basic Concepts > Conditions",
				"markdown": "The conditions are as follows.",
				"page_start": 8,
				"page_end": 8,
			},
		]
		chunks = build_chunks(rows, {"SD-1": {"title": "Referencer", "project": "PRJ-1"}}, {})
		self.assertEqual([chunk.section for chunk in chunks], ["SEC-EMPTY", "SEC-BODY"])
		self.assertEqual([chunk.title_only for chunk in chunks], [True, False])
		self.assertEqual(chunks[0].text, "Concessional tax rates under section 115BAC(1A)")


class TestSectionizeIntegration(FrappeTestCase):
	def _sections(self, sd):
		return frappe.get_all(
			"Source Section",
			filters={"source_document": sd},
			fields=[
				"name",
				"title",
				"level",
				"parent_source_section",
				"hierarchy_path",
				"is_group",
				"lft",
				"rgt",
				"page_start",
				"page_end",
			],
			order_by="lft asc",
		)

	def test_parse_builds_section_tree(self):
		path = Path(tempfile.mkdtemp()) / "sample.pdf"
		_make_sample_pdf(str(path))
		sd = parse_pdf(str(path), title="Sectionize Test")

		secs = self._sections(sd)
		self.assertTrue(secs, "parse produced no sections")
		roots = [s for s in secs if not s.parent_source_section]
		self.assertTrue(roots)
		for s in secs:
			self.assertGreaterEqual(s.level, 1)
			self.assertTrue(s.hierarchy_path)
			self.assertLess(s.lft, s.rgt)
		titles = " ".join(s.title for s in secs)
		self.assertIn("Procedures", titles)

	def test_remediation_rebuilds_tree_over_adopted_markdown(self):
		path = Path(tempfile.mkdtemp()) / "sample.pdf"
		_make_sample_pdf(str(path))
		with (
			patch("wikify.engine.llm.has_openrouter", return_value=True),
			patch("wikify.engine.llm.chat_completion", side_effect=_fake_chat),
		):
			sd = parse_pdf(str(path), title="Sectionize Remediate Test")

		before = {s.name for s in self._sections(sd)}
		self.assertTrue(before)

		with (
			patch("wikify.engine.llm.has_openrouter", return_value=True),
			patch("wikify.engine.llm.chat_completion", side_effect=_fake_chat),
			patch("wikify.engine.remediate.clean_markdown", side_effect=lambda md, model=None: md),
			patch("wikify.engine.remediate.vlm.parse_page_image", return_value=_MERMAID),
		):
			result = remediate_pdf(sd, str(path), scope="all")

		after = self._sections(sd)
		self.assertTrue(after, "remediation left an empty tree")
		self.assertEqual(result["sections"], len(after))
		self.assertFalse(before & {s.name for s in after}, "old section rows not replaced")
		self.assertTrue(any("Procedures" in s.title for s in after))
