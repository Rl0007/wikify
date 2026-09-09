from __future__ import annotations

import re
from itertools import pairwise

_HEADER_RE = re.compile(r"^\s*(?:flowchart|graph)\s+(?:TD|TB|BT|LR|RL)\s*$", re.IGNORECASE)
_MERMAID_BLOCK_RE = re.compile(
	r"^[ \t]*```[ \t]*mermaid[ \t]*\n(.*?)^[ \t]*```[ \t]*$", re.MULTILINE | re.DOTALL
)

_NODE_ID = r"[A-Za-z][A-Za-z0-9_]*"
_QUOTED_LABEL = r'(?:\[\s*"[^"]*"\s*\]|\(\s*"[^"]*"\s*\)|\{\s*"[^"]*"\s*\})'
_LABEL = rf"(?:{_QUOTED_LABEL}|\[[^\[\]]*\]|\([^()]*\)|\{{[^{{}}]*\}}|>[^<>]*\])"
_ARROW_RE = re.compile(r"\s*(?:--+>|--+|-\.-+>|-\.-+|==+>|==+)\s*")
_PIECE_RE = re.compile(rf"^({_NODE_ID})\s*({_LABEL})?$")
_EDGE_LABEL_RE = re.compile(r"^\|[^|]*\|\s*")

_DIRECTIVES = ("subgraph", "end", "classdef", "class ", "style", "click", "linkstyle", "%%")

_SHAPE_DELIMITERS = {"[": "]", "(": ")", "{": "}"}

_VALUE_LEAF_RE = re.compile(
	r"^(?:not\s+exceeding\s+|upto\s+|up\s+to\s+|max\.?\s+)?"
	r"(?:₹|rs\.?)?\s*[\d,]+(?:\.\d+)?\s*(?:%|per\s*cent|lakhs?|crores?)?$",
	re.IGNORECASE,
)
# A fan of this many bare values under one parent reads as a table flattened into two
# unbound columns, where a confidently wrong rate is worse than no diagram at all.
MIN_VALUE_LEAVES = 3
MIN_PARALLEL_GROUP = 3


def mermaid_blocks(markdown: str) -> list[str]:
	return [match.group(1) for match in _MERMAID_BLOCK_RE.finditer(markdown or "")]


def split_outside_labels(line: str, separator: str = ";") -> list[str]:
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
	lines: list[str] = []
	for raw in (source or "").splitlines()[1:]:
		for part in split_outside_labels(raw):
			line = part.strip()
			if line and not line.lower().startswith(_DIRECTIVES):
				lines.append(line)
	return lines


def quoted_label(label: str) -> str:
	text = label.strip()
	if len(text) >= 2 and text[0] == '"' and text[-1] == '"' and '"' not in text[1:-1]:
		return label
	if len(text) >= 2 and text[0] in _SHAPE_DELIMITERS and text[-1] == _SHAPE_DELIMITERS[text[0]]:
		return label
	return '"' + label.replace('"', "#quot;") + '"'


def quote_labels_in_statement(line: str) -> str:
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
	lines = (source or "").splitlines(keepends=True)
	return "".join(lines[:1] + [quote_labels_in_statement(line) for line in lines[1:]])


def unquote(label: str) -> str:
	if len(label) >= 2 and label[0] == '"' and label[-1] == '"':
		return label[1:-1].strip()
	return label


def parse_flowchart(source: str) -> tuple[dict[str, str], list[tuple[str, str]], list[str]]:
	labels: dict[str, str] = {}
	edges: list[tuple[str, str]] = []
	broken: list[str] = []
	for line in statements(source):
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
	# Parsed once per verdict: `remediate_pdf` runs this gate over every candidate of every
	# page, and a source that parsed itself twice cost up to nine parses a page.
	# Statements this cannot read contribute no nodes and no edges rather than condemning the
	# block — the reader's parser is the authority on syntax, not this regex.
	body = (source or "").strip()
	if not body:
		return ["empty diagram"]
	if not _HEADER_RE.match(body.splitlines()[0]):
		return []

	labels, edges, _unreadable = parse_flowchart(body)
	return tabular_signals(labels, edges)


def has_table(markdown: str) -> bool:
	text = markdown or ""
	if re.search(r"<table[\s>]", text, re.IGNORECASE):
		return True
	return sum(1 for line in text.splitlines() if line.strip().startswith("|")) >= 2


def demoted(source: str, reason: str) -> str:
	return f"> Diagram not rendered — {reason}.\n\n```text\n{source.strip()}\n```"


def remove_unverified_diagrams(markdown: str, fallback_image_url: str = "") -> tuple[str, list[str]]:
	text = markdown or ""
	notes: list[str] = []
	rejected = 0

	def replace(match: re.Match) -> str:
		nonlocal rejected
		source = match.group(1)
		repaired = quote_node_labels(source)
		errors = grid_errors(repaired)
		if errors:
			rejected += 1
			notes.append(f"mermaid rejected: {errors[0]}")
			return demoted(repaired, errors[0])
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
