from __future__ import annotations

import frappe
from frappe.utils.data import cint

from wikify.rag import history


@frappe.whitelist()
def list_sessions(project: str | None = None, limit: int = history.SESSION_PAGE_LENGTH) -> list[dict]:
	return history.list_sessions(project=project, limit=cint(limit) or history.SESSION_PAGE_LENGTH)


@frappe.whitelist()
def get_session(name: str | int) -> dict:
	return history.get_session(name)


@frappe.whitelist(methods=["POST"])
def delete_session(name: str | int) -> None:
	history.delete_session(name)
