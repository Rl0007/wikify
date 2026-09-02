"""What asks actually cost, read back off the log.

`history.py` writes one answer row per turn carrying the total `rag.usage` accumulated
across routing, rerank and synthesis. That log is the only honest source for unit
economics — published per-token rates miss rerank context size, retry legs and the
long tail of very expensive questions, which is exactly where a per-user price point
lives or dies.

Reports the distribution rather than the mean. The mean is what a pricing deck quotes;
p95 is what a heavy user on an unlimited plan actually costs you every month.
"""

from __future__ import annotations

import frappe
from frappe.utils.data import cint, flt

# Answer rows only — question rows carry no usage.
ANSWER_ROLE = "answer"

# Rupees per dollar. A pricing input, not a constant: pass the rate you plan against.
USD_TO_INR = 88.0

# Daily ask volumes to project a monthly bill for. Casual, regular, power.
PROJECTION_ASKS_PER_DAY = (5, 15, 30)


def percentile(sorted_values: list[float], fraction: float) -> float:
	"""Nearest-rank percentile. Empty list reports zero rather than raising."""
	if not sorted_values:
		return 0.0
	rank = max(0, min(len(sorted_values) - 1, round(fraction * (len(sorted_values) - 1))))
	return sorted_values[rank]


def summarise(costs: list[float]) -> dict:
	"""Distribution of a cost series, in USD."""
	ordered = sorted(costs)
	count = len(ordered)
	return {
		"asks": count,
		"total": sum(ordered),
		"mean": (sum(ordered) / count) if count else 0.0,
		"median": percentile(ordered, 0.50),
		"p90": percentile(ordered, 0.90),
		"p95": percentile(ordered, 0.95),
		"max": ordered[-1] if ordered else 0.0,
	}


def group_costs(rows: list[dict], key: str) -> dict[str, dict]:
	"""Cost distribution per distinct value of `key`, heaviest total first."""
	buckets: dict[str, list[float]] = {}
	for row in rows:
		buckets.setdefault(row.get(key) or "(unset)", []).append(flt(row["cost"]))
	grouped = {name: summarise(costs) for name, costs in buckets.items()}
	return dict(sorted(grouped.items(), key=lambda pair: pair[1]["total"], reverse=True))


def collect(project: str | None = None, limit: int = 0) -> list[dict]:
	"""Answer rows with usage on them, newest first.

	`limit=0` reads every row — the whole point is the tail, and truncating the read
	would cut exactly the expensive questions the report exists to find.
	"""
	filters = {"role": ANSWER_ROLE}
	if project:
		# `project` lives on the session, not the turn — resolve it to session names first
		# rather than joining, so the message read stays a plain indexed filter.
		sessions = frappe.get_all("Wikify Ask Session", filters={"project": project}, pluck="name")
		if not sessions:
			return []
		filters["session"] = ["in", sessions]

	return frappe.get_all(
		"Wikify Ask Message",
		filters=filters,
		fields=[
			"name",
			"session",
			"owner",
			"creation",
			"model",
			"route_intent",
			"refused",
			"cost",
			"prompt_tokens",
			"completion_tokens",
			"took_ms",
		],
		order_by="creation desc",
		limit_page_length=cint(limit),
	)


def report(project: str | None = None, usd_to_inr: float = USD_TO_INR) -> dict:
	"""Unit economics of the Ask surface: per-ask distribution, and what it implies per user.

	Run after any batch of real questions — every new ask sharpens the tail estimate.
	"""
	rows = collect(project=project)
	if not rows:
		frappe.throw("No answered asks logged yet — ask some questions first, then re-run.")

	costs = [flt(row["cost"]) for row in rows]
	overall = summarise(costs)

	sessions: dict[str, list[float]] = {}
	for row in rows:
		sessions.setdefault(row["session"], []).append(flt(row["cost"]))

	prompt_tokens = [cint(row["prompt_tokens"]) for row in rows]
	completion_tokens = [cint(row["completion_tokens"]) for row in rows]
	latencies = sorted(cint(row["took_ms"]) for row in rows)

	# A month of one user's asks, priced at the mean and again at p95. The gap between
	# the two columns is the margin a flat monthly plan has to absorb.
	projection = {
		f"{per_day}/day": {
			"at_mean_inr": overall["mean"] * per_day * 30 * usd_to_inr,
			"at_p95_inr": overall["p95"] * per_day * 30 * usd_to_inr,
		}
		for per_day in PROJECTION_ASKS_PER_DAY
	}

	return {
		"window": {"from": rows[-1]["creation"], "to": rows[0]["creation"]},
		"asks": overall["asks"],
		"sessions": len(sessions),
		"refused": sum(1 for row in rows if cint(row["refused"])),
		"cost_usd": overall,
		"cost_inr": {
			"mean": overall["mean"] * usd_to_inr,
			"median": overall["median"] * usd_to_inr,
			"p90": overall["p90"] * usd_to_inr,
			"p95": overall["p95"] * usd_to_inr,
			"max": overall["max"] * usd_to_inr,
			"total": overall["total"] * usd_to_inr,
		},
		"tokens": {
			"mean_prompt": (sum(prompt_tokens) / len(prompt_tokens)) if prompt_tokens else 0,
			"mean_completion": (sum(completion_tokens) / len(completion_tokens)) if completion_tokens else 0,
		},
		"latency_ms": {"median": percentile(latencies, 0.50), "p95": percentile(latencies, 0.95)},
		"asks_per_session": overall["asks"] / len(sessions),
		"cost_per_session_inr": summarise([sum(c) for c in sessions.values()])["mean"] * usd_to_inr,
		"by_model": group_costs(rows, "model"),
		"by_route": group_costs(rows, "route_intent"),
		"monthly_per_user_inr": projection,
	}


def print_report(project: str | None = None, usd_to_inr: float = USD_TO_INR) -> None:
	"""Console-readable form of `report()`, for `bench execute`."""
	rate = flt(usd_to_inr) or USD_TO_INR
	data = report(project=project, usd_to_inr=rate)

	print(f"\nAsks: {data['asks']}  Sessions: {data['sessions']}  Refused: {data['refused']}")
	print(f"Window: {data['window']['from']} -> {data['window']['to']}")

	inr = data["cost_inr"]
	print("\nCost per ask (INR)")
	for label in ("mean", "median", "p90", "p95", "max"):
		print(f"  {label:8} Rs {inr[label]:.4f}")
	print(f"  {'total':8} Rs {inr['total']:.2f}")

	print(
		f"\nTokens/ask: {data['tokens']['mean_prompt']:.0f} prompt, "
		f"{data['tokens']['mean_completion']:.0f} completion"
	)
	print(f"Latency: median {data['latency_ms']['median']} ms, p95 {data['latency_ms']['p95']} ms")
	print(f"Session: {data['asks_per_session']:.1f} asks, Rs {data['cost_per_session_inr']:.4f} mean")

	print("\nBy model")
	for model, stats in data["by_model"].items():
		print(f"  {model:34} {stats['asks']:5} asks  mean Rs {stats['mean'] * rate:.4f}")

	print("\nBy route")
	for route, stats in data["by_route"].items():
		print(f"  {route:34} {stats['asks']:5} asks  mean Rs {stats['mean'] * rate:.4f}")

	print("\nMonthly cost of one user (INR)")
	for volume, bill in data["monthly_per_user_inr"].items():
		print(f"  {volume:8} at mean Rs {bill['at_mean_inr']:7.2f}   at p95 Rs {bill['at_p95_inr']:7.2f}")
	print()
