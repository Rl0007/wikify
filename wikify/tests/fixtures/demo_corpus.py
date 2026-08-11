"""Repeatable demo corpus for the RAG POC (`specs/poc-rag-CONTRACT.md`).

Five healthcare-provider manuals whose sections deliberately **overlap in type**: each
document carries three job descriptions, a compensation/benefits section, a
qualifications-and-training section, an organisation overview, and one domain section.
Fifteen `staff_roles_and_responsibilities` sections spread over five documents is the
whole point — a naive top-k (k=8) vector search physically cannot return them all, while
a `section_type` metadata filter returns exactly fifteen. That is the completeness thesis
the POC demonstrates.

Built as Source Document + Source Section rows directly rather than through the parse
pipeline: no source PDFs for this domain exist in the repo, and a real parse is an
LLM call per page. The bodies below are hand-written prose standing in for parser output.

# ponytail: hand-authored corpus, replace with a real parsed PDF set once demo PDFs exist

Usage (from the bench root):

    bench --site wikify.localhost execute wikify.tests.fixtures.demo_corpus.seed_demo_corpus
    bench --site wikify.localhost execute wikify.tests.fixtures.demo_corpus.inventory
"""

from __future__ import annotations

import frappe
from frappe.utils.nestedset import get_descendants_of

from wikify.engine import store
from wikify.engine.generate import generate_wiki
from wikify.engine.loader.sectionizer import Section
from wikify.seed import seed_section_types

PROJECT_NAME = "Demo Corpus"
SPACE_NAME = "Demo Corpus"
SPACE_ROUTE = "demo-corpus"

# --- Northfield General Hospital — Nursing Services Manual ---

NORTHFIELD_OVERVIEW = """# Hospital Overview and Mission

Northfield General Hospital is a 480-bed district general hospital serving a population of
just under 400,000 people across the Northfield, Elmsworth and Carrow Vale districts. The
hospital opened its current site in 1978 and has since grown to eleven inpatient wards, a
twenty-four hour emergency department, a six-theatre elective surgical suite, and an
outpatient centre handling approximately 210,000 appointments each year.

Our stated mission is to deliver safe, timely and compassionate care as close to a
patient's home as is clinically appropriate. Three commitments follow from that mission
and govern every policy in this manual. First, no patient should wait for care because of
an avoidable process delay. Second, every member of staff should be able to describe their
own accountability without consulting a manager. Third, every clinical decision should be
recorded well enough that the next clinician can safely continue the episode of care.

The hospital is organised into four clinical divisions — Medicine, Surgery, Women's and
Children's Health, and Diagnostics and Therapies — each led by a divisional director
supported by a head of nursing and a divisional manager. Nursing services report
professionally to the Chief Nurse and operationally to the divisional structure. This dual
line is deliberate: professional standards, revalidation and clinical supervision stay
with the nursing hierarchy even when day-to-day task allocation sits with the division.

The Nursing Services Manual applies to every registered nurse, nursing associate and
healthcare assistant employed by or contracted to the hospital, including agency and bank
staff working a single shift.
"""

NORTHFIELD_BENEFITS = """# Pay Bands, Benefits and Leave Entitlements

Nursing and healthcare support roles at Northfield General are paid on the nine-band
national pay structure. Healthcare assistants enter at Band 2 and progress to Band 3 on
completion of the Care Certificate and a supervised competency portfolio. Newly registered
nurses are appointed at Band 5, senior and specialist nurses at Band 6, and ward sisters
and charge nurses at Band 7. Progression within a band is by annual increment, subject to
a satisfactory appraisal and completion of mandatory training.

Unsocial hours are paid as a percentage enhancement on basic salary: 30 per cent for
weekday nights and Saturdays, and 60 per cent for Sundays and public holidays. Staff at
Band 8 and above are not eligible for enhancements and instead receive time off in lieu at
the discretion of their divisional director.

Annual leave begins at 27 days plus eight public holidays, rising to 29 days after five
years of continuous service and 33 days after ten years. Leave is pro-rated for part-time
contracts and must be requested at least six weeks in advance for periods longer than five
consecutive working days.

All staff are automatically enrolled in the hospital pension scheme at a contribution rate
banded by salary, with the employer contributing 20.6 per cent. Additional benefits
include a subsidised staff restaurant, an on-site nursery with 45 places allocated by
ballot each September, an occupational health service offering same-week appointments, a
cycle-to-work salary sacrifice scheme, and access to a confidential employee assistance
helpline operating twenty-four hours a day.
"""

NORTHFIELD_WARD_SISTER = """# Job Description — Ward Sister / Charge Nurse

**Band:** 7  **Reports to:** Matron, Divisional Head of Nursing  **Hours:** 37.5 per week

**Purpose of the role.** The ward sister or charge nurse holds continuing 24-hour
responsibility for the standard of nursing care delivered on a designated ward. The
postholder is the visible clinical leader of the ward team, accountable for patient
safety, staff performance, and the effective use of the ward's staffing and non-pay
budget.

**Principal duties.** Lead the nursing team on the ward, setting and monitoring standards
of clinical practice against hospital policy and Nursing and Midwifery Council
requirements. Produce and publish the ward roster at least six weeks in advance, ensuring
every shift meets the agreed registered-nurse-to-patient ratio and skill mix. Conduct daily
safety huddles and a structured ward round with the medical team, escalating deteriorating
patients through the recognised pathway. Investigate incidents and complaints relating to
the ward, complete the associated reports within ten working days, and share the learning
with the team.

Manage the ward's non-pay budget, authorise expenditure within delegated limits, and
account for variance at the monthly divisional review. Undertake appraisals for all
directly reporting staff, agree personal development plans, and manage sickness absence,
capability and conduct issues in line with hospital procedure. Act as practice supervisor
and assessor for student nurses on placement.

**Person specification.** Registered Nurse with current NMC registration; minimum five
years post-registration experience of which two are at Band 6; degree-level study or
equivalent; evidence of leadership development; demonstrable experience of managing a
roster and a devolved budget.
"""

NORTHFIELD_STAFF_NURSE = """# Job Description — Staff Nurse (Band 5)

**Band:** 5  **Reports to:** Ward Sister / Charge Nurse  **Hours:** 37.5 per week, rotating

**Purpose of the role.** The staff nurse delivers and coordinates direct nursing care for
a defined group of patients throughout a shift, working within the NMC Code and the
hospital's clinical policies. The postholder is the first point of professional
accountability for the patients in their care during that shift.

**Principal duties.** Assess, plan, implement and evaluate individualised nursing care,
documenting each stage contemporaneously in the electronic patient record. Administer
medicines in accordance with the hospital medicines policy, including the two-person check
required for controlled drugs and intravenous preparations. Monitor and record vital
signs, calculate early warning scores, and escalate deterioration to the nurse in charge
and the medical team without delay.

Take charge of the ward in the absence of the sister or charge nurse on a shift-by-shift
basis once assessed as competent to do so. Give and receive structured handover at every
shift change using the agreed handover tool. Prepare patients for investigations and
theatre, receive them back, and carry out post-procedure observations to the specified
frequency. Contribute to discharge planning from the day of admission, liaising with
therapy, pharmacy and social care colleagues.

Supervise healthcare assistants and student nurses allocated to the same patient group,
delegating only tasks within the individual's assessed competence and retaining
accountability for the delegation itself.

**Person specification.** Registered Nurse with current NMC registration; ability to
demonstrate safe medicines management; commitment to revalidation; evidence of
continuing professional development within the last twelve months.
"""

NORTHFIELD_HCA = """# Job Description — Healthcare Assistant

**Band:** 2, progressing to Band 3  **Reports to:** Staff Nurse and Ward Sister

**Purpose of the role.** The healthcare assistant provides direct personal care and
practical support to patients under the supervision of a registered nurse, contributing to
a clean, safe and dignified ward environment. The role requires no prior professional
registration; all clinical tasks are performed under delegation and within assessed
competence.

**Principal duties.** Assist patients with washing, dressing, oral care, continence care,
toileting and mobility, always working in a way that preserves privacy and dignity. Serve
meals and drinks, assist patients who need help to eat, and accurately record food and
fluid intake on the relevant charts. Reposition patients on the agreed schedule and report
any change in skin condition to the nurse in charge immediately.

Take and record routine observations — temperature, pulse, respiratory rate, blood
pressure, oxygen saturation and blood glucose — once assessed as competent, and report any
reading outside the parameters set for that patient. Prepare beds and clinical areas,
carry out cleaning tasks allocated to nursing staff, and restock consumables. Chaperone
patients during examinations when asked. Escort patients to other departments and hand
over relevant information on arrival.

Answer call bells promptly and communicate with patients and relatives courteously,
referring clinical questions to a registered nurse.

**Person specification.** Good standard of literacy and numeracy; completion of the Care
Certificate within twelve weeks of appointment; ability to work rotating shifts including
nights, weekends and public holidays; evidence of a caring and respectful manner.
"""

