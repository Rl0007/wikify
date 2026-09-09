import frappe

from wikify.wikify.doctype.section_type.section_type import normalize_label


def execute():
	groups: dict[str, list] = {}
	for row in frappe.get_all("Section Type", fields=["name", "label", "creation"], order_by="creation asc"):
		key = normalize_label(row.label)
		if key:
			groups.setdefault(key, []).append(row)

	for dupes in groups.values():
		if len(dupes) < 2:
			continue
		canonical = next((r for r in dupes if not r.name.startswith("t_")), dupes[0])
		losers = [r.name for r in dupes if r.name != canonical.name]
		frappe.db.set_value(
			"Source Section",
			{"section_type": ("in", losers)},
			"section_type",
			canonical.name,
			update_modified=False,
		)
		frappe.db.delete("Section Type", {"name": ("in", losers)})
