"""Writing a bundle into a site.

Named `restore` rather than `import` because `import` is a reserved word and a module
called that cannot be imported by name.

Two invariants govern this module:

**Every name is reallocated.** Bundle names are never reused, not even the hash-named ones
that look collision-proof. A hash name is only unique on the site that minted it, and the
alternative — keep-if-free, remap-if-taken — means the same bundle imports differently
depending on what the target already holds. Reallocating always makes one code path
instead of two, and nothing outside the bundle refers to these names: the LanceDB index is
rebuilt from the rows, and the wiki is regenerated.

**Nothing is committed until everything succeeds.** The whole insert runs inside a
savepoint. A partially imported corpus is worse than a failed one, because it looks
complete — sections resolve, retrieval returns hits, and the gap only shows up as a wrong
answer to a question whose evidence never arrived.
"""

from __future__ import annotations

import json
import zipfile

import frappe
from frappe.utils.file_manager import save_file
from frappe.utils.nestedset import rebuild_tree

from wikify.rag import index
from wikify.transfer import bundle

SAVEPOINT = "wikify_bundle_import"


def read_bundle(path: str) -> tuple[dict, dict[str, list[dict]], dict]:
	"""Open a bundle, validate it, and return `(manifest, rows_by_doctype, file_index)`.

	Validation happens before a single row is written: schema version, then a checksum and
	a row count per set. A truncated or edited bundle is rejected here rather than
	discovered halfway through an insert.
	"""
	with zipfile.ZipFile(path) as archive:
		manifest = json.loads(archive.read(bundle.MANIFEST_NAME))
		bundle.assert_supported(manifest)

		rows: dict[str, list[dict]] = {}
		for member, doctype in bundle.ROW_SETS:
			payload = archive.read(member)
			expected = manifest.get("checksums", {}).get(member)
			actual = bundle.checksum(payload)
			if expected != actual:
				raise ValueError(f"{member} fails its checksum ({expected} != {actual}); bundle is corrupt.")
			rows[doctype] = json.loads(payload)
			declared = manifest.get("counts", {}).get(doctype)
			if declared is not None and declared != len(rows[doctype]):
				raise ValueError(
					f"{member} holds {len(rows[doctype])} rows but the manifest declares {declared}."
				)

		file_index = json.loads(archive.read(bundle.FILE_INDEX_NAME))
	return manifest, rows, file_index


def resolve_links(doctype: str, payload: dict, remap: dict[str, dict[str, str]]) -> None:
	"""Rewrite every link in `payload` from bundle names to the names just allocated.

	A link whose target is absent from the remap is dropped rather than carried through: it
	would point at a name that means something entirely different on this site, and a link
	to the wrong section is more damaging than no link at all.
	"""
	for field, target_doctype in bundle.LINK_FIELDS.get(doctype, {}).items():
		old_value = payload.get(field)
		if not old_value:
			continue
		new_value = remap.get(target_doctype, {}).get(old_value)
		if new_value:
			payload[field] = new_value
		else:
			payload.pop(field, None)


def merge_section_types(rows: list[dict], remap: dict[str, dict[str, str]]) -> int:
	"""Adopt the bundle's Section Types, letting the target's existing rows win.

	`Section Type` is named `field:type_name`, so its name IS the taxonomy term. Two sites
	that both know "Job Description" must converge on one row, not two — hence merge rather
	than reallocate, and hence an identity entry in the remap.
	"""
	created = 0
	existing = set(frappe.get_all("Section Type", pluck="name"))
	for row in rows:
		name = row.get("name")
		remap["Section Type"][name] = name
		if name in existing:
			continue
		payload = {key: value for key, value in row.items() if key != "name"}
		frappe.get_doc({"doctype": "Section Type", **payload}).insert()
		created += 1
	return created


