# Copyright (c) 2026, BWH and contributors
# For license information, please see license.txt

"""Slice 14 — agent write / action tools: the tree/taxonomy mutations, the deterministic
`use_page_image`, the confirm-gate on expensive tools, the terminal `ask_clarification`,
and the mutation realtime signal. litellm is mocked (no live model).
"""

from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from wikify.agent import session
from wikify.agent.context import Ctx
from wikify.agent.loop import AgentRunner
from wikify.agent.tools import pipeline as pl
from wikify.agent.tools import reparse as rep
from wikify.agent.tools import taxonomy as tax
from wikify.agent.tools import tree as tt
from wikify.engine import store
from wikify.engine.loader.sectionizer import Section
from wikify.tests import _cleanup

# A 1x1 PNG so `embed_page_image` has a real image File to point at.
_PNG = (
	b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
	b"\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00"
	b"\x00\x00IEND\xaeB`\x82"
)


def _sec(title, level, path, p_start, p_end):
	return Section(
		title=title,
		level=level,
		hierarchy_path=path,
		page_start=p_start,
		page_end=p_end,
		markdown=f"body of {title}",
	)


def _text_chunk(text):
	return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=text, tool_calls=None))])


def _tool_chunk(index, call_id, name, arguments):
	tc = SimpleNamespace(index=index, id=call_id, function=SimpleNamespace(name=name, arguments=arguments))
	return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=None, tool_calls=[tc]))])


class FakeLLM:
	def __init__(self, streams):
		self.streams = list(streams)
		self.calls = []

	def __call__(self, model, messages, tools, *, stream=True):
		self.calls.append({"model": model, "messages": list(messages)})
		return iter(self.streams.pop(0))


