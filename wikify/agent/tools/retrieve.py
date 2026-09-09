from __future__ import annotations

from frappe import _

from wikify.agent.context import Ctx
from wikify.agent.registry import Tool
from wikify.rag import search as rag_search

EXCERPT_LIMIT = 700


def format_hits(hits: list[dict], mode: str) -> str:
	lines = [f"{len(hits)} result(s) ({mode}):"]
	for position, hit in enumerate(hits, start=1):
		type_label = f" ({hit['section_type']})" if hit.get("section_type") else ""
		lines.append(
			f"\n[{position}] {rag_search.crumb(hit)}{type_label} [{rag_search.page_label(hit)}] "
			f"<{hit['section']}> score={hit['score']:.4f}"
		)
		text = hit.get("text") or ""
		lines.append(text if len(text) <= EXCERPT_LIMIT else text[:EXCERPT_LIMIT] + "…")
	return "\n".join(lines)


def semantic_search(ctx: Ctx, args: dict) -> str:
	from wikify.api.rag import search

	query = (args.get("query") or "").strip()
	if not query:
		return _("Provide a `query` to search for.")
	result = search(
		query,
		project=ctx.default_project(args.get("project")),
		source_document=ctx.default_document(args.get("source_document")),
		section_type=args.get("section_type"),
		limit=args.get("limit") or 8,
		mode=args.get("mode") or "hybrid",
		use_router=False,
	)
	hits = result["hits"]
	if not hits:
		return _("Nothing in the index matches that. Try different wording, or drop the filters.")
	return format_hits(hits, result["mode"])


TOOLS = [
	Tool(
		name="semantic_search",
		side="server",
		# nosemgrep
		description=(
			"Search the indexed wiki content by meaning and by keyword, returning ranked "
			"excerpts with their section ids — follow up with read_section to read a hit in "
			"full, then search again. Use mode='filter' with a section_type when the user "
			"wants EVERY item of a kind (it returns all matches, not a top-k); use "
			"mode='hybrid' for open questions. Defaults to the attached project/document."
		),
		parameters={
			"type": "object",
			"properties": {
				"query": {"type": "string", "description": "What to search for."},
				"section_type": {
					"type": "string",
					"description": "Restrict to one Section Type (see list_section_types).",
				},
				"mode": {
					"type": "string",
					"enum": ["hybrid", "vector", "fts", "filter"],
					"description": "hybrid (default), vector, fts (keyword), or filter (all matches).",
				},
				"limit": {"type": "integer", "description": "Max results, default 8."},
				"project": {"type": "string", "description": "Optional Wikify Project scope."},
				"source_document": {"type": "string", "description": "Optional single-document scope."},
			},
			"required": ["query"],
		},
		handler=semantic_search,
	),
]
