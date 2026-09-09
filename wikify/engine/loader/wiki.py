from __future__ import annotations

import re
from collections.abc import Callable

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_PAGEREF_RE = re.compile(r"((?:see|refer(?:\s+to)?)\s+)?(page\s*no\.?|page|pg\.?|p\.)\s*(\d{1,4})\b", re.I)


def slugify(text: str) -> str:
	return _SLUG_RE.sub("-", text.lower()).strip("-")[:60] or "page"


def is_internal_ref(cue: str | None, kind: str, num: int, page_count: int) -> bool:
	return (bool(cue) or "no" in kind.lower()) and 1 <= num <= page_count


def rewrite_page_refs(
	markdown: str,
	page_count: int,
	route_for_page: Callable[[int], str | None],
	current_route: str | None = None,
) -> tuple[str, int]:
	links = [0]

	def repl(m: re.Match) -> str:
		cue, kind, num = m.group(1), m.group(2), int(m.group(3))
		if is_internal_ref(cue, kind, num, page_count):
			route = route_for_page(num)
			if route and route != current_route:
				links[0] += 1
				return f"[{m.group(0)}](/{route})"
		return m.group(0)

	return _PAGEREF_RE.sub(repl, markdown or ""), links[0]
