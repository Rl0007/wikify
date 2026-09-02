# RAG POC — demo corpus inventory and golden questions

The corpus the RAG POC is demonstrated and evaluated against. Seeded by
`wikify/tests/fixtures/demo_corpus.py`; a later agent builds the eval harness from the
**Golden questions** table at the bottom of this file.

## How to (re)create it

```bash
# from the bench root
bench --site wikify.localhost execute wikify.tests.fixtures.demo_corpus.seed_demo_corpus
bench --site wikify.localhost execute wikify.tests.fixtures.demo_corpus.inventory
bench --site wikify.localhost execute wikify.tests.fixtures.demo_corpus.section_routes
```

`seed_demo_corpus()` is idempotent: documents are matched by title inside the demo
project, the section tree is rebuilt only when it drifts from the spec, and wiki
generation upserts pages against each section's `wiki_document`. Running it twice
produces identical counts.

The corpus was **authored directly as Source Document + Source Section rows**, not run
through the parse pipeline. `wikify.engine.settings.openrouter_key()` does resolve on this
site, but there are no source PDFs for this domain in the repo (only
`docs/what-is-wikify.pdf`), and a real parse costs an LLM call per page. Section bodies are
hand-written prose standing in for parser output; every leaf body is 218–273 words.

## What exists

| | |
|---|---|
| Wikify Project | **Demo Corpus** (`PRJ-2026-00003`) |
| Source Documents | **5** |
| Source Sections | **50** (15 groups + 35 leaves), all `include_in_wiki=1` |
| Wiki Documents | **50** section pages + 5 per-document root groups |
| Wiki Space route | **`/demo-corpus`** |

### Documents

| Document | Pages | Sections (groups + leaves) |
|---|---|---|
| Northfield Hospital Nursing Manual | 48 | 3 + 7 |
| Riverside Clinic Staff Handbook | 40 | 3 + 7 |
| St Aubyn Maternity Policy Manual | 44 | 3 + 7 |
| Lakeside Surgical Theatre Manual | 52 | 3 + 7 |
| Meridian Home Care Employee Manual | 38 | 3 + 7 |

### Sections by type

| `section_type` | Count | Spread |
|---|---|---|
| `staff_roles_and_responsibilities` | **15** | 3 in every document |
| `administrative_policies` (pay / benefits) | 5 | 1 in every document |
| `training_and_audits` (requirements) | 5 | 1 in every document |
| `other` (organisation overview) | 5 | 1 in every document |
| `clinical_protocols` | 1 | Northfield |
| `medication_management` | 1 | Riverside |
| `emergency_procedures` | 1 | St Aubyn |
| `surgical_procedures` | 1 | Lakeside |
| `patient_management` | 1 | Meridian |
| _(group nodes, untyped)_ | 15 | 3 in every document |

**Why this shape.** Fifteen job-description sections spread across five documents is the
completeness thesis in one number: a naive top-k vector search at `limit=8` physically
cannot return them all, and even at `limit=15` it will mix in the benefits and training
sections that talk about the same roles. A `section_type="staff_roles_and_responsibilities"`
filter (`mode="filter"`) returns exactly 15, every one of them a job description.

## Section → wiki route map

All routes are under `/demo-corpus/<document-slug>/<group-slug>/<section-slug>` and are
live (verified `200`). `JD` = `staff_roles_and_responsibilities`.

### Northfield Hospital Nursing Manual — `/demo-corpus/northfield-hospital-nursing-manual`

| Section | Type | Pages | Route suffix |
|---|---|---|---|
| Hospital Overview and Mission | other | 2–5 | `/governance-and-pay/hospital-overview-and-mission` |
| Pay Bands, Benefits and Leave Entitlements | administrative_policies | 6–12 | `/governance-and-pay/pay-bands-benefits-and-leave-entitlements` |
| Job Description — Ward Sister / Charge Nurse | JD | 13–18 | `/roles-and-responsibilities/job-description-ward-sister-charge-nurse` |
| Job Description — Staff Nurse (Band 5) | JD | 19–24 | `/roles-and-responsibilities/job-description-staff-nurse-band-5` |
| Job Description — Healthcare Assistant | JD | 25–30 | `/roles-and-responsibilities/job-description-healthcare-assistant` |
| Recruitment, Qualifications and Mandatory Training | training_and_audits | 31–37 | `/practice-standards/recruitment-qualifications-and-mandatory-training` |
| Pressure Ulcer Prevention Protocol | clinical_protocols | 38–48 | `/practice-standards/pressure-ulcer-prevention-protocol` |

### Riverside Clinic Staff Handbook — `/demo-corpus/riverside-clinic-staff-handbook`

