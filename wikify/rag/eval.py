"""Retrieval evaluation — the POC-2 thesis measured, not asserted.

Twelve golden questions over the Demo Corpus (`GOLDEN_QUESTIONS` below) are run down
two legs and scored side by side:

- **naive**  — plain top-k vector search on the raw question, no router, no filter. This is
  what a textbook RAG pipeline does, and it is the baseline the POC argues against.
- **routed** — `rag.router.route()` picks an intent, and an exhaustive intent takes the
  `mode="filter"` leg that returns **every** matching section instead of a top-k guess.

Three numbers per question. `recall@k` and `precision@k` are the usual ones;
**completeness** is the one that carries the thesis — did the leg return *all* of the
expected sources, or only some of them? A pipeline that finds six of fifteen job
descriptions is 40% recall and 0% complete, and for "give me all the job descriptions"
only the second number describes what the user actually got.

**Why the ground truth is transcribed here rather than parsed from prose.** Expected-source
lists written as English ("**Job Description — Community Midwife** (St Aubyn); ...
(Meridian)") can only be read by a regex over prose, which rots silently the first time
someone rewords a paragraph. Instead `GOLDEN_QUESTIONS` below carries the ground truth
literally and `resolve_expected()` checks every title against the real Source Sections at
run time — a transcription typo or a renamed section is a loud failure, not a quiet zero.

Usage (from the bench root):

    bench --site wikify.localhost execute wikify.rag.eval.main
    bench --site wikify.localhost execute wikify.rag.eval.main --kwargs "{'k': 15}"

`main()` prints the scorecard as text and writes the standalone HTML scorecard to
`docs/implementation/media/rag-eval.html`.
"""

from __future__ import annotations

import html as html_escape
import os
import time
from datetime import datetime

import frappe

from wikify.rag import answer as rag_answer
from wikify.rag import search as rag_search
from wikify.rag.router import route as route_question

DEMO_PROJECT_NAME = "Demo Corpus"
DEFAULT_K = 8
MODES = ("naive", "routed")
SCORECARD_PATH = ("docs", "implementation", "media", "rag-eval.html")

JD = "staff_roles_and_responsibilities"
PAY = "administrative_policies"
TRAINING = "training_and_audits"
OVERVIEW = "other"

NORTHFIELD_JDS = [
	"Job Description — Ward Sister / Charge Nurse",
	"Job Description — Staff Nurse (Band 5)",
	"Job Description — Healthcare Assistant",
]
RIVERSIDE_JDS = [
	"Job Description — Practice Manager",
	"Job Description — General Practitioner (Salaried)",
	"Job Description — Medical Receptionist",
]
ST_AUBYN_JDS = [
	"Job Description — Consultant Obstetrician",
	"Job Description — Community Midwife",
	"Job Description — Maternity Support Worker",
]
LAKESIDE_JDS = [
	"Job Description — Theatre Scrub Practitioner",
	"Job Description — Operating Department Practitioner (Anaesthetics)",
	"Job Description — Theatre Support Worker",
]
MERIDIAN_JDS = [
	"Job Description — Registered Care Manager",
	"Job Description — Community Care Worker",
	"Job Description — Care Coordinator (Scheduling)",
]

