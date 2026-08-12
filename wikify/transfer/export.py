"""Reading one project out of a site and into a bundle file.

Everything here is a read: export never mutates the source site, so it is safe to run
against a live corpus mid-session.
"""

from __future__ import annotations

import os
import zipfile

import frappe

from wikify.transfer import bundle


def document_names(project: str) -> list[str]:
	"""Every Source Document in `project`. The scope key for all the child row sets."""
	return frappe.get_all("Source Document", filters={"project": project}, pluck="name")


def collect_rows(project: str) -> dict[str, list[dict]]:
	"""Gather every row belonging to `project`, keyed by doctype.

	One query per doctype scoped by an `in` filter rather than a walk per document — the
	corpora this runs against are hundreds of pages, and a per-document fetch would be the
	n+1 that makes a 300-page export feel broken.
	"""
	documents = document_names(project)
	rows: dict[str, list[dict]] = {}

	rows["Wikify Project"] = frappe.get_all("Wikify Project", filters={"name": project}, fields=["*"])
	if not rows["Wikify Project"]:
		frappe.throw(f"Wikify Project '{project}' does not exist.")

	rows["Wikify Import"] = frappe.get_all("Wikify Import", filters={"project": project}, fields=["*"])
	rows["Source Document"] = frappe.get_all("Source Document", filters={"project": project}, fields=["*"])

	if documents:
		in_project = {"source_document": ["in", documents]}
		rows["Source Page"] = frappe.get_all(
			"Source Page", filters=in_project, fields=["*"], order_by="source_document asc, page_no asc"
		)
		# lft order puts every parent ahead of its children, which is exactly the order a
		# restore has to insert them in. Sorting here means the restore never has to.
		rows["Source Section"] = frappe.get_all(
			"Source Section", filters=in_project, fields=["*"], order_by="lft asc"
		)
		rows["Section Reference"] = frappe.get_all("Section Reference", filters=in_project, fields=["*"])
	else:
		rows["Source Page"] = []
		rows["Source Section"] = []
		rows["Section Reference"] = []

	used_types = {row.get("section_type") for row in rows["Source Section"] if row.get("section_type")}
	rows["Section Type"] = (
		frappe.get_all("Section Type", filters={"name": ["in", sorted(used_types)]}, fields=["*"])
		if used_types
		else []
	)
	return rows


def attachment_urls(rows: dict[str, list[dict]]) -> list[str]:
	"""Every distinct `file_url` referenced by the exported rows, in a stable order."""
	urls: set[str] = set()
	for doctype, fields in bundle.ATTACH_FIELDS.items():
		for row in rows.get(doctype, []):
			for field in fields:
				value = row.get(field)
				if value:
					urls.add(value)
	return sorted(urls)


def read_attachment(file_url: str) -> bytes | None:
	"""The bytes behind a `file_url`, or None if the row points at a file that is gone.

	Read straight off the site path instead of loading a `File` doc per attachment: a
	300-page document has 300 page PNGs, and that is 300 `get_doc` calls for a filename we
	can already derive from the URL.
	"""
	path = frappe.get_site_path(file_url.lstrip("/"))
	if not os.path.isfile(path):
		return None
	with open(path, "rb") as handle:
		return handle.read()


def export_project(project: str, *, output_dir: str | None = None) -> dict:
	"""Write `project` to a bundle file and return a report of what went into it.

	Missing attachments are reported rather than raised: a corpus whose page images were
	cleaned up is still worth moving, because the markdown — the part that cost money — is
	in the rows. The caller decides whether the gap matters.
	"""
	rows = collect_rows(project)
	payloads: dict[str, bytes] = {}
	counts: dict[str, int] = {}
	checksums: dict[str, str] = {}

	for path, doctype in bundle.ROW_SETS:
		scrubbed = [bundle.scrub_row(doctype, row) for row in rows.get(doctype, [])]
		payload = bundle.serialise(scrubbed)
		payloads[path] = payload
		counts[doctype] = len(scrubbed)
		checksums[path] = bundle.checksum(payload)

	file_index: dict[str, dict] = {}
	file_bytes: dict[str, bytes] = {}
	missing: list[str] = []
	for file_url in attachment_urls(rows):
		content = read_attachment(file_url)
		if content is None:
			missing.append(file_url)
			continue
		digest = bundle.checksum(content).split(":", 1)[1]
		name = os.path.basename(file_url)
		member = f"files/{digest[:16]}-{name}"
		file_bytes[member] = content
		file_index[file_url] = {"member": member, "file_name": name}

	manifest = bundle.build_manifest(project=rows["Wikify Project"][0], counts=counts, checksums=checksums)
	manifest["attachments"] = {"stored": len(file_index), "missing": missing}

	slug = frappe.scrub(rows["Wikify Project"][0].get("project_name") or project)
	output_dir = output_dir or frappe.get_site_path("private", "files")
	os.makedirs(output_dir, exist_ok=True)
	target = os.path.join(output_dir, f"{slug}{bundle.BUNDLE_SUFFIX}")

	with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
		archive.writestr(bundle.MANIFEST_NAME, bundle.serialise(manifest).decode("utf-8"))
		for path, payload in payloads.items():
			archive.writestr(path, payload)
		archive.writestr(bundle.FILE_INDEX_NAME, bundle.serialise(file_index).decode("utf-8"))
		for member, content in file_bytes.items():
			archive.writestr(member, content)

	return {
		"path": target,
		"size": os.path.getsize(target),
		"counts": counts,
		"attachments": manifest["attachments"],
	}