NORTHFIELD_TRAINING = """# Recruitment, Qualifications and Mandatory Training

Every nursing vacancy at Northfield General is recruited against the published job
description and person specification, and every appointment is subject to satisfactory
pre-employment checks: identity, right to work, professional registration verified
directly with the NMC, an enhanced disclosure and barring check, two references covering
the previous three years of employment, and occupational health clearance.

Shortlisting is carried out by at least two people, one of whom must have completed
recruitment and selection training within the last three years. Interview panels for Band
6 and above include a member from outside the recruiting division. Values-based questions
are mandatory for every clinical post, and their scores are recorded on the panel record
retained for twelve months.

All new starters attend a two-day corporate induction followed by a local induction
completed within four weeks and signed off by the line manager. Registered nurses new to
the organisation complete a supernumerary period of at least three shifts on their base
ward, extended to ten shifts for newly qualified nurses entering the preceptorship
programme.

Mandatory training is refreshed on a rolling schedule: basic life support, moving and
handling, infection prevention, safeguarding levels one and two, information governance,
fire safety and equality and diversity annually; immediate life support, conflict
resolution and safeguarding level three every three years for the staff groups that
require them. Compliance is reported monthly to the divisional board, and any individual
below 90 per cent compliance is placed on a documented recovery plan with their manager.
"""

NORTHFIELD_PRESSURE = """# Pressure Ulcer Prevention Protocol

Every adult inpatient must have a pressure ulcer risk assessment completed within six
hours of admission to the ward, repeated whenever the patient's condition changes and at
minimum every seven days. The assessment combines a validated risk score with clinical
judgement; a low score never overrides an obvious clinical concern.

A full skin inspection is carried out at the time of the first assessment and at least
daily thereafter, paying particular attention to the sacrum, heels, elbows, shoulder
blades, and any skin in contact with a device such as an oxygen mask, catheter tubing or
cast. Findings are recorded in the electronic record with the anatomical site and the
category of any damage found.

Patients assessed as at risk receive a documented repositioning schedule, normally two
hourly for those unable to reposition themselves independently and four hourly for those
with limited independent movement. A pressure-redistributing mattress is provided within
four hours of the assessment for patients at high risk; the equipment library holds stock
for this purpose and is contactable at all hours.

Nutrition and hydration are reviewed alongside skin care, with referral to the dietitian
where intake is inadequate for more than two days.

Any pressure damage of category two or above acquired after admission is reported as an
incident on the day it is identified, triggers a review by the tissue viability nurse
within one working day, and is included in the ward's monthly harm-free care report.
"""

# --- Riverside Community Clinic — Staff Handbook ---

RIVERSIDE_OVERVIEW = """# Who We Are: Riverside Community Clinic

Riverside Community Clinic is a primary care practice with a registered list of
approximately 14,200 patients, operating from a main site on Mill Street and a branch
surgery in Hollowbrook that opens four days a week. The practice was formed in 2011 by the
merger of two smaller partnerships and is now run by four GP partners supported by a
salaried medical team, a nursing team, and a business and administrative team.

Our purpose is straightforward: to give the people registered with us continuity of care
from a named clinician, and to make routine access to that clinician simple enough that
nobody delays seeking help. Continuity is measured every quarter and reported to the
partnership; it is the single metric the practice treats as non-negotiable.

Services provided directly by the clinic include general medical services, long-term
condition reviews for diabetes, asthma, chronic obstructive pulmonary disease and
hypertension, childhood and seasonal immunisations, cervical screening, minor surgery,
contraceptive services including implant and coil fitting, and a weekly clinic for
patients recently discharged from hospital.

The practice also hosts visiting services provided by the wider health system: community
physiotherapy on Tuesdays, a mental health practitioner on Wednesdays and Fridays, and a
social prescribing link worker four days a week.

This handbook applies to everyone working at the clinic, whether employed by the
partnership, engaged as a locum, or placed with us as a student or apprentice. It should
be read alongside the individual's contract of employment.
"""

RIVERSIDE_BENEFITS = """# Salary Scales, Pension and Employee Benefits

Riverside Community Clinic sets salaries against an internal scale reviewed each April by
the partnership and benchmarked against comparable practices in the region. Administrative
roles occupy scales A to C, nursing and clinical support roles scales D to F, and salaried
clinical roles are appointed on individually negotiated sessional rates. Every scale has
four annual increment points; movement to the next point requires a completed appraisal
and no live formal warning.

Salaried GPs are contracted by session, with each session covering four hours and ten
minutes of clinical time plus associated administration. The standard full-time commitment
is eight sessions per week. Sessional rates include an allowance for professional expenses
and one session per month of protected time for continuing professional development.

All eligible employees are enrolled in the NHS Pension Scheme from their first day; those
not eligible are enrolled in the practice's workplace pension with a five per cent
employer contribution. Employees may opt out in writing at any time and are re-enrolled
automatically every three years as the law requires.

Annual leave is 25 days plus public holidays for administrative staff and 30 days plus
public holidays for clinical staff, both pro-rated for part-time hours. The practice funds
professional registration fees and indemnity cover for all clinical staff, one paid study
day per year for administrative staff, free seasonal influenza vaccination, an interest
free travel loan, and a discretionary annual bonus paid in December when the partnership's
financial position allows.
"""

RIVERSIDE_PRACTICE_MANAGER = """# Job Description — Practice Manager

**Reports to:** The GP Partners  **Hours:** 37 per week  **Scale:** Senior management

**Purpose of the role.** The practice manager is responsible for the operational,
financial and human resource management of the clinic, enabling the clinical team to
deliver safe and effective care. The postholder is the senior non-clinical leader and
deputises for the partners on all business matters.

**Principal duties.** Manage the day-to-day running of both sites, including opening
arrangements, appointment capacity, room allocation and the resolution of operational
problems as they arise. Prepare the annual budget with the partners, monitor income and
expenditure monthly, oversee payroll, and ensure claims for enhanced services and
incentive schemes are submitted accurately and on time.

Lead the administrative and reception teams: recruit, induct, appraise and develop staff;
manage rotas and absence; and handle disciplinary and grievance matters in line with
practice policy. Maintain the practice's compliance framework, including health and safety,
fire, information governance, data protection and regulatory registration, and prepare the
evidence required for inspection.

Own the complaints process end to end, acknowledging within three working days and
responding substantively within twenty working days, and present a quarterly complaints
and significant events summary to the partnership meeting. Manage contracts with
suppliers, the building lease, and the clinical system supplier.

**Person specification.** Substantial management experience, ideally in primary care;
demonstrable financial and budget management; strong working knowledge of employment law;
experience of leading a team through change; qualification in management or equivalent
experience.
"""

RIVERSIDE_GP = """# Job Description — General Practitioner (Salaried)

**Reports to:** Lead GP Partner  **Commitment:** Six to eight sessions per week

**Purpose of the role.** The salaried general practitioner provides general medical
services to the practice's registered population, sharing fully in the clinical workload
and in the practice's commitment to continuity of care.

**Principal duties.** Undertake booked and on-the-day consultations, telephone and online
consultations, and home visits for housebound patients within the practice area. Assess,
diagnose, treat and where appropriate refer patients, working within the limits of
professional competence and referring on when those limits are reached.

Take responsibility for a named list of patients with complex needs, coordinating their
care with community services and secondary care. Contribute to the daily duty rota,
including the triage of urgent requests and the review of pathology results, hospital
correspondence and repeat prescription requests generated by the clinical system.

Participate in the practice's clinical governance programme: significant event analysis,
prescribing review, clinical audit, and the multidisciplinary team meeting held on the
first Wednesday of each month. Supervise and support the nursing team, pharmacist and
paramedic practitioner on clinical questions arising in their own consultations.

Maintain full and contemporaneous records in the clinical system, coding consultations
consistently so that the practice's disease registers remain accurate.

**Person specification.** Full GMC registration with a licence to practise and inclusion
on the National Performers List; membership of a recognised indemnity organisation;
evidence of annual appraisal and current revalidation status; commitment to working at
both practice sites.
"""

