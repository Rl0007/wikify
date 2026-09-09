# Copyright (c) 2026, BWH and contributors
# For license information, please see license.txt

"""POC-2 phase 1 — routing, grounded answering, the whitelisted API, and the reindex hook.

Retrieval itself (embedding, LanceDB) is covered by the `rag/` unit tests; here the store
is stubbed so these assert the layer above it: which leg a route picks, when we refuse,
that a citation always resolves, and that the ACL reaches the store as a pre-filter rather
than a post-hoc trim.
"""

import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from wikify.agent import context as agent_context
from wikify.agent.registry import build_default_registry
from wikify.api import explore as api_explore
from wikify.api import permission
from wikify.api import rag as api_rag
from wikify.engine import settings
from wikify.rag import answer as rag_answer
from wikify.rag import events as rag_events
from wikify.rag import index as rag_index
from wikify.rag import rerank as rag_rerank
from wikify.rag import router as rag_router
from wikify.rag import search as rag_search
from wikify.rag import usage as rag_usage
from wikify.rag.search import Hit


def make_hit(
	title: str = "Backend Engineer",
	score: float = 0.03,
	rerank_score: float | None = None,
	vector_score: float | None = None,
) -> Hit:
	return Hit(
		chunk_id=f"{title}::0",
		section=f"sec-{title}",
		source_document="doc-1",
		document_title="Handbook",
		title=title,
		text=f"{title} body text.",
		section_type="job_description",
		hierarchy_path=f"Roles > {title}",
		page_start=3,
		page_end=4,
		wiki_route=None,
		wikify_import=None,
		score=score,
		rerank_score=rerank_score,
		vector_score=vector_score,
	)


def make_project(label: str):
	"""A uniquely-named project — `project_name` is unique and tests share a database."""
	return frappe.get_doc(
		{"doctype": "Wikify Project", "project_name": f"{label} {frappe.generate_hash(length=6)}"}
	).insert()


def llm_reply(content: str) -> dict:
	"""An `engine.llm.chat_completion` response body carrying `content`."""
	return {"choices": [{"message": {"content": content}}]}


class TestRagRouter(FrappeTestCase):
	def test_falls_back_to_hybrid_without_a_key(self):
		with patch.object(settings, "openrouter_key", return_value=""):
			decided = rag_router.route("all the job descriptions")
		self.assertEqual(decided.intent, "hybrid")
		self.assertIsNone(decided.section_type)
		self.assertEqual(decided.query, "all the job descriptions")
		self.assertTrue(decided.reason)

	def test_falls_back_when_the_model_reply_is_unusable(self):
		with (
			patch.object(settings, "openrouter_key", return_value="key"),
			patch.object(rag_router.llm, "chat_completion", return_value=llm_reply("sorry, no idea")),
		):
			decided = rag_router.route("all the job descriptions")
		self.assertEqual(decided.intent, "hybrid")

	def test_exhaustive_intent_keeps_a_real_section_type(self):
		section_type = frappe.get_all("Section Type", pluck="type_name", limit=1)[0]
		reply = (
			'```json\n{"intent": "exhaustive", "section_type": "%s", '
			'"query": "every job description", "reason": "Listing them all."}\n```' % section_type
		)
		with (
			patch.object(settings, "openrouter_key", return_value="key"),
			patch.object(rag_router.llm, "chat_completion", return_value=llm_reply(reply)),
		):
			decided = rag_router.route("give me all of them")
		self.assertEqual(decided.intent, "exhaustive")
		self.assertEqual(decided.section_type, section_type)
		self.assertEqual(decided.query, "every job description")
		self.assertEqual(decided.reason, "Listing them all.")

	def test_invented_section_type_downgrades_instead_of_filtering_to_nothing(self):
		reply = '{"intent": "exhaustive", "section_type": "not_a_real_type", "query": "q", "reason": "r"}'
		with (
			patch.object(settings, "openrouter_key", return_value="key"),
			patch.object(rag_router.llm, "chat_completion", return_value=llm_reply(reply)),
		):
			decided = rag_router.route("all the widgets")
		self.assertEqual(decided.intent, "semantic")
		self.assertIsNone(decided.section_type)

	def test_history_is_passed_to_the_model_for_follow_up_rewriting(self):
		reply = '{"intent": "semantic", "section_type": null, "query": "salary of the backend role", "reason": "r"}'
		with (
			patch.object(settings, "openrouter_key", return_value="key"),
			patch.object(rag_router.llm, "chat_completion", return_value=llm_reply(reply)) as completion,
		):
			decided = rag_router.route(
				"what about the second one?",
				None,
				[
					{"role": "user", "content": "list the roles"},
					{"role": "assistant", "content": "1. QA 2. Backend"},
				],
			)
		sent = completion.call_args[0][1]
		self.assertIn("list the roles", [message["content"] for message in sent])
		self.assertEqual(decided.query, "salary of the backend role")


