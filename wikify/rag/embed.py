"""Static embeddings via model2vec (`minishlab/potion-base-8M`, 256-dim).

Static means a lookup table, not a transformer: numpy-only, no torch, no API key, and
fast enough to embed a whole project inline. Loading the model still costs ~1s, so it is
cached module-level — loading it per call dominated everything else in the POC.
"""

from __future__ import annotations

import threading

MODEL_NAME = "minishlab/potion-base-8M"
EMBED_DIM: int = 256

_model = None
_model_lock = threading.Lock()


def get_model():
	"""The cached `StaticModel`, loaded on first use.

	Double-checked locking so two gunicorn threads racing on the first search don't both
	pay the load (and don't both hit the HF cache concurrently).
	"""
	global _model

	if _model is None:
		with _model_lock:
			if _model is None:
				from model2vec import StaticModel

				_model = StaticModel.from_pretrained(MODEL_NAME)
	return _model


def embed(texts: list[str]) -> list[list[float]]:
	"""Embed a batch of texts. One `encode` call — model2vec batches internally."""
	texts = list(texts or [])
	if not texts:
		return []
	vectors = get_model().encode(texts)
	return [[float(value) for value in vector] for vector in vectors]


def embed_one(text: str) -> list[float]:
	return embed([text])[0]
