"""Retrieval — four modes over one table, then expansion back to whole sections.

The thesis of POC-2 lives in `mode="filter"`: *"give me all the job descriptions"* is an
exhaustive, structured intent, so it is answered by a metadata filter that returns **every**
matching row — never a top-k similarity guess that silently drops the tail. The similarity
modes (`vector` / `fts` / `hybrid`) are reserved for fuzzy "about X" questions.

Two rules hold across all modes:

- Metadata filters (project / document / type / the ACL project list) are pushed into the
  LanceDB `.where()` clause so they pre-filter the scan. Filtering after top-k would
  re-introduce exactly the recall hole the filter mode exists to close.
- Hits are deduped up to their parent Source Section and carry the section's full markdown,
  so a reader (human or LLM) never sees a chunk boundary.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import frappe
from frappe.utils.data import cint, flt

from wikify.rag import chunk, embed, evidence, store, usage

MODES = ("vector", "fts", "hybrid", "filter")
FTS_COLUMN = "text"


class AclDecision:
	"""A named non-list value for `allowed_projects`, so tracebacks read as English."""

	def __init__(self, label: str):
		self.label = label

	def __repr__(self) -> str:
		return f"search.{self.label}"


# The ACL pre-filter is the one argument that enforces permissions, so it gets no usable
# default: omitting it raises instead of quietly reading every project. A caller that
# genuinely has no ACL to apply (a background rebuild, an eval run) says so by passing
# `allowed_projects=ALL_PROJECTS`.
#
# Both are distinct objects on purpose. `ALL_PROJECTS` used to be `None`, which collided
# with every "argument not supplied" default in the call chain — a caller that forgot to
# thread the user's projects silently got the unscoped opt-out instead of the guard. Any
# value that is neither a project list nor `ALL_PROJECTS` (including `None`) now throws.
ALL_PROJECTS = AclDecision("ALL_PROJECTS")
ACL_REQUIRED = AclDecision("ACL_REQUIRED")

# Over-fetch chunks so dedupe-to-section still leaves `limit` distinct sections.
CANDIDATE_MULTIPLIER = 5
CANDIDATE_FLOOR = 50
RERANK_CANDIDATES = 50
RERANK_SNIPPET_CHARS = 700
# Asked to grade a whole candidate list in one reply the classifier degenerates into a run
# of zeros. Measured on PRJ-2026-00002: "what is the surcharge rate when total income
# exceeds 2 crore" scored 0.0 across all 35 candidates in a single call, and found the
# answering section at 8.0 when the same 35 candidates were graded ten at a time.
RERANK_BATCH_SIZE = 10
# The batches are independent HTTP POSTs, so they run concurrently and the rerank costs one
# batch of latency rather than all of them. What may NOT cross into a pool thread is frappe:
# `frappe.local` is unbound there, so the OpenRouter key is resolved on the calling thread and
# each batch's usage is billed on it too (`usage` is thread-local by design — see `rag.usage`).
# The pool is as wide as there are batches, capped: a width below the batch count costs a whole
# extra wave, which is what a fixed 4 was doing to the 5 batches of a 50-candidate rerank.
# ponytail: cap measured against 5 batches at ~2.3s each; revisit if RERANK_CANDIDATES grows
# past a couple of hundred, where the cap starts serialising again and rate limits come in.
RERANK_MAX_WORKERS = 8
# OpenRouter's default routing is price-weighted, so parallel batches are independently sampled
# from a provider distribution and the wall clock is set by the slowest draw. Pinning them to
# one endpoint is what makes the pool worth having. Measured on PRJ-2026-00002, 50 candidates
# in 5 batches, 9 runs each: unpinned 7.8s median (1.9x concurrency, 24.7s worst case), pinned
# 2.6s (4.2x, 4.1s worst case) — and 6 of 18 unpinned runs came back as truncated JSON that
# lost the whole verdict, against 0 of 18 pinned. `allow_fallbacks` stays on: a reranker that
# cannot reach its preferred provider must degrade to a slower one, never fail.
RERANK_PROVIDER = {"order": ["google-ai-studio"], "allow_fallbacks": True}

RESULT_COLUMNS = [
	"id",
	"section",
	"source_document",
	"section_type",
	"hierarchy_path",
	"page_start",
	"page_end",
	"wiki_route",
	"text",
]
# The score column is requested explicitly: LanceDB only auto-projects it for now, and
# warns that a future release will drop it from an explicit `select()`.
VECTOR_COLUMNS = [*RESULT_COLUMNS, "_distance"]
FTS_COLUMNS = [*RESULT_COLUMNS, "_score"]


@dataclass
class Hit:
	chunk_id: str
	section: str
	source_document: str
	document_title: str
	title: str
	text: str
	section_type: str | None
	hierarchy_path: str
	page_start: int
	page_end: int
	wiki_route: str | None
	score: float
	vector_rank: int | None = None
	fts_rank: int | None = None
	rerank_score: float | None = None
	# The embedding leg's absolute 0-1 similarity, kept alongside the rank-based fusion
	# score because it is the one retrieval number that means the same thing across
	# queries — `answer.below_floor` reads it as a second opinion on the reranker.
	vector_score: float | None = None

	def as_dict(self) -> dict:
		return self.__dict__.copy()


def hit_value(hit, key: str):
	"""One field of a hit, whether it is still a `Hit` or already `as_dict()`ed.

	The two surfaces that label a hit sit either side of the whitelisted API — synthesis
	holds Hits, the agent tool holds the JSON it returned — and they must print the same
	label, so the labelling reads both shapes rather than being written twice.
	"""
	return hit.get(key) if isinstance(hit, dict) else getattr(hit, key, None)


def page_label(hit) -> str:
	"""A hit's page span as it is shown: `p.5`, or `p.5-7` when it runs across pages."""
	start, end = hit_value(hit, "page_start"), hit_value(hit, "page_end")
	return f"p.{start}-{end}" if end and end != start else f"p.{start}"


