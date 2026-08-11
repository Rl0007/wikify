# Copyright (c) 2026, BWH and contributors
# For license information, please see license.txt

"""Slice 12 — agent walking skeleton: the litellm tool-loop, the `read_tree` tool, message
persistence, the concurrency guard, and cancel. litellm is mocked (no live model).
"""

from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from wikify.agent import session
from wikify.agent.context import Ctx, resolve_attachments
from wikify.agent.loop import AgentRunner, cancel_key, request_cancel
from wikify.agent.tools.read import (
	_list_section_types,
	_read_page,
	_read_section,
	_read_tree,
	_search_sections,
)
from wikify.api import agent as agent_api
from wikify.engine import store
from wikify.engine.loader.sectionizer import Section
from wikify.tests import _cleanup


def _sec(title, level, path, p_start, p_end):
	return Section(
		title=title,
		level=level,
		hierarchy_path=path,
		page_start=p_start,
		page_end=p_end,
		markdown=f"body of {title}",
	)


def _system_text(content):
	"""Flatten a system message's content (string, or cache-marked content-part list)."""
	if isinstance(content, list):
		return "".join(part.get("text", "") for part in content)
	return content or ""


def _text_chunk(text):
	return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=text, tool_calls=None))])


def _tool_chunk(index, call_id, name, arguments):
	tc = SimpleNamespace(
		index=index,
		id=call_id,
		function=SimpleNamespace(name=name, arguments=arguments),
	)
	return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=None, tool_calls=[tc]))])


class FakeLLM:
	"""Replays canned streaming responses for successive `complete_with_tools` calls."""

	def __init__(self, streams):
		self.streams = list(streams)
		self.calls = []

	def __call__(self, model, messages, tools, *, stream=True):
		self.calls.append({"model": model, "messages": list(messages)})
		return iter(self.streams.pop(0))


