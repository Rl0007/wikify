from __future__ import annotations

import re
from dataclasses import dataclass

import frappe
from frappe.utils.data import cint, flt

from wikify.rag import chunk, embed, evidence, rerank, store

MODES = ("vector", "fts", "hybrid", "filter")
FTS_COLUMN = "text"


class AclDecision:
	def __init__(self, label: str):
		self.label = label

	def __repr__(self) -> str:
		return f"search.{self.label}"


# Both are distinct objects on purpose. `ALL_PROJECTS` used to be `None`, which collided
# with every "argument not supplied" default in the call chain — a caller that forgot to
# thread the user's projects silently got the unscoped opt-out instead of the guard.
ALL_PROJECTS = AclDecision("ALL_PROJECTS")
ACL_REQUIRED = AclDecision("ACL_REQUIRED")

CANDIDATE_MULTIPLIER = 5
CANDIDATE_FLOOR = 50
RERANK_CANDIDATES = 50

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
# The score column is requested explicitly: LanceDB only auto-projects it for now, and warns
# that a future release will drop it from an explicit `select()`.
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
	vector_score: float | None = None

	def as_dict(self) -> dict:
		return self.__dict__.copy()


def hit_value(hit, key: str):
	return hit.get(key) if isinstance(hit, dict) else getattr(hit, key, None)


def page_label(hit) -> str:
	start, end = hit_value(hit, "page_start"), hit_value(hit, "page_end")
	return f"p.{start}-{end}" if end and end != start else f"p.{start}"


def crumb(hit) -> str:
	path = hit_value(hit, "hierarchy_path") or hit_value(hit, "title")
	return f"{hit_value(hit, 'document_title')}{chunk.CONTEXT_SEPARATOR}{path}"


def assert_acl_decision(allowed_projects) -> None:
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
	return re.sub(r"[^\w\s]", " ", query or "").strip()


def has_fts_index(table) -> bool:
	return any(
		table_index.index_type == "FTS" and FTS_COLUMN in (table_index.columns or [])
		for table_index in table.list_indices()
	)


def run_vector(
	table, query: str, where: str | None, limit: int, vector: list[float] | None = None
) -> list[dict]:
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
	text = fts_query(query)
	if vector is None:
		vector = embed.embed_one(query)
	if not text or not has_fts_index(table):
		return run_vector(table, query, where, limit, vector=vector)
	query_builder = table.search(query_type="hybrid").vector(vector).text(text).select(RESULT_COLUMNS)
	return scoped(query_builder, where).limit(limit).to_list()


def run_filter(table, where: str | None) -> list[dict]:
	return scoped(table.search().select(RESULT_COLUMNS), where).limit(None).to_list()


def row_score(row: dict) -> float:
	if row.get("_relevance_score") is not None:
		return float(row["_relevance_score"])
	if row.get("_score") is not None:
		return float(row["_score"])
	if row.get("_distance") is not None:
		return 1.0 / (1.0 + float(row["_distance"]))
	return 1.0


def rank_by_chunk(rows: list[dict]) -> dict[str, int]:
	return {row["id"]: position for position, row in enumerate(rows, start=1)}


def similarity_by_chunk(rows: list[dict]) -> dict[str, float]:
	return {row["id"]: row_score(row) for row in rows}


def expand_to_sections(
	rows: list[dict],
	vector_ranks: dict[str, int],
	fts_ranks: dict[str, int],
	vector_scores: dict[str, float],
) -> list[Hit]:
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


def rerank_scores(query: str, hits: list[Hit]) -> dict[int, float]:
	relevance = rerank.scores(query, [hit.text or "" for hit in hits])
	return dict(enumerate(relevance))


def usable_verdict(scores: dict[int, float], expected: int) -> bool:
	# An all-zero result IS a verdict — every candidate's logit sat at the floor — and is
	# trusted as one; `answer.below_floor` never lets it refuse alone. The short/flat shapes
	# below cannot occur with a local scorer; the check is kept as the seam that catches a
	# future scorer regressing into them.
	if len(scores) < expected:
		return False
	present = set(scores.values())
	return len(scores) < 2 or len(present) > 1 or present == {0.0}


def rank_key(hit: Hit) -> tuple[float, float]:
	# One key drives both the rerank order and the `limit` slice. Sorting on `hit.score` after
	# reranking used to discard the rerank outright: the winner was pushed back to its fusion
	# position and sliced off, and questions the corpus answered came back refused.
	return (hit.rerank_score if hit.rerank_score is not None else -1.0, hit.score)


def rerank_hits(query: str, hits: list[Hit]) -> list[Hit]:
	# Never fatal: the search path degrades to the unreranked order when the model cannot be
	# loaded or the scores carry no verdict.
	if not hits:
		return hits

	head, tail = hits[:RERANK_CANDIDATES], hits[RERANK_CANDIDATES:]
	try:
		scores = rerank_scores(query, head)
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
	use_reranker: bool = False,
	allowed_projects: list[str] | AclDecision = ACL_REQUIRED,
) -> list[Hit]:
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
		query_vector = embed.embed_one(query)
		rows = run_hybrid(table, query, where, candidate_limit, vector=query_vector)
		vector_rows = run_vector(table, query, where, candidate_limit, vector=query_vector)
		vector_ranks = rank_by_chunk(vector_rows)
		vector_scores = similarity_by_chunk(vector_rows)
		fts_ranks = rank_by_chunk(run_fts(table, query, where, candidate_limit))

	hits = expand_to_sections(rows, vector_ranks, fts_ranks, vector_scores)
	if use_reranker and query and mode != "filter":
		hits = rerank_hits(query, hits)

	if mode == "filter":
		return sorted(hits, key=lambda hit: (hit.document_title, hit.page_start, hit.title))
	hits.sort(key=rank_key, reverse=True)
	return hits[: int(limit)]
