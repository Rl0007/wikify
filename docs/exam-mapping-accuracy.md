# Exam-question → topic mapping: accuracy measurement

**Baseline measured:** 12 August 2026 · **Re-measured after four fixes:** 12 August 2026
**Site:** `wikify.localhost` · **Project:** `PRJ-2026-00002`
**Population:** 190 `Wikify Exam Question` rows across 9 `Wikify Exam Paper` rows.
**Corpus:** one `Source Document` (`86805bos-aps1326-final-slmr-p4` — the ICAI CA-Final Paper 4
SARANSH revision material), 296 `Source Section` rows.

Both passes are read-only audits. No mapping was re-run in either; no row was modified.

---

# Part 2 — after the four fixes

## Headline

| Slice | n | Correct | Partial | Wrong | **Precision@1 (strict)** | Lenient |
|---|---|---|---|---|---|---|
| **Overall — baseline** | 40 | 17 | 7 | 16 | **42.5%** | 60.0% |
| **Overall — now** | 40 | 19 | 3 | 18 | **47.5%** | 55.0% |
| baseline arm: rank-1 was `statutory` | 20 | 8 → **13** | 4 → 1 | 8 → 6 | 40.0% → **65.0%** | 60.0% → 70.0% |
| baseline arm: rank-1 was `retrieval` | 20 | 9 → **6** | 3 → 2 | 8 → 12 | 45.0% → **30.0%** | 60.0% → 40.0% |

Split by the rank-1 method **as it stands today** (the method flipped on 9 of the 40 rows, all
retrieval → statutory):

| Slice | n | Correct | Partial | Wrong | **Precision@1 (strict)** | Lenient |
|---|---|---|---|---|---|---|
| rank-1 `method=statutory` **now** | 29 | 14 | 1 | 14 | **48.3%** | 51.7% |
| rank-1 `method=retrieval` **now** | 11 | 5 | 2 | 4 | **45.5%** | 63.6% |

### Precision did not improve.

Strict precision moved 42.5% → 47.5%. Lenient precision moved 60.0% → 55.0%. Both movements are
smaller than the sampling error on n=40 (±15pp), and they point in opposite directions.

The paired row-by-row movement is the number that matters, and it is brutal:

| Movement | Rows | Which |
|---|---|---|
| Improved (verdict got better) | **6** | S21, S25, S29, S31, S36, S40 |
| Regressed (verdict got worse) | **5** | S04, S07, S08, S09, S24 |
| Unchanged **Correct** | 13 | S11, S13, S16, S17, S18, S19, S22, S23, S26, S27, S32, S33, S38 |
| Unchanged *Partial* | 3 | S03, S14, S39 |
| Unchanged **Wrong** | 13 | S01, S02, S05, S06, S10, S12, S15, S20, S28, S30, S34, S35, S37 |

**Net: +1 question in forty.** McNemar on 6 discordant-up vs 5 discordant-down gives p = 1.0. On this
evidence the four fixes did not move precision; they moved *which* questions are wrong.

And the split above says the errors moved in a specific, diagnosable direction: the statutory leg got
genuinely better in isolation (40% → 65% on the rows it already owned) while **spreading onto 9 rows
that retrieval had been handling, and getting 8 of those 9 wrong**. The statutory leg's own accuracy
improved; its *coverage* grew faster than its accuracy, so the system as a whole stood still.

Two consequences worth stating next to the precision figure:

- Population-wide, rank-1 `method` moved from 91 statutory / 99 retrieval to **116 statutory / 74
  retrieval**. 25 more questions are now decided by the statutory leg.
- On the current split, statutory is **48.3%** and retrieval **45.5%**. The **2.0× weight on the
  statutory leg is still not earned** — the same conclusion the baseline reached, now with the
  statutory leg overriding retrieval on 25 more questions than before.

### The known bad example

The director-liability question (`qfo0gtj6ip`, refs `section 179, section 156`) is **unchanged**:
both refs are still dead, the statutory leg is still silent, and it still lands rank-1 under
`Interest for defaults in payment of advance tax [Section 234B]` at a retrieval score of 0.064.

---

## Sample integrity — 4 of the 40 questions no longer exist

The 2021 paper (`EXAM-2026-00002`) was **re-extracted at 07:54 today**, after the baseline audit was
written. All 16 of its questions were re-created with new hash names. The four baseline rows drawn
from it — `pbg9249c75` (S01), `pbfaqupl16` (S21), `pbgg3goe6f` (S22), `pbgnvhgamr` (S23) — **do not
exist in the data any more**.

I did not substitute different questions. For each of the four, exactly one row in the re-extracted
2021 paper carries the same question text and the same cited refs, so these are the *same questions
under new row names*, not replacements. They are judged as such and flagged in the table:

| Baseline row | Successor | Evidence of identity |
|---|---|---|
| S01 `pbg9249c75` | `vdhrb5oq7j` | Same text (115QA buyback, writ vs appeal); refs `143(2), 246A, 115QA` |
| S21 `pbfaqupl16` | `vdfqpcrfg8` | Same 14-mark ABC Ltd. computation; refs `40(a)(i), 40A(3), 115BAB, 112A, 44AB` |
| S22 `pbgg3goe6f` | `vdgtr907u0` | Same text (Mr. Z / ABC.com e-commerce); ref `194-O` |
| S23 `pbgnvhgamr` | `vdhveafpbc` | Same text (thin capitalisation); ref `94B` |

