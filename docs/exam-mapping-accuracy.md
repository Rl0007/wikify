# Exam-question → topic mapping: accuracy measurement

**Measured:** 12 August 2026 · **Site:** `wikify.localhost` · **Project:** `PRJ-2026-00002`
**Population:** 190 `Wikify Exam Question` rows across 9 `Wikify Exam Paper` rows, 566 `Question Topic Link` rows.
**Corpus:** one `Source Document` (`86805bos-aps1326-final-slmr-p4` — the ICAI CA-Final Paper 4 SARANSH revision material), 296 `Source Section` rows.

Read-only audit. No mapping was re-run; no row was modified.

---

## Headline

| Slice | n | Correct | Partial | Wrong | **Precision@1 (strict)** | Lenient (correct+partial) |
|---|---|---|---|---|---|---|
| **Overall** | 40 | 17 | 7 | 16 | **42.5%** | 60.0% |
| rank-1 `method=statutory` | 20 | 8 | 4 | 8 | **40.0%** | 60.0% |
| rank-1 `method=retrieval` | 20 | 9 | 3 | 8 | **45.0%** | 60.0% |

**The hypothesis is not supported.** The statutory leg is *not* materially more accurate than the
retrieval leg — it is marginally worse in this sample (40% vs 45% strict, 60% vs 60% lenient). With
20 per arm the difference is indistinguishable from noise (Fisher exact p ≈ 1.0; Wilson 95% CI
0.22–0.61 for statutory, 0.26–0.66 for retrieval). The design in `map.py` weights statutory **2.0×**
retrieval on the stated grounds that it is "evidence rather than similarity". That premise is sound;
the *implementation* does not deliver it, and the 2× weight currently amplifies a leg that is no more
reliable than the one it overrides — see failure modes S-1 through S-4.

Two more numbers worth stating next to the precision figure:

- **57 of 99** retrieval-led rank-1 links sit at or below a fusion score of **0.06**, against a
  sample maximum of 0.17. The retrieval leg is mostly returning its top-k off a flat score
  distribution — "something came back", exactly as `map_project`'s docstring warns, but the UI
  presents it as a topic ranking regardless.
- **25 of 190** questions have a rank-1 topic whose *label* is a mis-promoted document fragment
  (`AMT liability not attracted` ×12, `Interest for defaults in payment of advance tax [Section 234B]`
  ×7, `Applicable Fee for application for APA` ×3, …). **95 of 566** links overall carry such a label.
  `topic_of`'s `MIN_TOPIC_SUBTREE` guard was meant to prevent this and does not.

### On the known bad example

The director-liability question (`qfo0gtj6ip`, May 2023 Q6(a)(iii), refs `section 179, section 156`)
is **no longer under CAPITAL GAINS** — it is now rank-1 under
`Interest for defaults in payment of advance tax [Section 234B]` at a retrieval score of **0.06**.
Both of its refs are dead in the corpus, so the statutory leg stayed silent and pure retrieval noise
won. The specific symptom moved; the defect did not.

---

## Systematic failure modes

Ranked by measured blast radius. Each is evidence-backed against the live corpus.

### S-1 — `provision_pattern` has no right-hand boundary (prefix over-match)

`map.py:152` anchors on the citation word but not on the end of the number, so a short reference
matches every longer section sharing its prefix.

| ref | sections hit | exact occurrences | **false-positive occurrences** | what it actually matched |
|---|---|---|---|---|
| `11` | 56 | **0** | **264** | `SECTION 115JB`, `Section 115BAA`, `Section 115BAB`, `Section 115BAC(2)` |
| `9` | 28 | — | many | `Section 90`, `Section 91`, `Section 92A(1)`, `Section 92D` |
| `80` | 23 | — | many | `section 80A`, `section 80AC`, `section 80CCD` |
| `24` | 5 | 3 | 3 | `u/s 245N(a)`, `u/s 245N(b)(A)` |
| `23` | 3 | 0 | 4 | `u/s 234B`, `u/s 234C` |
| `15` | 2 | 1 | 4 | `section 153`, `section 153B`, `u/s 154` |

