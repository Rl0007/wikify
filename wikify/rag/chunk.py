"""Source Section → Chunk. Structure-aware splitting + contextual-retrieval prefixes.

Two ideas carry the retrieval quality here:

1. **Parent-document retrieval.** A chunk is only an addressing device — every chunk keeps
   its `section` (the Source Section name), and search expands hits back up to the whole
   section before showing them. So chunks can be small enough to embed well without the
   reader ever seeing a truncated fragment.
2. **Contextual retrieval.** `embed_text` is prefixed with the document title and the
   section's hierarchy path, so a chunk that says only "Your task." still embeds near
   "beat the clock". The stored `text` stays clean — the prefix is retrieval scaffolding,
   not content, and must never reach the reader.

Every chunk also carries its own **provenance**: the single page it came from (matched back
against `Source Page.canonical_markdown`, not inherited from the section's page range), and
its line span both inside the section and inside that page. A section can run across a dozen
pages, so "see pages 10-12" is not a claim a student can check; "page 11, lines 14-18" is.
When the page match is ambiguous the chunk keeps the range start and is flagged
`page_approximate` rather than asserting a precision it does not have.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import frappe

from wikify.rag import evidence

CONTEXT_SEPARATOR = " \u203a "  # " > " re-typeset for the breadcrumb prefix

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
	# Provenance. `page_no` is the page this chunk's text was actually matched back to;
	# `page_approximate` says it is the range start because the match was ambiguous. The
	# `line_*` pair indexes the parent section's markdown, `page_line_*` the resolved page's.
	page_no: int = 0
	page_approximate: bool = True
	line_start: int = 0
	line_end: int = 0
	page_line_start: int = 0
	page_line_end: int = 0
	# A breadcrumb section whose body lives in its children: it is indexed so exhaustive
	# filter mode can still return it, and kept out of the similarity legs (see `search`).
	title_only: bool = False


def is_heading(block: str) -> bool:
	return block.lstrip().startswith("#")


def markdown_blocks(markdown: str) -> list[str]:
	"""Split markdown into atomic blocks: a heading always starts one, blank lines end one."""
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
	"""Last-resort cut for a run of text with no whitespace to break on.

	A base64 blob or a 50k-character OCR run has no word boundary, so whitespace splitting
	returns it whole and it blows past the embedder's context window as a single chunk.
	"""
	if len(text) <= target:
		return [text]
	return [text[start : start + target] for start in range(0, len(text), target)]


def split_long_block(block: str, target: int) -> list[str]:
	"""Break one oversized block on whitespace — a word is never cut in half."""
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
	"""The last `chars` of `text`, snapped forward to the next word boundary."""
	if len(text) <= chars:
		return text
	tail = text[-chars:]
	space = tail.find(" ")
	return tail[space + 1 :] if space != -1 else tail


def split_pieces(markdown: str, target: int = CHUNK_TARGET_CHARS) -> list[str]:
	"""Split a section body into embeddable pieces on structural boundaries, no overlap.

	Blocks (headings / paragraphs) are packed greedily up to `target`; only a single block
	that is itself oversized is split mid-block, and then only on whitespace.

	A heading never ends up alone: it travels with the body underneath it, even when that
	pushes the piece past `target` by the heading line. A chunk that reads "## Benefits" and
	nothing else embeds as noise and cites as a dead end.

	Overlap is deliberately a separate step: a piece here is still a contiguous run of the
	section, which is what lets `evidence.locate_quote` pin it to a line span. Once the
	previous piece's tail is glued on, the text no longer maps to one place in the source.
	"""
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
					# Nothing but headings pending, and they already fill a piece on their own
					# (a table of contents, or a deep heading stack). Emit them so the buffer
					# can't grow without bound — carrying them forward forever would eventually
					# produce one piece the size of the section, and dropping them would delete
					# them from `text`, the FTS column, so a section stops matching its own title.
					# ponytail: the emitted piece is headings-only and embeds poorly; give it the
					# first body block underneath it if a real corpus starts hitting this branch.
					pieces.append("\n\n".join(carried))
					carried = []
				current = carried
			current.append(part)
	if current:
		pieces.append("\n\n".join(current))
	return pieces


def add_overlap(pieces: list[str], overlap: int = CHUNK_OVERLAP_CHARS) -> list[str]:
	"""Give each piece after the first the previous piece's tail, so a fact straddling a
	boundary is still retrievable from either side."""
	if overlap <= 0:
		return pieces
	return [
		piece if position == 0 else f"{overlap_tail(pieces[position - 1], overlap)}\n\n{piece}"
		for position, piece in enumerate(pieces)
	]


def split_markdown(
	markdown: str, target: int = CHUNK_TARGET_CHARS, overlap: int = CHUNK_OVERLAP_CHARS
) -> list[str]:
	"""The stored chunk texts for a section body: structural pieces, then overlap."""
	return add_overlap(split_pieces(markdown, target), overlap)


def contextual_prefix(document_title: str, hierarchy_path: str) -> str:
	"""The retrieval breadcrumb: document title, then the section's ancestor titles.

	Written with the single right-pointing angle quote (the separator the UI and the answer
	citations also use); the source keeps it as an escape so ruff's ambiguous-unicode rule
	stays on for everything else.
	"""
	path = CONTEXT_SEPARATOR.join(part.strip() for part in (hierarchy_path or "").split(">") if part.strip())
	crumbs = [crumb for crumb in (document_title, path) if crumb]
	return CONTEXT_SEPARATOR.join(crumbs)


def section_pages(rows: list[dict]) -> list[int]:
	"""Every page number the given section rows declare — the pages provenance will read."""
	wanted: set[int] = set()
	for row in rows:
		start = int(row.get("page_start") or 0)
		if start:
			wanted.update(range(start, max(start, int(row.get("page_end") or start)) + 1))
	return sorted(wanted)


def section_page_index(row: dict, pages: list[dict]) -> list[tuple[int, set]]:
	"""Shingle only the pages inside this section's declared range — once per section."""
	start = int(row.get("page_start") or 0)
	end = int(row.get("page_end") or start)
	if not start:
		return []
	wanted = range(start, max(start, end) + 1)
	return evidence.build_page_index([page for page in pages if page["page_no"] in wanted])


