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
from wikify.rag import usage as rag_usage
from wikify.rag.router import route as route_question

STREAM_EVENT = "wikify_rag_answer"

DEFAULT_LIMIT = 8
# `limit` widens the candidate list, and with the reranker on every candidate is a 512-token
# cross-encoder forward pass on the web worker inside the request. Unbounded, that is an
# authenticated way to spend the whole box on one call.
MAX_LIMIT = 100


def clamped_limit(limit) -> int:
	return min(cint(limit) or DEFAULT_LIMIT, MAX_LIMIT)


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
		limit=clamped_limit(limit),
		mode=mode,
		use_reranker=sbool(rerank),
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
	# Routing is an LLM call, and it runs before retrieval can decide there is nothing to
	# retrieve. `assert_readable` above cannot cover this: an unscoped ask has no project to
	# check, so without this a user who may read no wiki at all still spends a classifier
	# call — and a session row — on every question, only to be refused for want of hits.
	if not project and not readable:
		frappe.throw(_("You do not have access to any wiki."), frappe.PermissionError)
	rerank_enabled = sbool(rerank)

	with rag_usage.collect() as spend:
		decided = route_question(question, project, history)
		publish({"route": decided.as_dict()})

		key = answer_cache.cache_key(
			decided, project, rerank_enabled, readable, agent_llm.resolve_model(project=project)
		)
		# A refusal is never stored: it can come from a transient failure — a missing key, a
		# reranker returning a flat verdict — rather than from a fact about the corpus, and a
		# cached one would keep answering "I couldn't find this" for a day after the fix.
		cached = answer_cache.get(key)
		if cached:
			result = replay_cached_answer(cached, publish, spend)
		else:
			result = rag_answer.answer(
				question,
				project=project,
				history=history,
				rerank=rerank_enabled,
				allowed_projects=readable,
				decided=decided,
				on_citations=lambda citations: publish({"citations": citations}),
				on_delta=lambda delta: publish({"delta": delta}),
			)
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


def replay_cached_answer(cached: dict, publish, spend: dict) -> dict:
	# The interface renders the stream, not the return value, so a hit must publish the same
	# payloads a live answer does. One delta, not a faked token stream: there is no generation
	# to pace, and pacing it would spend the wall clock the cache exists to remove. `spend` is
	# what THIS turn cost — the routing call — never the original's price and never zero.
	publish({"citations": cached.get("citations") or []})
	if cached.get("answer"):
		publish({"delta": cached["answer"]})
	return {**cached, **spend, "cached": True}


def session_history(session: str | None) -> list[dict]:
	return rag_history.recent_turns(session)


@frappe.whitelist()
def index_status(project: str | None = None) -> dict:
	assert_readable(project)
	projects = [project] if project else readable_projects()
	totals = rag_index.index_stats(projects)
	totals["stale"] = is_stale(projects, totals["indexed_at"])
	return totals


def is_stale(projects: list[str], indexed_at) -> bool:
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
	query = (query or "").strip()
	if not query:
		frappe.throw(_("Enter a query to compare."))
	assert_readable(project)

	comparison = rag_answer.compare(query, project, readable_projects())
	return {
		"naive": [hit.as_dict() for hit in comparison["naive"]],
		"routed": [hit.as_dict() for hit in comparison["routed"]],
		"route": comparison["route"].as_dict(),
		"missed_by_naive": [hit.section for hit in comparison["missed_by_naive"]],
	}
