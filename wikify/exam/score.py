"""Turning mapped questions into the topic-by-year matrix and an importance ranking.

The product's actual claim lives here — "study these topics first" — so the arithmetic is
kept explicit and every number that feeds it is reported alongside, never collapsed into one
opaque score.

Three decisions worth stating outright, because each one is a place where a plausible
alternative would quietly mislead a student:

**Marks are conserved.** A question maps to up to three topics. Awarding each of them the
question's full marks would triple-count a 14-mark question into 42, so marks are split
across a question's topics in proportion to their match score. The matrix therefore sums to
the papers' own mark totals, which makes it checkable against the printed papers.

**Optionality is honoured.** Every ICAI paper prints "Question No.1 is compulsory. Answer
any four questions out of the remaining five." Scoring the printed total counts a question
nobody answers and inflates the whole grid by roughly a fifth.

**Recency is a weighting, not a filter.** Older papers still count, at `DECAY ** age`. A
topic examined every year until 2019 and never since should sink, not vanish — the syllabus
may have moved, but the student still deserves to see the shape of it.
"""

from __future__ import annotations

from collections import defaultdict

import frappe

# Weight applied per year of age. 0.9 halves a topic's contribution in ~6.6 years, which
# tracks how fast the CA syllabus turns over without erasing the older half of the window.
DECAY = 0.9


def topic_key(title: str) -> str:
	"""Normalised topic title, used as the heatmap row identity.

	Case and whitespace only — deliberately not fuzzy. Merging "CAPITAL GAINS" with
	"Capital Gains" is safe; merging near-matches would silently fuse two real chapters.
	"""
	return " ".join((title or "").split()).casefold()


def reference_year() -> int:
	"""The year decay is measured back from — today, not the newest paper in the data.

	Anchoring to the newest paper would make every topic's score jump the moment a new
	paper is imported, even though nothing about the older papers changed.
	"""
	return frappe.utils.now_datetime().year


def section_documents(project: str) -> dict[str, str]:
	"""`section -> source_document` for the project, so links can be scoped to sources."""
	documents = frappe.get_all("Source Document", filters={"project": project}, pluck="name")
	if not documents:
		return {}
	rows = frappe.get_all(
		"Source Section",
		filters={"source_document": ["in", documents]},
		fields=["name", "source_document"],
	)
	return {row["name"]: row["source_document"] for row in rows}


def cited_provisions(question: dict) -> list[str]:
	"""The provisions ICAI cited for this question, as printed labels."""
	return [ref.strip() for ref in (question.get("statutory_refs") or "").split(",") if ref.strip()]


def question_rows(project: str) -> list[dict]:
	"""Every mapped question in `project` with the fields scoring needs.

	Two queries — questions, then their topic links — rather than one per question. A
	`get_doc` per question would be 160 loads to read a child table.
	"""
	questions = frappe.get_all(
		"Wikify Exam Question",
		filters={"project": project, "mapping_status": "Mapped"},
		fields=[
			"name",
			"exam_paper",
			"question_no",
			"marks",
			"exam_year",
			"is_compulsory",
			"choice_group",
			"choose_count",
			"question_kind",
			"assessment_year",
			# Required by the gap test: without it `cited_provisions` reads empty for every
			# question and no gap can ever be reported.
			"statutory_refs",
		],
	)
	if not questions:
		return []

	links = frappe.get_all(
		"Question Topic Link",
		filters={"parent": ["in", [row["name"] for row in questions]]},
		fields=["parent", "topic_section", "topic_title", "section", "score", "rank", "method"],
		order_by="rank asc",
	)
	by_question = defaultdict(list)
	for link in links:
		by_question[link["parent"]].append(link)
	for row in questions:
		row["topics"] = by_question.get(row["name"], [])
	return questions


