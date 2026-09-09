# Copyright (c) 2026, BWH and contributors
# For license information, please see license.txt
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from wikify.api import rag as api_rag
from wikify.rag import answer_cache
from wikify.rag import usage as rag_usage
from wikify.rag.router import Route


def cached_answer(answer: str = "Two counts and an X-ray.") -> dict:
	return {
		"answer": answer,
		"citations": [{"n": 1, "title": "Swab Count"}],
		"route": {"intent": "semantic", "section_type": None, "query": "swab", "reason": "why"},
		"refused": False,
		"model": "anthropic/claude-sonnet-4.6",
		"cost": 0.018,
		"prompt_tokens": 9918,
		"completion_tokens": 907,
	}


def route_for(
	query: str = "What happens if the swab count doesn't match?",
	intent: str = "semantic",
	section_type: str | None = None,
) -> Route:
	return Route(intent, section_type, query, "why")


def key_for(**overrides) -> str:
	arguments = {
		"decided": route_for(),
		"project": "PRJ-TEST-1",
		"rerank": True,
		"readable": ["PRJ-TEST-1"],
		"model": "anthropic/claude-sonnet-4.6",
	}
	arguments.update(overrides)
	return answer_cache.cache_key(**arguments)


class TestAnswerCacheKey(FrappeTestCase):
	def setUp(self):
		patcher = patch.object(answer_cache, "index_version", return_value=172)
		patcher.start()
		self.addCleanup(patcher.stop)

	def test_spacing_and_case_reuse_the_entry(self):
		self.assertEqual(
			key_for(decided=route_for(query="What happens if the swab count doesn't match?")),
			key_for(decided=route_for(query="  what HAPPENS if the   swab count doesn't match?  ")),
		)

	def test_different_wording_does_not(self):
		self.assertNotEqual(
			key_for(decided=route_for(query="What happens if the swab count doesn't match?")),
			key_for(decided=route_for(query="What if the swab count is wrong?")),
		)

	def test_neighbouring_questions_do_not_collide(self):
		self.assertNotEqual(
			key_for(decided=route_for(query="surcharge rate for individuals")),
			key_for(decided=route_for(query="surcharge rate for companies")),
		)

	def test_permissions_are_part_of_the_key(self):
		self.assertNotEqual(
			key_for(readable=["PRJ-TEST-1"]),
			key_for(readable=["PRJ-TEST-1", "PRJ-TEST-2"]),
		)

	def test_readable_order_does_not_matter(self):
		self.assertEqual(
			key_for(readable=["PRJ-TEST-1", "PRJ-TEST-2"]),
			key_for(readable=["PRJ-TEST-2", "PRJ-TEST-1"]),
		)

	def test_project_scope_is_part_of_the_key(self):
		self.assertNotEqual(key_for(project="PRJ-TEST-1"), key_for(project="PRJ-TEST-2"))

	def test_model_is_part_of_the_key(self):
		self.assertNotEqual(key_for(model="anthropic/claude-sonnet-4.6"), key_for(model="x/y-1"))

	def test_two_follow_ups_that_mean_the_same_thing_share_an_entry(self):
		self.assertEqual(
			key_for(decided=route_for(query="surcharge rate for companies")),
			key_for(decided=route_for(query="surcharge rate for companies")),
		)

	def test_follow_ups_that_resolve_differently_still_key_apart(self):
		self.assertNotEqual(
			key_for(decided=route_for(query="surcharge rate for companies")),
			key_for(decided=route_for(query="surcharge rate for firms")),
		)

	def test_the_route_itself_is_part_of_the_key(self):
		self.assertNotEqual(
			key_for(decided=route_for(intent="semantic")),
			key_for(decided=route_for(intent="exhaustive")),
		)
		self.assertNotEqual(
			key_for(decided=route_for(section_type=None)),
			key_for(decided=route_for(section_type="job_description")),
		)

	def test_rerank_flag_is_part_of_the_key(self):
		self.assertNotEqual(key_for(rerank=True), key_for(rerank=False))

	def test_reindex_retires_the_entry(self):
		with patch.object(answer_cache, "index_version", return_value=173):
			after_reindex = key_for()
		self.assertNotEqual(key_for(), after_reindex)

	def test_a_prompt_or_floor_change_retires_the_entry(self):
		with patch.object(answer_cache, "CACHE_VERSION", answer_cache.CACHE_VERSION + 1):
			after_deploy = key_for()
		self.assertNotEqual(key_for(), after_deploy)

	def test_an_unreadable_store_yields_no_key_rather_than_a_guessed_one(self):
		with patch.object(answer_cache, "index_version", return_value=None):
			self.assertIsNone(key_for())

	def test_a_missing_key_reads_and_writes_nothing(self):
		self.assertIsNone(answer_cache.get(None))
		answer_cache.set(None, cached_answer())


