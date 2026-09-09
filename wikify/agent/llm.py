"""litellm adapter for the agent — the only LLM client on the agent side.

The pipeline keeps its own requests-based `engine/llm.py` (scoring/cleanup); the agent
streams tool-calling completions through litellm against OpenRouter, reusing the same
key resolver (`engine.settings.openrouter_key`). Models are `openrouter/`-prefixed.
"""

from __future__ import annotations

import litellm

from wikify.engine import settings

# Send only params the target model supports (drop the rest) instead of erroring.
litellm.drop_params = True

# Built-in fallback when neither the session, its project, nor Wikify Settings names a
# model. A capable tool-caller.
DEFAULT_AGENT_MODEL = "anthropic/claude-sonnet-4.6"


def resolve_model(explicit: str | None = None, project: str | None = None) -> str:
	"""Resolve the agent model: explicit → project override → Settings default → built-in.

	The per-project `agent_model` (slice 11) wins over the site-wide
	`Wikify Settings.agent_model` (slice 16), which wins over `DEFAULT_AGENT_MODEL`.
	"""
	if explicit:
		return explicit
	if project:
		import frappe

		model = frappe.db.get_value("Wikify Project", project, "agent_model")
		if model:
			return model
	return settings.get("agent_model") or DEFAULT_AGENT_MODEL


def agent_models() -> list[str]:
	"""Model ids for the panel's picker.

	The resolved site default first, then the (real, in-use) pipeline model ids configured
	in Wikify Settings — so every option is a model OpenRouter already serves here. The
	user can still set a per-project override to anything via project settings.
	"""
	models = [settings.get("agent_model") or DEFAULT_AGENT_MODEL]
	for field in ("judge_model", "cleanup_model", "classifier_model", "vlm_model"):
		model = settings.get(field)
		if model and model not in models:
			models.append(model)
	return models


def _openrouter_model(model: str) -> str:
	return model if model.startswith("openrouter/") else f"openrouter/{model}"


def complete_with_tools(
	model: str, messages: list, tools: list, *, stream: bool = True, include_usage: bool = False
):
	"""Stream a tool-calling completion. `tools` is a list of `registry.Tool`.

	Returns the litellm streaming response (iterate chunks) when `stream`, else the
	full response object. `include_usage` asks the provider for a final, choice-less chunk
	carrying the token + cost totals — off by default because a caller that iterates
	chunks without guarding `chunk.choices` would trip over it.
	"""
	key = settings.openrouter_key()
	if not key:
		raise RuntimeError("OPENROUTER key not set; the agent is unavailable.")

	tool_schemas = [
		{
			"type": "function",
			"function": {
				"name": t.name,
				"description": t.description,
				"parameters": t.parameters,
			},
		}
		for t in tools
	] or None

	return litellm.completion(
		model=_openrouter_model(model),
		messages=messages,
		tools=tool_schemas,
		stream=stream,
		stream_options={"include_usage": True} if stream and include_usage else None,
		api_key=key,
		num_retries=2,
	)
