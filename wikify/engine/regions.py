"""Per-region shape classification for a PDF page: `table` | `flow` | `figure` | `prose`.

The pipeline used to ask one question per page — "is this a diagram?" — and answer it with
`chars < 250 AND drawings > 40`. On a born-digital, diagram-heavy manual (the ICAI exam
referencers) every page carries both a full text layer *and* heavy vector art, so the
conjunction never fires: 235 of 236 pages came back `text` and the visual path was dead code.

Worse, once a page did reach the VLM nothing routed content by *shape*, so 2-D rate tables were
re-encoded as `flowchart TD` — a grammar that cannot express a grid, which silently destroys the
row-to-value correspondence a student is revising from.

This module answers the finer question: which *regions* of the page are table, flow, figure and
prose. The page-level kind is derived from them (a page with a reliable text layer AND
substantial table/diagram ink is `mixed`, not `text`), the region list is fed to the VLM as a
shape hint.

No LLM, no I/O — pure PyMuPDF geometry. Boxes are plain `(x0, y0, x1, y1)` tuples throughout:
this runs on every page of every parse, and building `fitz.Rect` objects for the tens of
thousands of intersection tests a drawing-heavy page needs costs more than the geometry does.
"""

from __future__ import annotations

from dataclasses import dataclass

TABLE = "table"
FLOW = "flow"
FIGURE = "figure"
PROSE = "prose"

# Running header/footer bands. Anything wholly inside them is page furniture, not content.
HEADER_BAND = 0.09
FOOTER_BAND = 0.94

# A drawing smaller than this (points²) is a bullet, tick or glyph — not art and not a rule.
MIN_DRAWING_AREA = 300.0

# Rule geometry — thin, long ink that is part of a diagram's structure rather than its area.
MAX_RULE_THICKNESS = 3.0
MIN_RULE_LENGTH = 30.0

# Grids are found from their CELLS, not their ruling lines: these documents draw tables as
# colour-filled cells whose borders are hairlines the line-based readers miss, and
# `page.find_tables()` costs 1-7s on the drawing-heavy pages here (and segfaulted on a
# full-document pass) to answer a question we only need a yes/no to.
MIN_CELL_WIDTH = 15.0
MIN_CELL_HEIGHT = 8.0

# Cells of one column share their left and right edge to within a rounding wobble.
CELL_EDGE_TOLERANCE = 2.0

# A grid needs a stack deep enough to be rows and neighbouring stacks to be columns; below
# this it is a boxed callout or a flowchart node.
MIN_GRID_ROWS = 3
MIN_GRID_COLUMNS = 2

# Neighbouring columns of one table touch; a wider gutter means two separate tables.
MAX_COLUMN_GAP = 12.0
MIN_COLUMN_OVERLAP = 0.5

# Nested column variants (a cell fill and its inner text fill) are the same column.
COLUMN_MERGE_TOLERANCE = 8.0

# Vector art is clustered on an occupancy grid rather than by pairwise rect merging: O(drawings)
# instead of O(drawings²), which matters on the pages here that carry 900 drawings.
GRID_COLUMNS = 48
GRID_ROWS = 64

# A region below this share of the page is an inline glyph, logo or bullet cluster.
MIN_REGION_AREA = 0.03

# Vector art needs labels inside it to be a flow chart; wordless art is a figure.
MIN_FLOW_LABELS = 3

# Only a *substantial* region forces a page off the plain-text path. A lone 2x2 grid does not
# need a vision model; a 4-row table, a wide grid, or a page-tenth of diagram does.
SUBSTANTIAL_TABLE_ROWS = 4
SUBSTANTIAL_TABLE_COLUMNS = 3
SUBSTANTIAL_GRAPHIC_AREA = 0.05


@dataclass(frozen=True)
class Region:
	"""One shape-classified area of a page, in PDF points."""

	shape: str
	bbox: tuple[float, float, float, float]
	area_fraction: float
	rows: int = 0
	columns: int = 0
	labels: int = 0

	@property
	def substantial(self) -> bool:
		if self.shape == TABLE:
			return self.rows >= SUBSTANTIAL_TABLE_ROWS or self.columns >= SUBSTANTIAL_TABLE_COLUMNS
		if self.shape in (FLOW, FIGURE):
			return self.area_fraction >= SUBSTANTIAL_GRAPHIC_AREA
		return False


