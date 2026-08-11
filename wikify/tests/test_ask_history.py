# Copyright (c) 2026, BWH and contributors
# For license information, please see license.txt

"""Ask conversation persistence + per-ask cost.

Three things are worth asserting here: that one ask lands as a question/answer pair a
later eval query can rejoin on `turn`, that the cost figure is the sum of all three LLM
legs rather than synthesis alone, and that one user's conversation is invisible to
another — which is enforced by the DocType's `if_owner` permission, not by a hand-rolled
filter, so it has to be exercised as a real second user.
"""

from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from wikify.api import ask_history as api_ask_history
from wikify.engine import llm as engine_llm
from wikify.engine.store import cost_of
from wikify.rag import history

OTHER_USER = "ask-history-other@example.com"

ROUTE_METRIC = {"label": "rag_route", "cost": 0.000021, "prompt_tokens": 900, "completion_tokens": 60}
RERANK_METRIC = {"label": "rag_rerank", "cost": 0.000044, "prompt_tokens": 1800, "completion_tokens": 40}
SYNTHESIS_METRIC = {
	"label": "synthesis",
	"cost": 0.004120,
	"prompt_tokens": 5200,
	"completion_tokens": 610,
}
ALL_LEGS = [ROUTE_METRIC, RERANK_METRIC, SYNTHESIS_METRIC]


def make_project(label: str = "Ask History") -> str:
	"""A uniquely-named project — `project_name` is unique and tests share a database."""
	return (
		frappe.get_doc(
			{"doctype": "Wikify Project", "project_name": f"{label} {frappe.generate_hash(length=6)}"}
		)
		.insert()
		.name
	)


def make_result(
	answer: str = "There are five job descriptions [1].",
	*,
	intent: str = "exhaustive",
	section_type: str | None = None,
	refused: bool = False,
	citations: list | None = None,
) -> dict:
	"""The dict `rag.answer.answer()` returns."""
	return {
		"answer": answer,
		"citations": []
		if refused
		else (citations or [{"chunk_id": "sec-a::0", "title": "Backend Engineer"}]),
		"route": {
			"intent": intent,
			"section_type": section_type,
			"query": "list every job description",
			"reason": "You asked for all of a kind of section.",
		},
		"refused": refused,
	}


def make_user(email: str) -> str:
	if not frappe.db.exists("User", email):
		user = frappe.get_doc(
			{"doctype": "User", "email": email, "first_name": "Ask Tester", "send_welcome_email": 0}
		).insert(ignore_permissions=True)
		user.add_roles("Wiki Approver")
	return email


