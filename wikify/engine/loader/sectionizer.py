from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

from wikify.engine.loader.toc import correct_level

MAX_TITLE_LENGTH = 140

RUNNING_HEADER_MIN_PAGES = 3
TOP_OF_PAGE_LINES = 3

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_NUM_RE = re.compile(r"^(\d+(?:\.\d+)*)\.?\s+\S")
_LEADING_NUM = re.compile(r"^(\d+)\b")
_DOUBLE_NUM = re.compile(r"^\d+\.\s+\d")


def _clean_title(raw: str) -> str:
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
	return "*" in title or bool(_DOUBLE_NUM.match(title))


def running_header_titles(pages: list[tuple[int, str]]) -> set[str]:
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
	level_map = level_map or {}
	sections: list[Section] = []
	stack: list[tuple[int, str]] = []
	current: Section | None = None
	buf: list[str] = []
	max_chapter = 0
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
					if title in opened_headers:
						if current is not None:
							current.page_end = page_no
						continue
					opened_headers.add(title)
				flush()
				buf = []
				level = correct_level(title, _infer_level(title, len(m.group(1))), level_map)
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