class TestRagAnswer(FrappeTestCase):
	def test_refuses_when_retrieval_comes_back_empty(self):
		with patch.object(rag_search, "search", return_value=[]):
			result = rag_answer.answer(
				"what is the capital of Mongolia", rerank=False, allowed_projects=["PRJ"]
			)
		self.assertTrue(result["refused"])
		self.assertEqual(result["citations"], [])
		self.assertIn("couldn't find this in the wiki", result["answer"])

	def test_refuses_when_the_rerank_score_is_below_the_floor(self):
		weak = [make_hit(rerank_score=1.0), make_hit("Other", rerank_score=0.0)]
		with patch.object(rag_search, "search", return_value=weak):
			result = rag_answer.answer("something unrelated", allowed_projects=["PRJ"])
		self.assertTrue(result["refused"])

	def test_answers_and_cites_when_retrieval_is_strong(self):
		hits = [make_hit(rerank_score=9.0), make_hit("QA Engineer", rerank_score=8.0)]
		with (
			patch.object(rag_search, "search", return_value=hits),
			patch.object(settings, "openrouter_key", return_value="key"),
			patch.object(rag_answer, "generate", return_value="Two roles are listed [1][2]."),
		):
			result = rag_answer.answer("which roles are described", allowed_projects=["PRJ"])
		self.assertFalse(result["refused"])
		self.assertEqual(len(result["citations"]), 2)
		self.assertEqual(result["answer"], "Two roles are listed [1][2].")

	def test_citation_markers_without_a_source_are_dropped(self):
		self.assertEqual(rag_answer.drop_unknown_citations("a [1] b [7] c", 2), "a [1] b  c")

	def test_exhaustive_intent_uses_the_filter_leg(self):
		decided = rag_router.Route("exhaustive", "job_description", "all job descriptions", "r")
		with patch.object(rag_search, "search", return_value=[]) as search:
			rag_answer.retrieve(decided, "PRJ", False, allowed_projects=["PRJ"])
		kwargs = search.call_args.kwargs
		self.assertEqual(kwargs["mode"], "filter")
		self.assertEqual(kwargs["section_type"], "job_description")
		self.assertEqual(kwargs["allowed_projects"], ["PRJ"])

	def test_callbacks_deliver_route_then_citations_then_deltas(self):
		order = []
		hits = [make_hit(rerank_score=9.0)]

		def fake_generate(question, context, model, on_delta):
			on_delta("hello")
			return "hello [1]"

		with (
			patch.object(rag_search, "search", return_value=hits),
			patch.object(settings, "openrouter_key", return_value="key"),
			patch.object(rag_answer, "generate", side_effect=fake_generate),
		):
			rag_answer.answer(
				"which roles",
				allowed_projects=["PRJ"],
				on_route=lambda route: order.append("route"),
				on_citations=lambda citations: order.append("citations"),
				on_delta=lambda delta: order.append("delta"),
			)
		self.assertEqual(order, ["route", "citations", "delta"])


