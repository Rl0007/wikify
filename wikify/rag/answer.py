"""Grounded synthesis — route, retrieve, cite, answer (or honestly refuse).

The answer is written from a numbered context built out of retrieved chunks, and every
claim must carry the `[n]` marker of the chunk it came from. Two guardrails keep it
honest:

- **Refusal.** When nothing retrieved clears the score floor we return `refused=True` and
  say so, instead of letting the model improvise from its own memory.
- **Citation sanitising.** Markers pointing past the end of the citation list are stripped
  from the answer, so a rendered `[7]` always resolves to a real source card.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from wikify.agent import llm
from wikify.engine import settings
from wikify.rag import evidence, usage
from wikify.rag import search as rag_search
from wikify.rag.chunk import CONTEXT_SEPARATOR
from wikify.rag.router import Route, route

# Which retrieval leg each intent takes. "filter" returns every match (completeness);
# "hybrid" is RRF-fused vector + full-text.
MODE_FOR_INTENT = {"exhaustive": "filter", "semantic": "hybrid", "hybrid": "hybrid"}

TOP_K = 8
# An exhaustive answer is allowed to be long, but a runaway type (e.g. "other") must not
# push a thousand chunks into the prompt.
EXHAUSTIVE_LIMIT = 60

# RRF fusion scores are RANK-based: a top-ranked irrelevant chunk scores exactly what a
# top-ranked perfect one does (measured: 0.0328 for both an on-topic and a nonsense query
# on the demo corpus). So hybrid/fts scores carry no absolute quality signal and can only
# refuse on emptiness. The rerank score — the cheap model's 0-10 relevance judgement — is
# the real guardrail, which is why `answer()` reranks by default.
MIN_RERANK_SCORE = 3.0
# Raw vector distance does carry signal (measured: 0.51 on-topic vs 0.33 for nonsense).
MIN_VECTOR_SCORE = 0.36
# ponytail: both floors are calibrated against the demo corpus with potion-base-8M; recheck
# them from the eval harness (recall@k per golden question) if the embedder or corpus changes.

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
) -> list:
	"""Run the leg the route chose. Exhaustive returns ALL matches, not a top-k.

	`allowed_projects` carries the caller's ACL decision straight through to `search()`,
	which is where an omitted one throws.
	"""
	mode = MODE_FOR_INTENT[decided.intent]
	return rag_search.search(
		decided.query,
		project=project,
		section_type=decided.section_type,
		limit=EXHAUSTIVE_LIMIT if mode == "filter" else TOP_K,
		mode=mode,
		rerank=rerank,
		allowed_projects=allowed_projects,
	)


def below_floor(hits: list, mode: str) -> bool:
	"""True when nothing retrieved is good enough to answer from.

	A `filter` leg is an exact metadata match — it either matched or it didn't — so only
	emptiness refuses there, and the same holds for the rank-scored fusion legs.
	"""
	if not hits:
		return True
	# Only a real ranking may refuse. `search.rerank_hits` leaves every score unset when the
	# reranker answers but ranks nothing, so a silent rerank failure degrades to the fusion
	# order here rather than turning into "the wiki doesn't cover this".
	reranked = [hit.rerank_score for hit in hits if hit.rerank_score is not None]
	if reranked:
		return max(reranked) < MIN_RERANK_SCORE
	if mode == "vector":
		return max(hit.score for hit in hits) < MIN_VECTOR_SCORE
	return False


def format_context(hits: list) -> str:
	"""The numbered excerpt block the model cites against — `[n]` is the hit's position."""
	blocks = []
	for position, hit in enumerate(hits, start=1):
		pages = f"p.{hit.page_start}"
		if hit.page_end and hit.page_end != hit.page_start:
			pages = f"p.{hit.page_start}-{hit.page_end}"
		crumb = f"{hit.document_title}{CONTEXT_SEPARATOR}{hit.hierarchy_path or hit.title}"
		header = f"[{position}] {crumb} ({pages})"
		if hit.section_type:
			header += f" — type: {hit.section_type}"
		blocks.append(f"{header}\n{hit.text}")
	return "\n\n---\n\n".join(blocks)


def drop_unknown_citations(text: str, citation_count: int) -> str:
	"""Remove `[n]` markers with no matching source card — a citation must resolve."""

	def keep(match: re.Match) -> str:
		number = int(match.group(1))
		return match.group(0) if 1 <= number <= citation_count else ""

	return CITATION_MARKER.sub(keep, text)


def generate(question: str, context: str, model: str, on_delta: Callable | None) -> str:
	"""Stream the synthesis, forwarding each delta to `on_delta` as it arrives."""
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
	# Kept and folded in once at the end: a provider that repeats a running total on every
	# chunk would otherwise be billed once per token.
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
	on_route: Callable | None = None,
	on_citations: Callable | None = None,
	on_delta: Callable | None = None,
) -> dict:
	"""Route → retrieve → cite → synthesise.

	Returns the answer, its citations, the route, and what the turn cost: `model` plus the
	`cost` / `prompt_tokens` / `completion_tokens` of every completion it made — routing,
	reranking and synthesis together, not synthesis alone.


	The three callbacks let a caller stream the same work in the order the UI needs it —
	route, then sources, then answer tokens — without re-running any of it. Omit them for
	a plain blocking answer. `allowed_projects` is the ACL pre-filter; it is handed to
	`search()` so it lands in the store's `where`, never as a post-hoc trim of the hits.
	It has no permissive default — a caller that omits it throws rather than answering from
	every project on the site.
	"""
	# Checked before routing so a missing ACL costs no LLM call and publishes no callback.
	rag_search.assert_acl_decision(allowed_projects)
	model = llm.resolve_model(project=project)

	with usage.collect() as spend:
		decided = route(question, project, history)
		if on_route:
			on_route(decided.as_dict())

		hits = retrieve(decided, project, rerank, allowed_projects)
		mode = MODE_FOR_INTENT[decided.intent]
		refused = below_floor(hits, mode) or not settings.openrouter_key()

		citations = [] if refused else [hit.as_dict() for hit in hits]
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
			# The quote each citation rests on is checked against the source before it is
			# shown — a citation rendered as confirmed while misstating a figure is worse
			# than none. `on_citations` has already fired, so only the returned dict
			# carries the verified set.
			citations = evidence.attach_verified_quotes(text, citations)

		return {
			"answer": text,
			"citations": citations,
			"route": decided.as_dict(),
			"refused": refused,
			"model": model,
			# A refusal still costs the routing call, so spend is reported on both paths.
			**spend,
		}