Corpus-wide: **46 of 396** parsed ref-instances over-match, and **38 of 116** ref-bearing questions
(33%) carry at least one over-matching ref.

This is the archetype the brief asked about. **S28** (charitable trust, `section 13(3), section 11`)
is filed rank-1 under `AMT liability not attracted` at score **8.00** — the single highest statutory
score in the sample — entirely on ref `11` matching 264 occurrences of `115B*`, with *zero* genuine
`section 11` hits. It is presented to the student as high-confidence examiner evidence.

Note this is the same class of bug the `statutory_hits` docstring claims to have fixed. That fix
added the *left* anchor (blocking bare numbers); the *right* boundary was never added.

### S-2 — the left anchor blocks the corpus's dominant citation format

`provision_pattern` requires the literal word `section`/`sec.`/`u/s` before the number. The corpus's
TDS and TCS chapters cite provisions as **table rows** — `| 192 | Salary | …` — with no such word.
Those refs therefore read as absent.

**45% of parsed ref-instances (178/396) resolve to zero sections**, and **25 questions have every
single ref dead**. The mechanism is visible in **S36** (salary TDS): refs `192` and `194A` are both
DEAD despite `TAX DEDUCTION AT SOURCE` being the obvious chapter and containing both, while the
incidental `115BAC(1A)` scored 35 occurrences across 12 sections. Result: rank-1 =
`DEDUCTIONS FROM GROSS TOTAL INCOME` at **10.05**, and the correct chapter sits at rank 3 with **0.03**.

Two sub-causes are tangled here and should be separated when fixing: sub-section-qualified refs
(`13(3)`, `16(ia)`, `9(1)(vii)`, `143(3)`, `154(7)`) almost never appear verbatim, and unanchored
table citations are unreachable by design.

### S-3 — ubiquitous provisions are scored as evidence

`115BAC` / `115BAA` / `115BAB` appear throughout a SARANSH rate-table document as background context,
not as subject matter. They are scored identically to a dispositive citation.

**S34** is the clean demonstration: the ref that decides the question (`91`, unilateral foreign-tax
relief) hits 3 sections with 3 occurrences; the incidental `115BAC` hits **25 sections with 58
occurrences**. `DOUBLE TAXATION RELIEF › Unilateral Relief [Section 91]` exists in the corpus and was
not surfaced at all. Rank-1 went to `DEDUCTIONS FROM GROSS TOTAL INCOME` at 8.00. Same pattern in
**S21**, where `40(a)(i)` and `40A(3)` correctly matched the PGBP chapter but were outranked by the
breadth of `115BAB`/`112A`/`44AB`.

These provisions need to behave like stopwords — down-weighted by inverse document frequency — rather
than as top-scoring evidence.

### S-4 — references to other statutes are parsed as Income-tax sections

`STATUTORY` (`map.py:47`) extracts any `section <number>` regardless of which Act it belongs to.

**S40** cites `section 43B(h)` plus **sections 15, 16, 23 and 2(n) of the MSMED Act, 2006**. `43B(h)`
is DEAD (sub-section form, per S-2); the MSMED numbers survive and — via S-1 — match `u/s 234B`,
`u/s 234C`, `section 153`, `section 154`. The question is filed rank-1 under
`Interest for defaults in payment of advance tax [Section 234B]`. `PROFITS AND GAINS OF BUSINESS OR
PROFESSION` (which holds `CERTAIN DEDUCTIONS TO BE MADE ONLY ON ACTUAL PAYMENT [SECTION 43B]`) is at
rank 3. A question about MSME payment discipline is telling the student to study advance-tax interest.

### S-5 — `statutory_refs` is written in two incompatible formats; one is unparseable

The extractor emits refs with the citation word (`section 194BA`) on 8 papers, and **bare**
(`115BAC, Chapter XII-A`, `139(1), 143(1)(a), 143(3), 147, 115BAA, 115BAB, 271D, 271E`) on
`EXAM-2026-00009` (November 2024, Paper 4). `STATUTORY` requires the citation word, so bare refs parse
to **nothing**.

