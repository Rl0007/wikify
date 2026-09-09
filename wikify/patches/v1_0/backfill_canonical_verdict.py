from __future__ import annotations

import frappe

from wikify.engine.verify import get_verdict

CHUNK_SIZE = 500


def execute() -> dict[str, int]:
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