# `expected` is the ground-truth source set (Source Section titles, unique across the demo
# corpus). `required` is the subset a semantic answer is wrong without.
# `exhaustive` marks the lane where recall is scored against the COMPLETE set
# rather than as recall@k.
GOLDEN_QUESTIONS = [
	{
		"id": "G1",
		"kind": "exhaustive",
		"question": "Give me all the job descriptions across all the PDFs.",
		"expected_intent": "exhaustive",
		"expected_section_type": JD,
		"exhaustive": True,
		"expected": [*NORTHFIELD_JDS, *RIVERSIDE_JDS, *ST_AUBYN_JDS, *LAKESIDE_JDS, *MERIDIAN_JDS],
		"required": [],
	},
	{
		"id": "G2",
		"kind": "exhaustive",
		"question": "List every pay, benefits and leave policy in the corpus.",
		"expected_intent": "exhaustive",
		"expected_section_type": PAY,
		"exhaustive": True,
		"expected": [
			"Pay Bands, Benefits and Leave Entitlements",
			"Salary Scales, Pension and Employee Benefits",
			"Rostering, On-Call Payments and Maternity Benefits",
			"Theatre Staff Pay, Overtime and Benefits Policy",
			"Reward, Mileage and Employee Benefits",
		],
		"required": [],
	},
	{
		"id": "G3",
		"kind": "exhaustive",
		"question": "What training and competency requirements does every organisation set for new staff?",
		"expected_intent": "exhaustive",
		"expected_section_type": TRAINING,
		"exhaustive": True,
		"expected": [
			"Recruitment, Qualifications and Mandatory Training",
			"Induction, Competency Framework and Annual Appraisal",
			"Midwifery Preceptorship and Skills Drills Programme",
			"Surgical Competency Sign-Off and Audit Cycle",
			"Care Certificate, Shadowing and Refresher Training Requirements",
		],
		"required": [],
	},
	{
		"id": "G4",
		"kind": "exhaustive",
		"question": "Which organisations does this corpus cover, and what does each one do?",
		"expected_intent": "exhaustive",
		"expected_section_type": OVERVIEW,
		"exhaustive": True,
		"expected": [
			"Hospital Overview and Mission",
			"Who We Are: Riverside Community Clinic",
			"St Aubyn Maternity Unit at a Glance",
			"Lakeside Surgical Centre: Purpose and Services",
			"Meridian Home Care: Company Overview",
		],
		"required": [],
	},
	{
		"id": "G5",
		"kind": "semantic",
		"question": "Who is responsible for making sure the roster is published in advance, and how far ahead?",
		"expected_intent": "semantic",
		"expected_section_type": None,
		"exhaustive": False,
		"expected": [
			"Job Description — Ward Sister / Charge Nurse",
			"Rostering, On-Call Payments and Maternity Benefits",
			"Job Description — Care Coordinator (Scheduling)",
		],
		"required": ["Job Description — Ward Sister / Charge Nurse"],
	},
	{
		"id": "G6",
		"kind": "semantic",
		"question": "What happens if the swab count doesn't match at the end of an operation?",
		"expected_intent": "semantic",
		"expected_section_type": None,
		"exhaustive": False,
		"expected": [
			"WHO Surgical Safety Checklist and Count Procedure",
			"Job Description — Theatre Scrub Practitioner",
		],
		"required": ["WHO Surgical Safety Checklist and Count Procedure"],
	},
	{
		"id": "G7",
		"kind": "semantic",
		"question": "A woman is bleeding heavily after giving birth — what should the team do?",
		"expected_intent": "semantic",
		"expected_section_type": None,
		"exhaustive": False,
		"expected": ["Obstetric Haemorrhage: Emergency Response"],
		"required": ["Obstetric Haemorrhage: Emergency Response"],
	},
	{
		"id": "G8",
		"kind": "semantic",
		"question": "How do staff who drive between visits get their travel costs back?",
		"expected_intent": "semantic",
		"expected_section_type": None,
		"exhaustive": False,
		"expected": [
			"Reward, Mileage and Employee Benefits",
			"Job Description — Community Care Worker",
		],
		"required": ["Reward, Mileage and Employee Benefits"],
	},
	{
		"id": "G9",
		"kind": "semantic",
		"question": "What has to happen before someone is allowed to work unsupervised?",
		"expected_intent": "semantic",
		"expected_section_type": None,
		"exhaustive": False,
		"expected": [
			"Recruitment, Qualifications and Mandatory Training",
			"Induction, Competency Framework and Annual Appraisal",
			"Care Certificate, Shadowing and Refresher Training Requirements",
			"Surgical Competency Sign-Off and Audit Cycle",
		],
		"required": ["Recruitment, Qualifications and Mandatory Training"],
	},
	{
		"id": "G10",
		"kind": "hybrid",
		"question": (
			"Compare how the hospital, the maternity unit and the home care provider pay for out-of-hours work."
		),
		"expected_intent": "hybrid",
		"expected_section_type": PAY,
		"exhaustive": False,
		"expected": [
			"Pay Bands, Benefits and Leave Entitlements",
			"Rostering, On-Call Payments and Maternity Benefits",
			"Reward, Mileage and Employee Benefits",
		],
		"required": [
			"Pay Bands, Benefits and Leave Entitlements",
			"Rostering, On-Call Payments and Maternity Benefits",
			"Reward, Mileage and Employee Benefits",
		],
	},
	{
		"id": "G11",
		"kind": "hybrid",
		"question": "Which roles require a driving licence?",
		"expected_intent": "hybrid",
		"expected_section_type": JD,
		"exhaustive": False,
		"expected": [
			"Job Description — Community Midwife",
			"Job Description — Registered Care Manager",
			"Job Description — Community Care Worker",
		],
		"required": [
			"Job Description — Community Midwife",
			"Job Description — Registered Care Manager",
			"Job Description — Community Care Worker",
		],
	},
	{
		"id": "G12",
		"kind": "refusal",
		"question": "What is the cyber security incident response plan for these organisations?",
		"expected_intent": "semantic",
		"expected_section_type": None,
		"exhaustive": False,
		"expected": [],
		"required": [],
		# Scored on `refused == True`, not on citations — so this is the one question that
		# needs the synthesis step, not just retrieval.
		"expect_refusal": True,
	},
]


