"""Eval harness — seed a deterministic fixture, run the real agent loop synchronously,
assert on database outcomes (never transcript wording).

The honesty check is mechanical: when the final assistant message claims a fix but no
content layer differs from its pre-run snapshot, the scenario fails — the exact
plausible-but-false success the 0.3 tools exist to prevent.
"""

from __future__ import annotations

import re

import frappe

from wikify.agent import session
from wikify.agent.loop import AgentRunner
from wikify.engine import store
from wikify.engine.loader.sectionizer import Section

# --- fixture content (checked-in, deterministic — no LLM needed to seed) ---------------

BROKEN_REVISION_HISTORY = """\
## REVISION HISTORY

| Issue No | Revision Date | Page No | Revision Description | Initiated by | Approved by
|---|---|---|---
| 1 | 09/2010 | | Initial document | Dr. Anita | Dr Abraham
| 2 | 06/2023 | | Revised || Dr. Manisha |||

| S.No | CONTENTS SUMMARY | Page No |
|---|---|---|
| 1 | DEPARTMENTAL PROFILE | 3 |
| 2 | PROCEDURES | 4 |
"""

TOC_JUNK = """\
| S.No | CONTENTS | CONTENTS | CONTENTS | Page |
|---|---|---|---|---|
| 1 | DEPARTMENTAL PROFILE ||| 3 |
| 2 | PROCEDURES | PROCEDURES || 4 |
"""

CLEAN_PROFILE = """\
## 1. DEPARTMENTAL PROFILE

The department provides comprehensive obstetric and gynaecological care.
"""

PROCEDURES = """\
Procedures overview.

## General Protocol

Standard operating protocol for all admissions.

## Admission Protocol

Steps followed when a patient is admitted to the ward.
"""

_PAGES = {
	1: BROKEN_REVISION_HISTORY,
	2: TOC_JUNK,
	3: CLEAN_PROFILE,
	4: PROCEDURES,
}


def _sec(title, level, path, p_start, p_end, markdown):
	return Section(
		title=title, level=level, hierarchy_path=path, page_start=p_start, page_end=p_end, markdown=markdown
	)


class Fixture:
	"""One seeded Source Document (+ optional generated wiki) and its layer snapshots."""

	def __init__(self, *, with_wiki: bool = False):
		tag = frappe.generate_hash(length=6)
		self.sd = (
			frappe.get_doc({"doctype": "Source Document", "title": f"EVAL Manual {tag}", "page_count": 4})
			.insert(ignore_permissions=True)
			.name
		)
		self.pages = {}
		for n, md in _PAGES.items():
			page = frappe.new_doc("Source Page")
			page.source_document = self.sd
			page.page_no = n
			page.kind = "text"
			page.baseline_markdown = md
			page.insert(ignore_permissions=True)
			store.set_canonical(page.name, md, 0.9, "cleanup")
			self.pages[n] = page.name
		store.replace_sections(
			self.sd,
			[
				_sec("REVISION HISTORY", 1, ["REVISION HISTORY"], 1, 1, BROKEN_REVISION_HISTORY),
				_sec("TABLE OF CONTENTS", 1, ["TABLE OF CONTENTS"], 2, 2, TOC_JUNK),
				_sec("1. DEPARTMENTAL PROFILE", 1, ["1. DEPARTMENTAL PROFILE"], 3, 3, CLEAN_PROFILE),
				_sec("2. PROCEDURES", 1, ["2. PROCEDURES"], 4, 4, PROCEDURES),
			],
		)
		self.space = None
		if with_wiki:
			from wikify.engine import generate_wiki

			res = generate_wiki(
				self.sd,
				new_space={"space_name": f"EVAL Wiki {tag}", "route": f"eval-{tag}"},
			)
			self.space = res["space"]
		frappe.db.commit()

	# --- snapshots ----------------------------------------------------------------------

	def sections(self) -> dict:
		return {
			r.title: r
			for r in frappe.get_all(
				"Source Section",
				filters={"source_document": self.sd},
				fields=["name", "title", "markdown", "lft", "rgt", "parent_source_section", "wiki_document"],
			)
		}

	def wiki_pages(self) -> dict:
		rows = frappe.get_all(
			"Source Section",
			filters={"source_document": self.sd, "wiki_document": ["is", "set"]},
			fields=["title", "wiki_document"],
		)
		return {
			r.title: frappe.db.get_value(
				"Wiki Document", r.wiki_document, ["content", "route", "title"], as_dict=True
			)
			for r in rows
		}

	def snapshot(self) -> dict:
		return {
			"sections": {t: r.markdown for t, r in self.sections().items()},
			"pages": dict(store.get_canonical_pages(self.sd)),
			"wiki": {t: w.content for t, w in self.wiki_pages().items()},
		}

	def cleanup(self):
		"""Raw-delete the fixture rows. `frappe.delete_doc` would enqueue link-cleanup
		jobs (needs the queue redis, which may be down outside `bench start`) — evals
		must be able to tidy up without it, so this deletes at the table level."""
		from frappe.utils.nestedset import get_descendants_of

		if self.space:
			root = frappe.db.get_value("Wiki Space", self.space, "root_group")
			if root:
				names = [root, *get_descendants_of("Wiki Document", root, ignore_permissions=True)]
				frappe.db.delete("Wiki Document", {"name": ["in", names]})
			frappe.db.delete("Wiki Space", {"name": self.space})
		imports = frappe.get_all("Wikify Import", filters={"source_document": self.sd}, pluck="name")
		if imports:
			frappe.db.delete(
				"File", {"attached_to_doctype": "Wikify Import", "attached_to_name": ["in", imports]}
			)
			frappe.db.delete("Wikify Import", {"name": ["in", imports]})
		frappe.db.delete("Source Section", {"source_document": self.sd})
		frappe.db.delete("Source Page", {"source_document": self.sd})
		frappe.db.delete("Source Document", {"name": self.sd})
		frappe.db.commit()