Population size is unchanged at 190, so the re-extraction replaced rather than added. The comparison
is like-for-like on all 40.

---

## Which failure modes are actually resolved

The baseline numbering (S-1…S-7, defined in Part 1 below) differs from the numbering used in the fix
brief. Mapping, to avoid confusion:

| Fix as described in the brief | Baseline failure mode it targets |
|---|---|
| "S-1 right anchor" | **S-1** prefix over-match |
| "S-2 table-cell citations + parent fallback" | **S-2** left anchor blocks table citations |
| "S-3 bare comma-separated format" | **S-5** unparseable second format |
| "S-4 inverse document frequency" | **S-3** ubiquitous provisions scored as evidence |
| "S-5 other statutes" | **S-4** other statutes parsed as Income-tax sections |

| Baseline mode | Status | Evidence |
|---|---|---|
| **S-1** prefix over-match | ✅ **Resolved** | Ref `11`: 264 phantom occurrences → **1 section, 1 occurrence**. S28's 8.00 junk score collapsed to 0.99 and the `AMT liability not attracted` label is gone from it. Corpus-wide over-matching is eliminated. *Cost: unmasked N-1.* |
| **S-2** table citations unreachable | ⚠️ **Partly resolved** | Dead ref-instances 45% → **21.4%** (95/443); questions with every ref dead 25 → **15**. `192` is now live (2 sections) and S36 — the flagship case — is repaired. But `194A`, `195`, `37`, `271D`, `271E`, `147`, `263`, `142(1)`, `115QA`, `246A`, `264`, `281B`, `115TD`, `148A`, `179`, `156`, `80TTB`, `35CCA`, `80GGB`, `73A`, `270A`, `92CE` are all still dead. *Cost: introduced N-2 and N-5.* |
| **S-3** ubiquitous provisions | ❌ **Not resolved** | S34 — the case the IDF fix was built for — is **unchanged and still wrong**. `115BAC` (25 sections, w=0.307) still outranks `91` (3 sections, w=0.721), because the score is **summed across matching sections**: total contribution is `n / log(1+n)`, which is *monotonically increasing in n*. 115BAC can contribute 25 × 0.307 = **7.68**; section 91 can contribute 3 × 0.721 = **2.16**. IDF slowed breadth's advantage; it did not reverse it. *Cost: introduced N-3.* |
| **S-4** other statutes | ❌ **Not resolved — replaced by a worse bug** | The negative lookahead does not reject the match; it forces the regex to **backtrack to a shorter number**. See N-4. |
| **S-5** bare comma format | ✅ **Resolved as parsing, ❌ made accuracy worse** | Questions whose raw refs parse to zero: 16 → **1**. But handing the statutory leg to `EXAM-2026-00009` produced four of the five regressions in the sample (S06, S07, S08, S09). The parse is correct; the leg it feeds is not good enough to use it. |
| **S-6** rollup lands on fragments | ❌ **Not addressed; the junk redistributed** | `AMT liability not attracted` fell 12 → 6 rank-1 rows, but `Interest for defaults … [Section 234B]` **rose 7 → 12** — it is now the 6th-largest heatmap row in the corpus. `Applicable Fee for application for APA` persists. New fragment labels appeared at rank 1: `PPP = Preceding Previous Year` (S04), `Additional points:`, `Deductions in Respect of Certain Payments`, `Time limit for exercising the option to shift out of the default tax regime`. Duplicate roots persist (`TDS, TCS AND ADVANCE TAX` vs `TDS,TCS AND ADVANCE TAX`; `SARANSH \| TRANSFER PRICING` vs `TRANSFER PRICING`). |
| **S-7** "no link" unreachable | ❌ **Not addressed** | All 190 questions are still `Mapped`; none is `No Match`. S01, S05, S15, S28, S35, S37 still receive confident links to chapters the corpus does not contain — and S01, S15 and S28 now receive them *from the statutory leg at scores of 0.99–2.94*, which reads as examiner evidence rather than as retrieval noise. |

So of the five modes the fixes targeted: **two resolved (S-1, S-5-as-parsing), one partly (S-2), two
not (S-3, S-4)** — and the two untargeted modes (S-6, S-7) are both worse in presentation than they
were.

---

## New failure modes the fixes introduced

### N-1 — the right anchor unmasked the hyphenated-section parser bug

`STATUTORY`'s number pattern (`[0-9]+[A-Z]{0,4}…`) does not cross a hyphen, so ICAI's own spelling of
a whole family of provisions truncates:

| Raw ref | Parsed as |
|---|---|
| `section 194-O` | `194` |
| `section 194-IA` | `194` |
| `section 80-IA` | `80` |
| `section 80-IA(10)` | `80` |

This bug is **pre-existing**, but the baseline hid it: with no right boundary, ref `194` over-matched
`section 194J`, `section 194C`, `section 194Q` and recovered the right chapter by accident. Measured
directly:

