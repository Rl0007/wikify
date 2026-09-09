# Copyright (c) 2026, BWH and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe.model.document import Document


class WikifyAskSession(Document):
	def before_insert(self) -> None:
		self.user = self.user or frappe.session.user
		self.started_at = self.started_at or frappe.utils.now_datetime()

	def on_trash(self) -> None:
		"""Cascade-delete this conversation's turns (they aren't a child table)."""
		frappe.db.delete("Wikify Ask Message", {"session": self.name})
