from __future__ import annotations

import json

import frappe
from frappe import _
from frappe.query_builder.functions import Count, Sum
from frappe.utils.data import cint, flt

from wikify.agent import llm as agent_llm
from wikify.agent.session import session_title

HISTORY_TURNS = 10

SESSION_PAGE_LENGTH = 50


def start_session(project: str | None = None, title: str | None = None) -> str:
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
	if not session or not frappe.db.exists("Wikify Ask Session", session):
		session = start_session(project=project, title=question)
	conversation = frappe.get_doc("Wikify Ask Session", session)
	conversation.check_permission("write")

	route = result.get("route") or {}
	refused = bool(result.get("refused"))
	section_type = route.get("section_type")
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
	if values.get("citations") is not None:
		values["citations"] = json.dumps(values["citations"])
	message = frappe.get_doc(
		{"doctype": "Wikify Ask Message", "session": session, "role": role, "content": content, **values}
	)
	message.insert(ignore_permissions=True)
	return message.name


def list_sessions(project: str | None = None, limit: int = SESSION_PAGE_LENGTH) -> list[dict]:
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
	conversation = frappe.get_doc("Wikify Ask Session", name)
	conversation.check_permission("read")
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
	frappe.delete_doc("Wikify Ask Session", name)