def area(box: tuple) -> float:
	return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def area_fraction(box: tuple, page_box: tuple) -> float:
	page_area = area(page_box)
	return area(box) / page_area if page_area else 0.0


def is_furniture(box: tuple, page_box: tuple) -> bool:
	"""Wholly inside the running header or footer band."""
	height = (page_box[3] - page_box[1]) or 1.0
	return (box[3] - page_box[1]) / height <= HEADER_BAND or (box[1] - page_box[1]) / height >= FOOTER_BAND


def overlap_fraction(box: tuple, others: list[tuple]) -> float:
	"""Largest share of `box` covered by any one of `others`."""
	own = area(box)
	if not own:
		return 0.0
	best = 0.0
	for other in others:
		width = min(box[2], other[2]) - max(box[0], other[0])
		height = min(box[3], other[3]) - max(box[1], other[1])
		if width > 0 and height > 0:
			best = max(best, width * height / own)
	return best


def is_rule(box: tuple) -> bool:
	"""A thin, long line — a table rule, an underline or a connector."""
	width, height = box[2] - box[0], box[3] - box[1]
	return (height <= MAX_RULE_THICKNESS and width >= MIN_RULE_LENGTH) or (
		width <= MAX_RULE_THICKNESS and height >= MIN_RULE_LENGTH
	)


def graphic_boxes(page, blocks: list) -> list[tuple]:
	"""Ink worth clustering: ruling lines, substantial vector art, and raster blocks."""
	boxes = []
	try:
		drawings = page.get_drawings()
	except Exception:
		drawings = []
	for drawing in drawings:
		box = tuple(float(value) for value in drawing["rect"])
		if is_rule(box) or area(box) >= MIN_DRAWING_AREA:
			boxes.append(box)
	boxes.extend(tuple(float(value) for value in block[:4]) for block in blocks if block[6] == 1)
	return boxes


def text_boxes(blocks: list) -> list[tuple]:
	"""Boxes of the page's non-empty text blocks."""
	return [
		tuple(float(value) for value in block[:4])
		for block in blocks
		if block[6] == 0 and (block[4] or "").strip()
	]


def cell_columns(boxes: list[tuple]) -> list[tuple]:
	"""Vertical stacks of same-width cells — one entry per column, as (x0, y0, x1, y1, cells)."""
	stacks: dict[tuple[int, int], list[tuple]] = {}
	for box in boxes:
		width, height = box[2] - box[0], box[3] - box[1]
		if width < MIN_CELL_WIDTH or height < MIN_CELL_HEIGHT or width * height < MIN_DRAWING_AREA:
			continue
		key = (round(box[0] / CELL_EDGE_TOLERANCE), round(box[2] / CELL_EDGE_TOLERANCE))
		stacks.setdefault(key, []).append(box)
	columns = []
	for cells in stacks.values():
		if len(cells) < MIN_GRID_ROWS:
			continue
		columns.append(
			(
				min(cell[0] for cell in cells),
				min(cell[1] for cell in cells),
				max(cell[2] for cell in cells),
				max(cell[3] for cell in cells),
				len(cells),
			)
		)
	return sorted(columns)


def vertical_overlap(column: tuple, other: tuple) -> float:
	"""Share of the shorter column's height that the two columns have in common."""
	shortest = min(column[3] - column[1], other[3] - other[1])
	if shortest <= 0:
		return 0.0
	return max(0.0, min(column[3], other[3]) - max(column[1], other[1])) / shortest


