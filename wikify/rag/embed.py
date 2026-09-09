from __future__ import annotations

import threading

import frappe
from frappe import _

MODEL_NAME = "minishlab/potion-base-8M"
MODEL_REVISION = "bf8b056651a2c21b8d2565580b8569da283cab23"
EMBED_DIM: int = 256

_model = None
_model_lock = threading.Lock()


def get_model():
	global _model

	if _model is None:
		with _model_lock:
			if _model is None:
				_model = load_model()
	return _model


def load_model():
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
	texts = list(texts or [])
	if not texts:
		return []
	vectors = get_model().encode(texts)
	return [[float(value) for value in vector] for vector in vectors]


def embed_one(text: str) -> list[float]:
	return embed([text])[0]
