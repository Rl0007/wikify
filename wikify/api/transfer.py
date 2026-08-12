"""Whitelisted endpoints for moving a project between sites.

Both sides are gated on System Manager rather than on project permissions: an export reads
every row in a project regardless of who may see which section, and an import writes rows
that the caller could not create one at a time. Anything looser would be a way around the
per-project ACL the rest of the app enforces.
"""

from __future__ import annotations

import os

import frappe

from wikify.transfer import bundle, export, restore


@frappe.whitelist()
def export_project(project: str) -> dict:
	"""Write `project` to a bundle in the site's private files and report what went in."""
	frappe.only_for("System Manager")
	report = export.export_project(project)
	# The caller gets a download URL, never a filesystem path — the path is meaningless to a
	# browser and leaks the site's layout.
	report["file_url"] = f"/private/files/{os.path.basename(report['path'])}"
	report.pop("path", None)
	return report


@frappe.whitelist()
def bundle_preview(file_url: str) -> dict:
	"""Read a bundle's manifest without importing it.

	Lets the UI show what is about to land — source site, project, row counts — before
	anyone commits to writing it. Validation failures surface here rather than mid-import.
	"""
	frappe.only_for("System Manager")
	manifest, rows, file_index = restore.read_bundle(bundle_path(file_url))
	return {
		"manifest": manifest,
		"counts": {doctype: len(row_set) for doctype, row_set in rows.items()},
		"attachments": len(file_index),
	}


@frappe.whitelist()
def import_bundle(file_url: str, rebuild_index: bool = True) -> dict:
	"""Import an uploaded bundle into this site."""
	frappe.only_for("System Manager")
	return restore.import_bundle(bundle_path(file_url), rebuild_index=frappe.utils.cint(rebuild_index))


def bundle_path(file_url: str) -> str:
	"""Resolve an uploaded `file_url` to a readable path, refusing anything outside the site.

	`file_url` arrives from the client, so it is treated as hostile: without this check a
	crafted `../../` would let a System Manager read an arbitrary file off the host as if it
	were a bundle.
	"""
	site_root = os.path.realpath(frappe.get_site_path())
	path = os.path.realpath(frappe.get_site_path(file_url.lstrip("/")))
	if not path.startswith(site_root + os.sep):
		frappe.throw("Bundle path must be a file uploaded to this site.")
	if not os.path.isfile(path):
		frappe.throw(f"No bundle found at '{file_url}'.")
	if not path.endswith(bundle.BUNDLE_SUFFIX):
		frappe.throw(f"Expected a {bundle.BUNDLE_SUFFIX} bundle.")
	return path
