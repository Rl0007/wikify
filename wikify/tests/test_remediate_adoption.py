# Copyright (c) 2026, BWH and contributors
# For license information, please see license.txt

"""Adoption floors: a remediation may only become canonical if it is a better read of the page.

Page 6 of the second ICAI referencer shipped `# TIE TIT Molo` — ten characters of OCR noise — as
its canonical markdown at composite 0.058, over a VLM read of the same page. Cleanup's own
eligibility test only asks whether recall regressed, and recall measured against an equally empty
baseline had not, so the worst candidate on the page won by default. These tests hold the two
floors that now sit under every method's rules.
"""

from __future__ import annotations

import unittest

from wikify.engine.remediate import content_chars, pick_winner, with_page_crop
from wikify.engine.verify import PageScore


def candidate(method: str, markdown: str, composite: float, eligible: bool = True) -> tuple:
	score = PageScore(
		page_no=6,
		text_recall=0.0,
		extra_ratio=0.0,
		table_score=None,
		judge_score=None,
		composite=composite,
		verdict="review",
	)
	return (method, markdown, score, eligible)


GOOD_MARKDOWN = (
	"# Capital asset\n\nA capital asset means property of any kind held by an assessee, "
	"whether or not connected with his business or profession [Section 2(14)].\n"
)
OCR_NOISE = "# TIE TIT Molo"


class TestAdoptionFloors(unittest.TestCase):
	def test_near_empty_candidate_never_beats_one_with_content(self):
		"""The live regression: cleanup passed its recall test, and still must not be adopted."""
		candidates = [candidate("vlm", GOOD_MARKDOWN, 0.82), candidate("cleanup", OCR_NOISE, 0.058)]
		winner = pick_winner(candidates, baseline_composite=0.05, baseline_markdown="")
		self.assertEqual(winner[0], "vlm")

	def test_near_empty_candidate_is_not_adopted_even_when_it_is_the_only_one(self):
		candidates = [candidate("cleanup", OCR_NOISE, 0.058)]
		self.assertIsNone(pick_winner(candidates, baseline_composite=0.31, baseline_markdown=GOOD_MARKDOWN))

	def test_a_dramatically_worse_candidate_is_never_adopted(self):
		candidates = [candidate("vlm", GOOD_MARKDOWN, 0.91), candidate("cleanup", GOOD_MARKDOWN, 0.40)]
		winner = pick_winner(candidates, baseline_composite=0.30, baseline_markdown=GOOD_MARKDOWN)
		self.assertEqual(winner[0], "vlm")

	def test_a_candidate_below_the_baseline_it_came_from_is_never_adopted(self):
		candidates = [candidate("cleanup", GOOD_MARKDOWN, 0.40)]
		self.assertIsNone(pick_winner(candidates, baseline_composite=0.88, baseline_markdown=GOOD_MARKDOWN))

	def test_a_genuinely_blank_page_still_adopts_its_best_read(self):
		"""Both floors are relative — they must not strand a page whose source really is bare."""
		candidates = [candidate("cleanup", "# Notes", 0.62)]
		winner = pick_winner(candidates, baseline_composite=0.20, baseline_markdown="")
		self.assertEqual(winner[0], "cleanup")

	def test_ineligible_candidates_stay_ineligible(self):
		candidates = [candidate("vlm", GOOD_MARKDOWN, 0.95, eligible=False)]
		self.assertIsNone(pick_winner(candidates, baseline_composite=0.30, baseline_markdown=""))


class TestContentChars(unittest.TestCase):
	def test_ocr_noise_does_not_read_as_content(self):
		self.assertEqual(content_chars(OCR_NOISE), len("TIETITMolo"))

	def test_a_crop_on_its_own_does_not_read_as_content(self):
		self.assertEqual(content_chars("![Source page](/files/page-0006.png)"), 0)

	def test_a_diagram_reads_as_content(self):
		markdown = '```mermaid\nflowchart TD\n\tA["Capital asset"] --> B["Property of any kind"]\n```'
		self.assertGreater(content_chars(markdown), 40)


class TestPageCropFallback(unittest.TestCase):
	def test_a_near_empty_page_keeps_its_crop(self):
		self.assertIn("![Source page](/files/p.png)", with_page_crop(OCR_NOISE, "/files/p.png"))

	def test_a_page_with_content_is_left_alone(self):
		self.assertEqual(with_page_crop(GOOD_MARKDOWN, "/files/p.png"), GOOD_MARKDOWN)

	def test_no_image_means_no_change(self):
		self.assertEqual(with_page_crop(OCR_NOISE, ""), OCR_NOISE)
