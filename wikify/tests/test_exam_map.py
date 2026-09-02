# Copyright (c) 2026, BWH and contributors
# For license information, please see license.txt

"""Question → corpus topic mapping (`wikify.exam.map`).

The statutory leg is tested as a pure text matcher against a hand-written corpus, because
the invariant it carries is a literal one: a provision either appears in the text or it does
not, and the whole point of the rewrite was to stop a tokeniser answering that question.
Topic rollup and `map_question` run against real `Source Section` rows so the nested-set
bounds rollup depends on are the real ones, with a real (local, free) LanceDB index behind
retrieval — redirected to a temp directory the way `test_rag_core` does.

The load-bearing assertions:
  - a fabricated provision matches NOTHING. `section 9999Z` and `section ZZZZ99` scored three
    confident hits apiece under the FTS leg this replaced, because the tokeniser matched the
    word "section" and ignored the number — which made every question look covered and made
    a genuine gap impossible to surface.
  - a bare number never matches; the citation word is the anchor.
  - `topic_of` climbs to the highest ancestor that is actually a chapter, and falls back to
    the section itself rather than filing a question under a one-line fragment.
  - `coverage_verdict` decides on the literal test, and keeps `unknown` distinct from `gap`.
"""

import shutil
import tempfile
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from wikify.engine import store as engine_store
from wikify.engine.loader.sectionizer import Section
from wikify.exam import map as exam_map
from wikify.rag import index, store

# A miniature corpus: three sections, each citing provisions the way the ICAI material does.
CORPUS_MARKDOWN = [
	(
		"sec-capital-gains",
		"Long-term capital gain is exempt under section 54F where the whole of the net "
		"consideration is invested. Section 54F is subject to the lock-in in section 54F(2), "
		"and section 54F does not apply where the assessee owns more than one house.",
	),
	(
		"sec-rates",
		"The concessional rates in section 115BAC apply unless the assessee opts out. "
		"The limit of Rs. 16 lakh is discussed on page 16 of the study material.",
	),
	("sec-preface", "This chapter has been revised for A.Y. 2025-26. No provisions are cited here."),
]


def make_section(title, hierarchy_path, level, markdown="Body text for the section."):
	return Section(
		title=title,
		level=level,
		hierarchy_path=hierarchy_path,
		page_start=1,
		page_end=1,
		markdown=markdown,
		section_type=None,
	)


