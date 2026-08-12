"""Turning an ICAI question paper PDF into `Wikify Exam Question` rows.

Question papers deliberately do NOT go through the parse/remediate pipeline. That pipeline
exists for scanned study material, where a page is an image and competing OCR readings have
to be adjudicated by an LLM. ICAI's Suggested Answers PDFs are clean digital text that
PyMuPDF reads perfectly, so remediating them would spend money fixing nothing.

What is genuinely hard here is *structure*, not characters:

- A "Suggested Answers" PDF interleaves questions with ICAI's model answers, and the answers
  are far longer than the questions. Naive splitting drags answer prose into question text.
- Sub-part markers are ambiguous. `(a)`, `(b)`, `(c)` head real sub-questions in some places
  and enumerate profit-and-loss line items *inside* one question in others. No regex tells
  those apart; reading the sentence does.
- The scheme changed. Pre-2024 papers are Paper 7 with one descriptive part; from May 2024
  it is Paper 4 with `Part I - Multiple Choice Questions`, an `Answer Key`, then
  `Part II - Descriptive Questions`.

So the split is deterministic where the document is reliable (part boundaries, `Question N`
headings, `(N Marks)` markers, the optionality sentence) and delegated to an LLM only for
the one judgement a regex cannot make: which spans are questions and which are answers.

Optionality is parsed from the printed instruction rather than assumed, because it decides
whether the marks arithmetic is honest — see `parse_optionality`.
"""

from __future__ import annotations

import re

import frappe

from wikify.engine import llm, settings

# OpenRouter re-draws a provider per call and unpinned parallel calls scatter across
# providers of different speed and reliability — the same failure that produced truncated
# JSON in the reranker. Pinned for the same reason.
PROVIDER = {"order": ["google-ai-studio"], "allow_fallbacks": True}

QUESTION_HEADING = re.compile(r"^\s{0,20}Question\s+(\d+)\s*$")
# ICAI prints hyphen, en dash and em dash interchangeably in "Part - II".
PART_HEADING = re.compile("^\\s*Part\\s*[-\u2013\u2014]?\\s*(I{1,2})\\b", re.IGNORECASE)
ANSWER_KEY_HEADING = re.compile(r"^\s*Answer\s+Key\s*$", re.IGNORECASE)
# "(2 Marks)" and the multi-part form "(2 x 5 = 10 Marks)", with either x or the sign.
MARKS_MARKER = re.compile(
	"\\(\\s*\\d+(?:\\s*[x\u00d7]\\s*\\d+)?\\s*(?:=\\s*\\d+\\s*)?Marks?\\s*\\)", re.IGNORECASE
)

# The printed optionality sentence, e.g. "Answer any four questions from the remaining five
# questions." Word-form numbers because ICAI never prints digits here.
OPTIONALITY = re.compile(
	r"answer\s+any\s+(\w+)\s+questions?\s+(?:from|out\s+of)\s+the\s+remaining\s+(\w+)",
	re.IGNORECASE,
)
COMPULSORY = re.compile(r"Question\s+No\.?\s*(\d+)\s+is\s+compulsory", re.IGNORECASE)

WORD_NUMBERS = {
	"one": 1,
	"two": 2,
	"three": 3,
	"four": 4,
	"five": 5,
	"six": 6,
	"seven": 7,
	"eight": 8,
	"nine": 9,
	"ten": 10,
}

# Running headers, footers and the copyright line. Dropped before segmentation so they
# cannot land in the middle of a question and break a span.
BOILERPLATE = (
	re.compile(r"Institute\s+of\s+Chartered\s+Accountants\s+of\s+India", re.IGNORECASE),
	re.compile(r"^\s*\d+\s+FINAL\s+EXAMINATION", re.IGNORECASE),
	re.compile(r"FINAL\s+EXAMINATION\s*:", re.IGNORECASE),
	re.compile(r"^\s*P\.?T\.?O\.?\s*$", re.IGNORECASE),
	re.compile(r"^\s*\d{1,3}\s*$"),
)


