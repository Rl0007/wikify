import frappe
from wikify.exam import map as exam_map, score


def run():
	report = exam_map.map_project("PRJ-2026-00002")
	frappe.db.commit()
	print("MAP", report["verdicts"], "evidence_coverage:", report["evidence_coverage"])
	grid = score.matrix("PRJ-2026-00002")
	print("TOTALS", grid["totals"])
	print("top topics:")
	for t in grid["topics"][:8]:
		print(f"   {t['score']:7.1f}  {t['title'][:56]}")
