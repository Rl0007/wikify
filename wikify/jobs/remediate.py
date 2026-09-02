"""Remediate job (Slice 3) — route flagged/all pages through cleanup/VLM, adopt the
best per page, and recompute canonical markdown + canonical mean.

Streams progress + per-page log lines (method, adopted, score delta, cost) over the
same realtime channels the parse job uses, then lands the Import back in `Review`.
"""

from __future__ import annotations

import frappe

from wikify.engine import remediate_pdf
from wikify.jobs._util import log, project_context, publish_progress


def run(import_name: str, scope: str = "flagged", instruction: str = "") -> None:
	imp = frappe.get_doc("Wikify Import", import_name)
	try:
		imp.db_set("status", "Remediating")
		imp.db_set("error", None)
		publish_progress(import_name, 0, f"Starting remediation ({scope})", status="Remediating")
		log(import_name, "info", "remediate", f"Remediating {scope} pages of {imp.import_title}")

		context = project_context(imp)
		if context:
			log(import_name, "info", "remediate", f"Using project context ({len(context)} chars)")
		if instruction:
			log(import_name, "info", "remediate", f"Steering re-parse: {instruction}")

		pdf_path = frappe.get_doc("File", {"file_url": imp.pdf}).get_full_path()

		def progress_cb(done: int, total: int) -> None:
			percent = (done / total * 100) if total else 100
			publish_progress(import_name, percent, f"Remediating page {done}/{total}")

		def stage_cb(label: str) -> None:
			publish_progress(import_name, 100, label)

		def page_cb(page_no, total, method, adopted, base_c, new_c, metrics) -> None:
			cost = sum(m["cost"] for m in metrics if m.get("cost")) or None
			delta = round((new_c or 0) - (base_c or 0), 3)
			verb = "adopted" if adopted else "kept baseline"
			meta = {
				"page_no": page_no,
				"method": method,
				"adopted": adopted,
				"base": base_c,
				"new": new_c,
				"delta": delta,
			}
			suffix = f" — {method} {verb} ({base_c}→{new_c}, Δ{delta:+.3f})"
			if cost:
				meta["cost"] = cost
				suffix += f" (${cost:.4f})"
			log(import_name, "info", "remediate", f"Page {page_no} {suffix}", meta=meta)

		result = remediate_pdf(
			imp.source_document,
			pdf_path,
			scope=scope,
			project_context=context,
			instruction=instruction,
			progress_cb=progress_cb,
			page_cb=page_cb,
			stage_cb=stage_cb,
		)

		imp.db_set("status", "Review")
		publish_progress(import_name, 100, f"Remediated {result['targets']} pages", status="Review")
		log(
			import_name,
			"info",
			"remediate",
			f"Done — {result['adopted']}/{result['targets']} adopted, "
			f"canonical mean {result['canonical_mean']}, {result['sections']} sections rebuilt",
		)
	except Exception:
		error = frappe.get_traceback()
		# Revert to Review (the parse result is intact); surface the error on the import.
		imp.db_set("status", "Review")
		imp.db_set("error", error)
		frappe.db.commit()
		log(import_name, "error", "remediate", "Remediation failed — see error on the import")
		publish_progress(import_name, 100, "Remediation failed", status="Review")
		raise
