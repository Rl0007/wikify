"""Split per-page markdown into hierarchical sections, tracking page ranges.

A section spans from one heading until the next heading of equal-or-higher level.
The embedded-ToC level map (if present) overrides parsed heading levels.

Ported verbatim from the POC `loader/sectionizer.py`; the only change is the import
path for `correct_level` (now `wikify.engine.loader.toc`). The `Section` dataclass is
the boundary the ORM seam (`store.replace_sections`) consumes to build the tree.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

from wikify.engine.loader.toc import correct_level

# A title lands in `Source Section.title`, a Data column capped at `frappe.db.VARCHAR_LEN`.
# An over-length title used to raise CharacterLengthExceededError mid-`replace_sections`,
# aborting the rebuild and silently dropping every section after the offending heading —
# on the 236-page ICAI referencer that cost pages 20-236 (93.6% of the document). The
# loader stays frappe-free, so the limit is mirrored here and pinned by a test.
MAX_TITLE_LENGTH = 140

# A heading that reappears at the top of many pages is the chapter's running page header,
# not a new section each time. Only the first occurrence opens a section; the repeats are
# page furniture and are dropped so the body flows on into the open section.
RUNNING_HEADER_MIN_PAGES = 3
TOP_OF_PAGE_LINES = 3

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
# Numbered headings ("1.", "1.1", "1.1.1") encode their own level — recover it.
# Allow an optional trailing dot so top-level "1." is caught, not just "1.1".
_NUM_RE = re.compile(r"^(\d+(?:\.\d+)*)\.?\s+\S")
_LEADING_NUM = re.compile(r"^(\d+)\b")
_DOUBLE_NUM = re.compile(r"^\d+\.\s+\d")  # e.g. "6. 3.1 ..." — a list item, not a chapter


def _clean_title(raw: str) -> str:
	# pymupdf4llm emits emphasized headings (e.g. `_**Verbal Orders**_`); strip the
	# wrapping bold/italic markers (`*` and `_`) so the tree title is plain text. This
	# also helps level inference — `_**2.1 Foo**_` → `2.1 Foo` now matches the numbering
	# regex — and stops a fully-bold numbered chapter being mis-demoted as a list item.
	title = raw.strip().strip("*_").strip()
	if len(title) <= MAX_TITLE_LENGTH:
		return title
	clipped = title[: MAX_TITLE_LENGTH - 1]
	head, _, _ = clipped.rpartition(" ")
	return f"{(head or clipped).rstrip()}…"


def _infer_level(title: str, fallback: int) -> int:
	m = _NUM_RE.match(title)
	if m:
		return min(6, m.group(1).count(".") + 1)
	return fallback


def _chapter_num(title: str) -> int | None:
	m = _LEADING_NUM.match(title)
	return int(m.group(1)) if m else None


def _looks_like_list_item(title: str) -> bool:
	"""A numbered 'heading' with a stray bold marker or double numbering is really
	a list item the parser mis-read (e.g. '2. **Maternal...', '6. 3.1 Eclampsia')."""
	return "*" in title or bool(_DOUBLE_NUM.match(title))


def running_header_titles(pages: list[tuple[int, str]]) -> set[str]:
	"""Cleaned heading titles that are really the chapter's running page header.

	A section heading is written once; a running header is re-stamped at the top of every
	page of its chapter, so the parser emits it as a fresh heading per page. Detected
	structurally — same title heading pages, most of them within the first few non-blank
	lines of the page — so no document-specific strings are needed.

	# ponytail: repetition + top-of-page placement is the whole signal; a genuine heading
	# that opens three or more short pages of its own (a glossary of one-term-per-page,
	# say) would be misread as furniture — require the occurrences to carry no body of
	# their own if such a corpus turns up.
	"""
	occurrences: dict[str, list[int]] = defaultdict(list)
	pages_seen: dict[str, set[int]] = defaultdict(set)
	for page_no, md in pages:
		for position, line in enumerate(line for line in md.splitlines() if line.strip()):
			match = _HEADING_RE.match(line)
			if not match:
				continue
			title = _clean_title(match.group(2))
			occurrences[title].append(position)
			pages_seen[title].add(page_no)
	return {
		title
		for title, positions in occurrences.items()
		if len(pages_seen[title]) >= RUNNING_HEADER_MIN_PAGES
		and sum(1 for position in positions if position < TOP_OF_PAGE_LINES) * 2 >= len(positions)
	}


@dataclass
class Section:
	title: str
	level: int
	hierarchy_path: list[str]
	page_start: int
	page_end: int
	markdown: str = ""
	section_type: str | None = None


def sectionize(pages: list[tuple[int, str]], level_map: dict[str, int] | None = None) -> list[Section]:
	"""pages: list of (page_no, markdown). Returns ordered sections."""
	level_map = level_map or {}
	sections: list[Section] = []
	stack: list[tuple[int, str]] = []  # (level, title) for hierarchy path
	current: Section | None = None
	buf: list[str] = []
	max_chapter = 0  # highest accepted top-level chapter number (sequence validation)
	running_headers = running_header_titles(pages)
	opened_headers: set[str] = set()

	def flush():
		if current is not None:
			current.markdown = "\n".join(buf).strip()
			sections.append(current)

	for page_no, md in pages:
		for line in md.splitlines():
			m = _HEADING_RE.match(line)
			if m:
				title = _clean_title(m.group(2))
				if title in running_headers:
					# First stamp opens the chapter; every later one is page furniture and
					# is dropped, so the page's body continues the section already open.
					if title in opened_headers:
						if current is not None:
							current.page_end = page_no
						continue
					opened_headers.add(title)
				flush()
				buf = []
				# Embedded ToC wins; else numbering; else the parser's '#' depth.
				level = correct_level(title, _infer_level(title, len(m.group(1))), level_map)
				# Heading validation: a numbered top-level heading that is out of
				# chapter sequence (number not greater than the last accepted chapter)
				# or malformed is a list item mis-read as a chapter — demote it so it
				# nests under the current chapter instead of becoming a new one.
				if level == 1 and title not in level_map:
					cnum = _chapter_num(title)
					if cnum is not None:
						if _looks_like_list_item(title) or cnum <= max_chapter:
							level = 2
						else:
							max_chapter = cnum
				while stack and stack[-1][0] >= level:
					stack.pop()
				stack.append((level, title))
				current = Section(
					title=title,
					level=level,
					hierarchy_path=[t for _, t in stack],
					page_start=page_no,
					page_end=page_no,
				)
			else:
				if current is not None:
					current.page_end = page_no
				buf.append(line)

	flush()

	# Collapse headings the parser re-emitted as per-page running titles.
	# Numbered headings (e.g. "3.", "3.1") should be unique, so merge ALL their
	# occurrences (running chapter headers span many pages). Non-numbered headings
	# only merge when consecutive (they may legitimately recur, e.g. "General").
	merged: list[Section] = []
	numbered: dict[str, Section] = {}
	for sec in sections:
		key = sec.title.lower()
		is_numbered = bool(_NUM_RE.match(sec.title))
		prev = merged[-1] if merged else None
		if is_numbered and key in numbered:
			tgt = numbered[key]
			tgt.page_end = max(tgt.page_end, sec.page_end)
			tgt.markdown = (tgt.markdown + "\n" + sec.markdown).strip()
		elif prev and prev.level == sec.level and prev.title.lower() == key:
			prev.page_end = max(prev.page_end, sec.page_end)
			prev.markdown = (prev.markdown + "\n" + sec.markdown).strip()
		else:
			merged.append(sec)
			if is_numbered:
				numbered[key] = sec
	sections = merged

	# Content before the first heading becomes a synthetic "Preamble" section.
	if not sections and pages:
		whole = "\n".join(md for _, md in pages).strip()
		if whole:
			sections.append(
				Section(
					title="Preamble",
					level=1,
					hierarchy_path=["Preamble"],
					page_start=pages[0][0],
					page_end=pages[-1][0],
					markdown=whole,
				)
			)
	return sections
