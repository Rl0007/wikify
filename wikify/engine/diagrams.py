"""Server-side mermaid gate: nothing whose *meaning* is corrupt reaches the database.

The dangerous failure, measured on the ICAI referencer, is **a table drawn as a flowchart**.
`flowchart TD` is a tree grammar and a rate table is a 2-D grid, so the VLM flattens the grid
into two parallel branches — the income slabs under one parent, the surcharge rates under
another — with nothing binding a slab to its rate. A student reading it gets the rate wrong,
and confidently wrong beats missing on every exam-prep metric. Any flowchart carrying the
signature of a flattened grid is rejected, and the page keeps whatever HTML/pipe table and
prose the model also produced; when nothing tabular survives, the page crop goes in instead,
so the reader always has the original to read.

**Syntax is not judged here.** It used to be: this module re-implemented enough of mermaid's
grammar in regex to decide whether a block would parse, while the reader renders it with the
real parser client-side. Every construct the regex did not know was read as broken and the
diagram was discarded — `A & B --> C` is valid mermaid 11 and cost page 6 its diagram twice
before it was special-cased, which is the shape of the whole problem: the approximation can
only ever be behind. A block whose syntax is genuinely broken now fails where the real parser
lives: `utils/mermaid.js` catches the parse error and renders an error chip above the block's
own source, so the reader sees what was meant and that it did not draw. Note the crop fallback
below does NOT cover that path — it fires only on the rejection this module still makes — and
on a published Frappe Wiki page, which renders outside this SPA, such a block degrades to a
plain code block.

What survives is the part regex is actually good at, and it is a *content* judgment rather
than a grammatical one: reading node labels and edges to recognise a flattened grid. Guessing
which rate belongs to which slab would be the same failure with better syntax, so that
verdict stays.

Every block is also passed through one **lossless** repair: quoting node labels. ICAI prints
every statutory reference in square brackets, so the VLM writes `A[CAPITAL ASSET<br>[Section
2(14)]]` — correct content that mermaid cannot parse, because the inner `]` closes the node
early. `A["CAPITAL ASSET<br>[Section 2(14)]"]` is valid and reads identically. The repair only
ever adds quotes; it never drops or rewrites a character.

Pure text in, text out — no LLM, no I/O, no frappe imports.
"""

from __future__ import annotations

import re
from itertools import pairwise

# We only ever ask for flowcharts, and the flattened-grid signature is defined in terms of nodes
# and edges. Any other diagram type simply isn't analysed here — it is not rejected for being
# unfamiliar, which is the mistake this module used to make.
_HEADER_RE = re.compile(r"^\s*(?:flowchart|graph)\s+(?:TD|TB|BT|LR|RL)\s*$", re.IGNORECASE)
_MERMAID_BLOCK_RE = re.compile(
	r"^[ \t]*```[ \t]*mermaid[ \t]*\n(.*?)^[ \t]*```[ \t]*$", re.MULTILINE | re.DOTALL
)

_NODE_ID = r"[A-Za-z][A-Za-z0-9_]*"
# A quoted label may hold anything but a quote — including the brackets that make the unquoted
# form unparseable — so the quoted alternatives are tried first.
_QUOTED_LABEL = r'(?:\[\s*"[^"]*"\s*\]|\(\s*"[^"]*"\s*\)|\{\s*"[^"]*"\s*\})'
_LABEL = rf"(?:{_QUOTED_LABEL}|\[[^\[\]]*\]|\([^()]*\)|\{{[^{{}}]*\}}|>[^<>]*\])"
_ARROW_RE = re.compile(r"\s*(?:--+>|--+|-\.-+>|-\.-+|==+>|==+)\s*")
_PIECE_RE = re.compile(rf"^({_NODE_ID})\s*({_LABEL})?$")
_EDGE_LABEL_RE = re.compile(r"^\|[^|]*\|\s*")

# Directives we tolerate but do not interpret — they carry no content, so they cannot corrupt data.
_DIRECTIVES = ("subgraph", "end", "classdef", "class ", "style", "click", "linkstyle", "%%")

# Node-shape delimiters, opener -> closer. `>` (the flag shape) is deliberately absent: it also
# ends an arrow, so treating it as an opener would swallow the rest of the statement.
_SHAPE_DELIMITERS = {"[": "]", "(": ")", "{": "}"}

# A leaf whose whole label is a bare rate or amount is a table cell that lost its row.
_VALUE_LEAF_RE = re.compile(
	r"^(?:not\s+exceeding\s+|upto\s+|up\s+to\s+|max\.?\s+)?"
	r"(?:₹|rs\.?)?\s*[\d,]+(?:\.\d+)?\s*(?:%|per\s*cent|lakhs?|crores?)?$",
	re.IGNORECASE,
)
MIN_VALUE_LEAVES = 3
MIN_PARALLEL_GROUP = 3