RIVERSIDE_RECEPTIONIST = """# Job Description — Medical Receptionist

**Reports to:** Reception Supervisor and Practice Manager  **Scale:** A to B

**Purpose of the role.** The medical receptionist is the first point of contact between
the practice and its patients. The postholder manages access to clinical appointments
courteously and consistently, and handles confidential information with absolute
discretion.

**Principal duties.** Answer incoming telephone calls promptly, establish the reason for
contact using the practice's agreed care navigation questions, and direct the patient to
the most appropriate service — a GP, a nurse, a pharmacist, a community service, or
self-care advice. Book, amend and cancel appointments across both sites, keeping the
appointment book accurate at all times.

Greet patients arriving at the desk, check and update their demographic details, and
manage the flow of the waiting area including any delay announcements. Register new
patients, process temporary resident forms, and prepare records for summarising.

Handle repeat prescription requests received by any route, checking them against the
clinical system and passing queries to the prescribing clinician. Receive, scan and
workflow incoming correspondence to the correct clinician within one working day. Take
messages accurately and pass urgent messages immediately rather than by workflow.

Open and close the premises on a rotating basis, following the security checklist, and
support colleagues at the branch surgery when cover is needed.

**Person specification.** Good standard of general education; confident telephone manner;
accurate keyboard skills; ability to remain calm with distressed or angry callers;
understanding of confidentiality; willingness to complete care navigation training within
three months of appointment.
"""

RIVERSIDE_TRAINING = """# Induction, Competency Framework and Annual Appraisal

Every new member of staff at Riverside Community Clinic follows a structured induction
lasting four weeks. Week one covers the practice, its sites, fire and security procedures,
information governance, confidentiality, and the clinical system. Weeks two to four are
role-specific and are signed off against a checklist held by the line manager; no member of
staff works unsupervised on a task until the corresponding line of that checklist is
signed.

Clinical and administrative competencies are held in a single framework with four levels:
aware, supervised, independent and able to teach. Reception staff are assessed on care
navigation, registration, prescription handling and correspondence workflow. Nursing staff
are assessed on immunisation, cervical screening, wound care, long-term condition review,
and any extended skill such as coil fitting or spirometry. Competence at independent level
expires after two years unless refreshed by observed practice.

Mandatory training for all staff comprises basic life support and anaphylaxis, infection
prevention and control, information governance, fire safety, health and safety,
safeguarding children and adults at the level appropriate to the role, and equality and
diversity. All are annual except safeguarding level three, which is three-yearly.

Every employee has an annual appraisal with their line manager, preceded by a
self-assessment and, for clinical staff, a review of at least one audit and one
significant event. Objectives set at appraisal are reviewed at a documented mid-year
check-in. Appraisal completion is reported to the partnership each October.
"""

RIVERSIDE_PRESCRIBING = """# Repeat Prescribing and Medicines Reconciliation

Repeat prescribing at Riverside Community Clinic operates on a strict authorise-then-issue
model. A medicine is added to a patient's repeat list only by a prescribing clinician, who
at the same time sets the quantity, the number of issues permitted before review, and the
review date. Administrative staff never add, amend or extend a repeat item.

Requests are accepted in writing, through the online account, through the community
pharmacy, or in person; they are not accepted by telephone except for housebound patients
recorded as exempt. Requests are processed within two working days. The processing clerk
checks the item against the repeat list, the requested quantity against the authorised
quantity, and the review date; any mismatch is passed to the duty prescriber rather than
issued.

Every patient on four or more regular medicines, or on any medicine requiring monitoring,
receives a structured medication review at least annually with the practice pharmacist.
The review covers indication, effectiveness, safety, adherence and the patient's own
priorities, and its outcome is recorded against the agreed codes.

Medicines reconciliation is completed within two working days of receiving a hospital
discharge summary. The pharmacist compares the discharge list against the practice record,
resolves every discrepancy with the prescriber, updates the repeat list, and records what
changed and why. Discharge summaries mentioning a high-risk medicine — anticoagulants,
insulin, methotrexate, lithium or opioids — are prioritised the same day.
"""

# --- St Aubyn Maternity Unit — Operational Policy Manual ---

AUBYN_OVERVIEW = """# St Aubyn Maternity Unit at a Glance

St Aubyn Maternity Unit is a consultant-led maternity service co-located with an alongside
midwifery-led birth centre, delivering approximately 3,100 babies each year. The unit
comprises a twelve-room delivery suite, a four-room birth centre with two pools, a
twenty-eight bed postnatal ward, a ten-bed antenatal ward, a triage and day assessment
area open around the clock, and a level two neonatal unit on the same floor.

The service covers the full maternity pathway: community antenatal care delivered from
seven clinic bases, hospital antenatal clinics for women with additional needs, intrapartum
care in either setting, postnatal care in hospital and at home for up to twenty-eight
days, and a consultant-led birth reflections service for women who wish to discuss a
previous birth.

The unit's operating philosophy is that birth is a physiological event that sometimes
needs medical help, not a medical event that sometimes proceeds normally. Practically this
means continuity of carer is offered to every woman, that midwives lead care for women
without complications with obstetric input available rather than imposed, and that any
escalation between the two models is documented with a reason.

Governance sits with a joint obstetric and midwifery board chaired alternately by the
clinical director and the head of midwifery. The board reviews every case meeting the
national reporting criteria, the monthly dashboard of clinical indicators, and the themes
arising from feedback collected from women using the service.
"""

AUBYN_BENEFITS = """# Rostering, On-Call Payments and Maternity Benefits

Midwifery staff at St Aubyn work a rolling twelve-week roster published eight weeks in
advance. The standard pattern is long days and long nights of eleven and a half hours,
with a maximum of three consecutive night shifts and a minimum rest period of forty-six
hours after any run of nights. Requests are accepted up to a fair-shares limit of four
per roster period and are allocated by a documented process rather than by seniority.

Community midwives carry a separate on-call commitment averaging one weekday night per
week and one weekend in six. On-call is paid as an availability allowance plus payment at
the appropriate hourly rate for any period actually worked, with a guaranteed minimum
call-out payment of two hours. A midwife called out after 02:00 is not rostered to a
clinical shift before 14:00 the following day.

Obstetric medical staff work a full shift rota compliant with the working time
regulations, banded and reviewed annually by a joint monitoring exercise.

The unit's own maternity, adoption and shared parental leave provisions follow national
terms: occupational maternity pay of eight weeks at full pay, eighteen weeks at half pay
plus statutory pay, and a further thirteen weeks of statutory pay, subject to twelve
months continuous service at the qualifying week. Keeping-in-touch days are available and
paid. Staff returning from maternity leave are entitled to a phased return over four weeks
and to request flexible working, which the unit will refuse only for one of the statutory
business reasons in writing.
"""

AUBYN_CONSULTANT = """# Job Description — Consultant Obstetrician

**Grade:** Consultant  **Programmed activities:** 10 per week  **Accountable to:** Clinical
Director for Women's Health

**Purpose of the role.** The consultant obstetrician provides expert obstetric and
gynaecological care, leads the multidisciplinary team on the delivery suite when rostered,
and shares in the clinical governance and training responsibilities of the department.

**Principal duties.** Deliver a weekly programme of consultant-led antenatal clinics
including specialist clinics for diabetes in pregnancy and previous caesarean birth. Take
consultant responsibility for the delivery suite on rostered days, attending the twice
daily ward round, reviewing every woman with a complicated labour, and being immediately
available for obstetric emergencies including instrumental birth and caesarean section.

Provide a share of the department's on-call commitment on a one-in-eight rota, remaining
within thirty minutes of the unit while on call. Undertake elective and emergency
operating lists as required and maintain the surgical logbook expected for revalidation.

Supervise and train specialty registrars and foundation doctors, provide named educational
supervision for at least two trainees, and contribute to midwifery skills drills. Lead or
co-lead at least one clinical audit per year and participate in the review of cases meeting
national reporting criteria.

**Person specification.** Full GMC registration with a licence to practise and entry on
the specialist register or within six months of the expected date of entry; MRCOG or
equivalent; evidence of advanced training in a subspecialty relevant to the department's
needs; documented experience of teaching and audit; ability to meet the on-call travel
requirement.
"""

