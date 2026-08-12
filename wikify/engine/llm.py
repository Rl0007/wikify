"""Chat completions — OpenRouter by default, a developer's Claude CLI on request.

I/O-boundary change only: the POC used the `openai` SDK; here we call OpenRouter's
REST endpoint with `requests` (already on the bench; no new dependency). The judge /
cleanup / classifier logic that calls this is unchanged.

`chat_completion` is the single seam every caller goes through, so the provider switch
lives here rather than in any caller: the return shape is the OpenAI chat-completion
dict either way, and callers keep reading `resp["choices"][0]["message"]["content"]`.

Each call records latency + token cost in a thread-safe metrics buffer so the parse
job can attach per-stage cost to the live log (and so a future benchmark can read it).
The API key + model ids come from `engine.settings` (the `Wikify Settings` Single).

The Claude CLI lane exists so a **local** site can run every feature — routing, reranking,
classification, question extraction, page parsing — off a developer's logged-in Claude
subscription instead of a metered OpenRouter key. It is chosen per site on the `Wikify
Settings` Single, so production simply leaves the field on OpenRouter.

What the CLI lane costs, all measured (see `claude_cli_completion` for how each is handled):
it reports no per-call price, so cost accounting records None rather than a lying 0.0; it
is far slower per call (~6-8s for a trivial prompt) because Claude Code re-sends its own
system prompt every time, charging ~30k cached tokens per call; and it gives no
structured-output guarantee, so JSON replies are unwrapped and validated here rather than
left to break each caller's parse.
"""

from __future__ import annotations

import base64
import json
import re
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import requests

from wikify.engine import settings

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

OPENROUTER = "OpenRouter"
CLAUDE_CLI = "Claude CLI"

JSON_ONLY_INSTRUCTION = (
	"Reply with the JSON document only — no prose before or after it, no markdown code "
	"fence, no explanation. Do not ask a clarifying question; answer with the JSON."
)

# A fenced block anywhere in the reply, language tag optional.
FENCE_PATTERN = re.compile(r"```[A-Za-z0-9_+-]*[ \t]*\r?\n?(.*?)```", re.DOTALL)

# The CLI pays Claude Code's own system-prompt overhead on every call (measured: ~7s wall
# and ~30k cached tokens for a trivial prompt), so the HTTP-shaped 120s budget the
# OpenRouter callers pass is too tight to be a useful ceiling here.
CLAUDE_CLI_MIN_TIMEOUT_SECONDS = 300


def has_openrouter() -> bool:
	"""Is a completion provider usable at all? — the gate every optional LLM stage reads.

	The name predates the second provider and is kept deliberately: `rag.search`,
	`jobs.parse`, `engine.remediate`, the classifier and the judge all read it as "can we
	call a model", and a site running on the CLI with no OpenRouter key would otherwise skip
	reranking, judging and classification without saying so.
	"""
	if settings.llm_provider() == CLAUDE_CLI:
		return bool(shutil.which(settings.claude_cli_path()))
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
	provider: dict | None = None,
	llm_provider: str = "",
) -> dict:
	"""Run a chat completion through the configured provider, recording latency + cost.

	Returns the parsed JSON response body (same shape as the OpenAI chat API, so
	callers read `resp["choices"][0]["message"]["content"]`) whichever provider served it.

	`api_key` is for callers running this on a worker thread: resolving the key reads
	`Wikify Settings`, and `frappe.local` is unbound off the request thread, so the caller
	resolves it once up front and hands it down. Omit it and it is resolved here as usual.

	`llm_provider` pins the provider for one call; left empty it is the site's `Wikify
	Settings` choice. `settings.llm_provider` is what makes that safe on the reranker's pool
	threads, where the Single cannot be read — see `settings.setting_across_threads`.

	`provider` is OpenRouter's provider-preference object (`{"sort": "throughput"}`,
	`{"order": [...], "allow_fallbacks": False}`). Omitted, OpenRouter picks by its
	price-weighted default, which can land parallel calls on providers of different speed.
	It is ignored by the Claude CLI, which has no provider routing to express.
	"""
	chosen = (llm_provider or settings.llm_provider()).strip()

	start = time.monotonic()
	if chosen == CLAUDE_CLI:
		data = claude_cli_completion(
			model,
			messages,
			response_format=response_format,
			timeout=max(timeout, CLAUDE_CLI_MIN_TIMEOUT_SECONDS),
		)
	else:
		data = openrouter_completion(
			model,
			messages,
			temperature=temperature,
			response_format=response_format,
			max_tokens=max_tokens,
			timeout=timeout,
			api_key=api_key,
			provider=provider,
		)
	seconds = time.monotonic() - start

	usage = data.get("usage") or {}
	with _metrics_lock:
		_metrics.append(
			{
				"label": label,
				"model": model,
				"provider": chosen,
				# What actually served it: the CLI answers a non-Anthropic id with the account's
				# own default model, and a page's parse is only reproducible if that is recorded.
				"provider_model": data.get("model"),
				"seconds": round(seconds, 3),
				"prompt_tokens": usage.get("prompt_tokens"),
				"completion_tokens": usage.get("completion_tokens"),
				"cost": usage.get("cost"),
			}
		)
	return data


