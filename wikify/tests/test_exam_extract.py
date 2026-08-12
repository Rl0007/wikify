# Copyright (c) 2026, BWH and contributors
# For license information, please see license.txt

"""Question-paper extraction (`wikify.exam.extract`).

The parsing functions are exercised against small synthetic papers rather than the real
ICAI PDFs: they are pure text functions, and a fixture that fits on a screen is the only
kind whose expected answer can be checked by eye. The one LLM boundary (`structure_block`)
is driven by a stub reply, because the whole point of the assertions there is that the
model's numbers are NOT trusted.

The load-bearing assertions:
  - marks come from the printed `(N Marks)` markers, never from the model. `(2 x 5 = 10
    Marks)` is worth 10, and a model that returns four sub-questions where the paper printed
    two markers gets truncated rather than believed. This is the Nov-2023-Q3 regression:
    asked to infer marks, the model turned a 14-mark question into 38.
  - `parse_optionality` reads the printed instruction from the DESCRIPTIVE part. In a
    new-scheme paper that sentence sits hundreds of lines past the document head, behind the
    whole MCQ section — reading the head instead marks every question compulsory and inflates
    attemptable marks back up to the printed total.
  - `attemptable_marks` honours optionality and can never exceed the printed total.
  - re-extracting a paper replaces its questions instead of doubling them, and takes the
    `Question Topic Link` children with it.
"""

from typing import ClassVar
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from wikify.exam import extract

# One descriptive question in the shape that produced the inflation: items (i)-(iv) are
# enumerated INSIDE the second sub-part and only the sub-part carries a marker.
NOV_2023_Q3 = """Question 3
(a) Examine the taxability of the following receipts in the hands of the assessee.
(6 Marks)
(b) Discuss, with reasons, whether the following expenses are allowable:
(i) interest paid on a loan taken to pay dividend;
(ii) contribution to an unapproved gratuity fund;
(iii) penalty levied under the GST law;
(iv) commission paid to a director without deduction of tax at source.
(8 Marks)
Answer
(a) The receipts are chargeable to tax under section 56(2)(x) ...
"""

OLD_SCHEME_PAPER = """Test Series: November 2023
FINAL EXAMINATION: NOVEMBER 2023
PAPER 7: DIRECT TAX LAWS AND INTERNATIONAL TAXATION
Question No.1 is compulsory.
Answer any four questions from the remaining five questions.
1
The Institute of Chartered Accountants of India
Question 1
Compute the total income of Mr. X for A.Y. 2024-25.
(14 Marks)
Answer
The total income works out to Rs. 12,40,000.
P.T.O.
Question 2
Discuss the applicability of section 115BAC.
(8 Marks)
Answer
Section 115BAC provides concessional rates.
"""


def entries_from(text: str, page_no: int = 1) -> list[tuple[int, str]]:
	"""`page_lines` output for a synthetic paper, all on one page."""
	return [(page_no, line) for line in text.splitlines()]


def new_scheme_entries() -> list[tuple[int, str]]:
	"""A May-2024-shape paper whose optionality sentence sits behind the whole MCQ section.

	The MCQ filler is deliberately longer than `parse_optionality`'s 80-line window, which is
	what makes the descriptive-part-not-document-head assertion mean anything.
	"""
	lines = [
		"FINAL EXAMINATION: MAY 2024",
		"PAPER 4: DIRECT TAX LAWS AND INTERNATIONAL TAXATION",
		"Part I - Multiple Choice Questions",
	]
	for number in range(1, 41):
		lines.append(f"MCQ {number}. Which of the following is correct for A.Y. 2025-26?")
		lines.append("(a) Option one (b) Option two (c) Option three (d) Option four")
	lines.append("Answer Key")
	lines.extend(f"MCQ {number} - (b)" for number in range(1, 41))
	lines.extend(
		[
			"Part II - Descriptive Questions",
			"Question No.1 is compulsory.",
			"Answer any four questions from the remaining five questions.",
			"Question 1",
			"Compute the total income of Ms. Y.",
			"(14 Marks)",
			"Answer",
			"Her total income is Rs. 9,80,000.",
			"Question 2",
			"Explain the scope of section 9(1)(i).",
			"(2 x 5 = 10 Marks)",
			"Answer",
			"Income deemed to accrue in India ...",
		]
	)
	return entries_from("\n".join(lines))


