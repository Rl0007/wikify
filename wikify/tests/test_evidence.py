# Copyright (c) 2026, BWH and contributors
# For license information, please see license.txt

"""Span-level grounding (`wikify.rag.evidence`) + chunk provenance (`wikify.rag.chunk`).

The load-bearing assertions:
  - a located span reports line and character offsets into the ORIGINAL text, so the line
    a student is told to check is the line that is actually there;
  - a near-verbatim quote (smart quotes, en dash, rupee-sign vs Rs., re-flowed whitespace, dropped
    bold markers) still resolves, and a FABRICATED quote does not — `verify_citations`
    must call it unverified rather than pass it through;
  - a chunk is pinned to the single page its text came from, and falls back to the section
    page range flagged `page_approximate` when two pages are too close to call.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from wikify.rag import chunk, evidence

# Two pages of a rate table, in the shape the ICAI corpus stores them.
PAGE_10 = """## AMT

Alternate minimum tax is not attracted where the adjusted total income of the
assessee does not exceed twenty lakh rupees.
"""

PAGE_11 = """## II. SURCHARGE

Surcharge is levied on the amount of income-tax.

| Total income | Rate of surcharge |
| --- | --- |
| Exceeding **Rs. 50 lakh** but not exceeding Rs. 1 crore | 10% |
| Exceeding Rs. 1 crore but not exceeding Rs. 2 crore | 15% |
| Exceeding Rs. 2 crore | 25% |