class TestRagApi(FrappeTestCase):
	def setUp(self):
		self.project = make_project("RAG API Test")

	def test_search_rejects_an_empty_query(self):
		with self.assertRaises(frappe.ValidationError):
			api_rag.search("   ")

	def test_search_passes_the_acl_list_into_the_store(self):
		with (
			patch.object(rag_search, "search", return_value=[]) as search,
			patch.object(api_rag, "readable_projects", return_value=["PRJ-ONLY-THIS"]),
		):
			result = api_rag.search("anything", use_router=False)
		self.assertEqual(search.call_args.kwargs["allowed_projects"], ["PRJ-ONLY-THIS"])
		self.assertIsNone(result["route"])
		self.assertEqual(result["hits"], [])

	def test_search_takes_the_section_type_from_the_route(self):
		decided = rag_router.Route("exhaustive", "job_description", "all job descriptions", "r")
		with (
			patch.object(rag_search, "search", return_value=[]) as search,
			patch.object(api_rag, "route_question", return_value=decided),
		):
			result = api_rag.search("give me all of them")
		self.assertEqual(search.call_args.kwargs["section_type"], "job_description")
		self.assertEqual(result["route"]["intent"], "exhaustive")

	def test_reading_an_unreadable_project_is_refused(self):
		# Guest stands in for any user without Wikify Project read: the scope guard throws
		# and the ACL list resolves empty instead of blowing up on get_list.
		frappe.set_user("Guest")
		self.addCleanup(frappe.set_user, "Administrator")
		with self.assertRaises(frappe.PermissionError):
			api_rag.search("anything", project=self.project.name)
		with self.assertRaises(frappe.PermissionError):
			api_rag.reindex(self.project.name)
		self.assertEqual(api_rag.readable_projects(), [])

	def test_compare_runs_both_legs_over_the_same_acl(self):
		naive_hits = [make_hit("Only One")]
		routed_hits = [make_hit("Only One"), make_hit("And Another")]
		decided = rag_router.Route("exhaustive", "job_description", "all job descriptions", "r")
		with (
			patch.object(rag_search, "search", return_value=naive_hits) as search,
			patch.object(rag_answer, "route", return_value=decided),
			patch.object(rag_answer, "retrieve", return_value=routed_hits),
		):
			result = api_rag.compare("all the job descriptions")
		self.assertEqual(search.call_args.kwargs["mode"], "vector")
		self.assertEqual(len(result["naive"]), 1)
		self.assertEqual(len(result["routed"]), 2)
		self.assertEqual(result["route"]["intent"], "exhaustive")

	def test_reindex_enqueues_on_the_long_queue(self):
		with patch.object(frappe, "enqueue", return_value=SimpleNamespace(id="job-1")) as enqueue:
			result = api_rag.reindex(self.project.name)
		self.assertEqual(result["job"], "job-1")
		self.assertEqual(enqueue.call_args.kwargs["queue"], "long")
		self.assertEqual(enqueue.call_args.args[0], "wikify.rag.index.rebuild_project")

	def test_index_status_scans_the_readable_projects_once(self):
		"""One scan for the whole readable scope — not one per project."""
		stats = {"chunks": 6, "sections": 4, "documents": 2, "indexed_at": "2026-01-01 00:00:00", "dim": 256}
		with (
			patch.object(api_rag, "readable_projects", return_value=["a", "b"]),
			patch.object(api_rag.rag_index, "index_stats", return_value=stats) as index_stats,
			patch.object(api_rag, "is_stale", return_value=False),
		):
			result = api_rag.index_status()
		index_stats.assert_called_once_with(["a", "b"])
		self.assertEqual(result["chunks"], 6)
		self.assertEqual(result["sections"], 4)
		self.assertFalse(result["stale"])


class TestIndexStatsAcl(FrappeTestCase):
	"""`index_stats` reads the chunk table, so it takes the same ACL decision `search` does.

	It defaulted to `projects=None` — the permissive default the AclDecision sentinels were
	introduced to make impossible — and reported chunk, section and document counts for
	every project on the site to any caller that forgot the argument.
	"""

	def test_an_omitted_scope_throws_instead_of_counting_the_whole_site(self):
		with self.assertRaises(frappe.ValidationError):
			rag_index.index_stats()

	def test_none_is_not_an_opt_out(self):
		with self.assertRaises(frappe.ValidationError):
			rag_index.index_stats(None)

	def test_all_projects_is_the_deliberate_opt_out(self):
		totals = rag_index.index_stats(rag_search.ALL_PROJECTS)
		self.assertIn("chunks", totals)

	def test_an_empty_acl_counts_nothing(self):
		self.assertEqual(rag_index.index_stats([])["chunks"], 0)


