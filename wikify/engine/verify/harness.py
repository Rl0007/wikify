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
	verdict: str
	kind: str = "text"
	notes: list[str] = field(default_factory=list)


def _composite(terms: dict, weights: dict) -> float:
	active = {k: v for k, v in terms.items() if k in weights and v is not None}
	total_w = sum(weights[k] for k in active)
	if total_w == 0:
		return 0.0
	return round(sum(weights[k] * active[k] for k in active) / total_w, 3)


def get_verdict(composite: float) -> str:
	if composite >= float(settings.get("pass_threshold")):
		return "pass"
	if composite >= float(settings.get("escalate_threshold")):
		return "escalate"
	return "review"


def _run_judge(image_data_url: str, markdown: str, notes: list[str]) -> float | None:
	for _attempt in range(2):
		try:
			jr = judge_page(image_data_url, markdown)
		except Exception as e:
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
