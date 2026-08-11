"""LanceDB storage seam — one embedded database under the site's private files.

Living inside `sites/<site>/private/files/wikify_lance` keeps the index site-scoped and
inside the normal backup/private boundary (never web-servable). One `chunks` table holds
every project; project scoping is a `.where()` pre-filter, so cross-project queries stay
a single scan and the ACL filter can be pushed into the same clause.

The schema is declared explicitly rather than inferred from the first batch: inference
gives `list<float64>` (variable length), which cannot be vector-indexed and silently
disables `.search()`. The vector column must be `fixed_size_list(float32, 256)`.
"""

from __future__ import annotations

import os

import frappe

from wikify.rag.embed import EMBED_DIM

TABLE_NAME = "chunks"
LANCE_DIRNAME = "wikify_lance"


def lance_path() -> str:
	"""Absolute path to the LanceDB directory (absolute: workers run from other cwds)."""
	return os.path.abspath(frappe.get_site_path("private", "files", LANCE_DIRNAME))


def connect():
	"""Open (creating the directory if needed) the site's LanceDB connection."""
	import lancedb

	path = lance_path()
	os.makedirs(path, exist_ok=True)
	return lancedb.connect(path)


def chunk_schema():
	"""The `chunks` pyarrow schema. `section` is the parent-document retrieval anchor."""
	import pyarrow as pa

	return pa.schema(
		[
			pa.field("id", pa.string()),
			pa.field("section", pa.string()),
			pa.field("source_document", pa.string()),
			pa.field("project", pa.string()),
			pa.field("text", pa.string()),
			pa.field("embed_text", pa.string()),
			pa.field("section_type", pa.string()),
			pa.field("hierarchy_path", pa.string()),
			pa.field("page_start", pa.int32()),
			pa.field("page_end", pa.int32()),
			# Span provenance: the page the chunk was matched back to (0 / approximate when
			# the match was ambiguous), its line span in the parent section's markdown, and
			# its line span on that page. This is what makes a citation checkable.
			pa.field("page_no", pa.int32()),
			pa.field("page_approximate", pa.bool_()),
			pa.field("line_start", pa.int32()),
			pa.field("line_end", pa.int32()),
			pa.field("page_line_start", pa.int32()),
			pa.field("page_line_end", pa.int32()),
			# Set on a section with no body of its own: returned by exhaustive filter mode,
			# excluded from the similarity legs so a bare title cannot outrank real content.
			pa.field("title_only", pa.bool_()),
			pa.field("wiki_route", pa.string()),
			pa.field("vector", pa.list_(pa.float32(), EMBED_DIM)),
		]
	)


def chunks_table(create: bool = False):
	"""The single `chunks` table, or `None` when it doesn't exist and `create` is False.

	Read paths pass `create=False` so a site that has never indexed answers "no results"
	instead of materialising an empty table on every search.
	"""
	db = connect()
	if TABLE_NAME in db.table_names():
		return db.open_table(TABLE_NAME)
	if not create:
		return None
	return db.create_table(TABLE_NAME, schema=chunk_schema())


def reset_chunks_table():
	"""Drop and recreate `chunks` against the CURRENT schema, then return the empty table.

	A LanceDB table keeps the schema it was created with, so a column added to
	`chunk_schema()` never appears on an existing table and every read of it comes back
	null. Adding a column is therefore a re-index, not a migration script — this drops the
	table so the caller can rebuild each project into it.
	"""
	db = connect()
	if TABLE_NAME in db.table_names():
		db.drop_table(TABLE_NAME)
	return db.create_table(TABLE_NAME, schema=chunk_schema())


def sql_literal(value) -> str:
	"""Quote a value for a LanceDB `.where()` clause (single quotes doubled)."""
	return "'" + str(value).replace("'", "''") + "'"
