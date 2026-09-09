# Copyright (c) 2026, BWH and contributors
# For license information, please see license.txt

"""The exact-match answer cache — what it may reuse, and what it must never reuse.

Ask is ~100% LLM wall clock, so a repeat question is served from cache. These tests pin
the two things that make that safe: an entry dies when the corpus behind it is rewritten,
and an entry is never shared across users, projects, models or conversations whose answers
would legitimately differ.
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from wikify.api import rag as api_rag
from wikify.rag import answer_cache


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


def key_for(**overrides) -> str:
	arguments = {
		"question": "What happens if the swab count doesn't match?",
		"project": "PRJ-TEST-1",
		"history": [],
		"rerank": True,
		"readable": ["PRJ-TEST-1"],
		"model": "anthropic/claude-sonnet-4.6",
	}
	arguments.update(overrides)
	return answer_cache.cache_key(**arguments)


class TestAnswerCacheKey(FrappeTestCase):
	def setUp(self):
		# Pinned so a key comparison isolates the field under test; the version's own
		# effect on the key is what `test_reindex_retires_the_entry` covers.
		patcher = patch.object(answer_cache, "index_version", return_value=172)
		patcher.start()
		self.addCleanup(patcher.stop)

	def test_spacing_and_case_reuse_the_entry(self):
		"""Differences that cannot change an answer must not cost a second one."""
		self.assertEqual(
			key_for(question="What happens if the swab count doesn't match?"),
			key_for(question="  what HAPPENS if the   swab count doesn't match?  "),
		)

	def test_different_wording_does_not(self):
		"""The cache is exact-match. Two ways of asking one thing are two entries."""
		self.assertNotEqual(
			key_for(question="What happens if the swab count doesn't match?"),
			key_for(question="What if the swab count is wrong?"),
		)

	def test_neighbouring_questions_do_not_collide(self):
		"""The failure a semantic cache would produce, pinned as a requirement.

		These two are near neighbours in embedding space and carry DIFFERENT statutory
		rates. Sharing an entry would answer one with the other's figure behind a real
		citation — so they must key apart.
		"""
		self.assertNotEqual(
			key_for(question="surcharge rate for individuals"),
			key_for(question="surcharge rate for companies"),
		)

	def test_permissions_are_part_of_the_key(self):
		"""A broadly-permissioned user's answer must never be served to a narrower one."""
		self.assertNotEqual(
			key_for(readable=["PRJ-TEST-1"]),
			key_for(readable=["PRJ-TEST-1", "PRJ-TEST-2"]),
		)

	def test_readable_order_does_not_matter(self):
		"""The permission query's row order is not guaranteed, so it must not split keys."""
		self.assertEqual(
			key_for(readable=["PRJ-TEST-1", "PRJ-TEST-2"]),
			key_for(readable=["PRJ-TEST-2", "PRJ-TEST-1"]),
		)

	def test_project_scope_is_part_of_the_key(self):
		self.assertNotEqual(key_for(project="PRJ-TEST-1"), key_for(project="PRJ-TEST-2"))

	def test_model_is_part_of_the_key(self):
		"""A project that switches synthesis model must not replay the old model's answers."""
		self.assertNotEqual(key_for(model="anthropic/claude-sonnet-4.6"), key_for(model="x/y-1"))

	def test_history_is_part_of_the_key(self):
		"""The router rewrites follow-ups against history, so "what about the second one?"
		means something different in a different conversation."""
		self.assertNotEqual(
			key_for(history=[]),
			key_for(history=[{"role": "user", "content": "list the job descriptions"}]),
		)

	def test_rerank_flag_is_part_of_the_key(self):
		self.assertNotEqual(key_for(rerank=True), key_for(rerank=False))

	def test_reindex_retires_the_entry(self):
		"""Freshness is the store's write counter, not an invalidation hook."""
		with patch.object(answer_cache, "index_version", return_value=173):
			after_reindex = key_for()
		self.assertNotEqual(key_for(), after_reindex)


class TestAskUsesTheCache(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.published = []

	def ask_once(self, answer_result: dict | None):
		"""Run `ask` with the answering path stubbed, capturing what reached realtime."""
		published = []
		with (
			patch.object(api_rag, "assert_readable"),
			patch.object(api_rag, "readable_projects", return_value=["PRJ-TEST-1"]),
			patch.object(api_rag, "session_history", return_value=[]),
			patch.object(api_rag.agent_llm, "resolve_model", return_value="anthropic/claude-sonnet-4.6"),
			patch.object(api_rag.rag_history, "record_turn", return_value="SESSION-1"),
			patch.object(answer_cache, "index_version", return_value=172),
			patch.object(api_rag.rag_answer, "answer", return_value=answer_result) as answered,
			# `publish_realtime(event, payload, user=...)` — the payload is positional.
			patch.object(
				frappe,
				"publish_realtime",
				side_effect=lambda event, payload=None, **options: published.append(payload or {}),
			),
		):
			result = api_rag.ask("What happens if the swab count doesn't match?", project="PRJ-TEST-1")
		return result, answered, published

	def test_second_ask_makes_no_llm_call_and_reports_no_spend(self):
		frappe.cache().delete_value(key_for())

		first, answered, _ = self.ask_once(cached_answer())
		self.assertEqual(answered.call_count, 1)
		self.assertFalse(first["cached"])
		self.assertEqual(first["cost"], 0.018)

		second, answered_again, published = self.ask_once(cached_answer())
		self.assertEqual(answered_again.call_count, 0, "a cache hit must not call the answerer")
		self.assertTrue(second["cached"])
		self.assertEqual(second["cost"], 0.0)
		self.assertEqual(second["completion_tokens"], 0)
		self.assertEqual(second["answer"], first["answer"])
		self.assertEqual(second["citations"], first["citations"])

		# The interface renders the stream, not the return value, so a hit has to emit the
		# same route -> citations -> answer -> done sequence a live answer does.
		messages = published
		self.assertTrue(any("route" in message for message in messages))
		self.assertTrue(any("citations" in message for message in messages))
		self.assertTrue(any(message.get("delta") for message in messages))
		self.assertTrue(any(message.get("done") for message in messages))

	def test_a_refusal_is_not_stored(self):
		"""A refusal can come from a transient failure — a missing key, a reranker
		returning a flat verdict — not from a fact about the corpus. Caching one would keep
		answering "I couldn't find this" for a day after the cause was fixed."""
		frappe.cache().delete_value(key_for())

		refusal = {**cached_answer("I couldn't find this in the wiki."), "refused": True, "citations": []}
		self.ask_once(refusal)

		_, answered_again, _ = self.ask_once(refusal)
		self.assertEqual(answered_again.call_count, 1, "a refusal must be re-asked, not replayed")