class TestExploreAcl(FrappeTestCase):
	"""Explore is a metadata read over the same corpus, so it carries the same ACL."""

	def setUp(self):
		self.project = make_project("Explore ACL")
		self.document = frappe.get_doc(
			{
				"doctype": "Source Document",
				"title": "Explore ACL Doc",
				"page_count": 1,
				"project": self.project.name,
			}
		).insert(ignore_permissions=True)
		self.addCleanup(frappe.set_user, "Administrator")

	def test_an_explicit_unreadable_project_is_refused(self):
		frappe.set_user("Guest")
		with self.assertRaises(frappe.PermissionError):
			api_explore.type_summary(project=self.project.name)
		with self.assertRaises(frappe.PermissionError):
			api_explore.sections_by_type("job_description", project=self.project.name)

	def test_an_unscoped_read_excludes_documents_the_user_cannot_see(self):
		frappe.set_user("Guest")
		hidden = permission.hidden_documents()
		self.assertIn(self.document.name, hidden)

	def test_a_project_less_document_stays_visible(self):
		"""The fix subtracts unreadable projects; it must not hide content with no project.

		A document outside every project carries no permission statement, and scoping to
		documents-in-readable-projects would have hidden it as a side effect.
		"""
		orphan = frappe.get_doc(
			{"doctype": "Source Document", "title": "No Project Doc", "page_count": 1}
		).insert(ignore_permissions=True)
		frappe.set_user("Guest")
		self.assertNotIn(orphan.name, permission.hidden_documents())


class TestAgentProjectScope(FrappeTestCase):
	"""The model chooses `project`, so resolving it must apply permissions."""

	def setUp(self):
		self.project = make_project("Agent Scope")
		self.addCleanup(frappe.set_user, "Administrator")

	def test_a_readable_project_still_resolves_by_title(self):
		ctx = agent_context.Ctx(session="S", user="Administrator")
		self.assertEqual(ctx.default_project(self.project.project_name), self.project.name)

	def test_an_unreadable_project_does_not_resolve_by_title(self):
		"""Otherwise a prompt widens the agent from its attached project to any on the site."""
		frappe.set_user("Guest")
		ctx = agent_context.Ctx(session="S", user="Guest", project=None)
		self.assertIsNone(ctx.default_project(self.project.project_name))
		self.assertIsNone(ctx.default_project(self.project.name))


class TestRagApiAgainstTheRealIndex(FrappeTestCase):
	"""One end-to-end pass over the real LanceDB store — everything else here stubs it."""

	def setUp(self):
		from wikify.rag import index as rag_index

		self.project = make_project("RAG Integration")
		self.document = frappe.get_doc(
			{"doctype": "Source Document", "title": "Integration Handbook", "project": self.project.name}
		).insert()
		self.section = frappe.get_doc(
			{
				"doctype": "Source Section",
				"source_document": self.document.name,
				"title": "Zarquon Protocol",
				"hierarchy_path": "Protocols > Zarquon Protocol",
				"page_start": 1,
				"page_end": 1,
				"markdown": "The Zarquon protocol requires two witnesses and a countersigned waiver.",
			}
		).insert()
		rag_index.upsert_section(self.section.name)
		self.addCleanup(rag_index.drop_section, self.section.name)

	def test_search_finds_an_indexed_section_and_answer_cites_it(self):
		result = api_rag.search("zarquon protocol witnesses", project=self.project.name, use_router=False)
		self.assertEqual(result["hits"][0]["section"], self.section.name)
		self.assertEqual(result["hits"][0]["document_title"], "Integration Handbook")

		with (
			patch.object(settings, "openrouter_key", return_value="key"),
			patch.object(rag_answer, "generate", return_value="Two witnesses are required [1]."),
			patch.object(
				rag_router, "route", return_value=rag_router.Route("semantic", None, "zarquon protocol", "r")
			),
		):
			answered = rag_answer.answer(
				"what does the zarquon protocol require",
				project=self.project.name,
				rerank=False,
				allowed_projects=[self.project.name],
			)
		self.assertFalse(answered["refused"])
		self.assertEqual(answered["citations"][0]["section"], self.section.name)

	def test_a_project_outside_the_acl_returns_nothing(self):
		result = api_rag.search("zarquon protocol witnesses", project=self.project.name, use_router=False)
		self.assertTrue(result["hits"])
		with patch.object(api_rag, "readable_projects", return_value=["some-other-project"]):
			blocked = api_rag.search("zarquon protocol witnesses", use_router=False)
		self.assertEqual(blocked["hits"], [])


