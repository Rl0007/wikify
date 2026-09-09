from __future__ import annotations

import frappe
from frappe import _

from wikify.seed import seed_uncategorized_project


@frappe.whitelist(methods=["POST"])
def create_project(project_name: str, description: str = "") -> str:
	project_name = (project_name or "").strip()
	if not project_name:
		frappe.throw(_("Project name is required."))
	proj = frappe.new_doc("Wikify Project")
	proj.project_name = project_name
	proj.description = description
	proj.insert()
	return proj.name


@frappe.whitelist(methods=["POST"])
def update_project(
	name: str,
	project_name: str | None = None,
	description: str | None = None,
	context_prompt: str | None = None,
	agent_model: str | None = None,
	status: str | None = None,
) -> str:
	proj = frappe.get_doc("Wikify Project", name)
	if project_name is not None:
		stripped = project_name.strip()
		if not stripped:
			frappe.throw(_("Project name is required."))
		proj.project_name = stripped
	if description is not None:
		proj.description = description
	if context_prompt is not None:
		proj.context_prompt = context_prompt
	if agent_model is not None:
		proj.agent_model = agent_model
	if status is not None:
		proj.status = status
	proj.save()
	return proj.name


@frappe.whitelist()
def list_projects() -> list[dict]:
	return frappe.get_all(
		"Wikify Project",
		fields=[
			"name",
			"project_name",
			"description",
			"status",
			"is_default",
			"import_count",
		],
		order_by="is_default desc, project_name asc",
	)


@frappe.whitelist()
def default_project() -> str:
	return seed_uncategorized_project()
