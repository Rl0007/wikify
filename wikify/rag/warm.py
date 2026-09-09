"""Fetch the retrieval models ahead of the first question.

Both models are pulled from the HF hub on first use, inside `search()` — which `ask()`
reaches on the web worker with `rerank=True` by default. Left to happen there, the first
question after a deploy pays ~90MB of ONNX plus the embedder, once per worker process,
and an air-gapped bench hangs on an HTTP timeout inside the answer path.

Wired to `after_migrate` so a deploy pays it instead, and callable on its own:

    bench --site <site> execute wikify.rag.warm.warm_models
"""

from __future__ import annotations

import frappe


def warm_models() -> dict[str, bool]:
	"""Load both models into the HF cache. Never fails the caller — a migrate must not
	break because a host has no network; the failure surfaces at first use instead."""
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
