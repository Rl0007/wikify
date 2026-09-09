import re
import shutil
import tempfile
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from wikify.engine import store as engine_store
from wikify.engine.loader.sectionizer import Section
from wikify.rag import chunk, index, rerank, search, store


def scores_favouring(wanted: str):
	def score(query, texts):
		return [9.0 if text == wanted else 0.0 for text in texts]

	return score


def make_bare_hit(title: str, score: float = 0.03) -> search.Hit:
	return search.Hit(
		chunk_id=f"{title}::0",
		section=f"sec-{title}",
		source_document="doc-1",
		document_title="Handbook",
		title=title,
		text=f"{title} body text.",
		section_type=None,
		hierarchy_path=title,
		page_start=1,
		page_end=1,
		wiki_route=None,
		wikify_import=None,
		score=score,
	)


RECIPE_SECTIONS = [
	("Coin recipe", "job_description", "Touch the coin and the score goes up by one point."),
	("Timer recipe", "job_description", "A countdown variable starts at thirty and drops each second."),
	("Sprite notes", "compensation", "Costumes and backdrops are swapped from the sprite pane."),
]

GOLD_ROLE_SECTIONS = [
	("Backend Engineer", "Owns the ingestion service, its queues and its database migrations."),
	("Frontend Engineer", "Builds the customer dashboard and keeps its accessibility audit green."),
	("Payroll Administrator", "Runs the monthly payroll cycle and reconciles the statutory filings."),
	("Falconry Handler", "Feeds the birds, cleans the mews and flies the peregrine at first light."),
	("Kiln Operator", "Stacks greenware, watches the cone packs and holds the soak at temperature."),
]

DISTRACTOR_TEMPLATE = (
	"Job description formatting note {number}. Every job description in this handbook uses the "
	"same job description template, and each description is reviewed before the job is posted."
)


def make_section(title, section_type, markdown, page=1):
	return Section(
		title=title,
		level=1,
		hierarchy_path=[title],
		page_start=page,
		page_end=page,
		markdown=markdown,
		section_type=section_type,
	)


def ensure_section_type(type_name):
	if not frappe.db.exists("Section Type", type_name):
		frappe.get_doc({"doctype": "Section Type", "type_name": type_name, "label": type_name}).insert(
			ignore_permissions=True
		)


