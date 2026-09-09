"""Write / tree tools (0.2 Slice 14) — reuse the `api.sections` NestedSet mutations.

These apply directly (cheap, reversible by re-editing): move, rename, retag, and the
include-in-wiki toggle. Each calls the existing whitelisted mutation so `lft`/`rgt`/
`level`/`hierarchy_path`/`is_group` stay consistent, then returns a short confirming
summary. All are flagged `mutates=True` so the loop tells open Tree views to refetch.
"""

from __future__ import annotations

import frappe
from frappe import _

from wikify.agent.context import Ctx
from wikify.agent.registry import Tool
from wikify.api import sections


def _title(name: str) -> str:
	return frappe.db.get_value("Source Section", name, "title") or name


def _move_section(ctx: Ctx, args: dict) -> str:
	name = args.get("name")
	if not name:
		return _("Provide the section `name` (the id shown in <angle brackets> in the tree).")
	new_parent = args.get("new_parent") or None
	new_index = args.get("new_index")
	try:
		sections.move_section(name, new_parent=new_parent, new_index=new_index)
	except frappe.ValidationError as e:
		return _("Couldn't move section: {0}").format(str(e))
	where = f"under {_title(new_parent)}" if new_parent else "to the top level"
	return _("Moved '{0}' {1}.").format(_title(name), where)


def _rename_section(ctx: Ctx, args: dict) -> str:
	name, title = args.get("name"), args.get("title")
	if not name or not (title or "").strip():
		return _("Provide the section `name` and a non-empty `title`.")
	old = _title(name)
	try:
		sections.rename_section(name, title)
	except frappe.ValidationError as e:
		return _("Couldn't rename section: {0}").format(str(e))
	return _("Renamed '{0}' → '{1}'.").format(old, title.strip())


def _set_section_type(ctx: Ctx, args: dict) -> str:
	name, section_type = args.get("name"), args.get("section_type")
	if not name:
		return _("Provide the section `name`.")
	try:
		res = sections.set_section_type(name, section_type)
	except frappe.ValidationError as e:
		return _("Couldn't retag section: {0}").format(str(e))
	tag = res["section_type"] or "(untagged)"
	return _("Tagged '{0}' as {1}.").format(_title(name), tag)


def _toggle_include_in_wiki(ctx: Ctx, args: dict) -> str:
	name = args.get("name")
	if not name:
		return _("Provide the section `name`.")
	include = args.get("include")
	include = True if include is None else bool(include)
	res = sections.toggle_include(name, include)
	verb = "Included" if include else "Excluded"
	return _("{0} '{1}' (and {2} section(s) in its subtree) {3} wiki generation.").format(
		verb, _title(name), res["count"], "in" if include else "from"
	)


# --- 0.3 Slice 20: structure surgery ----------------------------------------------------


def _create_section(ctx: Ctx, args: dict) -> str:
	source_document = ctx.default_document(args.get("source_document"))
	if not source_document:
		return _("No document specified. Open a document or pass `source_document`.")
	if not (args.get("title") or "").strip():
		return _("Provide a non-empty `title`.")
	try:
		res = sections.create_section(
			source_document,
			args["title"],
			parent=args.get("parent") or None,
			is_group=bool(args.get("is_group")),
			markdown=args.get("content") or "",
			section_type=args.get("section_type"),
			index=args.get("index"),
		)
	except frappe.ValidationError as e:
		return _("Couldn't create section: {0}").format(str(e))
	return _(
		"Created section '{0}' <{1}>. It appears in the wiki preview now; run regenerate_wiki "
		"to project it into a generated wiki (sync_wiki_page can't create pages)."
	).format(args["title"].strip(), res["name"])


def _delete_section(ctx: Ctx, args: dict) -> str:
	name = args.get("name")
	if not name:
		return _("Provide the section `name`.")
	title = _title(name)
	try:
		res = sections.delete_section(name)
	except frappe.ValidationError as e:
		return _("Couldn't delete section: {0}").format(str(e))
	return _(
		"Deleted '{0}' ({1} section(s) including its subtree). Any generated wiki pages for "
		"them are swept on the next regenerate_wiki."
	).format(title, res["deleted"])


def _split_section(ctx: Ctx, args: dict) -> str:
	name, at_heading = args.get("name"), args.get("at_heading")
	if not name or not (at_heading or "").strip():
		return _("Provide the section `name` and the `at_heading` to split at.")
	try:
		res = sections.split_section(name, at_heading, new_title=args.get("new_title"))
	except frappe.ValidationError as e:
		return _("Couldn't split: {0}").format(str(e))
	return _(
		"Split '{0}' — new sibling '{1}' <{2}> holds the content from '{3}' down. Both keep "
		"the original page range. Preview shows both now; regenerate_wiki is needed to give "
		"the new page a generated wiki page."
	).format(_title(name), res["new_title"], res["new_name"], at_heading)


def _merge_sections(ctx: Ctx, args: dict) -> str:
	names = args.get("names") or []
	if len(names) < 2:
		return _("Pass `names` — two or more sibling section ids; the FIRST is kept.")
	survivor_title = _title(names[0])
	try:
		res = sections.merge_sections(names)
	except frappe.ValidationError as e:
		return _("Couldn't merge: {0}").format(str(e))
	return _(
		"Merged {0} section(s) into '{1}' (content concatenated in tree order, children "
		"reparented). Deleted pages' generated wiki pages are swept on the next "
		"regenerate_wiki; run sync_wiki_page on <{2}> to update its own wiki page."
	).format(res["merged"], survivor_title, names[0])