class TestAskHistoryTurns(FrappeTestCase):
	def setUp(self):
		self.project = make_project()

	def test_first_turn_opens_a_titled_conversation(self):
		session = history.record_turn(
			None, "List every job description", make_result(), project=self.project, llm_metrics=ALL_LEGS
		)
		conversation = frappe.get_doc("Wikify Ask Session", session)
		self.assertEqual(conversation.title, "List every job description")
		self.assertEqual(conversation.project, self.project)
		self.assertEqual(conversation.user, frappe.session.user)
		self.assertEqual(conversation.message_count, 2)
		self.assertIsNotNone(conversation.started_at)

	def test_turn_number_pairs_the_question_with_its_answer(self):
		session = history.record_turn(None, "First question", make_result(), llm_metrics=ALL_LEGS)
		history.record_turn(session, "Second question", make_result(), llm_metrics=ALL_LEGS)

		rows = frappe.get_all(
			"Wikify Ask Message",
			filters={"session": session},
			fields=["role", "turn", "content"],
			order_by="creation asc",
		)
		self.assertEqual(
			[(row.role, row.turn) for row in rows],
			[("question", 1), ("answer", 1), ("question", 2), ("answer", 2)],
		)
		self.assertEqual(rows[2].content, "Second question")

	def test_answer_row_keeps_the_route_and_the_citations_it_stood_on(self):
		citations = [{"chunk_id": "sec-a::0", "title": "Backend Engineer", "wiki_route": "/demo/roles"}]
		session = history.record_turn(
			None, "List every job description", make_result(citations=citations), llm_metrics=ALL_LEGS
		)

		answer_row = history.get_session(session)["messages"][1]
		self.assertEqual(answer_row["route_intent"], "exhaustive")
		self.assertEqual(answer_row["route_reason"], "You asked for all of a kind of section.")
		self.assertEqual(answer_row["citations"], citations)
		self.assertEqual(answer_row["refused"], 0)

	def test_unknown_section_type_is_dropped_not_raised(self):
		"""A type deleted since routing must not cost a turn that was already answered."""
		session = history.record_turn(
			None, "Which roles?", make_result(section_type="no_such_type_here"), llm_metrics=ALL_LEGS
		)
		self.assertIsNone(history.get_session(session)["messages"][1]["route_section_type"])

	def test_refused_turn_is_logged_with_no_citations(self):
		session = history.record_turn(
			None, "Who won the cup?", make_result("I couldn't find this.", refused=True), llm_metrics=[]
		)
		answer_row = history.get_session(session)["messages"][1]
		self.assertEqual(answer_row["refused"], 1)
		self.assertEqual(answer_row["citations"], [])

	def test_a_stale_session_id_opens_a_fresh_conversation(self):
		"""ask() may still hold the id of a conversation the user has since deleted."""
		session = history.record_turn(
			"ASK-2026-99999", "List every job description", make_result(), llm_metrics=ALL_LEGS
		)
		self.assertNotEqual(session, "ASK-2026-99999")
		self.assertTrue(frappe.db.exists("Wikify Ask Session", session))

	def test_recent_turns_replay_as_router_history(self):
		session = history.record_turn(None, "First question", make_result("First answer."), llm_metrics=[])
		history.record_turn(session, "Second question", make_result("Second answer."), llm_metrics=[])

		self.assertEqual(
			history.recent_turns(session),
			[
				{"role": "user", "content": "First question"},
				{"role": "assistant", "content": "First answer."},
				{"role": "user", "content": "Second question"},
				{"role": "assistant", "content": "Second answer."},
			],
		)

	def test_deleting_a_conversation_removes_its_turns(self):
		session = history.record_turn(None, "List every job description", make_result(), llm_metrics=ALL_LEGS)
		history.delete_session(session)
		self.assertEqual(frappe.db.count("Wikify Ask Message", {"session": session}), 0)