class TestAgentWrite(FrappeTestCase):
	def setUp(self):
		self.sd = frappe.get_doc({"doctype": "Source Document", "title": "Write Test"}).insert(
			ignore_permissions=True
		)
		# The loop commits mid-turn, defeating rollback — raw-delete what we insert.
		self.addCleanup(_cleanup.delete_document, self.sd.name)
		_cleanup.register_session_sweep(self)
		store.replace_sections(
			self.sd.name,
			[
				_sec("1. Alpha", 1, ["1. Alpha"], 1, 2),
				_sec("1.1 Alpha-One", 2, ["1. Alpha", "1.1 Alpha-One"], 1, 1),
				_sec("2. Beta", 1, ["2. Beta"], 3, 3),
			],
		)
		self.ctx = Ctx(session="x", user="Administrator", source_document=self.sd.name)

	def _sections(self):
		return frappe.get_all("Source Section", filters={"source_document": self.sd.name}, order_by="lft asc")

	# --- tree / taxonomy tools -------------------------------------------------------

	def test_set_section_type_validates(self):
		secs = self._sections()
		tax.sections.create_section_type("Consent Forms")  # seed a type
		out = tt._set_section_type(self.ctx, {"name": secs[0].name, "section_type": "consent_forms"})
		self.assertIn("Tagged", out)
		self.assertEqual(frappe.db.get_value("Source Section", secs[0].name, "section_type"), "consent_forms")
		bad = tt._set_section_type(self.ctx, {"name": secs[0].name, "section_type": "nope"})
		self.assertIn("Unknown Section Type", bad)

	def test_create_section_type_slugifies_and_is_idempotent(self):
		r1 = tax._create_section_type(self.ctx, {"type_name": "Surgical Procedures"})
		self.assertIn("surgical_procedures", r1)
		self.assertTrue(frappe.db.exists("Section Type", "surgical_procedures"))
		r2 = tax._create_section_type(self.ctx, {"type_name": "surgical procedures"})
		self.assertIn("already exists", r2)

	def test_move_section_reparents_and_guards_cycles(self):
		secs = self._sections()  # [Alpha, Alpha-One, Beta]
		out = tt._move_section(self.ctx, {"name": secs[2].name, "new_parent": secs[0].name})
		self.assertIn("Moved", out)
		self.assertEqual(
			frappe.db.get_value("Source Section", secs[2].name, "parent_source_section"), secs[0].name
		)
		# Can't move a node into its own subtree.
		bad = tt._move_section(self.ctx, {"name": secs[0].name, "new_parent": secs[2].name})
		self.assertIn("subtree", bad)

	def test_rename_and_toggle(self):
		secs = self._sections()
		tt._rename_section(self.ctx, {"name": secs[0].name, "title": "Renamed Alpha"})
		self.assertEqual(frappe.db.get_value("Source Section", secs[0].name, "title"), "Renamed Alpha")
		out = tt._toggle_include_in_wiki(self.ctx, {"name": secs[0].name, "include": False})
		self.assertIn("Excluded", out)
		self.assertEqual(frappe.db.get_value("Source Section", secs[0].name, "include_in_wiki"), 0)

	# --- deterministic page-image embed ----------------------------------------------

	def test_use_page_image_embeds_deterministically(self):
		page_name = store.add_page(self.sd.name, 1, "visual", _PNG, "baseline body")
		out = rep._use_page_image(self.ctx, {"page_no": 1})
		self.assertIn("embeds its rendered image", out)
		row = frappe.db.get_value(
			"Source Page", page_name, ["canonical_source", "canonical_markdown"], as_dict=True
		)
		self.assertEqual(row.canonical_source, "image")
		self.assertTrue(row.canonical_markdown.startswith("![Page 1]("))

	# --- the confirm-gate ------------------------------------------------------------

	def _make_session(self):
		sess = session.get_or_create(
			None, user="Administrator", scope="document", source_document=self.sd.name
		)
		session.append_message(sess.name, "user", "Re-parse the whole document.", status="done")
		session.set_running(sess.name, True)
		return sess

	def test_confirm_gate_holds_then_runs_on_approval(self):
		# Unapproved: the loop must NOT execute the tool; it feeds back a sentinel + emits
		# wikify_agent_confirm, and nothing is enqueued.
		sess = self._make_session()
		fake = FakeLLM(
			[
				[_tool_chunk(0, "c1", "reparse_document", '{"instruction": "keep tables"}')],
				[_text_chunk("This will re-parse the whole document — confirm?")],
			]
		)
		events = []
		with (
			patch("wikify.agent.llm.complete_with_tools", fake),
			patch("frappe.enqueue") as enq,
			patch("frappe.publish_realtime", lambda event, *a, **k: events.append(event)),
		):
			AgentRunner(sess.name, "Administrator").run()
		enq.assert_not_called()
		self.assertTrue(any(e.startswith("wikify_agent_confirm") for e in events))
		tool_msg = frappe.get_all(
			"Wikify Agent Message",
			filters={"session": sess.name, "role": "tool"},
			pluck="content",
		)
		self.assertTrue(any("NOT EXECUTED" in c for c in tool_msg))

		# Approved: the same call now executes (enqueues the remediate job). The tool resolves
		# the owning Import from the document, so link one.
		project = frappe.db.get_value("Wikify Project", {"is_default": 1}, "name")
		frappe.get_doc(
			{
				"doctype": "Wikify Import",
				"import_title": "Write Test Import",
				"project": project,
				"pdf": "/private/files/x.pdf",
				"source_document": self.sd.name,
				"status": "Review",
			}
		).insert(ignore_permissions=True)
		sess2 = self._make_session()
		fake2 = FakeLLM(
			[
				[_tool_chunk(0, "c2", "reparse_document", '{"instruction": "keep tables"}')],
				[_text_chunk("Re-parse started.")],
			]
		)
		with (
			patch("wikify.agent.llm.complete_with_tools", fake2),
			patch("frappe.enqueue") as enq2,
		):
			AgentRunner(sess2.name, "Administrator", approved_tools=["reparse_document"]).run()
		enq2.assert_called_once()
		self.assertEqual(enq2.call_args.args[0], "wikify.jobs.remediate.run")

	# --- terminal ask_clarification --------------------------------------------------

	def test_ask_clarification_ends_turn(self):
		sess = self._make_session()
		fake = FakeLLM(
			[
				[
					_tool_chunk(
						0, "c1", "ask_clarification", '{"question": "Which page?", "options": ["1", "2"]}'
					)
				]
			]
		)
		events = []
		with (
			patch("wikify.agent.llm.complete_with_tools", fake),
			patch("frappe.publish_realtime", lambda event, *a, **k: events.append(event)),
		):
			AgentRunner(sess.name, "Administrator").run()
		self.assertTrue(any(e.startswith("wikify_agent_clarify") for e in events))
		clar = frappe.get_all(
			"Wikify Agent Message",
			filters={"session": sess.name, "role": "assistant", "status": "clarification"},
			fields=["content"],
		)
		self.assertEqual(len(clar), 1)
		self.assertIn("Which page?", clar[0].content)
		# Only one model round — the terminal tool ended the turn.
		self.assertEqual(len(fake.calls), 1)

	# --- mutation realtime signal ----------------------------------------------------

	def test_mutating_tool_emits_mutation_event(self):
		sess = self._make_session()
		secs = self._sections()
		fake = FakeLLM(
			[
				[_tool_chunk(0, "c1", "rename_section", f'{{"name": "{secs[0].name}", "title": "Z"}}')],
				[_text_chunk("Renamed.")],
			]
		)
		events = []
		with (
			patch("wikify.agent.llm.complete_with_tools", fake),
			patch("frappe.publish_realtime", lambda event, *a, **k: events.append(event)),
		):
			AgentRunner(sess.name, "Administrator").run()
		self.assertIn("wikify_agent_mutation", events)

	def test_mutations_batch_into_single_event_per_turn(self):
		"""0.4 slice 25 — N mutating tools → ONE aggregated mutation event at completion."""
		sess = self._make_session()
		secs = self._sections()
		fake = FakeLLM(
			[
				[
					_tool_chunk(0, "c1", "rename_section", f'{{"name": "{secs[0].name}", "title": "Y"}}'),
					_tool_chunk(1, "c2", "rename_section", f'{{"name": "{secs[2].name}", "title": "Z"}}'),
				],
				[_text_chunk("Renamed both sections.")],
			]
		)
		events = []
		with (
			patch("wikify.agent.llm.complete_with_tools", fake),
			patch(
				"frappe.publish_realtime",
				lambda event, payload=None, *a, **k: events.append((event, payload)),
			),
		):
			AgentRunner(sess.name, "Administrator").run()

		mutation_events = [e for e in events if e[0] == "wikify_agent_mutation"]
		self.assertEqual(len(mutation_events), 1)
		payload = mutation_events[0][1]
		self.assertEqual(payload["count"], 2)
		self.assertEqual(payload["layers"], ["tree"])
		self.assertEqual(payload["source_documents"], [self.sd.name])
		# The complete event carries the counts so the chat card renders immediately.
		complete = next(e for e in events if e[0].startswith("wikify_agent_complete"))
		self.assertEqual(complete[1]["mutation_count"], 2)
		self.assertEqual(complete[1]["mutated_tools"], ["rename_section", "rename_section"])
