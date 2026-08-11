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
from dataclasses import dataclass

import frappe

from wikify.rag import chunk, embed, store, usage

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

	def as_dict(self) -> dict:
		return self.__dict__.copy()


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


def expand_to_sections(
	rows: list[dict], vector_ranks: dict[str, int], fts_ranks: dict[str, int]
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
		for section in chunk.get_rows_by_name(
			"Source Section",
			list(best),
			["name", "title", "markdown", "hierarchy_path", "section_type", "source_document"],
		)
	}
	documents = {
		document["name"]: document
		for document in chunk.get_rows_by_name(
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
			)
		)
	return hits


def rerank_scores(query: str, hits: list[Hit], model: str) -> dict[int, float]:
	"""Ask the cheap model to score each candidate 0-10 for answering `query`."""
	from wikify.engine import llm

	candidates = "\n\n".join(
		f"[{position}] {hit.document_title}{chunk.CONTEXT_SEPARATOR}{hit.hierarchy_path or hit.title}\n"
		f"{(hit.text or '')[:RERANK_SNIPPET_CHARS]}"
		for position, hit in enumerate(hits)
	)
	response = llm.chat_completion(
		model,
		[
			{
				"role": "system",
				"content": (
					"You rank retrieved document sections by how well each one answers the "
					'user question. Reply with JSON: {"scores": [{"id": <candidate number>, '
					'"score": <0-10>}]} covering every candidate. No prose.'
				),
			},
			{"role": "user", "content": f"Question: {query}\n\nCandidates:\n\n{candidates}"},
		],
		label="rag_rerank",
		response_format={"type": "json_object"},
	)
	usage.add(response.get("usage"))
	content = response["choices"][0]["message"]["content"]
	parsed = frappe.parse_json(content) or {}
	from frappe.utils.data import cint, flt

	return {cint(item.get("id")): flt(item.get("score")) for item in parsed.get("scores") or []}


def ranks_candidates(scores: dict[int, float]) -> bool:
	"""False when the reply carries no ranking information, so it must not be read as one.

	The cheap classifier intermittently *succeeds* and returns 0 for every candidate. Taken
	at face value that is a confident "none of this is relevant", and `answer.below_floor`
	then refuses a question the retriever had already answered correctly — the worst failure
	this system has, because "the document doesn't say" is indistinguishable from the
	document genuinely not covering it. One flat verdict across several candidates is the
	reranker being unavailable, not the corpus being empty, so the caller keeps the fusion
	order instead.
	"""
	present = [score for score in scores.values() if score is not None]
	if len(present) < 2:
		return bool(present)
	return len(set(present)) > 1


def rerank_hits(query: str, hits: list[Hit]) -> list[Hit]:
	"""LLM rerank of the top candidates. Never fatal — the search path degrades to the
	unreranked order when no OpenRouter key is configured, the call fails, or the reply
	ranks nothing."""
	from wikify.engine import llm, settings

	if not hits or not llm.has_openrouter():
		return hits

	head, tail = hits[:RERANK_CANDIDATES], hits[RERANK_CANDIDATES:]
	try:
		scores = rerank_scores(query, head, settings.get("classifier_model"))
	except Exception:
		frappe.log_error(title="RAG rerank failed", message=frappe.get_traceback())
		return hits

	if not ranks_candidates(scores):
		frappe.log_error(
			title="RAG rerank returned a flat verdict",
			message=f"Query: {query}\nCandidates: {len(head)}\nScores: {scores}",
		)
		return hits

	for position, hit in enumerate(head):
		hit.rerank_score = scores.get(position)
	head.sort(
		key=lambda hit: (hit.rerank_score if hit.rerank_score is not None else -1.0, hit.score), reverse=True
	)
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
	if mode == "filter":
		rows = run_filter(table, where)
	elif mode == "vector":
		rows = run_vector(table, query, where, candidate_limit)
		vector_ranks = rank_by_chunk(rows)
	elif mode == "fts":
		rows = run_fts(table, query, where, candidate_limit)
		fts_ranks = rank_by_chunk(rows)
	else:
		# The fused result hides which leg found what, so the two legs are re-run to label
		# each hit. The query is embedded once here and handed to both vector passes —
		# re-embedding per pass is the expensive part, the extra scans are not.
		query_vector = embed.embed_one(query)
		rows = run_hybrid(table, query, where, candidate_limit, vector=query_vector)
		vector_ranks = rank_by_chunk(run_vector(table, query, where, candidate_limit, vector=query_vector))
		fts_ranks = rank_by_chunk(run_fts(table, query, where, candidate_limit))

	hits = expand_to_sections(rows, vector_ranks, fts_ranks)
	# Filter mode re-sorts by document/page below, so a rerank here would be an LLM call
	# whose entire output is thrown away.
	if rerank and query and mode != "filter":
		hits = rerank_hits(query, hits)

	if mode == "filter":
		return sorted(hits, key=lambda hit: (hit.document_title, hit.page_start, hit.title))
	hits.sort(key=lambda hit: hit.score, reverse=True)
	return hits[: int(limit)]