| Section | Type | Pages | Route suffix |
|---|---|---|---|
| Who We Are: Riverside Community Clinic | other | 2–5 | `/governance-and-pay/who-we-are-riverside-community-clinic` |
| Salary Scales, Pension and Employee Benefits | administrative_policies | 6–10 | `/governance-and-pay/salary-scales-pension-and-employee-benefits` |
| Job Description — Practice Manager | JD | 11–16 | `/roles-and-responsibilities/job-description-practice-manager` |
| Job Description — General Practitioner (Salaried) | JD | 17–22 | `/roles-and-responsibilities/job-description-general-practitioner-salaried` |
| Job Description — Medical Receptionist | JD | 23–28 | `/roles-and-responsibilities/job-description-medical-receptionist` |
| Induction, Competency Framework and Annual Appraisal | training_and_audits | 29–34 | `/practice-standards/induction-competency-framework-and-annual-appraisal` |
| Repeat Prescribing and Medicines Reconciliation | medication_management | 35–40 | `/practice-standards/repeat-prescribing-and-medicines-reconciliation` |

### St Aubyn Maternity Policy Manual — `/demo-corpus/st-aubyn-maternity-policy-manual`

| Section | Type | Pages | Route suffix |
|---|---|---|---|
| St Aubyn Maternity Unit at a Glance | other | 2–6 | `/unit-administration/st-aubyn-maternity-unit-at-a-glance` |
| Rostering, On-Call Payments and Maternity Benefits | administrative_policies | 7–11 | `/unit-administration/rostering-on-call-payments-and-maternity-benefits` |
| Job Description — Consultant Obstetrician | JD | 12–18 | `/roles-and-responsibilities/job-description-consultant-obstetrician` |
| Job Description — Community Midwife | JD | 19–24 | `/roles-and-responsibilities/job-description-community-midwife` |
| Job Description — Maternity Support Worker | JD | 25–30 | `/roles-and-responsibilities/job-description-maternity-support-worker` |
| Midwifery Preceptorship and Skills Drills Programme | training_and_audits | 31–36 | `/clinical-standards/midwifery-preceptorship-and-skills-drills-programme` |
| Obstetric Haemorrhage: Emergency Response | emergency_procedures | 37–44 | `/clinical-standards/obstetric-haemorrhage-emergency-response` |

### Lakeside Surgical Theatre Manual — `/demo-corpus/lakeside-surgical-theatre-manual`

| Section | Type | Pages | Route suffix |
|---|---|---|---|
| Lakeside Surgical Centre: Purpose and Services | other | 2–6 | `/centre-administration/lakeside-surgical-centre-purpose-and-services` |
| Theatre Staff Pay, Overtime and Benefits Policy | administrative_policies | 7–12 | `/centre-administration/theatre-staff-pay-overtime-and-benefits-policy` |
| Job Description — Theatre Scrub Practitioner | JD | 13–19 | `/roles-and-responsibilities/job-description-theatre-scrub-practitioner` |
| Job Description — Operating Department Practitioner (Anaesthetics) | JD | 20–27 | `/roles-and-responsibilities/job-description-operating-department-practitioner-anaestheti` |
| Job Description — Theatre Support Worker | JD | 28–34 | `/roles-and-responsibilities/job-description-theatre-support-worker` |
| Surgical Competency Sign-Off and Audit Cycle | training_and_audits | 35–42 | `/operational-standards/surgical-competency-sign-off-and-audit-cycle` |
| WHO Surgical Safety Checklist and Count Procedure | surgical_procedures | 43–52 | `/operational-standards/who-surgical-safety-checklist-and-count-procedure` |

### Meridian Home Care Employee Manual — `/demo-corpus/meridian-home-care-employee-manual`

| Section | Type | Pages | Route suffix |
|---|---|---|---|
| Meridian Home Care: Company Overview | other | 2–5 | `/company-administration/meridian-home-care-company-overview` |
| Reward, Mileage and Employee Benefits | administrative_policies | 6–10 | `/company-administration/reward-mileage-and-employee-benefits` |
| Job Description — Registered Care Manager | JD | 11–17 | `/roles-and-responsibilities/job-description-registered-care-manager` |
| Job Description — Community Care Worker | JD | 18–23 | `/roles-and-responsibilities/job-description-community-care-worker` |
| Job Description — Care Coordinator (Scheduling) | JD | 24–28 | `/roles-and-responsibilities/job-description-care-coordinator-scheduling` |
| Care Certificate, Shadowing and Refresher Training Requirements | training_and_audits | 29–33 | `/service-standards/care-certificate-shadowing-and-refresher-training-requiremen` |
| Person-Centred Care Planning and Review | patient_management | 34–38 | `/service-standards/person-centred-care-planning-and-review` |

## Golden questions

