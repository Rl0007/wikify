# Copyright (c) 2026, BWH and contributors
# For license information, please see license.txt

"""The mermaid gate + the ICAI page-11 correctness bar.

Page 11 of the ICAI Final Paper 4 referencer is a surcharge rate table. The old pipeline sent
it through `flowchart TD`, which hung the income slabs off one node and the surcharge rates off
another as parallel dangling branches — nothing bound a slab to its rate, so a student revising
from the output would quote the wrong statutory rate. These tests hold the two halves of the
fix: the gate rejects that shape, and the markdown we now store binds each slab to exactly one
rate in its own row.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from wikify.engine import diagrams

# The real corrupt output measured on page 11 before the fix (verbatim shape).
FLATTENED_TABLE_MERMAID = """flowchart TD
	A["Individual/HUF/AoP/BoI"] --> F["Particulars"]
	A --> G["Rate of surcharge on income-tax"]
	F --> F1["(i) TI ... > Rs 50 lakhs but <= Rs 1 crore"]
	F --> F2["(ii) TI ... > Rs 1 crore but <= Rs 2 crore"]
	F --> F3["(iii) TI ... > Rs 2 crore but <= Rs 5 crore"]
	F --> F4["(iv) TI ... > Rs 5 crore"]
	G --> G1["10%"]
	G --> G2["15%"]
	G --> G3["25%"]
	G --> G4["37%"]
"""

# Page 6 of `mhj80jag5u`, verbatim. The content is right — it is the unquoted label whose inner
# `]` closes the node early that mermaid cannot read, and ICAI prints every statutory reference
# exactly this way. Real mermaid 11 rejects this string and accepts the quoted repair of it.
STATUTORY_BRACKET_MERMAID = """flowchart TD
	A[CAPITAL ASSET<br>[Section 2(14)]] --> B[Property of any kind held by an assessee]
	A --> C[Excludes stock-in-trade [Section 2(14)(a)]]
	B --> D[Whether or not connected with his business or profession]
"""

GENUINE_FLOW_MERMAID = """flowchart TD
	A["Individual/HUF/AoP/BoI and Artificial Juridical Person"] --> B["paying tax under default tax regime u/s 115BAC"]
	A --> C["exercising the option to shift out of the default tax regime"]
