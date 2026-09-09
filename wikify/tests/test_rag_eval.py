# Copyright (c) 2026, BWH and contributors
# For license information, please see license.txt
import os
import tempfile

import frappe
from frappe.tests import IntegrationTestCase

from wikify.engine import settings
from wikify.rag import eval as rag_eval
from wikify.rag import index as rag_index
from wikify.rag.search import Hit

G1_EXPECTED_SOURCES = 15
G1_NAIVE_LIMIT = 8

HEADLINE_QUERY = "give me all the job descriptions across all the documents"
HEADLINE_NAIVE_CORRECT = 6
G1_NAIVE_CORRECT = 6

G1_NAIVE_MISSES = G1_EXPECTED_SOURCES - G1_NAIVE_CORRECT
HEADLINE_NAIVE_MISSES = G1_EXPECTED_SOURCES - HEADLINE_NAIVE_CORRECT


def make_hit(section: str, title: str = "", document: str = "Doc", section_type: str | None = None) -> Hit:
	return Hit(
		chunk_id=f"{section}::0",
		section=section,
		source_document="SD-1",
		document_title=document,
		title=title or section,
		text="",
		section_type=section_type,
		hierarchy_path="",
		page_start=1,
		page_end=2,
		wiki_route=None,
		wikify_import=None,
		score=0.5,
	)


def expected_sections(names: list[str]) -> list[dict]:
	return [
		{"name": name, "title": name, "document_title": "Doc", "section_type": "job_description"}
		for name in names
	]


class TestEvalScoring(IntegrationTestCase):
	def test_perfect_leg_is_complete(self):
		expected = expected_sections(["A", "B"])
		leg = rag_eval.score_leg(expected, [], [make_hit("A"), make_hit("B")])
		self.assertEqual(leg["recall"], 1.0)
		self.assertEqual(leg["precision"], 1.0)
		self.assertTrue(leg["complete"])
		self.assertEqual(leg["missed"], [])

	def test_one_missing_source_is_high_recall_but_not_complete(self):
		expected = expected_sections([f"S{number}" for number in range(15)])
		hits = [make_hit(f"S{number}") for number in range(14)]
		leg = rag_eval.score_leg(expected, [], hits)
		self.assertEqual(leg["recall"], round(14 / 15, 4))
		self.assertFalse(leg["complete"])
		self.assertEqual([row["title"] for row in leg["missed"]], ["S14"])

	def test_precision_counts_the_noise(self):
		expected = expected_sections(["A", "B"])
		hits = [make_hit("A"), make_hit("X"), make_hit("Y"), make_hit("Z")]
		leg = rag_eval.score_leg(expected, [], hits)
		self.assertEqual(leg["recall"], 0.5)
		self.assertEqual(leg["precision"], 0.25)
		self.assertFalse(leg["complete"])

	def test_required_source_is_tracked_separately_from_recall(self):
		expected = expected_sections(["Main", "Extra"])
		leg = rag_eval.score_leg(expected, ["Main"], [make_hit("Extra")])
		self.assertEqual(leg["recall"], 0.5)
		self.assertFalse(leg["required_hit"])
		self.assertTrue(rag_eval.score_leg(expected, ["Main"], [make_hit("Main")])["required_hit"])

	def test_empty_leg_scores_zero_recall_and_misses_everything(self):
		expected = expected_sections(["A", "B"])
		leg = rag_eval.score_leg(expected, [], [])
		self.assertEqual(leg["recall"], 0.0)
		self.assertIsNone(leg["precision"])
		self.assertFalse(leg["complete"])
		self.assertEqual(len(leg["missed"]), 2)

	def test_golden_questions_cover_the_declared_lanes(self):
		kinds = [question["kind"] for question in rag_eval.GOLDEN_QUESTIONS]
		self.assertEqual(len(rag_eval.GOLDEN_QUESTIONS), 12)
		self.assertEqual(kinds.count("exhaustive"), 4)
		self.assertEqual(kinds.count("semantic"), 5)
		self.assertEqual(kinds.count("hybrid"), 2)
		self.assertEqual(kinds.count("refusal"), 1)
		self.assertEqual(len(rag_eval.GOLDEN_QUESTIONS[0]["expected"]), G1_EXPECTED_SOURCES)
		self.assertEqual(
			len({question["id"] for question in rag_eval.GOLDEN_QUESTIONS}),
			len(rag_eval.GOLDEN_QUESTIONS),
		)