def demo_project() -> str:
	"""The Demo Corpus project name. Autonames are not stable across sites, so look it up."""
	project = frappe.db.get_value("Wikify Project", {"project_name": DEMO_PROJECT_NAME}, "name")
	if not project:
		frappe.throw(
			f"No Wikify Project named '{DEMO_PROJECT_NAME}'. Seed it first: "
			"bench --site <site> execute wikify.tests.fixtures.demo_corpus.seed_demo_corpus"
		)
	return project


def corpus_sections(project: str) -> list[dict]:
	"""Every leaf Source Section in the project, with the fields the scorecard renders."""
	documents = frappe.get_all("Source Document", filters={"project": project}, fields=["name", "title"])
	title_of = {document["name"]: document["title"] for document in documents}
	if not title_of:
		return []
	sections = frappe.get_all(
		"Source Section",
		filters={"source_document": ["in", list(title_of)], "is_group": 0},
		fields=["name", "title", "section_type", "source_document", "page_start", "page_end"],
		order_by="source_document asc, lft asc",
	)
	for section in sections:
		section["document_title"] = title_of.get(section["source_document"], section["source_document"])
	return sections


def resolve_expected(question: dict, sections: list[dict]) -> list[dict]:
	"""Map the transcribed expected titles onto real Source Sections.

	An unresolvable or ambiguous title means the ground truth has drifted from the corpus —
	that must fail loudly here rather than quietly score as a miss later on.
	"""
	by_title: dict[str, list[dict]] = {}
	for section in sections:
		by_title.setdefault(section["title"], []).append(section)

	resolved = []
	for title in question["expected"]:
		matches = by_title.get(title) or []
		if len(matches) != 1:
			frappe.throw(
				f"Golden question {question['id']}: expected source {title!r} matched "
				f"{len(matches)} sections in the corpus (need exactly 1)."
			)
		resolved.append(matches[0])
	return resolved


def section_summary(section: dict) -> dict:
	return {
		"section": section["name"],
		"title": section["title"],
		"document": section["document_title"],
		"section_type": section.get("section_type"),
	}


def hit_summary(hit) -> dict:
	return {
		"section": hit.section,
		"title": hit.title,
		"document": hit.document_title,
		"section_type": hit.section_type,
		"score": round(float(hit.score), 6),
		"vector_rank": hit.vector_rank,
		"fts_rank": hit.fts_rank,
	}