def openrouter_completion(
	model: str,
	messages: list,
	*,
	temperature: float = 0,
	response_format: dict | None = None,
	max_tokens: int | None = None,
	timeout: int = 120,
	api_key: str = "",
	provider: dict | None = None,
) -> dict:
	"""POST a chat completion to OpenRouter and return its JSON body verbatim."""
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
	if provider:
		body["provider"] = provider

	resp = requests.post(
		f"{OPENROUTER_BASE_URL}/chat/completions",
		headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
		json=body,
		timeout=timeout,
	)
	resp.raise_for_status()
	return resp.json()


def claude_cli_completion(
	model: str,
	messages: list,
	*,
	response_format: dict | None = None,
	timeout: int = CLAUDE_CLI_MIN_TIMEOUT_SECONDS,
) -> dict:
	"""Run one completion through the logged-in `claude` CLI, shaped like OpenRouter's.

	Spends a Claude subscription rather than metered credit, so the returned
	`usage["cost"]` is **None, never 0.0**: `rag.usage` and the ask history pin a real
	per-ask dollar figure, and a zero there would report a product that costs money as
	free. None is a claim of "not priced"; 0.0 is a claim of "free". What the CLI does
	report about the call is kept alongside under `subscription_cost_usd` so the
	subscription burn is still on the record without being read as a billable price.

	`temperature` and `max_tokens` are not inherited — the CLI exposes no flag for either.
	`response_format` is honoured only as far as it can be: there is no structured-output
	guarantee here, so the ask is repeated in the system prompt and the reply is unwrapped
	and parsed before it is handed back (see `json_content`). Every JSON caller —
	`rag.router`, the `rag.search` reranker, `exam.extract` — reads the content straight
	into `frappe.parse_json`, and a fenced or chatty reply reaching them lands as an empty
	verdict rather than an error, so the failure is raised here instead.

	The call runs in a throwaway directory, for two reasons: `claude -p` reads the CLAUDE.md
	of wherever it starts (measured — a classification prompt run from the bench root came
	back reasoning about Frappe doctypes), and the vision passes have to hand their page
	image over as a file the CLI is allowed to read, which means a file under its cwd.
	"""
	with tempfile.TemporaryDirectory(prefix="wikify-claude-") as workspace:
		system_prompt, prompt = split_messages(messages, workspace)
		if response_format:
			system_prompt = f"{system_prompt}\n\n{JSON_ONLY_INSTRUCTION}".strip()

		command = [settings.claude_cli_path(), "-p", prompt, "--output-format", "json"]
		cli_model = claude_cli_model(model)
		if cli_model:
			command += ["--model", cli_model]
		if system_prompt:
			command += ["--append-system-prompt", system_prompt]
		try:
			completed = subprocess.run(
				command, capture_output=True, text=True, timeout=timeout, cwd=workspace
			)
		except FileNotFoundError as error:
			raise RuntimeError(
				f"Claude CLI provider: `{settings.claude_cli_path()}` not found on PATH."
			) from error
		except subprocess.TimeoutExpired as error:
			raise RuntimeError(f"Claude CLI provider: no reply within {timeout}s.") from error

	if not (completed.stdout or "").strip():
		raise RuntimeError(
			f"Claude CLI provider: empty stdout (exit {completed.returncode}): "
			f"{(completed.stderr or '').strip()[:500]}"
		)

	try:
		payload = json.loads(completed.stdout)
	except json.JSONDecodeError as error:
		raise RuntimeError(
			f"Claude CLI provider: stdout was not JSON (exit {completed.returncode}): "
			f"{completed.stdout.strip()[:500]}"
		) from error

	# Read the payload before the exit code: a rejected model or an API failure puts the
	# human-readable reason in `result` and sets `is_error`, and the exit code — which is
	# 0 for some of those failures and 1 for others — never carries more than the flag does.
	if payload.get("is_error") or completed.returncode != 0:
		raise RuntimeError(
			f"Claude CLI provider: exit {completed.returncode}: {str(payload.get('result') or '')[:500]}"
		)

	content = (payload.get("result") or "").strip()
	if not content:
		raise RuntimeError("Claude CLI provider: reply carried no result text.")
	if response_format:
		content = json_content(content)

	usage = payload.get("usage") or {}
	cache_creation_tokens = usage.get("cache_creation_input_tokens") or 0
	cache_read_tokens = usage.get("cache_read_input_tokens") or 0
	return {
		"id": payload.get("session_id"),
		"model": served_model(payload) or model,
		"requested_model": model,
		"choices": [{"index": 0, "message": {"role": "assistant", "content": content}}],
		"usage": {
			# Claude Code's own system prompt is re-sent every call, so the cache figures are
			# the bulk of the tokens and belong in the prompt total, not hidden beside it.
			"prompt_tokens": (usage.get("input_tokens") or 0) + cache_creation_tokens + cache_read_tokens,
			"completion_tokens": usage.get("output_tokens") or 0,
			"cost": None,
			"cache_creation_tokens": cache_creation_tokens,
			"cache_read_tokens": cache_read_tokens,
			"subscription_cost_usd": payload.get("total_cost_usd"),
		},
	}