def crumb(hit) -> str:
	"""The breadcrumb: document title, then the section's ancestor path (or its own title)."""
	path = hit_value(hit, "hierarchy_path") or hit_value(hit, "title")
	return f"{hit_value(hit, 'document_title')}{chunk.CONTEXT_SEPARATOR}{path}"


def assert_acl_decision(allowed_projects) -> None:
	"""The single enforcement point: every retrieval must carry a deliberate ACL decision.

	Anything that is not a project list or the explicit `ALL_PROJECTS` opt-out — an omitted
	argument, a `None` threaded through from a caller's default — is a bug, never a licence
	to search the whole site.
	"""
	if allowed_projects is ALL_PROJECTS or isinstance(allowed_projects, list | tuple):
		return
	frappe.throw(
		"Retrieval needs an explicit `allowed_projects`: the list of projects this user may "
		f"read, or search.ALL_PROJECTS to search unscoped on purpose. Got {allowed_projects!r}."
	)


def build_where(
	project: str | None,
	source_document: str | None,
	section_type: str | None,
	allowed_projects: list[str] | AclDecision,
	include_title_only: bool = True,
) -> str | None:
	"""The `.where()` pre-filter clause, or None when nothing is scoped.

	`allowed_projects` is the ACL leg: an empty list means "this user may read nothing",
	which must scope to no rows rather than fall through to everything. Only the explicit
	`ALL_PROJECTS` opt-out drops the clause.

	`include_title_only=False` drops the breadcrumb sections that have no body of their own.
	They are indexed so exhaustive filter mode can return them, but they answer nothing and
	match on their title alone, so in a similarity leg they displace real content.
	"""
	clauses = []
	if not include_title_only:
		clauses.append("title_only = false")
	if project:
		clauses.append(f"project = {store.sql_literal(project)}")
	if source_document:
		clauses.append(f"source_document = {store.sql_literal(source_document)}")
	if section_type:
		clauses.append(f"section_type = {store.sql_literal(section_type)}")
	if allowed_projects is not ALL_PROJECTS:
		allowed = ", ".join(store.sql_literal(name) for name in allowed_projects)
		clauses.append(f"project IN ({allowed})" if allowed else "1 = 0")
	return " AND ".join(clauses) if clauses else None


def scoped(query, where: str | None):
	return query.where(where) if where else query


def fts_query(query: str) -> str:
	"""Strip punctuation the tantivy query parser treats as syntax (`:`, `?`, `-`, quotes)."""
	return re.sub(r"[^\w\s]", " ", query or "").strip()


def has_fts_index(table) -> bool:
	return any(
		table_index.index_type == "FTS" and FTS_COLUMN in (table_index.columns or [])
		for table_index in table.list_indices()
	)


def run_vector(
	table, query: str, where: str | None, limit: int, vector: list[float] | None = None
) -> list[dict]:
	"""`vector` lets a caller that already embedded `query` reuse it — embedding is the
	single most expensive step in a search, and hybrid mode needs the same vector twice."""
	return (
		scoped(
			table.search(vector if vector is not None else embed.embed_one(query)).select(VECTOR_COLUMNS),
			where,
		)
		.limit(limit)
		.to_list()
	)


