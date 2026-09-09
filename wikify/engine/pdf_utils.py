"""PDF rendering + page classification via PyMuPDF.

Ported from the POC `pdf_utils.py`. The one I/O change: instead of writing PNGs
to disk under `storage/pages/`, `render_png` returns the PNG bytes and `store.py`
persists them as Frappe **File** docs attached to each Source Page.
"""

from __future__ import annotations

import base64
from pathlib import Path

import fitz  # PyMuPDF

from wikify.engine import config, regions


def png_to_data_url(png_bytes: bytes) -> str:
	"""Inline a rendered page PNG as a data URL for the judge/VLM image input.

	The POC read a PNG off disk (`image_to_data_url`); here the bytes are already in
	hand from `render_png`, so we encode them directly — no File round-trip.
	"""
	b64 = base64.b64encode(png_bytes).decode("ascii")
	return f"data:image/png;base64,{b64}"


def classify_page(
	page,
	min_chars: int = config.VISUAL_MIN_CHARS,
	min_drawings: int = config.VISUAL_MIN_DRAWINGS,
) -> str:
	"""Page type: `visual` | `mixed` | `text`.

	The original rule was `chars < min_chars AND drawings >= min_drawings`, written for
	scanned pages. On a born-digital diagram-heavy manual every page has both a full text
	layer and heavy vector art, so the conjunction never fired and 235 of 236 ICAI pages were
	filed `text` — the visual path was unreachable. `regions.classify_page` answers it from the
	page's shape regions instead, and adds `mixed`: a trustworthy text layer *plus* substantial
	table/diagram ink, which needs a vision model to recover structure but must go on being
	scored against its text layer.
	"""
	return regions.classify_page(page, min_chars, min_drawings)


def render_png(page, dpi: int = config.RENDER_DPI) -> bytes:
	"""Render a page to PNG bytes at the given DPI."""
	zoom = dpi / 72.0
	matrix = fitz.Matrix(zoom, zoom)
	return page.get_pixmap(matrix=matrix).tobytes("png")


def page_count(pdf_path: str | Path) -> int:
	with fitz.open(pdf_path) as doc:
		return doc.page_count


def get_toc(pdf_path: str | Path) -> list[tuple[int, str, int]]:
	"""Embedded outline: list of (level, title, page_no). Empty if none."""
	with fitz.open(pdf_path) as doc:
		return [(lvl, title, page) for lvl, title, page in doc.get_toc()]