class TestStatutoryMatching(FrappeTestCase):
	"""`provision_pattern` / `statutory_hits` / `statutory_refs` — pure text, no DB."""

	def test_a_fabricated_provision_matches_nothing(self):
		"""The regression: `section 9999Z` used to score three confident hits."""
		refs = exam_map.statutory_refs({"statutory_refs": "section 9999Z"})
		self.assertEqual(refs, ["9999Z"])
		self.assertEqual(exam_map.statutory_hits(refs, CORPUS_MARKDOWN), [])

	def test_a_provision_that_is_not_even_a_section_number_is_never_extracted(self):
		"""`section ZZZZ99` has no leading digit, so it is not a provision reference at all."""
		refs = exam_map.statutory_refs({"statutory_refs": "section ZZZZ99"})
		self.assertEqual(refs, [])
		self.assertEqual(exam_map.statutory_hits(refs, CORPUS_MARKDOWN), [])

	def test_a_question_citing_only_fake_provisions_reads_as_a_gap_not_as_covered(self):
		question = {"statutory_refs": "section 9999Z, section ZZZZ99"}
		refs = exam_map.statutory_refs(question)
		hits = exam_map.statutory_hits(refs, CORPUS_MARKDOWN)

		self.assertEqual(hits, [])
		verdict = exam_map.coverage_verdict({"statutory_matched": bool(hits)}, refs)
		self.assertEqual(verdict, "gap")

	def test_a_real_provision_matches_only_the_section_that_cites_it(self):
		hits = exam_map.statutory_hits(["115BAC"], CORPUS_MARKDOWN)

		self.assertEqual([section for section, _score in hits], ["sec-rates"])

	def test_a_bare_number_cannot_match_a_page_number_or_a_rupee_figure(self):
		"""Without the citation anchor, the reference "16" matches half the corpus."""
		self.assertEqual(exam_map.statutory_hits(["16"], CORPUS_MARKDOWN), [])
		# ...and the anchored form still finds it when the corpus genuinely cites it.
		self.assertEqual(
			[section for section, _score in exam_map.statutory_hits(["16"], [("sec-x", "See section 16.")])],
			["sec-x"],
		)

	def test_the_citation_anchor_accepts_the_forms_icai_actually_prints(self):
		for text in ("section 54F", "Section 54F", "sec. 54F", "u/s 54F", "section [54F"):
			with self.subTest(text=text):
				self.assertTrue(exam_map.provision_pattern("54F").search(text), text)

	def test_repeated_citation_outranks_a_passing_mention_but_with_a_ceiling(self):
		corpus = [
			("sec-passing", "Relief is available under section 54F in some cases."),
			("sec-about-it", "Section 54F applies. section 54F(2) locks in. See section 54F again."),
		]
		hits = dict(exam_map.statutory_hits(["54F"], corpus))

		self.assertGreater(hits["sec-about-it"], hits["sec-passing"])
		# The occurrence ceiling is min(3, n)/3, so a section citing a provision three times
		# scores the same as one citing it twenty. Asserted as a RATIO rather than an absolute
		# score: the absolute value also carries the inverse-document-frequency weight for the
		# provision, and pinning that number made this test fail the moment IDF was introduced
		# even though the behaviour it is named for was still correct.
		self.assertAlmostEqual(hits["sec-about-it"] / hits["sec-passing"], 3.0)

	def test_a_provision_the_corpus_mentions_everywhere_is_weighted_down(self):
		"""Inverse document frequency: ubiquity is not evidence.

		A provision in every section says nothing about which chapter a question belongs to,
		while one in a single section is close to a pointer. Before this, 115BAC (25 sections)
		outscored 91 (3 sections) purely by being common.
		"""
		common = [(f"sec-{index}", "section 99 applies here.") for index in range(10)]
		rare = [("sec-rare", "section 77 applies here.")] + [
			(f"sec-other-{index}", "nothing relevant") for index in range(9)
		]

		common_score = dict(exam_map.statutory_hits(["99"], common))["sec-0"]
		rare_score = dict(exam_map.statutory_hits(["77"], rare))["sec-rare"]
		self.assertGreater(rare_score, common_score)

	def test_refs_are_normalised_and_deduplicated(self):
		refs = exam_map.statutory_refs({"statutory_refs": "section 115bac, u/s 115BAC, sec. 45(1A)"})
		self.assertEqual(refs, ["115BAC", "45(1A)"])

	def test_a_question_citing_nothing_is_unknown_rather_than_a_gap(self):
		"""Absence of a citation is not evidence of absence — most MCQs land here."""
		self.assertEqual(exam_map.coverage_verdict({"statutory_matched": False}, []), "unknown")
		self.assertEqual(exam_map.coverage_verdict({"statutory_matched": False}, ["45"]), "gap")
		self.assertEqual(exam_map.coverage_verdict({"statutory_matched": True}, ["45"]), "covered")