class TestExamExtractParsing(FrappeTestCase):
	"""Everything that reads the printed document. No DB, no network."""

	def test_marks_marker_yields_the_printed_total_not_the_multiplier(self):
		self.assertEqual(extract.marks_markers("(5 Marks)"), [5.0])
		self.assertEqual(extract.marks_markers("(1 Mark)"), [1.0])
		# "the last number before Marks is always the value awarded"
		self.assertEqual(extract.marks_markers("(2 x 5 = 10 Marks)"), [10.0])
		self.assertEqual(extract.marks_markers("(2 \u00d7 5 = 10 Marks)"), [10.0])
		self.assertEqual(extract.marks_markers("(4 marks)"), [4.0])
		self.assertEqual(extract.marks_markers("no marker here at all"), [])

	def test_markers_are_returned_in_printed_order(self):
		text = "(6 Marks) then later (2 x 5 = 10 Marks) and finally (4 Marks)"
		self.assertEqual(extract.marks_markers(text), [6.0, 10.0, 4.0])

	def test_enumerated_items_inside_a_sub_part_do_not_each_earn_the_marker(self):
		"""The Nov-2023 Q3 shape: four items, two markers, 14 marks — not 38."""
		markers = extract.marks_markers(NOV_2023_Q3)
		self.assertEqual(markers, [6.0, 8.0])
		self.assertEqual(sum(markers), 14.0)

	def test_boilerplate_is_dropped_before_segmentation(self):
		entries = extract.drop_boilerplate(entries_from(OLD_SCHEME_PAPER))
		kept = [line for _page, line in entries]

		self.assertNotIn("The Institute of Chartered Accountants of India", kept)
		self.assertNotIn("FINAL EXAMINATION: NOVEMBER 2023", kept)
		self.assertNotIn("P.T.O.", kept)
		self.assertNotIn("1", kept)
		# The questions themselves survive intact.
		self.assertIn("Question 1", kept)
		self.assertIn("(14 Marks)", kept)

	def test_an_old_scheme_paper_has_no_part_i_and_falls_through_to_descriptive(self):
		entries = extract.drop_boilerplate(entries_from(OLD_SCHEME_PAPER))
		parts = extract.split_parts(entries)

		self.assertEqual(parts["mcq"], [])
		self.assertEqual(parts["descriptive"], entries)

	def test_a_new_scheme_paper_splits_on_its_printed_part_headings(self):
		parts = extract.split_parts(extract.drop_boilerplate(new_scheme_entries()))
		mcq = [line for _page, line in parts["mcq"]]
		descriptive = [line for _page, line in parts["descriptive"]]

		self.assertEqual(mcq[0], "Part I - Multiple Choice Questions")
		# The MCQ span stops at the Answer Key — the key is neither question nor answer text.
		self.assertNotIn("Answer Key", mcq)
		self.assertNotIn("MCQ 1 - (b)", mcq)
		self.assertIn("MCQ 1. Which of the following is correct for A.Y. 2025-26?", mcq)

		self.assertEqual(descriptive[0], "Part II - Descriptive Questions")
		self.assertIn("Question 1", descriptive)
		self.assertNotIn("MCQ 1. Which of the following is correct for A.Y. 2025-26?", descriptive)

	def test_optionality_is_read_from_the_descriptive_part_not_the_document_head(self):
		"""The regression that made attemptable marks equal the printed total.

		In a new-scheme paper the sentence governing Part II is printed after the entire MCQ
		section. Scanning the head of the DOCUMENT finds nothing and every question is then
		treated as compulsory; scanning the head of the PART finds it.
		"""
		entries = extract.drop_boilerplate(new_scheme_entries())
		parts = extract.split_parts(entries)

		from_document_head = extract.parse_optionality(entries)
		self.assertIsNone(from_document_head["compulsory_question"])
		self.assertIsNone(from_document_head["choose_count"])

		from_descriptive = extract.parse_optionality(parts["descriptive"])
		self.assertEqual(from_descriptive["compulsory_question"], 1)
		self.assertEqual(from_descriptive["choose_count"], 4)
		self.assertEqual(from_descriptive["choice_size"], 5)

	def test_optionality_reads_the_old_scheme_sentence_too(self):
		entries = extract.drop_boilerplate(entries_from(OLD_SCHEME_PAPER))
		optionality = extract.parse_optionality(entries)

		self.assertEqual(
			optionality,
			{"compulsory_question": 1, "choose_count": 4, "choice_size": 5},
		)

	def test_a_paper_printing_no_optionality_sentence_parses_as_none(self):
		optionality = extract.parse_optionality(entries_from("Question 1\nCompute the total income.\n"))

		self.assertEqual(
			optionality,
			{"compulsory_question": None, "choose_count": None, "choice_size": None},
		)

	def test_question_blocks_split_on_headings_and_carry_the_page_they_started_on(self):
		entries = extract.drop_boilerplate(
			[(1, line) for line in OLD_SCHEME_PAPER.splitlines()[:12]]
			+ [(2, line) for line in OLD_SCHEME_PAPER.splitlines()[12:]]
		)
		blocks = extract.question_blocks(entries)

		self.assertEqual([block["question_no"] for block in blocks], [1, 2])
		self.assertEqual(blocks[0]["page_no"], 1)
		self.assertEqual(blocks[1]["page_no"], 2)
		# Each block still carries ICAI's model answer — stripping that is the LLM's job.
		self.assertIn("The total income works out to", blocks[0]["text"])
		# ...but not the NEXT question's text.
		self.assertNotIn("section 115BAC", blocks[0]["text"])
		self.assertIn("section 115BAC", blocks[1]["text"])

	def test_top_level_number_reads_the_leading_integer(self):
		self.assertEqual(extract.top_level_number("3(b)(ii)"), 3)
		self.assertEqual(extract.top_level_number("12"), 12)
		self.assertEqual(extract.top_level_number(" 4 (a)"), 4)
		self.assertIsNone(extract.top_level_number("(a)"))
		self.assertIsNone(extract.top_level_number(None))


