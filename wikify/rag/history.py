"""Conversation persistence + per-ask cost for the Ask surface.

Facade over `Wikify Ask Session` + `Wikify Ask Message`, mirroring `agent/session.py`:
turns are standalone rows rather than a child table, so a turn is appended without
rewriting the parent.

The log is deliberately more than display state. Every answer row keeps the route the
question took *and* the citations the answer stood on, so the accumulated conversations
double as a free eval set — "which questions did we route wrong", "which sources do
answers actually rest on" — which is why `route_intent` is indexed and why a `turn`
integer pairs a question row with its answer row instead of leaving that to creation order.

Cost is summed across all three legs of one ask:

- **routing** and **rerank** go through `engine.llm.chat_completion`, which already records
  cost + tokens into its own metrics buffer — read here by watermark rather than by
  `reset_metrics()`, because a parse job sharing the process would lose its buffer.
- **synthesis** goes through litellm (`agent/llm.complete_with_tools`), which reports usage
  only through a success callback; `add_litellm_metrics_callback` registers one.
"""

from __future__ import annotations

import json
import threading
import time

import frappe
from frappe import _
from frappe.utils.data import cint, flt

from wikify.agent import llm as agent_llm
from wikify.engine import llm as engine_llm
from wikify.engine.store import cost_of

# How many prior turns the router replays to rewrite a follow-up question.
HISTORY_TURNS = 10

# Newest-first page of conversations for the sidebar.
SESSION_PAGE_LENGTH = 50

# litellm hands a streamed completion to its success callback from a thread pool
# (`executor.submit(self.logging_obj.success_handler, ...)` at end-of-stream), so the
# synthesis usage can land a few milliseconds after `answer()` has already returned.
# The answer is fully streamed to the user by then, so waiting briefly for it costs the
# reader nothing and is the difference between logging the whole ask and logging only
# its two cheap legs.
SYNTHESIS_GRACE_SECONDS = 0.3
SYNTHESIS_POLL_SECONDS = 0.02

_metrics_lock = threading.Lock()
_litellm_metrics: list[dict] = []
_callback_registered = False

# Watermarks into both buffers, per thread, taken by `start_turn()`.
_watermarks: dict[int, tuple[int, int]] = {}


# --- cost capture -------------------------------------------------------------------


def add_litellm_metrics_callback() -> None:
	"""Register the litellm success callback once, so synthesis reports cost + tokens."""
	global _callback_registered
	if _callback_registered:
		return

	import litellm

	litellm.success_callback = [*litellm.success_callback, record_litellm_metrics]
	_callback_registered = True


def record_litellm_metrics(kwargs, response, start_time, end_time) -> None:
	"""litellm success callback — mirrors an `engine.llm` metrics entry.

	Never raise: litellm swallows callback errors, but a broken callback would still cost
	every agent completion a traceback in the logs.
	"""
	try:
		usage = getattr(response, "usage", None)
		with _metrics_lock:
			_litellm_metrics.append(
				{
					"label": "synthesis",
					"model": kwargs.get("model"),
					"cost": kwargs.get("response_cost"),
					"prompt_tokens": getattr(usage, "prompt_tokens", None),
					"completion_tokens": getattr(usage, "completion_tokens", None),
				}
			)
	except Exception:
		frappe.log_error(title="Wikify ask cost callback failed")


def start_turn() -> None:
	"""Mark where this ask's LLM calls begin. Call immediately before `answer()`."""
	add_litellm_metrics_callback()
	with _metrics_lock:
		# ponytail: both buffers are process-wide, so a second ask running concurrently in
		# the same worker can inflate this turn's cost; give engine.llm a per-turn buffer
		# if per-ask cost ever has to be billable rather than indicative.
		_watermarks[threading.get_ident()] = (len(engine_llm.get_metrics()), len(_litellm_metrics))


def turn_metrics(*, expect_synthesis: bool = True) -> list[dict]:
	"""Every LLM call recorded since `start_turn()` — routing, rerank and synthesis."""
	engine_mark, litellm_mark = _watermarks.pop(threading.get_ident(), (0, 0))
	if expect_synthesis:
		wait_for_synthesis_metrics(litellm_mark)
	with _metrics_lock:
		synthesis = _litellm_metrics[litellm_mark:]
		del _litellm_metrics[litellm_mark:]
	return engine_llm.get_metrics()[engine_mark:] + synthesis


def wait_for_synthesis_metrics(litellm_mark: int) -> None:
	deadline = time.monotonic() + SYNTHESIS_GRACE_SECONDS
	while time.monotonic() < deadline:
		with _metrics_lock:
			if len(_litellm_metrics) > litellm_mark:
				return
		time.sleep(SYNTHESIS_POLL_SECONDS)


def tokens_of(metrics: list[dict]) -> tuple[int, int]:
	"""Prompt and completion tokens across a metrics buffer."""
	prompt = sum(cint(entry.get("prompt_tokens")) for entry in metrics)
	completion = sum(cint(entry.get("completion_tokens")) for entry in metrics)
	return prompt, completion


# --- conversations ------------------------------------------------------------------


def start_session(project: str | None = None, title: str | None = None) -> str:
	"""Open a conversation owned by the current user and return its name."""
	if project and not frappe.has_permission("Wikify Project", doc=project):
		frappe.throw(_("You are not allowed to read {0}.").format(project), frappe.PermissionError)

	session = frappe.new_doc("Wikify Ask Session")
	session.project = project
	session.title = summarize(title) if title else None
	session.insert()
	return session.name


def summarize(question: str) -> str:
	"""A conversation title: the first line of the first question, trimmed."""
	first_line = (question or "").strip().splitlines()
	return first_line[0][:120] if first_line else ""


def record_turn(
	session: str | None,
	question: str,
	result: dict,
	*,
	project: str | None = None,
	took_ms: int | None = None,
	model: str | None = None,
	llm_metrics: list[dict] | None = None,
) -> str:
	"""Persist one ask — the question, the answer, its route, citations and cost.

	`result` is the dict `rag.answer.answer()` returns. Opens a conversation when `session`
	is empty — or names one that no longer exists, the same tolerance
	`agent.session.get_or_create` has — and returns the conversation name either way, so
	the caller can hand it back to the interface as one line at the end of `ask()`.
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
	metrics = llm_metrics if llm_metrics is not None else turn_metrics(expect_synthesis=not refused)
	prompt_tokens, completion_tokens = tokens_of(metrics)
	cost = flt(cost_of(metrics), 6)
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
		took_ms=cint(took_ms or result.get("took_ms")),
		model=model or agent_llm.resolve_model(project=conversation.project),
		prompt_tokens=prompt_tokens,
		completion_tokens=completion_tokens,
		cost=cost,
	)

	conversation.title = conversation.title or summarize(question)
	conversation.message_count = cint(conversation.message_count) + 2
	conversation.total_tokens = cint(conversation.total_tokens) + prompt_tokens + completion_tokens
	conversation.total_cost = flt(flt(conversation.total_cost) + cost, 6)
	conversation.save()
	return session


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
	"""
	if not session or not frappe.has_permission("Wikify Ask Session", doc=session):
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