class TestRagAgentTool(FrappeTestCase):
	def test_semantic_search_is_registered(self):
		tool = build_default_registry()["semantic_search"]
		self.assertEqual(tool.side, "server")
		self.assertFalse(tool.confirm)
		self.assertFalse(tool.mutates)

	def test_tool_reuses_the_api_and_skips_the_router(self):
		from wikify.agent.context import Ctx
		from wikify.agent.tools import retrieve

		payload = {"hits": [make_hit(rerank_score=9.0).as_dict()], "mode": "hybrid"}
		with patch.object(api_rag, "search", return_value=payload) as search:
			output = retrieve.semantic_search(Ctx(session="s", user="Administrator"), {"query": "roles"})
		self.assertFalse(search.call_args.kwargs["use_router"])
		self.assertIn("<sec-Backend Engineer>", output)


class TestReindexHook(FrappeTestCase):
	def setUp(self):
		self.project = make_project("RAG Hook Test")
		self.document = frappe.get_doc(
			{"doctype": "Source Document", "title": "Hooked", "project": self.project.name}
		).insert()
		self.section = frappe.get_doc(
			{"doctype": "Source Section", "source_document": self.document.name, "title": "A section"}
		).insert()
		frappe.cache().delete_value(rag_events.pending_key(self.project.name))

	def test_changes_coalesce_into_one_queued_rebuild(self):
		with (
			patch.object(rag_events, "indexing_suspended", return_value=False),
			patch.object(frappe, "enqueue") as enqueue,
		):
			rag_events.queue_reindex(self.section)
			rag_events.queue_reindex(self.section)
			rag_events.queue_reindex(self.section)
		self.assertEqual(enqueue.call_count, 1)
		self.assertEqual(enqueue.call_args.kwargs["queue"], "long")
		self.assertEqual(enqueue.call_args.kwargs["project"], self.project.name)
		self.assertTrue(enqueue.call_args.kwargs["enqueue_after_commit"])

	def test_the_next_change_requeues_once_the_job_has_started(self):
		with (
			patch.object(rag_events, "indexing_suspended", return_value=False),
			patch.object(frappe, "enqueue") as enqueue,
		):
			rag_events.queue_reindex(self.section)
			with patch("wikify.rag.index.rebuild_project"):
				rag_events.rebuild_pending_project(self.project.name)
			rag_events.queue_reindex(self.section)
		self.assertEqual(enqueue.call_count, 2)

	def test_bulk_pipeline_runs_do_not_enqueue(self):
		with patch.object(frappe, "enqueue") as enqueue:
			rag_events.queue_reindex(self.section)
		enqueue.assert_not_called()


def llm_reply_with_usage(content: str, cost: float, prompt: int, completion: int) -> dict:
	"""An `engine.llm.chat_completion` body that also carries OpenRouter's usage block."""
	return {
		"choices": [{"message": {"content": content}}],
		"usage": {"cost": cost, "prompt_tokens": prompt, "completion_tokens": completion},
	}


def streamed_chunks(pieces: list[str], usage: dict | None):
	"""A litellm stream: content chunks, then the choice-less usage chunk `include_usage` adds."""
	for piece in pieces:
		yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=piece))], usage=None)
	yield SimpleNamespace(choices=[], usage=SimpleNamespace(**usage) if usage else None)


