# Copyright (c) 2026, BWH and contributors
# For license information, please see license.txt
from __future__ import annotations

import unittest

import fitz

from wikify.engine import pdf_utils, regions


def grid_page(doc, rows: int = 6, columns: int = 4):
	page = doc.new_page()
	for line in range(6):
		page.insert_text(
			(72, 90 + line * 14), "Rates of surcharge for the assessment year. " * 2, fontsize=11
		)
	for row in range(rows):
		for column in range(columns):
			x = 72 + column * 110
			y = 200 + row * 26
			page.draw_rect(fitz.Rect(x, y, x + 105, y + 24), color=(0, 0, 0), fill=(0.9, 0.9, 1), width=0.5)
			page.insert_text((x + 4, y + 16), f"r{row}c{column}", fontsize=9)
	return page


def flow_page(doc):
	page = doc.new_page()
	page.insert_text((72, 90), "Decision tree for opting out of the default regime.", fontsize=11)
	for index in range(4):
		top = 150 + index * 90
		page.draw_rect(fitz.Rect(200, top, 350, top + 60), color=(0, 0, 0), width=1)
		page.insert_text((208, top + 34), f"step {index}", fontsize=10)
		if index:
			page.draw_line(fitz.Point(275, top - 30), fitz.Point(275, top))
	return page


def prose_page(doc):
	page = doc.new_page()
	page.insert_text((72, 90), "Plain prose with no artwork at all.\n" * 20, fontsize=11)
	return page


class TestRegions(unittest.TestCase):
	def setUp(self):
		self.doc = fitz.open()
		self.addCleanup(self.doc.close)

	def shapes(self, page) -> list[str]:
		return [region.shape for region in regions.find_regions(page)]

	def test_a_grid_is_found_as_a_table(self):
		page = grid_page(self.doc)
		tables = [region for region in regions.find_regions(page) if region.shape == regions.TABLE]
		self.assertEqual(len(tables), 1)
		self.assertGreaterEqual(tables[0].rows, 4)
		self.assertGreaterEqual(tables[0].columns, 3)
		self.assertTrue(tables[0].substantial)

	def test_labelled_boxes_are_a_flow_not_a_table(self):
		shapes = self.shapes(flow_page(self.doc))
		self.assertIn(regions.FLOW, shapes)
		self.assertNotIn(regions.TABLE, shapes)

	def test_a_text_page_with_heavy_art_is_mixed_not_text(self):
		page = grid_page(self.doc)
		self.assertGreater(len(page.get_text("text").strip()), 250)
		self.assertEqual(pdf_utils.classify_page(page, 250, 40), "mixed")

	def test_a_plain_prose_page_stays_text(self):
		self.assertEqual(pdf_utils.classify_page(prose_page(self.doc), 250, 40), "text")

	def test_a_textless_diagram_page_stays_visual(self):
		page = self.doc.new_page()
		for index in range(60):
			x, y = 50 + (index % 10) * 45, 120 + (index // 10) * 60
			page.draw_rect(fitz.Rect(x, y, x + 35, y + 45), color=(0, 0, 0), width=1)
		self.assertEqual(pdf_utils.classify_page(page, 250, 40), "visual")

	def test_a_flow_engulfed_by_a_grid_is_not_announced_twice(self):
		page = grid_page(self.doc, rows=10, columns=5)
		self.assertEqual(self.shapes(page).count(regions.FLOW), 0)

	def test_the_shape_hint_names_the_grid_and_forbids_a_flowchart(self):
		hint = regions.shape_hint(regions.find_regions(grid_page(self.doc)))
		self.assertIn("table region", hint)
		self.assertIn("never a flowchart", hint)

	def test_the_shape_hint_is_empty_for_a_prose_page(self):
		self.assertEqual(regions.shape_hint(regions.find_regions(prose_page(self.doc))), "")
