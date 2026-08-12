"""The bundle format — one project's corpus as a single portable file.

A bundle carries the *expensive* half of a project: `canonical_markdown` per page, the
remediation verdicts, the section tree and its markdown. All of that was paid for once in
LLM spend at parse time and is plain stored data afterwards, so moving a corpus between
sites costs nothing but disk.

What a bundle deliberately does NOT carry:

- **The LanceDB index.** Embeddings are `model2vec` static lookups — local, numpy-only, no
  key — so `rag.index.rebuild_project` regenerates the index on arrival for free. Shipping
  it would add ~30MB per project and let a stale index outlive the rows it describes.
- **`Wikify Settings`.** It holds the OpenRouter key; a shareable zip is exactly where a
  secret should never be.
- **Ask/Agent sessions.** Per-user conversation history, not corpus.
- **The generated wiki.** `Wiki Space`/`Wiki Document` belong to another app, so including
  them would couple bundle validity to the wiki app's schema. The target regenerates.
"""

from __future__ import annotations

import hashlib
import json

SCHEMA_VERSION = 1
BUNDLE_SUFFIX = ".wikify.zip"
MANIFEST_NAME = "manifest.json"
FILE_INDEX_NAME = "files/index.json"

# Emission order IS import order: every link target is written before the rows pointing at
# it, so a restore can insert straight down the list. The one cycle — `Wikify Import`
# .source_document against `Source Document`.import — is broken by DEFERRED_LINKS, because
# no ordering satisfies a cycle.
ROW_SETS: tuple[tuple[str, str], ...] = (
	("rows/00-section-type.json", "Section Type"),
	("rows/10-wikify-project.json", "Wikify Project"),
	("rows/20-wikify-import.json", "Wikify Import"),
	("rows/30-source-document.json", "Source Document"),
	("rows/40-source-page.json", "Source Page"),
	("rows/50-source-section.json", "Source Section"),
	("rows/60-section-reference.json", "Section Reference"),
)

# Written blank on insert, then filled in a second pass once the target row exists.
DEFERRED_LINKS: dict[str, tuple[str, ...]] = {"Wikify Import": ("source_document",)}

# Self-referential parent link — remapped to the new name of an already-inserted row.
PARENT_LINKS: dict[str, str] = {"Source Section": "parent_source_section"}

# Nested-set bounds are recomputed by `rebuild_tree` on import and never read from the
# bundle. Copied bounds that disagree with the parent links corrupt every ancestor and
# descendant query while every row still looks individually correct.
TREE_FIELDS: tuple[str, ...] = ("lft", "rgt", "old_parent")

# Belong to the source site's bookkeeping, not to the corpus.
SYSTEM_FIELDS: tuple[str, ...] = (
	"owner",
	"creation",
	"modified",
	"modified_by",
	"docstatus",
	"idx",
	"doctype",
	"_user_tags",
	"_comments",
	"_assign",
	"_liked_by",
)

# Cross-app links dropped in v1; the target regenerates its own wiki.
DROPPED_LINKS: dict[str, tuple[str, ...]] = {
	"Wikify Import": ("wiki_space",),
	"Source Document": ("wiki_space", "wiki_root_group"),
	"Source Section": ("wiki_document",),
}

# Fields holding a `file_url` whose bytes travel with the bundle.
ATTACH_FIELDS: dict[str, tuple[str, ...]] = {
	"Wikify Import": ("pdf",),
	"Source Document": ("pdf",),
	"Source Page": ("image",),
}

# Named by the value of a field rather than a hash or series, so two sites converge on the
# same taxonomy instead of forking it. Merged by name on import; the target's row wins.
MERGE_BY_NAME: tuple[str, ...] = ("Section Type",)

# Every link a restore has to rewrite, and the doctype whose remap table holds the answer.
# `Section Type` resolves through an identity remap because it merges by name.
LINK_FIELDS: dict[str, dict[str, str]] = {
	"Wikify Import": {"project": "Wikify Project", "source_document": "Source Document"},
	"Source Document": {"import": "Wikify Import", "project": "Wikify Project"},
	"Source Page": {"source_document": "Source Document"},
	"Source Section": {
		"source_document": "Source Document",
		"parent_source_section": "Source Section",
		"section_type": "Section Type",
	},
	"Section Reference": {
		"from_section": "Source Section",
		"to_section": "Source Section",
		"source_document": "Source Document",
	},
}


def doctype_for(path: str) -> str:
	"""The doctype a `rows/` path holds, or raise if the bundle names an unknown set."""
	for row_path, doctype in ROW_SETS:
		if row_path == path:
			return doctype
	raise KeyError(f"'{path}' is not a row set in schema version {SCHEMA_VERSION}")


def scrub_row(doctype: str, row: dict) -> dict:
	"""Strip a row down to what is portable: no site bookkeeping, no tree bounds, no wiki."""
	drop = set(SYSTEM_FIELDS) | set(DROPPED_LINKS.get(doctype, ()))
	if doctype in PARENT_LINKS:
		drop.update(TREE_FIELDS)
	return {key: value for key, value in row.items() if key not in drop and value is not None}


def serialise(payload) -> bytes:
	"""A row set, manifest or file index → deterministic JSON bytes.

	Sorted keys and fixed indentation so the same corpus produces byte-identical output on
	every export — which is what makes the manifest checksums worth comparing at all.
	"""
	return json.dumps(payload, sort_keys=True, ensure_ascii=False, indent="\t", default=str).encode("utf-8")


def checksum(payload: bytes) -> str:
	"""`sha256:<hex>` over a bundle member, as stored in the manifest."""
	return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def build_manifest(*, project: dict, counts: dict[str, int], checksums: dict[str, str]) -> dict:
	"""The manifest a restore validates against before it writes anything."""
	import frappe

	return {
		"schema_version": SCHEMA_VERSION,
		"exported_at": frappe.utils.now(),
		"source_site": frappe.local.site,
		"app_version": frappe.get_attr("wikify.__version__") if hasattr(frappe, "get_attr") else None,
		"project": {"name": project.get("name"), "project_name": project.get("project_name")},
		"counts": counts,
		"checksums": checksums,
	}


def assert_supported(manifest: dict) -> None:
	"""Refuse a bundle this build cannot read, rather than importing half of it.

	Only an exact schema match is accepted. A forward-compatible reader would have to guess
	what a newer writer meant by a field it has never seen, and guessing wrong here produces
	a corpus that imports cleanly and answers questions wrongly.
	"""
	version = manifest.get("schema_version")
	if version != SCHEMA_VERSION:
		raise ValueError(
			f"Bundle schema version {version!r} cannot be read by this build "
			f"(expected {SCHEMA_VERSION}). Export it again from a matching version of wikify."
		)