class TestAskCost(FrappeTestCase):
	"""What a question costs is the sum of every call it made, not just the synthesis."""

	def test_answer_totals_the_routing_and_synthesis_calls(self):
		route_reply = llm_reply_with_usage(
			'{"intent": "semantic", "section_type": null, "query": "which roles", "reason": "r"}',
			cost=0.001,
			prompt=100,
			completion=10,
		)
		stream = streamed_chunks(
			["Two roles [1]."], {"cost": 0.02, "prompt_tokens": 900, "completion_tokens": 300}
		)
		with (
			patch.object(settings, "openrouter_key", return_value="key"),
			patch("wikify.engine.llm.chat_completion", return_value=route_reply),
			patch("wikify.agent.llm.complete_with_tools", return_value=stream),
			patch.object(rag_search, "search", return_value=[make_hit(rerank_score=9.0)]),
		):
			result = rag_answer.answer(
				"which roles are described", allowed_projects=["PRJ"], on_delta=lambda delta: None
			)

		self.assertAlmostEqual(result["cost"], 0.021)
		self.assertEqual(result["prompt_tokens"], 1000)
		self.assertEqual(result["completion_tokens"], 310)
		self.assertTrue(result["model"])

	def test_a_refusal_still_reports_what_the_routing_call_cost(self):
		route_reply = llm_reply_with_usage(
			'{"intent": "semantic", "section_type": null, "query": "q", "reason": "r"}',
			cost=0.0004,
			prompt=80,
			completion=12,
		)
		with (
			patch.object(settings, "openrouter_key", return_value="key"),
			patch("wikify.engine.llm.chat_completion", return_value=route_reply),
			patch.object(rag_search, "search", return_value=[]),
		):
			result = rag_answer.answer("what is the capital of Mongolia", allowed_projects=["PRJ"])

		self.assertTrue(result["refused"])
		self.assertAlmostEqual(result["cost"], 0.0004)
		self.assertEqual(result["prompt_tokens"], 80)

	def test_the_rerank_costs_the_question_nothing(self):
		"""The reranker runs in-process, so a question is billed for routing and synthesis only.

		It used to be a metered LLM call billed to the turn; this pins that the swap actually
		removed that spend rather than merely moving where it was counted.
		"""
		with rag_usage.collect() as spend:
			rag_search.rerank_hits("which roles", [make_hit()])

		self.assertEqual(spend["cost"], 0.0)
		self.assertEqual(spend["prompt_tokens"], 0)
		self.assertEqual(spend["completion_tokens"], 0)

	def test_one_thread_is_never_billed_for_another(self):
		"""The web worker answers on threads, so the accumulator must not be shared."""
		totals = {}

		def bill(name: str, cost: float):
			with rag_usage.collect() as spend:
				rag_usage.add({"cost": cost, "prompt_tokens": 1, "completion_tokens": 1})
				time.sleep(0.05)
				totals[name] = spend["cost"]

		threads = [
			threading.Thread(target=bill, args=("first", 0.5)),
			threading.Thread(target=bill, args=("second", 0.25)),
		]
		for thread in threads:
			thread.start()
		for thread in threads:
			thread.join()

		self.assertEqual(totals, {"first": 0.5, "second": 0.25})

	def test_spend_outside_an_answer_is_not_accumulated(self):
		rag_usage.add({"cost": 1.0, "prompt_tokens": 5, "completion_tokens": 5})
		with rag_usage.collect() as spend:
			self.assertEqual(spend, rag_usage.empty())

	def test_ask_publishes_the_cost_when_the_answer_finishes(self):
		answered = {
			"answer": "a [1]",
			"citations": [],
			"route": {"intent": "semantic"},
			"refused": False,
			"model": "anthropic/claude-sonnet-4.6",
			"cost": 0.021,
			"prompt_tokens": 1000,
			"completion_tokens": 310,
		}
		# The cache is keyed only on inputs this test holds fixed, so a previous run's entry
		# would be replayed and this would assert the router's price instead of the answer's.
		with (
			patch.object(api_rag.answer_cache, "get", return_value=None),
			patch.object(api_rag.answer_cache, "set"),
			patch.object(rag_answer, "answer", return_value=dict(answered)),
			patch.object(frappe, "publish_realtime") as publish,
		):
			result = api_rag.ask("which roles are described")

		done = [call.args[1] for call in publish.call_args_list if call.args[1].get("done")]
		self.assertEqual(len(done), 1)
		self.assertAlmostEqual(done[0]["cost"], 0.021)
		self.assertEqual(done[0]["prompt_tokens"], 1000)
		self.assertEqual(done[0]["completion_tokens"], 310)
		self.assertEqual(done[0]["model"], "anthropic/claude-sonnet-4.6")
		self.assertAlmostEqual(result["cost"], 0.021)


