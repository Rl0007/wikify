# Copyright (c) 2026, BWH and contributors
# For license information, please see license.txt
from __future__ import annotations

import frappe


def _finalized(delete_fn) -> None:
	frappe.db.rollback()
	delete_fn()
	# nosemgrep
	frappe.db.commit()


def delete_document(sd_name: str) -> None:
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


def delete_section_type(type_name: str) -> None:
	_finalized(lambda: frappe.db.delete("Section Type", {"name": type_name}))


def register_session_sweep(testcase) -> None:
	before = set(frappe.get_all("Wikify Agent Session", pluck="name"))

	def sweep():
		def rows():
			for name in set(frappe.get_all("Wikify Agent Session", pluck="name")) - before:
				_delete_session_rows(name)

		_finalized(rows)

	testcase.addCleanup(sweep)