class TestAgent(FrappeTestCase):
	def setUp(self):
		self.sd = frappe.get_doc({"doctype": "Source Document", "title": "Agent Test"}).insert(
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

	# --- the read_tree tool ----------------------------------------------------------

	def test_read_tree_renders_hierarchy(self):
		ctx = Ctx(session="x", user="Administrator", source_document=self.sd.name)
		out = _read_tree(ctx, {})
		self.assertIn("1. Alpha", out)
		self.assertIn("  1.1 Alpha-One".strip(), out)
		self.assertIn("[p.1-2]", out)

	def test_read_tree_without_document_asks(self):
		ctx = Ctx(session="x", user="Administrator")
		out = _read_tree(ctx, {})
		self.assertIn("No document", out)

	# --- the loop --------------------------------------------------------------------

	def _make_session(self):
		sess = session.get_or_create(
			None, user="Administrator", scope="document", source_document=self.sd.name
		)
		session.append_message(sess.name, "user", "Summarize the tree.", status="done")
		session.set_running(sess.name, True)
		return sess

	def test_loop_calls_tool_then_answers(self):
		"""Round 1 streams a read_tree call; round 2 streams the final answer."""
		sess = self._make_session()
		fake = FakeLLM(
			[
				[_tool_chunk(0, "call_1", "read_tree", "{}")],
				[_text_chunk("The tree has "), _text_chunk("Alpha and Beta.")],
			]
		)
		with patch("wikify.agent.llm.complete_with_tools", fake):
			AgentRunner(sess.name, "Administrator").run()

		msgs = frappe.get_all(
			"Wikify Agent Message",
			filters={"session": sess.name},
			fields=["role", "status", "tool_name", "content", "tool_calls"],
			order_by="creation asc",
		)
		roles = [m.role for m in msgs]
		self.assertEqual(roles, ["user", "assistant", "tool", "assistant"])

		# The assistant's first turn requested the tool; the tool ran read_tree.
		self.assertIn("read_tree", msgs[1].tool_calls)
		self.assertEqual(msgs[2].tool_name, "read_tree")
		self.assertIn("Alpha", msgs[2].content)
		# Final answer is the streamed text, accumulated.
		self.assertEqual(msgs[3].content, "The tree has Alpha and Beta.")
		self.assertEqual(msgs[3].status, "done")

		# is_running cleared; the tool result was fed back to the model on round 2.
		self.assertEqual(frappe.db.get_value("Wikify Agent Session", sess.name, "is_running"), 0)
		self.assertEqual(len(fake.calls), 2)
		self.assertEqual(fake.calls[1]["messages"][-1]["role"], "tool")

	def test_loop_streams_realtime(self):
		sess = self._make_session()
		fake = FakeLLM([[_text_chunk("hi "), _text_chunk("there")]])
		events = []
		with (
			patch("wikify.agent.llm.complete_with_tools", fake),
			patch("frappe.publish_realtime", lambda event, *a, **k: events.append(event)),
		):
			AgentRunner(sess.name, "Administrator").run()
		self.assertTrue(any(e.startswith("wikify_agent_stream") for e in events))
		self.assertTrue(any(e.startswith("wikify_agent_complete") for e in events))

	def test_cancel_stops_mid_stream(self):
		"""A cancel signalled mid-stream stops the loop without persisting an answer.

		`run()` clears stale flags on entry, so cancel must arrive while streaming — here
		the stream requests it as it yields the first chunk; the per-chunk check catches it.
		"""
		sess = self._make_session()

		def cancelling_stream():
			request_cancel(sess.name)
			yield _text_chunk("should not finish")

		fake = FakeLLM([cancelling_stream()])
		with patch("wikify.agent.llm.complete_with_tools", fake):
			AgentRunner(sess.name, "Administrator").run()
		# No assistant answer persisted as done with that text.
		answers = frappe.get_all(
			"Wikify Agent Message",
			filters={"session": sess.name, "role": "assistant"},
			pluck="content",
		)
		self.assertNotIn("should not finish", answers)
		self.assertFalse(frappe.cache().get_value(cancel_key(sess.name)))

	# --- the API guard ---------------------------------------------------------------

	def test_run_rejects_when_already_running(self):
		sess = session.get_or_create(None, user="Administrator", scope="global")
		session.set_running(sess.name, True)
		with patch("frappe.enqueue"):
			with self.assertRaises(frappe.ValidationError):
				agent_api.run(prompt="hello", session_id=sess.name)

	def test_run_enqueues_and_returns_ids(self):
		with patch("frappe.enqueue") as enq:
			result = agent_api.run(prompt="hello", scope="document", source_document=self.sd.name)
		self.assertIn("session_id", result)
		self.assertIn("message_id", result)
		enq.assert_called_once()
		self.assertEqual(frappe.db.get_value("Wikify Agent Session", result["session_id"], "is_running"), 1)

	# --- slice 13: the extra read tools ----------------------------------------------

	def _first_section(self):
		return frappe.get_all(
			"Source Section", filters={"source_document": self.sd.name}, order_by="lft asc", limit=1
		)[0].name

	def test_read_section_returns_body_and_meta(self):
		name = self._first_section()
		out = _read_section(Ctx(session="x", user="Administrator"), {"name": name})
		self.assertIn("1. Alpha", out)
		self.assertIn("body of 1. Alpha", out)

	def test_read_page_uses_attached_document(self):
		frappe.get_doc(
			{
				"doctype": "Source Page",
				"source_document": self.sd.name,
				"page_no": 1,
				"verdict": "pass",
				"canonical_markdown": "Canonical page one body.",
			}
		).insert(ignore_permissions=True)
		ctx = Ctx(session="x", user="Administrator", source_document=self.sd.name)
		out = _read_page(ctx, {"page_no": 1})
		self.assertIn("Canonical page one body.", out)
		self.assertIn("Page 1", out)

	def _make_type(self, **kwargs):
		# Unique label too — Section Type labels are identity (0.4 slice 21), and a
		# committed leak of a real-looking label ("Introduction") once polluted the dev
		# taxonomy. Cleanup is registered because the agent loop commits mid-test,
		# defeating the FrappeTestCase rollback.
		tname = f"t_{frappe.generate_hash(length=6)}"
		kwargs.setdefault("label", f"Test Type {tname}")
		doc = frappe.get_doc({"doctype": "Section Type", "type_name": tname, **kwargs}).insert(
			ignore_permissions=True
		)
		self.addCleanup(frappe.db.delete, "Section Type", {"name": tname})
		return doc

	def test_list_section_types_lists_taxonomy(self):
		st = self._make_type()
		out = _list_section_types(Ctx(session="x", user="Administrator"), {})
		self.assertIn(st.type_name, out)

	def test_search_sections_by_type_spans_documents(self):
		st = self._make_type()
		name = self._first_section()
		frappe.db.set_value("Source Section", name, "section_type", st.type_name)
		out = _search_sections(Ctx(session="x", user="Administrator"), {"section_type": st.type_name})
		self.assertIn("1. Alpha", out)
		self.assertIn(self.sd.name, out)

	def test_explicit_bad_document_falls_back_to_attached(self):
		"""A model echoing "Title (id)" instead of the bare id falls back to the attachment."""
		ctx = Ctx(session="x", user="Administrator", source_document=self.sd.name)
		out = _read_tree(ctx, {"source_document": f"Agent Test ({self.sd.name})"})
		self.assertIn("1. Alpha", out)

	# --- slice 13: attachment resolution ---------------------------------------------

	def test_resolve_document_attachment_sets_scope_and_block(self):
		resolved = resolve_attachments([{"type": "document", "name": self.sd.name}])
		self.assertEqual(resolved.source_document, self.sd.name)
		self.assertIn("1. Alpha", resolved.block)

	def test_resolve_section_attachment_pins_its_document(self):
		name = self._first_section()
		resolved = resolve_attachments([{"type": "section", "name": name}])
		self.assertEqual(resolved.source_document, self.sd.name)
		self.assertIn("body of 1. Alpha", resolved.block)

	def test_resolve_wiki_view_section_adds_framing_line(self):
		"""A section attached from the Wiki tab (view=wiki) frames the block as a
		rendered wiki page; a plain section attachment doesn't."""
		name = self._first_section()
		wiki = resolve_attachments([{"type": "section", "name": name, "view": "wiki"}])
		self.assertIn("rendered wiki page", wiki.block)
		plain = resolve_attachments([{"type": "section", "name": name}])
		self.assertNotIn("rendered wiki page", plain.block)

	def test_resolve_project_attachment_injects_context_prompt(self):
		proj = frappe.get_doc(
			{
				"doctype": "Wikify Project",
				"project_name": f"Proj {frappe.generate_hash(length=6)}",
				"context_prompt": "Use UK spelling.",
			}
		).insert(ignore_permissions=True)
		self.addCleanup(_cleanup.delete_project, proj.name)
		resolved = resolve_attachments([{"type": "project", "name": proj.name}])
		self.assertEqual(resolved.project, proj.name)
		self.assertEqual(resolved.project_context, "Use UK spelling.")

	def test_stale_attachment_is_skipped(self):
		resolved = resolve_attachments([{"type": "document", "name": "does-not-exist"}])
		self.assertEqual(resolved.block, "")

	def test_loop_prepends_attachment_block(self):
		"""An attached document's tree outline reaches the model as a system message."""
		sess = session.get_or_create(None, user="Administrator", scope="global")
		session.append_message(sess.name, "user", "What's in this doc?", status="done")
		session.set_running(sess.name, True)
		fake = FakeLLM([[_text_chunk("It has Alpha and Beta.")]])
		with patch("wikify.agent.llm.complete_with_tools", fake):
			AgentRunner(
				sess.name, "Administrator", attachments=[{"type": "document", "name": self.sd.name}]
			).run()
		# System content may be a plain string or a cache-marked [{text, cache_control}] list
		# (Anthropic models) — flatten before asserting.
		systems = [_system_text(m["content"]) for m in fake.calls[0]["messages"] if m["role"] == "system"]
		self.assertTrue(any("1. Alpha" in s for s in systems))

	# --- slice 13: session listing ---------------------------------------------------

	def test_list_and_new_session(self):
		created = agent_api.new_session(scope="document", source_document=self.sd.name)
		self.assertIn("session_id", created)
		frappe.db.set_value("Wikify Agent Session", created["session_id"], "title", "My chat")
		listed = agent_api.list_sessions()
		self.assertTrue(any(s["name"] == created["session_id"] for s in listed))


class TestSearchSectionsRecall(FrappeTestCase):
	"""`search_sections` must never let a `query` hint look like an empty section type.

	Live regression: asked "How many job descriptions are in the Demo Corpus project?" the
	agent reasoned its way to the right type, searched with query="job descriptions", got
	"No sections of type X match", and told the user there were none — while 15 sections
	sat under titles reading "Job Description — …".
	"""

	def setUp(self):
		self.sd = frappe.get_doc({"doctype": "Source Document", "title": "Recall Test"}).insert(
			ignore_permissions=True
		)
		self.addCleanup(_cleanup.delete_document, self.sd.name)
		_cleanup.register_session_sweep(self)
		self.section_type = self._make_type(
			label=f"Staff Roles {frappe.generate_hash(length=6)}",
			description="Job descriptions and role profiles — one section per post.",
		)
		titles = [
			"Job Description — Ward Sister",
			"Job Description — Staff Nurse (Band 5)",
			"Job Description — Healthcare Assistant",
		]
		store.replace_sections(
			self.sd.name,
			[_sec("Roles and Responsibilities", 1, ["Roles and Responsibilities"], 1, 1)]
			+ [
				_sec(title, 2, ["Roles and Responsibilities", title], page, page)
				for page, title in enumerate(titles, start=2)
			],
		)
		for row in frappe.get_all(
			"Source Section", filters={"source_document": self.sd.name, "level": 2}, pluck="name"
		):
			frappe.db.set_value("Source Section", row, "section_type", self.section_type.type_name)

	def _make_type(self, **kwargs):
		type_name = f"t_{frappe.generate_hash(length=6)}"
		kwargs.setdefault("label", f"Test Type {type_name}")
		doc = frappe.get_doc({"doctype": "Section Type", "type_name": type_name, **kwargs}).insert(
			ignore_permissions=True
		)
		# The loop test commits mid-turn, so a plain delete would be rolled back with it.
		self.addCleanup(_cleanup.delete_section_type, type_name)
		return doc

	def _search(self, **args):
		return _search_sections(Ctx(session="x", user="Administrator"), args)

	def test_plural_query_matches_singular_titles(self):
		"""The exact failing shape: the user's plural wording vs singular stored titles."""
		out = self._search(section_type=self.section_type.type_name, query="job descriptions")
		self.assertIn("Ward Sister", out)
		self.assertIn("Staff Nurse", out)
		self.assertIn("Healthcare Assistant", out)
		self.assertNotIn("no sections", out.lower())

	def test_unmatched_query_returns_everything_and_says_so(self):
		out = self._search(section_type=self.section_type.type_name, query="aardvark husbandry")
		self.assertIn("IGNORED", out)
		self.assertIn("3 section(s)", out)
		self.assertIn("Ward Sister", out)

	def test_empty_type_reads_as_empty_not_as_a_filter_miss(self):
		empty_type = self._make_type()
		out = self._search(section_type=empty_type.type_name, source_document=self.sd.name)
		self.assertIn("no sections in", out)
		self.assertIn(self.section_type.type_name, out)  # names the types that do have content

	def test_project_title_resolves_to_its_id(self):
		"""The live failure: the user says "the Demo Corpus project" and the model passes
		that title, which used to scope the query to zero documents."""
		project = frappe.get_doc(
			{"doctype": "Wikify Project", "project_name": f"Recall Corpus {frappe.generate_hash(length=6)}"}
		).insert(ignore_permissions=True)
		self.addCleanup(_cleanup.delete_project, project.name)
		frappe.db.set_value("Source Document", self.sd.name, "project", project.name)

		out = self._search(section_type=self.section_type.type_name, project=project.project_name)
		self.assertIn(project.name, out)
		self.assertIn("Ward Sister", out)

	def test_unresolvable_project_says_the_scope_was_dropped(self):
		out = self._search(section_type=self.section_type.type_name, project="No Such Corpus")
		self.assertIn("matches no project", out)
		self.assertIn("Ward Sister", out)

	def test_unknown_type_names_near_matches(self):
		out = self._search(section_type="job_description")
		self.assertIn("not a Section Type", out)
		self.assertIn(self.section_type.type_name, out)

	def test_agent_loop_sees_the_count_for_the_failing_question(self):
		"""End to end through the loop: the tool message the model reads must carry the count."""
		sess = session.get_or_create(None, user="Administrator", scope="global")
		session.append_message(
			sess.name, "user", "How many job descriptions are in this project?", status="done"
		)
		session.set_running(sess.name, True)
		fake = FakeLLM(
			[
				[
					_tool_chunk(
						0,
						"call_1",
						"search_sections",
						'{"section_type": "%s", "query": "job descriptions"}' % self.section_type.type_name,
					)
				],
				[_text_chunk("There are 3 job descriptions.")],
			]
		)
		with patch("wikify.agent.llm.complete_with_tools", fake):
			AgentRunner(sess.name, "Administrator").run()
		tool_result = frappe.get_all(
			"Wikify Agent Message",
			filters={"session": sess.name, "role": "tool"},
			pluck="content",
		)[0]
		self.assertIn("3 of 3 section(s)", tool_result)
		self.assertIn("Ward Sister", tool_result)
