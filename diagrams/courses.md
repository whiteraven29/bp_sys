# Planned courses — the starting list

**Started:** 3 October 2026. **Sources:** your answers in `questions.md` and the
research in `veta-research.md` (its marks: ✅ official · ⚠️ secondary, verify ·
❓ not found).

This is the list the system will be set up from: one row becomes one
programme. When a figure is confirmed, replace it here and mark it ✔ (confirmed
by the college). The VETA fees below are **VETA's own centres' fees**, kept for
reference only; the college sets its own.

---

## The two kinds of VETA course

```text
                          VETA courses (CBET, competence-based)
                 ┌───────────────────────────┴───────────────────────────┐
                 ▼                                                       ▼
        LONG COURSE (NVA)                                       SHORT COURSE
  leads to NVA Level I, II, III                     1 day to 6 months, no NVA level
  1–3 years, one level per year                     certificate from the college
  starts in January (March? ⚠️)                     (approved by NACTVET — your Q10)
  calendar year, Jan → Nov/Dec  ✅                   any start date the college sets
  national assessment (CBA) by VETA,                tested by the tutors, pass / fail
  May/June and Nov/Dec  ⚠️                           repeat a failed module (your Q6)
  aptitude test before admission  ✅
```

Neither is semester-based like PST, and neither uses NTA levels or a GPA.

---

## Starting now — first intake 19 October 2026

| Proposed code | Course | Kind | Length | Ends about | Award | College fee |
|---|---|---|---|---|---|---|
| HDB | Hair Dressing and Beautician (*Ususi na Urembo*) | short | **4 months** ✔ | about 19 February 2027 | college certificate | day 450,000 ✔ · hostel 700,000 ✔ |
| HCT | Hair Cutting (*Mafunzo ya Kunyoa*) | short | **3 months** ✔ | about 19 January 2027 | college certificate | day 400,000 ✔ · hostel 650,000 ✔ |

**From the college's form (page 3), confirmed 3 October:**

| | Course fee | Awamu 1 (on reporting) | Awamu 2 (after 1 month) | Hostel | Hostel awamu 1 / 2 | Hostel total |
|---|---|---|---|---|---|---|
| HDB | 450,000 | 300,000 | 150,000 | 250,000 | 500,000 / 200,000 | 700,000 |
| HCT | 400,000 | 300,000 | 100,000 | 250,000 | 450,000 / 200,000 | 650,000 |

Every student, day or hostel, also pays once: uniform (*sare*) 50,000, identity
card 10,000, caution money (*tahadhari*) 20,000, examinations (*mitihani*) 20,000
— 100,000. Entry: the applicant can read and write (*awe anajua kusoma na
kuandika*). Paid into the same CRDB accounts as PST. Loaded by
`seed_veta_courses`, which checks every total against the form; awamu 1 is
marked *before admission*, and the accountant can change that.

## Planned

| Proposed code | Course | Kind | Length | Award | VETA reference fee | Decision still open |
|---|---|---|---|---|---|---|
| ICT | Computer (ICT) | **long** ✔ (your choice) | 2 years ✅ | NVA Level II ✅ | TSh 60,000/yr day, 120,000 boarding ✅ | intake month (January?) |
| ELE | Electronics | long ✅ or short ⚠️ | long 2 yrs, exit Level II ✅ · short 4 wks–3 months ⚠️ | NVA Level II (long) | long 60,000/yr ✅ · short 160,000–295,000 ⚠️ | **long or short?** (QV2) |
| MPM | Mobile Phone Maintenance | short (no long course exists) ⚠️ | 4 weeks–3 months ⚠️ | college certificate | 115,000–350,000 ⚠️ | length; one course or three stages (basic / intermediate / advanced)? (QV3) |
| LAB | Laboratory Assistant | long ✅ | 2 years ✅ | NVA Level II ✅ | 60,000/yr ✅ | **not for clinical labs** — VETA's award suits school / engineering labs; hospital labs need NACTVET Health Laboratory Sciences (QV4) |
| APR | Animal Production | long ⚠️ | ❓ | NVA (Animal Husbandry) ⚠️ | ❓ | **VETA Animal Husbandry or NACTVET Animal Health & Production (NTA 4–6)?** The NACTVET route would be semester-based like PST (QV5) |

---

## What every VETA course has in common (so far)

| Item | Value | From |
|---|---|---|
| Student number | the college registration number; an NVA number is obtained only for the national exam | your Q4 |
| Results | tutors enter test marks; pass / fail; no GPA; the college's own results | your Q5 |
| Failing | repeat the required modules | your Q6 |
| Attendance | taken | your Q11 |
| Hostel | allowed | your Q9 |
| Admission | all four desks; next of kin required | your Q12 |
| Run by | the course / training coordinator | your Q18, Q23 |
| Payment dates | not PST's awamu form | your Q15 |
| Required items (TPH book…) | none | your Q16 |
| Entry documents | NIDA ID or birth certificate, 2 passport photos ⚠️; two CSEE passes for Level III ✅ | research §1, §3 |
| Application form fee | about TSh 5,000 at VETA centres ⚠️ | research §3 |

## Data each course will need in the system

```text
  Programme   code · name · department · kind (long / short) · award
              (NVA level or college certificate) · length · pass mark
     │
     ├── Modules (if it has them): code · name · order · NVA level (long courses)
     │
     ├── Fees for the year: what (tuition, registration, exam, certificate…)
     │   · amount · instalments, due so many days after the start · hostel rate
     │
     └── Intakes: start date · end date · admission dates · capacity
```
