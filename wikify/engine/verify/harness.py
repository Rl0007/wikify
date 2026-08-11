"""POC-0 scoring engine: combine deterministic + judge into a PageScore.

Scoring is page-type aware. TEXT pages use recall/extra/table/judge. VISUAL pages
(diagrams/flowcharts/images) have near-empty extractable text, so recall/extra are
meaningless there — the composite is judge-dominant (the judge sees the image).

Ported from the POC `verify/harness.py`. Thresholds come from `engine.settings`
(user-tunable); the composite weights stay code-side in `engine.config`; the judge
receives the page image as a data URL.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from wikify.engine import config, llm, settings
from wikify.engine.verify import deterministic as det
from wikify.engine.verify.judge import judge_page


@dataclass
class PageScore:
	page_no: int
	text_recall: float
	extra_ratio: float
	table_score: float | None
	judge_score: float | None
	composite: float
	verdict: str  # pass | escalate | review
	kind: str = "text"  # text | visual
	notes: list[str] = field(default_factory=list)


def _composite(terms: dict, weights: dict) -> float:
	active = {k: v for k, v in terms.items() if k in weights and v is not None}
	total_w = sum(weights[k] for k in active)
	if total_w == 0:
		return 0.0
	return round(sum(weights[k] * active[k] for k in active) / total_w, 3)


def get_verdict(composite: float) -> str:
	"""The pass/escalate/review badge for a composite score. Public because the verdict
	must follow whichever composite the user is actually reading — `store.set_canonical`
	re-derives it from the canonical composite once a remediation is adopted."""
	if composite >= float(settings.get("pass_threshold")):
		return "pass"
	if composite >= float(settings.get("escalate_threshold")):
		return "escalate"
	return "review"


def _run_judge(image_data_url: str, markdown: str, notes: list[str]) -> float | None:
	"""Judge with one retry if the reply is unparseable."""
	for _attempt in range(2):
		try:
			jr = judge_page(image_data_url, markdown)
		except Exception as e:  # best-effort
			notes.append(f"judge failed: {e}")
			return None
		if jr.get("judge_score") is not None:
			if jr.get("note"):
				notes.append(f"judge: {jr['note']}")
			return jr["judge_score"]
	notes.append("judge unparseable after retry")
	return None


def score_page(
	page_no: int,
	markdown: str,
	ground_truth: str,
	*,
	image_data_url: str | None = None,
	use_judge: bool = False,
	page_kind: str = "text",
) -> PageScore:
	recall = det.text_recall(ground_truth, markdown)
	extra = det.extra_ratio(ground_truth, markdown)
	tscore = det.table_score(ground_truth, markdown)

	notes: list[str] = []
	judge_score = None
	if use_judge and image_data_url and llm.has_openrouter():
		judge_score = _run_judge(image_data_url, markdown, notes)

	if page_kind == "visual":
		# Text GT is unreliable on diagrams — judge dominates; recall/extra excluded.
		composite = _composite({"judge_score": judge_score, "table_score": tscore}, config.VISUAL_WEIGHTS)
		if judge_score is None:
			notes.append("visual page but no judge score — composite unreliable")
	else:
		composite = _composite(
			{
				"text_recall": recall,
				"not_hallucinated": 1.0 - extra,
				"table_score": tscore,
				"judge_score": judge_score,
			},
			config.WEIGHTS,
		)
		if recall < 0.85:
			notes.append("low text recall — possible dropped content")
		if extra > 0.25:
			notes.append("high extra ratio — possible hallucination")
		if tscore == 0.0:
			notes.append("table present but not reproduced")
		# Structure-mangling artifacts (picture-omitted wrappers, <br> blobs, broken
		# tables) aren't visible to recall/extra — penalize so the page gets flagged.
		artifacts = det.parser_artifacts(markdown)
		if artifacts:
			composite = round(composite * 0.7, 3)
			notes.append("parser artifacts (" + ", ".join(artifacts) + ") — needs cleanup")

	return PageScore(
		page_no=page_no,
		text_recall=round(recall, 3),
		extra_ratio=round(extra, 3),
		table_score=None if tscore is None else round(tscore, 3),
		judge_score=judge_score,
		composite=composite,
		verdict=get_verdict(composite),
		kind=page_kind,
		notes=notes,
	)
