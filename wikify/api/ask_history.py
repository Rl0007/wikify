"""Whitelisted read API for Ask conversations — the sidebar and the transcript view.

Kept out of `api/rag.py` on purpose: that module is the retrieval surface, this one only
reads back what `rag/history.py` wrote. Scoping is the DocType's `if_owner` permission,
applied by `get_list`/`check_permission` inside `rag.history`, so a user sees their own
conversations and a System Manager sees every one — no filter is re-implemented here.
"""

from __future__ import annotations

import frappe
from frappe.utils.data import cint

from wikify.rag import history


@frappe.whitelist()
def list_sessions(project: str | None = None, limit: int = history.SESSION_PAGE_LENGTH) -> list[dict]:
	"""Conversations the current user may read, newest first."""
	return history.list_sessions(project=project, limit=cint(limit) or history.SESSION_PAGE_LENGTH)


@frappe.whitelist()
def get_session(name: str | int) -> dict:
	"""One conversation with its turns, each answer carrying its route, citations and cost."""
	return history.get_session(name)


@frappe.whitelist(methods=["POST"])
def delete_session(name: str | int) -> None:
	history.delete_session(name)
