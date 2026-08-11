"""Wiki-generation helpers ported from the POC `loader/wiki.py`.

Only the *pure* pieces live here — slug derivation and the page-reference rewrite
regex — so they stay verbatim against the POC and unit-testable without Frappe. The
orchestration (creating Wiki Documents, resolving page N → a section's route) lives in
`engine/generate.py`, which injects the resolver into `rewrite_page_refs`.

The source PDFs cross-reference by **page number** ("refer Page No. 130", "see page
42"); a wiki has no page numbers, only links. After every Wiki Document exists (so
targets resolve), pass 2 rewrites internal page refs into wiki links. External book
citations ("Williams p820") are deliberately left as text — only refs with a see/refer
cue or the "Page No. N" form (and `N <= page_count`) are treated as internal.
"""

from __future__ import annotations

import re
from collections.abc import Callable

_SLUG_RE = re.compile(r"[^a-z0-9]+")
# Optional see/refer cue, the word page/pg/p (with optional "no"), then the number.
_PAGEREF_RE = re.compile(r"((?:see|refer(?:\s+to)?)\s+)?(page\s*no\.?|page|pg\.?|p\.)\s*(\d{1,4})\b", re.I)


def slugify(text: str) -> str:
	return _SLUG_RE.sub("-", text.lower()).strip("-")[:60] or "page"


def is_internal_ref(cue: str | None, kind: str, num: int, page_count: int) -> bool:
	"""A `_PAGEREF_RE` match is an internal cross-reference (vs an external citation like
	"Williams p820") when it carries a see/refer cue or the "Page No." form, and N is a
	real page of this PDF. The one definition behind link rewriting (here) and reference
	extraction (`engine.refs`)."""
	return (bool(cue) or "no" in kind.lower()) and 1 <= num <= page_count


def rewrite_page_refs(
	markdown: str,
	page_count: int,
	route_for_page: Callable[[int], str | None],
	current_route: str | None = None,
) -> tuple[str, int]:
	"""Rewrite internal "page N" references into `[text](/route)` wiki links.

	`route_for_page(n)` resolves a PDF page number to the route of the wiki page that
	covers it (or None). A ref is internal when it carries a see/refer cue or the
	"Page No." form *and* `1 <= N <= page_count`; external citations are left untouched.
	A ref resolving to the page it sits on is left as plain text. Returns the rewritten
	markdown and the number of links created.
	"""
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
