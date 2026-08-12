"""Resolved access to the `Wikify Settings` Single.

Lifts the POC `config.py` model ids + tunables into a site-config doctype so they're
switchable without code (the POC already made them env-overridable). Code-side
constants that we don't expose for tuning (the composite weights, visual detection
heuristics) stay in `engine.config`.

The OpenRouter key resolves in priority order: the `Wikify Settings` password →
`site_config.json` (`openrouter_key`) → process env (`OPENROUTER_KEY` /
`OPENROUTER_API_KEY`) → the app's `.env` file. The Settings password is the intended
home; the rest are dev conveniences so a key in `apps/wikify/.env` still works.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import frappe

# Defaults mirror the POC `config.py` (used when a Settings field is blank).
DEFAULTS = {
	"vlm_model": "mistralai/mistral-medium-3.1",
	"cleanup_model": "google/gemini-2.5-flash",
	"judge_model": "anthropic/claude-sonnet-4.6",
	"classifier_model": "google/gemini-2.5-flash",
	"pass_threshold": 0.90,
	"escalate_threshold": 0.70,
	"cleanup_recall_tolerance": 0.12,
	"render_dpi": 150,
	"visual_min_chars": 250,
	"visual_min_drawings": 40,
	"remediation_workers": 6,
	"classify_workers": 8,
	"judge_all_pages": 0,
	"llm_provider": "OpenRouter",
	"claude_cli_path": "claude",
}


def get_settings():
	"""The `Wikify Settings` Single doc (cached per request)."""
	doc = frappe.get_cached_doc("Wikify Settings")
	remember_across_threads(doc)
	return doc


def get(field: str):
	"""A single setting, falling back to the POC default when blank."""
	try:
		value = get_settings().get(field)
	except Exception:
		value = None
	if value in (None, ""):
		return DEFAULTS.get(field)
	return value


@lru_cache(maxsize=1)
def _dotenv_key() -> str:
	"""Last-resort: read OPENROUTER_KEY from the app's .env without python-dotenv."""
	env_path = Path(frappe.get_app_path("wikify")).parent / ".env"
	if not env_path.exists():
		return ""
	for line in env_path.read_text(encoding="utf-8").splitlines():
		line = line.strip()
		if line.startswith(("OPENROUTER_KEY", "OPENROUTER_API_KEY")) and "=" in line:
			return line.split("=", 1)[1].strip().strip("\"'")
	return ""


# The provider fields, which have to answer on threads that cannot read the Single.
THREAD_VISIBLE_FIELDS = ("llm_provider", "claude_cli_path")

# field -> {site: last value read on a frappe-bound thread}. See `setting_across_threads`.
_resolved_across_threads: dict[str, dict[str, str]] = {}


def remember_across_threads(doc) -> None:
	"""Record the provider fields for the pool threads that cannot read them themselves.

	Primed from every settings read rather than only from `llm_provider()`, because the
	reranker's first look at the provider happens *inside* its `ThreadPoolExecutor` — by
	then it is too late to ask the database. Its own setup reads the API key on the request
	thread first, and that read is what fills this in.
	"""
	try:
		site = frappe.local.site
	except Exception:
		return
	for field in THREAD_VISIBLE_FIELDS:
		_resolved_across_threads.setdefault(field, {})[site] = (doc.get(field) or "").strip()


def setting_across_threads(field: str) -> str:
	"""A setting that still resolves on a pool thread, where `frappe.local` is unbound.

	`get()` swallows the unbound-local error and hands back the DEFAULTS value, which is
	fine for a tuning number and wrong for the provider switch: the reranker grades its
	batches on a `ThreadPoolExecutor`, so a site set to Claude CLI would have silently sent
	every rerank batch to OpenRouter instead. Reading the Single is impossible off the
	request thread, so the value last read on a bound thread stands in for it.

	Remembered per site, and only trusted off-thread when every site in the process agrees:
	one bench process can serve a production site on OpenRouter and a dev site on the CLI,
	and billing one site's answer to the other's provider is worse than falling back.

	# ponytail: a worker process whose very first settings read happens on a pool thread has
	# nothing remembered and falls back to OpenRouter; pass `llm_provider` down to
	# `llm.chat_completion` from the pool's setup, the way `api_key` already is, if a job
	# ever turns out to start that way.
	"""
	try:
		return (get_settings().get(field) or "").strip()
	except Exception:
		agreed = set(_resolved_across_threads.get(field, {}).values())
		return agreed.pop() if len(agreed) == 1 else ""


def llm_provider() -> str:
	"""Which client serves `llm.chat_completion` — "OpenRouter" or "Claude CLI".

	Per site, not per call, so that a local site can run every feature — routing, reranking,
	classification, extraction — through a developer's logged-in Claude subscription while
	production stays on the metered OpenRouter key, with no caller aware of either.
	"""
	return setting_across_threads("llm_provider") or DEFAULTS["llm_provider"]


def claude_cli_path() -> str:
	"""The `claude` executable to spawn. Blank Settings field means "on PATH"."""
	return setting_across_threads("claude_cli_path") or DEFAULTS["claude_cli_path"]


def openrouter_key() -> str:
	"""Resolve the OpenRouter API key (Settings → site config → env → .env)."""
	try:
		key = get_settings().get_password("openrouter_api_key")
	except Exception:
		key = None
	key = (
		key
		or frappe.conf.get("openrouter_key")
		or os.environ.get("OPENROUTER_KEY")
		or os.environ.get("OPENROUTER_API_KEY")
		or _dotenv_key()
	)
	return (key or "").strip()
