"""Static embeddings via model2vec (`minishlab/potion-base-8M`, 256-dim).

Static means a lookup table, not a transformer: numpy-only, no torch, no API key, and
fast enough to embed a whole project inline. Loading the model still costs ~1s, so it is
cached module-level — loading it per call dominated everything else in the POC.
"""

from __future__ import annotations

import threading

import frappe
from frappe import _

MODEL_NAME = "minishlab/potion-base-8M"
# Pinned to the weights every eval number and every calibrated floor in `rag.answer` was
# measured against. An unpinned repo can serve different vectors tomorrow and nothing
# downstream would notice: the scores would simply move.
MODEL_REVISION = "bf8b056651a2c21b8d2565580b8569da283cab23"
EMBED_DIM: int = 256

_model = None
_model_lock = threading.Lock()


def get_model():
	"""The cached `StaticModel`, loaded on first use.

	Double-checked locking so two gunicorn threads racing on the first search don't both
	pay the load (and don't both hit the HF cache concurrently).

	The snapshot is resolved here and handed over as a local path because model2vec's
	`from_pretrained` takes no `revision` and defaults to `force_download=True` — it would
	re-fetch the weights on every cold start and accept whatever the repo serves that day.
	"""
	global _model

	if _model is None:
		with _model_lock:
			if _model is None:
				_model = load_model()
	return _model


def load_model():
	"""Load the pinned snapshot, or say plainly why retrieval cannot run.

	Every mode except `filter` embeds the query, so there is nothing to degrade to here —
	but an offline bench must fail with the command that fixes it rather than an HTTP
	traceback out of the hub client.
	"""
	from huggingface_hub import snapshot_download
	from model2vec import StaticModel

	try:
		return StaticModel.from_pretrained(snapshot_download(MODEL_NAME, revision=MODEL_REVISION))
	except Exception:
		frappe.log_error(title="wikify: embedding model unavailable")
		frappe.throw(
			_(
				"The embedding model ({0}) is not available on this bench. Run `bench --site "
				"<site> execute wikify.rag.warm.warm_models` on a host with network access, or "
				"re-run migrate."
			).format(MODEL_NAME)
		)


def embed(texts: list[str]) -> list[list[float]]:
	"""Embed a batch of texts. One `encode` call — model2vec batches internally."""
	texts = list(texts or [])
	if not texts:
		return []
	vectors = get_model().encode(texts)
	return [[float(value) for value in vector] for vector in vectors]


def embed_one(text: str) -> list[float]:
	return embed([text])[0]
