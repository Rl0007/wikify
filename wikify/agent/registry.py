"""Tool dataclass + registry.

Builder's pattern — plain `Tool` instances collected from module-level `TOOLS` lists,
no decorator. Adding a capability = registering one Tool; the loop never changes.
Handlers call existing `api/` and `jobs/` functions; they don't re-implement pipeline
logic.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from wikify.agent.context import Ctx


@dataclass
class Tool:
	name: str
	side: Literal["server", "terminal"]
	description: str
	parameters: dict  # raw JSON-schema for the function args
	handler: Callable[[Ctx, dict], str]  # runs it; returns a result/summary string
	# Slice 14: a `confirm` tool is expensive/destructive — the loop holds it for a UI
	# confirm card and only runs it once the user approves (its name in `Ctx.approved`).
	# A `mutates` tool changed a DocType — the loop emits `wikify_agent_mutation` so open
	# Tree/Pages views refetch.
	confirm: bool = False
	mutates: bool = False


def build_default_registry() -> dict[str, Tool]:
	"""All available tools, keyed by name (read + write/reparse/pipeline/converse)."""
	from wikify.agent.tools import content, converse, pipeline, read, reparse, retrieve, taxonomy, tree

	registry: dict[str, Tool] = {}
	for module in (read, retrieve, tree, taxonomy, content, reparse, pipeline, converse):
		for tool in module.TOOLS:
			registry[tool.name] = tool
	return registry