AUBYN_COMMUNITY_MIDWIFE = """# Job Description — Community Midwife

**Band:** 6  **Reports to:** Community Team Leader  **Base:** One of seven clinic bases

**Purpose of the role.** The community midwife provides midwifery care to a named caseload
of women through pregnancy, birth and the postnatal period, working autonomously within
the NMC Code and referring to obstetric or neonatal colleagues where care needs step
beyond midwifery-led practice.

**Principal duties.** Carry a personal caseload of women, undertaking the booking
appointment before the tenth week of pregnancy wherever possible, and completing the
antenatal schedule of care at the intervals set by national guidance. Assess each woman's
physical, psychological and social needs at every contact, and make onward referrals to
obstetric, mental health, safeguarding, smoking cessation or social care services as
indicated.

Provide intrapartum care for home births and in the alongside birth centre as part of the
on-call rota, including the transfer of care to the delivery suite where escalation is
required. Provide postnatal care in the woman's home, including newborn examination where
qualified, feeding support, and assessment for postnatal depression using the agreed tool.

Maintain accurate contemporaneous records in the maternity information system, complete
the statutory notification of birth, and contribute to the community team's caseload
review meeting each fortnight.

Act as practice supervisor and assessor for student midwives allocated to the team.

**Person specification.** Registered Midwife with current NMC registration; ability to
work autonomously and carry an on-call commitment; full driving licence and access to a
vehicle; newborn examination qualification desirable; evidence of continuing professional
development in the last twelve months.
"""

AUBYN_SUPPORT_WORKER = """# Job Description — Maternity Support Worker

**Band:** 3  **Reports to:** Ward Manager or Community Team Leader

**Purpose of the role.** The maternity support worker assists registered midwives in
providing care to women and their babies in hospital and community settings, undertaking
delegated clinical tasks within assessed competence and providing practical and emotional
support to families.

**Principal duties.** Support women with personal care, mobility and comfort after birth,
and assist with the care of babies including bathing, nappy care and safe sleep advice.
Provide practical infant feeding support under the direction of a midwife, in line with
the unit's accredited infant feeding standards, and escalate any feeding concern promptly.

Record maternal observations including temperature, pulse, blood pressure and respiratory
rate, perform venepuncture and newborn blood spot sampling once assessed as competent, and
report any result or observation outside the expected range to the midwife responsible for
that woman. Prepare clinical rooms and equipment for antenatal clinics, chaperone during
examinations, and maintain stock levels.

Undertake home visits alongside or, for defined postnatal contacts, on behalf of the
community midwife, following the visit plan and reporting back the same day. Support the
running of parent education sessions and the antenatal clinic administration.

**Person specification.** Level 3 qualification in maternity support or health and social
care, or willingness to work towards it; completion of the Care Certificate; ability to
work shifts including weekends; good written and verbal communication; understanding of
the boundaries of the delegated role.
"""

AUBYN_TRAINING = """# Midwifery Preceptorship and Skills Drills Programme

Every newly registered midwife joining St Aubyn enters a twelve-month preceptorship
programme. The first four weeks are supernumerary and rotate through the delivery suite,
the birth centre, the postnatal ward and a community team so the preceptee sees the whole
pathway before carrying independent responsibility. A named preceptor is allocated on day
one and protected time is rostered for a monthly meeting.

Progress is assessed against a competency document covering intrapartum care,
perineal repair, intravenous cannulation and drug administration, newborn assessment,
emergency response and record keeping. Each competency is signed at supervised, then
independent level by an assessor who has observed practice directly; self-declaration is
not accepted for any clinical competency.

Multidisciplinary skills drills run every week on the delivery suite and monthly in the
community teams. The annual cycle covers shoulder dystocia, postpartum haemorrhage,
maternal collapse and resuscitation, cord prolapse, eclampsia, neonatal resuscitation and
breech birth. Attendance is mandatory: every clinical member of staff, including consultant
obstetricians and anaesthetists, must attend the full annual set, and attendance is
reported to the joint governance board each quarter.

Mandatory training additionally includes fetal monitoring assessment, which must be passed
annually by anyone interpreting a cardiotocograph, safeguarding at level three for all
clinical staff, and the unit's own escalation and human factors session.
"""

AUBYN_HAEMORRHAGE = """# Obstetric Haemorrhage: Emergency Response

Postpartum haemorrhage is declared when blood loss after birth reaches 500 millilitres and
is continuing, or at any volume where the woman shows signs of cardiovascular compromise.
Estimated loss is never used alone: all swabs, pads and drapes are weighed, and the
running total is called aloud and recorded on the haemorrhage chart by a designated
scribe.

On declaration the emergency call is made using the unit's 2222 number stating "obstetric
haemorrhage" and the location. The call summons the obstetric registrar and consultant,
the anaesthetic registrar, a second midwife, the theatre team and a porter for urgent
blood transport. The delivery suite coordinator attends every call and takes the role of
team leader until a more senior clinician assumes it explicitly.

Immediate management runs in parallel rather than in sequence: lie the woman flat and give
high-flow oxygen; insert two large-bore cannulae and take a full blood count, coagulation
screen and cross-match for four units; begin a warmed crystalloid infusion; rub up a
uterine contraction and empty the bladder; and give uterotonic drugs in the order set out
on the haemorrhage proforma.

If bleeding continues beyond 1,000 millilitres the major haemorrhage protocol is
activated, which releases the emergency blood pack from the transfusion laboratory without
further authorisation. Transfer to theatre for examination under anaesthesia is made
without delay where the cause is not controlled. A structured debrief with the woman and
with the team is held within seventy-two hours.
"""

# --- Lakeside Surgical Centre — Theatre Operations Manual ---

LAKESIDE_OVERVIEW = """# Lakeside Surgical Centre: Purpose and Services

Lakeside Surgical Centre is a dedicated elective treatment centre with six operating
theatres, two of which have laminar flow ventilation for joint replacement, a
twenty-four bed day surgery unit and a thirty-two bed inpatient ward. The centre performs
around 14,000 procedures a year and takes no emergency admissions, which is the single
structural reason its cancellation rate stays below two per cent.

Specialties operating at the centre are orthopaedics, general surgery, urology,
gynaecology, ophthalmology and oral surgery. Anaesthetic services cover general, regional
and local anaesthesia with sedation, supported by a four-bay recovery area staffed
whenever a theatre is running.

The centre's operating principle is separation: elective work is physically and
organisationally separated from emergency work so that beds, staff and theatre time
committed to a planned operation cannot be taken for another purpose. Every process in
this manual exists to protect that separation or to protect the patient once inside it.

Patients are admitted only after a completed pre-operative assessment, a documented
anaesthetic review where indicated, and confirmation that the planned discharge
arrangements are in place. Procedures are scheduled in lists built by the theatre
scheduling team against published session times, with the order of the list set by the
operating surgeon on clinical grounds — children, patients with diabetes and patients with
learning disabilities are placed early unless there is a stated reason not to.

The centre is registered with the regulator and inspected against the safe, effective,
caring, responsive and well-led domains.
"""

LAKESIDE_BENEFITS = """# Theatre Staff Pay, Overtime and Benefits Policy

Theatre practitioners at Lakeside are appointed at Band 5 on registration and progress to
Band 6 on completion of the centre's specialist competency portfolio in either scrub,
anaesthetics or recovery, which typically takes eighteen months. Theatre support workers
are appointed at Band 2 and move to Band 3 on completion of their competency portfolio.
Team leaders are Band 7 and the theatre manager Band 8a.

Scheduled sessions run from 08:00 to 18:00 Monday to Friday, with a Saturday list rota
operating three weekends in four. Saturday working attracts the standard enhancement;
staff who work more than one Saturday in a four-week period receive a compensating rest day
in the following period.

Overlist running beyond the scheduled session is paid at the plain hourly rate for the
first hour and at time and a half thereafter, and requires the theatre coordinator's
authorisation before the list is extended. Unauthorised overruns are still paid, but are
reported to the theatre manager as a scheduling exception so the underlying cause is
addressed.

Staff receive 27 days of annual leave plus public holidays, rising with service, and are
required to take at least two weeks as a continuous block to support roster planning.
Additional benefits include employer-funded professional registration fees, a fully funded
place on one accredited course per person per year subject to service need, free on-site
parking, subsidised meals when working an extended day, and access to the occupational
health physiotherapy service without referral.
"""

LAKESIDE_SCRUB = """# Job Description — Theatre Scrub Practitioner

**Band:** 5, progressing to 6  **Reports to:** Theatre Team Leader

**Purpose of the role.** The scrub practitioner prepares for and assists at surgical
procedures, maintaining the sterile field and accounting for every instrument, swab and
sharp used during the operation. The postholder is accountable for the accuracy of the
surgical count.

**Principal duties.** Prepare the theatre and the instrument trolleys for each case on the
list, checking instrument sets against the tray list and confirming the availability of
implants, prostheses and specialist equipment before the patient is sent for. Participate
fully in the team brief at the start of the list and in each of the three checklist
moments for every patient.

Perform the surgical scrub, gown and glove, and maintain the sterile field throughout,
challenging any breach regardless of the seniority of the person who caused it.
Anticipate and pass instruments, manage specimens and label them correctly at the point of
removal, and handle sharps using a designated neutral zone.

Carry out the count of swabs, needles, blades and instruments with a second registered
practitioner before the procedure begins, at the closure of a cavity, and at the end of
the operation, resolving any discrepancy before the patient leaves theatre.

Support the safe positioning of the patient, the application of diathermy and tourniquets,
and the documented checks each requires.

**Person specification.** Registered Nurse or Operating Department Practitioner with
current NMC or HCPC registration; completion of or willingness to complete the scrub
competency portfolio; ability to participate in the Saturday list rota; meticulous
attention to detail under time pressure.
"""