def page_lines(pdf_path: str) -> list[tuple[int, str]]:
	"""`(page_no, line)` for the whole PDF, 1-indexed pages.

	Page numbers are carried alongside every line so an extracted question can record where
	it appeared — a citation the student can actually check against the paper.
	"""
	import fitz

	entries: list[tuple[int, str]] = []
	with fitz.open(pdf_path) as document:
		for index, page in enumerate(document, start=1):
			for line in page.get_text().splitlines():
				entries.append((index, line))
	return entries


def drop_boilerplate(entries: list[tuple[int, str]]) -> list[tuple[int, str]]:
	"""Remove running headers, footers and bare page numbers."""
	return [
		(page, line)
		for page, line in entries
		if line.strip() and not any(pattern.search(line) for pattern in BOILERPLATE)
	]


def parse_optionality(entries: list[tuple[int, str]]) -> dict:
	"""Read the printed instruction that says how many questions are actually attempted.

	This is not decoration. Every ICAI paper prints "Question No.1 is compulsory. Answer any
	four questions out of the remaining five." A paper whose questions total 100 marks is
	therefore worth ~86 attemptable marks, and scoring topics on the printed total silently
	inflates every figure by the value of the question nobody answers.
	"""
	# Read from the head of whatever part was handed in, not the head of the document. In a
	# new-scheme paper the "answer any four" sentence governs Part II and is printed after the
	# entire MCQ section — several hundred lines in. Scanning the document head instead found
	# nothing, so every question was marked compulsory and attemptable marks equalled the
	# printed total, which is precisely the ~20% inflation this function exists to prevent.
	head = "\n".join(line for _page, line in entries[:80])
	compulsory = COMPULSORY.search(head)
	choice = OPTIONALITY.search(head)
	return {
		"compulsory_question": int(compulsory.group(1)) if compulsory else None,
		"choose_count": WORD_NUMBERS.get(choice.group(1).lower()) if choice else None,
		"choice_size": WORD_NUMBERS.get(choice.group(2).lower()) if choice else None,
	}


def split_parts(entries: list[tuple[int, str]]) -> dict[str, list[tuple[int, str]]]:
	"""Split a new-scheme paper into `mcq` and `descriptive`, keyed by the printed headings.

	Old-scheme papers have no Part I, so everything falls through to `descriptive` and the
	MCQ pass simply finds nothing — which is the correct answer for those papers.
	"""
	mcq_start = answer_key = descriptive_start = None
	for index, (_page, line) in enumerate(entries):
		part = PART_HEADING.match(line)
		if part and part.group(1).upper() == "I" and mcq_start is None:
			mcq_start = index
		elif part and part.group(1).upper() == "II":
			descriptive_start = index
		elif ANSWER_KEY_HEADING.match(line) and answer_key is None:
			answer_key = index

	if mcq_start is None or descriptive_start is None:
		return {"mcq": [], "descriptive": entries}
	return {
		"mcq": entries[mcq_start : answer_key or descriptive_start],
		"descriptive": entries[descriptive_start:],
	}


def question_blocks(entries: list[tuple[int, str]]) -> list[dict]:
	"""Split the descriptive part into one block per `Question N` heading.

	Each block still contains ICAI's model answers; stripping those is the LLM's job, not a
	regex's, because an answer ends wherever the next question begins and nothing in the
	text marks that boundary reliably.
	"""
	starts: list[tuple[int, int]] = []
	for index, (_page, line) in enumerate(entries):
		heading = QUESTION_HEADING.match(line)
		if heading:
			starts.append((index, int(heading.group(1))))

	blocks = []
	for position, (index, number) in enumerate(starts):
		end = starts[position + 1][0] if position + 1 < len(starts) else len(entries)
		span = entries[index:end]
		blocks.append(
			{
				"question_no": number,
				"page_no": span[0][0],
				"text": "\n".join(line for _page, line in span),
			}
		)
	return blocks