class TestGoldenQuestions(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.project = frappe.db.get_value(
			"Wikify Project", {"project_name": rag_eval.DEMO_PROJECT_NAME}, "name"
		)
		cls.results = None
		if not cls.project:
			return
		if not rag_index.index_stats([cls.project])["chunks"]:
			cls.project = None
			return
		if not settings.openrouter_key():
			return
		cls.results = rag_eval.run_eval(project=cls.project, k=G1_NAIVE_LIMIT)

	def setUp(self):
		if not self.project:
			self.skipTest(
				"Demo Corpus is not seeded or not indexed on this site — run "
				"wikify.tests.fixtures.demo_corpus.seed_demo_corpus then rag.index.rebuild_project"
			)
		if self.results is None:
			self.skipTest("No OpenRouter key configured, so the routed leg cannot be evaluated")

	def question(self, question_id: str) -> dict:
		return next(row for row in self.results["questions"] if row["id"] == question_id)

	def test_ground_truth_resolves_against_the_real_corpus(self):
		for row in self.results["questions"]:
			declared = next(q for q in rag_eval.GOLDEN_QUESTIONS if q["id"] == row["id"])
			self.assertEqual(
				len(row["expected_sources"]),
				len(declared["expected"]),
				f"{row['id']}: expected sources did not all resolve to Source Sections",
			)

	def test_g1_reproduces_the_headline_naive_vs_routed_numbers(self):
		row = self.question("G1")
		naive, routed = row["modes"]["naive"], row["modes"]["routed"]

		self.assertEqual(row["route"]["intent"], "exhaustive")
		self.assertEqual(row["route"]["section_type"], "staff_roles_and_responsibilities")

		self.assertEqual(naive["returned_count"], G1_NAIVE_LIMIT)
		self.assertEqual(routed["returned_count"], G1_EXPECTED_SOURCES)

		self.assertEqual(naive["correct_count"], G1_NAIVE_CORRECT)
		self.assertEqual(naive["recall"], round(G1_NAIVE_CORRECT / G1_EXPECTED_SOURCES, 4))
		self.assertEqual(routed["recall"], 1.0)
		self.assertFalse(naive["complete"])
		self.assertTrue(routed["complete"])
		self.assertEqual(len(naive["missed"]), G1_NAIVE_MISSES)
		self.assertEqual(len(routed["missed"]), 0)

	def test_g1_naive_misses_whole_documents_that_routed_covers(self):
		row = self.question("G1")
		self.assertLess(row["modes"]["naive"]["documents_covered"], 5)
		self.assertEqual(row["modes"]["routed"]["documents_covered"], 5)

	def test_routed_beats_naive_on_exhaustive_completeness(self):
		naive = self.results["aggregate"]["naive"]
		routed = self.results["aggregate"]["routed"]
		self.assertGreater(routed["exhaustive_complete_count"], naive["exhaustive_complete_count"])
		self.assertGreater(routed["exhaustive_recall"], naive["exhaustive_recall"])
		self.assertGreater(routed["found_total"], naive["found_total"])
		self.assertLess(routed["missed_total"], naive["missed_total"])

	def test_routed_never_loses_ground_on_a_golden_question(self):
		regressions = [
			(row["id"], row["modes"]["naive"]["recall"], row["modes"]["routed"]["recall"])
			for row in self.results["questions"]
			if row["expected_sources"] and row["modes"]["routed"]["recall"] < row["modes"]["naive"]["recall"]
		]
		self.assertEqual(regressions, [], f"routed retrieval lost recall on: {regressions}")

	def test_exhaustive_questions_use_the_filter_leg(self):
		for row in self.results["questions"]:
			if row["route"] and row["route"]["intent"] == "exhaustive":
				section_type = row["route"]["section_type"]
				in_corpus = frappe.get_all(
					"Source Section",
					filters={
						"section_type": section_type,
						"source_document": ["in", self.corpus_documents()],
						"is_group": 0,
					},
					pluck="name",
				)
				self.assertEqual(
					row["modes"]["routed"]["returned_count"],
					len(in_corpus),
					f"{row['id']}: filter leg did not return every {section_type} section",
				)

	def corpus_documents(self) -> list[str]:
		return frappe.get_all("Source Document", filters={"project": self.project}, pluck="name")

	def test_g4_reaches_the_other_catch_all(self):
		row = self.question("G4")
		self.assertEqual(row["expected_intent"], "exhaustive")
		self.assertEqual(row["route"]["intent"], "exhaustive")
		self.assertEqual(row["route"]["section_type"], "other")
		self.assertTrue(row["modes"]["routed"]["complete"])
		self.assertFalse(row["modes"]["naive"]["complete"])

	def test_g12_is_refused_rather_than_answered(self):
		row = self.question("G12")
		self.assertTrue(row["expect_refusal"])
		self.assertTrue(row["refused"], "G12 must refuse — the corpus has no cyber security plan")

	def test_compare_query_reproduces_the_demoed_headline(self):
		comparison = rag_eval.compare_query(HEADLINE_QUERY, project=self.project)
		self.assertEqual(comparison["naive"]["count"], G1_NAIVE_LIMIT)
		self.assertEqual(comparison["routed"]["count"], G1_EXPECTED_SOURCES)
		self.assertEqual(comparison["routed"]["documents_covered"], 5)
		self.assertEqual(comparison["naive"]["documents_covered"], 4)
		correct = [
			hit
			for hit in comparison["naive"]["hits"]
			if hit["section_type"] == "staff_roles_and_responsibilities"
		]
		self.assertEqual(len(correct), HEADLINE_NAIVE_CORRECT)
		self.assertEqual(len(comparison["missed_by_naive"]), HEADLINE_NAIVE_MISSES)
		naive_sections = {hit["section"] for hit in comparison["naive"]["hits"]}
		for missed in comparison["missed_by_naive"]:
			self.assertNotIn(missed["section"], naive_sections)

	def test_results_are_json_serializable(self):
		encoded = frappe.as_json(self.results)
		self.assertIn("G1", encoded)
		self.assertEqual(frappe.parse_json(encoded)["project"], self.project)

	def test_scorecard_is_self_contained_and_names_the_missed_sources(self):
		with tempfile.TemporaryDirectory() as directory:
			path = os.path.join(directory, "rag-eval.html")
			rag_eval.write_scorecard(self.results, path)
			with open(path, encoding="utf-8") as scorecard:
				markup = scorecard.read()

		self.assertNotIn("http://", markup)
		self.assertNotIn("https://", markup)
		self.assertNotIn("<script", markup)
		for missed in self.question("G1")["modes"]["naive"]["missed"]:
			self.assertIn(rag_eval.escape(missed["title"]), markup)