class TestAskHistoryCost(FrappeTestCase):
	def test_turn_metrics_sums_routing_rerank_and_synthesis(self):
		"""The user wants cost per ask, so all three legs land in one figure."""
		engine_calls: list[dict] = []
		with patch.object(engine_llm, "get_metrics", side_effect=lambda: list(engine_calls)):
			history.start_turn()
			engine_calls.extend([ROUTE_METRIC, RERANK_METRIC])
			history.record_litellm_metrics(
				{"model": "claude-sonnet-4.6", "response_cost": SYNTHESIS_METRIC["cost"]},
				SimpleNamespace(usage=SimpleNamespace(prompt_tokens=5200, completion_tokens=610)),
				None,
				None,
			)
			metrics = history.turn_metrics()

		self.assertEqual([entry.get("label") for entry in metrics], ["rag_route", "rag_rerank", "synthesis"])
		self.assertAlmostEqual(cost_of(metrics), 0.004185)
		self.assertEqual(history.tokens_of(metrics), (7900, 710))

	def test_watermark_excludes_a_previous_ask(self):
		engine_calls = [ROUTE_METRIC]
		with patch.object(engine_llm, "get_metrics", side_effect=lambda: list(engine_calls)):
			history.start_turn()
			engine_calls.append(RERANK_METRIC)
			history.record_litellm_metrics({"response_cost": 0.001}, SimpleNamespace(usage=None), None, None)
			metrics = history.turn_metrics()

		self.assertEqual([entry.get("label") for entry in metrics], ["rag_rerank", "synthesis"])

	def test_cost_lands_on_the_answer_row_and_rolls_up(self):
		session = history.record_turn(None, "First question", make_result(), llm_metrics=ALL_LEGS)
		history.record_turn(session, "Second question", make_result(), llm_metrics=[ROUTE_METRIC])

		question_row, answer_row = history.get_session(session)["messages"][:2]
		self.assertEqual(question_row["cost"], 0)
		self.assertAlmostEqual(answer_row["cost"], 0.004185)
		self.assertEqual((answer_row["prompt_tokens"], answer_row["completion_tokens"]), (7900, 710))

		conversation = frappe.get_doc("Wikify Ask Session", session)
		self.assertAlmostEqual(conversation.total_cost, 0.004206)
		self.assertEqual(conversation.total_tokens, 9570)
		self.assertEqual(conversation.message_count, 4)

	def test_a_refused_turn_does_not_wait_for_synthesis_usage(self):
		"""Nothing synthesises on a refusal, so the grace wait must be skipped."""
		with patch.object(engine_llm, "get_metrics", return_value=[]):
			history.start_turn()
			with patch.object(history, "wait_for_synthesis_metrics") as waited:
				history.record_turn(None, "Who won the cup?", make_result(refused=True))
		waited.assert_not_called()


class TestAskHistoryPermissions(FrappeTestCase):
	def setUp(self):
		self.other_user = make_user(OTHER_USER)
		self.session = history.record_turn(
			None, "Administrator's question", make_result(), llm_metrics=ALL_LEGS
		)
		self.addCleanup(frappe.set_user, "Administrator")

	def test_another_user_cannot_list_or_read_the_conversation(self):
		frappe.set_user(self.other_user)
		self.assertNotIn(self.session, [row.name for row in history.list_sessions()])
		with self.assertRaises(frappe.PermissionError):
			history.get_session(self.session)

	def test_another_user_cannot_append_to_the_conversation(self):
		frappe.set_user(self.other_user)
		with self.assertRaises(frappe.PermissionError):
			history.record_turn(self.session, "Sneaking in", make_result(), llm_metrics=[])

	def test_a_user_sees_their_own_conversation(self):
		frappe.set_user(self.other_user)
		own_session = history.record_turn(None, "My own question", make_result(), llm_metrics=[ROUTE_METRIC])

		listed = [row.name for row in history.list_sessions()]
		self.assertIn(own_session, listed)
		self.assertNotIn(self.session, listed)
		self.assertEqual(history.get_session(own_session)["title"], "My own question")

	def test_system_manager_sees_every_conversation(self):
		frappe.set_user(self.other_user)
		own_session = history.record_turn(None, "My own question", make_result(), llm_metrics=[ROUTE_METRIC])

		frappe.set_user("Administrator")
		listed = {row.name for row in history.list_sessions()}
		self.assertIn(own_session, listed)
		self.assertIn(self.session, listed)


class TestAskHistoryApi(FrappeTestCase):
	def test_api_returns_the_conversation_and_its_turns(self):
		project = make_project()
		session = history.record_turn(
			None, "List every job description", make_result(), project=project, llm_metrics=ALL_LEGS
		)

		listed = api_ask_history.list_sessions(project=project)
		self.assertEqual([row.name for row in listed], [session])
		self.assertAlmostEqual(listed[0].total_cost, 0.004185)

		loaded = api_ask_history.get_session(session)
		self.assertEqual(len(loaded["messages"]), 2)
		self.assertEqual(loaded["messages"][1]["route_intent"], "exhaustive")

		api_ask_history.delete_session(session)
		self.assertFalse(frappe.db.exists("Wikify Ask Session", session))
