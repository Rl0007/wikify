from __future__ import annotations

import re
import unicodedata
from collections import Counter
from difflib import SequenceMatcher

import frappe

SHINGLE_SIZE = 5

MIN_PAGE_MATCH_SCORE = 0.30
PAGE_TIE_MARGIN = 0.10

MIN_QUOTE_SCORE = 0.82
QUOTE_WINDOW_SLACK = 6
MAX_ANCHOR_CANDIDATES = 200

DASH_CHARS = "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"
SINGLE_QUOTE_CHARS = "\u2018\u2019\u201a\u201b\u2032"
DOUBLE_QUOTE_CHARS = "\u201c\u201d\u201e\u201f\u2033"
DROPPED_CHARS = frozenset("*_`#")
EXPANDED_CHARS = {"\u20b9": "rs"}

TOKEN_PATTERN = re.compile(r"[0-9a-z]+")
OPERATOR_CHARS = "%/=<>\u2264\u2265\u00d7\u00f7\u2260"
FIGURE_PATTERN = re.compile(
	f"[0-9a-z]*[0-9][0-9a-z]*|\\([+-]\\)|(?<![0-9a-z])[+-](?=[0-9])|[{OPERATOR_CHARS}]"
)
CITATION_MARKER = re.compile(r"\[(\d+)\]")
QUOTED_SPAN = re.compile(
	'["\u201c]([^"\u201c\u201d\n]{15,400})["\u201d]\\s*(?:\\([^)\n]{0,60}\\))?\\s*\\[(\\d+)\\]'
)

PAGE_FIELDS = ["name", "source_document", "page_no", "canonical_markdown"]
BATCH_SIZE = 500

NOT_LOCATED = {
	"found": False,
	"line_start": 0,
	"line_end": 0,
	"char_start": 0,
	"char_end": 0,
	"score": 0.0,
	"matched_text": "",
	"reason": "quote not found in the cited source",
}
FIGURES_DIFFER = "quote misstates a figure in the cited source"


def normalise(text: str) -> tuple[str, list[int]]:
	normalised: list[str] = []
	offsets: list[int] = []
	source = text or ""
	pending_space = False
	for index, char in enumerate(source):
		if char.isspace():
			pending_space = bool(normalised)
			continue
		if char in DROPPED_CHARS:
			continue
		if (
			char == ","
			and normalised
			and normalised[-1].isdigit()
			and index + 1 < len(source)
			and source[index + 1].isdigit()
		):
			continue
		if char in DASH_CHARS:
			mapped = "-"
		elif char in SINGLE_QUOTE_CHARS:
			mapped = "'"
		elif char in DOUBLE_QUOTE_CHARS:
			mapped = '"'
		elif char in EXPANDED_CHARS:
			mapped = EXPANDED_CHARS[char]
		elif ord(char) > 127:
			mapped = unicodedata.normalize("NFKC", char)
		else:
			mapped = char
		mapped = mapped.lower()
		if not mapped:
			continue
		if pending_space:
			normalised.append(" ")
			offsets.append(index)
			pending_space = False
		normalised.extend(mapped)
		offsets.extend([index] * len(mapped))
		pending_space = char in EXPANDED_CHARS
	return "".join(normalised), offsets


def tokens_with_offsets(normalised: str, offsets: list[int]) -> list[tuple[str, int, int]]:
	return [
		(match.group(0), offsets[match.start()], offsets[match.end() - 1] + 1)
		for match in TOKEN_PATTERN.finditer(normalised)
	]


def get_tokens(text: str) -> list[str]:
	normalised, offsets = normalise(text)
	return [token for token, _, _ in tokens_with_offsets(normalised, offsets)]


def get_figures(text: str) -> list[str]:
	normalised, _ = normalise(text)
	return FIGURE_PATTERN.findall(normalised)


def figure_window(quote: str, source: str, char_start: int, char_end: int) -> str:
	quote_tokens = tokens_with_offsets(*normalise(quote))
	if not quote_tokens:
		return source[char_start:char_end]
	lead = quote_tokens[0][1]
	tail = len(quote) - quote_tokens[-1][2]
	return source[max(0, char_start - lead) : min(len(source), char_end + tail)]


def figures_agree(quote: str, source: str, char_start: int, char_end: int) -> bool:
	return get_figures(quote) == get_figures(figure_window(quote, source, char_start, char_end))


def get_shingles(tokens: list[str], size: int = SHINGLE_SIZE) -> set[tuple[str, ...]]:
	if not tokens:
		return set()
	if len(tokens) < size:
		return {tuple(tokens)}
	return {tuple(tokens[start : start + size]) for start in range(len(tokens) - size + 1)}


def get_source_text(chunk_or_section) -> str:
	if isinstance(chunk_or_section, str):
		return chunk_or_section
	if isinstance(chunk_or_section, dict):
		reader = chunk_or_section.get
	else:

		def reader(key, default=None):
			return getattr(chunk_or_section, key, default)

	for key in ("markdown", "canonical_markdown", "text"):
		value = reader(key, None)
		if value:
			return value
	return ""