**16 of 132** ref-bearing questions parse to zero refs — **14 of them in that one paper**, i.e. half
of it. Those questions silently lose the statutory leg entirely *and* fall to
`coverage_verdict → "unknown"` instead of `covered`/`gap`, so they cannot appear in the gap report
either. **S06** (penalties under 271D/271E) is one: it lands rank-1 under `NON RESIDENT TAXATION`.

### S-6 — `topic_of` rollup lands on the wrong chapter (a labelling defect, not a retrieval one)

The sectioniser produced a mangled tree, and the rollup faithfully climbs it. Two distinct symptoms:

**(a) Real chapters nested under unrelated parents.** `TAXATION OF COMPANIES`,
`MINIMUM ALTERNATE TAX ON COMPANIES [SECTION 115JB]` and `ASSESSMENT OF VARIOUS ENTITIES` are all
children of `DEDUCTIONS FROM GROSS TOTAL INCOME`. Every company-taxation and MAT question is
therefore labelled "Deductions from Gross Total Income" for the student. That is why DEDUCTIONS is the
#2 topic by rank-1 count (27 questions) — it is absorbing traffic it did not earn. **S25** and **S31**
both match a *correct* section (`TAXATION OF COMPANIES`) and display a *wrong* chapter.

**(b) `MIN_TOPIC_SUBTREE` does not filter fragments.** `AMT liability not attracted` carries 15
descendants — it swallowed the entire rates / surcharge / rebate / residential-status block — so it
clears the ≥3 threshold and becomes a first-class heatmap row. Same for
`Applicable Fee for application for APA` (13 descendants, holding the TP penalty and secondary-
adjustment material) and `Interest for defaults … [Section 234B]` (3). Subtree size is not a proxy for
"is a real chapter".

Also visible: the same chapter exists as **three separate roots** (`Assessment of Various Entities`
×2 plus a nested `ASSESSMENT OF VARIOUS ENTITIES`, and `Capital Gains` / `CAPITAL GAINS`).
`score.topic_key` merges them by case-folded title, which rescues the exact-duplicate pairs but not
`SARANSH | TRANSFER PRICING` vs `TRANSFER PRICING`.

### S-7 — no chapter exists, yet three links are always stored

The corpus has no material on assessment procedure, appeals, revision, rectification, search &
seizure, penalties, return filing, charitable trusts or clubbing. Questions on those topics should
surface as gaps; instead each gets three links at the retrieval noise floor. **S01** (writ vs appeal),
**S05** (doctrine of precedence), **S15** (s263 revision), **S35** and **S37** (s154 / s148A) all land
on confident-looking chapter names at scores of 0.03–0.09.

`map_question`'s docstring states an uncovered question is stored with no links and
`mapping_status = "No Match"` — **no question in the project is in that state; all 190 are "Mapped"**.
The gap path is reachable only through `score.matrix`, and only when refs parse (S-5) and fail to
match (S-1/S-2 make spurious matches likely), so in practice it rarely fires.

Note a recurring attractor: `REFERENCE TO TRANSFER PRICING OFFICER [SECTION 92CA]` catches
rectification and reassessment questions (S35, S37) because its text mentions `section 153`/`153B`
time limits.

---

## The sample

Stratified over rank-1 `method` (20 statutory / 20 retrieval — the population splits 91/99, so this is
near-proportional), all 4 `question_kind` values, and all 6 exam years present (2021–2026), drawn with
a fixed seed across method×kind buckets with year-spread ordering.

Verdict is on the **rank-1 `topic_title`** — the chapter label the student actually sees.
**Correct** = the chapter genuinely teaches this question. **Partial** = an adjacent or defensible
chapter, but not the one a student should be sent to first. **Wrong** = a different subject, or no
link should have been made at all.