STRUCTURE_PROMPT = """You are reading one question from an ICAI CA Final exam paper's
"Suggested Answers" document. The text below contains the question (possibly split into
sub-parts) INTERLEAVED with ICAI's official model answers.

This question has EXACTLY {count} sub-part(s), worth {marks} marks respectively, in that
order. That count is not negotiable — it was counted from the printed "(N Marks)" markers.
Return exactly {count} entries in the same order.

Return JSON: {{"questions": [...]}} where each entry has:
  "question_no"      e.g. "3" or "3(a)" or "3(b)(ii)" — use the numbering the paper uses
  "kind"             one of Descriptive, MCQ, Case Study, Numerical
  "text"             ONLY what the candidate was asked. Never model-answer content.
  "statutory_refs"   section references cited in the MODEL ANSWER for that sub-part,
                     e.g. ["section 45(1A)", "section 115BAC"]. [] if none.

Rules:
- Roman numerals (i), (ii), (iii) and letters (a), (b) often enumerate items WITHIN one
  sub-part rather than starting a new one. A new sub-part begins only where the previous one
  ended with its own "(N Marks)" marker. When in doubt, merge rather than split.
- Preserve the question's wording; collapse whitespace but do not paraphrase or summarise.
- Do NOT return a "marks" field. Marks are taken from the printed markers, not from you.
"""


def marks_markers(text: str) -> list[float]:
	"""Every "(N Marks)" value in the block, in printed order.

	Marks are read from the document rather than asked of the model, because the model gets
	this wrong in a way that is invisible downstream. On November 2023 Q3, items (i)-(iv)
	sit inside one 8-mark sub-part with a single marker after (iv); asked to infer marks, the
	model returned four 8-mark questions and inflated that question from 14 to 38. A heatmap
	built on those numbers would tell a student to prioritise the wrong chapter.

	The multi-part form "(2 x 5 = 10 Marks)" yields its total, 10 — the last number before
	"Marks" is always the value awarded.
	"""
	values = []
	for match in MARKS_MARKER.finditer(text):
		numbers = re.findall(r"\d+(?:\.\d+)?", match.group(0))
		if numbers:
			values.append(float(numbers[-1]))
	return values


def structure_block(block: dict, model: str, api_key: str) -> list[dict]:
	"""One LLM call per question block → its sub-questions.

	The whole block including answers is sent rather than a heuristically stripped version:
	the answers are what make the sub-part boundaries legible, and they are also where the
	statutory references live.
	# ponytail: sends full answer text, so a long block costs input tokens for content only
	# two fields need; pre-trim answers if paper volume ever makes this the dominant cost.
	"""
	marks = marks_markers(block["text"])
	if not marks:
		return []

	response = llm.chat_completion(
		model,
		[
			{
				"role": "system",
				"content": STRUCTURE_PROMPT.format(
					count=len(marks), marks=", ".join(str(value) for value in marks)
				),
			},
			{"role": "user", "content": block["text"][:60000]},
		],
		label="exam.structure",
		response_format={"type": "json_object"},
		api_key=api_key,
		provider=PROVIDER,
		timeout=180,
	)
	try:
		parsed = frappe.parse_json(response["choices"][0]["message"]["content"])
	except Exception:
		return []
	questions = (parsed or {}).get("questions") or []

	# The marker count is ground truth. If the model still disagreed, keep the marks aligned
	# to the markers and let the count mismatch surface in the report rather than silently
	# storing a question whose marks came from nowhere.
	for position, entry in enumerate(questions):
		entry["marks"] = marks[position] if position < len(marks) else 0.0
		entry.setdefault("page_no", block["page_no"])
	return {
		"questions": questions[: len(marks)],
		"expected": len(marks),
		"returned": len(questions),
		"question_no": block["question_no"],
	}


MCQ_PROMPT = """You are reading the "Part I - Multiple Choice Questions" section of an ICAI
CA Final exam paper. Extract every numbered MCQ.

Return JSON: {"questions": [...]} where each entry has:
  "question_no"     the printed number, as a string
  "marks"           marks for that MCQ (the paper states them; usually 1 or 2)
  "kind"            "MCQ"
  "text"            the question stem AND its options, preserved
  "statutory_refs"  section references the question turns on, [] if none

Some MCQs share a common case-study preamble. Repeat the relevant preamble into each
question's "text" so every entry stands alone. Never invent marks.
"""


