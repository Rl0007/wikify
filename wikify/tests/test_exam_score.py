# Copyright (c) 2026, BWH and contributors
# For license information, please see license.txt

"""The topic-by-year matrix (`wikify.exam.score`).

This is where the product's actual claim — "study these topics first" — is computed, so the
fixture is deliberately small enough that every number in the grid can be worked out by hand
and written into the assertion. Real rows, real child tables, rolled back per test; nothing
here needs retrieval or an LLM, because scoring reads the links mapping already stored.

The load-bearing assertions:
  - marks are CONSERVED. A question mapped to three topics must not award each of them its
    full marks — the grid has to sum to the papers' own totals, which is what makes it
    checkable against the printed papers.
  - `topic_key` merges "CAPITAL GAINS" with "Capital Gains" (the same chapter appearing twice
    in the tree used to produce two rows, each holding half the truth) and must NOT fuse two
    different chapters.
  - scoping by `sources` changes the gap set: a question whose evidence lives in a document
    the reader has not got is a gap under that scope and covered under the whole project.
  - optionality is honoured, and recency is a weighting rather than a filter.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from wikify.engine import store as engine_store
from wikify.engine.loader.sectionizer import Section
from wikify.exam import score


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


class TestTopicKey(FrappeTestCase):
	"""Row identity for the heatmap. Case and whitespace only — deliberately not fuzzy."""

	def test_case_and_whitespace_variants_of_one_chapter_merge(self):
		self.assertEqual(score.topic_key("CAPITAL GAINS"), score.topic_key("Capital Gains"))
		self.assertEqual(score.topic_key("  Capital   Gains \n"), score.topic_key("capital gains"))

	def test_two_different_chapters_are_never_fused(self):
		distinct = [
			"Capital Gains",
			"Capital Gain",
			"Capital Gains Exemptions",
			"Income from Capital Gains",
			"Profits and Gains of Business or Profession",
		]
		self.assertEqual(len({score.topic_key(title) for title in distinct}), len(distinct))

	def test_a_missing_title_degrades_to_an_empty_key_rather_than_raising(self):
		self.assertEqual(score.topic_key(None), "")
		self.assertEqual(score.topic_key(""), "")


class TestAttemptableWeight(FrappeTestCase):
	"""The per-question multiplier that keeps the grid on attemptable rather than printed marks."""

	def test_a_compulsory_question_is_worth_all_of_itself(self):
		rows = [{"name": "q1", "exam_paper": "P1", "question_no": "1", "is_compulsory": 1}]
		self.assertEqual(score.attemptable_weight(rows), {"q1": 1.0})

	def test_four_of_five_weights_every_member_of_the_group_at_four_fifths(self):
		rows = [
			{
				"name": f"q{number}",
				"exam_paper": "P1",
				"question_no": str(number),
				"is_compulsory": 0,
				"choice_group": "descriptive-choice",
				"choose_count": 4,
			}
			for number in range(2, 7)
		]
		weights = score.attemptable_weight(rows)

		self.assertEqual(set(weights.values()), {0.8})

	def test_sub_parts_do_not_inflate_the_group_size(self):
		""" "any four of the remaining five" is about questions 2-6, not their (a)/(b) pieces."""
		rows = []
		for number in range(2, 7):
			for part in ("a", "b"):
				rows.append(
					{
						"name": f"q{number}{part}",
						"exam_paper": "P1",
						"question_no": f"{number}({part})",
						"is_compulsory": 0,
						"choice_group": "descriptive-choice",
						"choose_count": 4,
					}
				)
		weights = score.attemptable_weight(rows)

		# Ten rows, but five distinct questions — so 4/5, not 4/10.
		self.assertEqual(set(weights.values()), {0.8})

	def test_a_group_from_another_paper_is_a_different_group(self):
		rows = [
			{
				"name": f"{paper}-{number}",
				"exam_paper": paper,
				"question_no": str(number),
				"is_compulsory": 0,
				"choice_group": "descriptive-choice",
				"choose_count": 4,
			}
			for paper in ("P1", "P2")
			for number in range(2, 7)
		]
		self.assertEqual(set(score.attemptable_weight(rows).values()), {0.8})

	def test_a_weight_can_never_exceed_one(self):
		"""A paper printing "answer any six of five" is nonsense; it must not amplify marks."""
		rows = [
			{
				"name": "q2",
				"exam_paper": "P1",
				"question_no": "2",
				"is_compulsory": 0,
				"choice_group": "descriptive-choice",
				"choose_count": 6,
			}
		]
		self.assertEqual(score.attemptable_weight(rows), {"q2": 1.0})


class TestMatrix(FrappeTestCase):
	"""The grid itself, over a hand-computable fixture."""

	def setUp(self):
		suffix = frappe.generate_hash(length=8)
		self.project = frappe.get_doc(
			{"doctype": "Wikify Project", "project_name": f"Exam Score Test {suffix}"}
		).insert(ignore_permissions=True)
		# Sections are addressed by the key they were created under, never by title: the two
		# capital-gains chapters differ only in case, and MariaDB's default collation would
		# resolve a title lookup to either of them.
		self.sections: dict[str, str] = {}

		# Two source documents so `sources` scoping has something to scope. Document A also
		# carries the same chapter heading twice, in two cases — the shape that used to
		# produce two heatmap rows each holding half the truth.
		self.document_a = self.add_document(
			"Capital Gains Handbook",
			[
				("cg-chapter", make_section("CAPITAL GAINS", ["CAPITAL GAINS"], 1)),
				("cg-54f", make_section("Section 54F", ["CAPITAL GAINS", "Section 54F"], 2)),
				("cg-45", make_section("Section 45", ["CAPITAL GAINS", "Section 45"], 2)),
				("cg-indexation", make_section("Indexation", ["CAPITAL GAINS", "Indexation"], 2)),
				("cg-chapter-again", make_section("Capital Gains", ["Capital Gains"], 1)),
				("cg-cost", make_section("Cost of acquisition", ["Capital Gains", "Cost of acquisition"], 2)),
			],
		)
		self.document_b = self.add_document(
			"Business Income Handbook",
			[
				(
					"pgbp-chapter",
					make_section("PROFITS AND GAINS OF BUSINESS", ["PROFITS AND GAINS OF BUSINESS"], 1),
				),
				("pgbp-37", make_section("Section 37", ["PROFITS AND GAINS OF BUSINESS", "Section 37"], 2)),
				(
					"pgbp-40a",
					make_section("Section 40A", ["PROFITS AND GAINS OF BUSINESS", "Section 40A"], 2),
				),
			],
		)

		self.paper_2024 = self.add_paper("May", 2024, suffix)
		self.paper_2023 = self.add_paper("November", 2023, suffix)

		# 14 marks, one topic, evidence in document A.
		self.question_capital = self.add_question(
			self.paper_2024,
			"1",
			14.0,
			"section 54F",
			[("CAPITAL GAINS", "cg-chapter", "cg-54f", 2.0, "statutory")],
		)
		# 10 marks, one topic, evidence in document B — the row that scoping will strand.
		self.question_business = self.add_question(
			self.paper_2024,
			"2",
			10.0,
			"section 37",
			[("PROFITS AND GAINS OF BUSINESS", "pgbp-chapter", "pgbp-37", 2.0, "statutory")],
		)
		# 6 marks, no provisions cited, TWO topics with unequal scores — the split that has
		# to conserve marks (4.5 / 1.5, never 6 + 6). The second link lands on the chapter
		# whose heading repeats, so the two have to collapse into one row.
		self.question_split = self.add_question(
			self.paper_2023,
			"3",
			6.0,
			"",
			[
				("CAPITAL GAINS", "cg-chapter", "cg-45", 3.0, "retrieval"),
				("Capital Gains", "cg-chapter-again", "cg-cost", 1.0, "retrieval"),
			],
		)

	def add_document(self, title, sections):
		"""`sections` is `[(key, Section)]`; each section's name is recorded under its key."""
		document = frappe.get_doc(
			{"doctype": "Source Document", "title": title, "project": self.project.name}
		).insert(ignore_permissions=True)
		engine_store.replace_sections(document.name, [section for _key, section in sections])
		rows = frappe.get_all(
			"Source Section",
			filters={"source_document": document.name},
			pluck="name",
			order_by="sort_order asc",
		)
		for (key, _section), name in zip(sections, rows, strict=True):
			self.sections[key] = name
		return document.name

	def add_paper(self, month, year, suffix):
		return (
			frappe.get_doc(
				{
					"doctype": "Wikify Exam Paper",
					"paper_title": f"{month} {year} — DT {suffix}",
					"project": self.project.name,
					"exam_month": month,
					"exam_year": year,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

	def add_question(self, paper, question_no, marks, refs, links, **extra):
		"""`links` are `(topic_title, topic_section_key, section_key, score, method)`."""
		document = frappe.get_doc(
			{
				"doctype": "Wikify Exam Question",
				"exam_paper": paper,
				"question_no": question_no,
				"question_text": f"Question {question_no}",
				"statutory_refs": refs,
				"marks": marks,
				"is_compulsory": extra.pop("is_compulsory", 1),
				"mapping_status": "Mapped",
				**extra,
			}
		)
		for rank, (topic_title, topic_key, section_key, link_score, method) in enumerate(links, 1):
			document.append(
				"topics",
				{
					"topic_title": topic_title,
					"topic_section": self.sections[topic_key],
					"section": self.sections[section_key],
					"score": link_score,
					"rank": rank,
					"method": method,
				},
			)
		document.insert(ignore_permissions=True)
		return document.name

	def cells_total(self, grid, field="marks"):
		return round(sum(cell[field] for cell in grid["cells"].values()), 2)

	def test_the_grid_conserves_marks(self):
		"""A question on three topics must not become three times its marks."""
		grid = score.matrix(self.project.name)

		self.assertEqual(grid["totals"]["marks"], 30.0)
		self.assertEqual(self.cells_total(grid), 30.0)
		self.assertEqual(round(sum(topic["marks"] for topic in grid["topics"]), 2), 30.0)

	def test_a_question_on_two_topics_splits_its_marks_by_match_score(self):
		grid = score.matrix(self.project.name)
		by_key = {topic["topic"]: topic for topic in grid["topics"]}

		# Q3's 6 marks split 3:1 across two links that both normalise to `capital gains`, so
		# that row holds Q1's 14 plus all 6 of Q3 — 20, never 14 + 6 + 6.
		self.assertEqual(by_key["capital gains"]["marks"], 20.0)
		self.assertEqual(by_key["capital gains"]["questions"], 2.0)
		self.assertEqual(grid["cells"]["capital gains|2023"]["marks"], 6.0)
		self.assertEqual(by_key["profits and gains of business"]["marks"], 10.0)

	def test_one_chapter_appearing_twice_in_the_tree_is_one_heatmap_row(self):
		grid = score.matrix(self.project.name)
		keys = [topic["topic"] for topic in grid["topics"]]

		self.assertEqual(len(keys), len(set(keys)))
		capital = [topic for topic in grid["topics"] if topic["topic"] == "capital gains"]
		self.assertEqual(len(capital), 1)
		# Both tree positions are kept, so the row can still be traced back to the corpus.
		self.assertEqual(
			set(capital[0]["sections"]),
			{self.sections["cg-chapter"], self.sections["cg-chapter-again"]},
		)
		# ...and the other chapter is emphatically NOT merged into it.
		self.assertIn("profits and gains of business", keys)

	def test_a_cell_is_marks_times_share_times_weight_times_decay(self):
		grid = score.matrix(self.project.name)
		current = grid["reference_year"]
		cell = grid["cells"]["capital gains|2024"]

		self.assertEqual(cell["marks"], 14.0)
		self.assertEqual(cell["attemptable"], 14.0)
		self.assertEqual(cell["score"], round(14.0 * score.DECAY ** (current - 2024), 2))

	def test_recency_is_a_weighting_not_a_filter(self):
		"""A topic last examined years ago should sink, not vanish."""
		grid = score.matrix(self.project.name)

		self.assertEqual(grid["years"], [2023, 2024])
		older = grid["cells"]["capital gains|2023"]
		newer = grid["cells"]["capital gains|2024"]
		self.assertGreater(older["marks"], 0)
		self.assertLess(older["score"] / older["marks"], newer["score"] / newer["marks"])

	def test_optionality_discounts_the_attemptable_column_but_not_the_marks_column(self):
		paper = self.add_paper("May", 2022, frappe.generate_hash(length=6))
		for number in range(2, 7):
			self.add_question(
				paper,
				str(number),
				10.0,
				"",
				[("CAPITAL GAINS", "cg-chapter", "cg-45", 1.0, "retrieval")],
				is_compulsory=0,
				choice_group="descriptive-choice",
				choose_count=4,
			)

		grid = score.matrix(self.project.name)

		self.assertEqual(grid["totals"]["marks"], 80.0)
		# 30 compulsory marks + 50 optional marks at 4/5.
		self.assertEqual(grid["totals"]["attemptable"], 70.0)
		self.assertLess(grid["totals"]["attemptable"], grid["totals"]["marks"])
		self.assertEqual(grid["cells"]["capital gains|2022"]["marks"], 50.0)
		self.assertEqual(grid["cells"]["capital gains|2022"]["attemptable"], 40.0)

	def test_scoping_to_one_source_moves_the_stranded_question_into_the_gaps(self):
		"""The same corpus answers "what does THIS document prepare me for?" differently."""
		whole = score.matrix(self.project.name)
		self.assertEqual(whole["gaps"], [])
		self.assertEqual(whole["totals"]["gap_questions"], 0)

		scoped = score.matrix(self.project.name, sources=[self.document_a])

		self.assertEqual(scoped["sources"], [self.document_a])
		self.assertEqual(scoped["totals"]["gap_questions"], 1)
		self.assertEqual([gap["label"] for gap in scoped["gaps"]], ["section 37"])
		self.assertEqual(scoped["gaps"][0]["marks"], 10.0)
		self.assertEqual(scoped["gaps"][0]["examples"][0]["question"], self.question_business)
		# The business chapter is gone from the grid rather than folded into capital gains.
		self.assertNotIn("profits and gains of business", [topic["topic"] for topic in scoped["topics"]])

	def test_marks_are_still_conserved_once_a_question_becomes_a_gap(self):
		scoped = score.matrix(self.project.name, sources=[self.document_a])

		self.assertEqual(self.cells_total(scoped), 20.0)
		self.assertEqual(scoped["totals"]["uncovered_marks"], 10.0)
		self.assertEqual(
			self.cells_total(scoped) + scoped["totals"]["uncovered_marks"],
			scoped["totals"]["marks"],
		)

	def test_scoping_to_the_other_source_strands_the_other_question(self):
		scoped = score.matrix(self.project.name, sources=[self.document_b])

		self.assertEqual([gap["label"] for gap in scoped["gaps"]], ["section 54F"])
		self.assertEqual([topic["topic"] for topic in scoped["topics"]], ["profits and gains of business"])
		# Q3 cited nothing, so it is neither covered nor a gap — it simply drops out of scope.
		self.assertEqual(scoped["totals"]["gap_questions"], 1)

	def test_a_question_citing_nothing_is_never_reported_as_a_gap(self):
		"""Absence of a citation is not evidence of absence."""
		scoped = score.matrix(self.project.name, sources=[self.document_b])
		examples = [example["question"] for gap in scoped["gaps"] for example in gap["examples"]]

		self.assertNotIn(self.question_split, examples)

	def test_evidence_backed_topics_are_flagged_apart_from_similarity_only_ones(self):
		grid = score.matrix(self.project.name)
		by_key = {topic["topic"]: topic for topic in grid["topics"]}

		self.assertTrue(by_key["profits and gains of business"]["evidence_backed"])
		# The capital gains row carries one statutory link (Q1) and two retrieval links (Q3).
		self.assertTrue(by_key["capital gains"]["evidence_backed"])

	def test_a_project_with_no_mapped_questions_returns_an_empty_grid(self):
		empty = frappe.get_doc(
			{
				"doctype": "Wikify Project",
				"project_name": f"Exam Score Empty {frappe.generate_hash(length=8)}",
			}
		).insert(ignore_permissions=True)

		grid = score.matrix(empty.name)

		self.assertEqual(grid["topics"], [])
		self.assertEqual(grid["cells"], {})

	def test_a_question_with_no_exam_year_is_left_out_of_the_grid(self):
		"""A yearless question has no column to sit in; it must not silently land in year 0."""
		grid = score.matrix(self.project.name)
		self.assertNotIn(0, grid["years"])