def score_leg(expected: list[dict], required: list[str], hits: list) -> dict:
	"""recall@k, precision@k, and completeness for one retrieval leg of one question."""
	expected_names = {section["name"] for section in expected}
	returned = [hit_summary(hit) for hit in hits]
	returned_names = {row["section"] for row in returned}
	found = expected_names & returned_names
	missed = [section_summary(section) for section in expected if section["name"] not in returned_names]
	required_names = {section["name"] for section in expected if section["title"] in set(required)}

	return {
		"returned": returned,
		"returned_count": len(returned),
		"documents_covered": len({row["document"] for row in returned}),
		"correct_count": len(found),
		"recall": round(len(found) / len(expected_names), 4) if expected_names else None,
		"precision": round(len(found) / len(returned), 4) if returned else None,
		# The metric that carries the thesis: did we return ALL of them, or only some?
		"complete": bool(expected_names) and expected_names <= returned_names,
		"required_hit": required_names <= returned_names if required_names else None,
		"missed": missed,
	}


def mean(values: list[float]) -> float | None:
	numbers = [value for value in values if value is not None]
	return round(sum(numbers) / len(numbers), 4) if numbers else None


def aggregate(rows: list[dict], mode: str) -> dict:
	"""Aggregate one mode across the scored (non-refusal) questions."""
	scored = [row for row in rows if row["modes"].get(mode) and row["expected_sources"]]
	exhaustive = [row for row in scored if row["exhaustive"]]
	legs = [row["modes"][mode] for row in scored]
	exhaustive_legs = [row["modes"][mode] for row in exhaustive]
	complete = [leg for leg in legs if leg["complete"]]
	return {
		"questions": len(scored),
		"recall": mean([leg["recall"] for leg in legs]),
		"precision": mean([leg["precision"] for leg in legs]),
		"completeness": round(len(complete) / len(legs), 4) if legs else None,
		"complete_count": len(complete),
		"exhaustive_recall": mean([leg["recall"] for leg in exhaustive_legs]),
		"exhaustive_complete_count": len([leg for leg in exhaustive_legs if leg["complete"]]),
		"exhaustive_questions": len(exhaustive_legs),
		"expected_total": sum(len(row["expected_sources"]) for row in scored),
		"found_total": sum(leg["correct_count"] for leg in legs),
		"missed_total": sum(len(leg["missed"]) for leg in legs),
	}


def run_eval(
	project: str | None = None,
	k: int = DEFAULT_K,
	modes: tuple = MODES,
	question_ids: list | None = None,
	check_refusals: bool = True,
) -> dict:
	"""Run every golden question down each mode and score it. JSON-serializable throughout.

	`check_refusals` runs the full `answer()` synthesis for the refusal question only — it
	is the one question that cannot be scored from retrieval alone, and it is also the only
	one that costs a generation call.
	"""
	project = project or demo_project()
	sections = corpus_sections(project)
	if not sections:
		frappe.throw(f"Project {project} has no Source Sections to evaluate against.")

	selected = [q for q in GOLDEN_QUESTIONS if not question_ids or q["id"] in set(question_ids)]
	started = time.monotonic()
	rows = []
	for question in selected:
		expected = resolve_expected(question, sections)
		decided = route_question(question["question"], project) if "routed" in modes else None
		row = {
			"id": question["id"],
			"kind": question["kind"],
			"question": question["question"],
			"exhaustive": question["exhaustive"],
			"expected_intent": question["expected_intent"],
			"expected_section_type": question["expected_section_type"],
			"expected_sources": [section_summary(section) for section in expected],
			"required_sources": question["required"],
			"route": decided.as_dict() if decided else None,
			"intent_match": (decided.intent == question["expected_intent"]) if decided else None,
			"section_type_match": (
				(decided.section_type == question["expected_section_type"]) if decided else None
			),
			"modes": {},
			"refused": None,
			"expect_refusal": bool(question.get("expect_refusal")),
		}
		if "naive" in modes:
			naive = rag_answer.naive_retrieve(question["question"], project, rag_search.ALL_PROJECTS, limit=k)
			row["modes"]["naive"] = score_leg(expected, question["required"], naive)
		if "routed" in modes:
			routed = rag_answer.retrieve(decided, project, False, rag_search.ALL_PROJECTS, top_k=k)
			row["modes"]["routed"] = score_leg(expected, question["required"], routed)
		if check_refusals and question.get("expect_refusal"):
			synthesised = rag_answer.answer(
				question["question"], project=project, allowed_projects=rag_search.ALL_PROJECTS
			)
			row["refused"] = bool(synthesised["refused"])
		rows.append(row)

	return {
		"project": project,
		"k": k,
		"modes": list(modes),
		"generated_at": datetime.now().isoformat(timespec="seconds"),
		"took_ms": int((time.monotonic() - started) * 1000),
		"corpus": {
			"documents": len({section["source_document"] for section in sections}),
			"sections": len(sections),
		},
		"questions": rows,
		"aggregate": {mode: aggregate(rows, mode) for mode in modes},
	}