def insert_rows(rows: dict[str, list[dict]], remap: dict[str, dict[str, str]]) -> dict[str, int]:
	"""Insert every row set in bundle order, recording old name → new name as it goes."""
	created: dict[str, int] = {}
	for _member, doctype in bundle.ROW_SETS:
		if doctype in bundle.MERGE_BY_NAME:
			created[doctype] = merge_section_types(rows.get(doctype, []), remap)
			continue

		deferred = bundle.DEFERRED_LINKS.get(doctype, ())
		count = 0
		for row in rows.get(doctype, []):
			payload = {key: value for key, value in row.items() if key != "name"}
			for field in deferred:
				payload.pop(field, None)
			resolve_links(doctype, payload, remap)
			if doctype == "Wikify Project":
				# The source site's default project has no authority here, and two rows
				# flagged default is a state the app does not expect.
				payload["is_default"] = 0
			document = frappe.get_doc({"doctype": doctype, **payload})
			document.insert()
			remap[doctype][row["name"]] = document.name
			count += 1
		created[doctype] = count
	return created


def fill_deferred_links(rows: dict[str, list[dict]], remap: dict[str, dict[str, str]]) -> int:
	"""Close the `Wikify Import` ↔ `Source Document` cycle now that both sides exist."""
	filled = 0
	for doctype, fields in bundle.DEFERRED_LINKS.items():
		for row in rows.get(doctype, []):
			new_name = remap[doctype].get(row["name"])
			if not new_name:
				continue
			updates = {}
			for field in fields:
				old_value = row.get(field)
				target_doctype = bundle.LINK_FIELDS[doctype][field]
				new_value = remap.get(target_doctype, {}).get(old_value) if old_value else None
				if new_value:
					updates[field] = new_value
			if updates:
				frappe.db.set_value(doctype, new_name, updates)
				filled += 1
	return filled


def restore_attachments(
	path: str, rows: dict[str, list[dict]], remap: dict[str, dict[str, str]], file_index: dict
) -> int:
	"""Re-save the bundled files and repoint every attach field at the new URLs.

	Written with `db.set_value` on purpose: a `Source Page` save would fire the propagation
	hook once per page, queueing a re-section job for a document that is still mid-import.
	"""
	saved: dict[str, str] = {}
	repointed = 0
	with zipfile.ZipFile(path) as archive:
		for doctype, fields in bundle.ATTACH_FIELDS.items():
			for row in rows.get(doctype, []):
				new_name = remap[doctype].get(row["name"])
				if not new_name:
					continue
				updates = {}
				for field in fields:
					old_url = row.get(field)
					entry = file_index.get(old_url) if old_url else None
					if not entry:
						# Attachment was already missing when the bundle was written; the
						# row keeps its stale URL rather than gaining a broken new one.
						continue
					if old_url not in saved:
						file_doc = save_file(
							entry["file_name"],
							archive.read(entry["member"]),
							doctype,
							new_name,
							is_private=1,
						)
						saved[old_url] = file_doc.file_url
					updates[field] = saved[old_url]
				if updates:
					frappe.db.set_value(doctype, new_name, updates)
					repointed += 1
	return repointed


def import_bundle(path: str, *, rebuild_index: bool = True) -> dict:
	"""Import a bundle and return a report of what was created.

	The index rebuild runs after the transaction commits, never inside it: LanceDB is not
	part of the SQL transaction, so an index built inside a savepoint that later rolls back
	would describe rows that no longer exist.
	"""
	manifest, rows, file_index = read_bundle(path)
	remap: dict[str, dict[str, str]] = {doctype: {} for _member, doctype in bundle.ROW_SETS}

	frappe.db.savepoint(SAVEPOINT)
	try:
		created = insert_rows(rows, remap)
		deferred = fill_deferred_links(rows, remap)
		attachments = restore_attachments(path, rows, remap, file_index)
		# Trust the parent links, not the bundle's bounds. Frappe recomputes lft/rgt for the
		# whole doctype, which also repairs any pre-existing drift on the target.
		# ponytail: rebuilds every Source Section on the site, not just the imported ones;
		# scope it if a site ever holds enough projects for this to be slow.
		rebuild_tree("Source Section")
	except Exception:
		frappe.db.rollback(save_point=SAVEPOINT)
		raise
	frappe.db.commit()

	project = remap["Wikify Project"].get(manifest["project"]["name"])
	report = {
		"project": project,
		"source_project": manifest["project"]["name"],
		"source_site": manifest.get("source_site"),
		"created": created,
		"deferred_links": deferred,
		"attachments": attachments,
	}
	if rebuild_index and project:
		report["index"] = index.rebuild_project(project)
	return report
