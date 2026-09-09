from __future__ import annotations

import frappe


def warm_models() -> dict[str, bool]:
	from wikify.rag import embed, rerank

	loaded = {}
	for name, load in (("embed", embed.get_model), ("rerank", rerank.get_model)):
		try:
			load()
			loaded[name] = True
		except Exception:
			frappe.log_error(title=f"wikify: could not warm the {name} model")
			loaded[name] = False
	return loaded