def table_regions(page_box: tuple, boxes: list[tuple]) -> list[Region]:
	"""Grids, found by chaining adjacent, vertically-aligned columns of cells."""
	chains: list[list[tuple]] = []
	for column in cell_columns(boxes):
		joined = next(
			(
				chain
				for chain in chains
				if column[0] <= max(member[2] for member in chain) + MAX_COLUMN_GAP
				and vertical_overlap(column, span_of(chain)) >= MIN_COLUMN_OVERLAP
			),
			None,
		)
		if joined is None:
			chains.append([column])
		else:
			joined.append(column)

	regions = []
	for chain in chains:
		# Nested variants (a cell fill and the inner fill behind its text) are one column.
		starts = {round(column[0] / COLUMN_MERGE_TOLERANCE) for column in chain}
		if len(starts) < MIN_GRID_COLUMNS:
			continue
		box = span_of(chain)
		fraction = area_fraction(box, page_box)
		if fraction < MIN_REGION_AREA:
			continue
		rows = max(column[4] for column in chain)
		regions.append(Region(TABLE, box, fraction, rows=rows, columns=len(starts)))
	return regions


def span_of(chain: list[tuple]) -> tuple[float, float, float, float]:
	return (
		min(column[0] for column in chain),
		min(column[1] for column in chain),
		max(column[2] for column in chain),
		max(column[3] for column in chain),
	)


def grid_cells(box: tuple, page_box: tuple) -> set[tuple[int, int]]:
	"""The occupancy-grid cells a box covers."""
	width = (page_box[2] - page_box[0]) or 1.0
	height = (page_box[3] - page_box[1]) or 1.0
	col0 = max(0, min(GRID_COLUMNS - 1, int((box[0] - page_box[0]) / width * GRID_COLUMNS)))
	col1 = max(0, min(GRID_COLUMNS - 1, int((box[2] - page_box[0]) / width * GRID_COLUMNS)))
	row0 = max(0, min(GRID_ROWS - 1, int((box[1] - page_box[1]) / height * GRID_ROWS)))
	row1 = max(0, min(GRID_ROWS - 1, int((box[3] - page_box[1]) / height * GRID_ROWS)))
	return {(column, row) for column in range(col0, col1 + 1) for row in range(row0, row1 + 1)}


def connected_components(cells: set[tuple[int, int]]) -> list[set[tuple[int, int]]]:
	"""4-neighbour flood fill over occupied grid cells."""
	remaining = set(cells)
	components = []
	while remaining:
		seed = remaining.pop()
		component = {seed}
		frontier = [seed]
		while frontier:
			column, row = frontier.pop()
			for neighbour in ((column + 1, row), (column - 1, row), (column, row + 1), (column, row - 1)):
				if neighbour in remaining:
					remaining.remove(neighbour)
					component.add(neighbour)
					frontier.append(neighbour)
		components.append(component)
	return components


def component_box(component: set[tuple[int, int]], page_box: tuple) -> tuple[float, float, float, float]:
	width = (page_box[2] - page_box[0]) / GRID_COLUMNS
	height = (page_box[3] - page_box[1]) / GRID_ROWS
	columns = [cell[0] for cell in component]
	rows = [cell[1] for cell in component]
	return (
		page_box[0] + min(columns) * width,
		page_box[1] + min(rows) * height,
		page_box[0] + (max(columns) + 1) * width,
		page_box[1] + (max(rows) + 1) * height,
	)


def label_count(labels: list[tuple], region_box: tuple) -> int:
	"""Text blocks sitting inside a region — the boxes' labels, for flow vs figure."""
	return sum(1 for box in labels if overlap_fraction(box, [region_box]) > 0.6)


