"""Conversation persistence + per-ask cost for the Ask surface.

Facade over `Wikify Ask Session` + `Wikify Ask Message`, mirroring `agent/session.py`:
turns are standalone rows rather than a child table, so a turn is appended without
rewriting the parent.

The log is deliberately more than display state. Every answer row keeps the route the
question took *and* the citations the answer stood on, so the accumulated conversations
double as a free eval set — "which questions did we route wrong", "which sources do
answers actually rest on" — which is why `route_intent` is indexed and why a `turn`
integer pairs a question row with its answer row instead of leaving that to creation order.

What a turn cost is not measured here. `rag.usage` accumulates all three legs of the ask
— routing, rerank and synthesis — and `answer()` returns that total, so the figure logged
is character-for-character the one the reader was shown. A second, independently derived
total is how the log and the answer came to disagree by 13x.
"""

from __future__ import annotations

import json

import frappe
from frappe import _
from frappe.query_builder.functions import Count, Sum
from frappe.utils.data import cint, flt

from wikify.agent import llm as agent_llm
from wikify.agent.session import session_title

# How many prior turns the router replays to rewrite a follow-up question.
HISTORY_TURNS = 10

# Newest-first page of conversations for the sidebar.
SESSION_PAGE_LENGTH = 50


def start_session(project: str | None = None, title: str | None = None) -> str:
	"""Open a conversation owned by the current user and return its name."""
	if project and not frappe.has_permission("Wikify Project", doc=project):
		frappe.throw(_("You are not allowed to read {0}.").format(project), frappe.PermissionError)

	session = frappe.new_doc("Wikify Ask Session")
	session.project = project
	session.title = session_title(title) or None
	session.insert()
	return session.name


def record_turn(
	session: str | None,
	question: str,
	result: dict,
	*,
	project: str | None = None,
	took_ms: int | None = None,
	model: str | None = None,
) -> str:
	"""Persist one ask — the question, the answer, its route, citations and cost.

	`result` is the dict `rag.answer.answer()` returns, cost and tokens included. Opens a
	conversation when `session` is empty — or names one that no longer exists, the same
	tolerance `agent.session.get_or_create` has — and returns the conversation name either
	way, so the caller can hand it back to the interface as one line at the end of `ask()`.
	"""
	if not session or not frappe.db.exists("Wikify Ask Session", session):
		session = start_session(project=project, title=question)
	conversation = frappe.get_doc("Wikify Ask Session", session)
	conversation.check_permission("write")

	route = result.get("route") or {}
	refused = bool(result.get("refused"))
	section_type = route.get("section_type")
	# A type deleted between routing and logging must not cost the user a turn that was
	# already answered and streamed, so an unknown one is dropped rather than raised —
	# the router degrades on an unknown type the same way.
	if section_type and not frappe.db.exists("Section Type", section_type):
		section_type = None
	prompt_tokens = cint(result.get("prompt_tokens"))
	completion_tokens = cint(result.get("completion_tokens"))
	cost = flt(result.get("cost"), 6)
	turn = cint(frappe.db.count("Wikify Ask Message", {"session": session, "role": "question"})) + 1

	append_message(session, "question", question, turn=turn)
	append_message(
		session,
		"answer",
		result.get("answer") or "",
		turn=turn,
		route_intent=route.get("intent"),
		route_section_type=section_type,
		route_reason=route.get("reason"),
		citations=result.get("citations") or [],
		refused=refused,
		took_ms=cint(took_ms),
		model=model or agent_llm.resolve_model(project=conversation.project),
		prompt_tokens=prompt_tokens,
		completion_tokens=completion_tokens,
		cost=cost,
	)

	conversation.title = conversation.title or session_title(question)
	set_totals(conversation)
	conversation.save()
	return session


def set_totals(conversation) -> None:
	"""Re-total a conversation from its own turns.

	Summed rather than incremented so the header cannot drift from the rows it claims to
	total — a deleted turn, a failed insert or a legacy row totalled by the old
	double-counting cost path all heal on the next turn.
	"""
	message = frappe.qb.DocType("Wikify Ask Message")
	totals = (
		frappe.qb.from_(message)
		.select(
			Count(message.name).as_("messages"),
			Sum(message.cost).as_("cost"),
			Sum(message.prompt_tokens).as_("prompt_tokens"),
			Sum(message.completion_tokens).as_("completion_tokens"),
		)
		.where(message.session == conversation.name)
		.run(as_dict=True)[0]
	)
	conversation.message_count = cint(totals.messages)
	conversation.total_tokens = cint(totals.prompt_tokens) + cint(totals.completion_tokens)
	conversation.total_cost = flt(totals.cost, 6)


def append_message(session: str, role: str, content: str, **values) -> str:
	"""Insert one `Wikify Ask Message` row and return its name."""
	if values.get("citations") is not None:
		values["citations"] = json.dumps(values["citations"])
	message = frappe.get_doc(
		{"doctype": "Wikify Ask Message", "session": session, "role": role, "content": content, **values}
	)
	message.insert()
	return message.name


def list_sessions(project: str | None = None, limit: int = SESSION_PAGE_LENGTH) -> list[dict]:
	"""Conversations the current user may read, newest first.

	`get_list` (not `get_all`) so the DocType's `if_owner` permission does the scoping —
	a user sees their own, a System Manager sees every one.
	"""
	filters = {"project": project} if project else None
	return frappe.get_list(
		"Wikify Ask Session",
		filters=filters,
		fields=[
			"name",
			"title",
			"user",
			"project",
			"started_at",
			"message_count",
			"total_tokens",
			"total_cost",
			"modified",
		],
		order_by="modified desc",
		limit_page_length=cint(limit),
	)


def get_session(name: str | int) -> dict:
	"""One conversation with its turns in order."""
	conversation = frappe.get_doc("Wikify Ask Session", name)
	conversation.check_permission("read")
	# Read permission on the turns is derived from the parent, which was just checked.
	messages = frappe.get_all(
		"Wikify Ask Message",
		filters={"session": conversation.name},
		fields=[
			"name",
			"role",
			"turn",
			"content",
			"route_intent",
			"route_section_type",
			"route_reason",
			"citations",
			"refused",
			"took_ms",
			"model",
			"prompt_tokens",
			"completion_tokens",
			"cost",
			"creation",
		],
		order_by="creation asc",
	)
	for message in messages:
		message["citations"] = frappe.parse_json(message["citations"]) or []
	return {**conversation.as_dict(), "messages": messages}


def recent_turns(session: str | int | None) -> list[dict]:
	"""Prior turns as router-shaped messages, so a follow-up question can be rewritten.

	A forged session id must not feed someone else's conversation into the router prompt,
	so an unreadable one replays as no history rather than throwing a user's follow-up away.

	The existence check has to come first: `has_permission` loads the document, so a name
	that was never a conversation raises `DoesNotExistError` at the caller instead of
	replaying empty. Administrator short-circuits the permission check and never saw it.
	"""
	if not session or not frappe.db.exists("Wikify Ask Session", session):
		return []
	if not frappe.has_permission("Wikify Ask Session", doc=session):
		return []
	messages = frappe.get_all(
		"Wikify Ask Message",
		filters={"session": session},
		fields=["role", "content"],
		order_by="creation desc",
		limit=HISTORY_TURNS * 2,
	)
	roles = {"question": "user", "answer": "assistant"}
	return [{"role": roles[row.role], "content": row.content or ""} for row in reversed(messages)]


def delete_session(name: str | int) -> None:
	"""Delete a conversation and, via its `on_trash`, every turn in it."""
	frappe.delete_doc("Wikify Ask Session", name)
