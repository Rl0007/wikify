"""Scheduled maintenance for the Imports flow."""

from __future__ import annotations

import frappe
from frappe.utils import add_to_date, get_datetime, now_datetime

from wikify.jobs._util import log, publish_progress

#: Statuses that mean a background job owns the import right now.
RUNNING_STATUSES = ("Queued", "Parsing", "Remediating", "Generating Wiki")

#: A running import whose progress hasn't moved for this long has lost its worker.
#: Jobs write progress every few seconds (a 400-page remediation runs for the better
#: part of an hour but publishes on every page), so this keys off time-since-last-
#: progress and never touches a legitimately slow pass.
STALE_MINUTES = 30


def stale_cutoff():
	"""Progress older than this means the job is gone, not slow."""
	return add_to_date(now_datetime(), minutes=-STALE_MINUTES)


def is_stale(import_doc) -> bool:
	"""Is this import sitting in a running status with no progress written since the cutoff?"""
	return import_doc.status in RUNNING_STATUSES and get_datetime(import_doc.modified) < stale_cutoff()


def fail_stuck_imports() -> None:
	"""Fail imports whose worker died mid-job.

	Nothing else moves an import out of a running status, so a killed worker (OOM,
	deploy, hard timeout) leaves the UI showing an in-flight job forever with no way
	back except editing `status` by hand.
	"""
	stuck = frappe.get_all(
		"Wikify Import",
		filters={"status": ("in", RUNNING_STATUSES), "modified": ("<", stale_cutoff())},
		fields=["name", "status"],
	)
	for imp in stuck:
		message = (
			f"No progress for {STALE_MINUTES} minutes while {imp.status} — the worker running "
			f"this import was lost. Retry the import to restart it."
		)
		frappe.db.set_value("Wikify Import", imp.name, {"status": "Failed", "error": message})
		publish_progress(imp.name, 100, "Worker lost", status="Failed")
		log(imp.name, "error", "scheduler", message)