def graphic_regions(
	page_box: tuple, blocks: list, graphics: list[tuple], occupied: list[tuple]
) -> list[Region]:
	"""Clusters of non-grid ink, each classified `flow` or `figure`.

	Labelled boxes wired together are a flow — mermaid's actual job. Wordless ink is a figure,
	where a crop of the original is the only faithful representation we can offer.

	`graphics` is the page's furniture-free ink, passed in rather than re-derived: reading it
	means `page.get_drawings()`, which costs ~25ms on a drawing-heavy page.
	"""
	labels = text_boxes(blocks)
	boxes = [box for box in graphics if overlap_fraction(box, occupied) <= 0.5]

	cells: set[tuple[int, int]] = set()
	for box in boxes:
		cells |= grid_cells(box, page_box)

	regions = []
	for component in connected_components(cells):
		region_box = component_box(component, page_box)
		fraction = area_fraction(region_box, page_box)
		# A component can still engulf a grid even when each of its own boxes dodged one:
		# connectors and cell fills chain through the table's area. Announcing that span as a
		# second `flow` region is exactly the hint that talks the model into a flowchart.
		if fraction < MIN_REGION_AREA or overlap_fraction(region_box, occupied) > 0.5:
			continue
		inside = label_count(labels, region_box)
		shape = FLOW if inside >= MIN_FLOW_LABELS else FIGURE
		regions.append(Region(shape, region_box, fraction, labels=inside))
	return regions


def prose_region(page_box: tuple, blocks: list, occupied: list[tuple]) -> Region | None:
	"""The union of text blocks outside every graphic region, if any survive."""
	box = None
	for candidate in text_boxes(blocks):
		if is_furniture(candidate, page_box) or overlap_fraction(candidate, occupied) > 0.5:
			continue
		box = (
			candidate
			if box is None
			else (
				min(box[0], candidate[0]),
				min(box[1], candidate[1]),
				max(box[2], candidate[2]),
				max(box[3], candidate[3]),
			)
		)
	if box is None:
		return None
	return Region(PROSE, box, area_fraction(box, page_box))


def find_regions(page) -> list[Region]:
	"""Shape-classified regions of one page: grids first, then the remaining ink, then prose.

	The page's blocks and its ink are read once here and threaded down — the grid pass and
	the graphic pass used to call `page.get_drawings()` each, doubling the most expensive
	step in the analysis for a list both of them already had.
	"""
	blocks = page.get_text("blocks")
	page_box = tuple(float(value) for value in page.rect)
	graphics = [box for box in graphic_boxes(page, blocks) if not is_furniture(box, page_box)]
	regions = table_regions(page_box, graphics)
	regions.extend(graphic_regions(page_box, blocks, graphics, [region.bbox for region in regions]))
	prose = prose_region(page_box, blocks, [region.bbox for region in regions])
	if prose:
		regions.append(prose)
	return regions


def classify_page(page, min_chars: int, min_drawings: int) -> str:
	"""Page kind from its regions: `visual` | `mixed` | `text`.

	`visual` keeps its old meaning — no usable text layer, so the rendered image is the only
	ground truth. `mixed` is the case the old AND-gate could not express: a trustworthy text
	layer *and* substantial table/diagram ink, which still needs a vision model to recover the
	structure but must go on being scored against its text layer.
	"""
	regions = find_regions(page)
	prose_chars = len(page.get_text("text").strip())
	graphics = [region for region in regions if region.shape in (TABLE, FLOW, FIGURE)]
	if prose_chars < min_chars and (graphics or page.get_images()):
		return "visual"
	if any(region.substantial for region in graphics):
		return "mixed"
	try:
		if len(page.get_drawings()) >= min_drawings:
			return "mixed"
	except Exception:
		pass
	return "text"


def shape_hint(regions: list[Region]) -> str:
	"""One line per non-prose shape, for the VLM prompt — what to expect and how much of it.

	Deliberately coarse: it tells the model a grid is present, never where its cells are. The
	model still reads the image; the hint only stops it inventing a flowchart for a grid.
	"""
	counts: dict[str, int] = {}
	widest = 0
	for region in regions:
		if region.shape == PROSE:
			continue
		counts[region.shape] = counts.get(region.shape, 0) + 1
		if region.shape == TABLE:
			widest = max(widest, region.columns)
	if not counts:
		return ""
	parts = [f"{count} {shape} region(s)" for shape, count in sorted(counts.items())]
	line = "Layout analysis of this page found: " + ", ".join(parts) + "."
	if widest >= MIN_GRID_COLUMNS:
		line += f" The widest ruled grid has ~{widest + 1} columns — it is a TABLE, never a flowchart."
	return line + "\n"