def compare_query(query: str, project: str | None = None, k: int = DEFAULT_K) -> dict:
	"""One query, both legs, plus the diff — the structure `/rag-lab` renders.

	The retrieval is `rag.answer.compare`, the same code `wikify.api.rag.compare` runs; this
	is the harness-side, permission-free wrapper that adds the document-coverage counts the
	visual comparison leans on.
	"""
	query = (query or "").strip()
	if not query:
		frappe.throw("Enter a query to compare.")
	project = project or demo_project()

	comparison = rag_answer.compare(query, project, rag_search.ALL_PROJECTS, naive_limit=k, top_k=k)
	decided = comparison["route"]
	total_documents = len(frappe.get_all("Source Document", filters={"project": project}, pluck="name"))

	return {
		"query": query,
		"project": project,
		"k": k,
		"route": decided.as_dict(),
		"naive": {
			"mode": "vector",
			"hits": [hit_summary(hit) for hit in comparison["naive"]],
			"count": len(comparison["naive"]),
			"documents_covered": len({hit.document_title for hit in comparison["naive"]}),
		},
		"routed": {
			"mode": rag_answer.MODE_FOR_INTENT[decided.intent],
			"hits": [hit_summary(hit) for hit in comparison["routed"]],
			"count": len(comparison["routed"]),
			"documents_covered": len({hit.document_title for hit in comparison["routed"]}),
		},
		"documents_in_project": total_documents,
		"missed_by_naive": [hit_summary(hit) for hit in comparison["missed_by_naive"]],
	}