| ref `194` matches | Baseline pattern (left anchor only) | Current pattern |
|---|---|---|
| `TAX DEDUCTION AT SOURCE` | **3 occurrences** | **0** |
| `SPECIAL PROVISIONS … [SECTION 115A]` | 2 | 0 |
| `TDS,TCS AND ADVANCE TAX` | 1 | 1 |
| `FUNDAMENTALS OF BEPS` | 1 | 1 |

The corpus writes it as `| **194-O** Sale consideration or …` — a **bolded** table cell, which the new
`\|\s*194\s*\|` table anchor cannot reach either. Result: **S22 fell 2.03 → 0.66 and S27 fell 2.03 →
0.66**, both now resting on a *TCS* section for a *TDS* question, with the precise chapter demoted to
rank 3 at the noise floor. Both still score as Correct on the chapter label, so this costs no
precision points in this sample — but it has hollowed out the evidence underneath two of them.

### N-2 — the parent-section fallback is now a major matching path, and it over-generalises

**86 of 443 ref-instances (19.4%)** now resolve *only* via `parent_provision`. That is not a rare
rescue; it is one match in five. And the parent of a sub-section is frequently a far broader or
entirely unrelated target:

| Ref | Falls back to | Sections hit | What it actually matches |
|---|---|---|---|
| `143(1)`, `143(1)(a)`, `143(2)`, `143(3)` | `143` | **1** | *Only* `Interest for defaults in payment of advance tax [Section 234B]` (3 occ) |
| `10(1)`, `10(37)`, `10(50)` | `10` | **20** | `SECTION 56(2)(x)`, `WITHHOLDING TAX … NON RESIDENTS`, `Computation of book profit` |
| `139(5)` | `139` | **19** | `Section 40(a)`, `Rule 10CB(1)` repatriation timetable |
| `2(24)(xviii)` | `2` | **10** | `TYPE OF CAPITAL ASSET BASED ON PERIOD OF HOLDING` |
| `112(1)(c)(iii)` | `112` | **5** | `SCHEME FOR TAXATION OF REITs` |
| `9(1)(v)`, `9(1)(vii)` | `9` | **9** | `Nested List Representation`, `LIST OF SPECIFIED BUSINESS` |
| `154(7)` | `154` | 1 | `REFERENCE TO TRANSFER PRICING OFFICER [SECTION 92CA]` |

The `143` row is the single most damaging line in this audit. Assessment-procedure questions are
common, `143(x)` is the standard citation, and the *only* place the corpus writes "section 143" is
inside the advance-tax-interest section. **Every question citing 143(1)/(2)/(3) is now confidently
filed under advance-tax interest** — which is precisely why the `Interest … [Section 234B]` heatmap
row grew from 7 to 12 rank-1 questions. S01, S06 and S15 are all this bug.

### N-3 — IDF inverts confidence at n=1

`weight = 1 / log(1 + len(matches))` peaks at **1.443 when a ref matches exactly one section** — the
maximum weight the system can assign. Measured across the corpus:

| Sections matched | IDF weight | Example refs |
|---|---|---|
| 1 | **1.443** | `11`, `43B`, `154`, `153`, `234B`, `143` (via fallback) |
| 2 | 0.910 | `192`, `16`, `44BBA`, `94B`, `206C` |
| 3 | 0.721 | `91`, `24` |
| 25 | 0.307 | `115BAC` |

Combined with N-2 this is the core new defect: **a single spurious parent-fallback hit is now the
highest-confidence evidence the system can produce.** `143(3)` → `143` → one section → weight 1.443 →
S15 lands on advance-tax interest at 2.912 *as statutory evidence*, where the baseline at least
labelled the same wrong answer as retrieval noise at 0.09. The confident-wrong answers did not go
away; they changed which chapter they point at and acquired a better-looking provenance.

### N-4 — the other-statute lookahead backtracks into a truncated number or a year

`(?!\s+of\s+the\s+(?!Income)\w+)` does not reject the match. When it fails, the regex backtracks to a
shorter digit run and matches *that*:

| Raw text | Parsed as | Should be |
|---|---|---|
| `Section 15 of the MSMED Act, 2006` | `1` | *(rejected)* |
| `section 16 of the MSMED Act` | `1` | *(rejected)* |
| `section 2(n) of the MSMED Act, 2006` | `2` | *(rejected)* |
| `section 23 of MSMED Act, 2006` | **`23`** | *(rejected — no "the", so the lookahead never fires)* |
| `section 8 of the Companies Act, 2013` | **`2013`** | *(rejected)* |
| `Clause (7) … Chartered Accountants Act, 1949` | **`1949`** | *(rejected)* |
| `section 15 of the Income-tax Act` | `15` | `15` ✅ |

**12 questions** now carry a suspiciously short numeric ref (`1`, `2`, `4`, `5`, `9`, `11`, `12`,
`15`, `17`, `23`, `24`). S40's rank-1 *did* become correct — but for an unrelated reason (`43B(h)` →
`43B` via the parent fallback), and the leaked `1` and `2` put `CAPITAL GAINS` at rank 2 with 1.248
against the right answer's 1.654. The fix as written turns "harvests the wrong statute's section"
into "harvests a truncated number or a calendar year", which is strictly harder to spot.

### N-5 — the table-cell anchor matches ordinary numeric table data

`\|\s*N\s*\|` matches any table cell containing just that number — including serial-number columns:

| ref | sections matched by table cell only | by citation word only |
|---|---|---|
| `1` | **4** | 0 |
| `2` | **4** | 6 |
| `3` | 2 | 0 |
| `4` | 1 | 0 |
| `5` | 1 | 0 |

The blast radius is small on its own, but it is exactly what gives N-4's truncated `1` and `2`
somewhere to land — the two bugs compose into S40's rank-2 `CAPITAL GAINS`.

---

## Updated fix list, in priority order

The ordering is by measured cost, and the first three are all repairs *to the fixes just landed*.

1. **Suppress the parent-section fallback when the parent's match set does not contain the
   sub-section's own subject.** This is the largest single defect in the current code — 19.4% of all
   ref-instances, and the sole cause of the `Interest … [Section 234B]` row growing to 12 questions.
   Minimum viable guard: do not fall back when the parent is a bare 1–3 digit number *and* the parent
   resolves to a section whose title does not contain the parent number. Better: only fall back when
   the parent match set is small **and** the matched section's title cites the parent explicitly
   (`[SECTION 43B]`) — `43B(h)` → `43B` is the case the fallback was written for and it passes that
   test; `143(3)` → the 234B interest section does not.
   *Blast radius: 86 ref-instances; S01, S06, S15, S24 in this sample; 12 rank-1 rows population-wide.*

2. **Stop IDF peaking at n=1.** `1/log(1+n)` awards maximum confidence to the thinnest possible
   evidence. Either floor the denominator (`1/log(2+n)`, capping the weight at ~0.91) or — better —
   fix the actual complaint from S-3 by making the *per-ref total* independent of breadth: normalise
   each ref's contribution to sum to 1 across the sections it matches, then weight by IDF. As written
   the total is `n/log(1+n)`, which still grows with n, so **S-3 is not fixed and S34 is still wrong**.
   Verify against S34 (must surface `Unilateral Relief [Section 91]`) and S15 (must not surface 234B).

3. **Rewrite the other-statute rejection so it rejects instead of backtracking.** Match the whole
   citation first (`section\s+(\d+…)(?:\([^)]+\))*`), *then* discard the match in a second pass if it
   is followed by `of (the )?<non-Income> Act`. Also handle the missing-`the` form (`of MSMED Act`),
   and reject 4-digit refs outright — no Income-tax section is numbered 1949 or 2013.
   *Blast radius: 12 questions carrying truncated refs; 3 carrying another statute's citation.*

4. **Parse hyphenated section numbers.** `194-O`, `194-IA`, `80-IA` are how ICAI writes them and how
   the corpus prints them. Extend the number pattern to `[0-9]+[A-Z]{0,4}(?:-[A-Z]{1,3})?…` and let
   `provision_pattern` match the bolded / decorated table-cell form (`| **194-O** …`). This restores
   the evidence under S22, S27 and S19, which currently rest on a TCS section by accident.

5. **Recover the still-dead references.** 95 ref-instances (21.4%) and 15 questions with every ref
   dead. The residue is no longer table-format — it is provisions the corpus genuinely lacks
   (`271D`, `263`, `148A`, `179`, `156`, `246A`, `264`, `281B`) mixed with ones it has under a form
   the matcher misses (`194A`, `195`, `37`). Separate those two populations before writing any more
   regex; the first group is fix 7's problem, not the matcher's.

6. **Reject a rank-1 statutory answer whose entire support is one section with ≤3 occurrences.** This
   is the cheap cross-cutting guard for N-2 + N-3 while 1 and 2 are being done properly. Every
   confidently-wrong statutory rank-1 in this sample (S01 2.94, S06 5.77, S15 2.91, S35 3.88, S37
   3.88) rests on exactly this shape.

7. **Make "no link" reachable** *(baseline item 8, unchanged and now more urgent)*. All 190 questions
   are still `Mapped`. The fixes made this worse, not better: six questions on topics the corpus does
   not cover now carry *statutory-method* rank-1 links at scores of 0.99–5.77, so the UI presents
   them as examiner evidence rather than as similarity noise.

8. **Replace `MIN_TOPIC_SUBTREE` with a real chapter test** *(baseline item 6, unchanged)*. Still the
   biggest presentation defect. The junk labels redistributed rather than left: `AMT liability not
   attracted` 12 → 6, `Interest … [Section 234B]` 7 → 12, plus new arrivals `PPP = Preceding Previous
   Year`, `Additional points:` and `Time limit for exercising the option to shift out of the default
   tax regime` at rank 1.

9. **Fix the rollup's parent choice for mis-nested chapters** *(baseline item 7, unchanged)*.
   `TAXATION OF COMPANIES` still rolls up to `DEDUCTIONS FROM GROSS TOTAL INCOME`, which is why S07,
   S08 and S09 regressed: the statutory leg found a *correct* section and the rollup relabelled it.
   Also merge the duplicate roots (`TDS, TCS AND ADVANCE TAX` / `TDS,TCS AND ADVANCE TAX`).

10. **Do not enable the bare-format parser until the statutory leg is above the retrieval leg.** The
    parse itself is correct and should stay in the code, but on this sample it converted four
    retrieval-correct questions into statutory-wrong ones (S06–S09). Gate it behind the same quality
    bar as the rest of the leg, and re-check it after fixes 1–4.