| # | Question | Year · Kind · Marks | Rank-1 topic (method, score) | Verdict | Reasoning |
|---|---|---|---|---|---|
| S01 | `pbg9249c75` | 2021 · Descriptive · 4 | Interest … [Section 234B] (retrieval, 0.06) | **Wrong** | Writ vs statutory appeal against a 115QA buyback assessment. Nothing to do with advance-tax interest; no appeals chapter exists → S-7. |
| S02 | `ps9ejtt9t2` | 2022 · Descriptive · 2 | ACTION PLAN 13 TP DOCUMENTATION (retrieval, 0.09) | **Wrong** | Static vs ambulatory treaty interpretation. `BASIC PRINCIPLES OF INTERPRETATION OF A TREATY` exists and is at rank 3. |
| S03 | `ps9f2kjjb4` | 2022 · Descriptive · 2 | FUNDAMENTALS OF BEPS (retrieval, 0.09) | *Partial* | Significant economic presence is an Act provision (Expl. 2A to s.9(1)(i)); BEPS Action 1 is adjacent context. NON RESIDENT TAXATION at rank 3 is the right row. |
| S04 | `q6e2pfpf99` | 2023 · Descriptive · 6 | NON RESIDENT TAXATION (retrieval, 0.08) | **Correct** | Offshore/onshore FTS taxability for a Korean company; matched section is the royalty/FTS-for-NR provision. |
| S05 | `q6fifs1bde` | 2023 · Descriptive · 4 | WHO CAN BE AN APPLICANT … ADVANCE RULING (retrieval, 0.03) | **Wrong** | Doctrine of precedence / ratio vs obiter. Noise-floor score, no such chapter → S-7. |
| S06 | `kfqvudbs7r` | 2024 · MCQ · 2 | NON RESIDENT TAXATION (retrieval, 0.06) | **Wrong** | Cash loans and 271D/271E penalties for an Indian company. Bare-format refs parsed to nothing → S-5. |
| S07 | `kfr805p0gs` | 2024 · MCQ · 2 | NON RESIDENT TAXATION (retrieval, 0.06) | **Correct** | NRI capital gains under Chapter XII-A / 115E. Right chapter. |
| S08 | `kfrs1kh5tc` | 2024 · MCQ · 2 | NON RESIDENT TAXATION (retrieval, 0.06) | **Correct** | Same case study, same provision set. |
| S09 | `kfsnft9n8b` | 2024 · MCQ · 2 | NON RESIDENT TAXATION (retrieval, 0.08) | *Partial* | REIT distribution to non-resident unit holders. The chapter that teaches it is the REIT scheme under Assessment of Various Entities (rank 3). |
| S10 | `kfujs6smmr` | 2024 · Descriptive · 3 | PERSON \[SECTION 2(31)] (retrieval, 0.06) | **Wrong** | Online-gaming TDS u/s 194BA. Should be TAX DEDUCTION AT SOURCE; 194BA is absent from the corpus, so this should have been a gap. |
| S11 | `l700u3012p` | 2024 · Numerical · 6 | NON RESIDENT TAXATION (retrieval, 0.09) | **Correct** | POEM/residence of a US company plus GDR dividend and FTS from Government. |
| S12 | `re7vgptci2` | 2025 · MCQ · 2 | TAX DEDUCTION AT SOURCE (retrieval, 0.03) | **Wrong** | Gift to spouse → clubbing of house-property and debenture interest income, plus a JDA. `CLUBBING PROVISIONS` exists as its own row. Noise-floor score. |
| S13 | `re8a0lr3cs` | 2025 · MCQ · 2 | TRANSFER PRICING (retrieval, 0.17) | **Correct** | Resale Price Method as most appropriate method; matched `METHODS FOR COMPUTING ALP [SECTION 92C]`. Highest retrieval score in the sample — and the only single-link row. |
| S14 | `re8bc1kksk` | 2025 · MCQ · 2 | NON RESIDENT TAXATION (retrieval, 0.11) | *Partial* | Business-trust income and rupee-denominated bonds. Non-resident element is real but the REIT scheme (rank 2) is the chapter. |
| S15 | `re8dnsn1h5` | 2025 · Descriptive · 4 | Applicable Fee for application for APA (retrieval, 0.09) | **Wrong** | Limitation for a s.263 revision order, matched to the Rule 10CB repatriation timetable. Fragment label (S-6b) + no chapter (S-7). |
| S16 | `re8oqapns0` | 2025 · MCQ · 2 | NON RESIDENT TAXATION (retrieval, 0.06) | **Correct** | Presumptive taxation of a non-resident cruise operator; `PRESUMPTIVE PROVISIONS APPLICABLE TO NON RESIDENTS` is in that chapter. |
| S17 | `re8rpjh7ki` | 2025 · Case Study · 6 | TRANSFER PRICING (retrieval, 0.12) | **Correct** | Associated enterprise status and options after a TPO adjustment. |
| S18 | `s4s3j25mmn` | 2026 · Numerical · 4 | CAPITAL GAINS (retrieval, 0.09) | **Correct** | Buyback of shares; matched `CAPITAL GAINS ON BUYBACK OF SHARES [SECTION 46A]` exactly. |
| S19 | `s4ssjho34i` | 2026 · MCQ · 2 | TAX DEDUCTION AT SOURCE (retrieval, 0.03) | **Correct** | E-commerce operator TDS u/s 194-O plus 194J. Right chapter — but at the noise floor, so the outcome is not reliably reproducible. |
| S20 | `s4tb1gnmvg` | 2026 · Descriptive · 3 | 21 Other income (OI) (retrieval, 0.03) | **Wrong** | Article 14, independent personal services. The corpus contains `14 Independent personal services` as its own row. Straight miss. |
| S21 | `pbfaqupl16` | 2021 · Numerical · 14 | Assessment of Various Entities (statutory, 2.69) | *Partial* | 14-mark company income computation. Label is defensible; the matched section is `TAXATION OF CO-OPERATIVE SOCIETIES`. PGBP refs (`40(a)(i)`, `40A(3)`) matched but were outranked → S-3. |
| S22 | `pbgg3goe6f` | 2021 · Descriptive · 4 | TAX DEDUCTION AT SOURCE (statutory, 2.03) | **Correct** | 194-O e-commerce TDS. Clean, dispositive ref. |
| S23 | `pbgnvhgamr` | 2021 · Descriptive · 4 | SARANSH \| TRANSFER PRICING (statutory, 1.33) | **Correct** | Thin capitalisation; matched `Limitation of interest deduction [Section 94B]` exactly. Label is a duplicate of the TRANSFER PRICING row → S-6. |
| S24 | `pjh3n4116b` | 2022 · Descriptive · 6 | NON RESIDENT TAXATION (statutory, 2.75) | **Correct** | Agency business connection, s.115A rates, s.91 relief. Chapter right (matched section is the POEM test — off within the chapter). |
| S25 | `ps8nfp2jtf` | 2022 · Numerical · 14 | DEDUCTIONS FROM GROSS TOTAL INCOME (statutory, 3.39) | *Partial* | Matched section is `TAXATION OF COMPANIES` — correct — but rolls up to a chapter label that misdescribes a PGBP-dominated computation (40(a)(ia), 43B, 35AD, 36(1)(va)) → S-6a. |
| S26 | `q6e1cd097t` | 2023 · Numerical · 3 | TDS,TCS AND ADVANCE TAX (statutory, 3.36) | **Correct** | 206C(1H)/194Q on a scrap purchase; matched `TAX COLLECTION AT SOURCE [SECTION 206C]`. |
| S27 | `q6e3agssc2` | 2023 · Numerical · 2 | TAX DEDUCTION AT SOURCE (statutory, 2.03) | **Correct** | 194-IA TDS on immovable property. |
| S28 | `q6e3ebjvtq` | 2023 · Descriptive · 8 | AMT liability not attracted (statutory, **8.00**) | **Wrong** | Charitable trust, s.13(3) benefit to interested persons. Ref `11` matched 264 occurrences of `115B*` and zero of `section 11` → S-1. Highest statutory score in the sample and completely wrong. No trust chapter exists → should be a gap. |
| S29 | `q6efffagro` | 2023 · Case Study · 6 | Assessment of Various Entities (statutory, 2.00) | **Wrong** | ALP determination and TP penalties for an SEZ unit. Matched the 115BAE/115BAD concessional-regime section; TRANSFER PRICING is the chapter. |
| S30 | `qfocpccn2g` | 2023 · Numerical · 3 | Assessment of Various Entities (statutory, 1.33) | **Wrong** | 44BBA presumptive income of a non-resident airline, matched to MAT non-applicability for foreign companies. NON RESIDENT TAXATION is the chapter. |
| S31 | `kftctrumh8` | 2024 · Numerical · 14 | DEDUCTIONS FROM GROSS TOTAL INCOME (statutory, 5.36) | *Partial* | 80-IAB and 80M make the label partly earned, but it arrives via the same mislabelled `TAXATION OF COMPANIES` path as S25, and the question is PGBP-dominated. |
| S32 | `kfugoeamjs` | 2024 · Descriptive · 3 | TDS,TCS AND ADVANCE TAX (statutory, 3.40) | **Correct** | 206C(1G) TCS on an overseas tour package, plus 206CCA. |
| S33 | `l6u4nh487l` | 2024 · Numerical · 14 | PROFITS AND GAINS OF BUSINESS OR PROFESSION (statutory, 5.36) | **Correct** | 14-mark PGBP computation; matched the s.40 inadmissible-deductions section. The leg working as designed. |
| S34 | `l70kd1ia7m` | 2024 · Numerical · 6 | DEDUCTIONS FROM GROSS TOTAL INCOME (statutory, 8.00) | **Wrong** | Foreign-income relief where no DTAA exists — squarely `Unilateral Relief [Section 91]`, which is in the corpus and was not surfaced. Ubiquitous `115BAC` drowned the dispositive `91` → S-3. |
| S35 | `l71iaudl2a` | 2024 · Descriptive · 4 | TRANSFER PRICING (statutory, 1.37) | **Wrong** | s.154 rectification time limit, matched to `REFERENCE TO TPO [SECTION 92CA]` on its s.153 mentions. No rectification chapter → should be a gap. |
| S36 | `re8h3gvm48` | 2025 · Numerical · 3 | DEDUCTIONS FROM GROSS TOTAL INCOME (statutory, **10.05**) | **Wrong** | Employer TDS on salary u/s 192. `192` and `194A` both read as absent because the TDS chapter cites them in table rows → S-2. Correct chapter at rank 3, score 0.03. |
| S37 | `re9pg1abef` | 2025 · Descriptive · 4 | TRANSFER PRICING (statutory, 1.36) | **Wrong** | s.154 vs s.148A reassessment. Same TPO attractor as S35; three of four refs dead. |
| S38 | `s4s4nqvgfh` | 2026 · MCQ · 2 | NON RESIDENT TAXATION (statutory, 0.69) | **Correct** | 44BBC presumptive cruise income for a non-resident. Right chapter — note the score is far below the wrong answers in S28/S34/S36. |
| S39 | `s4ssivqhad` | 2026 · Numerical · 6 | DEDUCTIONS FROM GROSS TOTAL INCOME (statutory, 8.00) | *Partial* | 80QQB and 80TTB are genuine Chapter VI-A items, but the question turns on s.91 foreign-tax relief. Same shape as S34, partly rescued by the deduction refs. |
| S40 | `s4t86s365o` | 2026 · Descriptive · 6 | Interest … [Section 234B] (statutory, 2.67) | **Wrong** | 43B(h) MSME payment discipline and Form 3CD clause 22. MSMED Act sections 15/16/23 parsed as Income-tax sections and over-matched onto 234B/234C/153/154 → S-4 + S-1. PGBP at rank 3. |

