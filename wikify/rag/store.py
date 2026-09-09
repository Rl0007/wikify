from __future__ import annotations

import os

import frappe

from wikify.rag.embed import EMBED_DIM

TABLE_NAME = "chunks"
LANCE_DIRNAME = "wikify_lance"


def lance_path() -> str:
	return os.path.abspath(frappe.get_site_path("private", "files", LANCE_DIRNAME))


def connect():
	import lancedb

	path = lance_path()
	os.makedirs(path, exist_ok=True)
	return lancedb.connect(path)


def chunk_schema():
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
			pa.field("page_no", pa.int32()),
			pa.field("page_approximate", pa.bool_()),
			pa.field("line_start", pa.int32()),
			pa.field("line_end", pa.int32()),
			pa.field("page_line_start", pa.int32()),
			pa.field("page_line_end", pa.int32()),
			pa.field("title_only", pa.bool_()),
			pa.field("wiki_route", pa.string()),
			pa.field("vector", pa.list_(pa.float32(), EMBED_DIM)),
		]
	)


def chunks_table(create: bool = False):
	db = connect()
	if TABLE_NAME in db.table_names():
		return db.open_table(TABLE_NAME)
	if not create:
		return None
	return db.create_table(TABLE_NAME, schema=chunk_schema())


def reset_chunks_table():
	db = connect()
	if TABLE_NAME in db.table_names():
		db.drop_table(TABLE_NAME)
	return db.create_table(TABLE_NAME, schema=chunk_schema())


def sql_literal(value) -> str:
	return "'" + str(value).replace("'", "''") + "'"
