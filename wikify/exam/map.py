"""Linking each exam question to the corpus sections that teach it.

Two independent legs, because they fail differently:

**Retrieval** — the question text through `rag.search`. Good at paraphrase, weak when the
question is a numerical computation whose wording shares little vocabulary with the study
material that explains it.

**Statutory** — the section references ICAI cites in its own model answer, matched against
the corpus full-text. This is the stronger signal and it is nearly free: it is the
examiner's own statement of what the question tests, not a guess about what it resembles.
A question whose answer cites `section 115BAC` is *about* 115BAC, whatever words the stem
used.

Neither leg calls `answer.ask()`. Synthesising a grounded answer per question would cost
~$0.047 each — about $28 across 600 questions — to produce prose nobody reads. Retrieval's
embedding leg is local `model2vec` and free.

Topic rollup is the other half of the job, and it is not simply "take the root ancestor".
The ICAI corpus's level-1 sections are a mix of real chapters (`CAPITAL GAINS`), front
matter (`Preface`, `INDEX`), OCR noise, and stray fragments the sectioniser promoted by
mistake (`AMT liability not attracted`). Rolling up blindly produces a heatmap whose rows
are mostly rubbish. `topic_of` therefore climbs to the highest ancestor that actually holds
a subtree, and falls back to the section itself when no ancestor qualifies — see below.
"""

from __future__ import annotations

import math
import re

import frappe

from wikify.rag import answer as rag_answer
from wikify.rag import search

# An ancestor needs at least this many descendant sections to count as a topic. Tuned
# against the ICAI corpus, where real chapters carry dozens of descendants and the spurious
# level-1 fragments carry none.
MIN_TOPIC_SUBTREE = 3

# How many retrieved sections to consider per question before rollup. Small on purpose:
# beyond the first handful, hits are about the topic's neighbours rather than the topic, and
# every extra hit dilutes the year-by-topic matrix with near-misses.
RETRIEVAL_DEPTH = 6

# Matches "section 115BAC", "Section 45(1A)", "u/s 54F" — the forms ICAI prints.
# The trailing lookahead rejects provisions of OTHER statutes: "section 15 of the MSMED Act"
# is not an Income-tax section, and harvesting it produced confident matches against
# whatever Income-tax section happened to share the number.
STATUTORY = re.compile(
	r"(?:section|sec\.?|u/s)\s*([0-9]+[A-Z]{0,4}(?:\([0-9A-Za-z]+\))*)"
	r"(?!\s+of\s+the\s+(?!Income)\w+)",
	re.IGNORECASE,
)

# A provision written without any citation word, as a bare comma-separated list. Extraction
# emits this format for some papers, and the anchored regex above reads none of it — 14
# questions in one paper silently lost their statutory leg AND their gap verdict.
BARE_PROVISION = re.compile(r"^[0-9]+[A-Z]{0,4}(?:\([0-9A-Za-z]+\))*$")


def section_index(project: str) -> dict[str, dict]:
	"""Every section in `project`, keyed by name, with the fields rollup needs.

	One query for the whole project rather than a walk per question: mapping 160 questions
	against 296 sections would otherwise be tens of thousands of row fetches.
	"""
	documents = frappe.get_all("Source Document", filters={"project": project}, pluck="name")
	if not documents:
		return {}
	rows = frappe.get_all(
		"Source Section",
		filters={"source_document": ["in", documents]},
		fields=["name", "title", "parent_source_section", "level", "lft", "rgt"],
	)
	return {row["name"]: row for row in rows}