Twelve questions over the corpus above: **4 exhaustive-intent**, **5 fuzzy semantic**,
**2 cross-document hybrid**, **1 refusal**. Column meanings, so the eval harness can be
built mechanically from this table:

- **Intent** — the `Route.intent` the router (`rag/router.py`) is expected to pick.
- **Filter** — the `section_type` the router should extract, or `—`.
- **Expected sources** — the sections that must appear in `citations`. For exhaustive
  questions this is the *complete* set and recall is scored as
  `|retrieved ∩ expected| / |expected|`; a passing run needs **recall = 1.0**. For
  semantic questions score `recall@8` against the listed sections, with the **bolded**
  one required.

---

### G1 — exhaustive · `staff_roles_and_responsibilities`

**Q.** "Give me all the job descriptions across all the PDFs."

**Expected answer.** Fifteen job descriptions across five documents, grouped by
organisation: Northfield (Ward Sister / Charge Nurse, Staff Nurse Band 5, Healthcare
Assistant), Riverside (Practice Manager, Salaried GP, Medical Receptionist), St Aubyn
(Consultant Obstetrician, Community Midwife, Maternity Support Worker), Lakeside (Theatre
Scrub Practitioner, ODP Anaesthetics, Theatre Support Worker), Meridian (Registered Care
Manager, Community Care Worker, Care Coordinator).

**Expected sources.** All **15** `staff_roles_and_responsibilities` sections. This is the
headline query — the naive-vs-routed comparison in `/rag-lab` should show naive top-k
returning at most 8 and missing whole documents, routed filter returning 15/15.

---

### G2 — exhaustive · `administrative_policies`

**Q.** "List every pay, benefits and leave policy in the corpus."

**Expected answer.** Five, one per document: Northfield "Pay Bands, Benefits and Leave
Entitlements"; Riverside "Salary Scales, Pension and Employee Benefits"; St Aubyn
"Rostering, On-Call Payments and Maternity Benefits"; Lakeside "Theatre Staff Pay, Overtime
and Benefits Policy"; Meridian "Reward, Mileage and Employee Benefits".

**Expected sources.** All **5** `administrative_policies` sections.

---

### G3 — exhaustive · `training_and_audits`

**Q.** "What training and competency requirements does every organisation set for new
staff?"

**Expected answer.** Five sections, one per document: Northfield's mandatory-training
schedule and pre-employment checks; Riverside's four-week induction, competency framework
and annual appraisal; St Aubyn's twelve-month preceptorship and weekly skills drills;
Lakeside's competency portfolios, specialty sign-off and monthly audit cycle; Meridian's
five-day induction, four shadowing shifts and Care Certificate within twelve weeks.

**Expected sources.** All **5** `training_and_audits` sections.

---

### G4 — exhaustive · `other`

**Q.** "Which organisations does this corpus cover, and what does each one do?"

**Expected answer.** Five providers: Northfield General Hospital (480-bed district general
hospital); Riverside Community Clinic (primary care practice, ~14,200 patients); St Aubyn
Maternity Unit (consultant-led maternity, ~3,100 births a year); Lakeside Surgical Centre
(elective treatment centre, six theatres, ~14,000 procedures a year); Meridian Home Care
Services (domiciliary care provider supporting ~620 people).

**Expected sources.** All **5** `other` (organisation overview) sections.

---

### G5 — semantic

**Q.** "Who is responsible for making sure the roster is published in advance, and how far
ahead?"

**Expected answer.** The ward sister / charge nurse at Northfield publishes the ward roster
**at least six weeks in advance** with the agreed nurse-to-patient ratio and skill mix. St
Aubyn publishes a rolling twelve-week midwifery roster **eight weeks** ahead. At Meridian
the care coordinator produces the area rota **at least one week** in advance.

**Expected sources.** **Job Description — Ward Sister / Charge Nurse** (Northfield);
Rostering, On-Call Payments and Maternity Benefits (St Aubyn); Job Description — Care
Coordinator (Scheduling) (Meridian).

---

### G6 — semantic

**Q.** "What happens if the swab count doesn't match at the end of an operation?"

**Expected answer.** A discrepancy **stops the closure**: the surgical field is searched,
then the bins and drapes; if the item is still unaccounted for an **intra-operative
radiograph** is taken before the patient leaves theatre. Counts are performed by the scrub
practitioner and a second registered practitioner before the procedure, at cavity closure
and at the end, and are recorded on the theatre whiteboard.

**Expected sources.** **WHO Surgical Safety Checklist and Count Procedure** (Lakeside);
Job Description — Theatre Scrub Practitioner (Lakeside).

---

### G7 — semantic

**Q.** "A woman is bleeding heavily after giving birth — what should the team do?"