---

## What to fix, in priority order

1. **Anchor `provision_pattern` on the right.** Append a negative lookahead so `11` cannot match
   `115BAA` — roughly `+ r"(?![0-9A-Za-z(])"` after the escaped ref, with care to still allow a
   following `(` when the ref itself is sub-section-qualified. Cheapest fix, largest single effect:
   it removes 264 phantom occurrences on ref `11` alone and directly repairs the highest-scoring
   wrong answer in the sample (S28).
   *Blast radius: 38 of 116 ref-bearing questions.*

2. **Make the citation anchor cover table-row citations.** Accept a provision at the start of a
   markdown table cell (`| 192 |`) or after a pipe, not only after the word `section`. Without this,
   the entire TDS/TCS chapter is invisible to the statutory leg, which is why a salary-TDS question
   scores 10.05 on the wrong chapter (S36). Pair it with a fallback that matches a sub-section ref by
   its parent section (`16(ia)` → `16`) *after* fix 1 makes that safe.
   *Blast radius: 178 of 396 ref-instances currently dead; 25 questions have every ref dead.*

3. **Normalise `statutory_refs` at write time and reject the bare format.** Either have extraction
   always emit `section <n>`, or make `STATUTORY` accept a bare comma-separated list. Right now half
   of `EXAM-2026-00009` silently loses its statutory leg *and* its gap verdict.
   *Blast radius: 16 questions, 14 of them in one paper.*

