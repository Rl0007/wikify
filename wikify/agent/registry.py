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
	parameters: dict
	handler: Callable[[Ctx, dict], str]
	confirm: bool = False
	mutates: bool = False


def build_default_registry() -> dict[str, Tool]:
	from wikify.agent.tools import content, converse, pipeline, read, reparse, retrieve, taxonomy, tree

	registry: dict[str, Tool] = {}
	for module in (read, retrieve, tree, taxonomy, content, reparse, pipeline, converse):
		for tool in module.TOOLS:
			registry[tool.name] = tool
	return registry