LAKESIDE_ODP = """# Job Description — Operating Department Practitioner (Anaesthetics)

**Band:** 5, progressing to 6  **Reports to:** Anaesthetic Team Leader

**Purpose of the role.** The anaesthetic ODP assists the anaesthetist through every phase
of anaesthesia, prepares and checks the anaesthetic environment and equipment, and
provides skilled support during induction, maintenance and emergence.

**Principal duties.** Complete the full anaesthetic machine and equipment check before the
first case of each list and record it, and confirm the availability and working order of
the difficult airway trolley, suction, monitoring and warming devices. Prepare drugs with
the anaesthetist, labelling every syringe at the moment of drawing up and applying the
two-person check for controlled drugs.

Receive the patient into the anaesthetic room, confirm identity, procedure, site and
consent as part of the sign-in, and provide reassurance during induction. Assist with
airway management including cricoid pressure and the passing of airway devices, with
vascular access, with regional blocks, and with invasive monitoring insertion.

Anticipate and respond to anaesthetic emergencies including failed intubation, anaphylaxis
and malignant hyperthermia, knowing the location and contents of every emergency box in
the department. Support safe transfer of the patient to the operating table and to
recovery, handing over to the recovery practitioner using the agreed structure.

Maintain stock in the anaesthetic room and report equipment faults through the asset
system on the day they are found.

**Person specification.** HCPC registration as an Operating Department Practitioner, or
NMC registration with an anaesthetic nursing qualification; current adult and paediatric
life support certification; ability to work at pace while remaining methodical.
"""

LAKESIDE_SUPPORT = """# Job Description — Theatre Support Worker

**Band:** 2, progressing to Band 3  **Reports to:** Theatre Team Leader

**Purpose of the role.** The theatre support worker keeps the operating department running
by preparing rooms and equipment, moving patients safely, and supporting the registered
team with delegated tasks that do not require registration.

**Principal duties.** Prepare the theatre between cases: clean surfaces and equipment to
the agreed standard, dispose of clinical waste and sharps correctly, restock consumables
against the list held in each room, and confirm the room is ready to the practitioner in
charge. Assist with the safe transfer and positioning of patients using the correct
handling equipment, and apply patient warming and anti-embolism devices under direction.

Collect patients from the ward with a registered member of staff, checking identity and
the theatre checklist on collection, and return patients to recovery and the ward.
Transport specimens to the laboratory promptly and record the transfer.

Assist the scrub practitioner as a runner during the case, opening sterile packs onto the
field without contaminating them, connecting suction and diathermy leads, and fetching
additional instruments or implants when asked. Take used instrument sets to the
decontamination hatch, complete the tracking record, and collect processed sets.

**Person specification.** Good standard of literacy and numeracy; completion of the Care
Certificate within twelve weeks; ability to stand for extended periods and to move
equipment safely; understanding of asepsis sufficient to work near a sterile field;
reliability and punctuality, as lists cannot start without the room being ready.
"""

LAKESIDE_TRAINING = """# Surgical Competency Sign-Off and Audit Cycle

Nobody at Lakeside works unsupervised in a theatre role until the corresponding competency
portfolio is signed. Portfolios exist for scrub, anaesthetic assistance, recovery, and
support-worker duties, and each is structured as a set of observed practice records, a
knowledge assessment and a reflective account. Sign-off requires two different assessors,
at least one of whom must be a team leader.

Speciality-specific sign-off sits on top of the core portfolio. A practitioner competent to
scrub for general surgery is not thereby competent to scrub for arthroplasty; each
specialty list has its own instrument, implant and positioning requirements, and the
rostering system will not allocate a practitioner to a list for which they hold no
sign-off. Competence lapses if a practitioner has not worked that specialty for six
months, and is restored by three supervised cases.

The department runs a rolling audit cycle with one topic per month. Fixed topics repeated
annually are the surgical safety checklist, the swab and instrument count, surgical site
infection surveillance, prophylactic antibiotic timing, venous thromboembolism assessment,
specimen labelling accuracy and theatre start and turnaround times.

Each audit is presented at the departmental governance meeting with an action plan naming
an owner and a date, and every action is re-checked at the following meeting until closed.
Where an audit shows compliance below 95 per cent on a safety-critical measure, a repeat
audit is scheduled within eight weeks rather than the following year.
"""

LAKESIDE_CHECKLIST = """# WHO Surgical Safety Checklist and Count Procedure

Every procedure carried out at Lakeside uses the five-part safety process: team brief
before the list, sign-in before induction, time-out before incision, sign-out before the
patient leaves theatre, and debrief at the end of the list. No part may be delegated to
paperwork completed afterwards; each is a spoken exchange with the whole team stopped and
attending.

At the brief the list is read through case by case, naming each patient, the planned
procedure and side, the anticipated duration, implants required, and any anticipated
difficulty. Everyone present introduces themselves by name and role. At sign-in the
patient confirms their identity, the procedure, the site and their consent, and allergies,
airway risk, blood loss risk and anaesthetic equipment checks are confirmed aloud.

Time-out happens with the patient draped and the surgeon present. The team confirms
identity, procedure and marked site, the sterility of the instruments, prophylactic
antibiotics given within the last sixty minutes, imaging displayed, and any critical step
each discipline expects.

The count of swabs, needles, blades, and separable instrument parts is performed by the
scrub practitioner and a second registered practitioner: before the procedure, when any
cavity is closed, and at the end. Counts are recorded on the theatre whiteboard as they
occur. A discrepancy stops the closure: the field is searched, the bins and drapes are
searched, and if the item is still unaccounted for an intra-operative radiograph is taken
before the patient leaves the theatre.
"""

# --- Meridian Home Care Services — Employee Policy and Roles Manual ---

MERIDIAN_OVERVIEW = """# Meridian Home Care: Company Overview

Meridian Home Care Services is an independent domiciliary care provider supporting around
620 people in their own homes across three neighbouring local authority areas. The company
was founded in 2009, employs just over 300 care workers, and is registered with and
inspected by the national care regulator.

Meridian provides personal care, medication support, meal preparation, domestic support
and companionship, ranging from a single thirty-minute call a day to a full package of
four calls a day with waking or sleeping night cover. About sixty per cent of the work is
commissioned by the three local authorities under framework contracts, and the remainder is
arranged privately by individuals and families.

The company's stated purpose is to make it possible for people to stay in their own homes
for as long as that is what they want and it remains safe. The commitments that follow
from that are: a small consistent team for each person supported, calls that arrive within
a thirty-minute window of the agreed time, and a care plan written with the person rather
than about them.

Meridian operates from a central office with an on-call service covering every hour
outside office time. Field supervision is provided by six care coordinators and three
registered managers, each responsible for a geographic area. A digital care record is used
at the point of care: every visit is checked in and out on the worker's phone, and every
task and observation is recorded before the worker leaves.
"""

MERIDIAN_BENEFITS = """# Reward, Mileage and Employee Benefits

Meridian pays care workers an hourly rate for all working time, including travel time
between calls, which is calculated automatically by the scheduling system from the actual
route rather than estimated. Rates are banded: standard rate for weekday daytime calls,
an enhanced rate for evenings after 20:00 and for weekends, and a further enhancement for
public holidays. Sleeping nights are paid as a flat allowance plus the hourly rate for any
period the worker is woken and working.

Mileage is paid at 45 pence per mile for the first 10,000 business miles in a tax year and
25 pence thereafter, claimed automatically from the checked-in route and paid with the
following month's salary. Public transport fares are reimbursed in full on production of a
receipt. Business insurance for the worker's own vehicle is a condition of employment for
roles requiring driving, and Meridian contributes a fixed annual sum towards its cost.

Care workers accrue holiday at the statutory rate of 5.6 weeks pro-rated to hours worked,
and are paid holiday at their average rate over the previous fifty-two weeks so that
enhancements are properly reflected.

Additional benefits include a workplace pension with a four per cent employer
contribution, statutory sick pay from day one of absence with company sick pay after two
years of service, a refer-a-friend payment of £250 paid after the new recruit completes
six months, free enhanced disclosure checks, a paid day of leave on the employee's
birthday, and access to a 24-hour counselling line.
"""