class TestExamExtractOptionalityArithmetic(FrappeTestCase):
	"""`apply_optionality` + `attemptable_marks` — the marks a candidate can actually score."""

	OPTIONALITY: ClassVar[dict] = {"compulsory_question": 1, "choose_count": 4, "choice_size": 5}

	def paper(self) -> list[dict]:
		"""Q1 compulsory (14), Q2-Q6 optional. Q2 is split into two sub-parts."""
		return [
			{"question_no": "1", "marks": 14.0},
			{"question_no": "2(a)", "marks": 6.0},
			{"question_no": "2(b)", "marks": 8.0},
			{"question_no": "3", "marks": 14.0},
			{"question_no": "4", "marks": 14.0},
			{"question_no": "5", "marks": 14.0},
			{"question_no": "6", "marks": 10.0},
		]

	def test_descriptive_questions_are_tagged_against_the_printed_instruction(self):
		questions = self.paper()
		extract.apply_optionality(questions, self.OPTIONALITY, "descriptive")

		self.assertEqual(questions[0]["is_compulsory"], 1)
		self.assertNotIn("choice_group", questions[0])
		for entry in questions[1:]:
			self.assertEqual(entry["is_compulsory"], 0)
			self.assertEqual(entry["choice_group"], "descriptive-choice")
			self.assertEqual(entry["choose_count"], 4)

	def test_mcqs_are_all_compulsory_whatever_the_descriptive_part_says(self):
		questions = self.paper()
		extract.apply_optionality(questions, self.OPTIONALITY, "mcq")

		self.assertTrue(all(entry["is_compulsory"] == 1 for entry in questions))
		self.assertTrue(all("choice_group" not in entry for entry in questions))

	def test_attemptable_marks_drop_the_cheapest_alternative_and_stay_under_the_total(self):
		questions = self.paper()
		extract.apply_optionality(questions, self.OPTIONALITY, "descriptive")

		total = sum(entry["marks"] for entry in questions)
		attemptable = extract.attemptable_marks(questions, self.OPTIONALITY)

		# Q2 is 6 + 8 = 14 as ONE question. The four best of {14, 14, 14, 14, 10} are kept,
		# so the 10-mark Q6 is the one nobody answers.
		self.assertEqual(total, 80.0)
		self.assertEqual(attemptable, 70.0)
		self.assertLessEqual(attemptable, total)

	def test_attemptable_marks_never_exceed_the_total_for_any_shape_of_paper(self):
		shapes = [
			[14.0, 14.0, 14.0, 14.0, 14.0, 14.0],
			[20.0, 5.0, 5.0, 5.0, 5.0, 5.0],
			[1.0, 30.0, 30.0, 30.0, 30.0, 30.0],
		]
		for marks in shapes:
			with self.subTest(marks=marks):
				questions = [
					{"question_no": str(number), "marks": value}
					for number, value in enumerate(marks, start=1)
				]
				extract.apply_optionality(questions, self.OPTIONALITY, "descriptive")
				attemptable = extract.attemptable_marks(questions, self.OPTIONALITY)
				self.assertLessEqual(attemptable, sum(marks))

	def test_an_unparsed_optionality_sentence_discounts_nothing(self):
		"""No sentence read → everything compulsory, rather than a guessed discount."""
		optionality = {"compulsory_question": None, "choose_count": None, "choice_size": None}
		questions = self.paper()
		extract.apply_optionality(questions, optionality, "descriptive")

		self.assertTrue(all(entry["is_compulsory"] == 1 for entry in questions))
		self.assertEqual(
			extract.attemptable_marks(questions, optionality),
			sum(entry["marks"] for entry in questions),
		)


