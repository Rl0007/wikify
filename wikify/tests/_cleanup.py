# Copyright (c) 2026, BWH and contributors
# For license information, please see license.txt

"""Raw-delete helpers for committed test fixtures.

The agent loop commits mid-turn (`loop._record_mutation`), so any test that drives
`AgentRunner` — or anything else that commits — defeats the FrappeTestCase rollback and
leaks its fixtures into the site DB (the 2026-07-03 audit found 647 leaked test
documents and 15 leaked projects). Every test that inserts a Source Document, agent
session, or project must register the matching helper with `addCleanup`. Deletes are at
the table level: `frappe.delete_doc` would enqueue link-cleanup jobs, which need the
queue redis that isn't running under `run-tests`.
"""

from __future__ import annotations

import frappe


def _finalized(delete_fn) -> None:
	"""Roll back, delete, commit — the shape a cleanup needs to actually stick.

	Rollback first discards the test's *uncommitted* writes (so committing the deletes
	below doesn't accidentally persist unrelated state, e.g. a Wikify Settings change a
	test relied on rollback to restore). The commit then makes the deletes durable —
	without it, the FrappeTestCase rollback would resurrect fixture rows that a mid-test
	commit already persisted.
	"""
	frappe.db.rollback()
	delete_fn()
	frappe.db.commit()


def delete_document(sd_name: str) -> None:
	"""Delete a Source Document and everything hanging off it (imports, logs, files)."""
	_finalized(lambda: _delete_document_rows(sd_name))


def _delete_document_rows(sd_name: str) -> None:
	imports = frappe.get_all("Wikify Import", filters={"source_document": sd_name}, pluck="name")
	if imports:
		frappe.db.delete("Import Log Entry", {"import": ("in", imports)})
		frappe.db.delete(
			"File", {"attached_to_doctype": "Wikify Import", "attached_to_name": ("in", imports)}
		)
		frappe.db.delete("Wikify Import", {"name": ("in", imports)})
	pages = frappe.get_all("Source Page", filters={"source_document": sd_name}, pluck="name")
	if pages:
		frappe.db.delete("File", {"attached_to_doctype": "Source Page", "attached_to_name": ("in", pages)})
		frappe.db.delete("Source Page", {"name": ("in", pages)})
	frappe.db.delete("Section Reference", {"source_document": sd_name})
	frappe.db.delete("Source Section", {"source_document": sd_name})
	frappe.db.delete("Source Document", {"name": sd_name})


def _delete_session_rows(session_name: str) -> None:
	frappe.db.delete("Wikify Agent Message", {"session": session_name})
	frappe.db.delete("Wikify Agent Session", {"name": session_name})


def delete_session(session_name: str) -> None:
	_finalized(lambda: _delete_session_rows(session_name))


def delete_project(name: str) -> None:
	_finalized(lambda: frappe.db.delete("Wikify Project", {"name": name}))


def register_session_sweep(testcase) -> None:
	"""Snapshot the session table now; at cleanup, delete any sessions created since.

	Call from setUp. Catches sessions minted anywhere inside the test (helpers,
	`session.get_or_create`, the API) without threading names around.
	"""
	before = set(frappe.get_all("Wikify Agent Session", pluck="name"))

	def sweep():
		def rows():
			for name in set(frappe.get_all("Wikify Agent Session", pluck="name")) - before:
				_delete_session_rows(name)

		_finalized(rows)

	testcase.addCleanup(sweep)