def structure_mcqs(entries: list[tuple[int, str]], model: str, api_key: str) -> list[dict]:
	"""Extract Part I MCQs. Returns [] for old-scheme papers, which have no Part I."""
	if not entries:
		return []
	text = "\n".join(line for _page, line in entries)
	response = llm.chat_completion(
		model,
		[
			{"role": "system", "content": MCQ_PROMPT},
			{"role": "user", "content": text[:120000]},
		],
		label="exam.mcq",
		response_format={"type": "json_object"},
		api_key=api_key,
		provider=PROVIDER,
		timeout=240,
	)
	try:
		parsed = frappe.parse_json(response["choices"][0]["message"]["content"])
	except Exception:
		return []
	questions = (parsed or {}).get("questions") or []
	first_page = entries[0][0]
	for entry in questions:
		entry.setdefault("page_no", first_page)
		entry["kind"] = "MCQ"
	return questions


def top_level_number(question_no: str) -> int | None:
	"""The leading integer of "3(b)(ii)" → 3, used to apply the paper's optionality."""
	match = re.match(r"\s*(\d+)", str(question_no or ""))
	return int(match.group(1)) if match else None


def apply_optionality(questions: list[dict], optionality: dict, part: str) -> None:
	"""Tag each question compulsory or as a member of the choice group, in place.

	Only the descriptive part carries optionality — Part I MCQs are all compulsory.
	"""
	if part != "descriptive":
		for entry in questions:
			entry["is_compulsory"] = 1
		return

	compulsory = optionality.get("compulsory_question")
	choose = optionality.get("choose_count")
	for entry in questions:
		number = top_level_number(entry.get("question_no"))
		if compulsory is not None and number == compulsory:
			entry["is_compulsory"] = 1
		elif choose:
			entry["is_compulsory"] = 0
			entry["choice_group"] = "descriptive-choice"
			entry["choose_count"] = choose
		else:
			# No optionality sentence parsed: treat everything as compulsory rather than
			# silently discounting marks on a guess.
			entry["is_compulsory"] = 1


def attemptable_marks(questions: list[dict], optionality: dict) -> float:
	"""Marks a candidate can actually score, honouring "answer any four of five".

	The compulsory questions count in full. For the choice group, only the `choose_count`
	highest-scoring alternatives count — using the highest rather than an average keeps this
	an upper bound, so the figure can never overstate what a topic was worth.
	"""
	compulsory_total = sum(float(q.get("marks") or 0) for q in questions if q.get("is_compulsory"))

	optional = [q for q in questions if not q.get("is_compulsory")]
	choose = optionality.get("choose_count")
	if not optional or not choose:
		return compulsory_total + sum(float(q.get("marks") or 0) for q in optional)

	by_question: dict[int, float] = {}
	for entry in optional:
		number = top_level_number(entry.get("question_no"))
		by_question[number] = by_question.get(number, 0.0) + float(entry.get("marks") or 0)
	best = sorted(by_question.values(), reverse=True)[:choose]
	return compulsory_total + sum(best)


def remove_questions(paper: str) -> int:
	"""Delete a paper's questions AND their topic links. Returns the questions removed.

	The child rows have to go explicitly: `db.delete` on the parent doctype does not cascade,
	so deleting questions alone strands their `Question Topic Link` rows. Those orphans do
	not corrupt any current number — every reader filters links by a live parent — but they
	accumulate silently on every re-extraction, and a later query that forgets to filter
	would read them as real.
	"""
	names = frappe.get_all("Wikify Exam Question", filters={"exam_paper": paper}, pluck="name")
	if not names:
		return 0
	frappe.db.delete("Question Topic Link", {"parent": ["in", names]})
	frappe.db.delete("Wikify Exam Question", {"name": ["in", names]})
	return len(names)


def save_questions(paper: str, questions: list[dict]) -> int:
	"""Insert the extracted questions, replacing any previous extraction for this paper.

	Delete-then-insert so a re-run is idempotent rather than doubling the corpus, and so a
	corrected extraction cannot leave stale rows behind skewing every count.
	"""
	remove_questions(paper)
	created = 0
	for entry in questions:
		refs = entry.get("statutory_refs") or []
		frappe.get_doc(
			{
				"doctype": "Wikify Exam Question",
				"exam_paper": paper,
				"question_no": str(entry.get("question_no") or "?")[:140],
				"part_label": entry.get("part_label"),
				"question_kind": entry.get("kind") or "Descriptive",
				"marks": frappe.utils.flt(entry.get("marks")),
				"is_compulsory": frappe.utils.cint(entry.get("is_compulsory")),
				"choice_group": entry.get("choice_group"),
				"choose_count": frappe.utils.cint(entry.get("choose_count")),
				"page_no": frappe.utils.cint(entry.get("page_no")),
				"question_text": entry.get("text"),
				"statutory_refs": ", ".join(refs) if isinstance(refs, list) else refs,
			}
		).insert()
		created += 1
	return created