4. **Down-weight ubiquitous provisions.** Score each ref by inverse document frequency across the
   corpus, so a provision appearing in 25 of 296 sections (`115BAC`) cannot outweigh one appearing in
   3 (`91`). Cap or normalise the per-question total the same way. Fixes S34, S39, and the S21/S25/S31
   family.

5. **Reject references to other statutes.** `STATUTORY` should not harvest `section 15 of the MSMED
   Act`. Either require Income-tax context, or blacklist a following `of the … Act` clause. Fixes S40
   and prevents a whole class of confident nonsense.

6. **Replace `MIN_TOPIC_SUBTREE` with a real chapter test.** Subtree size does not distinguish a
   chapter from a fragment that swallowed one — `AMT liability not attracted` has 15 descendants.
   Prefer matching level-1 titles against the document's own `INDEX` section (which the corpus has),
   or against a curated chapter list. This is the single biggest *presentation* defect: 25 rank-1
   labels and 95 links currently read as gibberish to a student, independently of whether the
   underlying section was right.

7. **Fix the rollup's parent choice for mis-nested chapters.** `TAXATION OF COMPANIES` and
   `MINIMUM ALTERNATE TAX` must not roll up to `DEDUCTIONS FROM GROSS TOTAL INCOME`. Fix 6 largely
   subsumes this if the chapter list is authoritative; otherwise `topic_of` needs to stop climbing
   when the candidate ancestor's title is itself a known chapter.