class TestSilentRerankFailure(FrappeTestCase):
	"""The reranker must never be able to mute the product on its own.

	Graded against the real corpus: "what are the slab rates under section 115BAC(1A)" is
	answered at rank 2 by both the hybrid and the FTS leg, yet `ask` refused it because the
	rerank scored every candidate 0.0. Refusing on that alone is the worst thing this system
	does when it is wrong — a reader cannot tell "the reranker died" from "the document does
	not cover this" — so a below-floor rerank now needs the embedding leg to agree.
	"""

	QUESTION = "what are the slab rates under section 115BAC(1A)"

	def retrieved(self, vector_score: float | None = None) -> list[Hit]:
		return [
			make_hit("I. INCOME TAX RATES", vector_score=vector_score),
			make_hit("II. SURCHARGE", vector_score=vector_score),
		]

	def rerank(self, scores: list[float], vector_score: float | None = None) -> list[Hit]:
		with patch.object(rag_rerank, "scores", return_value=scores):
			return rag_search.rerank_hits(self.QUESTION, self.retrieved(vector_score))

	def answer_over_the_rerank_path(self, scores: list[float], vector_score: float | None) -> dict:
		"""End to end through the real rerank path, with only the store and the synthesis stubbed."""

		def retrieve_and_rerank(query, **kwargs):
			return rag_search.rerank_hits(query, self.retrieved(vector_score))

		with (
			patch.object(settings, "openrouter_key", return_value="key"),
			patch.object(rag_rerank, "scores", return_value=scores),
			patch.object(rag_search, "search", side_effect=retrieve_and_rerank),
			patch.object(rag_answer, "generate", return_value="The slabs are ... [1]"),
		):
			return rag_answer.answer(self.QUESTION, rerank=True, allowed_projects=["PRJ"])

	def test_an_all_zero_verdict_keeps_the_fusion_order(self):
		"""Zero for everything separates nothing, so the fusion order has to survive it."""
		hits = self.rerank([0.0, 0.0])
		self.assertEqual([hit.title for hit in hits], ["I. INCOME TAX RATES", "II. SURCHARGE"])

	def test_an_identical_non_zero_verdict_is_discarded(self):
		"""One repeated score is the model declining to judge — not a ranking, not a refusal."""
		hits = self.rerank([5.0, 5.0])
		self.assertEqual([hit.rerank_score for hit in hits], [None, None])

	def test_a_real_ranking_still_reorders(self):
		hits = self.rerank([2.0, 9.0])
		self.assertEqual([hit.title for hit in hits], ["II. SURCHARGE", "I. INCOME TAX RATES"])

	def test_the_answer_is_not_refused_when_the_reranker_zeroes_a_hit_the_corpus_answers(self):
		"""The 115BAC casualty: an all-zero rerank over a section the embedding leg matched
		strongly must fall back to the fusion order and answer, never refuse."""
		result = self.answer_over_the_rerank_path([0.0, 0.0], vector_score=0.61)

		self.assertFalse(result["refused"])
		self.assertEqual(len(result["citations"]), 2)

	def test_an_overruled_rerank_reports_no_score_rather_than_zero(self):
		"""The scores are known-bad once the embedding leg overrules them, and `null` is what
		the source card hides on — a "rerank 0.0" chip reads as a dead reranker."""
		result = self.answer_over_the_rerank_path([0.0, 0.0], vector_score=0.61)

		self.assertTrue(all(citation["rerank_score"] is None for citation in result["citations"]))

	def test_a_genuinely_uncovered_question_is_still_refused(self):
		"""Both legs agree there is nothing here, so the honest answer is still to refuse."""
		result = self.answer_over_the_rerank_path([0.0, 0.0], vector_score=0.39)

		self.assertTrue(result["refused"])
		self.assertEqual(result["citations"], [])

	def test_a_working_reranker_can_still_refuse(self):
		weak = [
			make_hit(rerank_score=1.0, vector_score=0.31),
			make_hit("Other", rerank_score=0.5, vector_score=0.30),
		]
		with patch.object(rag_search, "search", return_value=weak):
			result = rag_answer.answer("what is the capital of Mongolia", allowed_projects=["PRJ"])

		self.assertTrue(result["refused"])
