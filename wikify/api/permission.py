# Copyright (c) 2026, BWH and contributors
# For license information, please see license.txt

"""Project-scoped permission helpers, shared by every read surface.

`readable_projects` / `assert_readable` are the ACL decision the retrieval and Explore
APIs both hand to their stores — one definition, so the two screens cannot drift into
disagreeing about what a user may see.
"""

from __future__ import annotations

import frappe
from frappe import _


def readable_projects() -> list[str]:
	"""Wikify Projects the current user may read (`get_list` applies permissions).

	A user with no read permission at all gets `[]`, not an exception: an unscoped search
	must come back empty for them, and `search()` reads an empty ACL list as "no rows".
	"""
	if not frappe.has_permission("Wikify Project", ptype="read"):
		return []
	return frappe.get_list("Wikify Project", pluck="name", limit_page_length=0)


def assert_readable(project: str | None, source_document: str | None = None) -> None:
	"""Guard an explicit scope: both a project and a document resolve to a project check."""
	if source_document and not project:
		project = frappe.db.get_value("Source Document", source_document, "project")
	if not project:
		return
	if not frappe.has_permission("Wikify Project", doc=project):
		frappe.throw(_("You are not allowed to read {0}.").format(project), frappe.PermissionError)


def documents_in_projects(projects: list[str]) -> list[str]:
	return frappe.get_all("Source Document", filters={"project": ["in", projects]}, pluck="name")


def hidden_documents() -> list[str]:
	"""Source Documents in projects this user may NOT read, to exclude from an unscoped screen.

	An exclusion rather than an allow-list on purpose. A document with no project at all
	carries no permission statement, and is visible to anyone who can reach the screen
	today; scoping to documents-in-readable-projects would silently hide that content as a
	side effect of a permission fix. This subtracts exactly what the user may not see.
	"""
	unreadable = set(frappe.get_all("Wikify Project", pluck="name")) - set(readable_projects())
	if not unreadable:
		return []
	return frappe.get_all("Source Document", filters={"project": ["in", list(unreadable)]}, pluck="name")


def has_app_permission() -> bool:
	"""Gate the Wikify tile on the desk apps screen.

	This hides a desk tile and nothing more — `/wikify` stays directly reachable — so it is
	not the app's authorization boundary; the per-project checks above are.
	"""
	if frappe.session.user == "Administrator":
		return True

	return bool(frappe.has_permission("Wikify Import", ptype="read"))