def subtree_size(row: dict) -> int:
	"""Descendant count from the nested-set bounds."""
	lft, rgt = row.get("lft") or 0, row.get("rgt") or 0
	return max(0, (rgt - lft - 1) // 2)


def topic_of(section: str, index: dict[str, dict]) -> dict | None:
	"""The heatmap row a section belongs to.

	Climbs to the HIGHEST ancestor whose subtree is big enough to be a real chapter. Taking
	the root unconditionally would file questions under `Preface`, `INDEX` and one-line
	fragments that the sectioniser promoted to level 1; taking the section itself would give
	a 296-row grid no student can read. Falling back to the section itself when nothing
	qualifies keeps a thin corner of the corpus visible instead of silently dropping it.
	"""
	row = index.get(section)
	if not row:
		return None

	chain = []
	cursor = row
	seen = set()
	while cursor and cursor["name"] not in seen:
		seen.add(cursor["name"])
		chain.append(cursor)
		parent = cursor.get("parent_source_section")
		cursor = index.get(parent) if parent else None

	# chain runs section → … → root; the last qualifying entry is the highest one.
	qualifying = [entry for entry in chain if subtree_size(entry) >= MIN_TOPIC_SUBTREE]
	chosen = qualifying[-1] if qualifying else row
	return {"section": chosen["name"], "title": (chosen.get("title") or "").strip() or chosen["name"]}


def statutory_refs(question: dict) -> list[str]:
	"""Normalised section numbers cited in ICAI's model answer for this question.

	Reads both formats extraction emits: anchored ("section 45(1A)") and bare
	("45(1A), 115BAC"). The bare fallback only runs when the anchored pass finds nothing, so
	a well-formed field is never re-parsed loosely.
	"""
	raw = question.get("statutory_refs") or ""
	found = {match.group(1).upper() for match in STATUTORY.finditer(raw)}
	if not found:
		found = {token.strip().upper() for token in raw.split(",") if BARE_PROVISION.match(token.strip())}
	return sorted(found)


def retrieval_hits(question: dict, project: str) -> tuple[list[tuple[str, float]], float]:
	"""`([(section, score)], best_vector_score)` from a hybrid search on the question text.

	The absolute vector similarity is returned alongside the fusion scores because it is the
	only retrieval number that means the same thing across queries — fusion ranks are
	relative to whatever else came back, so a top hit in a corpus with nothing relevant still
	ranks first. Deciding coverage needs the absolute figure.
	"""
	text = (question.get("question_text") or "").strip()
	if not text:
		return [], 0.0
	hits = search.search(
		text[:4000],
		project=project,
		limit=RETRIEVAL_DEPTH,
		mode="hybrid",
		allowed_projects=search.ALL_PROJECTS,
	)
	best_vector = max((hit.vector_score or 0.0) for hit in hits) if hits else 0.0
	return [(hit.section, float(hit.score or 0)) for hit in hits], float(best_vector)


def markdown_index(project: str) -> list[tuple[str, str]]:
	"""`(section, markdown)` for the whole project, loaded once per mapping run.

	Held in memory rather than queried per provision: a run resolves a few hundred distinct
	references against ~300 sections, and one fetch beats hundreds of round trips.
	"""
	documents = frappe.get_all("Source Document", filters={"project": project}, pluck="name")
	if not documents:
		return []
	rows = frappe.get_all(
		"Source Section",
		filters={"source_document": ["in", documents]},
		fields=["name", "markdown"],
	)
	return [(row["name"], row.get("markdown") or "") for row in rows]


def provision_pattern(ref: str) -> re.Pattern:
	"""A regex that matches `ref` only where it is used AS a provision.

	Anchored on BOTH sides. The left anchor (a citation word) stops a bare number matching a
	page number or a rupee figure. The right anchor stops a short reference swallowing every
	longer section that shares its prefix — without it, ref `11` matched `115JB`, `115BAA`,
	`115BAC` and friends for **264 phantom occurrences and zero genuine ones**, which filed a
	charitable-trust question under `AMT liability not attracted` at the highest statutory
	score in the corpus and presented it to the student as examiner evidence.

	A following `(` is still allowed, because `section 11(2)` genuinely is section 11; only
	an alphanumeric continuation is rejected.
	"""
	escaped = re.escape(ref)
	boundary = r"(?![0-9A-Za-z])"
	# Three ways this corpus writes a provision, measured over its own markdown:
	# "section N" (474), "u/s N" (356), and a bare number in a markdown table cell (123).
	# Without the table form the entire TDS/TCS chapter is invisible to the statutory leg,
	# which is why a salary-TDS question scored highest against the wrong chapter.
	return re.compile(
		r"(?:(?:section|sec\.?|u/s)\s*\[?\s*"
		+ escaped
		+ boundary
		+ r"|\|\s*"
		+ escaped
		+ boundary
		+ r"\s*\|)",
		re.IGNORECASE,
	)


def parent_provision(ref: str) -> str | None:
	"""`16(ia)` -> `16`. The parent section a sub-section belongs to.

	Only safe now that `provision_pattern` is anchored on the right: before that, falling
	back to `16` would have matched `160`, `161` and every other section sharing the prefix.
	"""
	base = ref.split("(", 1)[0].strip()
	return base if base and base != ref else None


def statutory_hits(refs: list[str], markdown: list[tuple[str, str]]) -> list[tuple[str, float]]:
	"""`(section, score)` for sections that genuinely cite one of `refs`.

	A literal, anchored text match — NOT full-text search. The FTS leg this replaces scored
	`section 9999Z` and `section ZZZZ99` as three confident hits apiece, because the tokeniser
	matched the word "section" and ignored the number. Every question that cited anything
	therefore looked covered, which made the coverage verdict meaningless and made it
	impossible for a genuine gap to ever surface.
	"""
	found: dict[str, float] = {}
	for ref in refs:
		matches = sections_citing(ref, markdown)
		if not matches:
			# Fall back to the parent section, so a question citing 16(ia) still finds the
			# chapter that teaches section 16 when the sub-section itself is not spelled out.
			parent = parent_provision(ref)
			matches = sections_citing(parent, markdown) if parent else {}
		if not matches:
			continue
		# Inverse document frequency: a provision the corpus mentions everywhere says little
		# about which chapter a question belongs to, while one appearing in three sections is
		# close to a pointer. Without this, 115BAC (25 sections) outweighed 91 (3 sections)
		# purely by being common.
		weight = 1.0 / math.log(1 + len(matches))
		for section, occurrences in matches.items():
			found[section] = found.get(section, 0.0) + weight * (min(3.0, occurrences) / 3.0)
	return sorted(found.items(), key=lambda pair: pair[1], reverse=True)[:6]


def sections_citing(ref: str | None, markdown: list[tuple[str, str]]) -> dict[str, int]:
	"""`section -> occurrence count` for one provision."""
	if not ref:
		return {}
	pattern = provision_pattern(ref)
	hits: dict[str, int] = {}
	for section, text in markdown:
		if not text:
			continue
		occurrences = len(pattern.findall(text))
		if occurrences:
			hits[section] = occurrences
	return hits


def rank_topics(
	question: dict, project: str, index: dict[str, dict], markdown: list[tuple[str, str]]
) -> dict:
	"""Both legs, rolled up to topics and merged into a ranked list.

	The statutory leg is weighted above retrieval because it is evidence rather than
	similarity — ICAI naming a provision in its own answer says what the question tests,
	while a vector match only says the wording looked alike.
	"""
	scores: dict[str, dict] = {}

	def add(section: str, weight: float, method: str) -> None:
		topic = topic_of(section, index)
		if not topic:
			return
		entry = scores.setdefault(
			topic["section"],
			{
				"topic_section": topic["section"],
				"topic_title": topic["title"],
				"score": 0.0,
				"section": section,
				"method": method,
			},
		)
		entry["score"] += weight
		if method == "statutory":
			entry["method"] = "statutory"

	hits, best_vector = retrieval_hits(question, project)
	for section, score in hits:
		add(section, score, "retrieval")

	statutory = statutory_hits(statutory_refs(question), markdown)
	for section, score in statutory:
		add(section, score * 2.0, "statutory")

	ranked = sorted(scores.values(), key=lambda entry: entry["score"], reverse=True)
	for position, entry in enumerate(ranked, start=1):
		entry["rank"] = position
	return {"topics": ranked, "best_vector": best_vector, "statutory_matched": bool(statutory)}


def coverage_verdict(outcome: dict, refs: list[str]) -> str:
	"""`covered` / `gap` / `unknown` — whether this project holds material for the question.

	Decided on the LITERAL test, not on similarity. ICAI names the provisions in its own
	model answer; either those provisions appear in the corpus or they do not. No threshold
	to tune, and the answer is checkable by hand.

	A vector floor was tried first and measured against this corpus: real Direct Tax
	questions score 0.500-0.698, but a lean-manufacturing question about Kanban and takt
	time scores 0.443 and a GST question 0.548 — both above `answer.MIN_VECTOR_SCORE` (0.36),
	and the GST one above the weakest genuine question. Static 256-dim embeddings simply do
	not separate on-syllabus from off-syllabus by enough to carry this decision, so the
	similarity score is not used for it at all.

	`unknown` is its own verdict rather than being folded into either side: a question whose
	answer cites no provision (most MCQs, some discussion questions) tells us nothing about
	coverage, and guessing would either invent gaps or hide them. Those keep their retrieval
	mapping and are surfaced as "similarity only" in the UI.
	"""
	if outcome["statutory_matched"]:
		return "covered"
	return "gap" if refs else "unknown"


def map_question(
	question: dict,
	project: str,
	index: dict[str, dict],
	markdown: list[tuple[str, str]],
	*,
	keep: int = 3,
) -> dict:
	"""Store the topic links for one question.

	An uncovered question is stored with NO topic links and `mapping_status = "No Match"`,
	so it never contributes marks to a chapter it has nothing to do with. It is not lost —
	`score.gaps` reports it under the provision ICAI cited.
	"""
	outcome = rank_topics(question, project, index, markdown)
	verdict = coverage_verdict(outcome, statutory_refs(question))
	# Every match is stored, including those of a question this project cannot cover.
	# Mapping gathers evidence; deciding whether that evidence counts belongs to `score`,
	# which knows which sources the reader has scoped to. Suppressing links here would
	# freeze the covered/gap verdict against the whole project and make it impossible to ask
	# "what does THIS document alone prepare me for?".
	ranked = outcome["topics"][:keep]
	document = frappe.get_doc("Wikify Exam Question", question["name"])
	document.topics = []
	for entry in ranked:
		document.append(
			"topics",
			{
				"topic_title": entry["topic_title"][:140],
				"topic_section": entry["topic_section"],
				"section": entry["section"],
				"score": entry["score"],
				"rank": entry["rank"],
				"method": entry["method"],
			},
		)
	document.mapping_status = "Mapped" if ranked else "No Match"
	document.mapped_at = frappe.utils.now()
	document.save()
	return {
		"links": len(ranked),
		"evidence": any(entry["method"] == "statutory" for entry in ranked),
		"verdict": verdict,
		"best_vector": round(outcome["best_vector"], 4),
	}


def map_project(project: str, *, papers: list[str] | None = None) -> dict:
	"""Map every extracted question in `project` to corpus topics.

	Two coverage numbers are reported and only the second one means much.

	`coverage` counts questions that got any link at all, and it runs at or near 1.0 by
	construction: a hybrid search returns its top-k whether or not the corpus contains
	anything relevant, so "mapped" only ever meant "something came back".

	`evidence_coverage` counts questions where the statutory leg fired — where ICAI's own
	answer named a provision and that provision was found in the corpus. That is the figure
	to trust, and the gap between the two is the share of the heatmap resting on similarity
	alone.
	"""
	index = section_index(project)
	markdown = markdown_index(project)
	if not index:
		frappe.throw(f"Project '{project}' has no sections to map against.")

	filters = {"project": project}
	if papers:
		filters["exam_paper"] = ["in", papers]
	questions = frappe.get_all(
		"Wikify Exam Question",
		filters=filters,
		fields=["name", "question_text", "statutory_refs", "marks", "exam_year"],
	)

	mapped = unmatched = evidence_backed = 0
	verdicts = {"covered": 0, "gap": 0, "unknown": 0}
	for question in questions:
		outcome = map_question(question, project, index, markdown)
		verdicts[outcome["verdict"]] += 1
		if outcome["links"]:
			mapped += 1
		else:
			unmatched += 1
		if outcome["evidence"]:
			evidence_backed += 1

	total = len(questions)
	return {
		"project": project,
		"questions": total,
		"mapped": mapped,
		"unmatched": unmatched,
		"coverage": round(mapped / total, 4) if total else 0.0,
		"evidence_backed": evidence_backed,
		"verdicts": verdicts,
		"evidence_coverage": round(evidence_backed / total, 4) if total else 0.0,
		"topics_available": len({topic_of(name, index)["section"] for name in index}),
	}
