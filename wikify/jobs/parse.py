"""Parse job — the walking skeleton's spine (Slice 1b).

Render + baseline-parse every page of an Import's PDF into Source Document +
Source Page rows, streaming progress + log lines, and land the Import in `Review`.
Scoring, sectionizing, and classification are added in later slices.
"""

from __future__ import annotations

import frappe
from frappe.utils import now_datetime

from wikify.engine import llm, parse_pdf, remediate_pdf
from wikify.engine.sectionize import rebuild_and_classify
from wikify.jobs._util import log, project_context, publish_progress


def run(import_name: str) -> None:
	imp = frappe.get_doc("Wikify Import", import_name)
	try:
		imp.db_set("status", "Parsing")
		imp.db_set("error", None)
		imp.db_set("started_at", now_datetime())
		publish_progress(import_name, 0, "Starting parse", status="Parsing")
		log(import_name, "info", "parse", f"Starting parse of {imp.import_title}")

		context = project_context(imp)
		if context:
			log(import_name, "info", "parse", f"Using project context ({len(context)} chars)")

		pdf_path = frappe.get_doc("File", {"file_url": imp.pdf}).get_full_path()

		def progress_cb(done: int, total: int) -> None:
			publish_progress(import_name, done / total * 100, f"Parsing page {done}/{total}")

		# Post-page-loop phases (sectionize + classify) keep the bar at 100 but update the
		# label, so a long classify pass reads as progressing, not stuck on the last page.
		def stage_cb(label: str) -> None:
			publish_progress(import_name, 100, label)

		def page_cb(page_no, total, kind, score, metrics) -> None:
			# Per-stage cost (judge) rides along in meta so the Overview log can show it.
			cost = sum(m["cost"] for m in metrics if m.get("cost")) or None
			meta = {"page_no": page_no, "kind": kind, "composite": score.composite, "verdict": score.verdict}
			if metrics:
				meta["cost"] = cost
				meta["models"] = [m["model"] for m in metrics]
			suffix = f" — {score.verdict} {score.composite}"
			if cost:
				suffix += f" (${cost:.4f})"
			log(import_name, "info", "parse", f"Page {page_no}/{total} ({kind}){suffix}", meta=meta)

		# Parse + score only; the tree is built after remediation (below) so it's built once,
		# over the cleaned canonical markdown rather than the raw baseline.
		source_document = parse_pdf(
			pdf_path,
			title=imp.import_title,
			import_name=import_name,
			pdf_url=imp.pdf,
			project=imp.project,
			project_context=context,
			progress_cb=progress_cb,
			page_cb=page_cb,
			stage_cb=stage_cb,
			sectionize=False,
		)
		imp.db_set("source_document", source_document)

		# Auto-remediate EVERY page before Review (0.4 slice 22 — no flagged-only gate), so
		# the section tree (and any wiki built from it) come from cleaned, artifact-free
		# markdown and no "good enough" page dodges the VLM pass. remediate_pdf rebuilds the
		# tree + classifies over the canonical markdown, standing in for parse's own
		# sectionize pass. Needs cloud models; without a key, build the tree on baseline —
		# loudly, never silently.
		page_count_ = frappe.db.count("Source Page", {"source_document": source_document})
		if llm.has_openrouter():
			imp.db_set("status", "Remediating")
			publish_progress(import_name, 0, f"Remediating {page_count_} pages", status="Remediating")
			log(import_name, "info", "remediate", f"Remediating all {page_count_} pages (dual-pass)")

			def rem_progress_cb(done: int, total: int) -> None:
				publish_progress(
					import_name, (done / total * 100) if total else 100, f"Remediating page {done}/{total}"
				)

			def rem_page_cb(page_no, total, method, adopted, base_c, new_c, metrics) -> None:
				cost = sum(m["cost"] for m in metrics if m.get("cost")) or None
				delta = round((new_c or 0) - (base_c or 0), 3)
				verb = "adopted" if adopted else "kept baseline"
				meta = {"page_no": page_no, "method": method, "adopted": adopted, "delta": delta}
				suffix = f" — {method} {verb} ({base_c}→{new_c}, Δ{delta:+.3f})"
				if cost:
					meta["cost"] = cost
					suffix += f" (${cost:.4f})"
				log(import_name, "info", "remediate", f"Page {page_no}{suffix}", meta=meta)

			result = remediate_pdf(
				source_document,
				pdf_path,
				scope="all",
				project_context=context,
				progress_cb=rem_progress_cb,
				page_cb=rem_page_cb,
				stage_cb=stage_cb,
			)
			if result.get("cost"):
				log(
					import_name,
					"info",
					"remediate",
					f"Remediation done — {result['adopted']}/{result['targets']} adopted, ${result['cost']:.4f}",
					meta={"cost": result["cost"], "adopted": result["adopted"]},
				)
		else:
			log(
				import_name,
				"warn",
				"remediate",
				"VLM pass skipped: no OPENROUTER_KEY — pages are baseline-only",
			)
			rebuild_and_classify(source_document, pdf_path, stage_cb, project_context=context)

		mean_score, page_count = frappe.db.get_value(
			"Source Document", source_document, ["mean_score", "page_count"]
		)
		n_sections = frappe.db.count("Source Section", {"source_document": source_document})
		imp.db_set("page_count", page_count)
		imp.db_set("completed_at", now_datetime())
		publish_progress(import_name, 100, f"Parsed {page_count} pages", status="Review")
		log(
			import_name,
			"info",
			"parse",
			f"Done — {page_count} pages, {n_sections} sections, mean {mean_score}, status Review",
		)
	except Exception:
		error = frappe.get_traceback()
		imp.db_set("status", "Failed")
		imp.db_set("error", error)
		frappe.db.commit()
		log(import_name, "error", "parse", "Parse failed — see error on the import")
		raise
