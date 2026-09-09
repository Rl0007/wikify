from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

import litellm
from frappe.utils.data import cint, flt
from litellm.integrations.custom_logger import CustomLogger

SPEND_GRACE_SECONDS = 5.0
SPEND_POLL_SECONDS = 0.01

_local = threading.local()
_lock = threading.Lock()

_awaiting_price: dict[str, Collector] = {}
_callback_registered = False


def empty() -> dict:
	return {"cost": 0.0, "prompt_tokens": 0, "completion_tokens": 0}


@dataclass
class Collector:
	totals: dict = field(default_factory=empty)
	pending: set[str] = field(default_factory=set)


@contextmanager
def collect() -> Iterator[dict]:
	if getattr(_local, "collector", None) is not None:
		yield _local.collector.totals
		return

	collector = Collector()
	_local.collector = collector
	add_litellm_callback()
	try:
		yield collector.totals
	finally:
		with _lock:
			for call_id in collector.pending:
				_awaiting_price.pop(call_id, None)
			collector.pending.clear()
		_local.collector = None


def add(usage) -> None:
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
	deadline = time.monotonic() + SPEND_GRACE_SECONDS
	while time.monotonic() < deadline:
		with _lock:
			if not collector.pending:
				return
		time.sleep(SPEND_POLL_SECONDS)


class LitellmSpend(CustomLogger):
	def log_pre_api_call(self, model, messages, kwargs):
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
		with _lock:
			collector = _awaiting_price.pop(call_id, None)
			if collector is None:
				return
			collector.pending.discard(call_id)
			collector.totals["cost"] += flt(cost)


def add_litellm_callback() -> None:
	global _callback_registered
	with _lock:
		if _callback_registered:
			return
		litellm.callbacks = [*litellm.callbacks, LitellmSpend()]
		_callback_registered = True