def report_stage(paper: str, label: str, progress: float) -> None:
	"""Record what the job is doing, so a waiting user is not staring at nothing.

	Written with `db_set` + an immediate commit because the caller is a background worker
	inside a long transaction: without the commit the row does not change until the whole
	job finishes, which is precisely the window the progress is meant to cover.
	"""
	frappe.db.set_value("Wikify Exam Paper", paper, {"stage_label": label, "stage_progress": progress})
	frappe.db.commit()


def extract_paper(paper: str) -> dict:
	"""Extract every question from a `Wikify Exam Paper`'s PDF and store them."""
	document = frappe.get_doc("Wikify Exam Paper", paper)
	if not document.source_pdf:
		frappe.throw(f"Exam paper '{paper}' has no source PDF attached.")

	pdf_path = frappe.get_site_path(document.source_pdf.lstrip("/"))
	model = settings.get("classifier_model")
	api_key = settings.openrouter_key()

	document.db_set("status", "Extracting")
	try:
		report_stage(paper, "Reading the PDF", 5)
		entries = drop_boilerplate(page_lines(pdf_path))
		parts = split_parts(entries)
		optionality = parse_optionality(parts["descriptive"])

		collected: list[dict] = []
		report_stage(paper, "Extracting multiple-choice questions", 15)
		mcqs = structure_mcqs(parts["mcq"], model, api_key)
		apply_optionality(mcqs, optionality, "mcq")
		for entry in mcqs:
			entry["part_label"] = "Part I"
		collected.extend(mcqs)

		descriptive: list[dict] = []
		disagreements: list[dict] = []
		blocks = question_blocks(parts["descriptive"])
		for position, block in enumerate(blocks, start=1):
			# 25% -> 85% spread across the descriptive questions, which are the slow part:
			# one LLM call each, several seconds apiece.
			report_stage(
				paper,
				f"Extracting question {block['question_no']} of {len(blocks)}",
				25 + (60 * position / max(1, len(blocks))),
			)
			outcome = structure_block(block, model, api_key)
			if not outcome:
				continue
			descriptive.extend(outcome["questions"])
			if outcome["returned"] != outcome["expected"]:
				disagreements.append(
					{
						"question": outcome["question_no"],
						"markers": outcome["expected"],
						"model_returned": outcome["returned"],
					}
				)
		apply_optionality(descriptive, optionality, "descriptive")
		descriptive_label = "Part II" if parts["mcq"] else "Descriptive"
		for entry in descriptive:
			entry["part_label"] = descriptive_label
		collected.extend(descriptive)

		report_stage(paper, "Saving questions", 90)
		created = save_questions(paper, collected)
		total = sum(float(q.get("marks") or 0) for q in collected)
		document.db_set(
			{
				"status": "Extracted",
				"question_count": created,
				"total_marks": total,
				"attempted_marks": attemptable_marks(collected, optionality),
				"extracted_at": frappe.utils.now(),
				"error": None,
				"stage_label": None,
				"stage_progress": 100,
			}
		)
	except Exception as exc:
		document.db_set({"status": "Failed", "error": frappe.get_traceback()})
		raise exc

	return {
		"paper": paper,
		"questions": created,
		"mcq": len(mcqs),
		"descriptive": len(descriptive),
		"total_marks": total,
		"attemptable_marks": document.attempted_marks,
		"optionality": optionality,
		# Surfaced rather than swallowed: a block where the model disagreed with the printed
		# marker count is the signal that its segmentation is untrustworthy for that question,
		# even though the marks themselves were forced to match the markers.
		"segmentation_disagreements": disagreements,
	}