11. **Re-measure on this same 40-question sample after each of 1–4.** Two of the four fixes just
    landed did not do what they were built to do, and one made things worse in a way that was only
    visible on a paired re-judge. The population-level indicators (dead refs, evidence_coverage) all
    moved in the right direction while precision stood still — **do not trust them as a proxy again**.

---

## The sample, re-judged

Same 40 questions, same rubric. Verdict is on the **rank-1 `topic_title`** — the chapter label the
student actually sees. **Correct** = the chapter genuinely teaches this question. **Partial** = an
adjacent or defensible chapter, but not the one a student should be sent to first. **Wrong** = a
different subject, or no link should have been made at all.

† = judged on the re-extracted successor row (see *Sample integrity* above).

| # | Question | Baseline rank-1 (method, score) | Base | Now rank-1 (method, score) | **Now** | Reasoning |
|---|---|---|---|---|---|---|
| S01† | `vdhrb5oq7j` | Interest … [234B] (retr, 0.06) | **W** | Interest … [234B] (**stat**, 2.94) | **Wrong** | Writ vs statutory appeal against a 115QA buyback assessment. Same wrong answer, now dressed as evidence: `115QA` and `246A` are dead, `143(2)` → parent `143` → the *only* section citing "143" is the 234B interest one, at max IDF weight. N-2 + N-3. |
| S02 | `ps9ejtt9t2` | ACTION PLAN 13 TP DOC (retr, 0.09) | **W** | ACTION PLAN 13 TP DOC (retr, 0.086) | **Wrong** | Static vs ambulatory treaty interpretation. No refs; retrieval unchanged. `BASIC PRINCIPLES OF INTERPRETATION OF A TREATY` still at rank 3. |
| S03 | `ps9f2kjjb4` | FUNDAMENTALS OF BEPS (retr, 0.09) | *P* | FUNDAMENTALS OF BEPS (retr, 0.093) | *Partial* | Significant economic presence. Unchanged — no refs, so no fix touched it. |
| S04 | `q6e2pfpf99` | NON RESIDENT TAXATION (retr, 0.08) | **C** | `PPP = Preceding Previous Year` (**stat**, 1.77) | **Wrong** | **Regression.** Offshore/onshore FTS for a Korean company. `9(1)(vii)`/`9(1)(v)` → parent `9` → 9 sections, top hit `Nested List Representation`, which rolls up to the fragment label `PPP = Preceding Previous Year`. The statutory leg took a question retrieval had right and filed it under gibberish. N-2 + S-6. |
| S05 | `q6fifs1bde` | WHO CAN BE AN APPLICANT … (retr, 0.03) | **W** | WHO CAN BE AN APPLICANT … (retr, 0.032) | **Wrong** | Doctrine of precedence, ratio vs obiter. No refs, noise floor, no such chapter. S-7 untouched. |
| S06 | `kfqvudbs7r` | NON RESIDENT TAXATION (retr, 0.06) | **W** | Interest … [234B] (**stat**, **5.77**) | **Wrong** | **The bare-format fix backfired.** All 8 refs now parse (S-5 ✅) but `271D`, `271E`, `147` are dead, `115BAA`/`115BAB` are background noise, and `143(1)(a)` + `143(3)` both fall back to `143` → 234B at 1.443 each. A cash-loan penalty question is now filed under advance-tax interest at the third-highest statutory score in the sample. N-2 + N-3. |
| S07 | `kfr805p0gs` | NON RESIDENT TAXATION (retr, 0.06) | **C** | DEDUCTIONS FROM GTI (**stat**, 1.87) | **Wrong** | **Regression.** NRI capital gains under Chapter XII-A / 115E. `115E` is dead; the bare-format fix handed the leg to `115BAC` (25 sections) and `115A`, and the rollup relabelled the matched `TAXATION OF COMPANIES` as Deductions. S-3 unfixed + S-6a. |
| S08 | `kfrs1kh5tc` | NON RESIDENT TAXATION (retr, 0.06) | **C** | DEDUCTIONS FROM GTI (**stat**, 2.48) | **Wrong** | **Regression.** Same case study. Sole parsed ref is `115BAC` — the textbook ubiquitous provision. IDF gave it 0.307 but it still matches 25 sections, so it wins outright. Direct proof S-3 is not fixed. |
| S09 | `kfsnft9n8b` | NON RESIDENT TAXATION (retr, 0.08) | *P* | DEDUCTIONS FROM GTI (**stat**, 1.18) | **Wrong** | **Regression.** REIT distribution to non-resident unit holders. Sole ref `115BAA` (16 sections). The REIT chapter is at rank 2/3 under `Assessment of Various Entities` — it was rank 3 at baseline and is still not rank 1. |
| S10 | `kfujs6smmr` | PERSON [SECTION 2(31)] (retr, 0.06) | **W** | NON RESIDENT TAXATION (**stat**, 0.638) | **Wrong** | Online-gaming TDS u/s 194BA. **Near-miss improvement:** the table-cell fix made `194BA` live (2 sections), and `TAX DEDUCTION AT SOURCE` is now rank 2 at **0.637** — losing by 0.001 to the non-resident withholding section. Right evidence, wrong tie-break. |
| S11 | `l700u3012p` | NON RESIDENT TAXATION (retr, 0.09) | **C** | NON RESIDENT TAXATION (retr, 0.091) | **Correct** | POEM/residence of a US company. No refs; unchanged. |
| S12 | `re7vgptci2` | TAX DEDUCTION AT SOURCE (retr, 0.03) | **W** | TAX DEDUCTION AT SOURCE (retr, 0.030) | **Wrong** | Clubbing of spouse's income. `CLUBBING PROVISIONS` exists as its own row and is still not surfaced. Noise floor, unchanged. |
| S13 | `re8a0lr3cs` | TRANSFER PRICING (retr, 0.17) | **C** | TRANSFER PRICING (retr, 0.170) | **Correct** | Resale Price Method. Highest retrieval score in the sample and still the only single-link row. Unchanged. |
| S14 | `re8bc1kksk` | NON RESIDENT TAXATION (retr, 0.11) | *P* | NON RESIDENT TAXATION (retr, 0.112) | *Partial* | Business-trust income and rupee-denominated bonds. REIT scheme still at rank 2. Unchanged. |
| S15 | `re8dnsn1h5` | Applicable Fee for APA (retr, 0.09) | **W** | Interest … [234B] (**stat**, 2.91) | **Wrong** | Limitation for a s.263 revision order. `263(2)` and `142(1)` dead; `143(3)` → `143` → 234B at 1.443. The wrong answer changed chapters and gained a statutory label. Textbook N-2 + N-3, and S-7 still means this should be a gap. |
| S16 | `re8oqapns0` | NON RESIDENT TAXATION (retr, 0.06) | **C** | NON RESIDENT TAXATION (retr, 0.060) | **Correct** | Presumptive taxation of a non-resident cruise operator. Unchanged. |
| S17 | `re8rpjh7ki` | TRANSFER PRICING (retr, 0.12) | **C** | TRANSFER PRICING (**stat**, 1.08) | **Correct** | Associated-enterprise status after a TPO adjustment. **Improved in kind:** `92A(2)` → parent `92A` → `TRANSFER PRICING`, so the same correct answer is now evidence-backed rather than a similarity guess. The parent fallback working as intended. |
| S18 | `s4s3j25mmn` | CAPITAL GAINS (retr, 0.09) | **C** | CAPITAL GAINS (retr, 0.092) | **Correct** | Buyback of shares, matched `[SECTION 46A]`. Unchanged. |
| S19 | `s4ssjho34i` | TAX DEDUCTION AT SOURCE (retr, 0.03) | **C** | TAX DEDUCTION AT SOURCE (retr, 0.031) | **Correct** | 194-O / 194J e-commerce TDS. Still correct, still at the noise floor — and note the statutory leg *should* be carrying this one and cannot, because `194-O` truncates to `194` (N-1). |
| S20 | `s4tb1gnmvg` | 21 Other income (OI) (retr, 0.03) | **W** | 21 Other income (OI) (retr, 0.033) | **Wrong** | Article 14, independent personal services. `Article 14` parses to no ref at all; `14 Independent personal services` exists in the corpus. Straight miss, unchanged. |
| S21† | `vdfqpcrfg8` | Assessment of Various Entities (stat, 2.69) | *P* | **PGBP** (stat, 4.00) | **Correct** | **Improved.** 14-mark ABC Ltd. computation. IDF working exactly as designed: `40(a)(i)`, `40A(3)`, `35DDA` at 1.443 each now outweigh the breadth of `115BAB` (0.39) and `112A` (0.455). This is the S-3 fix delivering. |
| S22† | `vdgtr907u0` | TAX DEDUCTION AT SOURCE (stat, 2.03) | **C** | TDS,TCS AND ADVANCE TAX (stat, 0.66) | **Correct** | 194-O e-commerce TDS. Label still correct (TDS chapter family), **but the evidence rotted**: score fell 2.03 → 0.66, the matched section is now a *TCS* one, and `TAX DEDUCTION AT SOURCE` fell to rank 3 at 0.032. Cause is N-1 — verified: ref `194` matched `TAX DEDUCTION AT SOURCE` 3× under the baseline pattern and 0× now. Right verdict, hollow reasoning. |
| S23† | `vdhveafpbc` | SARANSH \| TRANSFER PRICING (stat, 1.33) | **C** | SARANSH \| TRANSFER PRICING (stat, 1.21) | **Correct** | Thin capitalisation, matched `[Section 94B]` exactly. Unchanged, including the duplicate-root label defect (S-6). |
| S24 | `pjh3n4116b` | NON RESIDENT TAXATION (stat, 2.75) | **C** | Assessment of Various Entities (stat, 2.56) | **Wrong** | **Regression.** Agency business connection, s.115A rates, s.91 relief for a foreign company. `112(1)(c)(iii)` → parent `112` → 5 sections including `SCHEME FOR TAXATION OF REITs`, which combined with `115A`'s REIT hit to overtake `NON RESIDENT TAXATION` (now rank 2 at 1.05). Pure N-2 damage. |
| S25 | `ps8nfp2jtf` | DEDUCTIONS FROM GTI (stat, 3.39) | *P* | **PGBP** (stat, 4.80) | **Correct** | **Improved.** 14-mark PGBP-dominated computation. `36(1)(iva)`, `40(a)(ia)`, `43B`, `194H` at 1.443 each beat `115BAA` (0.353) and `139(1)` (0.334). The S-3 fix delivering again — on the rows where the dispositive refs happen to be corpus-rare. |
| S26 | `q6e1cd097t` | TDS,TCS AND ADVANCE TAX (stat, 3.36) | **C** | TDS,TCS AND ADVANCE TAX (stat, 8.48) | **Correct** | 206C(1H)/194Q on a scrap purchase. Unchanged verdict; score more than doubled because three of four refs now sit at 1.443. Note the score inflation is not extra confidence — it is N-3. |
| S27 | `q6e3agssc2` | TAX DEDUCTION AT SOURCE (stat, 2.03) | **C** | TDS,TCS AND ADVANCE TAX (stat, 0.66) | **Correct** | 194-IA TDS on immovable property. Identical shape to S22: label survives, evidence rotted to a TCS section, `TAX DEDUCTION AT SOURCE` demoted to rank 3, and rank 2 is `FUNDAMENTALS OF BEPS` at 0.61. N-1. |
| S28 | `q6e3ebjvtq` | **AMT liability not attracted (stat, 8.00)** | **W** | INCOME FROM OTHER SOURCES (stat, 0.99) | **Wrong** | **S-1 is genuinely fixed here.** Ref `11`: 264 phantom occurrences → 1. The 8.00 junk score and the gibberish label are both gone. But the question — charitable trust, s.13(3) benefit to interested persons — is *still* wrong, now via `13(3)` → `[SECTION 56(2)(x)]`. There is no trust chapter in the corpus, so the only correct output is a gap (S-7). Fix landed; verdict didn't move. |
| S29 | `q6efffagro` | Assessment of Various Entities (stat, 2.00) | **W** | **TRANSFER PRICING** (stat, 2.99) | **Correct** | **Improved.** ALP determination and TP penalties for an SEZ unit. `92A(2)` → parent `92A` → `TRANSFER PRICING` (1.443), plus `92B` and `92D`, together beating `10AA`'s breadth (13 sections, 0.379). Both new mechanisms pulling the right way. |
| S30 | `qfocpccn2g` | Assessment of Various Entities (stat, 1.33) | **W** | Assessment of Various Entities (stat, 1.21) | **Wrong** | 44BBA presumptive income of a non-resident airline. `44BBA` matches 2 sections, one of which is literally titled `Assessment of Various Entities`. `NON RESIDENT TAXATION` is the chapter and is absent from the top 3. Unchanged. |
| S31 | `kftctrumh8` | DEDUCTIONS FROM GTI (stat, 5.36) | *P* | **PGBP** (stat, 3.87) | **Correct** | **Improved.** 14-mark computation with 80-IAB/80M. `36(1)(iii)` and `41(1)` at 1.443 now carry it to PGBP. Note `80-IAB` truncated to `80` (N-1) and contributed 10 sections of noise anyway — it improved *despite* that. |
| S32 | `kfugoeamjs` | TDS,TCS AND ADVANCE TAX (stat, 3.40) | **C** | TDS,TCS AND ADVANCE TAX (stat, 4.16) | **Correct** | 206C(1G) TCS on an overseas tour package. Unchanged. |
| S33 | `l6u4nh487l` | PGBP (stat, 5.36) | **C** | PGBP (stat, 4.25) | **Correct** | 14-mark PGBP computation. Unchanged — the leg working as designed. Note rank 2 is `Applicable Fee for application for APA` at 0.67, from `139(1)` → the Rule 10CB timetable. |
| S34 | `l70kd1ia7m` | DEDUCTIONS FROM GTI (stat, **8.00**) | **W** | DEDUCTIONS FROM GTI (stat, 2.46) | **Wrong** | **The IDF fix's own test case, and it failed.** Foreign-income relief with no DTAA — squarely `Unilateral Relief [Section 91]`. `91` (3 sections, w=0.721) still loses to `115BAC` (25 sections, w=0.307) because contributions are *summed*: 25 × 0.307 = 7.68 vs 3 × 0.721 = 2.16. `DOUBLE TAXATION RELIEF` is still absent from the top 3. The score fell; the answer did not change. |
| S35 | `l71iaudl2a` | TRANSFER PRICING (stat, 1.37) | **W** | TRANSFER PRICING (stat, **3.88**) | **Wrong** | s.154 rectification time limit, still caught by the `REFERENCE TO TPO [SECTION 92CA]` attractor on its s.153/154 mentions. **Worse than baseline:** the parent fallback added `154(7)` → `154` and `143(3)` → `143`, both at 1.443, so the same wrong answer now scores 2.8× higher. N-2 + N-3 actively reinforcing a known-bad attractor. |
| S36 | `re8h3gvm48` | DEDUCTIONS FROM GTI (stat, **10.05**) | **W** | **TDS,TCS AND ADVANCE TAX** (stat, 2.91) | **Correct** | **Improved — the flagship S-2 repair.** Employer TDS on salary u/s 192. `192` is now live (2 sections) via the table-cell anchor, and the matched section is the TDS/TCS chapter node itself. The 10.05 wrong answer is gone. Caveat: `194A` is *still* dead, and `16(ia)` → parent `16` landed on `Conditions … concessional rates` rather than the salary-deduction section. |
| S37 | `re9pg1abef` | TRANSFER PRICING (stat, 1.36) | **W** | TRANSFER PRICING (stat, **3.88**) | **Wrong** | s.154 vs s.148A reassessment. Identical to S35 — same TPO attractor, same 2.9× score inflation from the parent fallback. `148A(1)` still dead. |
| S38 | `s4s4nqvgfh` | NON RESIDENT TAXATION (stat, 0.69) | **C** | NON RESIDENT TAXATION (stat, 0.99) | **Correct** | 44BBC presumptive cruise income for a non-resident. Unchanged. |
| S39 | `s4ssivqhad` | DEDUCTIONS FROM GTI (stat, 8.00) | *P* | DEDUCTIONS FROM GTI (stat, 3.21) | *Partial* | 80QQB/80TTB vs the s.91 foreign-tax relief the question turns on. `80TTB` is dead; `91` (0.721) again loses to `115BAC` (0.307 × 25). Same shape as S34, same non-fix. Score fell, verdict unchanged. |
| S40 | `s4t86s365o` | Interest … [234B] (stat, 2.67) | **W** | **PGBP** (stat, 1.65) | **Correct** | **Improved — but not by the fix that was aimed at it.** 43B(h) MSME payment discipline. The win comes from `43B(h)` → parent `43B` → `CERTAIN DEDUCTIONS … ON ACTUAL PAYMENT [SECTION 43B]`. The other-statute fix did **not** work: the MSMED refs leaked through as truncated `1`, `2` and `23` (N-4), and `1`/`2` matching table serial-number cells (N-5) put `CAPITAL GAINS` at rank 2 with 1.248 — within 0.4 of taking the question. Right answer, one bad table cell away from wrong. |