8. **Make "no link" reachable.** All 190 questions are `Mapped`; none is `No Match`, despite the
   corpus holding no material on appeals, revision, rectification, penalties, clubbing or charitable
   trusts. Introduce a floor below which a retrieval-only link is not stored — the sample's wrong
   retrieval answers cluster at 0.03–0.09 while the correct ones reach 0.08–0.17, so a floor will cost
   some true positives and needs tuning against this sample, not guessed. Alternatively keep storing
   the links but stop presenting rank-1 as a topic when it is retrieval-only and below the floor.

9. **Re-measure after 1–5.** These numbers are the baseline. Re-running this audit on the same
   40-question sample after each fix is the only way to know whether the 2.0× statutory weight is
   earning its keep; on today's evidence it is not.

---

### Method notes and limits

- Judgement is mine, one pass, against the question text plus its cited `statutory_refs`, with the
  corpus section tree and matched-section markdown in hand. No second rater, so the strict/lenient
  split is offered precisely because the boundary between *Partial* and *Wrong* is where a second
  rater would most likely disagree.
- n=40 gives roughly ±15pp at 95% confidence on the overall figure, and the two per-method arms
  (n=20 each) can only rule out a large effect, not a small one. What the sample does establish is
  that the statutory leg is not *materially better* — a 2× weight is not supported by this evidence.
- Precision is scored on the rank-1 **topic label**, since that is what drives the heatmap row and the
  "study this first" claim. Several rows (S23, S25, S31) matched a correct *section* under a wrong
  *chapter*; scoring on sections would look better and would misdescribe what the student sees.
- Recall was not measured. A question whose correct chapter exists but never appears in any of its
  three links (S20, S34) is counted only as a rank-1 miss.
- Temporary read-only module `wikify/temp_audit_dump.py` was used to extract the data and replay the
  ref matcher; it has been deleted. Nothing under `wikify/exam/`, `wikify/transfer/`, `wikify/api/`,
  `wikify/tests/` or the frontend was read into the mapping path or modified.
