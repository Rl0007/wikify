"""Query routing — the core thesis of POC-2 made executable.

*"Give me all the job descriptions"* is a **completeness** question: the right answer is a
metadata filter on `Source Section.section_type` that returns every match, not a top-k
similarity guess that silently under-recalls. This module decides, per question, which of
the three retrieval legs to use:

- `exhaustive` — filter on a section type, return **all** matches.
- `semantic`   — fuzzy "something about Y", hybrid top-k.
- `hybrid`     — a type filter *and* similarity ranking inside it.

It also rewrites conversational follow-ups ("what about the second one?") into a
standalone query, because retrieval has no memory — only the rewritten query is embedded.

The classification runs on the cheap `classifier_model` through `engine.llm.chat_completion`
— the same JSON-mode client the reranker uses, so routing shows up in the per-call cost
metrics. When no key is configured (or the model misbehaves) routing falls back
deterministically to `hybrid`: the union leg is never wrong, only less pointed, so
retrieval must never hard-fail on the router.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass

import frappe

from wikify.engine import llm, settings
from wikify.rag import usage

# The router only ever sees a short tail of the conversation — enough to resolve "it" /
# "the second one", not enough to blow up the cheap model's context.
HISTORY_TURNS = 6

FALLBACK_REASON = "Routing is unavailable, so I searched both by meaning and by section type."


@dataclass
class Route:
	"""A routing decision. `reason` is rendered in the UI — write it for a human."""

	intent: str  # "exhaustive" | "semantic" | "hybrid"
	section_type: str | None
	query: str  # rewritten, standalone
	reason: str

	def as_dict(self) -> dict:
		return asdict(self)


INTENTS = ("exhaustive", "semantic", "hybrid")

SYSTEM_PROMPT = """You route questions against a wiki built from PDFs. Every section of \
every document carries a `section_type` tag from a fixed taxonomy.

Pick ONE intent:
- "exhaustive" — the question asks for EVERY item of one kind. The giveaway is a universal \
word: all, every, each, both, list the, how many, which ones are there, across all the \
documents. Asking what the COLLECTION ITSELF holds is the same request even without one of \
those words, because it sweeps every document to pull one thing out of each. Completeness \
beats ranking, so this returns every matching section. Requires a section_type from the \
taxonomy.
- "hybrid" — an open question whose OWN WORDS name the kind of section it is about ("what \
do the pay policies say about overtime"), or one that sets the SAME rule side by side \
across a HANDFUL OF NAMED organisations, which asks for that kind of section in each of \
them. If the sweep is over the whole collection rather than a named few, it is \
"exhaustive", not "hybrid". Requires a section_type.
- "semantic" — everything else. This is the DEFAULT.

Naming the kind means using the taxonomy's own words or an obvious synonym for them — \
"roles", "job descriptions", "pay policies". Naming a TOPIC is not naming a kind: \
"rostering", "travel costs" and "supervision" are subjects that turn up inside several \
kinds of section.

Each type below carries a description of what it holds. Read it to work out WHICH type a \
question names — never as a licence to filter. A question that merely mentions a topic \
listed in a description has still not named a kind, because those topics are restated \
inside role descriptions, training sections and procedures too.

Choosing a filter is the risky move: a wrong section_type hides the answer completely, and \
answers often span several types (a rostering rule can sit in a job description AND a pay \
policy). So only filter when the user's own wording demands it. If you are weighing \
"semantic" against "hybrid", pick "semantic".

Worked examples, on a different corpus to the one you are routing:
- "Show me every equipment maintenance procedure in these manuals." -> exhaustive, \
equipment_and_facilities. A universal word plus a named kind.
- "Which roles have to be registered with a professional body?" -> hybrid, \
staff_roles_and_responsibilities. The question's own word is "roles".
- "Put each employer's leave entitlement side by side." -> hybrid, \
administrative_policies. Comparing one rule across organisations asks for that policy \
section in every one of them.
- "How do staff get their uniform costs reimbursed?" -> semantic, null. Expenses sit in \
the pay policy AND in the role sections; a filter would hide half the answer.
- "What should the team do if a controlled drug goes missing mid-shift?" -> semantic, \
null. A what-happens-if scenario is retold across several kinds of section.