def format_span(source: str, char_start: int, char_end: int, score: float) -> dict:
	return {
		"found": True,
		"reason": None,
		"line_start": source.count("\n", 0, char_start) + 1,
		"line_end": source.count("\n", 0, max(char_start, char_end - 1)) + 1,
		"char_start": char_start,
		"char_end": char_end,
		"score": round(float(score), 4),
		"matched_text": source[char_start:char_end],
	}


def best_token_window(
	quote_tokens: list[str], source_tokens: list[tuple[str, int, int]]
) -> tuple[int, int, float] | None:
	if not quote_tokens or not source_tokens:
		return None
	words = [token for token, _, _ in source_tokens]
	counts = Counter(words)
	anchor = min(
		range(len(quote_tokens)), key=lambda index: counts.get(quote_tokens[index]) or len(words) + 1
	)
	positions = [index for index, word in enumerate(words) if word == quote_tokens[anchor]]
	if not positions:
		return None

	width = len(quote_tokens) + QUOTE_WINDOW_SLACK
	best: tuple[int, int, float] | None = None
	for position in positions[:MAX_ANCHOR_CANDIDATES]:
		start = max(0, position - anchor - QUOTE_WINDOW_SLACK // 2)
		matcher = SequenceMatcher(None, quote_tokens, words[start : start + width], autojunk=False)
		score = matcher.ratio()
		blocks = [block for block in matcher.get_matching_blocks() if block.size]
		if not blocks or (best is not None and score <= best[2]):
			continue
		best = (start + blocks[0].b, start + blocks[-1].b + blocks[-1].size - 1, score)
	return best


def locate_quote(quote: str, chunk_or_section) -> dict:
	source = get_source_text(chunk_or_section)
	normalised_quote, quote_offsets = normalise(quote or "")
	normalised_source, source_offsets = normalise(source)
	if not normalised_quote or not normalised_source:
		return dict(NOT_LOCATED)

	position = normalised_source.find(normalised_quote)
	if position != -1:
		return format_span(
			source,
			source_offsets[position],
			source_offsets[position + len(normalised_quote) - 1] + 1,
			1.0,
		)

	quote_tokens = [token for token, _, _ in tokens_with_offsets(normalised_quote, quote_offsets)]
	source_tokens = tokens_with_offsets(normalised_source, source_offsets)
	best = best_token_window(quote_tokens, source_tokens)
	if best is None:
		return dict(NOT_LOCATED)
	first, last, score = best
	if score < MIN_QUOTE_SCORE:
		return {**NOT_LOCATED, "score": round(float(score), 4)}

	span = format_span(source, source_tokens[first][1], source_tokens[last][2], score)
	if not figures_agree(quote, source, span["char_start"], span["char_end"]):
		return {**NOT_LOCATED, "score": span["score"], "reason": FIGURES_DIFFER}
	return span


def build_page_index(pages: list[dict]) -> list[tuple[int, set[tuple[str, ...]], str]]:
	index = []
	for page in pages:
		tokens = get_tokens(page.get("canonical_markdown") or "")
		index.append((page["page_no"], get_shingles(tokens), f" {' '.join(tokens)} "))
	return index


def page_scores(tokens: list[str], page_index: list[tuple[int, set, str]]) -> list[tuple[float, int]]:
	if len(tokens) >= SHINGLE_SIZE:
		wanted = get_shingles(tokens)
		return [(len(wanted & shingles) / len(wanted), page_no) for page_no, shingles, _ in page_index]
	needle = f" {' '.join(tokens)} "
	return [(1.0 if needle in run else 0.0, page_no) for page_no, _, run in page_index]


def resolve_page(text: str, page_index: list[tuple[int, set, str]], fallback_page: int = 0) -> dict:
	tokens = get_tokens(text)
	if not tokens or not page_index:
		return {"page_no": fallback_page, "page_approximate": True, "page_score": 0.0}

	scored = sorted(page_scores(tokens, page_index), key=lambda entry: (-entry[0], entry[1]))
	best_score, best_page = scored[0]
	runner_up = scored[1][0] if len(scored) > 1 else 0.0
	if best_score < MIN_PAGE_MATCH_SCORE or best_score - runner_up < PAGE_TIE_MARGIN:
		return {"page_no": fallback_page, "page_approximate": True, "page_score": round(best_score, 4)}
	return {"page_no": best_page, "page_approximate": False, "page_score": round(best_score, 4)}


def get_rows_by_name(doctype: str, names: list[str], fields: list[str]) -> list[dict]:
	unique = [name for name in dict.fromkeys(names) if name]
	rows: list[dict] = []
	for start in range(0, len(unique), BATCH_SIZE):
		rows.extend(
			frappe.get_all(
				doctype, filters={"name": ["in", unique[start : start + BATCH_SIZE]]}, fields=fields
			)
		)
	return rows


def get_pages_by_document(
	document_names: list[str], page_numbers: list[int] | None = None
) -> dict[str, list[dict]]:
	documents = [name for name in dict.fromkeys(document_names) if name]
	numbers = None if page_numbers is None else sorted({number for number in page_numbers if number})
	if not documents or numbers == []:
		return {}

	grouped: dict[str, list[dict]] = {}
	for start in range(0, len(documents), BATCH_SIZE):
		filters: dict = {"source_document": ["in", documents[start : start + BATCH_SIZE]]}
		if numbers is not None:
			filters["page_no"] = ["in", numbers]
		for row in frappe.get_all("Source Page", filters=filters, fields=PAGE_FIELDS, order_by="page_no asc"):
			grouped.setdefault(row["source_document"], []).append(row)
	return grouped


def page_line_span(text: str, pages: list[dict], page_no: int) -> tuple[int, int]:
	for page in pages:
		if page["page_no"] != page_no:
			continue
		located = locate_quote(text, page)
		if located["found"]:
			return located["line_start"], located["line_end"]
	return 0, 0


def page_range(citation: dict) -> list[int]:
	start = int(citation.get("page_start") or 0)
	end = int(citation.get("page_end") or start)
	if not start:
		return []
	return list(range(start, max(start, end) + 1))


def check_quote(quote: str, citation: dict, pages_by_document: dict[str, list[dict]]) -> dict:
	located = locate_quote(quote, citation)
	result = {
		"status": "verified" if located["found"] else "unverified",
		"reason": located.get("reason"),
		"line_start": located["line_start"],
		"line_end": located["line_end"],
		"char_start": located["char_start"],
		"char_end": located["char_end"],
		"score": located["score"],
		"page_no": int(citation.get("page_start") or 0),
		"page_approximate": True,
		"page_score": 0.0,
		"page_line_start": 0,
		"page_line_end": 0,
	}
	if not located["found"]:
		return result

	wanted = page_range(citation)
	pages = [
		page
		for page in pages_by_document.get(citation.get("source_document") or "", [])
		if page["page_no"] in wanted
	]
	result.update(resolve_page(located["matched_text"], build_page_index(pages), result["page_no"]))
	if result["page_approximate"]:
		return result

	result["page_line_start"], result["page_line_end"] = page_line_span(quote, pages, result["page_no"])
	return result


def verify_citations(answer_markdown: str, citations: list[dict]) -> dict:
	citations = citations or []
	pages_by_document = get_pages_by_document(
		[citation.get("source_document") for citation in citations],
		[number for citation in citations for number in page_range(citation)],
	)
	cited = {int(number) for number in CITATION_MARKER.findall(answer_markdown or "")}

	checked: list[dict] = []
	for position, citation in enumerate(citations, start=1):
		quote = (citation.get("quote") or "").strip()
		row = {
			"index": position,
			"section": citation.get("section"),
			"source_document": citation.get("source_document"),
			"cited": position in cited,
			"quote": quote or None,
			"status": "unquoted",
			"reason": None if quote else "citation carries no quote",
		}
		if quote:
			row.update(check_quote(quote, citation, pages_by_document))
		checked.append(row)

	inline: list[dict] = []
	for match in QUOTED_SPAN.finditer(answer_markdown or ""):
		quote, number = match.group(1).strip(), int(match.group(2))
		if not 1 <= number <= len(citations):
			inline.append(
				{
					"index": number,
					"quote": quote,
					"status": "unverified",
					"reason": "citation marker has no source",
				}
			)
			continue
		inline.append(
			{"index": number, "quote": quote, **check_quote(quote, citations[number - 1], pages_by_document)}
		)

	statuses = [row["status"] for row in checked + inline]
	return {
		"citations": checked,
		"quotes": inline,
		"total": len(statuses),
		"verified": statuses.count("verified"),
		"unverified": statuses.count("unverified"),
		"unquoted": statuses.count("unquoted"),
		"grounded": "unverified" not in statuses,
	}


def attach_verified_quotes(answer_markdown: str, citations: list[dict]) -> list[dict]:
	report = verify_citations(answer_markdown, citations)
	best_inline: dict[int, dict] = {}
	for row in report["quotes"]:
		current = best_inline.get(row["index"])
		if current is None or (row["status"] == "verified" and current["status"] != "verified"):
			best_inline[row["index"]] = row

	attached = []
	for row in report["citations"]:
		citation = dict(citations[row["index"] - 1])
		chosen = row if row["status"] == "verified" else best_inline.get(row["index"], row)
		citation.update(
			{
				"quote": chosen.get("quote"),
				"quote_status": chosen.get("status"),
				"quote_line_start": chosen.get("line_start") or 0,
				"quote_line_end": chosen.get("line_end") or 0,
				"quote_page_no": chosen.get("page_no") or 0,
				"quote_page_approximate": bool(chosen.get("page_approximate", True)),
				"quote_page_line_start": chosen.get("page_line_start") or 0,
				"quote_page_line_end": chosen.get("page_line_end") or 0,
			}
		)
		attached.append(citation)
	return attached