Marginal relief is available in respect of the surcharge.
"""

SECTION_MARKDOWN = f"{PAGE_10}\n{PAGE_11}"

PAGES = [
	{"source_document": "DOC-1", "page_no": 10, "canonical_markdown": PAGE_10},
	{"source_document": "DOC-1", "page_no": 11, "canonical_markdown": PAGE_11},
]

SECTION_ROW = {
	"name": "SEC-1",
	"source_document": "DOC-1",
	"title": "AMT and surcharge",
	"section_type": None,
	"hierarchy_path": "Basic Concepts > Surcharge",
	"page_start": 10,
	"page_end": 11,
	"markdown": SECTION_MARKDOWN,
	"wiki_document": None,
}


def citation(source_document: str = "DOC-1", **overrides) -> dict:
	base = {
		"section": "SEC-1",
		"source_document": source_document,
		"text": SECTION_MARKDOWN,
		"page_start": 10,
		"page_end": 11,
	}
	base.update(overrides)
	return base


def seed_pages() -> str:
	"""A real Source Document carrying PAGE_10 / PAGE_11, rolled back with the test.

	`verify_citations` resolves a quote down to a page by reading `Source Page` out of the
	database, so the page leg is only exercised against real rows — a dict fixture would
	prove the matcher works and the query doesn't.
	"""
	project = frappe.get_doc(
		{"doctype": "Wikify Project", "project_name": f"Evidence Test {frappe.generate_hash(length=8)}"}
	).insert(ignore_permissions=True)
	document = frappe.get_doc(
		{"doctype": "Source Document", "title": "SARANSH", "project": project.name}
	).insert(ignore_permissions=True)
	for page in PAGES:
		frappe.get_doc(
			{
				"doctype": "Source Page",
				"source_document": document.name,
				"page_no": page["page_no"],
				"canonical_markdown": page["canonical_markdown"],
			}
		).insert(ignore_permissions=True)
	return document.name


class TestNormalise(FrappeTestCase):
	def test_folds_the_variants_a_quote_actually_arrives_with(self):
		normalised, _ = evidence.normalise("Exceeding\u00a0 **Rs. 1,00,000** \u2013 \u201cthe limit\u201d")
		self.assertEqual(normalised, 'exceeding rs. 100000 - "the limit"')

	def test_rupee_sign_and_rs_share_tokens(self):
		self.assertEqual(evidence.get_tokens("\u20b92 crore"), ["rs", "2", "crore"])
		self.assertEqual(evidence.get_tokens("Rs. 2 crore"), ["rs", "2", "crore"])

	def test_offsets_point_back_into_the_original_text(self):
		source = "a  **b**\nc"
		normalised, offsets = evidence.normalise(source)
		self.assertEqual(normalised, "a b c")
		self.assertEqual(len(offsets), len(normalised))
		self.assertEqual(source[offsets[normalised.index("b")]], "b")
		self.assertEqual(source[offsets[-1]], "c")


class TestLocateQuote(FrappeTestCase):
	def test_exact_quote_reports_its_line_and_characters(self):
		quote = "Marginal relief is available in respect of the surcharge."
		located = evidence.locate_quote(quote, PAGE_11)

		self.assertTrue(located["found"])
		self.assertEqual(located["score"], 1.0)
		self.assertEqual(PAGE_11[located["char_start"] : located["char_end"]], quote)
		# The reported line must BE the line: read it back out of the source.
		lines = PAGE_11.split("\n")
		self.assertEqual(located["line_start"], located["line_end"])
		self.assertEqual(lines[located["line_start"] - 1], quote)

	def test_multi_line_quote_spans_the_lines_it_covers(self):
		located = evidence.locate_quote(
			"the adjusted total income of the assessee does not exceed twenty lakh rupees", PAGE_10
		)
		self.assertTrue(located["found"])
		self.assertEqual((located["line_start"], located["line_end"]), (3, 4))

	def test_near_verbatim_quote_still_resolves(self):
		# Rupee sign for "Rs.", en dash, collapsed whitespace, bold markers gone.
		located = evidence.locate_quote(
			"Exceeding \u20b92 crore \u2013 25%", "| Exceeding **Rs. 2 crore** | 25% |"
		)
		self.assertTrue(located["found"])
		self.assertGreaterEqual(located["score"], evidence.MIN_QUOTE_SCORE)

	def test_fabricated_quote_is_not_located(self):
		located = evidence.locate_quote(
			"Surcharge on total income exceeding Rs. 2 crore is 37% under the default regime.",
			SECTION_MARKDOWN,
		)
		self.assertFalse(located["found"])
		self.assertEqual((located["line_start"], located["char_start"]), (0, 0))

	def test_reads_the_body_off_a_chunk_or_a_row(self):
		quote = "Marginal relief is available"
		self.assertTrue(evidence.locate_quote(quote, {"markdown": PAGE_11})["found"])
		self.assertTrue(evidence.locate_quote(quote, {"text": PAGE_11})["found"])
		self.assertFalse(evidence.locate_quote(quote, "")["found"])
		self.assertFalse(evidence.locate_quote("", PAGE_11)["found"])


class TestFigureFidelity(FrappeTestCase):
	"""Prose may drift; a figure may not.

	Both cases here were found VERIFYING against the real ICAI page 6 before the figure gate
	existed: fuzzy similarity is length-dependent, so a single wrong digit is a rounding error
	to it and a statutory rate to a student.
	"""

	# The real line, ICAI SARANSH p.6, canonical_markdown line 30.
	HEC_LINE = (
		"*   HEC @4% on amount of income-tax (+) surcharge, if any OR (-) rebate u/s 87A, "
		"if applicable, is levied."
	)

	def test_flipped_accounting_sign_is_not_verified(self):
		# Scored 0.85 and verified before the gate: "(+)" and "(-)" carry no word tokens, so a
		# token-similarity match cannot see the difference between adding and subtracting.
		located = evidence.locate_quote(
			self.HEC_LINE.replace("(+) surcharge", "(-) surcharge"), self.HEC_LINE
		)
		self.assertFalse(located["found"])
		self.assertEqual(located["reason"], evidence.FIGURES_DIFFER)

	def test_one_wrong_digit_in_a_long_quote_is_not_verified(self):
		# The dangerous direction: padding the quote raises the fuzzy ratio, so the longer the
		# citation the better a wrong rate hides. Length must not buy credibility.
		long_source = f"{PAGE_10}\n{self.HEC_LINE} " + "Marginal relief is available. " * 12
		fabricated = f"{self.HEC_LINE} " + "Marginal relief is available. " * 12
		fabricated = fabricated.replace("@4%", "@6%")

		similarity = evidence.best_token_window(
			evidence.get_tokens(fabricated), evidence.tokens_with_offsets(*evidence.normalise(long_source))
		)
		self.assertGreater(similarity[2], evidence.MIN_QUOTE_SCORE, "the prose gate alone would pass this")

		located = evidence.locate_quote(fabricated, long_source)
		self.assertFalse(located["found"])
		self.assertEqual(located["reason"], evidence.FIGURES_DIFFER)

	def test_a_misquoted_statutory_reference_is_not_verified(self):
		located = evidence.locate_quote(self.HEC_LINE.replace("87A", "87B"), self.HEC_LINE)
		self.assertFalse(located["found"])
		self.assertEqual(located["reason"], evidence.FIGURES_DIFFER)

	def test_the_true_line_still_verifies(self):
		# The gate must not be a blanket refusal — the control for every rejection above.
		located = evidence.locate_quote(
			"HEC @4% on amount of income-tax (+) surcharge, if any OR (-) rebate u/s 87A", self.HEC_LINE
		)
		self.assertTrue(located["found"])
		self.assertIsNone(located["reason"])

	def test_prose_may_still_drift_around_identical_figures(self):
		self.assertEqual(evidence.get_figures("Rs. 1,00,000"), evidence.get_figures("\u20b91,00,000"))
		located = evidence.locate_quote(
			"Exceeding \u20b92 crore \u2013 25%", "| Exceeding **Rs. 2 crore** | 25% |"
		)
		self.assertTrue(located["found"])

	def test_figures_are_compared_in_order(self):
		source = "10% and 15%"
		self.assertTrue(evidence.figures_agree(source, source, 0, len(source)))
		self.assertFalse(evidence.figures_agree("15% and 10%", source, 0, len(source)))


class TestResolvePage(FrappeTestCase):
	def setUp(self):
		self.page_index = evidence.build_page_index(PAGES)

	def test_text_is_pinned_to_the_page_it_came_from(self):
		resolved = evidence.resolve_page(
			"| Exceeding Rs. 1 crore but not exceeding Rs. 2 crore | 15% |", self.page_index, 10
		)
		self.assertEqual(resolved["page_no"], 11)
		self.assertFalse(resolved["page_approximate"])

	def test_unmatched_text_falls_back_to_the_range_and_says_so(self):
		resolved = evidence.resolve_page("dividends declared by a foreign company", self.page_index, 10)
		self.assertEqual(resolved["page_no"], 10)
		self.assertTrue(resolved["page_approximate"])

	def test_no_pages_means_approximate_not_a_guess(self):
		resolved = evidence.resolve_page("anything at all", [], 7)
		self.assertEqual(resolved, {"page_no": 7, "page_approximate": True, "page_score": 0.0})


class TestVerifyCitations(FrappeTestCase):
	def setUp(self):
		self.document = seed_pages()

	def cited(self, **overrides) -> dict:
		return citation(self.document, **overrides)

	def test_real_quote_is_verified_and_carries_its_page_and_lines(self):
		quote = "Exceeding Rs. 2 crore"
		report = evidence.verify_citations(f'"{quote}" [1]', [self.cited(quote=quote)])

		row = report["citations"][0]
		self.assertEqual(row["status"], "verified")
		self.assertTrue(row["cited"])
		self.assertEqual(row["page_no"], 11)
		self.assertFalse(row["page_approximate"])
		self.assertGreater(row["page_line_start"], 0)
		self.assertEqual(report["unverified"], 0)
		self.assertTrue(report["grounded"])

	def test_fabricated_quote_is_rejected(self):
		report = evidence.verify_citations(
			"", [self.cited(quote="Surcharge exceeding Rs. 5 crore is levied at 37%.")]
		)
		row = report["citations"][0]
		self.assertEqual(row["status"], "unverified")
		self.assertEqual(row["reason"], "quote not found in the cited source")
		self.assertEqual(report["unverified"], 1)
		self.assertFalse(report["grounded"])

	def test_fabricated_quote_written_inline_in_the_answer_is_rejected(self):
		answer = (
			'The rate is 37%: "Surcharge exceeding Rs. 5 crore is levied at 37 per cent." [1]\n'
			'The base rate is 25%: "Exceeding Rs. 2 crore | 25%" [1]'
		)
		report = evidence.verify_citations(answer, [self.cited()])

		statuses = [row["status"] for row in report["quotes"]]
		self.assertEqual(statuses, ["unverified", "verified"])
		self.assertFalse(report["grounded"])

	def test_marker_with_no_source_is_unverified(self):
		report = evidence.verify_citations('"Marginal relief is available" [4]', [self.cited()])
		self.assertEqual(report["quotes"][0]["reason"], "citation marker has no source")

	def test_citation_without_a_quote_is_unquoted_not_verified(self):
		report = evidence.verify_citations("", [self.cited()])
		self.assertEqual(report["citations"][0]["status"], "unquoted")
		self.assertEqual(report["verified"], 0)

	def test_attach_keeps_the_unverified_quote_and_its_status(self):
		fabricated = "Surcharge exceeding Rs. 5 crore is levied at 37%."
		attached = evidence.attach_verified_quotes("", [self.cited(quote=fabricated)])

		self.assertEqual(attached[0]["quote"], fabricated)
		self.assertEqual(attached[0]["quote_status"], "unverified")
		self.assertEqual(attached[0]["text"], SECTION_MARKDOWN)

	def test_attach_pins_a_verified_quote_to_page_and_line(self):
		attached = evidence.attach_verified_quotes("", [self.cited(quote="Exceeding Rs. 2 crore")])
		self.assertEqual(attached[0]["quote_status"], "verified")
		self.assertEqual(attached[0]["quote_page_no"], 11)
		self.assertFalse(attached[0]["quote_page_approximate"])
		self.assertGreater(attached[0]["quote_page_line_start"], 0)

	def test_no_citations_reports_nothing_rather_than_throwing(self):
		self.assertEqual(evidence.verify_citations("answer with no sources", [])["total"], 0)


class TestChunkProvenance(FrappeTestCase):
	def build(self):
		return chunk.build_chunks(
			[SECTION_ROW],
			{"DOC-1": {"name": "DOC-1", "title": "SARANSH", "project": "PRJ-1"}},
			{},
			{"DOC-1": PAGES},
		)

	def test_chunk_resolves_to_a_single_page_not_the_range(self):
		chunks = self.build()
		self.assertTrue(chunks)
		# The whole section is one chunk here, and it is mostly page 11.
		self.assertIn(chunks[0].page_no, (10, 11))
		self.assertGreaterEqual(chunks[0].line_start, 1)
		self.assertGreaterEqual(chunks[0].line_end, chunks[0].line_start)

	def test_line_span_indexes_the_section_markdown(self):
		item = self.build()[0]
		lines = SECTION_MARKDOWN.split("\n")
		self.assertEqual(lines[item.line_start - 1].strip(), "## AMT")

	def test_without_pages_every_chunk_is_flagged_approximate(self):
		chunks = chunk.build_chunks([SECTION_ROW], {"DOC-1": {}}, {})
		self.assertTrue(all(item.page_approximate for item in chunks))
		self.assertTrue(all(item.page_no == SECTION_ROW["page_start"] for item in chunks))

	def test_row_carries_every_provenance_field_the_schema_declares(self):
		from wikify.rag import store

		row = chunk.chunk_row(self.build()[0], [0.0] * 4)
		self.assertEqual(set(row), {field.name for field in store.chunk_schema()})

	def test_overlap_is_added_after_the_pieces_are_located(self):
		markdown = "\n\n".join(f"Paragraph {index} " + "word " * 120 for index in range(4))
		pieces = chunk.split_pieces(markdown)
		self.assertGreater(len(pieces), 1)
		self.assertEqual(chunk.split_markdown(markdown), chunk.add_overlap(pieces))
		# Every un-overlapped piece is a contiguous run of the source, which is what makes
		# its line span meaningful.
		for piece in pieces:
			self.assertTrue(evidence.locate_quote(piece, markdown)["found"])


class TestPageLookup(FrappeTestCase):
	def test_unknown_documents_read_nothing(self):
		self.assertEqual(evidence.get_pages_by_document(["NOPE"], [1, 2]), {})
		self.assertEqual(evidence.get_pages_by_document([], []), {})
		self.assertEqual(chunk.get_pages_by_document(["NOPE"]), {})

	def test_pages_come_back_grouped_by_document(self):
		document = frappe.get_all("Source Page", fields=["source_document", "page_no"], limit=1)
		if not document:
			self.skipTest("no parsed Source Page on this site")
		grouped = evidence.get_pages_by_document([document[0]["source_document"]], [document[0]["page_no"]])
		self.assertEqual(list(grouped), [document[0]["source_document"]])
		self.assertEqual(grouped[document[0]["source_document"]][0]["page_no"], document[0]["page_no"])