Also rewrite the question into a STANDALONE query: resolve pronouns and references to \
earlier turns ("what about the second one?" -> "the second job description in ..."). If \
the question already stands alone, repeat it unchanged.

Write `reason` as ONE plain sentence addressed to the user, explaining what you are doing \
and why. It is shown in the interface. No jargon, no field names.

Reply with ONLY a JSON object:
{"intent": "...", "section_type": "<type_name or null>", "query": "...", "reason": "..."}"""


def taxonomy_lines() -> list[dict]:
	"""The Section Type taxonomy the router is allowed to choose from."""
	return frappe.get_all(
		"Section Type",
		fields=["type_name", "label", "description"],
		order_by="is_other asc, creation asc",
	)


def build_messages(question: str, project: str | None, history: list | None) -> list[dict]:
	types = taxonomy_lines()
	catalogue = "\n".join(
		f"- {row['type_name']}: {row['label'] or row['type_name']}"
		+ (f" — {row['description']}" if row.get("description") else "")
		for row in types
	)
	context = f"Taxonomy (the only valid section_type values):\n{catalogue or '(empty)'}"
	if project:
		project_context = frappe.db.get_value("Wikify Project", project, "context_prompt")
		if project_context:
			context += f"\n\nWhat this wiki is about:\n{project_context}"

	messages = [
		{"role": "system", "content": SYSTEM_PROMPT},
		{"role": "system", "content": context},
	]
	for turn in (history or [])[-HISTORY_TURNS:]:
		role = turn.get("role")
		content = (turn.get("content") or "").strip()
		if role in ("user", "assistant") and content:
			messages.append({"role": role, "content": content[:2000]})
	messages.append({"role": "user", "content": question})
	return messages


def parse_decision(content: str) -> dict:
	"""Read the model's JSON reply, tolerating a ```json fence or surrounding prose."""
	text = (content or "").strip()
	if text.startswith("```"):
		text = text.split("```")[1]
		text = text.removeprefix("json").strip()
	start, end = text.find("{"), text.rfind("}")
	if start == -1 or end == -1:
		return {}
	try:
		return frappe.parse_json(text[start : end + 1]) or {}
	except (ValueError, TypeError, json.JSONDecodeError):
		return {}


def fallback(question: str, reason: str = FALLBACK_REASON) -> Route:
	return Route(intent="hybrid", section_type=None, query=question, reason=reason)


def route(question: str, project: str | None = None, history: list | None = None) -> Route:
	"""Classify intent + rewrite the question into a standalone query.

	Never raises: any failure (no key, model down, unparseable reply, invented section
	type) degrades to the `hybrid` union leg with an honest reason.
	"""
	question = (question or "").strip()
	if not question:
		return fallback(question, "There was no question to route.")
	if not settings.openrouter_key():
		return fallback(question)

	try:
		response = llm.chat_completion(
			settings.get("classifier_model"),
			build_messages(question, project, history),
			label="rag_route",
			response_format={"type": "json_object"},
		)
		content = response["choices"][0]["message"]["content"]
		usage.add(response.get("usage"))
	except Exception:
		frappe.log_error(title="Wikify RAG router failed")
		return fallback(question)

	decision = parse_decision(content)
	if not decision:
		return fallback(question)

	intent = (decision.get("intent") or "").strip().lower()
	if intent not in INTENTS:
		return fallback(question)

	section_type = (decision.get("section_type") or "").strip() or None
	# A hallucinated type would filter the corpus down to nothing, so an unknown type
	# drops the filter rather than the results.
	if section_type and not frappe.db.exists("Section Type", section_type):
		section_type = None
	# Both filtered legs are meaningless without a type to filter on.
	if intent in ("exhaustive", "hybrid") and not section_type:
		intent = "semantic"

	rewritten = (decision.get("query") or "").strip() or question
	reason = (decision.get("reason") or "").strip() or FALLBACK_REASON
	return Route(intent=intent, section_type=section_type, query=rewritten, reason=reason)
