"""Whitelisted APIs for retrieval + grounded answering (POC-2 phase 1).

Five methods: `search` (the three legs), `ask` (grounded, streamed, cited), `index_status`,
`reindex`, and `compare` — the demo punchline that runs the same query as naive top-k
vector search *and* as the routed hybrid, so the interface can show what naive retrieval
missed.

Permissions: every read path resolves the projects the user may read and hands them to
`search()` as a pre-filter, so the ACL is applied inside the store's `where` clause rather
than by trimming rows after the fact — a post-hoc trim would let an unreadable project's
chunks consume the top-k budget and silently starve the answer.
"""

from __future__ import annotations

import time

import frappe
from frappe import _
from frappe.utils.data import cint, sbool

from wikify.agent import llm as agent_llm
from wikify.api.permission import (
	assert_readable,
	documents_in_projects,
	readable_projects,
)
from wikify.rag import answer as rag_answer
from wikify.rag import answer_cache
from wikify.rag import history as rag_history
from wikify.rag import index as rag_index
from wikify.rag import search as rag_search
from wikify.rag.router import route as route_question

STREAM_EVENT = "wikify_rag_answer"


@frappe.whitelist()
def search(
	query: str,
	project: str | None = None,
	source_document: str | None = None,
	section_type: str | None = None,
	limit: int = 8,
	mode: str = "hybrid",
	rerank: bool = False,
	use_router: bool = True,
) -> dict:
	"""Retrieve chunks for a query.

	The router runs by default and supplies the `section_type` filter when the caller did
	not name one; an explicit `mode` is always honoured. Pass `use_router=False` for a raw,
	unrouted leg (what `compare`'s naive side needs) — `route` then comes back as null.
	"""
	query = (query or "").strip()
	if not query:
		frappe.throw(_("Enter something to search for."))
	assert_readable(project, source_document)

	started = time.monotonic()
	decided = route_question(query, project) if sbool(use_router) else None
	if decided:
		section_type = section_type or decided.section_type
		query = decided.query

	hits = rag_search.search(
		query,
		project=project,
		source_document=source_document,
		section_type=section_type,
		limit=cint(limit) or 8,
		mode=mode,
		rerank=sbool(rerank),
		allowed_projects=readable_projects(),
	)
	return {
		"hits": [hit.as_dict() for hit in hits],
		"route": decided.as_dict() if decided else None,
		"took_ms": int((time.monotonic() - started) * 1000),
		"mode": mode,
	}


@frappe.whitelist(methods=["POST"])
def ask(
	question: str,
	project: str | None = None,
	session: str | None = None,
	stream: str | None = None,
	rerank: bool = True,
) -> dict:
	"""Answer a question from the wiki, with citations, streaming as it goes.

	`stream` and `session` are different things and are not interchangeable. `stream` is a
	caller-minted correlation token, echoed on every realtime payload so one tab can pick
	its own deltas off a per-user channel; the server never stores it. `session` is a
	`Wikify Ask Session` docname — absent on the first ask of a conversation, and returned
	so the caller can send it back to make the next question a follow-up.

	Realtime on `wikify_rag_answer` mirrors the agent loop's ordering: the route lands
	first (the interface shows *why* this retrieval strategy), then the sources, then the
	answer deltas, then `done` — which carries what the turn cost, so the price can be
	shown the moment the answer finishes. The same payload is also returned, so a caller
	that isn't listening still gets the whole answer.

	A repeat of the same question is served from `answer_cache` and publishes the identical
	sequence, with `cached: True` and zero spend. The cache is exact-match and keyed on the
	store's write counter, so it cannot outlive the corpus it answered from — see that
	module for why it is not keyed by similarity.
	"""
	question = (question or "").strip()
	if not question:
		frappe.throw(_("Ask a question."))
	assert_readable(project)

	user = frappe.session.user
	started = time.monotonic()

	def publish(payload: dict) -> None:
		frappe.publish_realtime(STREAM_EVENT, {"stream": stream, **payload}, user=user)

	history = session_history(session)
	readable = readable_projects()
	rerank_enabled = sbool(rerank)
	key = answer_cache.cache_key(
		question, project, history, rerank_enabled, readable, agent_llm.resolve_model(project=project)
	)

	cached = answer_cache.get(key)
	if cached:
		result = replay_cached_answer(cached, publish)
	else:
		result = rag_answer.answer(
			question,
			project=project,
			history=history,
			rerank=rerank_enabled,
			allowed_projects=readable,
			on_route=lambda route: publish({"route": route}),
			on_citations=lambda citations: publish({"citations": citations}),
			on_delta=lambda delta: publish({"delta": delta}),
		)
		# Stored before `took_ms` and `cached` land, so a replay is never billed the
		# original's clock and never reports itself as the run that paid for the answer.
		# A refusal is never stored: it can be produced by a transient failure — a missing
		# key, a reranker returning a flat verdict — rather than by a fact about the
		# corpus, and a cached one would keep answering "I couldn't find this" for a day
		# after the cause was fixed. It is also the cheap path, so re-running costs little.
		if not result["refused"]:
			answer_cache.set(key, result)
		result["cached"] = False

	result["took_ms"] = int((time.monotonic() - started) * 1000)
	publish(
		{
			"done": True,
			"refused": result["refused"],
			"took_ms": result["took_ms"],
			"cost": result["cost"],
			"prompt_tokens": result["prompt_tokens"],
			"completion_tokens": result["completion_tokens"],
			"model": result["model"],
			"cached": result["cached"],
		}
	)
	# The log is the product's own eval set: route decisions and citations are what let us
	# ask later which questions were routed wrong. A logging failure must never cost the
	# user an answer they already have.
	try:
		result["session"] = rag_history.record_turn(
			session,
			question,
			result,
			project=project,
			took_ms=result["took_ms"],
			model=result["model"],
		)
	except Exception:
		frappe.log_error(title="Wikify: could not record the ask turn")
	return result