def split_messages(messages: list, workspace: str) -> tuple[str, str]:
	"""Flatten OpenAI-shaped messages into (system prompt, single prompt) for `claude -p`.

	The CLI takes one prompt and one system prompt, so a multi-turn exchange has to be
	rendered back into text; roles are kept as labels rather than dropped so a judge
	prompt that shows an assistant draft still reads as one.
	"""
	system_parts: list[str] = []
	prompt_parts: list[str] = []
	for message in messages:
		role = message.get("role") or "user"
		text = message_text(message.get("content"), workspace)
		if role == "system":
			system_parts.append(text)
		elif role == "assistant":
			prompt_parts.append(f"Assistant: {text}")
		else:
			prompt_parts.append(text)
	return "\n\n".join(system_parts).strip(), "\n\n".join(prompt_parts).strip()


def message_text(content, workspace: str) -> str:
	"""One message's content as prompt text, with any image spilled into `workspace` first.

	The CLI has no inline-image argument, but it will read an image off disk, which is how
	the VLM and judge passes get their page across: the data URL is decoded into the
	throwaway working directory and the prompt points at it. Anything else — a remote image
	URL, an audio part — is refused rather than silently dropped, because a vision prompt
	that loses its page still reads as a valid prompt and would come back confidently wrong.
	"""
	if isinstance(content, str):
		return content
	parts = []
	for part in content or []:
		if not isinstance(part, dict):
			continue
		if part.get("type") == "text":
			parts.append(part.get("text") or "")
		elif part.get("type") == "image_url":
			path = write_data_url(part.get("image_url", {}).get("url") or "", workspace, len(parts))
			parts.append(f"The page image for this task is the file {path} — read it before answering.")
		else:
			raise RuntimeError(f"Claude CLI provider: cannot send a `{part.get('type')}` content part.")
	return "\n".join(parts)


def write_data_url(url: str, workspace: str, index: int) -> str:
	"""Decode a `data:image/...;base64,…` URL into `workspace`, returning the file path."""
	match = re.match(r"data:image/([A-Za-z0-9.+-]+);base64,(.+)$", url, re.DOTALL)
	if not match:
		raise RuntimeError(
			"Claude CLI provider: image parts must be base64 data URLs; "
			f"got `{url[:60]}`. Use OpenRouter for remote image URLs."
		)
	path = Path(workspace) / f"page-{index}.{match.group(1)}"
	path.write_bytes(base64.b64decode(match.group(2)))
	return str(path)


def claude_cli_model(model: str) -> str:
	"""Map an OpenRouter model id onto a `--model` argument, or "" for the CLI's own default.

	Two differences for an Anthropic id, both measured against `claude --model`: the vendor
	prefix has to go, and the CLI spells the version with dashes (`claude-sonnet-4-6`) where
	OpenRouter spells it with a dot (`anthropic/claude-sonnet-4.6`).

	A non-Anthropic id (the cleanup and classifier passes default to `google/gemini-2.5-flash`)
	has no CLI equivalent at all, and passing it through gets the whole call rejected with
	`is_error`. This lane exists so a local site can run *every* feature off the subscription,
	so the argument is dropped and the account's own default model answers. Nothing is
	silently mislabelled: the response reports what actually served under `model`, keeps the
	asked-for id under `requested_model`, and the metrics buffer records both.
	"""
	if not model.startswith("anthropic/"):
		return ""
	return model.split("/", 1)[1].replace(".", "-")


def served_model(payload: dict) -> str:
	"""The model the CLI actually billed, read off its `modelUsage` map."""
	return next(iter(payload.get("modelUsage") or {}), "")


def json_content(text: str) -> str:
	"""The JSON document inside a CLI reply, or a RuntimeError naming what came back instead.

	Three shapes have to survive, because the CLI gives no structured-output guarantee and
	drifts between them: bare JSON, a ```json fence, and a fence buried in prose ("Here is
	the JSON: ```…```"). What must NOT survive is a chatty non-answer — `frappe.parse_json`
	hands a non-JSON string straight back, so the reranker would read it as a batch that
	scored nothing and quietly drop every candidate. Raising here turns that into a failure
	the caller's existing fallback can see.
	"""
	candidates = [match.strip() for match in FENCE_PATTERN.findall(text)]
	candidates.append(text.strip())
	candidates.extend(bracket_span(text, opener, closer) for opener, closer in (("{", "}"), ("[", "]")))
	for candidate in candidates:
		if not candidate:
			continue
		try:
			json.loads(candidate)
		except json.JSONDecodeError:
			continue
		return candidate
	raise RuntimeError(f"Claude CLI provider: expected JSON, got: {text.strip()[:500]}")


def bracket_span(text: str, opener: str, closer: str) -> str:
	"""The outermost `opener`…`closer` span in `text`, for a reply that wraps JSON in prose."""
	start = text.find(opener)
	end = text.rfind(closer)
	return text[start : end + 1] if 0 <= start < end else ""
