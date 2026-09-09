from __future__ import annotations

import frappe
from frappe import _


def readable_projects() -> list[str]:
	if not frappe.has_permission("Wikify Project", ptype="read"):
		return []
	return frappe.get_list("Wikify Project", pluck="name", limit_page_length=0)


def assert_readable(project: str | None, source_document: str | None = None) -> None:
	if source_document and not project:
		project = frappe.db.get_value("Source Document", source_document, "project")
	if not project:
		return
	if not frappe.has_permission("Wikify Project", doc=project):
		frappe.throw(_("You are not allowed to read {0}.").format(project), frappe.PermissionError)


def documents_in_projects(projects: list[str]) -> list[str]:
	return frappe.get_all("Source Document", filters={"project": ["in", projects]}, pluck="name")


def hidden_documents() -> list[str]:
	unreadable = set(frappe.get_all("Wikify Project", pluck="name")) - set(readable_projects())
	if not unreadable:
		return []
	return frappe.get_all("Source Document", filters={"project": ["in", list(unreadable)]}, pluck="name")


def has_app_permission() -> bool:
	if frappe.session.user == "Administrator":
		return True

	return bool(frappe.has_permission("Wikify Import", ptype="read"))
