from __future__ import annotations

import litellm

from wikify.engine import settings

litellm.drop_params = True

DEFAULT_AGENT_MODEL = "anthropic/claude-sonnet-4.6"


def resolve_model(explicit: str | None = None, project: str | None = None) -> str:
	if explicit:
		return explicit
	if project:
		import frappe

		model = frappe.db.get_value("Wikify Project", project, "agent_model")
		if model:
			return model
	return settings.get("agent_model") or DEFAULT_AGENT_MODEL


def agent_models() -> list[str]:
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