def attemptable_weight(questions: list[dict]) -> dict[str, float]:
	"""Per-question multiplier in [0, 1] reflecting how likely it is to be attempted.

	A compulsory question is worth 1. In a choice group where `choose_count` of `group_size`
	are answered, each member is worth `choose_count / group_size` — the expected fraction,
	which keeps the matrix's totals equal to the attemptable marks rather than the printed
	ones, and avoids pretending we know which four of five a candidate picked.
	"""
	group_sizes: dict[tuple, set] = defaultdict(set)
	for row in questions:
		if row.get("choice_group"):
			key = (row["exam_paper"], row["choice_group"])
			# Group size counts distinct top-level questions, not sub-parts: "any four of the
			# remaining five" is about questions 2-6, not about their (a)/(b) pieces.
			group_sizes[key].add(str(row["question_no"]).split("(")[0].strip())

	weights = {}
	for row in questions:
		if row.get("is_compulsory") or not row.get("choice_group"):
			weights[row["name"]] = 1.0
			continue
		key = (row["exam_paper"], row["choice_group"])
		size = len(group_sizes.get(key) or ())
		choose = row.get("choose_count") or 0
		weights[row["name"]] = min(1.0, choose / size) if size and choose else 1.0
	return weights


def matrix(project: str, sources: list[str] | None = None) -> dict:
	"""The topic-by-year grid, a ranked topic list, and the gaps — all scoped to `sources`.

	`sources` is a list of `Source Document` names; omit it for the whole project. Scoping is
	applied HERE rather than at mapping time so switching scope is instant and free: mapping
	stored every match it found, and this function decides which of them the reader has
	actually got.

	Two results come out of one pass, and they answer different questions:

	- `topics` — what these sources DO prepare you for, weighted by marks and recency.
	- `gaps` — provisions ICAI examined that these sources do NOT contain. A question counts
	  as a gap only when the examiner named provisions in the model answer and none of them
	  appear in the scoped sources. That is a literal, checkable test; similarity is not
	  used for it, having been measured as unable to tell a tax question from a question
	  about lean manufacturing.

	A question citing no provision at all is neither: it keeps its similarity mapping and is
	flagged `similarity only`, because absence of a citation is not evidence of absence.
	"""
	questions = question_rows(project)
	if not questions:
		return {"project": project, "topics": [], "years": [], "cells": {}, "totals": {}}

	weights = attemptable_weight(questions)
	current = reference_year()
	in_scope = set(sources) if sources else None
	documents = section_documents(project) if in_scope else {}

	def link_in_scope(link: dict) -> bool:
		if in_scope is None:
			return True
		return documents.get(link.get("section")) in in_scope

	gap_buckets: dict[str, dict] = defaultdict(
		lambda: {"marks": 0.0, "score": 0.0, "questions": 0.0, "years": set(), "examples": []}
	)
	gap_questions = 0

	cells: dict[tuple[str, int], dict] = defaultdict(
		lambda: {"marks": 0.0, "attemptable": 0.0, "questions": 0, "score": 0.0}
	)
	titles: dict[str, str] = {}
	sections_for_topic: dict[str, set] = defaultdict(set)
	years: set[int] = set()
	evidence_topics: set[str] = set()

	for row in questions:
		year = int(row.get("exam_year") or 0)
		if not year:
			continue

		marks = float(row.get("marks") or 0)
		weight = weights.get(row["name"], 1.0)
		decay = DECAY ** max(0, current - year)

		scoped = [link for link in row["topics"] if link_in_scope(link)]
		provisions = cited_provisions(row)
		has_evidence = any(link.get("method") == "statutory" for link in scoped)

		# The examiner named provisions and none of them live in the scoped sources: this is
		# a gap, and it must NOT be folded into the nearest-looking chapter. Doing so would
		# inflate that chapter and bury the one fact worth surfacing.
		if provisions and not has_evidence:
			gap_questions += 1
			share = 1 / len(provisions)
			for label in provisions:
				bucket = gap_buckets[label.casefold()]
				bucket["label"] = label
				bucket["marks"] += marks * share
				bucket["score"] += marks * share * weight * decay
				bucket["questions"] += share
				bucket["years"].add(year)
				if len(bucket["examples"]) < 3:
					bucket["examples"].append(
						{
							"question": row["name"],
							"question_no": row["question_no"],
							"exam_year": year,
							"marks": marks,
						}
					)
			continue

		topics = scoped
		if not topics:
			continue
		years.add(year)

		# Split the question's marks across its topics by match score so the grid conserves
		# marks. Equal split when scores are absent or zero, rather than dropping the row.
		total_score = sum(float(link.get("score") or 0) for link in topics)
		for link in topics:
			share = (float(link.get("score") or 0) / total_score) if total_score else (1 / len(topics))
			# Key rows by normalised title, not by section name. The corpus contains the same
			# chapter heading at more than one place in the tree, which as distinct sections
			# produced two heatmap rows both labelled "Assessment of Various Entities", each
			# holding half the truth. A student reading that would under-weight the topic twice.
			topic = topic_key(link.get("topic_title") or link["topic_section"])
			titles.setdefault(topic, (link.get("topic_title") or link["topic_section"]).strip())
			sections_for_topic[topic].add(link["topic_section"])
			if link.get("method") == "statutory":
				evidence_topics.add(topic)
			cell = cells[(topic, year)]
			cell["marks"] += marks * share
			cell["attemptable"] += marks * share * weight
			cell["questions"] += share
			cell["score"] += marks * share * weight * decay

	summary: dict[str, dict] = defaultdict(
		lambda: {"marks": 0.0, "attemptable": 0.0, "questions": 0.0, "score": 0.0, "years": set()}
	)
	for (topic, year), cell in cells.items():
		entry = summary[topic]
		entry["marks"] += cell["marks"]
		entry["attemptable"] += cell["attemptable"]
		entry["questions"] += cell["questions"]
		entry["score"] += cell["score"]
		entry["years"].add(year)

	topics = [
		{
			"topic": topic,
			"title": titles.get(topic, topic),
			"sections": sorted(sections_for_topic.get(topic, ())),
			"marks": round(entry["marks"], 2),
			"attemptable": round(entry["attemptable"], 2),
			"questions": round(entry["questions"], 2),
			"score": round(entry["score"], 2),
			"years_present": len(entry["years"]),
			"last_seen": max(entry["years"]),
			"evidence_backed": topic in evidence_topics,
		}
		for topic, entry in summary.items()
	]
	topics.sort(key=lambda entry: entry["score"], reverse=True)

	uncovered = [
		{
			"label": bucket.get("label", key),
			"marks": round(bucket["marks"], 2),
			"score": round(bucket["score"], 2),
			"questions": round(bucket["questions"], 2),
			"years_present": len(bucket["years"]),
			"last_seen": max(bucket["years"]) if bucket["years"] else None,
			"examples": bucket["examples"],
		}
		for key, bucket in gap_buckets.items()
	]
	uncovered.sort(key=lambda entry: entry["score"], reverse=True)

	return {
		"project": project,
		"reference_year": current,
		"decay": DECAY,
		"years": sorted(years),
		"sources": sorted(in_scope) if in_scope else [],
		"topics": topics,
		"gaps": uncovered,
		"cells": {
			f"{topic}|{year}": {k: round(v, 2) for k, v in cell.items()}
			for (topic, year), cell in cells.items()
		},
		"totals": {
			"questions": len(questions),
			"marks": round(sum(float(row.get("marks") or 0) for row in questions), 2),
			"attemptable": round(
				sum(float(row.get("marks") or 0) * weights.get(row["name"], 1.0) for row in questions), 2
			),
			"topics": len(topics),
			"gap_questions": gap_questions,
			"uncovered_questions": round(sum(row["questions"] for row in uncovered), 2),
			"uncovered_marks": round(sum(row["marks"] for row in uncovered), 2),
		},
	}
