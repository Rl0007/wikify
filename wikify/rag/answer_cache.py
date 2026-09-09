"""Exact-match cache for whole answers, so a repeated question costs nothing.

Ask is ~100% LLM wall clock — routing, reranking and synthesis, measured at 16.3s and
$0.018 for one question. Exam revision traffic has a fat head: many students work the same
syllabus, so the same wording arrives again and again. Serving the second one from cache
turns 16.3s into a lookup.

**Exact match on normalised text, never similarity.** A semantic cache keyed by embedding
distance would hit far more often, and is the wrong trade here: "surcharge rate for
individuals" and "surcharge rate for companies" are near neighbours carrying *different
statutory rates*, so a near-miss returns a confidently wrong figure behind a real citation
with a real page number — which the evidence gate cannot catch, because the citation is
genuine and merely answers a different question. See `docs/rag-latency-prior-art.md` §7,
where semantic caching is disqualified on exactly this failure mode.

**Freshness comes from LanceDB, not from invalidation hooks.** `chunks_table().version` is
a monotonic counter the store bumps on every write, so folding it into the key retires
every cached answer for the corpus the moment anything is reindexed, remediated or
re-sectioned. There is no code path that can forget to invalidate, because no code path
invalidates.

The key also carries everything else an answer depends on: the asking user's readable
projects (two users with different permissions must never share an entry), the synthesis
model, the conversation history the router would rewrite against, and the rerank flag.
"""

from __future__ import annotations

import hashlib
import json
import re

import frappe

from wikify.rag import store

# A day is a backstop, not the freshness mechanism — `index_version()` already retires an
# entry the moment its corpus changes. This only stops abandoned keys accumulating in redis.
TTL_SECONDS = 86400

WHITESPACE = re.compile(r"\s+")


def normalise(question: str) -> str:
	"""Fold the differences that never change the answer: spacing and letter case.

	This is still exact matching — two questions collide only when their text is identical
	once spacing and case are levelled. It is not a similarity threshold and must never
	become one.
	"""
	return WHITESPACE.sub(" ", (question or "").strip()).casefold()


def index_version() -> int:
	"""The store's monotonic write counter, or 0 when there is no table to read.

	A missing table means nothing is indexed, so every answer is a refusal and they may
	share a key. A store that cannot be opened must not take the ask down with it — the
	caller loses the cache, not the answer.
	"""
	try:
		table = store.chunks_table()
	except Exception:
		return 0
	return getattr(table, "version", 0) if table is not None else 0


def cache_key(
	question: str,
	project: str | None,
	history: list | None,
	rerank: bool,
	readable: list[str],
	model: str,
) -> str:
	"""Hash every input the answer actually depends on.

	`readable` is load-bearing: it is the ACL pre-filter handed to the store, so leaving it
	out would let a broadly-permissioned user's answer be served to a narrowly-permissioned
	one. It is sorted because the permission query's row order is not guaranteed stable.
	"""
	payload = json.dumps(
		{
			"question": normalise(question),
			"project": project or "",
			"history": history or [],
			"rerank": bool(rerank),
			"readable": sorted(readable or []),
			"model": model or "",
			"index_version": index_version(),
		},
		sort_keys=True,
		default=str,
	)
	return f"wikify_rag_answer:{hashlib.sha256(payload.encode()).hexdigest()}"


def get(key: str) -> dict | None:
	"""The cached answer, or None. A cache read must never cost the caller an answer."""
	try:
		return frappe.cache().get_value(key)
	except Exception:
		frappe.log_error(title="Wikify: could not read the ask cache")
		return None


def set(key: str, result: dict) -> None:
	"""Store an answer. The caller decides what is worth storing — see `api.rag.ask`, which
	withholds refusals because a transient failure produces the same shape as a real one.

	Named `set` to read as a cache verb at the call site (`answer_cache.set(...)`); it
	shadows the builtin only inside this module, which uses no set literals.
	"""
	try:
		frappe.cache().set_value(key, result, expires_in_sec=TTL_SECONDS)
	except Exception:
		frappe.log_error(title="Wikify: could not write the ask cache")