---

### Method notes and limits

- Judgement is mine, one pass, against the question text plus its cited `statutory_refs`, with the
  corpus section tree and matched-section markdown in hand. Same rater as the baseline, which removes
  inter-rater drift from the before/after comparison but not rater bias from either figure.
- **Rubric consistency check.** S22, S27 and S36 land on `TDS,TCS AND ADVANCE TAX` rather than the
  narrower `TAX DEDUCTION AT SOURCE`. I verified the tree: `TAX DEDUCTION AT SOURCE` is a level-2
  child of a `TDS, TCS AND ADVANCE TAX` root, and `TAX COLLECTION AT SOURCE [SECTION 206C]` is a
  child of the other. The parent chapter genuinely teaches TDS, so all three score **Correct** —
  consistent with the baseline scoring S26 and S32 Correct on the same label. Scoring them *Partial*
  instead would put strict precision at 40.0% (below the 42.5% baseline) rather than 47.5%; either
  way the conclusion "no improvement" holds, which is why the boundary is stated explicitly here.
- n=40 gives roughly ±15pp at 95% confidence, so the 42.5% → 47.5% move is not measurable. The
  **paired** comparison (6 up, 5 down, McNemar p = 1.0) is the stronger test and it is flat.
- Precision is scored on the rank-1 **topic label**, as at baseline.
- Recall was not measured. S10 is the sharpest case: the correct chapter is at rank 2, 0.001 behind.
- Four rows are judged on re-extracted successor questions (see *Sample integrity*); their identity is
  established by matching question text and cited refs, not by row name.
