# Copyright (c) 2026, BWH and contributors
# For license information, please see license.txt

"""Permission checks for surfaces outside the SPA — currently the desk apps screen."""

import frappe


def has_app_permission() -> bool:
	"""Gate the Wikify tile on the desk apps screen.

	Wikify's DocTypes are System Manager-only, so anyone who can read an Import can use
	the app; everyone else would land on an empty SPA.
	"""
	if frappe.session.user == "Administrator":
		return True

	return bool(frappe.has_permission("Wikify Import", ptype="read"))
