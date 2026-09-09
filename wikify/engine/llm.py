from __future__ import annotations

import threading
import time

import requests

from wikify.engine import settings

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


def has_openrouter() -> bool:
	return bool(settings.openrouter_key())


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
	provider: dict | None = None,
) -> dict:
	key = api_key or settings.openrouter_key()
	if not key:
		raise RuntimeError("OPENROUTER key not set; cloud features unavailable.")

	body: dict = {
		"model": model,
		"messages": messages,
		"temperature": temperature,
		"usage": {"include": True},
	}
	if response_format is not None:
		body["response_format"] = response_format
	if max_tokens is not None:
		body["max_tokens"] = max_tokens
	if provider:
		body["provider"] = provider

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
