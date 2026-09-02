# Wikify — agent guide

PDFs → reviewed, typed, navigable **Frappe Wiki** spaces. A Frappe app (`wikify/`) with
a Frappe UI v1 SPA, porting the validated POC pipeline (`scratch/pdf_lab/`) into
first-class DocTypes + a guided review UI.

## Read first

- `specs/product/README.md` — product spec index (the **what**).
- `specs/product/IMPLEMENTATION-PLAN.md` — tracer-bullet slices, delivery order, and
  the **Verification** protocol (the **how/when**). Build in slice order; verify each
  slice before moving on.
- `scratch/pdf_lab/` — the working POC the `engine/` package is ported from. Logic is
  unchanged on port; only the I/O boundaries change (no Flask, no SQLite → DocTypes).

## Dev environment

| | |
|---|---|
| Bench root | `/Users/deathstarconsole/company_projects/frappe/develop` (run all `bench` commands here) |
| Dev site | **`wikify.localhost`** |
| Login | **Administrator / admin** |
| Installed apps | `frappe`, `wiki`, `wikify` (`wiki` is the wiki-generation target — Slice 7) |
| SPA mount | `/wikify` (once Slice 1a lands) |
| LLM key | `OPENROUTER_KEY` in `apps/wikify/.env`; surfaced via `Wikify Settings` once it exists |

Frappe is installed on the bench; `bench` may not be on `PATH` in a bare shell — invoke
from the bench root.

## Common commands (from bench root)

```bash
bench start                                   # web + socketio + workers + redis (needed for jobs + realtime)
bench --site wikify.localhost migrate            # apply DocType / schema changes
bench --site wikify.localhost console            # interactive python REPL with frappe loaded
bench --site wikify.localhost execute wikify.engine.parse_pdf --kwargs "{'pdf_path': '...'}"   # headless pipeline run
bench --site wikify.localhost run-tests --app wikify
bench build --app wikify                       # build the SPA (or use vite dev via the frontend)
bench --site wikify.localhost set-config developer_mode 1   # export DocType json on change
bench --site wikify.localhost clear-cache
```

Background jobs (parse/remediate/classify/generate) run on the **long** queue and emit
progress over realtime — `bench start` (or a running worker + socketio) must be up to
exercise them end-to-end.

## Conventions

- **Backend:** follow `specs/product/` + the `frappe-app-dev` skill. DocTypes per
  `02-data-model.md`; pipeline lives in `wikify/engine/` with `engine/store.py` as the
  ORM seam; jobs in `wikify/jobs/`; thin whitelisted APIs in `wikify/api/`.
- **Frontend:** Frappe UI v1 + the `frappe-ui` skill. Data via `useCall`/`useList`/
  `useDoc` (v3 — **not** legacy `createResource`). Semantic tokens only (`bg-surface-*`,
  `text-ink-*`, `border-outline-*`); one solid primary action per page.
- **Verify each slice** against `wikify.localhost` per the plan's Verification section
  before starting the next.
- `pre-commit` (ruff/eslint/prettier/pyupgrade) runs on commit; keep it green.

## Git

Local **`main`** tracks `origin/main` (the canonical cloud branch). `develop` is a
preserved backup with an unrelated history — don't merge it. **Commit work directly on
`main`** (project preference — no feature branches); push only when asked.
