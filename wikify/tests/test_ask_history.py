# Copyright (c) 2026, BWH and contributors
# For license information, please see license.txt

"""Ask conversation persistence + per-ask cost.

Three things are worth asserting here: that one ask lands as a question/answer pair a
later eval query can rejoin on `turn`, that the logged cost is the figure the ask itself
reported and that a conversation totals its own turns, and that one user's conversation
is invisible to another — which is enforced by the DocType's `if_owner` permission, not
by a hand-rolled filter, so it has to be exercised as a real second user.

What the ask *costs* is measured in `rag.usage` and asserted in `test_ask_cost`.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from wikify.api import ask_history as api_ask_history
from wikify.rag import history

OTHER_USER = "ask-history-other@example.com"

# One ask's three legs — routing, rerank, synthesis — as `answer()` reports them.
ALL_LEGS_COST = 0.004185
ALL_LEGS_PROMPT_TOKENS = 7900
ALL_LEGS_COMPLETION_TOKENS = 710


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
	cost: float = ALL_LEGS_COST,
	prompt_tokens: int = ALL_LEGS_PROMPT_TOKENS,
	completion_tokens: int = ALL_LEGS_COMPLETION_TOKENS,
) -> dict:
	"""The dict `rag.answer.answer()` returns."""
	return {
		"cost": cost,
		"prompt_tokens": prompt_tokens,
		"completion_tokens": completion_tokens,
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
		session = history.record_turn(None, "List every job description", make_result(), project=self.project)
		conversation = frappe.get_doc("Wikify Ask Session", session)
		self.assertEqual(conversation.title, "List every job description")
		self.assertEqual(conversation.project, self.project)
		self.assertEqual(conversation.user, frappe.session.user)
		self.assertEqual(conversation.message_count, 2)
		self.assertIsNotNone(conversation.started_at)

	def test_turn_number_pairs_the_question_with_its_answer(self):
		session = history.record_turn(None, "First question", make_result())
		history.record_turn(session, "Second question", make_result())

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
		session = history.record_turn(None, "List every job description", make_result(citations=citations))

		answer_row = history.get_session(session)["messages"][1]
		self.assertEqual(answer_row["route_intent"], "exhaustive")
		self.assertEqual(answer_row["route_reason"], "You asked for all of a kind of section.")
		self.assertEqual(answer_row["citations"], citations)
		self.assertEqual(answer_row["refused"], 0)

	def test_unknown_section_type_is_dropped_not_raised(self):
		"""A type deleted since routing must not cost a turn that was already answered."""
		session = history.record_turn(None, "Which roles?", make_result(section_type="no_such_type_here"))
		self.assertIsNone(history.get_session(session)["messages"][1]["route_section_type"])

	def test_refused_turn_is_logged_with_no_citations(self):
		session = history.record_turn(
			None, "Who won the cup?", make_result("I couldn't find this.", refused=True)
		)
		answer_row = history.get_session(session)["messages"][1]
		self.assertEqual(answer_row["refused"], 1)
		self.assertEqual(answer_row["citations"], [])

	def test_a_stale_session_id_opens_a_fresh_conversation(self):
		"""ask() may still hold the id of a conversation the user has since deleted."""
		session = history.record_turn("ASK-2026-99999", "List every job description", make_result())
		self.assertNotEqual(session, "ASK-2026-99999")
		self.assertTrue(frappe.db.exists("Wikify Ask Session", session))

	def test_recent_turns_replay_as_router_history(self):
		session = history.record_turn(None, "First question", make_result("First answer."))
		history.record_turn(session, "Second question", make_result("Second answer."))

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
		session = history.record_turn(None, "List every job description", make_result())
		history.delete_session(session)
		self.assertEqual(frappe.db.count("Wikify Ask Message", {"session": session}), 0)


class TestAskHistoryCost(FrappeTestCase):
	def test_the_logged_cost_is_the_one_the_ask_reported(self):
		"""The row and the answer must carry the same figure — they once differed by 13x."""
		result = make_result()
		session = history.record_turn(None, "First question", result)

		question_row, answer_row = history.get_session(session)["messages"][:2]
		self.assertEqual(question_row["cost"], 0)
		self.assertAlmostEqual(answer_row["cost"], result["cost"])
		self.assertEqual(
			(answer_row["prompt_tokens"], answer_row["completion_tokens"]),
			(result["prompt_tokens"], result["completion_tokens"]),
		)

	def test_a_conversation_totals_exactly_its_own_turns(self):
		session = history.record_turn(None, "First question", make_result())
		history.record_turn(
			session,
			"Second question",
			make_result(cost=0.000021, prompt_tokens=900, completion_tokens=60),
		)

		conversation = frappe.get_doc("Wikify Ask Session", session)
		messages = history.get_session(session)["messages"]
		self.assertAlmostEqual(conversation.total_cost, sum(row["cost"] for row in messages))
		self.assertEqual(
			conversation.total_tokens,
			sum(row["prompt_tokens"] + row["completion_tokens"] for row in messages),
		)
		self.assertEqual(conversation.message_count, len(messages))

	def test_a_drifted_total_heals_on_the_next_turn(self):
		"""Totals are summed, not incremented, so a legacy over-counted row corrects itself."""
		session = history.record_turn(None, "First question", make_result())
		frappe.db.set_value("Wikify Ask Session", session, "total_cost", 9.99)

		history.record_turn(session, "Second question", make_result())

		conversation = frappe.get_doc("Wikify Ask Session", session)
		self.assertAlmostEqual(conversation.total_cost, ALL_LEGS_COST * 2)


class TestAskHistoryPermissions(FrappeTestCase):
	def setUp(self):
		self.other_user = make_user(OTHER_USER)
		self.session = history.record_turn(None, "Administrator's question", make_result())
		self.addCleanup(frappe.set_user, "Administrator")

	def test_another_user_cannot_list_or_read_the_conversation(self):
		frappe.set_user(self.other_user)
		self.assertNotIn(self.session, [row.name for row in history.list_sessions()])
		with self.assertRaises(frappe.PermissionError):
			history.get_session(self.session)

	def test_another_user_cannot_append_to_the_conversation(self):
		frappe.set_user(self.other_user)
		with self.assertRaises(frappe.PermissionError):
			history.record_turn(self.session, "Sneaking in", make_result())

	def test_a_user_sees_their_own_conversation(self):
		frappe.set_user(self.other_user)
		own_session = history.record_turn(
			None, "My own question", make_result(cost=0.000021, prompt_tokens=900, completion_tokens=60)
		)

		listed = [row.name for row in history.list_sessions()]
		self.assertIn(own_session, listed)
		self.assertNotIn(self.session, listed)
		self.assertEqual(history.get_session(own_session)["title"], "My own question")

	def test_system_manager_sees_every_conversation(self):
		frappe.set_user(self.other_user)
		own_session = history.record_turn(
			None, "My own question", make_result(cost=0.000021, prompt_tokens=900, completion_tokens=60)
		)

		frappe.set_user("Administrator")
		listed = {row.name for row in history.list_sessions()}
		self.assertIn(own_session, listed)
		self.assertIn(self.session, listed)


class TestAskHistoryApi(FrappeTestCase):
	def test_api_returns_the_conversation_and_its_turns(self):
		project = make_project()
		session = history.record_turn(None, "List every job description", make_result(), project=project)

		listed = api_ask_history.list_sessions(project=project)
		self.assertEqual([row.name for row in listed], [session])
		self.assertAlmostEqual(listed[0].total_cost, 0.004185)

		loaded = api_ask_history.get_session(session)
		self.assertEqual(len(loaded["messages"]), 2)
		self.assertEqual(loaded["messages"][1]["route_intent"], "exhaustive")

		api_ask_history.delete_session(session)
		self.assertFalse(frappe.db.exists("Wikify Ask Session", session))


class TestAskHistorySessionIdentity(FrappeTestCase):
	"""`stream` and `session` are different identifiers and must stay that way.

	They shared one name until 0.7: the interface minted a per-ask correlation token and
	sent it as the conversation docname. Every ask therefore opened a new conversation,
	history never replayed, and `recent_turns` loaded a document that had never existed —
	which raises for a normal user and is short-circuited for Administrator, so these run
	as a normal user on purpose.
	"""

	def setUp(self):
		self.other_user = make_user(OTHER_USER)
		self.addCleanup(frappe.set_user, "Administrator")

	def test_a_correlation_token_replays_as_no_history_for_a_normal_user(self):
		frappe.set_user(self.other_user)
		self.assertEqual(history.recent_turns("rag-1786000000000-a1b2c3"), [])

	def test_an_unknown_session_replays_as_no_history_for_administrator_too(self):
		self.assertEqual(history.recent_turns("ASK-2026-99999"), [])

	def test_handing_the_returned_session_back_continues_one_conversation(self):
		frappe.set_user(self.other_user)
		first = history.record_turn(None, "Which roles are listed?", make_result("Five roles."))
		second = history.record_turn(first, "And their pay bands?", make_result("Bands 3 to 7."))

		self.assertEqual(second, first)
		self.assertEqual(
			[turn["content"] for turn in history.recent_turns(second)],
			["Which roles are listed?", "Five roles.", "And their pay bands?", "Bands 3 to 7."],
		)
