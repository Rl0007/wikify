"""Relevance scoring with a local cross-encoder (`ms-marco-MiniLM-L6-v2`, ONNX on CPU).

A cross-encoder reads the question and one candidate together and emits a single relevance
logit, so it judges the pair the way an LLM reranker does but in one small forward pass
instead of a chat completion. The published comparisons are one-sided: dedicated rerankers
*beat* LLM rerankers on NDCG@10 while running an order of magnitude faster (Voyage 2025-10,
ZeroEntropy 2025-09 — see `docs/rag-latency-prior-art.md` §1.1). This is not a quality trade.

Local, like `rag.embed`, and for the same reason: no API key, no GPU, no server. The weights
are ~90MB of ONNX pulled from the HF hub on first use and cached there.

Two things dominate the latency and are therefore constants here rather than defaults buried
in a call: how many tokens of each candidate are read (`MAX_TOKENS` — cost is linear in it,
and it is why a reranker over whole 1,100-token sections is slow), and how the pairs are
batched (`BATCH_SIZE`, over length-sorted pairs, so a short candidate is not padded out to
the longest one in the list).
"""

from __future__ import annotations

import threading

import numpy as np

MODEL_REPO = "cross-encoder/ms-marco-MiniLM-L6-v2"
MODEL_FILE = "onnx/model.onnx"
TOKENIZER_FILE = "tokenizer.json"
# Tokens of the query+candidate pair the encoder reads. Latency is linear in this and it is
# the single biggest lever there is — but truncation is where recall goes, and it does not
# degrade gracefully: on the 11 golden questions, 512 tokens scored 100% recall, 320 scored
# 89%, 256 scored 100% and 128 scored 81%. That is not a quality curve, it is a coin flip on
# whether the answering sentence survives the cut, so the encoder reads its full window.
MAX_TOKENS = 512
# Pairs go through the session in batches so a long candidate does not pad out the whole
# list. Wider batches were not faster: the encoder already saturates every core.
BATCH_SIZE = 16
# Scores go out on the 0-10 scale the LLM reranker used, because `answer.MIN_RERANK_SCORE`
# and the citation cards both read that scale. The map is linear in the LOGIT, not in its
# sigmoid: the sigmoid squeezes everything short of "certainly relevant" into the first
# thousandth of the range, which is precisely where a refusal floor has to live. Measured on
# the demo corpus, top logits run -11.5 (a question the corpus does not answer) to +0.7 (one
# it answers well), so the window below is the observed range with headroom either side.
LOGIT_FLOOR = -12.0
LOGIT_CEILING = 4.0
SCORE_SCALE = 10.0

_models: dict[str, tuple] = {}
_model_lock = threading.Lock()


def get_model(repo: str = MODEL_REPO):
	"""The cached `(tokenizer, session, input_names)`, loaded and downloaded on first use.

	Double-checked locking so two gunicorn threads racing on the first search don't both pay
	the load — the same pattern, and the same reason, as `embed.get_model`.
	"""
	if repo not in _models:
		with _model_lock:
			if repo not in _models:
				import onnxruntime
				from huggingface_hub import hf_hub_download
				from tokenizers import Tokenizer

				tokenizer = Tokenizer.from_file(hf_hub_download(repo, TOKENIZER_FILE))
				# `only_second` truncates the candidate and never the question: a question cut
				# in half is scored against nothing.
				tokenizer.enable_truncation(max_length=MAX_TOKENS, strategy="only_second")
				# Padding is done per batch below, not per list, so it stays off here.
				tokenizer.no_padding()
				session = onnxruntime.InferenceSession(hf_hub_download(repo, MODEL_FILE))
				_models[repo] = (tokenizer, session, {value.name for value in session.get_inputs()})
	return _models[repo]


def padded(rows: list[list[int]], width: int) -> np.ndarray:
	"""The rows as one `int64` matrix, each right-padded with zeros to `width`."""
	matrix = np.zeros((len(rows), width), dtype=np.int64)
	for position, row in enumerate(rows):
		matrix[position, : len(row)] = row
	return matrix


def run_batch(session, input_names: set[str], encodings) -> list[float]:
	"""One forward pass over a batch of encoded pairs, returning its raw relevance logits."""
	width = max(len(encoding.ids) for encoding in encodings)
	inputs = {
		"input_ids": padded([encoding.ids for encoding in encodings], width),
		"attention_mask": padded([encoding.attention_mask for encoding in encodings], width),
		"token_type_ids": padded([encoding.type_ids for encoding in encodings], width),
	}
	logits = session.run(None, {name: value for name, value in inputs.items() if name in input_names})[0]
	return np.reshape(logits, (len(encodings), -1))[:, -1].tolist()


def graded(logit: float) -> float:
	"""One relevance logit on the 0-10 scale — see `LOGIT_FLOOR` for why the map is linear."""
	span = (logit - LOGIT_FLOOR) / (LOGIT_CEILING - LOGIT_FLOOR)
	return round(min(max(span, 0.0), 1.0) * SCORE_SCALE, 4)


def scores(query: str, texts: list[str], repo: str = MODEL_REPO) -> list[float]:
	"""Score every candidate 0-10 for how well it answers `query`, in the order given.

	Pairs are batched shortest-first because padding is per batch: mixing a 40-token heading
	in with a 512-token section makes the encoder read 512 tokens for both.
	"""
	if not texts:
		return []

	tokenizer, session, input_names = get_model(repo)
	encodings = tokenizer.encode_batch([(query, text) for text in texts])
	order = sorted(range(len(encodings)), key=lambda position: len(encodings[position].ids))

	relevance = [0.0] * len(texts)
	for start in range(0, len(order), BATCH_SIZE):
		positions = order[start : start + BATCH_SIZE]
		batch = [encodings[position] for position in positions]
		logits = run_batch(session, input_names, batch)
		for position, logit in zip(positions, logits, strict=True):
			relevance[position] = graded(logit)
	return relevance
