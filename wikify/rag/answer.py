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
from wikify.rag.router import Route, route

# Which retrieval leg each intent takes. "filter" returns every match (completeness);
# "hybrid" is RRF-fused vector + full-text.
MODE_FOR_INTENT = {"exhaustive": "filter", "semantic": "hybrid", "hybrid": "hybrid"}

TOP_K = 8
# An exhaustive answer is allowed to be long, but a runaway type (e.g. "other") must not
# push a thousand chunks into the prompt.
EXHAUSTIVE_LIMIT = 60
# The naive leg of `compare`: plain vector top-k with no filter and no routing.
NAIVE_LIMIT = 8

# RRF fusion scores are RANK-based: a top-ranked irrelevant chunk scores exactly what a
# top-ranked perfect one does (measured: 0.0328 for both an on-topic and a nonsense query
# on the demo corpus). So hybrid/fts scores carry no absolute quality signal and can only
# refuse on emptiness. The rerank score — the cheap model's 0-10 relevance judgement — is
# the real guardrail, which is why `answer()` reranks by default.
MIN_RERANK_SCORE = 3.0
# Raw vector distance does carry signal (measured: 0.51 on-topic vs 0.33 for nonsense).
MIN_VECTOR_SCORE = 0.36
# The reranker must never be able to mute the product on its own, because it fails silently:
# it answers, it looks healthy, and it scores every candidate 0. So a below-floor rerank only
# refuses when the embedding leg agrees, and a strong embedding match overrules it outright.
# Deliberately well clear of MIN_VECTOR_SCORE — overruling a refusal takes real confidence,
# not merely "good enough to answer from". Measured on potion-base-8M: questions the corpus
# answers peak at 0.56-0.63 (ICAI) and 0.60 (demo), questions it does not at 0.39-0.45 (ICAI)
# and 0.44 (demo).
STRONG_VECTOR_SCORE = 0.5
# ponytail: all three floors are calibrated against potion-base-8M on the demo + ICAI
# corpora; recheck them from the eval harness (recall@k per golden question) if the embedder
# or corpus changes.

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
	"""Run the leg the route chose. Exhaustive returns ALL matches, not a top-k.

	`allowed_projects` carries the caller's ACL decision straight through to `search()`,
	which is where an omitted one throws. `top_k` widens the similarity legs for a caller
	measuring recall at a different k (the eval harness) — the exhaustive leg ignores it,
	because completeness is the whole point of that mode.
	"""
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
	"""The baseline: raw question, plain vector top-k, no router and no metadata filter.

	This is what a textbook RAG pipeline does, and it is the thing the POC argues against —
	so it is defined once and both the demo API and the eval harness measure the same leg.
	"""
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
	"""Naive top-k beside the routed leg, plus the sections only routing found.

	Hits and the `Route` come back as objects; each caller shapes its own payload. The demo
	endpoint and the eval scorecard both read this, so the headline number they show can
	never be computed two different ways — which is the exact drift the eval exists to catch.
	"""
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
	"""The closest embedding match in the result set, or None when this leg has no say."""
	similarities = [hit.vector_score for hit in hits if hit.vector_score is not None]
	return max(similarities) if similarities else None


def below_floor(hits: list, mode: str) -> bool:
	"""True when nothing retrieved is good enough to answer from.

	Refusing is the most damaging thing this system does when it is wrong — to a reader
	"I couldn't find this in the wiki" is indistinguishable from the document not covering
	it — so it takes two independent legs to agree, never the reranker alone.

	A `filter` leg is an exact metadata match — it either matched or it didn't — so only
	emptiness refuses there, and the same holds for the rank-scored fusion legs.
	"""
	if not hits:
		return True
	# `search.rerank_hits` leaves every score unset when the reranker carries no verdict, so
	# a silent rerank failure degrades to the fusion order here rather than turning into
	# "the wiki doesn't cover this".
	if any(hit.rerank_score is not None for hit in hits):
		return rerank_below_floor(hits) and not overrules_rerank(hits)
	if mode == "vector":
		return max(hit.score for hit in hits) < MIN_VECTOR_SCORE
	return False


def rerank_below_floor(hits: list) -> bool:
	"""True when the reranker scored every candidate under the floor."""
	reranked = [hit.rerank_score for hit in hits if hit.rerank_score is not None]
	return bool(reranked) and max(reranked) < MIN_RERANK_SCORE


def overrules_rerank(hits: list) -> bool:
	"""True when the embedding leg is confident enough to veto a below-floor rerank."""
	best = best_vector_score(hits)
	return best is not None and best >= STRONG_VECTOR_SCORE


def rerank_overruled(hits: list) -> bool:
	"""The reranker wanted to refuse and the embedding leg would not let it."""
	return rerank_below_floor(hits) and overrules_rerank(hits)


def clear_rerank_scores(citations: list[dict]) -> None:
	"""Drop the rerank numbers from the cards. Used when the embedding leg overruled the
	rerank: those scores are demonstrably wrong, and a card reading "rerank 0.0" next to an
	answer the reranker tried to suppress is worse than no number at all. `None` is the
	signal the UI hides on."""
	for citation in citations:
		citation["rerank_score"] = None


def format_context(hits: list) -> str:
	"""The numbered excerpt block the model cites against — `[n]` is the hit's position.

	Whole sections, not windows around the matched chunk. Windowing is the obvious cost fix
	and it was measured and rejected: on the six ICAI rate questions of `specs/poc-icai-EVAL.md`,
	trimming each section to 1,500 chars around its match cut the synthesis prompt 11,002 →
	3,783 tokens and the spend $0.078 → $0.035 per ask, but dropped the 37% surcharge rate out
	of G1 and took verified citations 12 → 7. Synthesis is decode-bound (measured: 80% of its
	wall clock is decode at ~52 tok/s), so the prompt was never buying us speed — only money,
	and not at the price of a statutory rate.
	# ponytail: revisit only behind a figure-recall gate over a table-heavy question set, and
	# with the window sized from the citation quotes rather than a flat character budget.
	"""
	blocks = []
	for position, hit in enumerate(hits, start=1):
		header = f"[{position}] {rag_search.crumb(hit)} ({rag_search.page_label(hit)})"
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
	decided: Route | None = None,
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
		# A caller that had to route before it could decide something (`api.rag.ask` keys its
		# cache on the rewritten query) hands the decision in rather than paying for it twice.
		# `collect()` nests onto the outer total, so that caller's routing spend is still in
		# `spend` — the figure has to cover every leg the turn made, wherever it was made.
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
