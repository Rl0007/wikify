from __future__ import annotations

import base64
from pathlib import Path

import fitz

from wikify.engine import config, regions


def png_to_data_url(png_bytes: bytes) -> str:
	b64 = base64.b64encode(png_bytes).decode("ascii")
	return f"data:image/png;base64,{b64}"


def classify_page(
	page,
	min_chars: int = config.VISUAL_MIN_CHARS,
	min_drawings: int = config.VISUAL_MIN_DRAWINGS,
) -> str:
	return regions.classify_page(page, min_chars, min_drawings)


def render_png(page, dpi: int = config.RENDER_DPI) -> bytes:
	zoom = dpi / 72.0
	matrix = fitz.Matrix(zoom, zoom)
	return page.get_pixmap(matrix=matrix).tobytes("png")


def page_count(pdf_path: str | Path) -> int:
	with fitz.open(pdf_path) as doc:
		return doc.page_count


def get_toc(pdf_path: str | Path) -> list[tuple[int, str, int]]:
	with fitz.open(pdf_path) as doc:
		return [(lvl, title, page) for lvl, title, page in doc.get_toc()]
