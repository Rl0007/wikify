from __future__ import annotations

import re
from collections.abc import Callable

from wikify.agent import llm
from wikify.engine import settings
from wikify.rag import evidence, usage
from wikify.rag import search as rag_search
from wikify.rag.router import Route, route

MODE_FOR_INTENT = {"exhaustive": "filter", "semantic": "hybrid", "hybrid": "hybrid"}

TOP_K = 8
EXHAUSTIVE_LIMIT = 60
NAIVE_LIMIT = 8

MIN_RERANK_SCORE = 3.0
MIN_VECTOR_SCORE = 0.36
STRONG_VECTOR_SCORE = 0.5

REFUSAL = (
	"I couldn't find this in the wiki. Nothing in the indexed documents is close enough to "
	"your question for me to answer it without guessing."
)

SYSTEM_PROMPT = """You answer questions strictly from the numbered wiki excerpts given to \
you. Rules:

- Use ONLY the excerpts. If they do not cover part of the question, say that part is not \
in the wiki.
- Cite every factual sentence with the marker of the excerpt it came from: [1], [3]. Put \
the marker at the end of the sentence. Never cite a number that is not in the list.
- Answer in markdown. Lead with the answer, keep it tight, use a bullet list when the \
question asks for several things.
- When the excerpts are an exhaustive list of a kind of section, list every one of them \
and say how many there are.
- Never invent document titles, page numbers, or excerpt numbers.
- Support each factual sentence with a short VERBATIM quote copied character-for-character \
from the excerpt, in double quotes, immediately before the marker: "...exact words..." [3]. \
Quote 5-40 words. Never paraphrase inside quotation marks."""

CITATION_MARKER = re.compile(r"\[(\d+)\]")


def retrieve(
	decided: Route,
	project: str | None,
	rerank: bool,
	allowed_projects=rag_search.ACL_REQUIRED,
	top_k: int = TOP_K,
) -> list:
	mode = MODE_FOR_INTENT[decided.intent]
	return rag_search.search(
		decided.query,
		project=project,
		section_type=decided.section_type,
		limit=EXHAUSTIVE_LIMIT if mode == "filter" else top_k,
		mode=mode,
		use_reranker=rerank,
		allowed_projects=allowed_projects,
	)


def naive_retrieve(query: str, project: str | None, allowed_projects, limit: int = NAIVE_LIMIT) -> list:
	return rag_search.search(
		query, project=project, limit=limit, mode="vector", allowed_projects=allowed_projects
	)


def compare(
	query: str,
	project: str | None,
	allowed_projects,
	naive_limit: int = NAIVE_LIMIT,
	top_k: int = TOP_K,
) -> dict:
	naive = naive_retrieve(query, project, allowed_projects, naive_limit)
	decided = route(query, project)
	routed = retrieve(decided, project, False, allowed_projects, top_k=top_k)
	found_by_naive = {hit.section for hit in naive}
	return {
		"route": decided,
		"naive": naive,
		"routed": routed,
		"missed_by_naive": [hit for hit in routed if hit.section not in found_by_naive],
	}


def best_vector_score(hits: list) -> float | None:
	similarities = [hit.vector_score for hit in hits if hit.vector_score is not None]
	return max(similarities) if similarities else None


def below_floor(hits: list, mode: str) -> bool:
	if not hits:
		return True
	if any(hit.rerank_score is not None for hit in hits):
		return rerank_below_floor(hits) and not overrules_rerank(hits)
	if mode == "vector":
		return max(hit.score for hit in hits) < MIN_VECTOR_SCORE
	return False


def rerank_below_floor(hits: list) -> bool:
	reranked = [hit.rerank_score for hit in hits if hit.rerank_score is not None]
	return bool(reranked) and max(reranked) < MIN_RERANK_SCORE


def overrules_rerank(hits: list) -> bool:
	best = best_vector_score(hits)
	return best is not None and best >= STRONG_VECTOR_SCORE


def rerank_overruled(hits: list) -> bool:
	return rerank_below_floor(hits) and overrules_rerank(hits)


def clear_rerank_scores(citations: list[dict]) -> None:
	for citation in citations:
		citation["rerank_score"] = None


def format_context(hits: list) -> str:
	blocks = []
	for position, hit in enumerate(hits, start=1):
		header = f"[{position}] {rag_search.crumb(hit)} ({rag_search.page_label(hit)})"
		if hit.section_type:
			header += f" — type: {hit.section_type}"
		blocks.append(f"{header}\n{hit.text}")
	return "\n\n---\n\n".join(blocks)


def drop_unknown_citations(text: str, citation_count: int) -> str:
	def keep(match: re.Match) -> str:
		number = int(match.group(1))
		return match.group(0) if 1 <= number <= citation_count else ""

	return CITATION_MARKER.sub(keep, text)


def generate(question: str, context: str, model: str, on_delta: Callable | None) -> str:
	messages = [
		{"role": "system", "content": SYSTEM_PROMPT},
		{"role": "user", "content": f"Excerpts:\n\n{context}\n\n---\n\nQuestion: {question}"},
	]
	streamed = on_delta is not None
	response = llm.complete_with_tools(model, messages, [], stream=streamed, include_usage=streamed)
	if not streamed:
		usage.add(getattr(response, "usage", None))
		return response.choices[0].message.content or ""

	text = ""
	streamed_usage = None
	for chunk in response:
		streamed_usage = getattr(chunk, "usage", None) or streamed_usage
		delta = chunk.choices[0].delta if chunk.choices else None
		piece = getattr(delta, "content", None) if delta else None
		if piece:
			text += piece
			on_delta(piece)
	usage.add(streamed_usage)
	return text


def answer(
	question: str,
	*,
	project: str | None = None,
	history: list | None = None,
	rerank: bool = True,
	allowed_projects=rag_search.ACL_REQUIRED,
	decided: Route | None = None,
	on_route: Callable | None = None,
	on_citations: Callable | None = None,
	on_delta: Callable | None = None,
) -> dict:
	rag_search.assert_acl_decision(allowed_projects)
	model = llm.resolve_model(project=project)

	with usage.collect() as spend:
		decided = decided or route(question, project, history)
		if on_route:
			on_route(decided.as_dict())

		hits = retrieve(decided, project, rerank, allowed_projects)
		mode = MODE_FOR_INTENT[decided.intent]
		refused = below_floor(hits, mode) or not settings.openrouter_key()

		citations = [] if refused else [hit.as_dict() for hit in hits]
		if citations and rerank_overruled(hits):
			clear_rerank_scores(citations)
		if on_citations:
			on_citations(citations)

		if refused:
			text = REFUSAL if below_floor(hits, mode) else f"{REFUSAL} (No language model is configured.)"
			if on_delta:
				on_delta(text)
			citations = []
		else:
			text = drop_unknown_citations(
				generate(decided.query, format_context(hits), model, on_delta), len(hits)
			)
			citations = evidence.attach_verified_quotes(text, citations)

		return {
			"answer": text,
			"citations": citations,
			"route": decided.as_dict(),
			"refused": refused,
			"model": model,
			**spend,
		}