class TestAskUsesTheCache(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		patcher = patch.object(answer_cache, "index_version", return_value=172)
		patcher.start()
		self.addCleanup(patcher.stop)
		frappe.cache().delete_value(key_for())

	def ask_once(self, answer_result: dict | None, routing_cost: float = 0.0):
		published = []

		def routed(question, project=None, history=None):
			if routing_cost:
				rag_usage.add({"cost": routing_cost, "prompt_tokens": 40, "completion_tokens": 8})
			return route_for()

		with (
			patch.object(api_rag, "assert_readable"),
			patch.object(api_rag, "readable_projects", return_value=["PRJ-TEST-1"]),
			patch.object(api_rag, "session_history", return_value=[]),
			patch.object(api_rag, "route_question", side_effect=routed),
			patch.object(api_rag.agent_llm, "resolve_model", return_value="anthropic/claude-sonnet-4.6"),
			patch.object(api_rag.rag_history, "record_turn", return_value="SESSION-1"),
			patch.object(answer_cache, "index_version", return_value=172),
			patch.object(api_rag.rag_answer, "answer", return_value=answer_result) as answered,
			patch.object(
				frappe,
				"publish_realtime",
				side_effect=lambda event, payload=None, **options: published.append(payload or {}),
			),
		):
			result = api_rag.ask("What happens if the swab count doesn't match?", project="PRJ-TEST-1")
		return result, answered, published

	def test_second_ask_makes_no_synthesis_call_and_replays_the_answer(self):
		first, answered, _ = self.ask_once(cached_answer())
		self.assertEqual(answered.call_count, 1)
		self.assertFalse(first["cached"])
		self.assertEqual(first["cost"], 0.018)

		second, answered_again, published = self.ask_once(cached_answer())
		self.assertEqual(answered_again.call_count, 0, "a cache hit must not call the answerer")
		self.assertTrue(second["cached"])
		self.assertEqual(second["answer"], first["answer"])
		self.assertEqual(second["citations"], first["citations"])

		messages = published
		self.assertTrue(any("route" in message for message in messages))
		self.assertTrue(any("citations" in message for message in messages))
		self.assertTrue(any(message.get("delta") for message in messages))
		self.assertTrue(any(message.get("done") for message in messages))

	def test_a_hit_reports_the_routing_call_it_made_and_nothing_more(self):
		frappe.cache().delete_value(key_for())
		self.ask_once(cached_answer(), routing_cost=0.0004)

		second, answered_again, _ = self.ask_once(cached_answer(), routing_cost=0.0004)
		self.assertEqual(answered_again.call_count, 0)
		self.assertAlmostEqual(second["cost"], 0.0004)
		self.assertEqual(second["prompt_tokens"], 40)
		self.assertEqual(second["completion_tokens"], 8)

	def test_a_refusal_is_not_stored(self):
		refusal = {**cached_answer("I couldn't find this in the wiki."), "refused": True, "citations": []}
		self.ask_once(refusal)

		_, answered_again, _ = self.ask_once(refusal)
		self.assertEqual(answered_again.call_count, 1, "a refusal must be re-asked, not replayed")