- Temporary read-only modules `wikify/temp_audit2.py` and `wikify/temp_audit3.py` were used to extract
  the data and replay the ref matcher; both have been deleted. Nothing under `wikify/exam/`,
  `wikify/transfer/`, `wikify/api/`, `wikify/tests/` or the frontend was modified, and the mapping was
  not re-run.

---
---

# Part 1 — the baseline (12 August 2026, before the fixes)

*Preserved verbatim so the before/after is legible in one document.*

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

## Systematic failure modes (baseline)

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

## The baseline sample

Stratified over rank-1 `method` (20 statutory / 20 retrieval — the population splits 91/99, so this is
near-proportional), all 4 `question_kind` values, and all 6 exam years present (2021–2026), drawn with
a fixed seed across method×kind buckets with year-spread ordering.

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

## Baseline fix list (superseded by Part 2)

1. **Anchor `provision_pattern` on the right.** — *landed; see S-1 status and N-1.*
2. **Make the citation anchor cover table-row citations** + parent-section fallback. — *landed; see
   S-2 status, N-2 and N-5.*
3. **Normalise `statutory_refs` at write time / accept the bare format.** — *landed; see S-5 status.*
4. **Down-weight ubiquitous provisions by inverse document frequency.** — *landed; see S-3 status and
   N-3. Did not achieve its goal.*
5. **Reject references to other statutes.** — *landed; see S-4 status and N-4. Did not achieve its
   goal.*
6. **Replace `MIN_TOPIC_SUBTREE` with a real chapter test.** — *not attempted.*
7. **Fix the rollup's parent choice for mis-nested chapters.** — *not attempted.*
8. **Make "no link" reachable.** — *not attempted.*
9. **Re-measure after 1–5.** — *done; this is Part 2.*