def run_fts(table, query: str, where: str | None, limit: int) -> list[dict]:
	text = fts_query(query)
	if not text or not has_fts_index(table):
		return []
	return scoped(table.search(text, query_type="fts").select(FTS_COLUMNS), where).limit(limit).to_list()


def run_hybrid(
	table, query: str, where: str | None, limit: int, vector: list[float] | None = None
) -> list[dict]:
	"""LanceDB's native hybrid search — vector + FTS legs fused with reciprocal rank fusion.

	Falls back to the vector leg when there is no FTS index yet (a project indexed by an
	older build, or an empty table); a missing keyword leg must degrade, not raise.
	"""
	text = fts_query(query)
	if vector is None:
		vector = embed.embed_one(query)
	if not text or not has_fts_index(table):
		return run_vector(table, query, where, limit, vector=vector)
	query_builder = table.search(query_type="hybrid").vector(vector).text(text).select(RESULT_COLUMNS)
	return scoped(query_builder, where).limit(limit).to_list()


def run_filter(table, where: str | None) -> list[dict]:
	"""Exhaustive scan — every row matching the metadata filter, with no top-k truncation."""
	return scoped(table.search().select(RESULT_COLUMNS), where).limit(None).to_list()


def row_score(row: dict) -> float:
	"""One comparable score per mode: RRF relevance, BM25 score, or inverted L2 distance."""
	if row.get("_relevance_score") is not None:
		return float(row["_relevance_score"])
	if row.get("_score") is not None:
		return float(row["_score"])
	if row.get("_distance") is not None:
		return 1.0 / (1.0 + float(row["_distance"]))
	return 1.0


def rank_by_chunk(rows: list[dict]) -> dict[str, int]:
	"""chunk id → 1-based rank, so the UI can show *why* a section ranked."""
	return {row["id"]: position for position, row in enumerate(rows, start=1)}


def similarity_by_chunk(rows: list[dict]) -> dict[str, float]:
	"""chunk id → embedding similarity, read off the vector rows the ranking pass already
	scanned. Free: no extra query, the rows are in hand."""
	return {row["id"]: row_score(row) for row in rows}


def expand_to_sections(
	rows: list[dict],
	vector_ranks: dict[str, int],
	fts_ranks: dict[str, int],
	vector_scores: dict[str, float],
) -> list[Hit]:
	"""Dedupe chunk hits to their parent section and attach the full section markdown."""
	best: dict[str, dict] = {}
	matched_chunks: dict[str, list[str]] = {}
	for row in rows:
		score = row_score(row)
		current = best.get(row["section"])
		if current is None or score > current["score"]:
			best[row["section"]] = {"row": row, "score": score}
		matched_chunks.setdefault(row["section"], []).append(row["id"])

	sections = {
		section["name"]: section
		for section in evidence.get_rows_by_name(
			"Source Section",
			list(best),
			["name", "title", "markdown", "hierarchy_path", "section_type", "source_document"],
		)
	}
	documents = {
		document["name"]: document
		for document in evidence.get_rows_by_name(
			"Source Document",
			[entry["row"]["source_document"] for entry in best.values()],
			["name", "title"],
		)
	}

	hits: list[Hit] = []
	for section_name, entry in best.items():
		row = entry["row"]
		section = sections.get(section_name)
		if section is None:
			# The section was deleted since indexing — skip rather than show a dead citation.
			continue
		ranks = [
			(vector_ranks.get(chunk_id), fts_ranks.get(chunk_id)) for chunk_id in matched_chunks[section_name]
		]
		vector_hits = [rank for rank, _ in ranks if rank]
		fts_hits = [rank for _, rank in ranks if rank]
		similarities = [
			vector_scores[chunk_id]
			for chunk_id in matched_chunks[section_name]
			if vector_scores.get(chunk_id) is not None
		]
		hits.append(
			Hit(
				chunk_id=row["id"],
				section=section_name,
				source_document=row["source_document"],
				document_title=(documents.get(row["source_document"]) or {}).get("title")
				or row["source_document"],
				title=section.get("title") or section_name,
				text=section.get("markdown") or row.get("text") or "",
				section_type=section.get("section_type") or None,
				hierarchy_path=section.get("hierarchy_path") or row.get("hierarchy_path") or "",
				page_start=row.get("page_start") or 0,
				page_end=row.get("page_end") or 0,
				wiki_route=row.get("wiki_route") or None,
				score=round(entry["score"], 6),
				vector_rank=min(vector_hits) if vector_hits else None,
				fts_rank=min(fts_hits) if fts_hits else None,
				vector_score=round(max(similarities), 6) if similarities else None,
			)
		)
	return hits


