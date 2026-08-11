"""OpenRouter client — ported from the POC `config.chat_completion`.

I/O-boundary change only: the POC used the `openai` SDK; here we call OpenRouter's
REST endpoint with `requests` (already on the bench; no new dependency). The judge /
cleanup / classifier logic that calls this is unchanged.

Each call records latency + token cost in a thread-safe metrics buffer so the parse
job can attach per-stage cost to the live log (and so a future benchmark can read it).
The API key + model ids come from `engine.settings` (the `Wikify Settings` Single).
"""

from __future__ import annotations

import threading
import time

import requests

from wikify.engine import settings

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


def has_openrouter() -> bool:
	return bool(settings.openrouter_key())


# --- lightweight per-call metrics (cost + latency), mirrors POC config.py ---
_metrics_lock = threading.Lock()
_metrics: list[dict] = []


def reset_metrics() -> None:
	with _metrics_lock:
		_metrics.clear()


def get_metrics() -> list[dict]:
	with _metrics_lock:
		return list(_metrics)


def chat_completion(
	model: str,
	messages: list,
	label: str = "",
	*,
	temperature: float = 0,
	response_format: dict | None = None,
	max_tokens: int | None = None,
	timeout: int = 120,
	api_key: str = "",
) -> dict:
	"""POST a chat completion to OpenRouter, recording latency + token cost.

	Returns the parsed JSON response body (same shape as the OpenAI chat API, so
	callers read `resp["choices"][0]["message"]["content"]`).

	`api_key` is for callers running this on a worker thread: resolving the key reads
	`Wikify Settings`, and `frappe.local` is unbound off the request thread, so the caller
	resolves it once up front and hands it down. Omit it and it is resolved here as usual.
	"""
	key = api_key or settings.openrouter_key()
	if not key:
		raise RuntimeError("OPENROUTER key not set; cloud features unavailable.")

	body: dict = {
		"model": model,
		"messages": messages,
		"temperature": temperature,
		"usage": {"include": True},  # ask OpenRouter to return cost
	}
	if response_format is not None:
		body["response_format"] = response_format
	if max_tokens is not None:
		body["max_tokens"] = max_tokens

	t0 = time.monotonic()
	resp = requests.post(
		f"{OPENROUTER_BASE_URL}/chat/completions",
		headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
		json=body,
		timeout=timeout,
	)
	dt = time.monotonic() - t0
	resp.raise_for_status()
	data = resp.json()

	usage = data.get("usage") or {}
	with _metrics_lock:
		_metrics.append(
			{
				"label": label,
				"model": model,
				"seconds": round(dt, 3),
				"prompt_tokens": usage.get("prompt_tokens"),
				"completion_tokens": usage.get("completion_tokens"),
				"cost": usage.get("cost"),
			}
		)
	return data