def stub_llm(payload: dict):
	"""A `chat_completion` stand-in returning `payload` as the model's JSON reply."""

	def reply(model, messages, **kwargs):
		return {"choices": [{"message": {"content": frappe.as_json(payload)}}]}

	return reply


class TestExamStructureBlock(FrappeTestCase):
	"""The single LLM boundary. The model is stubbed; what is tested is the distrust of it."""

	def test_the_model_cannot_inflate_marks_past_the_printed_markers(self):
		"""The Nov-2023 Q3 failure, reproduced: model says 4 x 8 = 38, paper says 14."""
		block = {"question_no": 3, "page_no": 7, "text": NOV_2023_Q3}
		inflated = {
			"questions": [
				{"question_no": "3(a)", "kind": "Descriptive", "text": "Examine ...", "marks": 6},
				{"question_no": "3(b)(i)", "kind": "Descriptive", "text": "interest ...", "marks": 8},
				{"question_no": "3(b)(ii)", "kind": "Descriptive", "text": "gratuity ...", "marks": 8},
				{"question_no": "3(b)(iii)", "kind": "Descriptive", "text": "penalty ...", "marks": 8},
				{"question_no": "3(b)(iv)", "kind": "Descriptive", "text": "commission ...", "marks": 8},
			]
		}
		with patch.object(extract.llm, "chat_completion", side_effect=stub_llm(inflated)):
			outcome = extract.structure_block(block, "stub-model", "stub-key")

		self.assertEqual(outcome["expected"], 2)
		self.assertEqual(outcome["returned"], 5)
		self.assertEqual(outcome["question_no"], 3)

		questions = outcome["questions"]
		self.assertEqual(len(questions), 2)
		self.assertEqual([entry["marks"] for entry in questions], [6.0, 8.0])
		self.assertEqual(sum(entry["marks"] for entry in questions), 14.0)
		self.assertEqual([entry["page_no"] for entry in questions], [7, 7])

	def test_the_prompt_states_the_marker_count_as_non_negotiable(self):
		block = {"question_no": 3, "page_no": 7, "text": NOV_2023_Q3}
		captured = {}

		def reply(model, messages, **kwargs):
			captured["system"] = messages[0]["content"]
			return {"choices": [{"message": {"content": '{"questions": []}'}}]}

		with patch.object(extract.llm, "chat_completion", side_effect=reply):
			extract.structure_block(block, "stub-model", "stub-key")

		self.assertIn("EXACTLY 2 sub-part(s)", captured["system"])
		self.assertIn("6.0, 8.0", captured["system"])
		self.assertIn('Do NOT return a "marks" field', captured["system"])

	def test_a_block_with_no_printed_marker_never_reaches_the_model(self):
		block = {"question_no": 9, "page_no": 3, "text": "Question 9\nAnswer\nSome prose with no marker."}
		with patch.object(extract.llm, "chat_completion", side_effect=AssertionError("LLM called")) as call:
			self.assertEqual(extract.structure_block(block, "stub-model", "stub-key"), [])
		call.assert_not_called()

	def test_unparseable_model_output_yields_no_questions_rather_than_junk(self):
		block = {"question_no": 3, "page_no": 7, "text": NOV_2023_Q3}

		def reply(model, messages, **kwargs):
			return {"choices": [{"message": {"content": "{ this is not json"}}]}

		with patch.object(extract.llm, "chat_completion", side_effect=reply):
			outcome = extract.structure_block(block, "stub-model", "stub-key")

		self.assertEqual(outcome, [])