TOOLS = [
	Tool(
		name="move_section",
		side="server",
		# adjacent literals are one wrapped sentence, not a missing comma
		# nosemgrep
		description=(
			"Reparent and/or reorder a section in the tree. Pass the section id and the new "
			"parent id (omit new_parent to move it to the top level); new_index is the 0-based "
			"position among the destination's children (omit to append)."
		),
		parameters={
			"type": "object",
			"properties": {
				"name": {"type": "string", "description": "Source Section id to move."},
				"new_parent": {
					"type": "string",
					"description": "Destination parent section id. Omit for top level.",
				},
				"new_index": {"type": "integer", "description": "0-based position among siblings."},
			},
			"required": ["name"],
		},
		handler=_move_section,
		mutates=True,
	),
	Tool(
		name="rename_section",
		side="server",
		description="Rename a section (updates its hierarchy path and its descendants').",
		parameters={
			"type": "object",
			"properties": {
				"name": {"type": "string", "description": "Source Section id."},
				"title": {"type": "string", "description": "New title."},
			},
			"required": ["name", "title"],
		},
		handler=_rename_section,
		mutates=True,
	),
	Tool(
		name="set_section_type",
		side="server",
		# adjacent literals are one wrapped sentence, not a missing comma
		# nosemgrep
		description=(
			"Retag a section with a Section Type (the machine key, e.g. surgical_procedures). "
			"Use list_section_types to see the taxonomy; create_section_type to add a new tag. "
			"Pass an empty section_type to clear the tag."
		),
		parameters={
			"type": "object",
			"properties": {
				"name": {"type": "string", "description": "Source Section id."},
				"section_type": {
					"type": "string",
					"description": "Section Type machine key, or empty to clear.",
				},
			},
			"required": ["name"],
		},
		handler=_set_section_type,
		mutates=True,
	),
	Tool(
		name="toggle_include_in_wiki",
		side="server",
		description="Include or exclude a section (and its whole subtree) from wiki generation.",
		parameters={
			"type": "object",
			"properties": {
				"name": {"type": "string", "description": "Source Section id."},
				"include": {"type": "boolean", "description": "true to include, false to exclude."},
			},
			"required": ["name", "include"],
		},
		handler=_toggle_include_in_wiki,
		mutates=True,
	),
	Tool(
		name="create_section",
		side="server",
		# adjacent literals are one wrapped sentence, not a missing comma
		# nosemgrep
		description=(
			"Create a new section (a future wiki page) under a parent — e.g. 'add a glossary "
			"page'. No page range is set. Defaults to the attached document; omit parent for "
			"top level."
		),
		parameters={
			"type": "object",
			"properties": {
				"source_document": {"type": "string", "description": "Omit to use the attached document."},
				"title": {"type": "string", "description": "New section title."},
				"parent": {"type": "string", "description": "Parent section id (omit for top level)."},
				"is_group": {"type": "boolean", "description": "true for a folder/group node."},
				"content": {"type": "string", "description": "Initial markdown body."},
				"section_type": {"type": "string", "description": "Optional Section Type key."},
				"index": {"type": "integer", "description": "0-based position among siblings."},
			},
			"required": ["title"],
		},
		handler=_create_section,
		mutates=True,
	),
	Tool(
		name="delete_section",
		side="server",
		# adjacent literals are one wrapped sentence, not a missing comma
		# nosemgrep
		description=(
			"Delete a section AND its whole subtree. Destructive — the user must confirm "
			"before it runs. Prefer toggle_include_in_wiki when the user only wants it out of "
			"the wiki."
		),
		parameters={
			"type": "object",
			"properties": {
				"name": {"type": "string", "description": "Source Section id to delete."},
			},
			"required": ["name"],
		},
		handler=_delete_section,
		mutates=True,
		confirm=True,
	),
	Tool(
		name="split_section",
		side="server",
		# adjacent literals are one wrapped sentence, not a missing comma
		# nosemgrep
		description=(
			"Split one section into two sibling pages at a markdown heading inside its body. "
			"The original keeps everything above the heading; a new sibling right after it "
			"gets the heading and everything below. at_heading matches the heading text (with "
			"or without #s); fails loudly if not found."
		),
		parameters={
			"type": "object",
			"properties": {
				"name": {"type": "string", "description": "Source Section id to split."},
				"at_heading": {"type": "string", "description": "Heading text to split at."},
				"new_title": {
					"type": "string",
					"description": "Title for the new sibling (defaults to the heading text).",
				},
			},
			"required": ["name", "at_heading"],
		},
		handler=_split_section,
		mutates=True,
	),
	Tool(
		name="merge_sections",
		side="server",
		# adjacent literals are one wrapped sentence, not a missing comma
		# nosemgrep
		description=(
			"Merge two or more SIBLING sections into the first listed: markdown concatenated "
			"in tree order, children reparented to the survivor, the others deleted."
		),
		parameters={
			"type": "object",
			"properties": {
				"names": {
					"type": "array",
					"items": {"type": "string"},
					"description": "Sibling section ids; the FIRST is kept.",
				},
			},
			"required": ["names"],
		},
		handler=_merge_sections,
		mutates=True,
	),
]