MERIDIAN_MANAGER = """# Job Description — Registered Care Manager

**Reports to:** Operations Director  **Registration:** Named registered manager with the
care regulator for the allocated area

**Purpose of the role.** The registered care manager holds legal and regulatory
responsibility for the quality and safety of the care delivered in their area, and leads
the coordinators and care workers who deliver it.

**Principal duties.** Ensure the service in the allocated area meets the fundamental
standards at all times, and maintain the evidence that demonstrates it. Complete or
delegate the initial assessment of every new person supported, agree the care plan and
risk assessments with them and their family, and review each plan at least every six
months or sooner if needs change.

Lead the safeguarding response for the area: act as the point of referral for concerns
raised by staff, make referrals to the local authority within agreed timescales,
participate in strategy discussions, and complete internal investigations. Manage
incidents, accidents and medication errors, ensuring each is recorded, investigated and
learned from, and submit statutory notifications where the threshold is met.

Recruit, induct, supervise and appraise the coordinators and care workers in the area,
carrying out at least one observed practice check on every care worker each year and
holding documented supervision quarterly. Manage capacity: accept or decline new packages
against the area's actual staffing, and never accept a package that cannot be delivered
consistently.

**Person specification.** Level 5 Diploma in Leadership for Health and Social Care or
equivalent; at least two years supervisory experience in a regulated care service; fitness
to be registered with the regulator; full driving licence; strong understanding of
safeguarding and mental capacity legislation.
"""

MERIDIAN_CARE_WORKER = """# Job Description — Community Care Worker

**Reports to:** Care Coordinator  **Hours:** Zero-hours, part-time or full-time contracts
available

**Purpose of the role.** The community care worker delivers personal care and practical
support to people in their own homes, following each person's care plan and treating their
home, their routine and their preferences with respect.

**Principal duties.** Deliver the calls allocated on the daily rota, checking in on arrival
and out on leaving using the mobile application so that the office can see in real time
that every person has been visited. Provide personal care including washing, showering,
dressing, oral care, shaving, continence support and catheter or stoma care where trained,
always in the manner recorded in the care plan.

Support with medication according to the level recorded for that person — prompting,
assisting or administering — signing the medication administration record at the time and
never in advance. Report any refused, missed or wrongly available medicine to the office
before leaving the property.

Prepare meals and drinks, encourage adequate intake, and record what was eaten where
monitoring is in place. Carry out agreed domestic tasks and light shopping. Support people
to get to appointments, social activities and community groups where this is part of the
package.

Observe and report change: skin condition, mood, appetite, mobility, confusion, or
anything about the home environment that appears unsafe. Complete the digital care record
before leaving each call.

**Person specification.** No prior experience required; completion of the Care Certificate
within twelve weeks; ability to work to a rota including alternate weekends; driving
licence and business insurance for rural rounds; patience, reliability and discretion.
"""

MERIDIAN_COORDINATOR = """# Job Description — Care Coordinator (Scheduling)

**Reports to:** Registered Care Manager  **Base:** Central office with field visits

**Purpose of the role.** The care coordinator builds and maintains the rota for their
area, matching care workers to the people they support so that continuity, travel time and
contracted hours all work — and holds the front line of the office's contact with workers
and families.

**Principal duties.** Produce the area rota at least one week in advance, allocating calls
to the smallest practical team for each person supported and building in realistic travel
time between addresses. Adjust the live rota through the day for sickness, traffic and
changes in need, ensuring no call is missed and that any call running more than thirty
minutes late is communicated to the person or their family.

Act as first point of contact for care workers on shift, answering questions about tasks
and equipment, and escalating clinical or safeguarding concerns to the registered manager
immediately rather than resolving them alone. Carry a share of the out-of-hours on-call
phone on a published rota.

Complete introductory visits with new care workers so that the person supported meets
their worker before the first solo call. Monitor call timing and duration data weekly and
report exceptions. Maintain accurate records of worker availability, training expiry dates
and vehicle documents, and stop allocation to any worker whose mandatory training or
disclosure check has lapsed.

**Person specification.** Experience in a scheduling, dispatch or coordination role;
confident with rostering software and spreadsheets; excellent telephone manner under
pressure; understanding of domiciliary care; willingness to undertake occasional care
calls to cover emergencies.
"""

MERIDIAN_TRAINING = """# Care Certificate, Shadowing and Refresher Training Requirements

No care worker at Meridian attends a call alone until three conditions are met: the
five-day classroom induction is complete, a minimum of four shadowing shifts have been
signed off by an experienced worker, and an observed practice check has been carried out
by a coordinator or registered manager.

The classroom induction covers the fifteen standards of the Care Certificate at
introductory level, moving and handling with practical assessment, basic life support,
safeguarding adults, medication support, infection prevention, food hygiene, and the
digital care record. The full Care Certificate portfolio must then be completed within
twelve weeks of the start date; failure to complete it without an agreed extension stops
further shift allocation.

Shadowing shifts are arranged in the geographic area the worker will actually cover and
include at least one double-handed call and one medication call. The experienced worker
records what the new worker did and did not do, and any gap identified is addressed before
sign-off rather than noted for later.

Refresher training runs on a fixed cycle: moving and handling, basic life support,
safeguarding, medication and infection prevention annually; food hygiene, fire safety and
mental capacity every two years. Specialist competencies — catheter care, stoma care,
percutaneous feeding, end-of-life care and epilepsy rescue medication — are trained by the
clinical lead and reassessed annually against a practical checklist. The scheduling system
blocks allocation to any call requiring a competency the worker does not currently hold.
"""

MERIDIAN_CARE_PLANNING = """# Person-Centred Care Planning and Review

Every person supported by Meridian has a care plan written with them, in their own words
wherever possible, before the first call takes place. The plan is produced from an
assessment visit carried out by a registered manager or a senior coordinator, and covers
what the person can do independently, what they want help with, how they want that help
given, and what matters to them beyond the tasks.

The plan is structured by call rather than by task list, so a worker arriving for the
morning call reads what that call involves, in what order, and what the person's
preferences are — which side they prefer to be assisted from, how they take their tea, what
they want to be called. Risk assessments sit alongside the plan and cover moving and
handling, the home environment, medication, nutrition, skin integrity and lone working.

A copy of the plan is held in the home and the same content is available in the digital
care record on the worker's phone; the digital version is the master and any handwritten
change must be entered in the system the same day.

Plans are reviewed at six weeks after the package starts and at least every six months
thereafter, with the person and, where they wish, a family member. A review is also
triggered by any hospital admission, fall, safeguarding concern, medication error, or a
request from the person or a worker. Every review records what changed, what stayed the
same, and who agreed it.
"""

# --- corpus layout ---
# Each section is (title, level, hierarchy_path, page_start, page_end, section_type, markdown).
# Groups carry no body and no type: the wiki generator gives them a Contents rollup, and
# leaving them untyped keeps a `section_type` filter returning exactly the leaf sections.

GROUP = None