RERANK_SYSTEM_PROMPT = (
	"You rank retrieved document sections by how well each one answers the "
	'user question. Reply with JSON: {"scores": [{"id": <candidate number>, '
	'"score": <0-10>}]} covering every candidate. No prose.'
)


def score_batch(
	query: str,
	hits: list[Hit],
	offset: int,
	model: str,
	api_key: str = "",
	provider: dict | None = None,
) -> tuple[dict[int, float], dict | None]:
	"""Score one batch of candidates 0-10, keyed by each one's position in the FULL list.

	A reply that skips candidates is dropped whole: the missing ones would otherwise read as
	an unscored 0 and outrank nothing, which is the same silent zero this batching exists to
	prevent.

	Returns the scores and the completion's usage payload rather than folding the usage in
	here: this runs on a pool thread and `usage` is thread-local, so only the caller can bill
	it. A dropped batch still reports its usage — the call was made and it was paid for.
	"""
	from wikify.engine import llm

	candidates = "\n\n".join(
		f"[{offset + position}] {hit.document_title}{chunk.CONTEXT_SEPARATOR}{hit.hierarchy_path or hit.title}\n"
		f"{(hit.text or '')[:RERANK_SNIPPET_CHARS]}"
		for position, hit in enumerate(hits)
	)
	response = llm.chat_completion(
		model,
		[
			{"role": "system", "content": RERANK_SYSTEM_PROMPT},
			{"role": "user", "content": f"Question: {query}\n\nCandidates:\n\n{candidates}"},
		],
		label="rag_rerank",
		response_format={"type": "json_object"},
		api_key=api_key,
		provider=provider,
	)
	parsed = frappe.parse_json(response["choices"][0]["message"]["content"]) or {}
	scores = {cint(item.get("id")): flt(item.get("score")) for item in parsed.get("scores") or []}
	complete = set(range(offset, offset + len(hits))) <= set(scores)
	return (scores if complete else {}), response.get("usage")


def rerank_scores(query: str, hits: list[Hit], model: str) -> dict[int, float]:
	"""Ask the cheap model to score every candidate 0-10 for answering `query`.

	Graded in batches of `RERANK_BATCH_SIZE` — see the constant for the measurement that
	forced it, and `RERANK_MAX_WORKERS` for why the batches run concurrently. The candidate
	numbers stay global across batches so a score always maps back to the same hit.
	"""
	from wikify.engine import settings

	api_key = settings.openrouter_key()
	offsets = range(0, len(hits), RERANK_BATCH_SIZE)
	with ThreadPoolExecutor(max_workers=min(len(offsets), RERANK_MAX_WORKERS)) as pool:
		batches = list(
			pool.map(
				lambda offset: score_batch(
					query,
					hits[offset : offset + RERANK_BATCH_SIZE],
					offset,
					model,
					api_key,
					RERANK_PROVIDER,
				),
				offsets,
			)
		)

	scores: dict[int, float] = {}
	for batch_scores, batch_usage in batches:
		usage.add(batch_usage)
		scores.update(batch_scores)
	return scores


def usable_verdict(scores: dict[int, float], expected: int) -> bool:
	"""False when the reply carries no verdict at all, so it must not be read as one.

	Two shapes are not verdicts. A reply that covers only part of the candidate list lost
	whichever batch failed, so the survivors would be ranked against nothing. And one
	identical non-zero score across every candidate is the model declining to discriminate.

	An all-zero reply IS a verdict — "none of these are relevant" — and is trusted as one,
	because the false all-zeros that made this system refuse answerable questions came from
	over-long candidate lists (see `RERANK_BATCH_SIZE`), not from the model's judgement.
	`answer.below_floor` never lets it refuse alone: the embedding leg has to agree.
	"""
	if len(scores) < expected:
		return False
	present = set(scores.values())
	# "Declining to discriminate" needs something to discriminate between, so a lone
	# candidate's score is always taken at face value.
	return len(scores) < 2 or len(present) > 1 or present == {0.0}