class TestTopicRollup(FrappeTestCase):
	"""`topic_of` against a real nested-set tree."""

	def setUp(self):
		suffix = frappe.generate_hash(length=8)
		self.project = frappe.get_doc(
			{"doctype": "Wikify Project", "project_name": f"Exam Map Rollup {suffix}"}
		).insert(ignore_permissions=True)
		self.document = frappe.get_doc(
			{"doctype": "Source Document", "title": "Direct Tax Handbook", "project": self.project.name}
		).insert(ignore_permissions=True)

		# A real chapter, a thin fragment the sectioniser promoted to level 1, and the two
		# sides of the MIN_TOPIC_SUBTREE boundary.
		engine_store.replace_sections(
			self.document.name,
			[
				make_section("CAPITAL GAINS", ["CAPITAL GAINS"], 1),
				make_section("Section 45", ["CAPITAL GAINS", "Section 45"], 2),
				make_section("Section 45(1A)", ["CAPITAL GAINS", "Section 45", "Section 45(1A)"], 3),
				make_section("Section 54", ["CAPITAL GAINS", "Section 54"], 2),
				make_section("Section 54F", ["CAPITAL GAINS", "Section 54F"], 2),
				make_section("AMT liability not attracted", ["AMT liability not attracted"], 1),
				make_section("Stray note", ["AMT liability not attracted", "Stray note"], 2),
				make_section("THIN CHAPTER", ["THIN CHAPTER"], 1),
				make_section("Thin one", ["THIN CHAPTER", "Thin one"], 2),
				make_section("Thin two", ["THIN CHAPTER", "Thin two"], 2),
				make_section("JUST BIG ENOUGH", ["JUST BIG ENOUGH"], 1),
				make_section("Big one", ["JUST BIG ENOUGH", "Big one"], 2),
				make_section("Big two", ["JUST BIG ENOUGH", "Big two"], 2),
				make_section("Big three", ["JUST BIG ENOUGH", "Big three"], 2),
			],
		)
		self.index = exam_map.section_index(self.project.name)

	def section(self, title: str) -> str:
		return frappe.db.get_value(
			"Source Section", {"source_document": self.document.name, "title": title}, "name"
		)

	def test_subtree_size_counts_descendants_from_the_nested_set_bounds(self):
		self.assertEqual(exam_map.subtree_size(self.index[self.section("CAPITAL GAINS")]), 4)
		self.assertEqual(exam_map.subtree_size(self.index[self.section("Section 45")]), 1)
		self.assertEqual(exam_map.subtree_size(self.index[self.section("Section 54")]), 0)

	def test_a_deep_section_rolls_up_to_its_chapter(self):
		topic = exam_map.topic_of(self.section("Section 45(1A)"), self.index)

		self.assertEqual(topic["section"], self.section("CAPITAL GAINS"))
		self.assertEqual(topic["title"], "CAPITAL GAINS")

	def test_siblings_across_the_chapter_share_one_heatmap_row(self):
		"""If they did not, one chapter would appear as several rows each holding part of it."""
		topics = {
			exam_map.topic_of(self.section(title), self.index)["section"]
			for title in ("Section 45", "Section 45(1A)", "Section 54", "Section 54F")
		}
		self.assertEqual(topics, {self.section("CAPITAL GAINS")})

	def test_a_section_under_a_one_line_fragment_falls_back_to_itself(self):
		"""Rolling up blindly files questions under `Preface`, `INDEX` and OCR noise."""
		topic = exam_map.topic_of(self.section("Stray note"), self.index)

		self.assertEqual(topic["section"], self.section("Stray note"))
		self.assertNotEqual(topic["section"], self.section("AMT liability not attracted"))

	def test_the_subtree_threshold_is_the_line_between_a_chapter_and_a_fragment(self):
		self.assertEqual(exam_map.MIN_TOPIC_SUBTREE, 3)
		# Two descendants: not a chapter, so the child stays its own topic.
		self.assertEqual(
			exam_map.topic_of(self.section("Thin one"), self.index)["section"],
			self.section("Thin one"),
		)
		# Three descendants: a chapter, so the child rolls up into it.
		self.assertEqual(
			exam_map.topic_of(self.section("Big one"), self.index)["section"],
			self.section("JUST BIG ENOUGH"),
		)

	def test_an_unknown_section_maps_to_no_topic_at_all(self):
		self.assertIsNone(exam_map.topic_of("does-not-exist", self.index))

	def test_the_section_index_is_scoped_to_the_project(self):
		other = frappe.get_doc(
			{
				"doctype": "Wikify Project",
				"project_name": f"Exam Map Other {frappe.generate_hash(length=8)}",
			}
		).insert(ignore_permissions=True)
		self.assertEqual(exam_map.section_index(other.name), {})