DOCUMENTS: list[dict] = [
	{
		"title": "Northfield Hospital Nursing Manual",
		"page_count": 48,
		"sections": [
			("Governance and Pay", 1, ["Governance and Pay"], 1, 12, GROUP, ""),
			(
				"Hospital Overview and Mission",
				2,
				["Governance and Pay", "Hospital Overview and Mission"],
				2,
				5,
				"other",
				NORTHFIELD_OVERVIEW,
			),
			(
				"Pay Bands, Benefits and Leave Entitlements",
				2,
				["Governance and Pay", "Pay Bands, Benefits and Leave Entitlements"],
				6,
				12,
				"administrative_policies",
				NORTHFIELD_BENEFITS,
			),
			("Roles and Responsibilities", 1, ["Roles and Responsibilities"], 13, 30, GROUP, ""),
			(
				"Job Description — Ward Sister / Charge Nurse",
				2,
				["Roles and Responsibilities", "Job Description — Ward Sister / Charge Nurse"],
				13,
				18,
				"staff_roles_and_responsibilities",
				NORTHFIELD_WARD_SISTER,
			),
			(
				"Job Description — Staff Nurse (Band 5)",
				2,
				["Roles and Responsibilities", "Job Description — Staff Nurse (Band 5)"],
				19,
				24,
				"staff_roles_and_responsibilities",
				NORTHFIELD_STAFF_NURSE,
			),
			(
				"Job Description — Healthcare Assistant",
				2,
				["Roles and Responsibilities", "Job Description — Healthcare Assistant"],
				25,
				30,
				"staff_roles_and_responsibilities",
				NORTHFIELD_HCA,
			),
			("Practice Standards", 1, ["Practice Standards"], 31, 48, GROUP, ""),
			(
				"Recruitment, Qualifications and Mandatory Training",
				2,
				["Practice Standards", "Recruitment, Qualifications and Mandatory Training"],
				31,
				37,
				"training_and_audits",
				NORTHFIELD_TRAINING,
			),
			(
				"Pressure Ulcer Prevention Protocol",
				2,
				["Practice Standards", "Pressure Ulcer Prevention Protocol"],
				38,
				48,
				"clinical_protocols",
				NORTHFIELD_PRESSURE,
			),
		],
	},
	{
		"title": "Riverside Clinic Staff Handbook",
		"page_count": 40,
		"sections": [
			("Governance and Pay", 1, ["Governance and Pay"], 1, 10, GROUP, ""),
			(
				"Who We Are: Riverside Community Clinic",
				2,
				["Governance and Pay", "Who We Are: Riverside Community Clinic"],
				2,
				5,
				"other",
				RIVERSIDE_OVERVIEW,
			),
			(
				"Salary Scales, Pension and Employee Benefits",
				2,
				["Governance and Pay", "Salary Scales, Pension and Employee Benefits"],
				6,
				10,
				"administrative_policies",
				RIVERSIDE_BENEFITS,
			),
			("Roles and Responsibilities", 1, ["Roles and Responsibilities"], 11, 28, GROUP, ""),
			(
				"Job Description — Practice Manager",
				2,
				["Roles and Responsibilities", "Job Description — Practice Manager"],
				11,
				16,
				"staff_roles_and_responsibilities",
				RIVERSIDE_PRACTICE_MANAGER,
			),
			(
				"Job Description — General Practitioner (Salaried)",
				2,
				["Roles and Responsibilities", "Job Description — General Practitioner (Salaried)"],
				17,
				22,
				"staff_roles_and_responsibilities",
				RIVERSIDE_GP,
			),
			(
				"Job Description — Medical Receptionist",
				2,
				["Roles and Responsibilities", "Job Description — Medical Receptionist"],
				23,
				28,
				"staff_roles_and_responsibilities",
				RIVERSIDE_RECEPTIONIST,
			),
			("Practice Standards", 1, ["Practice Standards"], 29, 40, GROUP, ""),
			(
				"Induction, Competency Framework and Annual Appraisal",
				2,
				["Practice Standards", "Induction, Competency Framework and Annual Appraisal"],
				29,
				34,
				"training_and_audits",
				RIVERSIDE_TRAINING,
			),
			(
				"Repeat Prescribing and Medicines Reconciliation",
				2,
				["Practice Standards", "Repeat Prescribing and Medicines Reconciliation"],
				35,
				40,
				"medication_management",
				RIVERSIDE_PRESCRIBING,
			),
		],
	},
	{
		"title": "St Aubyn Maternity Policy Manual",
		"page_count": 44,
		"sections": [
			("Unit Administration", 1, ["Unit Administration"], 1, 11, GROUP, ""),
			(
				"St Aubyn Maternity Unit at a Glance",
				2,
				["Unit Administration", "St Aubyn Maternity Unit at a Glance"],
				2,
				6,
				"other",
				AUBYN_OVERVIEW,
			),
			(
				"Rostering, On-Call Payments and Maternity Benefits",
				2,
				["Unit Administration", "Rostering, On-Call Payments and Maternity Benefits"],
				7,
				11,
				"administrative_policies",
				AUBYN_BENEFITS,
			),
			("Roles and Responsibilities", 1, ["Roles and Responsibilities"], 12, 30, GROUP, ""),
			(
				"Job Description — Consultant Obstetrician",
				2,
				["Roles and Responsibilities", "Job Description — Consultant Obstetrician"],
				12,
				18,
				"staff_roles_and_responsibilities",
				AUBYN_CONSULTANT,
			),
			(
				"Job Description — Community Midwife",
				2,
				["Roles and Responsibilities", "Job Description — Community Midwife"],
				19,
				24,
				"staff_roles_and_responsibilities",
				AUBYN_COMMUNITY_MIDWIFE,
			),
			(
				"Job Description — Maternity Support Worker",
				2,
				["Roles and Responsibilities", "Job Description — Maternity Support Worker"],
				25,
				30,
				"staff_roles_and_responsibilities",
				AUBYN_SUPPORT_WORKER,
			),
			("Clinical Standards", 1, ["Clinical Standards"], 31, 44, GROUP, ""),
			(
				"Midwifery Preceptorship and Skills Drills Programme",
				2,
				["Clinical Standards", "Midwifery Preceptorship and Skills Drills Programme"],
				31,
				36,
				"training_and_audits",
				AUBYN_TRAINING,
			),
			(
				"Obstetric Haemorrhage: Emergency Response",
				2,
				["Clinical Standards", "Obstetric Haemorrhage: Emergency Response"],
				37,
				44,
				"emergency_procedures",
				AUBYN_HAEMORRHAGE,
			),
		],
	},
	{
		"title": "Lakeside Surgical Theatre Manual",
		"page_count": 52,
		"sections": [
			(
				"Centre Administration",
				1,
				["Centre Administration"],
				1,
				12,
				GROUP,
				"",
			),
			(
				"Lakeside Surgical Centre: Purpose and Services",
				2,
				["Centre Administration", "Lakeside Surgical Centre: Purpose and Services"],
				2,
				6,
				"other",
				LAKESIDE_OVERVIEW,
			),
			(
				"Theatre Staff Pay, Overtime and Benefits Policy",
				2,
				["Centre Administration", "Theatre Staff Pay, Overtime and Benefits Policy"],
				7,
				12,
				"administrative_policies",
				LAKESIDE_BENEFITS,
			),
			("Roles and Responsibilities", 1, ["Roles and Responsibilities"], 13, 34, GROUP, ""),
			(
				"Job Description — Theatre Scrub Practitioner",
				2,
				["Roles and Responsibilities", "Job Description — Theatre Scrub Practitioner"],
				13,
				19,
				"staff_roles_and_responsibilities",
				LAKESIDE_SCRUB,
			),
			(
				"Job Description — Operating Department Practitioner (Anaesthetics)",
				2,
				[
					"Roles and Responsibilities",
					"Job Description — Operating Department Practitioner (Anaesthetics)",
				],
				20,
				27,
				"staff_roles_and_responsibilities",
				LAKESIDE_ODP,
			),
			(
				"Job Description — Theatre Support Worker",
				2,
				["Roles and Responsibilities", "Job Description — Theatre Support Worker"],
				28,
				34,
				"staff_roles_and_responsibilities",
				LAKESIDE_SUPPORT,
			),
			("Operational Standards", 1, ["Operational Standards"], 35, 52, GROUP, ""),
			(
				"Surgical Competency Sign-Off and Audit Cycle",
				2,
				["Operational Standards", "Surgical Competency Sign-Off and Audit Cycle"],
				35,
				42,
				"training_and_audits",
				LAKESIDE_TRAINING,
			),
			(
				"WHO Surgical Safety Checklist and Count Procedure",
				2,
				["Operational Standards", "WHO Surgical Safety Checklist and Count Procedure"],
				43,
				52,
				"surgical_procedures",
				LAKESIDE_CHECKLIST,
			),
		],
	},
	{
		"title": "Meridian Home Care Employee Manual",
		"page_count": 38,
		"sections": [
			("Company Administration", 1, ["Company Administration"], 1, 10, GROUP, ""),
			(
				"Meridian Home Care: Company Overview",
				2,
				["Company Administration", "Meridian Home Care: Company Overview"],
				2,
				5,
				"other",
				MERIDIAN_OVERVIEW,
			),
			(
				"Reward, Mileage and Employee Benefits",
				2,
				["Company Administration", "Reward, Mileage and Employee Benefits"],
				6,
				10,
				"administrative_policies",
				MERIDIAN_BENEFITS,
			),
			("Roles and Responsibilities", 1, ["Roles and Responsibilities"], 11, 28, GROUP, ""),
			(
				"Job Description — Registered Care Manager",
				2,
				["Roles and Responsibilities", "Job Description — Registered Care Manager"],
				11,
				17,
				"staff_roles_and_responsibilities",
				MERIDIAN_MANAGER,
			),
			(
				"Job Description — Community Care Worker",
				2,
				["Roles and Responsibilities", "Job Description — Community Care Worker"],
				18,
				23,
				"staff_roles_and_responsibilities",
				MERIDIAN_CARE_WORKER,
			),
			(
				"Job Description — Care Coordinator (Scheduling)",
				2,
				["Roles and Responsibilities", "Job Description — Care Coordinator (Scheduling)"],
				24,
				28,
				"staff_roles_and_responsibilities",
				MERIDIAN_COORDINATOR,
			),
			("Service Standards", 1, ["Service Standards"], 29, 38, GROUP, ""),
			(
				"Care Certificate, Shadowing and Refresher Training Requirements",
				2,
				["Service Standards", "Care Certificate, Shadowing and Refresher Training Requirements"],
				29,
				33,
				"training_and_audits",
				MERIDIAN_TRAINING,
			),
			(
				"Person-Centred Care Planning and Review",
				2,
				["Service Standards", "Person-Centred Care Planning and Review"],
				34,
				38,
				"patient_management",
				MERIDIAN_CARE_PLANNING,
			),
		],
	},
]