def resolve_provenance(piece: str, markdown: str, row: dict, page_index, pages: list[dict]) -> dict:
	"""Where one piece sits: section lines, then the page it came from and its lines there."""
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
	"""Turn pre-fetched Source Section rows into Chunks (no per-row queries).

	`pages_by_document` supplies the parsed page text used to pin each chunk to a single
	page; omit it and every chunk falls back to its section's page range, flagged
	approximate. It is a defaulted argument so the callers that only want text keep working.
	"""
	pages_by_document = pages_by_document or {}
	chunks: list[Chunk] = []
	for row in rows:
		document = documents.get(row["source_document"]) or {}
		document_title = document.get("title") or row["source_document"]
		prefix = contextual_prefix(document_title, row.get("hierarchy_path") or "")
		route = wiki_routes.get(row.get("wiki_document") or "")
		markdown = row.get("markdown") or ""
		pieces = split_pieces(markdown)
		# A section with an empty body is a breadcrumb node whose text lives in its children.
		# It still gets a row, because `mode="filter"` promises every matching section and a
		# row-less section is invisible to it — carrying its title as the text so it stays
		# readable in a citation. `title_only` then keeps it out of the similarity legs: it
		# carries no answer but matches on title alone, so it outranks real content (measured:
		# an empty 76-character section ranked #1 in vector, hybrid AND FTS at once).
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
					# Resolved from the piece WITHOUT its overlap tail: the tail belongs to the
					# previous piece, so including it would drag the reported span backwards.
					**resolve_provenance(piece, markdown, row, page_index, pages),
				)
			)
	return chunks


def resolve_context(rows: list[dict]) -> tuple[dict[str, dict], dict[str, str]]:
	"""(documents by name, wiki route by Wiki Document name) for a batch of section rows."""
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
	"""Chunks for a batch of sections, reading each document's pages once for the whole batch.

	Per-section chunking used to re-read every page of the parent document once per section,
	so a ten-section propagation pass over a 236-page manual loaded 2,360 page rows to use
	a couple of dozen. Only the pages the batch's sections declare are fetched.
	"""
	rows = evidence.get_rows_by_name("Source Section", section_names, SECTION_FIELDS)
	if not rows:
		return []
	documents, wiki_routes = resolve_context(rows)
	pages = evidence.get_pages_by_document([row["source_document"] for row in rows], section_pages(rows))
	return build_chunks(rows, documents, wiki_routes, pages)


def chunks_for_section(section_name: str) -> list[Chunk]:
	return chunks_for_sections([section_name])


def chunks_for_project(project: str) -> list[Chunk]:
	"""Every chunk for every section of every document in a project (tree order)."""
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
	"""One Chunk as a LanceDB row. Colocated with the dataclass so a field added to `Chunk`
	and to `store.chunk_schema()` cannot be forgotten on the write path."""
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