class TestMapQuestion(FrappeTestCase):
	"""`map_question` end to end: both legs, real rows, real (local) index."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.lance_dir = tempfile.mkdtemp(prefix="wikify_exam_map_lance_")
		cls.lance_patch = patch.object(store, "lance_path", lambda: cls.lance_dir)
		cls.lance_patch.start()

	@classmethod
	def tearDownClass(cls):
		cls.lance_patch.stop()
		shutil.rmtree(cls.lance_dir, ignore_errors=True)
		super().tearDownClass()

	def setUp(self):
		# LanceDB writes sit outside the test transaction, so the table is dropped per test.
		database = store.connect()
		if store.TABLE_NAME in database.table_names():
			database.drop_table(store.TABLE_NAME)

		suffix = frappe.generate_hash(length=8)
		self.project = frappe.get_doc(
			{"doctype": "Wikify Project", "project_name": f"Exam Map Question {suffix}"}
		).insert(ignore_permissions=True)
		self.document = frappe.get_doc(
			{"doctype": "Source Document", "title": "Direct Tax Handbook", "project": self.project.name}
		).insert(ignore_permissions=True)
		engine_store.replace_sections(
			self.document.name,
			[
				make_section("CAPITAL GAINS", ["CAPITAL GAINS"], 1, "Chapter on capital gains."),
				make_section(
					"Exemption under section 54F",
					["CAPITAL GAINS", "Exemption under section 54F"],
					2,
					"Exemption under section 54F is available where the net consideration is "
					"invested in a residential house. Section 54F is withdrawn on transfer within "
					"three years, as section 54F(2) provides.",
				),
				make_section(
					"Cost of acquisition",
					["CAPITAL GAINS", "Cost of acquisition"],
					2,
					"The cost of acquisition of a capital asset acquired before 2001 may be "
					"substituted by its fair market value on 1st April 2001.",
				),
				make_section(
					"Indexation",
					["CAPITAL GAINS", "Indexation"],
					2,
					"The cost inflation index is notified each year and applies to long-term assets.",
				),
			],
		)
		index.rebuild_project(self.project.name)

		self.paper = frappe.get_doc(
			{
				"doctype": "Wikify Exam Paper",
				"paper_title": f"November 2023 — DT {suffix}",
				"project": self.project.name,
				"exam_month": "November",
				"exam_year": 2023,
			}
		).insert(ignore_permissions=True)
		self.index = exam_map.section_index(self.project.name)
		self.markdown = exam_map.markdown_index(self.project.name)

	def add_question(self, question_no, text, refs, marks=6.0):
		document = frappe.get_doc(
			{
				"doctype": "Wikify Exam Question",
				"exam_paper": self.paper.name,
				"question_no": question_no,
				"question_text": text,
				"statutory_refs": refs,
				"marks": marks,
				"is_compulsory": 1,
			}
		).insert(ignore_permissions=True)
		return {
			"name": document.name,
			"question_text": text,
			"statutory_refs": refs,
			"marks": marks,
		}

	def test_a_question_whose_cited_provision_lives_in_the_corpus_is_covered_by_evidence(self):
		question = self.add_question(
			"1",
			"Compute the exemption available on the sale of a plot of land, and state the "
			"conditions the assessee must satisfy.",
			"section 54F",
		)

		outcome = exam_map.map_question(question, self.project.name, self.index, self.markdown)

		self.assertEqual(outcome["verdict"], "covered")
		self.assertTrue(outcome["evidence"])
		self.assertGreater(outcome["links"], 0)

		document = frappe.get_doc("Wikify Exam Question", question["name"])
		self.assertEqual(document.mapping_status, "Mapped")
		self.assertIsNotNone(document.mapped_at)
		self.assertIn("statutory", [row.method for row in document.topics])
		# Ranks are dense and start at 1, so the UI can order them without re-sorting.
		self.assertEqual([row.rank for row in document.topics], list(range(1, len(document.topics) + 1)))
		# Every link rolled up to a topic, not to the leaf section it matched.
		chapter = frappe.db.get_value(
			"Source Section", {"source_document": self.document.name, "title": "CAPITAL GAINS"}, "name"
		)
		self.assertEqual({row.topic_section for row in document.topics}, {chapter})

	def test_a_question_citing_a_provision_the_corpus_lacks_is_a_gap_with_no_evidence(self):
		"""The similarity leg still returns something — the verdict must not believe it."""
		question = self.add_question(
			"2",
			"Discuss the treatment of a capital asset transferred by an assessee.",
			"section 9999Z",
		)

		outcome = exam_map.map_question(question, self.project.name, self.index, self.markdown)

		self.assertEqual(outcome["verdict"], "gap")
		self.assertFalse(outcome["evidence"])
		document = frappe.get_doc("Wikify Exam Question", question["name"])
		self.assertNotIn("statutory", [row.method for row in document.topics])

	def test_a_question_with_nothing_to_match_on_is_stored_as_no_match(self):
		question = self.add_question("3", "", "")

		outcome = exam_map.map_question(question, self.project.name, self.index, self.markdown)

		self.assertEqual(outcome["links"], 0)
		self.assertEqual(outcome["verdict"], "unknown")
		self.assertEqual(
			frappe.db.get_value("Wikify Exam Question", question["name"], "mapping_status"),
			"No Match",
		)

	def test_re_mapping_replaces_the_links_rather_than_appending_to_them(self):
		question = self.add_question("4", "Exemption under section 54F on sale of a plot.", "section 54F")

		first = exam_map.map_question(question, self.project.name, self.index, self.markdown)
		second = exam_map.map_question(question, self.project.name, self.index, self.markdown)

		self.assertEqual(first["links"], second["links"])
		self.assertEqual(
			frappe.db.count("Question Topic Link", {"parent": question["name"]}),
			second["links"],
		)

	def test_keep_caps_the_number_of_links_stored(self):
		question = self.add_question("5", "Capital gains on the transfer of a residential house.", "")

		outcome = exam_map.map_question(question, self.project.name, self.index, self.markdown, keep=1)

		self.assertLessEqual(outcome["links"], 1)

	def test_mapping_a_project_with_no_sections_is_refused_rather_than_reported_as_zero(self):
		empty = frappe.get_doc(
			{
				"doctype": "Wikify Project",
				"project_name": f"Exam Map Empty {frappe.generate_hash(length=8)}",
			}
		).insert(ignore_permissions=True)

		with self.assertRaises(frappe.ValidationError):
			exam_map.map_project(empty.name)

	def test_map_project_reports_evidence_coverage_apart_from_bare_coverage(self):
		"""`coverage` is ~1.0 by construction; only `evidence_coverage` means anything."""
		self.add_question(
			"1", "Exemption on sale of a plot of land under the capital gains chapter.", "section 54F"
		)
		self.add_question("2", "Discuss the treatment of a transferred capital asset.", "section 9999Z")

		report = exam_map.map_project(self.project.name)

		self.assertEqual(report["questions"], 2)
		self.assertEqual(report["evidence_backed"], 1)
		self.assertEqual(report["evidence_coverage"], 0.5)
		self.assertEqual(report["verdicts"], {"covered": 1, "gap": 1, "unknown": 0})
		self.assertGreaterEqual(report["coverage"], report["evidence_coverage"])
