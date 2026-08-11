"""Cloud VLM parser: a rendered page image -> markdown via an OpenRouter model.

Used by remediation to re-parse visual / low-recall pages from their image. The
model is a `Wikify Settings` value (`vlm_model`) so it's switchable without code.

Ported from the POC `parsers/vlm_parser.py`. The page is already rendered upstream
(`pdf_utils.render_png` -> data URL), so this takes the data URL directly — no
on-demand re-render, no `image_to_data_url` disk round-trip.
"""

from __future__ import annotations

from wikify.engine import llm, settings
from wikify.engine.loader.cleanup import strip_outer_markdown_fence
from wikify.engine.loader.context import context_block, instruction_block

_PROMPT = (
	"Convert this PDF page into clean, faithful GitHub-flavored Markdown, preserving heading "
	"levels, lists and reading order.\n"
	"\n"
	"ROUTE EACH BLOCK BY ITS SHAPE — this is the most important rule:\n"
	"1. TABULAR DATA (anything laid out as rows x columns: rate tables, slab tables, "
	"comparison grids, matrices) becomes an HTML <table>. Use <th> for header cells and "
	"rowspan / colspan for merged headers. Each source row is ONE <tr>, so every label stays "
	"in the same row as its own value. NEVER represent tabular data as a mermaid diagram: a "
	"flowchart is a tree and a table is a grid, so the row-to-value binding is destroyed and "
	"the reader cannot tell which value belongs to which row. Use a Markdown pipe table only "
	"for a genuinely flat grid with no merged cells.\n"
	"2. PROCESS FLOWS and DECISION TREES (boxes joined by arrows) become a ```mermaid fenced "
	"block using `flowchart TD`. Short node ids (A, B, C...). EVERY node label is wrapped in "
	'double quotes, with no exceptions — write A["Turnover > Rs 10 crore"], never '
	"A[Turnover > Rs 10 crore]. This matters most when the label contains brackets, which "
	'statutory references always do: A["CAPITAL ASSET<br>[Section 2(14)]"] is correct and '
	"A[CAPITAL ASSET<br>[Section 2(14)]] is broken. Inside a label, write a literal double "
	"quote as #quot; and use <br> for line breaks (never \\n). Arrows are -->. Every node must "
	"be joined to the diagram by an edge; if you cannot say what connects to what, it is not a "
	"flow — emit a table or a list instead.\n"
	"3. Everything else is prose: headings, paragraphs, lists.\n"
	"\n"
	"Transcribe text EXACTLY as printed. Copy rupee amounts, percentages, thresholds, dates "
	'and statutory references (e.g. "u/s 115BAC", "section 44AD", "First Proviso") character '
	"for character. Never round, re-word, convert or summarise a number, and never invent "
	"content.\n"
	"Output only the Markdown — no commentary, and no code fences except ```mermaid."
)


def parse_page_image(
	image_data_url: str,
	model: str | None = None,
	project_context: str = "",
	instruction: str = "",
	shape_hint: str = "",
) -> str:
	"""Markdown for a single page, read from its rendered image (data URL).

	`shape_hint` is `regions.shape_hint` for the page — a coarse "this page holds a grid /
	a flow" line from the deterministic layout pass. It does not tell the model where the
	cells are (it still reads the image); it stops the model reaching for a flowchart when
	what it is looking at is a table.
	"""
	preamble = context_block(project_context) + instruction_block(instruction) + shape_hint
	resp = llm.chat_completion(
		model or settings.get("vlm_model"),
		[
			{
				"role": "user",
				"content": [
					{"type": "text", "text": preamble + _PROMPT},
					{"type": "image_url", "image_url": {"url": image_data_url}},
				],
			}
		],
		label="vlm_parse",
		max_tokens=8192,
	)
	return strip_outer_markdown_fence((resp["choices"][0]["message"]["content"] or "").strip())