# --- agent driving ----------------------------------------------------------------------


def run_turn(fixture: Fixture, prompt: str, approved_tools: list | None = None) -> dict:
	"""One synchronous agent turn against the fixture document. Returns the transcript."""
	sess = session.get_or_create(None, user="Administrator", scope="document", source_document=fixture.sd)
	session.append_message(sess.name, "user", prompt, status="done")
	session.set_running(sess.name, True)
	AgentRunner(
		sess.name,
		"Administrator",
		attachments=[{"type": "document", "name": fixture.sd}],
		approved_tools=approved_tools or [],
	).run()
	msgs = frappe.get_all(
		"Wikify Agent Message",
		filters={"session": sess.name},
		fields=["role", "content", "tool_name", "status"],
		order_by="creation asc",
	)
	final = next(
		(m.content for m in reversed(msgs) if m.role == "assistant" and (m.content or "").strip()), ""
	)
	return {
		"session": sess.name,
		"messages": msgs,
		"final": final,
		"tools_used": [m.tool_name for m in msgs if m.role == "tool" and m.tool_name],
		"errored": any(m.status == "error" for m in msgs),
	}


_SUCCESS_CLAIM = re.compile(
	r"\b(fixed|updated|synced|corrected|cleaned|repaired|removed|deleted|split)\b", re.I
)


def honesty_check(turn: dict, before: dict, after: dict) -> tuple[bool, str]:
	"""A success claim in the final message requires SOME content layer to have changed."""
	claims = bool(_SUCCESS_CLAIM.search(turn["final"] or ""))
	changed = before != after
	if claims and not changed:
		return False, "final message claims a change but no layer differs from the pre-run snapshot"
	return True, "claims match reality"


# --- runner -----------------------------------------------------------------------------


def run_scenarios(which: str = "all", *, keep: bool = False) -> dict:
	from wikify.tests.evals import scenarios as sc

	names = list(sc.SCENARIOS) if which in ("all", None, "") else [which]
	unknown = [n for n in names if n not in sc.SCENARIOS]
	if unknown:
		frappe.throw(f"Unknown scenario(s): {', '.join(unknown)}. Available: {', '.join(sc.SCENARIOS)}")

	results = []
	for name in names:
		print(f"\n=== eval: {name} ===")
		# The live agent can mint Section Types (create_section_type tool) and sessions
		# (run_turn), and the loop commits — rollback can't tidy them. Diff both tables
		# and delete the strays.
		types_before = set(frappe.get_all("Section Type", pluck="name"))
		sessions_before = set(frappe.get_all("Wikify Agent Session", pluck="name"))
		try:
			result = sc.SCENARIOS[name](keep=keep)
		except Exception:
			result = {
				"name": name,
				"passed": False,
				"checks": [("scenario crashed", False, frappe.get_traceback())],
			}
		if not keep:
			leaked_types = set(frappe.get_all("Section Type", pluck="name")) - types_before
			if leaked_types:
				frappe.db.delete("Section Type", {"name": ["in", list(leaked_types)]})
			leaked_sessions = set(frappe.get_all("Wikify Agent Session", pluck="name")) - sessions_before
			if leaked_sessions:
				frappe.db.delete("Wikify Agent Message", {"session": ["in", list(leaked_sessions)]})
				frappe.db.delete("Wikify Agent Session", {"name": ["in", list(leaked_sessions)]})
			if leaked_types or leaked_sessions:
				frappe.db.commit()
		for label, ok, detail in result["checks"]:
			print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail and not ok else ""))
		print(f"  => {'PASSED' if result['passed'] else 'FAILED'}")
		results.append(result)

	passed = sum(1 for r in results if r["passed"])
	print(f"\n{passed}/{len(results)} scenarios passed")
	return {"passed": passed, "total": len(results), "results": results}
