import re
import frappe
from collections import Counter
from wikify.exam import map as exam_map


def run():
	md = exam_map.markdown_index("PRJ-2026-00002")
	blob = "\n".join(text for _s, text in md)
	# How does the corpus actually write a provision?
	patterns = {
		"section N": r"[Ss]ection\s+\d+",
		"u/s N": r"u/s\s*\d+",
		"table cell | N |": r"\|\s*\d+[A-Z]{0,4}(?:\([0-9A-Za-z]+\))?\s*\|",
		"bare [Section N]": r"\[\s*[Ss]ection\s+\d+",
		"Sec. N": r"Sec\.\s*\d+",
	}
	for label, pat in patterns.items():
		print(f"  {label:22} {len(re.findall(pat, blob)):6}")

	print("\n-- how many cited refs are DEAD against the corpus? --")
	rows = frappe.get_all("Wikify Exam Question", filters={"project": "PRJ-2026-00002"},
		fields=["name", "statutory_refs"])
	total = dead = 0
	dead_qs = 0
	for row in rows:
		refs = exam_map.statutory_refs(row)
		if not refs:
			continue
		alive = 0
		for ref in refs:
			total += 1
			if exam_map.statutory_hits([ref], md):
				alive += 1
			else:
				dead += 1
		if refs and not alive:
			dead_qs += 1
	print(f"  ref-instances: {total}  dead: {dead}  ({round(100*dead/max(1,total))}%)")
	print(f"  questions with EVERY ref dead: {dead_qs}")