SCORECARD_CSS = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body { margin: 0; padding: 40px 32px 72px; font: 15px/1.55 -apple-system, BlinkMacSystemFont,
	"Segoe UI", Roboto, Helvetica, Arial, sans-serif; color: #1b1f24; background: #f6f7f9; }
main { max-width: 1280px; margin: 0 auto; }
h1 { font-size: 28px; margin: 0 0 6px; letter-spacing: -0.01em; }
h2 { font-size: 17px; margin: 40px 0 12px; }
.sub { color: #6b7280; margin: 0 0 28px; font-size: 14px; }
/* Three across so the six cards land as a clean naive/routed grid rather than 5 + 1 orphan. */
.cards { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px; }
@media (max-width: 760px) { .cards { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
.card { background: #fff; border: 1px solid #e3e6ea; border-radius: 12px; padding: 16px 18px; }
.card .label { font-size: 12px; text-transform: uppercase; letter-spacing: .06em; color: #6b7280; }
.card .value { font-size: 30px; font-weight: 650; margin-top: 6px; letter-spacing: -0.02em; }
.card .value.naive { color: #b45309; }
.card .value.routed { color: #15803d; }
.card .foot { font-size: 12.5px; color: #6b7280; margin-top: 4px; }
table { width: 100%; border-collapse: collapse; background: #fff; border: 1px solid #e3e6ea;
	border-radius: 12px; overflow: hidden; table-layout: fixed; }
/* Fixed columns keep the two legs the same width, so the naive/routed bars are visually
   comparable even when one side carries a long list of missed sources. */
col.c-id { width: 9%; } col.c-q { width: 24%; }
col.c-leg { width: 29%; } col.c-delta { width: 9%; }
th { text-align: left; font-size: 12px; text-transform: uppercase; letter-spacing: .05em;
	color: #6b7280; padding: 11px 14px; background: #fafbfc; border-bottom: 1px solid #e3e6ea; }
td { padding: 13px 14px; border-bottom: 1px solid #eef0f3; vertical-align: top; font-size: 14px; }
tr:last-child td { border-bottom: 0; }
.qid { font-weight: 650; }
.q { color: #374151; }
.tag { display: inline-block; font-size: 11px; padding: 2px 7px; border-radius: 999px;
	background: #eef2ff; color: #4338ca; margin-right: 5px; max-width: 100%;
	overflow-wrap: anywhere; }
.badges { margin: 7px 0 0; }
/* The kind tag is one short word and must never hyphenate; only the long route tag wraps. */
.qid .tag { white-space: nowrap; margin-top: 5px; }
.tag.exh { background: #ecfdf5; color: #047857; }
.tag.warn { background: #fef3c7; color: #92400e; }
.tag.bad { background: #fee2e2; color: #b91c1c; }
.bar { position: relative; height: 9px; border-radius: 999px; background: #e9edf1; overflow: hidden;
	margin: 4px 0 3px; min-width: 120px; }
.bar span { position: absolute; inset: 0 auto 0 0; border-radius: 999px; }
.bar.naive span { background: #f59e0b; }
.bar.routed span { background: #22c55e; }
.metric { font-variant-numeric: tabular-nums; font-size: 13px; color: #374151; }
.metric b { font-weight: 650; }
.missed { margin: 6px 0 0; padding: 8px 10px; border-left: 3px solid #dc2626; background: #fef2f2;
	border-radius: 0 6px 6px 0; }
.missed .head { font-size: 11.5px; text-transform: uppercase; letter-spacing: .05em; color: #b91c1c;
	font-weight: 650; }
.missed ul { margin: 4px 0 0; padding-left: 16px; color: #7f1d1d; font-size: 12.5px;
	columns: 2; column-gap: 14px; }
.missed li { break-inside: avoid; margin-bottom: 2px; }
.none { color: #15803d; font-size: 12.5px; }
.reason { font-size: 12.5px; color: #6b7280; margin-top: 6px; font-style: italic; }
.legend { font-size: 13px; color: #6b7280; margin-top: 14px; }
.win { color: #15803d; font-weight: 650; }
.lose { color: #b91c1c; font-weight: 650; }
.flat { color: #6b7280; }
"""


def escape(value) -> str:
	return html_escape.escape("" if value is None else str(value))


def percent(value) -> str:
	return "—" if value is None else f"{value * 100:.0f}%"


def bar(value, mode: str) -> str:
	width = 0 if value is None else max(0.0, min(1.0, float(value))) * 100
	return f'<div class="bar {mode}"><span style="width:{width:.1f}%"></span></div>'


def leg_cell(leg: dict | None, mode: str, scored: bool = True) -> str:
	"""One naive/routed cell: the recall bar, the raw counts, and the misses in red.

	`scored` is false for the refusal question, which has no expected sources — an empty
	`missed` list there means "nothing was ever expected", not "we found everything", and
	printing a green "complete" would be the harness flattering itself.
	"""
	if not leg:
		return '<td class="metric">—</td>'
	if not scored:
		return (
			f'<td><div class="metric">{leg["returned_count"]} returned · '
			f"{leg['documents_covered']} docs</div>"
			'<div class="reason">not scored on sources — this question scores on refusal</div></td>'
		)
	pieces = [
		bar(leg["recall"], mode),
		f'<div class="metric">recall <b>{percent(leg["recall"])}</b> · '
		f"precision <b>{percent(leg['precision'])}</b> · "
		f"{leg['correct_count']}/{leg['correct_count'] + len(leg['missed'])} sources · "
		f"{leg['returned_count']} returned · {leg['documents_covered']} docs</div>",
	]
	if leg["missed"]:
		items = "".join(
			f"<li>{escape(row['title'])} <em>({escape(row['document'])})</em></li>" for row in leg["missed"]
		)
		pieces.append(
			f'<div class="missed"><div class="head">missed {len(leg["missed"])}</div><ul>{items}</ul></div>'
		)
	else:
		pieces.append('<div class="none">complete — nothing missed</div>')
	return f"<td>{''.join(pieces)}</td>"


def verdict_cell(row: dict) -> str:
	"""Did routed actually beat naive on this question? Reported honestly, including ties."""
	naive, routed = row["modes"].get("naive"), row["modes"].get("routed")
	if row["expect_refusal"]:
		if row["refused"] is None:
			return '<td class="flat">not checked</td>'
		return '<td class="win">refused ✓</td>' if row["refused"] else '<td class="lose">answered ✗</td>'
	if not naive or not routed:
		return '<td class="flat">—</td>'
	if routed["recall"] > naive["recall"]:
		return f'<td class="win">+{(routed["recall"] - naive["recall"]) * 100:.0f} pts</td>'
	if routed["recall"] < naive["recall"]:
		return f'<td class="lose">-{(naive["recall"] - routed["recall"]) * 100:.0f} pts</td>'
	return '<td class="flat">tie</td>'


def question_row(row: dict) -> str:
	"""One question. The route badge lives in the question cell, not the narrow id cell —
	`staff_roles_and_responsibilities` is wider than the id column will ever be."""
	route = row.get("route") or {}
	kind = f'<span class="tag{" exh" if row["exhaustive"] else ""}">{escape(row["kind"])}</span>'
	badge = ""
	if route:
		matched = row["intent_match"] and row["section_type_match"]
		badge = (
			f'<span class="tag{"" if matched else " warn"}">→ {escape(route.get("intent"))}'
			+ (f" · {escape(route.get('section_type'))}" if route.get("section_type") else "")
			+ "</span>"
		)
	reason = f'<div class="reason">{escape(route.get("reason"))}</div>' if route.get("reason") else ""
	scored = bool(row["expected_sources"])
	return (
		"<tr>"
		f'<td class="qid">{escape(row["id"])}<div>{kind}</div></td>'
		f'<td class="q">{escape(row["question"])}<div class="badges">{badge}</div>{reason}'
		f'<div class="metric">expected sources: <b>{len(row["expected_sources"])}</b></div></td>'
		f"{leg_cell(row['modes'].get('naive'), 'naive', scored)}"
		f"{leg_cell(row['modes'].get('routed'), 'routed', scored)}"
		f"{verdict_cell(row)}"
		"</tr>"
	)


def summary_cards(results: dict) -> str:
	naive = results["aggregate"].get("naive") or {}
	routed = results["aggregate"].get("routed") or {}
	cards = [
		(
			"Naive mean recall",
			percent(naive.get("recall")),
			f"{naive.get('found_total', 0)}/{naive.get('expected_total', 0)} expected sources found",
			"naive",
		),
		(
			"Routed mean recall",
			percent(routed.get("recall")),
			f"{routed.get('found_total', 0)}/{routed.get('expected_total', 0)} expected sources found",
			"routed",
		),
		(
			"Naive completeness",
			percent(naive.get("completeness")),
			f"{naive.get('complete_count', 0)}/{naive.get('questions', 0)} questions fully answered",
			"naive",
		),
		(
			"Routed completeness",
			percent(routed.get("completeness")),
			f"{routed.get('complete_count', 0)}/{routed.get('questions', 0)} questions fully answered",
			"routed",
		),
		(
			"Exhaustive lane (naive)",
			percent(naive.get("exhaustive_recall")),
			f"{naive.get('exhaustive_complete_count', 0)}/{naive.get('exhaustive_questions', 0)} complete",
			"naive",
		),
		(
			"Exhaustive lane (routed)",
			percent(routed.get("exhaustive_recall")),
			f"{routed.get('exhaustive_complete_count', 0)}/{routed.get('exhaustive_questions', 0)} complete",
			"routed",
		),
	]
	return "".join(
		f'<div class="card"><div class="label">{escape(label)}</div>'
		f'<div class="value {tone}">{value}</div><div class="foot">{escape(foot)}</div></div>'
		for label, value, foot, tone in cards
	)


def render_scorecard(results: dict) -> str:
	"""The whole scorecard as one self-contained HTML document — no CDN, no assets."""
	rows = "".join(question_row(row) for row in results["questions"])
	corpus = results["corpus"]
	return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Wikify RAG — retrieval scorecard</title>
<style>{SCORECARD_CSS}</style></head>
<body><main>
<h1>Retrieval scorecard — naive top-k vs routed</h1>
<p class="sub">Project <b>{escape(results["project"])}</b> · {corpus["documents"]} documents ·
{corpus["sections"]} content sections · k={results["k"]} ·
generated {escape(results["generated_at"])} · {results["took_ms"]} ms</p>
<div class="cards">{summary_cards(results)}</div>
<h2>Per-question</h2>
<table>
<colgroup><col class="c-id"><col class="c-q"><col class="c-leg"><col class="c-leg"><col class="c-delta"></colgroup>
<thead><tr><th>#</th><th>Question</th><th>Naive top-k (vector)</th><th>Routed</th><th>Δ</th></tr></thead>
<tbody>{rows}</tbody>
</table>
<p class="legend"><b>Completeness</b> is the metric that carries the thesis: it is true only when a
leg returned <em>every</em> expected source. A leg can score high recall and still be 0% complete —
which is exactly what "give me all the job descriptions" looks like on a naive top-k pipeline.
Sources shown in red were expected and never returned.</p>
</main></body></html>
"""


def scorecard_path() -> str:
	app_root = os.path.abspath(os.path.join(frappe.get_app_path("wikify"), ".."))
	return os.path.join(app_root, *SCORECARD_PATH)


def write_scorecard(results: dict, path: str | None = None) -> str:
	path = path or scorecard_path()
	os.makedirs(os.path.dirname(path), exist_ok=True)
	with open(path, "w", encoding="utf-8") as scorecard:
		scorecard.write(render_scorecard(results))
	return path


def format_summary(results: dict) -> str:
	"""The terminal view — the same numbers the scorecard shows, pasteable into a report."""
	lines = [
		f"Project {results['project']} · k={results['k']} · {results['took_ms']} ms",
		"",
		f"{'#':4} {'kind':11} {'exp':>4} {'naive recall':>13} {'routed recall':>14} {'naive':>6} {'routed':>7}",
	]
	for row in results["questions"]:
		naive = row["modes"].get("naive") or {}
		routed = row["modes"].get("routed") or {}
		# The refusal question has no expected sources, so "partial" would be a lie there —
		# it is scored on `refused`, printed on the next line.
		scored = bool(row["expected_sources"])
		lines.append(
			f"{row['id']:4} {row['kind']:11} {len(row['expected_sources']):>4} "
			f"{percent(naive.get('recall')):>13} {percent(routed.get('recall')):>14} "
			f"{(('complete' if naive.get('complete') else 'partial') if scored else '—'):>6} "
			f"{(('complete' if routed.get('complete') else 'partial') if scored else '—'):>7}"
		)
		if row["expect_refusal"]:
			lines.append(f"{'':4} refusal check: refused={row['refused']}")
		for missed in (routed.get("missed") or [])[:5]:
			lines.append(f"{'':4} routed MISSED: {missed['title']} ({missed['document']})")

	lines.append("")
	for mode in results["modes"]:
		totals = results["aggregate"][mode]
		lines.append(
			f"{mode:7} mean recall {percent(totals['recall'])} · mean precision "
			f"{percent(totals['precision'])} · completeness {percent(totals['completeness'])} "
			f"({totals['complete_count']}/{totals['questions']}) · exhaustive recall "
			f"{percent(totals['exhaustive_recall'])} · found {totals['found_total']}/"
			f"{totals['expected_total']} sources"
		)
	return "\n".join(lines)


def main(project: str | None = None, k: int = DEFAULT_K, path: str | None = None) -> dict:
	"""`bench --site <site> execute wikify.rag.eval.main` — run everything, write the scorecard."""
	results = run_eval(project=project, k=int(k))
	written = write_scorecard(results, path)
	print(format_summary(results))
	print(f"\nscorecard: {written}")
	return {"aggregate": results["aggregate"], "scorecard": written, "questions": len(results["questions"])}