def mermaid_blocks(markdown: str) -> list[str]:
	"""The source of every ```mermaid fenced block, in document order."""
	return [match.group(1) for match in _MERMAID_BLOCK_RE.finditer(markdown or "")]


def split_outside_labels(line: str, separator: str = ";") -> list[str]:
	"""Split a statement on `separator` only where it sits outside every label and quote.

	A label may legitimately carry one — `A["Rs 1,00,000; see proviso"]`, `B["Profit & Loss"]`,
	and the `#quot;` entity we escape with ends in one — and splitting there would tear a valid
	node in half.
	"""
	parts: list[str] = []
	depth = 0
	quoted = False
	start = 0
	for index, char in enumerate(line):
		if char == '"':
			quoted = not quoted
		elif quoted:
			continue
		elif char in _SHAPE_DELIMITERS:
			depth += 1
		elif char in _SHAPE_DELIMITERS.values():
			depth = max(0, depth - 1)
		elif char == separator and not depth:
			parts.append(line[start:index])
			start = index + 1
	parts.append(line[start:])
	return parts


def statements(source: str) -> list[str]:
	"""Content lines of a diagram body, semicolons split, directives dropped."""
	lines: list[str] = []
	for raw in (source or "").splitlines()[1:]:
		for part in split_outside_labels(raw):
			line = part.strip()
			if line and not line.lower().startswith(_DIRECTIVES):
				lines.append(line)
	return lines


def quoted_label(label: str) -> str:
	"""The label as a mermaid string literal, with any quote it carries escaped as `#quot;`.

	Left untouched when it is already a clean quoted string, or when it is a compound node
	shape (`[(cylinder)]`, `([stadium])`) whose own delimiters would be captured as text.
	"""
	text = label.strip()
	if len(text) >= 2 and text[0] == '"' and text[-1] == '"' and '"' not in text[1:-1]:
		return label
	if len(text) >= 2 and text[0] in _SHAPE_DELIMITERS and text[-1] == _SHAPE_DELIMITERS[text[0]]:
		return label
	return '"' + label.replace('"', "#quot;") + '"'


def quote_labels_in_statement(line: str) -> str:
	"""One statement with every node label quoted. Nesting is matched per shape family, so the
	inner brackets of `A[CAPITAL ASSET<br>[Section 2(14)]]` end up inside the label, not ending
	it. An unterminated label is left exactly as written — that is a real defect, not a quoting
	one, and the gate must still see it."""
	if line.lstrip().startswith("%%"):
		return line
	repaired: list[str] = []
	index = 0
	while index < len(line):
		char = line[index]
		follows_node_id = index == 0 or line[index - 1].isalnum() or line[index - 1] == "_"
		if char not in _SHAPE_DELIMITERS or not follows_node_id:
			repaired.append(char)
			index += 1
			continue
		closer = _SHAPE_DELIMITERS[char]
		run = 0
		while index + run < len(line) and line[index + run] == char:
			run += 1
		depth = run
		scan = index + run
		while scan < len(line) and depth:
			if line[scan] == char:
				depth += 1
			elif line[scan] == closer:
				depth -= 1
			scan += 1
		if depth:
			repaired.append(line[index:])
			break
		repaired.append(char * run + quoted_label(line[index + run : scan - run]) + closer * run)
		index = scan
	return "".join(repaired)


def quote_node_labels(source: str) -> str:
	"""The diagram body with every node label wrapped in double quotes. Lossless — it only ever
	adds quotes, so a repaired block still has to clear `diagram_errors` before it is stored."""
	lines = (source or "").splitlines(keepends=True)
	return "".join(lines[:1] + [quote_labels_in_statement(line) for line in lines[1:]])


def unquote(label: str) -> str:
	"""Drop a *balanced* pair of wrapping quotes. A lone quote is left in place — it is the
	unterminated label that stops mermaid parsing, and stripping it would hide the defect."""
	if len(label) >= 2 and label[0] == '"' and label[-1] == '"':
		return label[1:-1].strip()
	return label


