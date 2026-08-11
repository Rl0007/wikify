"""What one answer cost — token and money totals for the model calls it actually made.

Answering a question spends up to three completions in three different modules (the
router's classification, the reranker's scoring, the synthesis stream), so the total is
folded into a thread-local accumulator rather than threaded back through every signature.

Thread-local, not module-level, because the web worker serves requests on threads: a
shared buffer would bill one user's question to another's. Outside a `collect()` block
`add()` is a no-op, so the retrieval paths that are not part of an answer cost nothing to
instrument.

The money arrives by two different roads because the two clients report it differently:

- the OpenRouter REST client (`engine.llm`) returns `usage.cost` on the response itself,
  folded in by `add()`;
- litellm (synthesis) puts the price nowhere on the response — it reports it only to a
  callback, after the stream has ended, on one of its own pool threads. `LitellmSpend`
  therefore ties each litellm call to the collector that started it in `log_pre_api_call`
  (which litellm runs inline, on the calling thread) and bills it back by the same call id
  when the price lands. Only the money comes from the callback; the tokens are already on
  the response.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

import litellm
from frappe.utils.data import cint, flt
from litellm.integrations.custom_logger import CustomLogger

# litellm prices a streamed completion a few milliseconds after the stream ends, so a
# collector with a call outstanding waits for it. Generous, because reporting a cost of
# zero for the leg that carries ~95% of the spend is far worse than a short pause on an
# answer the reader has already been streamed.
SPEND_GRACE_SECONDS = 5.0
SPEND_POLL_SECONDS = 0.01

_local = threading.local()
_lock = threading.Lock()

# litellm call id -> the collector that started it. Module-level, because the callback
# that resolves it runs on a different thread than the one that made the call.
_awaiting_price: dict[str, Collector] = {}
_callback_registered = False


def empty() -> dict:
	return {"cost": 0.0, "prompt_tokens": 0, "completion_tokens": 0}


@dataclass
class Collector:
	"""One question's running total, plus the litellm calls it is still owed a price for."""

	totals: dict = field(default_factory=empty)
	pending: set[str] = field(default_factory=set)


@contextmanager
def collect() -> Iterator[dict]:
	"""Total the usage of every `add()` inside the block. Nesting reuses the outer total."""
	if getattr(_local, "collector", None) is not None:
		yield _local.collector.totals
		return

	collector = Collector()
	_local.collector = collector
	add_litellm_callback()
	try:
		yield collector.totals
	finally:
		# No wait here: `answer()` reads the totals INSIDE the block, so a price that lands
		# after it has read them cannot reach any reported number — waiting for one would
		# only add up to the whole grace period to a turn that is already finished. `add()`
		# does the waiting, at the one moment the figure is still going somewhere.
		with _lock:
			for call_id in collector.pending:
				_awaiting_price.pop(call_id, None)
			collector.pending.clear()
		_local.collector = None


def add(usage) -> None:
	"""Fold one completion's `usage` payload into the current total.

	`usage` is whatever the client handed back: a dict from the OpenRouter REST client, or
	litellm's usage object on the final stream chunk. Cost is only present when the
	provider reports it — we never estimate it, an unpriced call adds 0.

	Folding also collects any litellm price this block is still owed, because
	`answer()` reads the total inside the `collect()` block, immediately after the last
	`add()` — waiting at the end of the block would be too late to be in the number.
	"""
	collector = getattr(_local, "collector", None)
	if collector is None:
		return
	wait_for_prices(collector)
	if not usage:
		return
	collector.totals["cost"] += flt(read(usage, "cost"))
	collector.totals["prompt_tokens"] += cint(read(usage, "prompt_tokens"))
	collector.totals["completion_tokens"] += cint(read(usage, "completion_tokens"))


def read(usage, key: str):
	return usage.get(key) if isinstance(usage, dict) else getattr(usage, key, None)


def wait_for_prices(collector: Collector) -> None:
	"""Wait for litellm to price the calls this collector started. No-op when it owes none.

	# ponytail: a price that never arrives costs the ask the whole grace period, since
	# litellm reporting neither success nor failure is the only way out; give the
	# completion its own timeout if a provider ever goes quiet on us.
	"""
	deadline = time.monotonic() + SPEND_GRACE_SECONDS
	while time.monotonic() < deadline:
		with _lock:
			if not collector.pending:
				return
		time.sleep(SPEND_POLL_SECONDS)


class LitellmSpend(CustomLogger):
	"""Bills each litellm completion to the collector that started it."""

	def log_pre_api_call(self, model, messages, kwargs):
		# Runs inline on the calling thread, which is the only moment the call and the
		# question that made it are known to be the same piece of work.
		collector = getattr(_local, "collector", None)
		call_id = kwargs.get("litellm_call_id")
		if collector is None or not call_id:
			return
		with _lock:
			collector.pending.add(call_id)
			_awaiting_price[call_id] = collector

	def log_success_event(self, kwargs, response_obj, start_time, end_time):
		self.add_price(kwargs.get("litellm_call_id"), kwargs.get("response_cost"))

	def log_failure_event(self, kwargs, response_obj, start_time, end_time):
		self.add_price(kwargs.get("litellm_call_id"), None)

	def add_price(self, call_id: str | None, cost) -> None:
		"""Add one completion's price to its collector, exactly once.

		Popping the call id is what makes it once: litellm may report the same streamed
		completion more than once, and a repeated price would bill the reader twice.
		"""
		with _lock:
			collector = _awaiting_price.pop(call_id, None)
			if collector is None:
				return
			collector.pending.discard(call_id)
			collector.totals["cost"] += flt(cost)


def add_litellm_callback() -> None:
	"""Register the cost logger once per process."""
	global _callback_registered
	with _lock:
		if _callback_registered:
			return
		litellm.callbacks = [*litellm.callbacks, LitellmSpend()]
		_callback_registered = True
