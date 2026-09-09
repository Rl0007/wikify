from __future__ import annotations

import re
from dataclasses import dataclass

import frappe

from wikify.rag import evidence

CONTEXT_SEPARATOR = " \u203a "

# The contextual-retrieval prefix (document title + hierarchy path) is embedded with each
# chunk so a fragment carries where it came from — see `CONTEXT_SEPARATOR` and `embed_text`.
CHUNK_TARGET_CHARS = 1200
CHUNK_OVERLAP_CHARS = 150

SECTION_FIELDS = [
	"name",
	"source_document",
	"title",
	"section_type",
	"hierarchy_path",
	"page_start",
	"page_end",
	"markdown",
	"wiki_document",
]


@dataclass
class Chunk:
	id: str
	section: str
	source_document: str
	project: str
	text: str
	embed_text: str
	section_type: str | None
	hierarchy_path: str
	page_start: int
	page_end: int
	wiki_route: str | None
	page_no: int = 0
	# `page_no` is the page this chunk's text was actually matched back to; `page_approximate`
	# says it is the range start because the match was ambiguous. The `line_*` pair indexes the
	# parent section's markdown, `page_line_*` the resolved page's.
	page_approximate: bool = True
	line_start: int = 0
	line_end: int = 0
	page_line_start: int = 0
	page_line_end: int = 0
	# A breadcrumb section whose body lives in its children: indexed so exhaustive filter mode
	# can still return it, and kept out of the similarity legs (see `search`).
	title_only: bool = False


def is_heading(block: str) -> bool:
	return block.lstrip().startswith("#")


def markdown_blocks(markdown: str) -> list[str]:
	blocks: list[str] = []
	current: list[str] = []
	for line in markdown.splitlines():
		if is_heading(line) and current:
			blocks.append("\n".join(current))
			current = [line]
			continue
		if not line.strip():
			if current:
				blocks.append("\n".join(current))
				current = []
			continue
		current.append(line)
	if current:
		blocks.append("\n".join(current))
	return [block.strip() for block in blocks if block.strip()]


def split_on_characters(text: str, target: int) -> list[str]:
	if len(text) <= target:
		return [text]
	return [text[start : start + target] for start in range(0, len(text), target)]


def split_long_block(block: str, target: int) -> list[str]:
	if len(block) <= target:
		return [block]
	parts: list[str] = []
	current = ""
	for token in re.split(r"(\s+)", block):
		if current and len(current) + len(token) > target:
			parts.append(current.strip())
			current = token.strip()
			continue
		current += token
	if current.strip():
		parts.append(current.strip())
	return [piece for part in parts for piece in split_on_characters(part, target)]


def overlap_tail(text: str, chars: int) -> str:
	if len(text) <= chars:
		return text
	tail = text[-chars:]
	space = tail.find(" ")
	return tail[space + 1 :] if space != -1 else tail


def split_pieces(markdown: str, target: int = CHUNK_TARGET_CHARS) -> list[str]:
	markdown = (markdown or "").strip()
	if not markdown:
		return []
	if len(markdown) <= target:
		return [markdown]

	pieces: list[str] = []
	current: list[str] = []
	for block in markdown_blocks(markdown):
		for part in split_long_block(block, target):
			if current and len("\n\n".join(current)) + 2 + len(part) > target:
				carried: list[str] = []
				while current and is_heading(current[-1]):
					carried.insert(0, current.pop())
				if current:
					pieces.append("\n\n".join(current))
				elif len("\n\n".join(carried)) >= target:
					pieces.append("\n\n".join(carried))
					carried = []
				current = carried
			current.append(part)
	if current:
		pieces.append("\n\n".join(current))
	return pieces


def add_overlap(pieces: list[str], overlap: int = CHUNK_OVERLAP_CHARS) -> list[str]:
	if overlap <= 0:
		return pieces
	return [
		piece if position == 0 else f"{overlap_tail(pieces[position - 1], overlap)}\n\n{piece}"
		for position, piece in enumerate(pieces)
	]


def split_markdown(
	markdown: str, target: int = CHUNK_TARGET_CHARS, overlap: int = CHUNK_OVERLAP_CHARS
) -> list[str]:
	return add_overlap(split_pieces(markdown, target), overlap)


def contextual_prefix(document_title: str, hierarchy_path: str) -> str:
	path = CONTEXT_SEPARATOR.join(part.strip() for part in (hierarchy_path or "").split(">") if part.strip())
	crumbs = [crumb for crumb in (document_title, path) if crumb]
	return CONTEXT_SEPARATOR.join(crumbs)


def section_pages(rows: list[dict]) -> list[int]:
	wanted: set[int] = set()
	for row in rows:
		start = int(row.get("page_start") or 0)
		if start:
			wanted.update(range(start, max(start, int(row.get("page_end") or start)) + 1))
	return sorted(wanted)