def rank_key(hit: Hit) -> tuple[float, float]:
	"""How hits are ordered once a rerank has run: the model's judgement first, the fusion
	score as the tiebreak. Candidates past `RERANK_CANDIDATES` were never judged and sort
	below the ones that were."""
	return (hit.rerank_score if hit.rerank_score is not None else -1.0, hit.score)


def rerank_hits(query: str, hits: list[Hit]) -> list[Hit]:
	"""LLM rerank of the top candidates. Never fatal — the search path degrades to the
	unreranked order when no OpenRouter key is configured, the call fails, or the reply
	carries no verdict."""
	from wikify.engine import llm, settings

	if not hits or not llm.has_openrouter():
		return hits

	head, tail = hits[:RERANK_CANDIDATES], hits[RERANK_CANDIDATES:]
	try:
		scores = rerank_scores(query, head, settings.get("classifier_model"))
	except Exception:
		frappe.log_error(title="RAG rerank failed", message=frappe.get_traceback())
		return hits

	if not usable_verdict(scores, len(head)):
		frappe.log_error(
			title="RAG rerank returned no verdict",
			message=f"Query: {query}\nCandidates: {len(head)}\nScores: {scores}",
		)
		return hits

	for position, hit in enumerate(head):
		hit.rerank_score = scores.get(position)
	head.sort(key=rank_key, reverse=True)
	return head + tail


def search(
	query: str,
	*,
	project: str | None = None,
	source_document: str | None = None,
	section_type: str | None = None,
	limit: int = 8,
	mode: str = "hybrid",
	rerank: bool = False,
	allowed_projects: list[str] | AclDecision = ACL_REQUIRED,
) -> list[Hit]:
	"""Retrieve sections for `query`. See the module docstring for what each mode means.

	`mode="filter"` ignores `limit` by design — it returns every section matching the
	metadata filter, which is the completeness guarantee the whole POC rests on.
	`allowed_projects` is the internal ACL pre-filter and has no usable default: pass the
	list of readable projects, or `ALL_PROJECTS` to opt out of the ACL clause on purpose.
	"""
	if mode not in MODES:
		frappe.throw(f"Unknown search mode '{mode}'. Expected one of: {', '.join(MODES)}.")
	assert_acl_decision(allowed_projects)

	table = store.chunks_table()
	if table is None:
		return []

	where = build_where(
		project, source_document, section_type, allowed_projects, include_title_only=mode == "filter"
	)
	candidate_limit = max(int(limit) * CANDIDATE_MULTIPLIER, CANDIDATE_FLOOR)

	vector_ranks: dict[str, int] = {}
	fts_ranks: dict[str, int] = {}
	vector_scores: dict[str, float] = {}
	if mode == "filter":
		rows = run_filter(table, where)
	elif mode == "vector":
		rows = run_vector(table, query, where, candidate_limit)
		vector_ranks = rank_by_chunk(rows)
		vector_scores = similarity_by_chunk(rows)
	elif mode == "fts":
		rows = run_fts(table, query, where, candidate_limit)
		fts_ranks = rank_by_chunk(rows)
	else:
		# The fused result hides which leg found what, so the two legs are re-run to label
		# each hit. The query is embedded once here and handed to both vector passes —
		# re-embedding per pass is the expensive part, the extra scans are not.
		query_vector = embed.embed_one(query)
		rows = run_hybrid(table, query, where, candidate_limit, vector=query_vector)
		vector_rows = run_vector(table, query, where, candidate_limit, vector=query_vector)
		vector_ranks = rank_by_chunk(vector_rows)
		vector_scores = similarity_by_chunk(vector_rows)
		fts_ranks = rank_by_chunk(run_fts(table, query, where, candidate_limit))

	hits = expand_to_sections(rows, vector_ranks, fts_ranks, vector_scores)
	# Filter mode re-sorts by document/page below, so a rerank here would be an LLM call
	# whose entire output is thrown away.
	if rerank and query and mode != "filter":
		hits = rerank_hits(query, hits)

	if mode == "filter":
		return sorted(hits, key=lambda hit: (hit.document_title, hit.page_start, hit.title))
	# `rank_key` falls back to the fusion score, so an unreranked search is ordered exactly
	# as before. Sorting on `hit.score` here used to discard the rerank outright: the
	# reranked winner was pushed back into fusion position and then cut by the `limit`
	# slice, which is how "slab rates under section 115BAC(1A)" scored 8.0 at fusion rank 10
	# and was never returned at all.
	hits.sort(key=rank_key, reverse=True)
	return hits[: int(limit)]
