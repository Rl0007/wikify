# Copyright (c) 2026, BWH and contributors
# For license information, please see license.txt

import tempfile
from pathlib import Path
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from wikify.engine import parse_pdf, remediate_pdf, store
from wikify.engine.loader.cleanup import clean_pages, strip_outer_markdown_fence
from wikify.engine.loader.sectionizer import Section, sectionize
from wikify.tests.test_parse_pipeline import _make_sample_pdf
from wikify.tests.test_remediate_pipeline import _MERMAID, _fake_chat


class TestStripMarkdownFence(FrappeTestCase):
	"""LLM parsers sometimes wrap the whole reply in a ```markdown fence."""

	def test_strips_outer_markdown_fence(self):
		table = "| a | b |\n|---|---|\n| 1 | 2 |"
		self.assertEqual(strip_outer_markdown_fence(f"```markdown\n{table}\n```"), table)

	def test_drops_commentary_after_the_fence(self):
		# The real page-10 shape: a fenced page body followed by an LLM aside.
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
		# A genuine inner diagram fence (more than one fence) must survive verbatim.
		page = '```mermaid\nflowchart TD\nA["x"] --> B["y"]\n```'
		self.assertEqual(strip_outer_markdown_fence(page), page)

	def test_does_not_strip_when_inner_fence_present(self):
		wrapped = "```markdown\n# Title\n```mermaid\nflowchart TD\n```\n```"
		self.assertEqual(strip_outer_markdown_fence(wrapped), wrapped)


class TestSectionizer(FrappeTestCase):
	"""Pure sectionizer logic — no DB, deterministic (the ported POC behavior)."""

	def test_numbered_headings_nest_by_their_number(self):
		pages = [
			(1, "## 1. Introduction\nbody\n## 1.1 Scope\nscope body"),
			(2, "## 2. Procedures\nproc body"),
		]
		secs = sectionize(pages)
		titles = [(s.title, s.level) for s in secs]
		self.assertEqual(titles, [("1. Introduction", 1), ("1.1 Scope", 2), ("2. Procedures", 1)])
		# Scope's hierarchy path threads through Introduction; page ranges tracked.
		scope = secs[1]
		self.assertEqual(scope.hierarchy_path, ["1. Introduction", "1.1 Scope"])
		self.assertEqual(secs[2].page_start, 2)

	def test_out_of_sequence_numbered_heading_is_demoted(self):
		# "1. Eclampsia" after chapter 2 isn't a new chapter — it's a mis-read list
		# item; it must nest under the current chapter, not restart numbering.
		pages = [(1, "## 1. First\na\n## 2. Second\nb\n## 1. Eclampsia\nc")]
		secs = sectionize(pages)
		levels = {s.title: s.level for s in secs}
		self.assertEqual(levels["1. First"], 1)
		self.assertEqual(levels["2. Second"], 1)
		self.assertEqual(levels["1. Eclampsia"], 2)  # demoted

	def test_repeated_numbered_running_header_is_merged(self):
		# A numbered chapter heading re-emitted as a per-page running title collapses
		# into one section spanning the page range (not one section per page).
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
		# pymupdf4llm emits bold/italic headings (`_**Verbal Orders**_`, `## **2.1 Foo**`);
		# the wrapping markers are stripped, and numbering still drives the level.
		pages = [
			(1, "## _**Verbal Orders**_\nbody"),
			(2, "## **2. Procedures**\np\n## **2.1 Scope**\nq"),
		]
		secs = sectionize(pages)
		by_title = {s.title: s.level for s in secs}
		self.assertIn("Verbal Orders", by_title)
		self.assertEqual(by_title["2. Procedures"], 1)
		self.assertEqual(by_title["2.1 Scope"], 2)  # numbering recovered after strip
		# No emphasis markers leak into any title.
		self.assertFalse(any("*" in s.title or s.title.startswith("_") for s in secs))

	def test_clean_pages_strips_varying_boilerplate_before_sectionizing(self):
		# Running footers ("Pg 2 of 2") must not survive as fake headings.
		pages = [(1, "## 1. Intro\nbody\nPg 1 of 2"), (2, "## 2. Next\nbody\nPg 2 of 2")]
		secs = sectionize(clean_pages(pages))
		self.assertTrue(all("Pg" not in s.markdown for s in secs))
		self.assertEqual([s.title for s in secs], ["1. Intro", "2. Next"])

	def test_clean_pages_strips_signoff_footer_block(self):
		# The QMS sign-off footer (a table row with >=2 sign-off phrases) plus its
		# orphaned |---| separator are page furniture and must be removed.
		footer = "|**Prepared by - Dr. A**|**Issued by: QMC**|**Approved by - Dr. B**|\n|---|---|---|"
		pages = [(1, f"## 1. Intro\nreal body\n{footer}"), (2, f"## 2. Next\nmore body\n{footer}")]
		cleaned = dict(clean_pages(pages))
		for md in cleaned.values():
			self.assertNotIn("Prepared by", md)
			self.assertNotIn("Issued by", md)
			self.assertNotIn("|---|---|---|", md)
		self.assertIn("real body", cleaned[1])
		self.assertIn("more body", cleaned[2])

	def test_clean_pages_keeps_data_row_mentioning_approved_by_once(self):
		# A genuine data row that merely mentions one sign-off phrase is NOT furniture.
		table = "| Step | Status |\n|---|---|\n| Reviewed and approved by committee | done |"
		pages = [(1, f"## 1. Audit\n{table}")]
		cleaned = dict(clean_pages(pages))
		self.assertIn("approved by committee", cleaned[1])
		self.assertIn("|---|---|", cleaned[1])  # the real table separator survives


class TestTitleFit(FrappeTestCase):
	def test_overlong_section_title_is_stored_truncated(self):
		long_title = "A. " + "Responsibilities of the transplant coordinator " * 8
		self.assertGreater(len(long_title), store.TITLE_MAX)
		sd = store.create_document("Title Fit Test")
		section = Section(
			title=long_title, level=1, hierarchy_path=[long_title], page_start=1, page_end=1, markdown="body"
		)
		store.replace_sections(sd, [section])

		stored = frappe.get_all(
			"Source Section", filters={"source_document": sd}, fields=["title", "markdown"]
		)
		self.assertEqual(len(stored), 1)
		self.assertEqual(stored[0].title, long_title[: store.TITLE_MAX])
		self.assertEqual(stored[0].markdown, "body")


class TestSectionizeIntegration(FrappeTestCase):
	"""parse/remediate build the Source Section NestedSet tree."""

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
		# A real top-level section exists and levels/paths are persisted.
		roots = [s for s in secs if not s.parent_source_section]
		self.assertTrue(roots)
		for s in secs:
			self.assertGreaterEqual(s.level, 1)
			self.assertTrue(s.hierarchy_path)
			# NestedSet bounds are well-formed.
			self.assertLess(s.lft, s.rgt)
		# The numbered chapters from the fixture surface as sections.
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
			# Cleanup keeps content (identity) so the rebuilt tree can't go empty.
			patch("wikify.engine.remediate.clean_markdown", side_effect=lambda md, model=None: md),
			patch("wikify.engine.remediate.vlm.parse_page_image", return_value=_MERMAID),
		):
			result = remediate_pdf(sd, str(path), scope="all")

		after = self._sections(sd)
		# Tree was rebuilt (fresh rows) and did NOT revert to empty/baseline-less.
		self.assertTrue(after, "remediation left an empty tree")
		self.assertEqual(result["sections"], len(after))
		self.assertFalse(before & {s.name for s in after}, "old section rows not replaced")
		self.assertTrue(any("Procedures" in s.title for s in after))
