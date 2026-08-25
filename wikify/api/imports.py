"""Whitelisted APIs for the Imports flow."""

from __future__ import annotations

import frappe

from wikify.engine import preview_wiki as _preview_wiki
from wikify.seed import seed_uncategorized_project

#: Guard against a runaway drag-and-drop — one worker chews through these serially.
MAX_BATCH = 25


def _create_import(pdf_file_url: str, title: str, project: str) -> str:
	"""Create one Wikify Import and enqueue its parse job. Returns the Import's name."""
	imp = frappe.new_doc("Wikify Import")
	imp.import_title = title or pdf_file_url.rsplit("/", 1)[-1].removesuffix(".pdf")
	imp.pdf = pdf_file_url
	imp.project = project
	imp.status = "Queued"
	imp.insert()

	frappe.enqueue(
		"wikify.jobs.parse.run",
		queue="long",
		timeout=3600,
		import_name=imp.name,
	)
	return imp.name


@frappe.whitelist()
def start_import(pdf_file_url: str, title: str, project: str | None = None) -> str:
	"""Create a Wikify Import for an uploaded PDF and enqueue the parse job.

	`project` is the owning Wikify Project; it defaults to "Uncategorized" when omitted.
	Returns the new Import's name so the SPA can route to its detail page.
	"""
	return _create_import(pdf_file_url, title, project or seed_uncategorized_project())


@frappe.whitelist()
def start_imports(files: list[dict] | str, project: str | None = None) -> list[str]:
	"""Batch sibling of `start_import` — one Import per uploaded PDF, one project.

	`files` is a list of `{"file_url": ..., "title": ...}`. Imports are created and
	enqueued in the given order; the long queue then works through them. Returns the new
	Import names in the same order.
	"""
	if isinstance(files, str):
		files = frappe.parse_json(files)
	if not files:
		frappe.throw("No files to import.")
	if len(files) > MAX_BATCH:
		frappe.throw(f"Import at most {MAX_BATCH} PDFs at a time (got {len(files)}).")

	if any(not f.get("file_url") for f in files):
		frappe.throw("Every file needs a file_url.")

	# Resolve the default once — not once per file.
	project = project or seed_uncategorized_project()
	return [_create_import(f["file_url"], f.get("title"), project) for f in files]


@frappe.whitelist()
def trigger_remediation(import_name: str, scope: str = "flagged") -> str:
	"""Enqueue the remediation pass over an imported doc's pages.

	`scope` is `flagged` (non-pass pages only) or `all` (every page). Only runs from
	`Review`; flips the Import to `Remediating` and returns its name.
	"""
	if scope not in ("flagged", "all"):
		frappe.throw(f"Invalid scope: {scope!r} (expected 'flagged' or 'all').")

	imp = frappe.get_doc("Wikify Import", import_name)
	if not imp.source_document:
		frappe.throw("Nothing to remediate — parse hasn't produced a document yet.")
	if imp.status != "Review":
		frappe.throw(f"Can only remediate from Review (current status: {imp.status}).")

	imp.db_set("status", "Remediating")
	frappe.enqueue(
		"wikify.jobs.remediate.run",
		queue="long",
		timeout=3600,
		import_name=import_name,
		scope=scope,
	)
	return import_name


@frappe.whitelist()
def reclassify(import_name: str) -> str:
	"""Re-tag the doc's Source Sections after manual tree edits.

	Parse/remediate classify eagerly; this is the on-demand re-run. It doesn't change
	the import status (a doc stays in Review or Graphed while re-tagging), so it's
	available at any post-parse stage.
	"""
	imp = frappe.get_doc("Wikify Import", import_name)
	if not imp.source_document:
		frappe.throw("Nothing to classify — parse hasn't produced a document yet.")

	frappe.enqueue(
		"wikify.jobs.classify.run",
		queue="long",
		timeout=1800,
		import_name=import_name,
	)
	return import_name


# --- Slice 7: wiki generation --------------------------------------------------------


@frappe.whitelist()
def preview_wiki(import_name: str) -> dict:
	"""Projected wiki structure (no writes) — the included-section tree + counts.

	Drives the Wiki tab's preview so the user sees what generation will produce before
	committing. Available once a document exists; the included subset reflects the tree
	edits made in review.
	"""
	imp = frappe.get_doc("Wikify Import", import_name)
	if not imp.source_document:
		frappe.throw("Nothing to preview — parse hasn't produced a document yet.")
	preview = _preview_wiki(imp.source_document)
	preview["wiki_space"] = frappe.db.get_value("Source Document", imp.source_document, "wiki_space")
	return preview


@frappe.whitelist()
def generate_wiki(
	import_name: str,
	wiki_space: str | None = None,
	new_space: dict | str | None = None,
) -> str:
	"""Enqueue wiki generation under an existing or new Wiki Space.

	Pass either `wiki_space` (existing space name) or `new_space` ({space_name, route}).
	Only runs once the tree is approved (`Graphed`) or has already been generated
	(`Completed` → regenerate in place). Flips the Import to `Generating Wiki`.
	"""
	imp = frappe.get_doc("Wikify Import", import_name)
	if not imp.source_document:
		frappe.throw("Nothing to generate — parse hasn't produced a document yet.")
	if imp.status not in ("Graphed", "Completed"):
		frappe.throw(
			f"Approve the section tree first — can only generate from Graphed or Completed "
			f"(current status: {imp.status})."
		)
	if isinstance(new_space, str):
		new_space = frappe.parse_json(new_space)
	if not wiki_space and not new_space:
		frappe.throw("Choose an existing Wiki Space or provide a new one.")

	imp.db_set("status", "Generating Wiki")
	frappe.enqueue(
		"wikify.jobs.generate.run",
		queue="long",
		timeout=3600,
		import_name=import_name,
		wiki_space=wiki_space,
		new_space=new_space,
	)
	return import_name
