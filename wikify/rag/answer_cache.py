from __future__ import annotations

import hashlib
import json
import re

import frappe

from wikify.rag import store

TTL_SECONDS = 86400

CACHE_VERSION = 1

WHITESPACE = re.compile(r"\s+")


def normalise(question: str) -> str:
	return WHITESPACE.sub(" ", (question or "").strip()).casefold()


def index_version() -> int | None:
	try:
		table = store.chunks_table()
	except Exception:
		frappe.log_error(title="Wikify: could not read the index version for the ask cache")
		return None
	return getattr(table, "version", 0) if table is not None else 0


def cache_key(
	decided,
	project: str | None,
	rerank: bool,
	readable: list[str],
	model: str,
	version: int | None = None,
) -> str:
	corpus = index_version() if version is None else version
	if corpus is None:
		return None
	payload = json.dumps(
		{
			"query": normalise(getattr(decided, "query", "") or ""),
			"section_type": getattr(decided, "section_type", None) or "",
			"intent": getattr(decided, "intent", None) or "",
			"project": project or "",
			"rerank": bool(rerank),
			"readable": sorted(readable or []),
			"model": model or "",
			"index_version": corpus,
			"cache_version": CACHE_VERSION,
		},
		sort_keys=True,
		default=str,
	)
	return f"wikify_rag_answer:{hashlib.sha256(payload.encode()).hexdigest()}"


def get(key: str | None) -> dict | None:
	if not key:
		return None
	try:
		return frappe.cache().get_value(key)
	except Exception:
		frappe.log_error(title="Wikify: could not read the ask cache")
		return None


def set(key: str | None, result: dict) -> None:
	if not key:
		return
	try:
		frappe.cache().set_value(key, result, expires_in_sec=TTL_SECONDS)
	except Exception:
		frappe.log_error(title="Wikify: could not write the ask cache")
