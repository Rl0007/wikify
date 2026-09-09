from __future__ import annotations

import threading

import numpy as np

MODEL_REPO = "cross-encoder/ms-marco-MiniLM-L6-v2"
# Pinned: the 0-10 map below and `answer.MIN_RERANK_SCORE` are calibrated against the
# logits THESE weights produce.
MODEL_REVISION = "233902d25c440f23af6f7d6e94d2946bac0bee0a"
MODEL_FILE = "onnx/model.onnx"
TOKENIZER_FILE = "tokenizer.json"
# Truncation does not degrade gracefully: on the 11 golden questions 512 tokens scored
# 100% recall, 320 scored 89%, 256 scored 100%, 128 scored 81%.
MAX_TOKENS = 512
# Wider batches were not faster — the encoder already saturates every core.
BATCH_SIZE = 16
# Linear in the LOGIT, not its sigmoid, which squeezes everything short of "certainly
# relevant" into the first thousandth — where the refusal floor has to live. Measured top
# logits run -11.5 (unanswerable) to +0.7 (answered well).
LOGIT_FLOOR = -12.0
LOGIT_CEILING = 4.0
SCORE_SCALE = 10.0

_models: dict[str, tuple] = {}
_model_lock = threading.Lock()


def get_model(repo: str = MODEL_REPO):
	if repo not in _models:
		with _model_lock:
			if repo not in _models:
				import onnxruntime
				from huggingface_hub import hf_hub_download
				from tokenizers import Tokenizer

				tokenizer = Tokenizer.from_file(
					hf_hub_download(repo, TOKENIZER_FILE, revision=MODEL_REVISION)
				)
				# `only_second` truncates the candidate, never the question: a question cut in
				# half is scored against nothing.
				tokenizer.enable_truncation(max_length=MAX_TOKENS, strategy="only_second")
				tokenizer.no_padding()
				weights = hf_hub_download(repo, MODEL_FILE, revision=MODEL_REVISION)
				session = onnxruntime.InferenceSession(weights)
				_models[repo] = (tokenizer, session, {value.name for value in session.get_inputs()})
	return _models[repo]


def padded(rows: list[list[int]], width: int) -> np.ndarray:
	matrix = np.zeros((len(rows), width), dtype=np.int64)
	for position, row in enumerate(rows):
		matrix[position, : len(row)] = row
	return matrix


def run_batch(session, input_names: set[str], encodings) -> list[float]:
	width = max(len(encoding.ids) for encoding in encodings)
	inputs = {
		"input_ids": padded([encoding.ids for encoding in encodings], width),
		"attention_mask": padded([encoding.attention_mask for encoding in encodings], width),
		"token_type_ids": padded([encoding.type_ids for encoding in encodings], width),
	}
	logits = session.run(None, {name: value for name, value in inputs.items() if name in input_names})[0]
	return np.reshape(logits, (len(encodings), -1))[:, -1].tolist()


def graded(logit: float) -> float:
	span = (logit - LOGIT_FLOOR) / (LOGIT_CEILING - LOGIT_FLOOR)
	return round(min(max(span, 0.0), 1.0) * SCORE_SCALE, 4)


def scores(query: str, texts: list[str], repo: str = MODEL_REPO) -> list[float]:
	if not texts:
		return []

	tokenizer, session, input_names = get_model(repo)
	encodings = tokenizer.encode_batch([(query, text) for text in texts])
	# Shortest-first because padding is per batch: mixing a 40-token heading in with a
	# 512-token section makes the encoder read 512 tokens for both.
	order = sorted(range(len(encodings)), key=lambda position: len(encodings[position].ids))

	relevance = [0.0] * len(texts)
	for start in range(0, len(order), BATCH_SIZE):
		positions = order[start : start + BATCH_SIZE]
		batch = [encodings[position] for position in positions]
		logits = run_batch(session, input_names, batch)
		for position, logit in zip(positions, logits, strict=True):
			relevance[position] = graded(logit)
	return relevance