class TestRagCore(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.lance_dir = tempfile.mkdtemp(prefix="wikify_lance_test_")
		cls.lance_patch = patch.object(store, "lance_path", lambda: cls.lance_dir)
		cls.lance_patch.start()

	@classmethod
	def tearDownClass(cls):
		cls.lance_patch.stop()
		shutil.rmtree(cls.lance_dir, ignore_errors=True)
		super().tearDownClass()

	def setUp(self):
		database = store.connect()
		if store.TABLE_NAME in database.table_names():
			database.drop_table(store.TABLE_NAME)

		for type_name in ("job_description", "compensation"):
			ensure_section_type(type_name)

		suffix = frappe.generate_hash(length=8)
		self.project = frappe.get_doc(
			{"doctype": "Wikify Project", "project_name": f"RAG Core Test {suffix}"}
		).insert(ignore_permissions=True)
		self.other_project = frappe.get_doc(
			{"doctype": "Wikify Project", "project_name": f"RAG Core Other {suffix}"}
		).insert(ignore_permissions=True)

		self.document = frappe.get_doc(
			{
				"doctype": "Source Document",
				"title": "Scratch Coins Worksheet",
				"project": self.project.name,
			}
		).insert(ignore_permissions=True)
		engine_store.replace_sections(
			self.document.name,
			[
				make_section(title, section_type, markdown)
				for title, section_type, markdown in RECIPE_SECTIONS
			],
		)

		self.other_document = frappe.get_doc(
			{
				"doctype": "Source Document",
				"title": "Other Project Handbook",
				"project": self.other_project.name,
			}
		).insert(ignore_permissions=True)
		engine_store.replace_sections(
			self.other_document.name,
			[make_section("Coin recipe elsewhere", "job_description", "The coin scores a point here too.")],
		)

		index.rebuild_project(self.project.name)
		index.rebuild_project(self.other_project.name)

	def section_named(self, title):
		return frappe.db.get_value(
			"Source Section", {"source_document": self.document.name, "title": title}, "name"
		)

	def add_document(self, title, project, sections):
		document = frappe.get_doc({"doctype": "Source Document", "title": title, "project": project}).insert(
			ignore_permissions=True
		)
		engine_store.replace_sections(
			document.name,
			[
				make_section(section_title, section_type, markdown)
				for section_title, section_type, markdown in sections
			],
		)
		index.rebuild_project(project)
		return document

	def test_embed_text_carries_context_prefix_and_text_stays_clean(self):
		chunks = chunk.chunks_for_section(self.section_named("Coin recipe"))
		self.assertEqual(len(chunks), 1)
		built = chunks[0]
		self.assertEqual(built.id, f"{built.section}::0")
		self.assertTrue(
			built.embed_text.startswith(f"Scratch Coins Worksheet{chunk.CONTEXT_SEPARATOR}Coin recipe\n\n"),
			built.embed_text,
		)
		self.assertEqual(built.text, RECIPE_SECTIONS[0][2])
		self.assertNotIn(chunk.CONTEXT_SEPARATOR, built.text)
		self.assertEqual(built.project, self.project.name)
		self.assertEqual(built.section_type, "job_description")

	def test_oversized_section_splits_on_boundaries_with_overlap_and_whole_words(self):
		paragraph = "the quick brown fox jumps over the lazy dog " * 40
		markdown = f"## Alpha\n\n{paragraph}\n\n## Beta\n\n{paragraph}"
		pieces = chunk.split_markdown(markdown)

		self.assertGreater(len(pieces), 1)
		words = set(markdown.split())
		budget = chunk.CHUNK_TARGET_CHARS + chunk.CHUNK_OVERLAP_CHARS + len("## Alpha") + 4
		for piece in pieces:
			self.assertLessEqual(len(piece), budget)
			self.assertTrue(set(piece.split()) <= words, piece[:80])
			blocks = [block for block in piece.split("\n\n") if block.strip()]
			self.assertFalse(all(chunk.is_heading(block) for block in blocks), piece)
			self.assertFalse(chunk.is_heading(blocks[-1]), piece)
		for position in range(1, len(pieces)):
			overlap = pieces[position].split("\n\n")[0]
			self.assertIn(overlap, pieces[position - 1])
		self.assertCoversMarkdown(markdown, pieces)

	def assertCoversMarkdown(self, markdown, pieces):
		joined = "\n".join(pieces)
		for line in markdown.splitlines():
			if chunk.is_heading(line):
				self.assertIn(line.strip(), joined, f"heading dropped: {line!r}")
		missing = set(markdown.split()) - set(joined.split())
		self.assertFalse(missing, f"tokens dropped: {sorted(missing)[:5]}")

	def test_heading_survives_a_body_that_overflows_the_first_piece(self):
		markdown = "# Job Description: Backend Engineer\n\n" + ("service ownership rota " * 250)
		pieces = chunk.split_markdown(markdown)

		self.assertGreater(len(pieces), 1)
		self.assertCoversMarkdown(markdown, pieces)
		self.assertIn("# Job Description: Backend Engineer", pieces[0])

	def test_a_heading_stack_is_carried_not_dropped(self):
		stack = "# Handbook\n\n## People\n\n### Backend Engineer"
		markdown = f"{('preamble words ' * 120).strip()}\n\n{stack}\n\n" + ("duty roster line " * 200)
		pieces = chunk.split_markdown(markdown)

		self.assertCoversMarkdown(markdown, pieces)
		with_stack = [piece for piece in pieces if "### Backend Engineer" in piece]
		self.assertEqual(len(with_stack), 1)
		self.assertIn("# Handbook", with_stack[0])
		self.assertIn("## People", with_stack[0])

	def test_a_body_of_nothing_but_headings_keeps_every_heading(self):
		markdown = "\n\n".join(f"## Chapter {number}" for number in range(200))
		pieces = chunk.split_markdown(markdown)

		self.assertCoversMarkdown(markdown, pieces)
		self.assertGreater(len(pieces), 1)

	def test_a_run_with_no_whitespace_is_cut_on_character_count(self):
		markdown = "x" * 50000
		pieces = chunk.split_markdown(markdown)

		self.assertGreater(len(pieces), 1)
		budget = chunk.CHUNK_TARGET_CHARS + chunk.CHUNK_OVERLAP_CHARS + 4
		for piece in pieces:
			self.assertLessEqual(len(piece), budget)
		self.assertGreaterEqual(sum(piece.count("x") for piece in pieces), 50000)

	def test_short_section_is_one_chunk(self):
		self.assertEqual(chunk.split_markdown("a short body"), ["a short body"])
		self.assertEqual(chunk.split_markdown("   "), [])

	def test_a_section_with_no_body_is_returned_by_filter_but_never_ranked(self):
		document = self.add_document(
			"Empty Bodies",
			self.project.name,
			[
				("Concessional tax rates under section 115BAC", "job_description", ""),
				("Rates of tax", "job_description", "The concessional rate under section 115BAC is 10%."),
			],
		)
		section = frappe.db.get_value(
			"Source Section",
			{"source_document": document.name, "title": "Concessional tax rates under section 115BAC"},
			"name",
		)

		chunks = chunk.chunks_for_section(section)
		self.assertEqual(len(chunks), 1)
		self.assertEqual(chunks[0].text, "Concessional tax rates under section 115BAC")
		self.assertTrue(chunks[0].title_only)

		query = "concessional tax rates under section 115BAC"
		filtered = search.search(
			query,
			project=self.project.name,
			section_type="job_description",
			mode="filter",
			allowed_projects=search.ALL_PROJECTS,
		)
		self.assertIn("Concessional tax rates under section 115BAC", {hit.title for hit in filtered})

		for mode in ("vector", "fts", "hybrid"):
			titles = {
				hit.title
				for hit in search.search(
					query, project=self.project.name, mode=mode, allowed_projects=search.ALL_PROJECTS
				)
			}
			self.assertNotIn("Concessional tax rates under section 115BAC", titles, mode)
			self.assertIn("Rates of tax", titles, mode)

	def test_wiki_route_is_populated_from_the_linked_wiki_document(self):
		page = frappe.get_doc(
			{"doctype": "Wiki Document", "title": "Coin recipe", "route": "handbook/coin-recipe"}
		).insert(ignore_permissions=True)
		section = self.section_named("Coin recipe")
		frappe.db.set_value("Source Section", section, "wiki_document", page.name)

		self.assertEqual(chunk.chunks_for_section(section)[0].wiki_route, "handbook/coin-recipe")
		self.assertIsNone(chunk.chunks_for_section(self.section_named("Timer recipe"))[0].wiki_route)

	def test_rebuild_is_idempotent_and_drops_orphans(self):
		first = index.rebuild_project(self.project.name)
		second = index.rebuild_project(self.project.name)
		self.assertEqual(first["chunks"], second["chunks"])
		self.assertEqual(first["sections"], 3)

		stats = index.index_stats([self.project.name])
		self.assertEqual(stats["chunks"], first["chunks"])
		self.assertEqual(stats["sections"], 3)
		self.assertEqual(stats["documents"], 1)
		self.assertEqual(stats["dim"], 256)
		self.assertTrue(stats["indexed_at"])

		frappe.db.delete("Source Section", {"name": self.section_named("Sprite notes")})
		index.rebuild_project(self.project.name)
		self.assertEqual(index.index_stats([self.project.name])["sections"], 2)

	def test_upsert_and_drop_section(self):
		section = self.section_named("Timer recipe")
		engine_store.set_section_markdown(section, "A stopwatch counts down from sixty seconds.")
		self.assertEqual(index.upsert_section(section), 1)

		hits = search.search(
			"stopwatch counting down",
			project=self.project.name,
			mode="vector",
			allowed_projects=search.ALL_PROJECTS,
		)
		self.assertIn(section, [hit.section for hit in hits])

		index.drop_section(section)
		self.assertEqual(index.index_stats([self.project.name])["sections"], 2)

	def test_upsert_removes_the_chunks_a_shrinking_section_leaves_behind(self):
		section = self.section_named("Timer recipe")
		engine_store.set_section_markdown(section, "countdown timer variable " * 400)
		self.assertGreater(index.upsert_section(section), 1)

		engine_store.set_section_markdown(section, "A stopwatch counts down from sixty seconds.")
		self.assertEqual(index.upsert_section(section), 1)

		table = store.chunks_table()
		remaining = (
			table.search()
			.select(["id", "section"])
			.where(f"section = {store.sql_literal(section)}")
			.limit(None)
			.to_list()
		)
		self.assertEqual(len(remaining), 1)

	def test_a_failed_rebuild_requeues_itself_instead_of_leaving_zero_rows(self):
		from wikify.rag import events

		frappe.cache().delete_value(events.pending_key(self.project.name))
		table = store.chunks_table(create=True)

		with (
			patch.object(type(table), "add", side_effect=RuntimeError("worker died")),
			patch.object(store, "chunks_table", lambda create=False: table),
			patch("frappe.enqueue") as enqueue,
			self.assertRaises(RuntimeError),
		):
			index.rebuild_project(self.project.name)

		self.assertTrue(frappe.cache().get_value(events.pending_key(self.project.name)))
		self.assertEqual(enqueue.call_args.args[0], "wikify.rag.events.rebuild_pending_project")
		self.assertEqual(enqueue.call_args.kwargs["project"], self.project.name)
		frappe.cache().delete_value(events.pending_key(self.project.name))

	def test_filter_mode_is_exhaustive_and_ignores_limit(self):
		hits = search.search(
			"",
			project=self.project.name,
			section_type="job_description",
			mode="filter",
			limit=1,
			allowed_projects=search.ALL_PROJECTS,
		)
		self.assertEqual({hit.title for hit in hits}, {"Coin recipe", "Timer recipe"})

	def test_naive_vector_under_recalls_the_gold_set_that_filter_mode_returns_whole(self):
		question = "give me all the job descriptions"
		roles = self.add_document(
			"Roles Handbook",
			self.project.name,
			[(title, "job_description", markdown) for title, markdown in GOLD_ROLE_SECTIONS],
		)
		self.add_document(
			"Formatting Handbook",
			self.project.name,
			[
				(f"Formatting note {number}", "compensation", DISTRACTOR_TEMPLATE.format(number=number))
				for number in range(20)
			],
		)

		gold = {
			hit.section
			for hit in search.search(
				question,
				project=self.project.name,
				source_document=roles.name,
				section_type="job_description",
				mode="filter",
				allowed_projects=search.ALL_PROJECTS,
			)
		}
		self.assertEqual(len(gold), len(GOLD_ROLE_SECTIONS))

		naive = {
			hit.section
			for hit in search.search(
				question,
				project=self.project.name,
				mode="vector",
				limit=len(gold),
				allowed_projects=search.ALL_PROJECTS,
			)
		}
		self.assertEqual(len(naive), len(gold))
		missed = gold - naive
		self.assertTrue(missed, "naive top-k returned the whole gold set — fixture has no distractors")

		exhaustive = {
			hit.section
			for hit in search.search(
				question,
				project=self.project.name,
				section_type="job_description",
				mode="filter",
				limit=len(gold),
				allowed_projects=search.ALL_PROJECTS,
			)
		}
		self.assertTrue(gold <= exhaustive)

	def test_similarity_modes_return_hits_with_rank_provenance(self):
		query = "how does the score go up when touching the coin"
		scope = {"project": self.project.name, "limit": 3, "allowed_projects": search.ALL_PROJECTS}

		vector_hits = search.search(query, mode="vector", **scope)
		self.assertTrue(vector_hits)
		self.assertTrue(all(hit.vector_rank for hit in vector_hits))
		self.assertTrue(all(hit.fts_rank is None for hit in vector_hits))

		fts_hits = search.search(query, mode="fts", **scope)
		self.assertTrue(fts_hits)
		self.assertTrue(all(hit.fts_rank for hit in fts_hits))

		hybrid_hits = search.search(query, mode="hybrid", **scope)
		self.assertTrue(hybrid_hits)
		self.assertTrue(any(hit.vector_rank and hit.fts_rank for hit in hybrid_hits))
		self.assertEqual(hybrid_hits[0].title, "Coin recipe")

	def test_hits_carry_the_full_parent_section_not_the_chunk(self):
		long_body = "## Alpha\n\n" + ("the quick brown fox jumps over the lazy dog " * 60)
		section = self.section_named("Sprite notes")
		engine_store.set_section_markdown(section, long_body)
		index.upsert_section(section)

		hit = next(
			hit
			for hit in search.search(
				"quick brown fox",
				project=self.project.name,
				mode="vector",
				allowed_projects=search.ALL_PROJECTS,
			)
			if hit.section == section
		)
		self.assertEqual(hit.text, long_body)
		self.assertEqual(hit.document_title, "Scratch Coins Worksheet")
		self.assertEqual(hit.title, "Sprite notes")

	def test_scoping_and_acl_are_pre_filters(self):
		query = "the coin scores a point"
		crowd = search.CANDIDATE_FLOOR + 10
		self.add_document(
			"Foreign Coin Manual",
			self.other_project.name,
			[
				(f"Coin note {number}", "job_description", f"The coin scores a point, variation {number}.")
				for number in range(crowd)
			],
		)
		mine = set(
			frappe.get_all("Source Section", filters={"source_document": self.document.name}, pluck="name")
		)

		unscoped = search.search(query, mode="vector", limit=10, allowed_projects=search.ALL_PROJECTS)
		self.assertLess(len({hit.section for hit in unscoped} & mine), len(mine))

		scoped = search.search(
			query,
			project=self.project.name,
			mode="vector",
			limit=10,
			allowed_projects=search.ALL_PROJECTS,
		)
		self.assertEqual({hit.section for hit in scoped}, mine)

		by_document = search.search(
			query,
			source_document=self.other_document.name,
			mode="hybrid",
			limit=10,
			allowed_projects=search.ALL_PROJECTS,
		)
		self.assertEqual({hit.source_document for hit in by_document}, {self.other_document.name})

		by_acl = search.search(
			query, project=self.project.name, mode="vector", limit=10, allowed_projects=[self.project.name]
		)
		self.assertEqual({hit.section for hit in by_acl}, mine)

		blocked = search.search(
			query,
			project=self.project.name,
			mode="vector",
			limit=10,
			allowed_projects=[self.other_project.name],
		)
		self.assertEqual(blocked, [])

		self.assertEqual(search.search(query, mode="filter", allowed_projects=[]), [])

	def test_search_refuses_to_run_without_an_acl_decision(self):
		with self.assertRaises(frappe.ValidationError):
			search.search("the coin scores a point", project=self.project.name)

	def test_unknown_mode_throws(self):
		with self.assertRaises(frappe.ValidationError):
			search.search("anything", mode="magic", allowed_projects=search.ALL_PROJECTS)

	def test_missing_index_returns_no_hits(self):
		with patch.object(store, "chunks_table", lambda create=False: None):
			self.assertEqual(
				search.search("anything", mode="hybrid", allowed_projects=search.ALL_PROJECTS), []
			)

	def test_filter_mode_does_not_pay_for_a_rerank_it_discards(self):
		with patch.object(rerank, "scores") as scored:
			hits = search.search(
				"give me all the job descriptions",
				project=self.project.name,
				section_type="job_description",
				mode="filter",
				use_reranker=True,
				allowed_projects=search.ALL_PROJECTS,
			)

		self.assertTrue(hits)
		scored.assert_not_called()
		self.assertTrue(all(hit.rerank_score is None for hit in hits))

	def test_rerank_reorders_and_records_the_score(self):
		hits = search.search(
			"coin",
			project=self.project.name,
			mode="vector",
			limit=3,
			allowed_projects=search.ALL_PROJECTS,
		)
		self.assertGreater(len(hits), 1)
		wanted = hits[-1].section
		graded = [1.0] * len(hits)
		graded[-1] = 9.0

		with patch.object(rerank, "scores", return_value=graded):
			reranked = search.rerank_hits("coin", hits)

		self.assertEqual(reranked[0].section, wanted)
		self.assertEqual(reranked[0].rerank_score, 9.0)

	def test_rerank_runs_without_an_openrouter_key(self):
		scope = {
			"project": self.project.name,
			"mode": "vector",
			"limit": 3,
			"allowed_projects": search.ALL_PROJECTS,
		}
		with patch("wikify.engine.llm.has_openrouter", return_value=False):
			hits = search.search("coin", use_reranker=True, **scope)

		self.assertTrue(hits)
		self.assertTrue(all(hit.rerank_score is not None for hit in hits))

	def test_rerank_degrades_to_fusion_order_when_the_scorer_fails(self):
		scope = {
			"project": self.project.name,
			"mode": "vector",
			"limit": 3,
			"allowed_projects": search.ALL_PROJECTS,
		}
		order = [hit.section for hit in search.search("coin", **scope)]

		with patch.object(rerank, "scores", side_effect=RuntimeError("no model")):
			failed = search.search("coin", use_reranker=True, **scope)

		self.assertEqual([hit.section for hit in failed], order)
		self.assertTrue(all(hit.rerank_score is None for hit in failed))

	def test_search_returns_the_reranked_winner_not_the_fusion_winner(self):
		scope = {"project": self.project.name, "mode": "hybrid", "allowed_projects": search.ALL_PROJECTS}
		fusion_order = search.search("coin", limit=50, **scope)
		self.assertGreater(len(fusion_order), 1)
		outsider = fusion_order[-1]

		with patch.object(rerank, "scores", side_effect=scores_favouring(outsider.text or "")):
			hits = search.search("coin", limit=1, use_reranker=True, **scope)

		self.assertEqual([hit.section for hit in hits], [outsider.section])
		self.assertEqual(hits[0].rerank_score, 9.0)

	def test_every_candidate_is_scored(self):
		hits = [make_bare_hit(f"Section {position}") for position in range(25)]

		reranked = search.rerank_hits("coin", hits)

		self.assertEqual(len(reranked), 25)
		self.assertTrue(all(hit.rerank_score is not None for hit in reranked))

	def test_a_partial_verdict_is_dropped_whole(self):
		hits = [make_bare_hit(f"Section {position}") for position in range(3)]

		self.assertFalse(search.usable_verdict({0: 9.0}, len(hits)))

		with patch.object(rerank, "scores", return_value=[9.0]):
			reranked = search.rerank_hits("coin", hits)

		self.assertEqual([hit.title for hit in reranked], ["Section 0", "Section 1", "Section 2"])
		self.assertTrue(all(hit.rerank_score is None for hit in reranked))

	def test_the_similarity_leg_reports_an_absolute_score(self):
		scope = {"project": self.project.name, "allowed_projects": search.ALL_PROJECTS, "limit": 5}
		for mode in ("hybrid", "vector"):
			hits = search.search("coin", mode=mode, **scope)
			self.assertTrue(hits, mode)
			self.assertTrue(all(0.0 < hit.vector_score <= 1.0 for hit in hits), mode)

		keyword_only = search.search("coin", mode="fts", **scope)
		self.assertTrue(all(hit.vector_score is None for hit in keyword_only))