class TestExamQuestionStorage(FrappeTestCase):
	"""`save_questions` / `remove_questions` against the real DB, rolled back per test."""

	def setUp(self):
		suffix = frappe.generate_hash(length=8)
		self.project = frappe.get_doc(
			{"doctype": "Wikify Project", "project_name": f"Exam Extract Test {suffix}"}
		).insert(ignore_permissions=True)
		self.paper = frappe.get_doc(
			{
				"doctype": "Wikify Exam Paper",
				"paper_title": f"November 2023 — DT {suffix}",
				"project": self.project.name,
				"exam_month": "November",
				"exam_year": 2023,
			}
		).insert(ignore_permissions=True)

	def extracted(self) -> list[dict]:
		return [
			{
				"question_no": "1",
				"kind": "Descriptive",
				"text": "Compute the total income of Mr. X.",
				"marks": 14.0,
				"is_compulsory": 1,
				"page_no": 2,
				"statutory_refs": ["section 45(1A)", "section 115BAC"],
			},
			{
				"question_no": "2(a)",
				"kind": "Numerical",
				"text": "Compute the capital gain.",
				"marks": 6.0,
				"is_compulsory": 0,
				"choice_group": "descriptive-choice",
				"choose_count": 4,
				"page_no": 3,
				"statutory_refs": [],
			},
		]

	def question_names(self) -> list[str]:
		return frappe.get_all("Wikify Exam Question", filters={"exam_paper": self.paper.name}, pluck="name")

	def test_saved_questions_carry_their_marks_refs_and_paper_denormalisations(self):
		self.assertEqual(extract.save_questions(self.paper.name, self.extracted()), 2)

		rows = frappe.get_all(
			"Wikify Exam Question",
			filters={"exam_paper": self.paper.name},
			fields=["question_no", "marks", "statutory_refs", "project", "exam_year", "is_compulsory"],
			order_by="question_no asc",
		)
		self.assertEqual([row["question_no"] for row in rows], ["1", "2(a)"])
		self.assertEqual([row["marks"] for row in rows], [14.0, 6.0])
		self.assertEqual(rows[0]["statutory_refs"], "section 45(1A), section 115BAC")
		self.assertEqual(rows[1]["statutory_refs"], "")
		# `project` and `exam_year` are fetched from the paper — mapping and scoring both
		# filter on them, so a question that lost them is invisible to the rest of the app.
		self.assertEqual(rows[0]["project"], self.project.name)
		self.assertEqual(rows[0]["exam_year"], 2023)

	def test_re_extraction_replaces_the_previous_questions_instead_of_doubling_them(self):
		extract.save_questions(self.paper.name, self.extracted())
		first = set(self.question_names())

		extract.save_questions(self.paper.name, self.extracted())
		second = set(self.question_names())

		self.assertEqual(len(second), 2)
		self.assertFalse(first & second, "stale rows survived a re-extraction")

	def test_removing_questions_takes_their_topic_links_with_them(self):
		"""`db.delete` on the parent does not cascade — orphan links accumulate silently."""
		extract.save_questions(self.paper.name, self.extracted())
		names = self.question_names()
		for name in names:
			document = frappe.get_doc("Wikify Exam Question", name)
			document.append("topics", {"topic_title": "Capital Gains", "score": 1.0, "rank": 1})
			document.save(ignore_permissions=True)
		self.assertEqual(
			frappe.db.count("Question Topic Link", {"parent": ["in", names]}),
			2,
		)

		self.assertEqual(extract.remove_questions(self.paper.name), 2)

		self.assertEqual(self.question_names(), [])
		self.assertEqual(frappe.db.count("Question Topic Link", {"parent": ["in", names]}), 0)

	def test_removing_questions_from_a_paper_with_none_is_a_no_op(self):
		self.assertEqual(extract.remove_questions(self.paper.name), 0)
