"""Re-derive `Source Page.verdict` from the canonical composite on already-parsed docs.

Before the `store.set_canonical` fix, the verdict was frozen at the BASELINE parse, so a
page whose remediated content scored 0.99 still wore a `review` badge next to its 0.99
audit score. Pure recompute from fields already on the row — no re-parse, no LLM spend.

Idempotent and console-runnable: `wikify.patches.v1_0.backfill_canonical_verdict.execute()`.
"""

from __future__ import annotations

import frappe

from wikify.engine.verify import get_verdict

CHUNK_SIZE = 500


def execute() -> dict[str, int]:
	"""Returns the number of pages moved to each verdict (empty when nothing was stale)."""
	# canonical_composite is a Frappe Float (NOT NULL, default 0), so "never remediated"
	# reads as 0.0 — filter those out rather than badging them `review` off a phantom score.
	pages = frappe.get_all(
		"Source Page",
		filters={"canonical_composite": [">", 0]},
		fields=["name", "verdict", "canonical_composite"],
	)

	stale: dict[str, list[str]] = {}
	for page in pages:
		verdict = get_verdict(page["canonical_composite"])
		if verdict != page["verdict"]:
			stale.setdefault(verdict, []).append(page["name"])

	for verdict, names in stale.items():
		for start in range(0, len(names), CHUNK_SIZE):
			frappe.db.set_value(
				"Source Page",
				{"name": ["in", names[start : start + CHUNK_SIZE]]},
				"verdict",
				verdict,
				update_modified=False,
			)

	return {verdict: len(names) for verdict, names in stale.items()}