**Expected answer.** Declare postpartum haemorrhage at 500 ml and continuing, or at any
volume with cardiovascular compromise; weigh all swabs, pads and drapes rather than
estimating. Call 2222 stating "obstetric haemorrhage" and the location, which summons the
obstetric registrar and consultant, anaesthetic registrar, second midwife, theatre team and
a porter. Manage in parallel: lie flat, high-flow oxygen, two large-bore cannulae with
bloods and cross-match for four units, warmed crystalloid, rub up the uterus and empty the
bladder, uterotonics per the proforma. Beyond 1,000 ml activate the major haemorrhage
protocol; debrief within 72 hours.

**Expected sources.** **Obstetric Haemorrhage: Emergency Response** (St Aubyn).

---

### G8 — semantic

**Q.** "How do staff who drive between visits get their travel costs back?"

**Expected answer.** Meridian pays mileage at **45p per mile for the first 10,000 business
miles** in a tax year and **25p thereafter**, claimed automatically from the checked-in
route and paid with the following month's salary. Public transport fares are reimbursed in
full on production of a receipt, and Meridian contributes a fixed annual sum towards
business insurance on the worker's own vehicle. Travel time between calls is also paid as
working time.

**Expected sources.** **Reward, Mileage and Employee Benefits** (Meridian); Job Description
— Community Care Worker (Meridian).

---

### G9 — semantic

**Q.** "What has to happen before someone is allowed to work unsupervised?"

**Expected answer.** Northfield: corporate induction, local induction signed off within
four weeks, and a supernumerary period of at least three shifts (ten for newly qualified
nurses in preceptorship). Riverside: no one works unsupervised on a task until the
corresponding line of the role-specific checklist is signed by the line manager. Meridian:
five-day classroom induction, at least four signed-off shadowing shifts, and an observed
practice check by a coordinator or registered manager. Lakeside: the relevant competency
portfolio must be signed by two assessors, one a team leader, plus specialty-specific
sign-off.

**Expected sources.** **Recruitment, Qualifications and Mandatory Training** (Northfield);
Induction, Competency Framework and Annual Appraisal (Riverside); Care Certificate,
Shadowing and Refresher Training Requirements (Meridian); Surgical Competency Sign-Off and
Audit Cycle (Lakeside).

---

### G10 — hybrid

**Q.** "Compare how the hospital, the maternity unit and the home care provider pay for
out-of-hours work."

**Expected answer.** Northfield pays a percentage enhancement on basic salary — 30% for
weekday nights and Saturdays, 60% for Sundays and public holidays — with Band 8+ taking
time off in lieu instead. St Aubyn pays community midwives an availability allowance plus
the hourly rate for time actually worked, a guaranteed two-hour minimum call-out, and no
clinical shift before 14:00 after a call-out past 02:00. Meridian uses banded hourly rates
(standard, evenings after 20:00 and weekends, public holidays) with sleeping nights paid as
a flat allowance plus hourly rate for time awake.

**Expected sources.** **Pay Bands, Benefits and Leave Entitlements** (Northfield);
**Rostering, On-Call Payments and Maternity Benefits** (St Aubyn); **Reward, Mileage and
Employee Benefits** (Meridian). Lakeside's overtime policy is an acceptable extra hit.

---

### G11 — hybrid

**Q.** "Which roles require a driving licence?"

**Expected answer.** Three: the Community Midwife at St Aubyn (full licence and access to a
vehicle, for home births and postnatal visits), the Registered Care Manager at Meridian
(full driving licence), and the Community Care Worker at Meridian (licence and business
insurance for rural rounds). The consultant obstetrician's on-call requirement is to be
within thirty minutes of the unit but does not itself state a licence.

**Expected sources.** **Job Description — Community Midwife** (St Aubyn); **Job Description
— Registered Care Manager** (Meridian); **Job Description — Community Care Worker**
(Meridian).

---

### G12 — refusal

**Q.** "What is the cyber security incident response plan for these organisations?"

**Expected answer.** `refused: true`. Nothing in the corpus covers cyber security incident
response; the closest sections are information-governance mentions inside the mandatory
training lists, which do not answer the question. The answer must say so rather than
synthesising a plan, and must not cite a section as if it contained one.

**Expected sources.** none.

## Notes for the eval harness

- Scope every query with `project="PRJ-2026-00003"` (or look the project up by
  `project_name="Demo Corpus"` — the autoname is not stable across sites).
- The two pre-existing Source Documents on `wikify.localhost` are **not** part of this
  corpus and sit outside the demo project; leaving `project=None` will pull them in.
- Recall for G1–G4 must be measured against the full expected set, not `recall@8` — that
  distinction is the whole point of the exhaustive lane.
- G12 scores on `refused == True`, not on citations.