"""

# `| cell | cell |` rows, separator rows excluded.
_TABLE_ROW_RE = re.compile(r"^\s*\|(?!\s*[-:| ]+\|\s*$).*\|\s*$")
_RATE_RE = re.compile(r"\b(?:not\s+exceeding\s+)?\d+(?:\.\d+)?%", re.IGNORECASE)
_SLAB_RE = re.compile(r"₹\s*[\d.]+\s*(?:lakhs?|crores?)", re.IGNORECASE)


def table_rows(markdown: str) -> list[list[str]]:
	"""Cells of every pipe-table row in the markdown, in document order."""
	rows = []
	for line in markdown.splitlines():
		if _TABLE_ROW_RE.match(line):
			rows.append([cell.strip() for cell in line.strip().strip("|").split("|")])
	return rows


class TestMermaidGate(unittest.TestCase):
	def test_flattened_table_is_rejected(self):
		errors = diagrams.diagram_errors(FLATTENED_TABLE_MERMAID)
		self.assertTrue(errors, "a grid flattened into parallel dangling branches must be rejected")
		self.assertTrue(
			any("bare values" in error or "parallel leaf groups" in error for error in errors),
			f"expected a tabular-shape rejection, got {errors}",
		)

	def test_genuine_flow_is_kept(self):
		self.assertEqual(diagrams.diagram_errors(GENUINE_FLOW_MERMAID), [])

	def test_broken_syntax_is_rejected(self):
		self.assertTrue(diagrams.diagram_errors('flowchart TD\n\tA["unbalanced] --> B["ok"]'))
		self.assertTrue(diagrams.diagram_errors("sequenceDiagram\n\tA ->> B: hi"))
		self.assertTrue(diagrams.diagram_errors('flowchart TD\n\tA["lonely node"]'))

	def test_rejected_block_falls_back_to_the_source_crop(self):
		markdown = f"# Surcharge\n\n```mermaid\n{FLATTENED_TABLE_MERMAID}```\n"
		cleaned, notes = diagrams.remove_unverified_diagrams(markdown, "/files/page-0011.png")
		self.assertNotIn("mermaid", cleaned)
		self.assertIn("![Source region](/files/page-0011.png)", cleaned)
		self.assertTrue(notes)

	def test_a_surviving_table_needs_no_crop(self):
		markdown = f"| Slab | Rate |\n|---|---|\n| A | 10% |\n\n```mermaid\n{FLATTENED_TABLE_MERMAID}```\n"
		cleaned, _notes = diagrams.remove_unverified_diagrams(markdown, "/files/page-0011.png")
		self.assertNotIn("![Source region]", cleaned)
		self.assertIn("| A | 10% |", cleaned)

	def test_valid_markdown_is_returned_untouched(self):
		markdown = f"# Flow\n\n```mermaid\n{GENUINE_FLOW_MERMAID}```\n"
		self.assertEqual(diagrams.remove_unverified_diagrams(markdown, "/files/x.png"), (markdown, []))


class TestLabelRepair(unittest.TestCase):
	"""Correct content must not be thrown away over a missing pair of quotes."""

	def test_the_statutory_bracket_statement_is_the_one_that_used_to_be_rejected(self):
		errors = diagrams.diagram_errors(STATUTORY_BRACKET_MERMAID)
		self.assertIn(
			"unparseable statement: 'A[CAPITAL ASSET<br>[Section 2(14)]] --> B[Property of any ki'",
			errors,
		)

	def test_repair_makes_it_validate(self):
		repaired = diagrams.quote_node_labels(STATUTORY_BRACKET_MERMAID)
		self.assertEqual(diagrams.diagram_errors(repaired), [])
		self.assertIn('A["CAPITAL ASSET<br>[Section 2(14)]"]', repaired)
		self.assertIn('C["Excludes stock-in-trade [Section 2(14)(a)]"]', repaired)

	def test_the_gate_repairs_instead_of_rejecting(self):
		markdown = f"# Capital gains\n\n```mermaid\n{STATUTORY_BRACKET_MERMAID}```\n"
		cleaned, notes = diagrams.remove_unverified_diagrams(markdown, "/files/page-0006.png")
		self.assertNotIn("![Source region]", cleaned)
		self.assertNotIn("mermaid rejected", "; ".join(notes))
		self.assertEqual(notes, ["mermaid repaired: node labels quoted"])
		self.assertIn("Section 2(14)", cleaned)
		blocks = diagrams.mermaid_blocks(cleaned)
		self.assertEqual(len(blocks), 1)
		self.assertEqual(diagrams.diagram_errors(blocks[0]), [])

	def test_repair_adds_quotes_and_nothing_else(self):
		repaired = diagrams.quote_node_labels(STATUTORY_BRACKET_MERMAID)
		self.assertEqual(repaired.replace('"', ""), STATUTORY_BRACKET_MERMAID)

	def test_already_quoted_labels_are_left_alone(self):
		self.assertEqual(diagrams.quote_node_labels(GENUINE_FLOW_MERMAID), GENUINE_FLOW_MERMAID)

	def test_an_inner_quote_is_escaped_rather_than_doubled(self):
		repaired = diagrams.quote_node_labels('flowchart TD\n\tA[He said "yes"] --> B[ok]\n')
		self.assertIn('A["He said #quot;yes#quot;"]', repaired)
		self.assertEqual(diagrams.diagram_errors(repaired), [])

	def test_a_semicolon_inside_a_label_does_not_split_the_statement(self):
		source = 'flowchart TD\n\tA["Rs 1,00,000; see proviso"] --> B["ok"]\n'
		self.assertEqual(diagrams.statements(source), ['A["Rs 1,00,000; see proviso"] --> B["ok"]'])
		self.assertEqual(diagrams.diagram_errors(source), [])

	def test_ampersand_node_chaining_is_understood(self):
		"""`A & B --> C` is valid mermaid (real mermaid 11 parses it) and used to be rejected as
		an unparseable statement, which cost page 6 its diagram a second time."""
		source = (
			"flowchart TD\n"
			'\tA["Land"] --> N & O & P\n'
			'\tN & O & P --- S["These assets are hence, capital assets"]\n'
		)
		self.assertEqual(diagrams.diagram_errors(source), [])
		_labels, edges, _broken = diagrams.parse_flowchart(source)
		self.assertIn(("A", "N"), edges)
		self.assertIn(("P", "S"), edges)

	def test_an_ampersand_inside_a_label_is_not_a_node_separator(self):
		self.assertEqual(diagrams.diagram_errors('flowchart TD\n\tA["Profit & Loss"] --> B["ok"]\n'), [])

	def test_repair_never_rescues_a_flattened_table(self):
		"""Quoting fixes syntax, never meaning — an unbound grid is still rejected."""
		markdown = f"# Surcharge\n\n```mermaid\n{FLATTENED_TABLE_MERMAID}```\n"
		cleaned, notes = diagrams.remove_unverified_diagrams(markdown, "/files/page-0011.png")
		self.assertNotIn("mermaid", cleaned)
		self.assertTrue(any("mermaid rejected" in note for note in notes))

	def test_repair_never_rescues_broken_structure(self):
		for source in ("flowchart TD\n\tA[lonely node]\n", "sequenceDiagram\n\tA ->> B: hi\n"):
			cleaned, notes = diagrams.remove_unverified_diagrams(f"```mermaid\n{source}```\n", "")
			self.assertNotIn("mermaid", cleaned)
			self.assertTrue(any("mermaid rejected" in note for note in notes), notes)


class TestIcaiPage11(unittest.TestCase):
	"""The correctness bar: the stored page-11 markdown, as re-parsed by the fixed pipeline."""

	@classmethod
	def setUpClass(cls):
		fixture = Path(__file__).parent / "fixtures" / "icai_page_11_canonical.md"
		cls.markdown = fixture.read_text(encoding="utf-8")

	def test_no_diagram_encodes_the_rate_table(self):
		for source in diagrams.mermaid_blocks(self.markdown):
			self.assertEqual(diagrams.diagram_errors(source), [], f"stored an ungated diagram: {source[:80]}")
			self.assertFalse(_RATE_RE.search(source), "a surcharge rate is encoded in a flowchart")

	def test_every_slab_binds_to_exactly_one_rate_in_its_own_row(self):
		"""Cell adjacency: a row carrying an income slab carries its rate in the same row."""
		slab_rows = [row for row in table_rows(self.markdown) if _SLAB_RE.search(" ".join(row))]
		self.assertGreaterEqual(len(slab_rows), 6, "the slab rows themselves went missing")
		for row in slab_rows:
			rates = _RATE_RE.findall(" ".join(row))
			self.assertEqual(len(rates), 1, f"row does not bind to exactly one rate: {row}")
			# The rate must sit in a cell of its own, next to the slab — not narrated inside it.
			self.assertTrue(
				any(_RATE_RE.fullmatch(cell) for cell in row),
				f"the rate is not in its own cell: {row}",
			)

	def test_statutory_references_and_rupee_amounts_are_verbatim(self):
		for token in ("u/s 115BAC", "u/s 111A, 112 and 112A", "₹ 50 lakhs", "₹ 5 crore", "37%"):
			self.assertIn(token, self.markdown)

	def test_both_regimes_survive_with_their_own_rates(self):
		"""The default regime tops out at 25%; only the normal-provisions one reaches 37%."""
		default_regime, _, normal_regime = self.markdown.partition("exercising the option to shift out")
		self.assertNotIn("37%", default_regime)
		self.assertIn("37%", normal_regime)
