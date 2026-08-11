"""Seed data for Wikify masters.

`Section Type` is the classifier taxonomy — derived bottom-up from the real manuals'
headings (see the POC README). Seeded idempotently from both `after_install` (fresh
sites) and a `post_model_sync` patch (existing sites on `bench migrate`). Editable
afterward — the classifier reads whatever rows exist, so a corpus can re-derive types.
"""

from __future__ import annotations

import frappe

UNCATEGORIZED = "Uncategorized"

# (type_name, label, color, description) — order is the display order in Explore.
# The 11 POC types; `other` is the catch-all (is_other), always last.
SECTION_TYPES: list[tuple[str, str, str, str]] = [
	(
		"staff_roles_and_responsibilities",
		"Staff Roles & Responsibilities",
		"#3b82f6",
		"Job descriptions and role profiles — one section per post: purpose of the role, "
		"duties, reporting and supervision lines, required qualifications and licences, "
		"and the staffing structure.",
	),
	(
		"clinical_protocols",
		"Clinical Protocols",
		"#10b981",
		"Clinical guidelines and care pathways — assessment, risk scoring, treatment steps, "
		"escalation thresholds and review intervals for a named condition.",
	),
	(
		"surgical_procedures",
		"Surgical Procedures",
		"#ef4444",
		"Operative and theatre practice — surgical steps, peri-operative checklists, swab "
		"and instrument counts, and recovery.",
	),
	(
		"patient_management",
		"Patient Management",
		"#8b5cf6",
		"The patient or client journey — referral, admission, triage, care planning, "
		"monitoring, review and discharge.",
	),
	(
		"medication_management",
		"Medication Management",
		"#f59e0b",
		"Medicines handling — prescribing, dosing, administration, controlled drugs, "
		"storage, repeat prescriptions and reconciliation.",
	),
	(
		"administrative_policies",
		"Administrative Policies",
		"#64748b",
		"Non-clinical policy sections — pay bands and salary scales, overtime, on-call and "
		"unsocial-hours payments, pension, leave and other benefits, expenses and mileage, "
		"working-hours and rota rules, and HR or finance governance.",
	),
	(
		"equipment_and_facilities",
		"Equipment & Facilities",
		"#14b8a6",
		"Devices, instruments and consumables — checks, maintenance, decontamination, "
		"stock control, and the physical environment.",
	),
	(
		"training_and_audits",
		"Training & Audits",
		"#ec4899",
		"Becoming and staying competent — induction, mandatory training, competency "
		"frameworks and sign-off, preceptorship, appraisal, and the audit and compliance cycle.",
	),
	(
		"research_and_documentation",
		"Research & Documentation",
		"#6366f1",
		"Evidence and record-keeping — research methods, references, forms, templates and "
		"documentation standards.",
	),
	(
		"emergency_procedures",
		"Emergency Procedures",
		"#f97316",
		"Urgent response — resuscitation, emergency calls and escalation, major incident "
		"plans and other time-critical procedures.",
	),
	(
		"other",
		"Other",
		"#9ca3af",
		"Organisation overviews and front matter — who the provider is, its mission and "
		"values, its sites, the services it offers and its size (beds, list size, annual "
		'activity). An "about this organisation" or "who we are" section lives here, as '
		"does anything that fits no category above.",
	),
]


def seed_section_types() -> None:
	"""Insert any missing Section Type rows. Idempotent — never overwrites edits."""
	for type_name, label, color, description in SECTION_TYPES:
		if frappe.db.exists("Section Type", type_name):
			continue
		frappe.get_doc(
			{
				"doctype": "Section Type",
				"type_name": type_name,
				"label": label,
				"color": color,
				"description": description,
				"is_other": 1 if type_name == "other" else 0,
			}
		).insert(ignore_permissions=True)


def seed_uncategorized_project() -> str:
	"""Get-or-create the single default "Uncategorized" project. Idempotent.

	Keyed on `is_default` (not the name) so a renamed catch-all is still found.
	Returns the project name.
	"""
	existing = frappe.db.get_value("Wikify Project", {"is_default": 1}, "name")
	if existing:
		return existing
	doc = frappe.get_doc(
		{
			"doctype": "Wikify Project",
			"project_name": UNCATEGORIZED,
			"description": "Catch-all for unfiled documents.",
			"is_default": 1,
		}
	).insert(ignore_permissions=True)
	return doc.name