def build_sections(spec: dict) -> list[Section]:
	"""Turn a document spec's tuples into the `Section` objects `store.replace_sections` eats."""
	return [
		Section(
			title=title,
			level=level,
			hierarchy_path=list(path),
			page_start=page_start,
			page_end=page_end,
			markdown=markdown.strip(),
			section_type=section_type,
		)
		for title, level, path, page_start, page_end, section_type, markdown in spec["sections"]
	]


def get_or_create_project() -> str:
	"""Get-or-create the demo project, keyed on `project_name`. Returns its name."""
	existing = frappe.db.get_value("Wikify Project", {"project_name": PROJECT_NAME}, "name")
	if existing:
		return existing
	return (
		frappe.get_doc(
			{
				"doctype": "Wikify Project",
				"project_name": PROJECT_NAME,
				"description": "Five healthcare manuals with overlapping section types — the RAG POC corpus.",
			}
		)
		.insert(ignore_permissions=True)
		.name
	)


def get_or_create_space() -> str:
	"""Get-or-create the demo Wiki Space, keyed on its route. Returns its name."""
	existing = frappe.db.get_value("Wiki Space", {"route": SPACE_ROUTE}, "name")
	if existing:
		return existing
	space = frappe.new_doc("Wiki Space")
	space.space_name = SPACE_NAME
	space.route = SPACE_ROUTE
	space.is_published = 1
	space.insert(ignore_permissions=True)
	return space.name


def get_or_create_document(spec: dict, project: str) -> str:
	"""Get-or-create a Source Document by title within the demo project."""
	existing = frappe.db.get_value("Source Document", {"title": spec["title"], "project": project}, "name")
	if existing:
		frappe.db.set_value("Source Document", existing, "page_count", spec["page_count"])
		return existing
	source_document = store.create_document(spec["title"], project=project)
	store.set_page_count(source_document, spec["page_count"])
	return source_document


def sections_match(source_document: str, sections: list[Section]) -> bool:
	"""True when the stored tree already equals the spec — lets a re-run skip the rebuild
	(which would otherwise drop and recreate every wiki page)."""
	stored = frappe.get_all(
		"Source Section",
		filters={"source_document": source_document},
		fields=["title", "section_type", "page_start", "page_end", "hierarchy_path", "markdown"],
		order_by="lft asc",
	)
	if len(stored) != len(sections):
		return False
	for row, section in zip(stored, sections, strict=True):
		if (
			row.title != section.title
			or (row.section_type or None) != section.section_type
			or row.page_start != section.page_start
			or row.page_end != section.page_end
			or (row.hierarchy_path or "") != " > ".join(section.hierarchy_path)
			or (row.markdown or "") != section.markdown
		):
			return False
	return True


def prune_stale_documents(project: str) -> list[str]:
	"""Drop demo-project documents no longer in the spec (a retitled document would
	otherwise be left behind as a duplicate), taking their wiki subtree with them."""
	wanted = {spec["title"] for spec in DOCUMENTS}
	stale = [
		row
		for row in frappe.get_all(
			"Source Document", filters={"project": project}, fields=["name", "title", "wiki_root_group"]
		)
		if row.title not in wanted
	]
	for row in stale:
		if row.wiki_root_group and frappe.db.exists("Wiki Document", row.wiki_root_group):
			# NestedSet needs leaves gone first, hence deepest (largest lft) first.
			descendants = get_descendants_of("Wiki Document", row.wiki_root_group, ignore_permissions=True)
			for name in frappe.get_all(
				"Wiki Document", filters={"name": ["in", descendants]}, order_by="lft desc", pluck="name"
			):
				frappe.delete_doc("Wiki Document", name, ignore_permissions=True, force=True)
			frappe.delete_doc("Wiki Document", row.wiki_root_group, ignore_permissions=True, force=True)
		frappe.db.delete("Source Section", {"source_document": row.name})
		frappe.delete_doc("Source Document", row.name, ignore_permissions=True, force=True)
	return [row.title for row in stale]


def seed_demo_corpus() -> dict:
	"""Create (or refresh) the five-document demo corpus and project it into a Wiki Space.

	Idempotent: documents are matched by title within the demo project, the section tree is
	rebuilt only when it drifts from the spec, and wiki generation upserts pages against
	each section's `wiki_document`. Returns the inventory.
	"""
	seed_section_types()
	project = get_or_create_project()
	space = get_or_create_space()
	prune_stale_documents(project)

	for spec in DOCUMENTS:
		source_document = get_or_create_document(spec, project)
		sections = build_sections(spec)
		if not sections_match(source_document, sections):
			store.replace_sections(source_document, sections)
		generate_wiki(source_document, wiki_space=space)

	frappe.db.commit()
	return inventory()


def section_routes() -> list[dict]:
	"""(document, section, section_type, wiki route) for every demo section — the raw
	material for the golden-question expected-source lists."""
	project = frappe.db.get_value("Wikify Project", {"project_name": PROJECT_NAME}, "name")
	documents = frappe.get_all(
		"Source Document", filters={"project": project}, fields=["name", "title"], order_by="title asc"
	)
	title_of = {row.name: row.title for row in documents}
	sections = frappe.get_all(
		"Source Section",
		filters={"source_document": ["in", list(title_of)], "is_group": 0},
		fields=[
			"name",
			"source_document",
			"title",
			"section_type",
			"page_start",
			"page_end",
			"wiki_document",
		],
		order_by="source_document asc, lft asc",
	)
	routes = {
		row.name: row.route
		for row in frappe.get_all(
			"Wiki Document",
			filters={"name": ["in", [s.wiki_document for s in sections if s.wiki_document]]},
			fields=["name", "route"],
		)
	}
	rows = [
		{
			"document": title_of[section.source_document],
			"section": section.title,
			"section_type": section.section_type,
			"pages": f"{section.page_start}-{section.page_end}",
			"route": f"/{routes.get(section.wiki_document, '')}",
		}
		for section in sections
	]
	for row in rows:
		print(f"{row['section_type'] or '-':34} {row['route']}")
	return rows


def inventory() -> dict:
	"""Print and return the corpus counts — the evidence the demo data is really there."""
	project = frappe.db.get_value("Wikify Project", {"project_name": PROJECT_NAME}, "name")
	documents = frappe.get_all(
		"Source Document",
		filters={"project": project} if project else {},
		fields=["name", "title", "page_count", "status", "wiki_space"],
		order_by="title asc",
	)
	sections = frappe.get_all(
		"Source Section",
		filters={"source_document": ["in", [d.name for d in documents]]} if documents else {"name": ""},
		fields=["name", "title", "section_type", "is_group", "include_in_wiki", "wiki_document"],
	)
	by_type: dict[str, int] = {}
	for section in sections:
		key = section.section_type or ("(group)" if section.is_group else "(untyped)")
		by_type[key] = by_type.get(key, 0) + 1

	space_route = frappe.db.get_value("Wiki Space", {"route": SPACE_ROUTE}, "route")
	result = {
		"project": project,
		"documents": len(documents),
		"sections": len(sections),
		"sections_by_type": dict(sorted(by_type.items(), key=lambda item: -item[1])),
		"wiki_documents": len({s.wiki_document for s in sections if s.wiki_document}),
		"included_in_wiki": sum(1 for s in sections if s.include_in_wiki),
		"space_route": f"/{space_route}" if space_route else None,
		"titles": [d.title for d in documents],
		"totals_site_wide": {
			"Source Document": frappe.db.count("Source Document"),
			"Source Section": frappe.db.count("Source Section"),
			"Wiki Document": frappe.db.count("Wiki Document"),
		},
	}
	print(frappe.as_json(result, indent=2))
	return result
