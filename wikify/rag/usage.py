"""What one answer cost — token and money totals for the model calls it actually made.

Answering a question spends up to three completions in three different modules (the
router's classification, the reranker's scoring, the synthesis stream), so the total is
folded into a thread-local accumulator rather than threaded back through every signature.

Thread-local, not module-level, because the web worker serves requests on threads: a
shared buffer would bill one user's question to another's. Outside a `collect()` block
`add()` is a no-op, so the retrieval paths that are not part of an answer cost nothing to
instrument.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager

from frappe.utils.data import cint, flt

_local = threading.local()


def empty() -> dict:
	return {"cost": 0.0, "prompt_tokens": 0, "completion_tokens": 0}


@contextmanager
def collect() -> Iterator[dict]:
	"""Total the usage of every `add()` inside the block. Nesting reuses the outer total."""
	if getattr(_local, "totals", None) is not None:
		yield _local.totals
		return

	_local.totals = empty()
	try:
		yield _local.totals
	finally:
		_local.totals = None


def add(usage) -> None:
	"""Fold one completion's `usage` payload into the current total.

	`usage` is whatever the client handed back: a dict from the OpenRouter REST client, or
	litellm's usage object on the final stream chunk. Cost is only present when the
	provider reports it — we never estimate it, an unpriced call adds 0.
	"""
	totals = getattr(_local, "totals", None)
	if totals is None or not usage:
		return
	totals["cost"] += flt(read(usage, "cost"))
	totals["prompt_tokens"] += cint(read(usage, "prompt_tokens"))
	totals["completion_tokens"] += cint(read(usage, "completion_tokens"))


def read(usage, key: str):
	return usage.get(key) if isinstance(usage, dict) else getattr(usage, key, None)
