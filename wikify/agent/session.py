"""Facade over the two agent DocTypes — `Wikify Agent Session` + `Wikify Agent Message`.

Messages are standalone rows (not a child table), so the loop appends cheaply and one
row streams without rewriting the parent — same reasoning as `Import Log Entry`.
"""

from __future__ import annotations

import json

import frappe
from frappe.utils import now_datetime

# How many prior messages to replay as context each turn.
HISTORY_LIMIT = 40

# Both session doctypes cap `title` at 140; 120 leaves the trimmed line visibly short of it.
TITLE_CHARS = 120


def session_title(first_message: str | None) -> str:
	"""A conversation's title: the first line of its opening message, trimmed.

	Shared with the Ask surface (`rag.history`) so the two conversation lists cannot title
	themselves differently.
	"""
	lines = (first_message or "").strip().splitlines()
	return lines[0][:TITLE_CHARS] if lines else ""


def get_or_create(
	session_id: str | None,
	*,
	user: str,
	scope: str = "global",
	project: str | None = None,
	source_document: str | None = None,
	model: str | None = None,
):
	"""Return an existing session (by id) or create a fresh one for this user."""
	if session_id and frappe.db.exists("Wikify Agent Session", session_id):
		return frappe.get_doc("Wikify Agent Session", session_id)

	doc = frappe.new_doc("Wikify Agent Session")
	doc.user = user
	doc.scope = scope or "global"
	doc.project = project
	doc.source_document = source_document
	doc.model = model
	doc.status = "Active"
	doc.last_interaction_on = now_datetime()
	doc.insert(ignore_permissions=True)
	return doc


def append_message(
	session: str,
	role: str,
	content: str = "",
	*,
	status: str = "done",
	tool_calls: list | None = None,
	tool_name: str | None = None,
	tool_call_id: str | None = None,
	attachments: list | None = None,
	metadata: dict | None = None,
):
	"""Insert a `Wikify Agent Message` row and return it."""
	doc = frappe.get_doc(
		{
			"doctype": "Wikify Agent Message",
			"session": session,
			"role": role,
			"content": content,
			"status": status,
			"tool_calls": json.dumps(tool_calls) if tool_calls else None,
			"tool_name": tool_name,
			"tool_call_id": tool_call_id,
			"attachments_json": json.dumps(attachments) if attachments else None,
			"metadata_json": json.dumps(metadata) if metadata else None,
		}
	)
	doc.insert(ignore_permissions=True)
	return doc


def update_message(name: str, **values) -> None:
	"""Patch a message row (e.g. finalize a streamed assistant turn)."""
	if "tool_calls" in values and values["tool_calls"] is not None:
		values["tool_calls"] = json.dumps(values["tool_calls"])
	if "metadata" in values:
		values["metadata_json"] = json.dumps(values.pop("metadata"))
	frappe.db.set_value("Wikify Agent Message", name, values)


def history_messages(session: str) -> list[dict]:
	"""Replay persisted messages as OpenAI-format messages for the next completion."""
	rows = frappe.get_all(
		"Wikify Agent Message",
		filters={"session": session},
		fields=["role", "content", "tool_calls", "tool_name", "tool_call_id", "status"],
		order_by="creation asc",
		limit=HISTORY_LIMIT,
	)
	messages: list[dict] = []
	for r in rows:
		if r.status in ("error", "clarification"):
			# Clarifications/errors aren't replayed as model turns.
			if r.role == "user":
				messages.append({"role": "user", "content": r.content or ""})
			continue
		if r.role == "user":
			messages.append({"role": "user", "content": r.content or ""})
		elif r.role == "assistant":
			msg: dict = {"role": "assistant", "content": r.content or ""}
			if r.tool_calls:
				calls = json.loads(r.tool_calls)
				msg["tool_calls"] = [
					{
						"id": c["id"],
						"type": "function",
						"function": {"name": c["name"], "arguments": json.dumps(c.get("args", {}))},
					}
					for c in calls
				]
			messages.append(msg)
		elif r.role == "tool":
			messages.append(
				{"role": "tool", "tool_call_id": r.tool_call_id or "", "content": r.content or ""}
			)
	return messages


def set_running(session: str, value: bool) -> None:
	frappe.db.set_value("Wikify Agent Session", session, "is_running", 1 if value else 0)
	frappe.db.commit()


def touch(session: str, *, first_user_message: str | None = None) -> None:
	"""Bump `last_interaction_on`; set a title from the first user message if unset."""
	values = {"last_interaction_on": now_datetime()}
	title = session_title(first_user_message)
	if title and not frappe.db.get_value("Wikify Agent Session", session, "title"):
		values["title"] = title
	frappe.db.set_value("Wikify Agent Session", session, values)
	frappe.db.commit()
