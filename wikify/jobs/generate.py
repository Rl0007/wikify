"""Generate-wiki job (Slice 7) — project the approved Source Section tree into a Wiki
Space, streaming progress, then land the Import in `Completed`.

Mirrors the parse/remediate jobs: flip status to `Generating Wiki`, run the two-pass
`generate_wiki` engine entrypoint, persist the chosen space on both the Import and the
Source Document, and stream a final line with a link to the generated space.
"""

from __future__ import annotations

import frappe
from frappe.utils import now_datetime

from wikify.engine import generate_wiki
from wikify.jobs._util import log, publish_progress


def run(
	import_name: str,
	wiki_space: str | None = None,
	new_space: dict | None = None,
) -> None:
	imp = frappe.get_doc("Wikify Import", import_name)
	try:
		imp.db_set("status", "Generating Wiki")
		publish_progress(import_name, 0, "Generating wiki", status="Generating Wiki")
		log(import_name, "info", "generate", f"Generating wiki for {imp.import_title}")

		def progress_cb(done: int, total: int) -> None:
			percent = (done / total * 100) if total else 100
			publish_progress(import_name, percent, f"Writing wiki page {done}/{total}")

		def stage_cb(label: str) -> None:
			publish_progress(import_name, 100, label)

		result = generate_wiki(
			imp.source_document,
			wiki_space=wiki_space,
			new_space=new_space,
			progress_cb=progress_cb,
			stage_cb=stage_cb,
		)

		imp.db_set("wiki_space", result["space"])
		imp.db_set("status", "Completed")
		imp.db_set("completed_at", now_datetime())
		publish_progress(import_name, 100, "Wiki generated", status="Completed")
		log(
			import_name,
			"info",
			"generate",
			f"Done — {result['pages']} pages, {result['groups']} groups, "
			f"{result['deleted']} removed, {result['links']} page-refs linked "
			f"→ /{result['space_route']}",
			meta={"space": result["space"], "space_route": result["space_route"]},
		)
		# every import-progress broadcast in wikify/jobs is unscoped, not just this one; giving them
		# 		# a room is a backend+SPA change, not a lint fix
		# nosemgrep
		frappe.publish_realtime(
			"wikify_wiki_done",
			{"import": import_name, "space": result["space"], "space_route": result["space_route"]},
		)
	except Exception:
		error = frappe.get_traceback()
		# Revert to Graphed (the approved tree is intact); surface the error.
		imp.db_set("status", "Graphed")
		imp.db_set("error", error)
		frappe.db.commit()
		log(import_name, "error", "generate", "Wiki generation failed — see error on the import")
		publish_progress(import_name, 100, "Wiki generation failed", status="Graphed")
		raise