def parse_flowchart(source: str) -> tuple[dict[str, str], list[tuple[str, str]], list[str]]:
	"""(node labels, edges, unparseable statements) for a flowchart body."""
	labels: dict[str, str] = {}
	edges: list[tuple[str, str]] = []
	broken: list[str] = []
	for line in statements(source):
		# `A & B --> C` is mermaid's shorthand for every node on the left joining every node on
		# the right, so a statement is a chain of *groups*, not of single nodes.
		groups: list[list[str]] = []
		for piece in _ARROW_RE.split(line):
			nodes: list[str] = []
			for item in split_outside_labels(piece, "&"):
				match = _PIECE_RE.match(_EDGE_LABEL_RE.sub("", item).strip())
				if not match:
					broken.append(line)
					nodes = []
					break
				node_id, label = match.group(1), match.group(2)
				labels.setdefault(node_id, unquote(label[1:-1].strip()) if label else "")
				nodes.append(node_id)
			if not nodes:
				groups = []
				break
			groups.append(nodes)
		for left, right in pairwise(groups):
			edges.extend((source_node, target) for source_node in left for target in right)
	return labels, edges, broken


def tabular_signals(labels: dict[str, str], edges: list[tuple[str, str]]) -> list[str]:
	"""Signs this "flowchart" is really a grid whose row↔value binding has been lost."""
	if not edges:
		return []
	children: dict[str, list[str]] = {}
	has_outgoing = {source_node for source_node, _ in edges}
	for source_node, target in edges:
		children.setdefault(source_node, []).append(target)

	signals: list[str] = []
	value_leaves = [
		node
		for node, label in labels.items()
		if node not in has_outgoing and label and _VALUE_LEAF_RE.match(label.strip())
	]
	if len(value_leaves) >= MIN_VALUE_LEAVES:
		signals.append(
			f"{len(value_leaves)} nodes are bare values ({', '.join(sorted(value_leaves)[:4])}) — "
			"these are table cells, not process steps"
		)

	# Two parents each fanning out to leaves, with nothing crossing between the fans: the
	# left-hand column and the right-hand column of a grid, drawn side by side and unbound.
	edge_set = set(edges)
	leaf_fans = [
		(parent, set(kids))
		for parent, kids in children.items()
		if len(kids) >= MIN_PARALLEL_GROUP and all(kid not in has_outgoing for kid in kids)
	]
	for index, (parent, kids) in enumerate(leaf_fans):
		for other_parent, other_kids in leaf_fans[index + 1 :]:
			crossing = any((a, b) in edge_set or (b, a) in edge_set for a in kids for b in other_kids)
			if not crossing:
				signals.append(
					f"parallel leaf groups under {parent} and {other_parent} with no edge "
					"binding them — a grid flattened into two dangling columns"
				)
				return signals
	return signals


def grid_errors(source: str) -> list[str]:
	"""Every reason this mermaid block must not be stored — all of them about meaning.

	Empty, and for anything that is not a flowchart, because the flattened-grid signature is
	defined in terms of nodes and edges. Statements this cannot read contribute no nodes and
	no edges rather than condemning the block: the reader's parser is the authority on syntax.

	Parsed once per verdict — `remediate_pdf` runs this gate over every candidate of every
	page, so a source that parsed itself twice cost up to nine parses a page.
	"""
	body = (source or "").strip()
	if not body:
		return ["empty diagram"]
	if not _HEADER_RE.match(body.splitlines()[0]):
		return []

	labels, edges, _unreadable = parse_flowchart(body)
	return tabular_signals(labels, edges)


def has_table(markdown: str) -> bool:
	"""True when the markdown still carries a grid the reader can use (HTML or pipe)."""
	text = markdown or ""
	if re.search(r"<table[\s>]", text, re.IGNORECASE):
		return True
	return sum(1 for line in text.splitlines() if line.strip().startswith("|")) >= 2


def remove_unverified_diagrams(markdown: str, fallback_image_url: str = "") -> tuple[str, list[str]]:
	"""Repair what can be repaired, drop every mermaid block that still fails the gate; return
	(markdown, notes).

	When the rejection leaves the page with no grid at all, the page/region crop is embedded so
	the content is still readable — a picture of the truth beats a well-formed lie.
	"""
	text = markdown or ""
	notes: list[str] = []
	rejected = 0

	def replace(match: re.Match) -> str:
		nonlocal rejected
		source = match.group(1)
		# Repair first and unconditionally: it is lossless, so there is nothing to weigh, and
		# deciding whether it was *needed* would mean judging syntax again.
		repaired = quote_node_labels(source)
		errors = grid_errors(repaired)
		if errors:
			rejected += 1
			notes.append(f"mermaid rejected: {errors[0]}")
			return ""
		if repaired != source:
			notes.append("mermaid repaired: node labels quoted")
			return match.group(0).replace(source, repaired, 1)
		return match.group(0)

	cleaned = _MERMAID_BLOCK_RE.sub(replace, text)
	if rejected:
		cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
		if fallback_image_url and not has_table(cleaned):
			cleaned = f"{cleaned}\n\n![Source region]({fallback_image_url})".strip()
			notes.append("embedded the source crop in place of the rejected diagram")
	return cleaned, notes