def section_page_index(row: dict, pages: list[dict]) -> list[tuple[int, set]]:
	start = int(row.get("page_start") or 0)
	end = int(row.get("page_end") or start)
	if not start:
		return []
	wanted = range(start, max(start, end) + 1)
	return evidence.build_page_index([page for page in pages if page["page_no"] in wanted])


def resolve_provenance(piece: str, markdown: str, row: dict, page_index, pages: list[dict]) -> dict:
	in_section = evidence.locate_quote(piece, markdown)
	resolved = evidence.resolve_page(piece, page_index, int(row.get("page_start") or 0))
	provenance = {
		"page_no": resolved["page_no"],
		"page_approximate": resolved["page_approximate"],
		"line_start": in_section["line_start"],
		"line_end": in_section["line_end"],
		"page_line_start": 0,
		"page_line_end": 0,
	}
	if resolved["page_approximate"]:
		return provenance
	provenance["page_line_start"], provenance["page_line_end"] = evidence.page_line_span(
		piece, pages, resolved["page_no"]
	)
	return provenance


def build_chunks(
	rows: list[dict],
	documents: dict[str, dict],
	wiki_routes: dict[str, str],
	pages_by_document: dict[str, list[dict]] | None = None,
) -> list[Chunk]:
	pages_by_document = pages_by_document or {}
	chunks: list[Chunk] = []
	for row in rows:
		document = documents.get(row["source_document"]) or {}
		document_title = document.get("title") or row["source_document"]
		prefix = contextual_prefix(document_title, row.get("hierarchy_path") or "")
		route = wiki_routes.get(row.get("wiki_document") or "")
		markdown = row.get("markdown") or ""
		pieces = split_pieces(markdown)
		title_only = not pieces
		if title_only:
			pieces = [row.get("title") or row["name"]]
		pages = pages_by_document.get(row["source_document"]) or []
		page_index = [] if title_only else section_page_index(row, pages)
		for ordinal, (piece, text) in enumerate(zip(pieces, add_overlap(pieces), strict=True)):
			chunks.append(
				Chunk(
					id=f"{row['name']}::{ordinal}",
					section=row["name"],
					source_document=row["source_document"],
					project=document.get("project") or "",
					text=text,
					embed_text=f"{prefix}\n\n{text}" if prefix else text,
					section_type=row.get("section_type") or None,
					hierarchy_path=row.get("hierarchy_path") or "",
					page_start=row.get("page_start") or 0,
					page_end=row.get("page_end") or 0,
					wiki_route=route or None,
					title_only=title_only,
					**resolve_provenance(piece, markdown, row, page_index, pages),
				)
			)
	return chunks


def resolve_context(rows: list[dict]) -> tuple[dict[str, dict], dict[str, str]]:
	documents = {
		document["name"]: document
		for document in evidence.get_rows_by_name(
			"Source Document",
			[row["source_document"] for row in rows],
			["name", "title", "project"],
		)
	}
	wiki_routes = {
		page["name"]: page["route"]
		for page in evidence.get_rows_by_name(
			"Wiki Document",
			[row.get("wiki_document") for row in rows],
			["name", "route"],
		)
	}
	return documents, wiki_routes


def chunks_for_sections(section_names: list[str]) -> list[Chunk]:
	rows = evidence.get_rows_by_name("Source Section", section_names, SECTION_FIELDS)
	if not rows:
		return []
	documents, wiki_routes = resolve_context(rows)
	pages = evidence.get_pages_by_document([row["source_document"] for row in rows], section_pages(rows))
	return build_chunks(rows, documents, wiki_routes, pages)


def chunks_for_section(section_name: str) -> list[Chunk]:
	return chunks_for_sections([section_name])


def chunks_for_project(project: str) -> list[Chunk]:
	document_names = frappe.get_all("Source Document", filters={"project": project}, pluck="name")
	if not document_names:
		return []
	rows = frappe.get_all(
		"Source Section",
		filters={"source_document": ["in", document_names]},
		fields=SECTION_FIELDS,
		order_by="source_document asc, lft asc",
	)
	if not rows:
		return []
	documents, wiki_routes = resolve_context(rows)
	return build_chunks(rows, documents, wiki_routes, evidence.get_pages_by_document(document_names))


def chunk_row(item: Chunk, vector: list[float]) -> dict:
	return {
		"id": item.id,
		"section": item.section,
		"source_document": item.source_document,
		"project": item.project,
		"text": item.text,
		"embed_text": item.embed_text,
		"section_type": item.section_type,
		"hierarchy_path": item.hierarchy_path,
		"page_start": item.page_start,
		"page_end": item.page_end,
		"page_no": item.page_no,
		"page_approximate": item.page_approximate,
		"line_start": item.line_start,
		"line_end": item.line_end,
		"page_line_start": item.page_line_start,
		"page_line_end": item.page_line_end,
		"title_only": item.title_only,
		"wiki_route": item.wiki_route,
		"vector": vector,
	}