def replay_cached_answer(cached: dict, publish) -> dict:
	"""Re-emit a cached answer over realtime in the order a live one arrives.

	The interface reads the stream, not the return value, so a cache hit has to publish the
	same three payloads or the page shows nothing until `done`. The answer goes out as one
	delta rather than re-chunked: there is no generation to pace, and faking a token stream
	would spend the very wall clock the cache exists to remove.

	Spend is zeroed rather than replayed. This turn made no completion, so reporting the
	original's cost would overstate what the corpus is costing to run; `cached` is what
	tells the caller why the price is nothing.
	"""
	publish({"route": cached.get("route")})
	publish({"citations": cached.get("citations") or []})
	if cached.get("answer"):
		publish({"delta": cached["answer"]})
	return {
		**cached,
		"cost": 0.0,
		"prompt_tokens": 0,
		"completion_tokens": 0,
		"cached": True,
	}


def session_history(session: str | None) -> list[dict]:
	"""Prior turns of an Ask conversation, so the router can rewrite follow-up questions.

	Delegates to `rag.history`, which owns the Ask session doctypes and drops an unreadable
	session rather than replaying someone else's conversation into the router prompt.
	"""
	return rag_history.recent_turns(session)


@frappe.whitelist()
def index_status(project: str | None = None) -> dict:
	"""Index counts plus whether sections have changed since the index was written.

	Unscoped, this reports the union of the projects the user may read rather than the
	whole site — the card must not tell someone how much content they cannot see.
	"""
	assert_readable(project)
	projects = [project] if project else readable_projects()
	totals = rag_index.index_stats(projects)
	totals["stale"] = is_stale(projects, totals["indexed_at"])
	return totals


def is_stale(projects: list[str], indexed_at) -> bool:
	"""True when any in-scope section was edited after the index was last written."""
	if not indexed_at:
		return True
	if not projects:
		return False
	filters = {
		"modified": [">", indexed_at],
		"source_document": ["in", documents_in_projects(projects)],
	}
	return bool(frappe.get_all("Source Section", filters=filters, limit=1, pluck="name"))


@frappe.whitelist(methods=["POST"])
def reindex(project: str) -> dict:
	"""Rebuild a project's index in the background (embedding the corpus is slow)."""
	if not frappe.has_permission("Wikify Project", ptype="write", doc=project):
		frappe.throw(_("You are not allowed to reindex {0}.").format(project), frappe.PermissionError)
	job = frappe.enqueue(
		"wikify.rag.index.rebuild_project",
		queue="long",
		timeout=3600,
		project=project,
	)
	frappe.local.response["http_status_code"] = 202
	return {"job": job.id}


@frappe.whitelist(methods=["POST"])
def compare(query: str, project: str | None = None) -> dict:
	"""The demo punchline: naive top-k vector search beside the routed strategy.

	Same query, same corpus, same ACL. The naive leg is what a textbook RAG pipeline
	returns; the routed leg follows the router's decision — for an "all X" question that
	is an exhaustive metadata filter, which is where the recall gap shows up.
	"""
	query = (query or "").strip()
	if not query:
		frappe.throw(_("Enter a query to compare."))
	assert_readable(project)

	comparison = rag_answer.compare(query, project, readable_projects())
	return {
		"naive": [hit.as_dict() for hit in comparison["naive"]],
		"routed": [hit.as_dict() for hit in comparison["routed"]],
		"route": comparison["route"].as_dict(),
		# The headline number: the sections the routed leg found that naive top-k never saw.
		# Precomputed here so the interface can highlight them without re-deriving the diff.
		"missed_by_naive": [hit.section for hit in comparison["missed_by_naive"]],
	}
