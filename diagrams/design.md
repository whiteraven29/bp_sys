# Design — planned changes

**Started:** 2 October 2026. **Builds on:** the system in `structure.md`.

This file is the living design. When an answer comes in or a part is built,
update the section and add a line to the **Change log** at the end.

| Part | Subject | Status |
|---|---|---|
| 1 | VETA courses beside PST (sections 1–8) | **skeleton built 3 Oct** (step A of section 7; not committed); results, completion and long-course levels to come |
| 2 | Roles and offices (sections 9–15) | built 2 Oct (not committed); appointment letters wait on `questions.md` Q21–Q22 |
| 3 | Expenses (section 16): request, query, office funds or petty cash | **updated 3 Oct** (not committed) |
| 4 | The student record and what a repeater pays (sections 17–18) | **built 3 Oct** (not committed); open points Q25–Q29 |
| 5 | PST results as NACTVET decides them (section 19) | **built 8 Oct** (not committed); 187 of 188 real students match |

---

# Part 1 — VETA courses beside PST

**Updated 3 October 2026** with your answers in `questions.md` and the VETA
research (`veta-research.md`). The courses themselves are listed in
`courses.md`. First intake: **cosmetology, 19 October 2026**.

---

## 1. The rule this design keeps

> **PST and every semester-based programme keep working exactly as they do now.**
> Their tables, results, GPA, supplementaries, repeats, year advance, items and
> payment schedule are not changed. The VETA courses are added *beside* them.

Why: the semester track is woven through about 390 references in
`admissions.py`, `finance.py`, `progression.py` and `lifecycle.py`. Bending it to
fit intakes that start any month would mean rewriting the code PST's results
depend on, and students' modules and results must never be altered.

---

## 2. Two tracks, one college

What the answers settled: every new course is a **VETA** course (competence-based,
CBET), not a NACTVET semester course. VETA has two kinds, and one design covers
both: a **course** has **intakes**; a short course lasts months, a long course
lasts years and climbs NVA levels.

```text
                        ┌────────────────────────────────────┐
                        │   SHARED — used by both tracks     │
                        │                                    │
                        │  StudentProfile (the person)       │
                        │  next of kin, documents, college ID│
                        │  Application desks + visibility    │
                        │  ChargeType, StudentCharge,        │
                        │  Invoice, Payment, statement       │
                        │  Hostel (residence, applications)  │
                        │  Portal sign-in and profile        │
                        └──────────────┬─────────────────────┘
                                       │
              ┌────────────────────────┴────────────────────────┐
              ▼                                                 ▼
  ┌───────────────────────────┐                 ┌─────────────────────────────────┐
  │ SEMESTER TRACK (as now)   │                 │  VETA TRACK (new)               │
  │ NACTVET — PST             │                 │  run by the course coordinator  │
  │                           │                 │                                 │
  │ AcademicYear → Semester   │                 │  Course → Intake (any month)    │
  │ 1 and 2                   │                 │   SHORT: months, college        │
  │ NTA levels 4–6            │                 │          certificate            │
  │ Module per semester       │                 │   LONG:  years, NVA Level I–III,│
  │ FeeStructure + awamu      │                 │          January (QV6)          │
  │ Required items            │                 │  Course modules                 │
  │ GPA, review, supp,        │                 │  Test marks → pass / fail,      │
  │ repeat, disco, advance    │                 │  no GPA; repeat failed modules  │
  │ NACTVET number            │                 │  College registration number    │
  └───────────────────────────┘                 │  (+ NVA number for VETA's exam) │
                                                │  Course fees; no items          │
                                                └─────────────────────────────────┘
```

A programme says which track it is on. Everything else follows from that.

---

## 3. New and changed tables

### 3a. What changes on existing tables (all optional, nothing removed)

| Table | Change | Effect on PST |
|---|---|---|
| `Programme` | `structure`: *semester* (default) or *VETA*; for VETA: `kind` (short / long), `award` (college certificate / NVA level), `length_months`, `pass_mark` (Q5) | none, every existing programme stays *semester* |
| `StudentProfile` | `nva_number` (optional): added when a long-course student registers for VETA's national exam (Q4). For a VETA student the registration-number field holds the **college registration number**, since they get no NACTVET number; their screens label it "Registration No." | none |
| `Application` | `intake` (optional); `semester` and `class_level` become optional | none, PST applications still fill semester and level |
| `StudentCharge` | `intake` (optional) | none |
| `AdmissionRequirement` | offered to semester programmes only (Q16: VETA courses have no items) | none |

### 3b. New tables

```text
  Programme (structure = VETA, kind = short | long)
     │
     ├──► CourseModule      code, name, order, NVA level (long courses only)
     │                      one list per course, reused by every intake
     │
     ├──► CourseFee         per course per year: charge type, amount,
     │                      instalments (days after the start), hostel rate (Q7, Q9)
     │
     ▼ n
  Intake   "Hair Cutting — October 2026"
     │  starts_on, ends_on, admission opens / closes, capacity,
     │  status: planned → open → running → finished
     │  copies the CourseFee rows when created ──► IntakeFee (editable per intake)
     │
     ▼ n
  CourseEnrollment   one person on one intake (never two at once — Q3)
     │  status: studying | completed | not completed | withdrawn
     │  current NVA level (long courses), certificate number, completed_on
     │
     ├──► CourseResult      module, test mark, passed (yes / no), attempt
     │                      (a repeat is a new row; the failed one is kept — Q6)
     ├──► CourseAttendance  per class session (Q11)
     └──► LevelAssessment   long courses: VETA's national result per level,
                            session (May/June, Nov/Dec), NVA certificate no. (QV7)
```

Kept apart from `Module`, `Student`, `StudentResult` and the attendance tables
on purpose: those all hang off a semester, and PST's grading, GPA, review and
advance must never see a VETA row.

---

## 4. The flows

### 4a. Setting up (once per course, then per intake)

```text
  PRINCIPAL / DPARC        COURSE COORDINATOR       ACCOUNTANT              ADMISSION OFFICER
  ┌────────────────┐   ┌────────────────────┐   ┌──────────────────┐   ┌──────────────────────┐
  │ Department     │──►│ Course modules     │──►│ Course fees for  │──►│ New intake: dates,   │
  │ Programme:     │   │ (and their NVA     │   │ the year,        │   │ admission dates,     │
  │  VETA, short / │   │ level, long        │   │ instalments,     │   │ capacity (fees copied│
  │  long, pass    │   │ courses)           │   │ hostel rate      │   │ in; finance may      │
  │  mark          │   │                    │   │                  │   │ adjust this intake)  │
  └────────────────┘   └────────────────────┘   └──────────────────┘   └──────────────────────┘
```

### 4b. A student joins an intake

All four desks, as for a PST first year (Q12).

```text
  ADMISSION OFFICER        RECORDS               ACCOUNTANT            ADMISSION OFFICER
  ┌──────────────────┐  ┌────────────────┐  ┌────────────────────┐  ┌──────────────────┐
  │ INTAKE           │─►│ INFORMATION    │─►│ FINANCE            │─►│ ADMISSION        │
  │ name, course,    │  │ details, next  │  │ intake fee raised  │  │ ADMIT ──► college│
  │ INTAKE (not      │  │ of kin (needed)│  │ day or hostel (Q9) │  │ ID, enrollment,  │
  │ semester)        │  │ documents (Q12)│  │ paid what is due   │  │ portal access    │
  │ college reg. no. │  │                │  │ to start? (Q8)     │  │                  │
  │ (aptitude score, │  │                │  │ NO items check     │  │                  │
  │  long — QV10)    │  │                │  │                    │  │                  │
  └──────────────────┘  └────────────────┘  └────────────────────┘  └──────────────────┘
```

A person already known to the college is found by name or number, not entered
twice. Nobody is on two courses at once (Q3).

### 4c. Money

```text
  IntakeFee rows ──► StudentCharge (intake set)
                            │
                            ▼
              Invoice per course fee (Q7), into the college's accounts or by
              GePG control number (QV9)
                            │
                            ▼
              Payment ──► allocations ──► balance ──► what must be paid before
                                                      starting / testing /
                                                      the certificate (Q8)
```

The accountant sees VETA students in the same Balances, Payments, Collections
and Statement screens, filtered by course or intake, and the Executive Summary
counts them.

### 4d. Results and finishing

```text
  tutor enters a test mark per module (Q5)
            │
            ▼
  mark ≥ the course's pass mark? ── no ──► repeat the module (Q6)
            │ yes                            (new attempt; the failed one is kept)
            ▼
  every module of the course passed?
            │
     ┌──────┴────────────────────────────────┐
     ▼ SHORT COURSE                          ▼ LONG COURSE
  COMPLETED                            every module of this level passed
     │                                        + VETA national test passed (QV7, QV8)?
     ▼                                        │
  fees cleared? (Q8) ──► college          next level ──… after the last level:
  certificate number recorded             COMPLETED; NVA certificate number from
  (college, approved by NACTVET — Q10)    VETA recorded (Q10)
     │
     ▼
  portal: open for how long after finishing? (Q13)
```

No GPA, no classification: pass or fail per module (Q5).

### 4e. The portal

```text
  Sign in with the college registration number (section 5)
        │
        ├─ PST student   → the portal as it is now
        └─ VETA student  → My course (intake, dates, level for long courses)
                           Attendance · Results (pass / fail per module)
                           What I Owe · Generate Invoice · My Invoices
                           Hostel request (Q9) · My Profile
                           (no semesters, items or GPA; stopping part-way: Q14)
```

---

## 5. One thing that must change in shared code: portal sign-in

Today sign-in looks up **module enrollments** (`Student` rows) by NACTVET
number, and the PIN lives on them (`views.login_view`). A VETA student has no
PST module enrollment and no NACTVET number (Q4), so they could never sign in.

```text
  sign-in form: number + PIN
        │
        ├─ found as a Student (module enrollment)?  ──► exactly as now (PST)
        │
        └─ not found ──► look up StudentProfile by registration number or
                         college ID, PIN kept on the profile ──► VETA portal
```

The PST path is tried first and is unchanged. The new path only runs when the
old one finds nobody. Tests must prove a PST student signs in exactly as before.

---

## 6. What the VETA track does not reuse

| Semester feature | VETA course | Why |
|---|---|---|
| Required items (TPH, calculator…) | none | Q16 |
| Payment schedule (awamu form) | intake fee instalments | Q15: other dates |
| GPA, semester review, supplementary, discontinuation | pass / fail per module, repeat failed modules | Q5, Q6 |
| Year-end advance | long courses: next NVA level after the level's tests (QV8) | VETA's progression |
| Admission window per semester | admission dates per intake | intakes start any month |
| Postponement | a later intake (Q14, open) | |
| *Reused:* hostel | yes | Q9 |
| *Reused:* attendance | its own tables (Q11) | PST's hang off semesters |

---

## 7. Build order — the first intake is 19 October 2026

PST opens on 12 October with this release; cosmetology a week later. So the
build is cut by what each date needs:

```text
  A. BY 19 OCTOBER — admit, bill, sign in                          ✔ BUILT 3 Oct
     1. The Principal / DPARC defines the course: department, name,
        code, structure (short / NVA I–III), duration              ✔
     2. The accountant: each fee's charge type (= its account),
        amount and instalments, days after the start               ✔
     3. The admission office: the VETA ID pattern; intakes          ✔
     4. Four desks: records needs a phone and a next of kin, NO
        documents; finance states day/hostel and raises the fees;
        admission admits → college ID, enrollment, portal password  ✔
     5. The student's own portal: course, what is owed, modules,
        details, password                                           ✔
     6. Course coordinator role and page: modules, class lists      ✔
     7. The accountant marks instalments "before admission"; the
        admission desk cannot admit until they are paid (Q8)       ✔
     8. seed_veta_courses: the cosmetology courses and fees from
        the college's form, checked total by total                 ✔

  B. BEFORE THE FIRST TESTS (about mid-November)
     6. Course modules, attendance, test marks → pass / fail, repeats   needs Q5, Q6, Q11

  C. BEFORE THE FIRST FINISHERS (Hair Cutting about 19 January 2027)
     7. Completion, certificate number, portal after finishing          needs Q10, Q13

  D. BEFORE THE FIRST LONG-COURSE INTAKE (January 2027? — QV6)
     8. NVA levels, moving up a level, VETA's national results          needs QV1–QV8, QV10
     9. Reports: students per intake, fees per course
```

Each step ships with its own tests, and the full suite (771 tests now) must
still pass unchanged after every step.

---

## 8. If a course goes the NACTVET way

Only Animal Production might (QV5: NACTVET Animal Health and Production, NTA
4–6). It would then be semester-based like PST, and two gaps apply:

- **Payment schedule** is one per year for all programmes; it would need its own
  (Q15: not PST's dates).
- **Required items** apply to every semester programme; it would need none, or
  its own list (Q16).

---

# Part 2 — Roles and offices

Source: the college's organisational chart (`organisational-chart.pdf` in this
folder) and the decisions you gave on 2 October 2026.

---

## 9. The problem today

```text
  Django's is_staff flag does three jobs at once:

      is_staff ──┬── "is an admin"            (menu items marked admin-only)
                 ├── exam powers              can_manage_exams = is_staff and not HOD
                 └── account management       can_manage_accounts = is_staff and not HOD

  Who has it:  Principal, HOD, and "exam officer"
  Exam officer has no role record of its own: it is any is_staff account
  that is neither Principal nor HOD.
```

So the exam officer creates accounts and hands out roles, and the Principal can
enter and approve marks. About 40 checks in `views.py` and 35 `admin-only` menu
items in `index.html` read the flag.

**Fix:** every power checks a named role. `is_staff` goes back to meaning only
"may open Django's `/admin/`", which is for the ICT officer.

---

## 10. The college, and who is in the system

```text
                      College Governing Board · Board Committees · Director
                                   (not in the system — Q24: no)
                                           │
                                  ┌────────┴────────┐
                                  │    PRINCIPAL    │ ◄─── Quality Officer (later)
                                  └────────┬────────┘
                    ┌──────────────────────┴───────────────────────┐
                    ▼                                              ▼
          ┌───────────────────┐                          ┌───────────────────┐
          │      DPARC  (new) │                          │      DPFPA  (new) │
          │ academic, research│                          │ finance, planning,│
          │ and consultancy   │                          │ administration    │
          └─────────┬─────────┘                          └─────────┬─────────┘
     ┌──────────┬───┴─────┬──────────┐        ┌──────┬──────┬──────┼──────┬──────┐
     ▼          ▼         ▼          ▼        ▼      ▼      ▼      ▼      ▼
   HoDs /      AO        EO         HLS      HRM    HoP    HoA     EM    Dean
   Training  admission  exam      library   (later)(later) accoun-estate (now)  
   Coordinator officer  officer   (later)                  tant   manager  │
     │                                         │                         ▼
     ▼                                         ▼                    Students'
   Academic staff (tutors)               Supporting staff           organisation
                                         (not in the system)        → students

   Not on the chart, in the system, reporting to DPFPA (Q20):
   Records officer · ICT officer · Secretary
   Training Coordinator (beside the HoDs) = the course coordinator who runs
   the VETA courses (Q18, Q23)
```

---

## 11. What each role may do (decided 2 October 2026)

| Role | May do | May not do |
|---|---|---|
| **Principal** | See everything, including college property (read-only). **Only one who approves postponement.** Declares a finisher cleared. Confirms semester reviews and runs the year advance. Finance summary; approves expenses with DPFPA (when expenses are built). **Appoints senior roles** (section 13). | Enter or approve marks college-wide (see section 12 for their own modules), record payments |
| **DPARC** *(new)* | Every academic and student area: departments, modules, admissions (the AO reports here), student records, results, **confirms semester reviews and runs the year advance**, services, sick records, performance. **Notified of each postponement and sees the list.** | Any finance amount, fee setup or payment. Approve a postponement |
| **DPFPA** *(new)* | Executive finance summary: collections, balances, spending once expenses exist. **Approves an expense; the Principal may approve it instead — one approval is enough.** **Sees college property** in figures and downloads the estate manager's Excel reports (the estate manager reports here). | Marks, results, editing student records, the estate manager's edit screens |
| **Exam officer** *(own role, no longer admin)* | Results processing and approval, authority upload, CA and result windows and cutoffs, eligibility, declarations, attendance, Results Summary, withholding, **reviewing results and preparing semester reviews**. **Reads this semester's modules and each module's class list** | Confirming a review, **advancing a semester**, and the departments' work: user accounts, departments, registering or editing modules, enrolling students, services (forms and requests), sick records. Finance |
| **ICT officer** *(new — you)* | Creates accounts, resets passwords, unlocks, sees the role-change log. **Django `/admin/` with every table**: the role makes the account a Django superuser, and taking the role away takes it back | Inside EduTrack: grant a senior role (only the Principal, by appointment), results, money, student records |
| **HOD** | Runs the department with the Principal and DPARC: modules and who teaches them, enrollments, staff list, forms and service-request decisions, **sick records**. Limiting to their own department comes later. | Marks and approvals (except CA for modules they teach) |
| **Accountant, Admission, Records, Estate, Secretary, Tutors** | As now | — |

---

## 12. Who enters marks for a module the Principal (or DPARC, or a HOD) teaches

The rule already in the code for every module is kept, and applied by
*module*, not by office:

```text
  for one module:

    teaches this module? ──yes──► enters CA (CATs, assignments) while the
           │                      exam officer has the CA window open
           │
    is exam officer? ─────yes──► enters end-of-semester and supplementary
           │                      marks, approves CA and final results
           │
    anyone else ──────────────► reads only (if their role may read results)
```

So a Principal who teaches enters CA for **their own modules** like any tutor,
and the exam officer enters the final exam and approves, as for every module.
Holding a senior office adds no power over marks.

**Found while checking this:** today a HOD who teaches cannot enter CA even for
their own modules. `check_result_entry` refuses every HOD before it checks who
teaches the module (`views.py` line 222). The rule above fixes that.

---

## 13. Appointing a senior role

Senior roles are given by the Principal with an appointment letter, and take
effect only when the person returns an acceptance letter.

```text
  ICT OFFICER              PRINCIPAL                   APPOINTEE               SYSTEM
  ┌──────────────┐   ┌──────────────────────┐   ┌──────────────────────┐   ┌────────────────┐
  │ creates the  │──►│ Appoint: person,     │──►│ sees the appointment │──►│ role becomes   │
  │ account      │   │ role, start date,    │   │ on sign-in, reads    │   │ active from    │
  │ (no senior   │   │ appointment letter   │   │ the letter, returns  │   │ start date;    │
  │ role)        │   │ (uploaded or printed)│   │ acceptance letter    │   │ logged; DPARC /│
  └──────────────┘   └──────────────────────┘   └──────────┬───────────┘   │ DPFPA notified │
                                                           │               └────────────────┘
                                                    declines / no answer
                                                           ▼
                                                  appointment lapses,
                                                  no role given
  Ending a role: Principal ends the appointment (date + reason) → role removed,
  history kept.
```

Answered so far: letters are **printed by the system from a template** (Q22);
letters go to DPARC, DPFPA, HOD, exam officer, quality assurance and admission
officer (Q21). Still open: the other roles, how the appointee accepts, and
whether an appointment has a term (Q21, Q22).

---

## 14. How it is built

Status 2 October 2026: steps 1–5 and 7 are built and tested; step 6 waits on
Q21–Q22. Until then the role editor is the appointment: only the Principal can
give a senior role, and every change is listed under **User Accounts ▸ Role
changes**.

```text
  1. Role records: ExamOfficer, DPARC, DPFPA, ICTOfficer profiles
     Migration: every account that is is_staff with no Principal/HOD profile
     gets an ExamOfficer profile FIRST, so nobody loses access on deploy day.
  2. Every check reads a named role (can_manage_exams → exam officer;
     can_manage_accounts → ICT; reviews → Principal + DPARC + exam officer
     prepares; advance → Principal + DPARC). Mark entry checked per module
     (section 12).
  3. Menu: data-roles per item replaces admin-only.
  4. Dashboards: DPARC (academic), DPFPA (finance summary + estate).
  5. Postponement: notice and list for DPARC.
  6. Appointments and letters (section 13).
  7. Django admin (is_staff + is_superuser) only on the ICT officer's account.
     The way back if it is ever locked out: createsuperuser on the server.
```

Tests: `test_offices.py` has a case for each row of the table in section 11,
the marks-by-module rule and the migration step; `test_roles.py` was brought in
line. 771 tests pass.

What the build added, beyond the table:

| Piece | Where |
|---|---|
| Every office's powers in one file | `attendance/roles.py` |
| Role records + role-change log + migration step | `models.py`, migration 0055 |
| Executive summary (totals only, no student named) | `GET /api/executive/finance/`, Executive Summary page |
| College property in figures | `GET /api/inventory/summary/`, Executive Summary ▸ Inventory (Principal and DPFPA) |
| Role-change log | `GET /api/staff-accounts/role-changes/`, User Accounts page |
| Postponement notices to the Principal and DPARC; the student told of the decision | `notifications.py`, `lifecycle.py` |
| The marks sheet's Save keeps to the CA window, like saving one result | `ResultViewSet.bulk_save` |
| Only the exam office deletes a result row | `ReadExamsWriteExamOfficer` |
| The ICT Officer role carries Django superuser (all of `/admin/`), while the app still shows the ICT screens | `views.set_roles`, `signals.py`, `roles.is_break_glass` |
| A superuser with no ICT office (made with `createsuperuser` on the server) is the emergency account: passes academic and account checks, never money or property | `roles.is_break_glass` |

---

## 15. Later, when the system has work for them

| Role (chart) | What would give them a place |
|---|---|
| Quality Officer | read-only results, Results Summary, evaluation forms |
| Dean (students) | **now** — the Dean of Students role reads students' health records (Part 4, section 17). Hostel allocation and sick records stay where they are for now |
| HRM | staff records, leave, appointment letters |
| HoP (procurement) | the expenses and procurement work |
| HLS (library) | a library section |
| Training Coordinator | **now (Q23: yes)** — the course coordinator role, built with the VETA track (design Part 1, step 5) |
| Supporting staff, Board, Director | not in the system (Q24: no) |

---

# Part 3 — Expenses

## 16. The expenses register

Asked for on 3 October 2026; the approval rule was decided on 2 October: the
Principal **or** DPFPA approves, one approval is enough. Updated the same day:
the accountant *requests*; the Principal or DPFPA may *query* instead of
deciding; the money comes from **office funds** or **petty cash**.

```text
  ACCOUNTANT                    PRINCIPAL or DPFPA                ACCOUNTANT
  ┌───────────────────────┐    ┌──────────────────────────┐   ┌──────────────────────────┐
  │ requests the expense: │───►│ APPROVE                   │──►│ records the payment:     │
  │ category, payee,      │    │  (over budget? a reason)  │   │ office funds: date,      │
  │ amount, date, ref.,   │    │ REJECT (with a reason)    │   │  method, account, ref.   │
  │ receipt (optional),   │    │ QUERY (a question) ──┐    │   │ petty cash: cash, out of │
  │ OFFICE FUNDS or       │    └──────────────────────┼────┘   │  the float (never more   │
  │ PETTY CASH            │                           │        │  than it holds)          │
  └──────────┬────────────┘                           ▼        └──────────────────────────┘
             │ ▲            QUERIED: the accountant answers,        PAID
             │ └─────────── correcting the request if need be;
             │              it waits for approval again
             └──► CANCELLED (before a decision, or while queried, with a reason)

  PETTY CASH FLOAT   top-ups from a college bank account (accountant)
                     − petty-cash expenses paid  =  what the float holds.
                     A wrong top-up is reversed with a negative one and a reason.

  Categories and a budget per category per year: the accountant's, revised by
  setting it again. Committed = approved + paid; waiting = pending + queried.
  Nothing is deleted; every step goes to the finance audit trail.
```

| Who | Sees |
|---|---|
| Accountant | Finance ▸ Expenses: the register (request, answer, pay, cancel), petty cash (the float and top-ups), and Categories & Budget |
| Principal, DPFPA | Executive Summary ▸ Spending: waiting and queried expenses (approve, reject or query), the petty cash float, budget against spending by category, money in against money out |
| Everybody else | nothing |

Code: `attendance/expenses.py` (the rules), `veta_views.py` (the API),
`test_expenses.py` (each rule a test). Possible next steps: the Head of
Procurement on the chart (HoP) raising requests before the accountant records
them; amount limits that need both approvers; monthly reports.

---

# Part 4 — The student record, and what a repeater pays

## 17. What records takes down (3 October 2026)

```text
  INTAKE (admission office)        RECORDS DESK                         AFTER ADMISSION
  ┌────────────────────────┐      ┌───────────────────────────────┐    ┌──────────────────────┐
  │ name, programme, level │─────►│ phone, GENDER, date of birth, │    │ DEAN OF STUDENTS     │
  │ Form Four index (may)  │      │ FORM FOUR INDEX (PST),        │    │ told of each health  │
  └────────────────────────┘      │ two next of kin               │    │ record; reads them   │
                                  │   — cannot clear without them │    │ and the certificate  │
                                  │                               │    │ for reference;       │
                                  │ HEALTH (any time):            │───►│ changes nothing      │
                                  │  scan the medical certificate │    └──────────────────────┘
                                  │  (Medical form) → mark seen   │
                                  │  → write the condition and    │    Principal: reads, may
                                  │    what to allow for          │    correct. Nobody else:
                                  │  closed with a reason when it │    not finance, tutors or
                                  │  no longer applies            │    the exam office.
                                  └───────────────────────────────┘
```

- **Form Four index number** (NECTA CSEE): kept as printed, `S0123/0045/2021`
  (P… for a private candidate; the year may be left off). One person per
  number. Required for a PST first year at the records desk; a continuing
  student without one is flagged, not held. A VETA course student is not
  asked (the course asks only that they can read and write).
- **Gender**: required at both records desks, PST and VETA; shown in the
  records search and beside each health record.
- **Health**: `HealthRecord` — condition, what to allow for, the certificate
  (a verified *Medical form* in the student's documents), who recorded it;
  current until closed. Not in Django's admin site, so the ICT account does
  not see it.
- **Dean of Students**: a new appointed role (`dean`), given by the Principal.
  Lands on Student Health.

## 18. What a repeater pays (3 October 2026)

A student repeating failed modules only pays no programme fees. Instead:

| Charge | Amount (2026/27) | When |
|---|---|---|
| Repeat Module | 200,000 per module sat again | when finance takes them (or enrollment raises it) |
| Repeat direct costs | 280,000 | once a year, the first time they are billed as a repeater |

A student who carries a repeat beside a full year pays the full year's fees
plus 200,000 per repeat module, and **not** the 280,000: they already pay
the year's direct costs. The accountant sets both rates per level under Fee
Setup; a charge type marked *Charged to repeaters* is the direct cost.
`seed_fee_structure` loads both.

---

# Part 5 — PST results, as NACTVET decides them

## 19. The decision, read from NACTVET's own sheets (8 October 2026)

The evidence is in `diagrams/`: `RESULTS SEMESTER II 2025/` (six classes,
including two repeat classes) and `SUPP RESULT/` (the supplementary sheets for
levels 4, 5 and 6). Every row was run through the system — the authority
upload, then the Results Summary — on a scratch database:

| Class | Students | Match |
|---|---|---|
| Level 4 2024/25, first sitting · after supplementary | 41 · 30 | 41 · 30 |
| Level 5 2024/25, first sitting · after supplementary | 29 · 23 | 29 · 23 |
| Level 6 2024/25, first sitting · after supplementary | 35 · 4 | 35 · 4 |
| Level 4 2023/24 (repeat sittings) · level 5 2022/23 | 4 · 1 | 4 · 1 |
| Level 5 2023/24 repeat class | 21 | 20 (see Q30) |
| **All** | **188** | **187** |

Your own sheets agree too: level 4 semester 1 (29 of 29) and level 5
semester 1 (Happiness Mbua: 1.3, discontinued, as NACTVET printed it).

```text
  every module's grade ── at its own letter: B*** is 3, D* is 1, a passed supp is C
            │             GPA = Σ points × credits ÷ Σ credits, CUT to one decimal
            ▼             (2.89 → 2.8; 1.99 → 1.9)
   1. discontinued by the authority (FAIL), or a REPEATED module failed
      again — starred or not: a repeat sitting has no supplementary ──► DISCO
   2. a result missing, withheld (*W*) or nullified (*N*)          ──► wait
   3. GPA below 2.0                                                ──► DISCO
   4. more than half of the first sittings failed (field counts)   ──► REPEAT them
   5. a starred first sitting (half or fewer)                      ──► SUPP
   6. a module failed outright, or after its supplementary         ──► REPEAT it
   7. otherwise                                 ──► PASS: First / Second / Pass
                                                    (level 6: First 4.4+, Upper
                                                    Second 3.5+, Lower Second
                                                    2.7+, Pass 2.0+)

  SUPPLEMENTARY SHEET (exam numbers …RSM…; the first sitting's are …TES…)
     uploaded as "Supplementary sitting": only modules that were waiting for a
     supplementary are taken, into their own field (the first sitting's grade
     is kept). Plain grade (NACTVET writes C) = passed; still starred = failed
     again → REPEAT, counted at its letter. Then the same steps 1–7: below 2.0
     after the supplementary is DISCO (NS3970/0014/2021: F* again, 1.7, FAIL).
```

What changed from the rules of 3 October (change log): the GPA no
longer counts a waiting supplementary as C, it is cut rather than rounded, it
is checked **before** "more than half failed" (level 5 2024/25: three
students with 3+ fails and GPA 1.9 were FAIL, two with 2.2–2.4 were REPEAT),
and a starred repeat sitting is a discontinuation at once.

Code: `progression.decide` — the one decision the semester review, the
Results Summary, the student's portal and the declarations screen all read;
`progression.semester_gpa` / `cut_gpa`; `grading.parse_authority_supp_grade`;
`StudentResult.authority_supp_grade` (migration 0059); the upload's `sitting`.
Tests: `test_authority_results.StarredGradesAtFaceValueTests` and the rewritten
`MoreThanHalfFailedTests`, each tied to a sheet row where there is one.

---

## Change log

| Date | Change |
|---|---|
| 2026-10-02 | First draft. Two-track design; PST unchanged; questions in `questions.md` |
| 2026-10-02 | Part 2, roles and offices, from the organisational chart and your decisions: DPARC and DPFPA added; exam officer gets its own role and no longer advances a semester or manages accounts; ICT officer manages accounts; postponement stays with the Principal, DPARC notified; estate reports to DPFPA; senior roles by appointment and acceptance letter; marks entered per module, not per office. Questions Q20–Q24 added |
| 2026-10-02 | Part 2 built: exam officer, DPARC, DPFPA and ICT officer roles; marks by module; review confirmed and year advanced by the Principal or DPARC; executive summary; role-change log; postponement notices. Expenses rule recorded: the Principal **or** DPFPA approves, one is enough. Appointment letters still to come |
| 2026-10-02 | The ICT officer is you: the ICT Officer role now makes the account a Django superuser (every table in `/admin/`, a Django Admin link in the menu) and stays the ICT officer inside the app. No sealed envelope account; `createsuperuser` on the server is the way back |
| 2026-10-02 | Exam officer trimmed to examinations: no User Accounts, Departments, module or enrollment management, Services or Sick Records; reads this semester's modules and class lists. Those are the departments' work (`roles.runs_departments`: Principal, DPARC, HOD); sick records go to the HOD. A superuser that holds an office is that office, so the old shared admin account is the exam officer after the deploy |
| 2026-10-02 | Executive Summary split into two menu items, Finance and Inventory, with charts. The Principal reads college property too (read-only, like DPFPA); the accountant sees Finance only |
| 2026-10-03 | Part 1 rewritten from your answers and the VETA research: every new course is a VETA course (short: months, college certificate; long: NVA Level I–III, January); pass / fail per module, no GPA; repeat failed modules; college registration number (NVA number only for VETA's exam); all four desks; hostel allowed; course coordinator. Build cut by date: admit and bill by 19 Oct (cosmetology), results by the first tests, completion by January, long courses by their first intake. Part 2: records, ICT and secretary report to DPFPA; Training Coordinator is the course coordinator; letters from a template; the Board and Director stay out |
| 2026-10-03 | Built: the VETA skeleton (step A — course definition with its structure, course fees and instalments by the accountant, the VETA ID pattern and intakes by the admission office, the four desks with no documents required, billing, the student's portal, the course coordinator) and the expenses register (Part 3). 815 tests |
| 2026-10-03 | Cosmetology fees from the college's form loaded by `seed_veta_courses` (same CRDB accounts as PST, VETA's own charge types); instalments can be marked *before admission* and admission waits for them (migration 0057). 820 tests |
| 2026-10-03 | PST results rule: failing more than half of a semester's modules (field included; first sittings only) means repeating the failed modules with no supplementary, whatever the GPA; half or fewer go to supplementaries. `progression.fails_more_than_half`, used by the review, the Results Summary, the portal statement and the declarations screen. 827 tests |
| 2026-10-03 | PST results rule: failing a module again at its repeat sitting (FAIL, or failed after its supplementary) is a discontinuation, whatever the GPA; it replaces "owed until passed". `progression.failed_repeat_sittings`, in the same four places. 830 tests |
| 2026-10-03 | PST results: GPA at face value (F is F, C is C) except a supplementary candidate's pending papers count as C (`progression.semester_gpa`); supplementaries only with that GPA ≥ 2.0, otherwise discontinued at once; a discontinuation closes the repeats owed for that semester, so a readmitted semester is sat afresh as first sittings. 834 tests |
| 2026-10-03 | Part 4: Form Four index number (PST, required by records), gender required at the VETA desk too, students' health records on verified medical certificates, the Dean of Students role reading them; a repeater pays 200,000 per module and a 280,000 direct cost once a year. Part 3: expenses are requested, may be queried and answered, and come from office funds or petty cash (a float with top-ups). Migration 0058. 864 tests |
| 2026-10-08 | Part 5: PST results checked against NACTVET's 2024/25 sheets in `diagrams/` (187 of 188 students match through the system). A starred grade counts at its own letter, the GPA is cut to one decimal and checked before "more than half failed"; a starred repeat sitting is a discontinuation at once; supplementary sheets upload as their own sitting (`authority_supp_grade`, migration 0059); level 6 classes First 4.4+ / Upper 3.5+ / Lower 2.7+ / Pass. One shared `progression.decide`. Field modules are "Pharmacy Practice", 5 credits. 869 tests |
| 2026-10-08 | Security review fixed (report in `diagrams/security-review.md`, kept out of git). The ICT officer is the password desk for everybody, the Principal and students included: resets recorded (`PasswordReset`) and shown to the holder; tutors no longer set student passwords; sign-in lockout (`SignInFailure`); API session-only with CSRF for students and throttles (`attendance/security.py`); Django 5.2 LTS; libraries self-hosted. Migration 0060. 896 tests |
| 2026-10-08 | Passwords are set or reset by the ICT officer and the Principal only, and only they see a temporary one: admission (PST and VETA) hands out none; the password desk issues every new student their own in one step (`passwords.issue_missing_student_passwords`). 896 tests |
| 2026-10-12 | Returning students from before the system (no results in it): readmission, repeat (with the module codes owed) or postponement return, entered from last year's papers by the exam office / Principal / DPARC (`admissions.record_returning_student`, Returning Students page, Excel sheet). They join admission's Expected back list and are billed as the year-end would have billed them. 906 tests |
| 2026-10-12 | A postponed student who was repeating comes back as a repeater for the modules they owe (`admissions.is_repeat_only` now counts a postponed standing with open repeats) — this was also wrong for the system's own postponements, which came back billed a full year. Postponements entered from paper may carry the modules. New entrants at level 5 or 6 checked: first-year bill with one-time charges, that level's modules. 912 tests |
| 2026-10-12 | Holiday payments without invoices: the accountant names the account the slip went into and the payment settles that account's charges only; the same bank reference twice needs confirming (one slip for two students), a reversed payment's reference is free again. Authority grade template: NA for modules not sat, a Sitting column for repeat sittings; upload reports grades for modules not sat instead of refusing them. Fixed: `generate_charges` was not in a transaction (its decorator had slipped onto the helper above it, since August). 920 tests |
| 2026-10-12 | Two result sheets, as NACTVET keeps them: continuing (first sittings) and repeaters, whose repeat grades complete the semester they failed (`progression.complete_with_repeats`, `repeaters_summary`; the portal statement for that semester shows the repeat in the failed module's place, GPA and remark recomputed). The continuing summary lists students with first sittings, its remark still the whole semester's as the review decides. Template: a Repeaters sheet with last time's grades filled in. GPA shown to one decimal. 925 tests |
| 2026-10-12 | Repeaters apart in CA and final marks as well: the Results page's Continuing / Repeaters switch drives entry, approval, printing and every download (`sitting=` on the CA, sign-off, final and field sheets). Checked end to end: a 2024/25 module owed, postponed 2025/26, back 2026/27 — billed as a repeater, on the repeaters' sheet, completing 2024/25. 928 tests |
| 2026-10-12 | Staff see repeaters for what they are: tagged **Repeat** and listed last under their own heading on the attendance roster and a module's student list, and on CA entry, which says how many are on the other sheet. Admissions ▸ Class lists: a level's students this semester (registrations; enrollments for those from before registrations), NACTVET number, First sitting / Repeater with the repeated modules, provisional while the admission window is open, Excel (`attendance/classlists.py`). CAT exam numbers from the same list: the examination officer sets the year's format (`CatNumberFormat`: `{COLLEGE}` `{PROG}` `{LEVEL}` `{SEM}` `{CAT}` `{YEAR}` `{YY}` `{SEQ}`, carried into the next year) and gives CAT 1 / CAT 2 numbers per level; each level's run starts at 1, numbers are kept once given and late students continue the run (`CatExamNumber`, migration 0061). 944 tests |
| 2026-10-12 | The printed class attendance register, to the college's template (BPHIST crest, landscape Letter, measured off the template): the tutor of a module (or the academic offices) declares its sessions — kept as `Module.register_sessions`, migration 0062 — and downloads a Word register with exactly that many: cover (department, academic year, NTA level, module code and name), signing pages of five sessions with the class's names and NACTVET numbers (35 a page, headings repeated; first sittings by name, then repeaters marked), summary of sessions, signatures of class representative, facilitator and HoD (`attendance/registers.py`). Take Attendance warns while the admission window is open. 956 tests |
| 2026-10-12 | The college's new name, Blue Pharma Institute of Science and Technology (BPHIST), wherever Blue Pharma College of Health was used: results and field sheet headings, class list and CAT Excel headings, the student form page, `CollegeProfile` defaults and the stored profile (migration 0063 — only while it still holds the old name; invoice terms' "BPHACOH refund policy" too), the fee-structure and evaluation-form seeds, form titles, introductions and print notes, and the sections and questions of forms nobody has answered (the seed's own rule), CAT number initials BLPCH → BPHIST for a year with no CAT number issued. Left as they are: bank account holder names (the bank's record), email and website, the college ID code BPH (fits both names), migration 0028 and NACTVET's sheets. 956 tests |
| 2026-10-13 | One menu item, one view (your rule, now the house rule): every sidebar item for every office checked in the browser. Results showed the same six cards in each item because `body.is-admin .admin-only.card {display:block !important}` beat the switcher; role visibility and menu-item visibility are now separate (`.sec-off`, `data-res-view`), Authority Grades and Entry Windows (exam office only) are items of their own. Eligibility ▸ Final Eligibility its own view; Executive Summary ▸ Finance lost a stale spending card. One Students menu (Find a student, Enrollments, College ID import) replaces Students + Student Records; Upload / Register removed — new students only through Admissions, and the API refuses a registration number never admitted (`serializers.known_student`). Portal: password alert at the top of the dashboard; Request a Service as options opening one at a time; My Profile as options (personal, academic, next of kin, documents, password); a repeater's Enrolled Modules, attendance, CA, GPA and eligibility leave out last time's sittings, which only complete the statement. 968 tests |
| 2026-10-16 | Service requests one at a time (your choice of three): `evaluations.submit` refuses a request-kind form while the student's earlier request for it is unanswered (with the secretary or forwarded), under a row lock on the profile; approved or declined frees it. `forms_for_student` marks it `waiting`, and the Request a Service tile says so. Forms set to one response each were already safe (unique receipt). 973 tests |
| 2026-10-16 | Health: a student reports a health problem from the portal with the hospital's document (`HealthReport`: condition, hospital, the date on it, the file filed as a medical form); records checks the original — Record verifies the document and writes the `HealthRecord`, the Dean is told; Not accepted needs a reason the student reads. Every record now carries `facility` and `issued_on` (a valid document names its hospital and a date not in the future). Health records is an item of the Students menu. The Dean of Students is notified of service requests (made, answered), postponements (asked, decided) and hostel applications (made, decided — the accountant now told of new ones), and reads Service Requests and Postponements. Inventory reviewed; a batch split by a transfer or repair now keeps its item type. Migration 0064. 983 tests |
| 2026-10-17 | College property through its life (your rules, all of Q35): `Asset.status` (in use, under repair, missing, disposal proposed, disposed), `custodian` (a staff account; `responsible_office` kept as its name), value fields (purchase date, unit cost, supplier, serial, funding, warranty, the expense that bought it). `attendance/inventory.py` holds the rules: only an asset in use moves, is repaired or proposed for disposal; part of a batch splits to its own tag. Disposal: proposed (estate) → approved / rejected with a reason (DPFPA, notified both ways) → carried out (estate: date, method, reference, proceeds) → off the register, the figures and the lists. Repairs: reported (under repair) → started → completed as repaired (condition good) or beyond repair (unserviceable). Inspections: opened open; results recorded while open (a defect needs its condition, relocated where it was found); closing requires every item checked and applies them (condition, missing, a transfer), then locks. No edit or delete on transfers, repairs, disposals, inspection results or assets; corrections to an asset leave location, quantity, custodian, condition and status alone. Handovers move everything one member of staff holds (older uncustodied records item by item). Import template version 2 (custodian from a staff list, value columns). Lab Technician role (open) and the laboratory's stock book (`LabItem`, `LabStockMovement`: received, issued, written off — never edited; balance, low stock, expiry), read by DPFPA and the Principal under Executive Summary. Migration 0065. 1001 tests |
| 2026-10-17 | Decided (not built): **option B** for a year whose results are late — opening the year is separated from deciding students; students without results are placed provisionally at the next level, settled from a "results now in" list after the NACTVET upload (fees reversed for those who do not progress), with a cut-off date; students may finish after Level 4 or 5 with that level's award through the finishing route; continuing students who never report are dealt with when the window closes. Questions Q42–Q52 in `questions.md` section N. |
| 2026-10-18 | Option B built (your answers to Q42–Q52): the advance places a student whose results are not out and who has failed nothing so far provisionally (`progression.provisional_placement`; a failure already in or Level 6 waits; only an unconfirmed decision blocks). Provisional places: the examination officer's cut-off (`Semester.provisional_cutoff`), the offices warned after it, results refreshed after each upload, passes settled together by the Principal / DPARC; a failure is recorded against the review's own semester and `call_off_later` cancels the later registrations and applications and resets their fees. Provisional students are tagged everywhere and not eligible for the end-of-semester exam. Finishing after NTA Level 5 (`ExitRequest`: portal only, the Principal decides, no refund, return any later year); Did not report (admission, from three months after the window opened; standing `not_reported`). Migration 0066. 1023 tests |
| 2026-10-18 | The paper Student Enrolment Form compared with the record: address, guarantor / sponsor and phone and the schools before (`StudentEducation`: O-level, A-level, college with years and index number) added; names stay one field. Passport photo (`StudentProfile.passport`): student or records / Principal, resized in the browser, JPG / PNG by content up to 512 KB, the old file deleted, served only through the app (`/api/my-passport/`, `/api/student-records/<id>/passport/`); shown on the portal dashboard (small), profile (large), the staff record and the application. Provisional students' applications say so (results line and tag). Migration 0067. 1034 tests |
| 2026-10-18 | Result sheets (your rule): the continuing sheet is only for students sitting every module of their programme at the level that semester, all as first sittings — "every module" being those the class sits as first sittings; anybody missing a module is on the repeaters' sheet. One rule, `progression.sheets_for`, behind the authority grade templates and the Results Summary. Templates: modules in code order, the Sitting column dropped (the sheet is chosen at download, the sitting at upload). Returning Students record the college ID the student already carries. |
| 2026-10-19 | One advance for everybody (your rule, changing Q43): every student whose review is not confirmed is placed provisionally, a failure so far included; only Level 6 finishing waits; *Send continuing students to finance* marks them provisional too. The server's advance failed with a 500 on a review confirmed without a programme: `progression.programme_and_level` (review → standing → registration → modules → the only programme offering the level) fills it; anyone still unplaceable is named in the preview; a refusal inside the advance is a 400 and changes nothing. Server errors now log their traceback. 1046 tests |
| 2026-10-19 | A new programme did not show at New application (the page kept the programme list it loaded first), and sending the form without one answered 500 (`filter(id='')`). Programme lists now reload where a programme is chosen; `views.pk_or_none` reads every id a request sends, so a blank or wrong one gets the view's message, never a server error. 1048 tests |
| 2026-10-19 | Items checked only (your rule): an item is *billed* (missing → on the bill, never a hold — TPH book) or *checked only* (certificates, signed forms — never billed; missing holds the student at the admission desk). `AdmissionRequirement.kind`, migration 0068 (items with no charge type become check-only); check-only items are marked by the desk holding the application; `admissions.items_holding` / `sync_item_hold`; `admit` refuses while one is missing or unchecked; Has it / Waive lifts only the items' own hold. 1056 tests |
| 2026-10-19 | Who checks items (your rule): records checks none; finance checks only the billed items (TPH book — missing is charged) and the admission desk sees whether that charge is paid; checked-only items are checked at the admission desk only, the last stage, before admitting (`admissions.CHECK_DESKS`). 1057 tests |
| 2026-10-19 | Counted and typed items (your rule): an item may be counted over the year (`AdmissionRequirement.quantity`; rim paper 2 — two clears the year, one is asked for again next semester, never billed) or typed in when checked (`value_label`; the NACTVET admission code, kept on the student's details via `admissions.typed_items`). `RequirementCheck.count` / `value`, migration 0069. Items check opens to the admission office for checked-only items. 1063 tests |
| 2026-10-19 | VETA students' identity card numbers (your rule): a series apart from PST's. The VETA pattern has its own counter and starts-at (migration 0070), must differ from PST's pattern, and is carried into each new year (`id_format_for` dropped it before). Blank still means "numbered like PST". 1066 tests |
| 2026-10-20 | First day of use, batch 2 (your list of 13 and the statuses). Sessions: five minutes unused signs out (IdleTimeoutMiddleware, keep-alive while typing); staff passwords to the students' rule at the next sign-in; CSRF failures explained and logged, sign-in never cached. Offices: no self-claiming of modules — the HOD gives their department's modules to their department's staff and sees no accounts. Money: invoices from the application window; department / programme filters and breakdowns (`finance.programme_by_profile`; a VETA student under their intake's course). Records: next of kin and who pays compulsory at every records desk; the VETA admission form's details on the shared form; every correction logged (`StudentDetailChange`). VETA: card numbers counted per course (`CollegeIdCounter`); items asked of VETA, checked at its admission desk. Statuses: FAIL for academic failure (readmission); Discontinued now disciplinary (Principal; back as new); Absconded (back after two years, as new); Suspended (place and fees kept, no exams, ends on its date); Deceased, Terminated (never again; portal closed); postponed over two years and finished after Level 5 come back as new — one-time charges again, same ID (`admissions.returns_as_new`). Migrations 0071–0073. 1102 tests |
| 2026-10-20 | PST is one programme across Levels 4–6 (the three certificates are NACTVET's awards per level, not programmes): the year end reads the next level within the programme, so one-per-level programmes would finish every Level 4 student. `manage.py merge_programmes` merges them (dry run first; fee cells folded; issued college IDs kept). Names in three parts (`first_name`, `middle_name`, `last_name`; `name` stays the whole, written from them; existing names split, migration 0074). Items for new students only (uniforms, clinical coat for those joining at Level 5/6); own NHIF card through the *Insurance* item — both waive before the invoice. NHIF's New / Continuous students sheets built from the records (Students ▸ NHIF sheets), for students paying the medical fee; marital status, National ID, NHIF card No. kept. New application kind split, at the admission officer's word: *First / Second / Third year* (all `kind=new`; `year_of_study` gives the level — `Programme.level_for_year`; a level the programme does not run is refused) apart from *Continuing* / *Readmission*; `Application.kind_label` names them on every desk ("Second year (new) — joins at NTA Level 5"). On the server PST04/05/06 merge into the existing PST; the merge reports each programme's department and active state and any fee clash (the stay-programme's amount wins), and keeps the merged programme active. 1122 tests |
| 2026-10-20 | Idle sign-out made thirty minutes (five was too short): `IDLE_TIMEOUT_SECONDS` default 1800; the sign-out messages read the setting (`middleware.idle_message`) instead of saying 5 minutes. 1122 tests |
| 2026-10-20 | A PST student signs in with the college ID as well as the registration number (`passwords.student_for_sign_in` maps a college ID to its profile's number): the password slip said so, but only VETA students could, and a first year's temporary number changes when NACTVET's arrives. 1123 tests |
| 2026-10-20 | Three students admitted on the server showed no *Issue passwords* button: admitted under PST04/05/06 while the semester's modules sat under PST, admission (`enroll_registration`, same programme only) enrolled them in nothing, and the password desk counts enrolled students only. The desk now names admitted students in no module (`passwords.admitted_without_modules`); `merge_programmes` ends by enrolling the active year's registrations of the merged programme (idempotent; new charges counted and flagged). 1124 tests |
| 2026-10-20 | The server's check showed the real cause: both students under PST, Level 4's modules with no programme, no Level 5 modules. `enroll_registration` now takes a module with no programme as the level's (as `enroll_module` always did — the two orders end in the same place); `ModuleViewSet.perform_update` enrolls the class when a module's programme, level or semester changes; `manage.py enroll_registered` (dry run / --apply) catches up registrations made before. 1128 tests |
| 2026-10-20 | Admission and finance make a student ours (the user's rule after Anitha's dry run raised a charge). The year's bill is raised once, at the finance desk: `enroll_registration` no longer calls the fee generator or the repeaters' flat cost (the repeat-module charge stays), the `bill_new_enrollment` signal is gone, `finance_generate_charges` and its card are gone. Ways into a module: admission, and a module created or edited for the level (`enroll_module`); the roster add / bulk add / module move are refused, `enroll_registered` and the merge's enrolment removed. A repeater who passed and now studies a full semester 2 is handed to the finance desk (`apply_advance`, `generate_charges(full_year=True)` from the desk). Passwords: kept on the profile too (`reset_student_password`, `profile_for_sign_in`), so an admitted student with no module signs in to the profile portal (programme, bill, details); `_portal_credentials` falls back to it. Money on account: `Payment.bank_account` (migration 0075), `finance.on_account` / `apply_on_account` after the finance desk's billing — same account only; statement and portal show it. Semester pass: `semester_pass.py` — registered, studying, billed, paid up to the semester's first schedule step; HMAC check code; admission prints, the student downloads, staff check at `/semester-pass/check/`. 1138 tests |
| 2026-10-21 | Student pass redesigned: an ID card (85.6 × 54 mm, `@page` at that size; zoomed on screen), a stamp box, no link. *Valid until* from `semester_pass.coverage`: the windows are the due dates of the student's own year bill (`source=structure`, items left out) — raised from their own entry's schedule, so new and continuing never mix; covered through window k → the day before window k+1; all covered by one payment → a year from it; all covered in several → a year from the first window, less a day; an active cleared override with a later date wins. Passes are kept (`StudentPass`, migration 0076: random code, valid_until, basis, source, issued_by); `issue` reuses a pass with the same date; `verify` takes the code alone. `finance.overpayments` + Finance ▸ Payments ▸ Overpayments (excess beyond an invoice's full fee, by invoice or account). After admission the admission office may record an item as *Has it* (PST requirements view, VETA item action) — nothing else. Menus: Admissions = desk, new, VETA applications, Pending, Applications, Expected back, Settings; Students gains pass, class lists, items check, did not report (`STUDENT_SIDE_SECTIONS`). First password = last name in capitals at admission (`passwords.give_first_password`, PST and VETA), change forced; the ICT desk keeps resets. Compulsory evaluations ask only those who studied the period (`evaluations.studied_for`). 1155 tests |
| 2026-10-21 | Design system across every dashboard (the user's palette: primary blue #123F80 for the sidebar, main actions and where you are; light blue #EFF6FF for selection and highlights; white workspace; cool grey #F1F5F9 behind). One layer per template, after the structural CSS and before `@media print` (`index.html`, `student_dashboard.html`; a shared snippet in the sign-in, password, compulsory-form and registration pages; `course_portal.html`, `semester_pass.html`): tokens, white top bar, blue sidebar with rounded rows, 14px cards with soft shadows, tile counters with icons, tables without grid lines, pills for status, help notes as callouts, dark-mode tokens. Desk rows rebuilt (`personCell`, `kindPill`, `deskLabel` pills, `owesCell`); the application window's summary as tiles and its sections as panels. The old #1565c0 replaced everywhere (charts use #1E5BB8, readable in both themes). 1155 tests |
| 2026-10-21 | Student portal pages tidied: Generate Invoice and What I Owe use `.panel-body` / `.panel-actions` / `.btn-row` / `.btn-soft`; the invoice result is `.invoice-msg` with busy / ok / err states (found from the panel, not the button's parent); money through `floatformat:"2g"`. My Profile: every option is panels of `.kv` two-column tables — one per next of kin, school, document and health record (documents and health rendered by script into the same shape); the document upload, health report and password forms are `.kv-form` rows. 1155 tests |
| 2026-10-21 | Next of kin: `NextOfKin.OFFERED` = mother, father, guardian, husband, wife, sister, brother, other relative, other; `OLD` = parent, spouse, sibling — still in `RELATIONSHIP_CHOICES` so old records read (migration 0077 changes no data), refused when written anew (`record_details`: only kept if already there at that position), and listed by `admissions.old_relationships` in `missing_details` (holds a first year at the records details desk; flags a continuing student). The records form offers the new list and shows an old value as *Parent — choose Mother or Father*. 1170 tests |
| 2026-10-21 | Shortfall waivers need DPFPA: `WaiverRequest` (migration 0078: charge, amount to stand waived, reason, pending / approved / refused, who and when). `finance.waiver_needs_approval` = family fee or direct cost; the accountant's `waive` on such a charge creates a request (202) instead of waiving; `finance.decide_waiver` (DPFPA only) applies it on approval; a refusal needs a note; notifications `waiver.requested` / `waiver.decided`. A pending request holds the PST finance desk (`complete_step`) and the VETA one (`desk_blockers`). Screens: *Waive a shortfall* in the application window at finance, *Shortfall waivers* under Waivers & Overrides, *Finance summary ▸ Waiver approvals*. DPFPA's menus split: `exec-nav-group` (Fees & collections, Spending, Waiver approvals) and `exec-inv-nav-group` (College property, Asset disposals, Laboratory stock). Modules: the *Tutor responsible* is chosen from `available_tutors` (an HOD's department's; everybody's for DPARC and the Principal) via `/api/module-tutors/`; `tutor_id` sets the display name and adds the tutor (no longer the HOD); `set_staff_modules` kept for emergencies. 1179 tests |
| 2026-10-22 | An admitted PST student with no module yet signs in to the normal dashboard: `passwords.profile_for_sign_in` now sets `session['portal_profile_id']` and goes to `/student-dashboard/`; `get_logged_student` returns the newest enrollment when one exists, otherwise a `PortalSeat` (name, number, password fields and a module stand-in with the registration's level and semester); `student_login_required`, `portal_signed_in`, the idle middleware and `security.STUDENT_SESSION_KEYS` know the new key; `student_reading` falls back to the record's password date. VETA keeps `/course-portal/`. Idle sign-out message: 'Your session expired. Sign in again.' — the limit is not told. 1179 tests |
| 2026-10-22 | The accountant took the override card ("Clear or hold a student by hand" — a `FinanceOverride`, saved at once) for a waiver. Waivers & Overrides now opens with *Waive a shortfall* (student picker → `loadWaiverFormFor` → the same `waiverFormHtml` / `requestWaiver` as the finance desk → DPFPA), and the override card is titled *Let through or hold — override* with a note that it takes nothing off the bill. 1179 tests |
| 2026-10-22 | The accountant saw two *Students* menus: the college one (Class lists, Items check since 21 Oct) and finance's. Finance's is now *Student accounts* (Balances & Clearance, Extra Charges, Waivers & Overrides, Hostel); `test_no_two_menus_share_a_name` keeps every top-level menu name unique. 1180 tests |
| 2026-10-22 | The accountant's sidebar ordered by use with CSS `order` under `body.accountant-desk` (Admissions, Payments, Student accounts, Finance Overview, Fee Setup, Expenses, Postpone & Finish, Finance summary, Audit Trail) — the markup and the other offices' order unchanged; they land on Admissions ▸ My desk. `finance` taken off the Students menu; *Items check* and *Class lists* buttons for `finance` only under Admissions; `showAdmissionSection` opens the menu the clicked button sits in. 1180 tests |
| 2026-10-22 | "The system is slow." Measured on a seeded 300-student database (Playwright timing every request; query counts per endpoint): `/api/applications/` ran 11 queries a row — `full_name_for` (up to 15 office tables) per step, next of kin three times, education, typed items, `is_veta_only`, residence, three balance sums, DPARC's money check — 3,313 for 300 rows. Now: `_staff_name` memo in the serializer context; `profile__next_of_kin`, `profile__education`, `profile__course_applications` prefetched (`exists()`/`count()` read the cache); `finance.balances_for` / `residences_for` and `admissions.typed_items_for` read the page at once (`_per_page`); `_hides_money` once per response — 20 queries. Modules: counts as `Subquery` annotations (`_count_of`). Staff list: `_offices_of` reads each office table once. Finance students: no prefetch of every charge and enrollment, the level from a values query, `programme_by_profile` asks each later source only about those still without a programme (0.94 → 0.24 s). `roles._holds` remembers an answer for the request (`OfficeLookupMiddleware`, a `ContextVar`), emptied by `post_save`/`post_delete` on every office model. `SlowRequestLogMiddleware` logs requests over `SLOW_REQUEST_MS` (1000) with their query count, path only. `test_query_counts.py` keeps lists flat. Boot cloak: `<body class="booting">` hides the sidebar and main until `boot()` has applied the roles (6 s safety). 1192 tests |
| 2026-10-22 | Waivers by instalment (the accountant's suggestion): `WaiverRequest.charge` optional, plus `academic_year`, `family`, `installment_number` (0079). `finance.shortfalls` lists each tuition / direct-cost instalment still owed on (items left out); `request_shortfall_waiver` takes what is short (≤ owed; one waiting per instalment); `decide_waiver` spreads it over `instalment_charges` (earliest due, then name) — none owed any more → refuse it. `POST /api/waiver-requests/`, `GET /api/waiver-requests/shortfalls/`; rows carry `shortfall`. The form: Category → Instalment that is short → What is short → Why. Old charge-based requests and `/student-charges/<id>/waive/` still work. 1192 tests |
| 2026-10-24 | NACTVET's 2025/26 sheets run through the system on a scratch database (upload, Results Summary): first sittings (MTS1) 90 of 90; older-year sheets (MTS2) are 2025/26 repeat sittings filed under the first year — 19 of 26, the rest repeats not re-sat (red, grade carried) read as failed again; uploaded under the printed year they would overwrite 42 first-sitting grades. Then: `ResultRelease` (migration 0080) — a semester's results shown to a level's students only once the exam office shows them (`POST /api/result-releases/`; `results-summary` returns `visible_to_students`); the data step keeps shown the active year's published (semester, level) pairs and the semesters its repeats complete. The portal reads every year for *End of Semester* (statement only), keyed (year, semester), each statement classed at its own level; a repeat shows only when its own and its origin semester are shown; a withheld repeat keeps back the semester it completes (no grades). `_provisional_notice` at the top of the portal. Withholding takes `profile_ids`; the repeaters' sheet carries `withheld_id`. The exam officer settles provisional students (`provisional_settle_passes`, and `semester-reviews/<id>/confirm/` when the review is provisional and the outcome is the proposed one); overruling and the semester review stay with the Principal / DPARC. A settled student's message waits for the results: `SemesterReview.notify_when_shown` (in 0080), `progression.tell_settled` / `tell_settled_when_shown`, sent by `result_release` when a level is shown. `testkit.show_all_results` for portal tests. The authority grade template's rows sorted by student name (A–Z, case ignored; registration number between equal names), not registration number. 1210 tests |
| 2026-10-24 | Server 500 on `POST /api/results/authority-grades/` when provisional students' results came in: `Notification` used in `authority_grades` (since 18 Oct) but never imported in views.py. Grades were already saved (the upload's own `transaction.atomic` block commits before the notice; no `ATOMIC_REQUESTS`). Imported; `test_provisional.SettlingTests.test_uploading_their_grades_tells_the_offices` goes through the endpoint (it fails with the server's NameError without the fix). An AST scan of attendance/ for names read but never bound found nothing else. 1210 tests |
| 2026-10-24 | Clear uploaded grades (your request): `progression.clear_authority_grades(semester, level, sheet, sitting, reason, actor, dry_run)` behind `POST /api/results/authority-grades/clear/` (exam office) and *Clear uploaded grades…* on Authority Grades. Scope = the sheet as `sheets_for` draws it; first sitting → `authority_grade`/`authority_status` emptied and `final_approved` off; supplementary → `authority_supp_grade` emptied, status back to the first sitting's. Every grade kept in `AuthorityGradeClearing` (migration 0081). Refused while `results_shown`, when a supplementary sits on a first sitting, and when any review on the semester is confirmed (other than provisional); provisional reviews refreshed after. The upload's alert now lists `not_sitting` rows by name — filled template cells for modules a student is not enrolled in were saved nowhere and reported only as a count. 1210 tests |
| 2026-10-25 | Earlier years' results (your decision: repeats sat in 2025/26 Sem 2 show under the year they clear, now and from now on). `authority.py`: `save_rows` (the upload's one rule, moved from views: never replaces a different grade; `NS` only on a repeat sitting; a repeater's re-listed modules counted as carried; `progression.link_repeat_origins` first), `import_first_sitting` (an earlier year only — year, semester and modules made if missing, credits from the Modules sheet; record-only enrollments, no password), `import_supplementary`, `import_repeat_sitting` (owed = `decide` of the year's record; a repeat sitting in the semester sat, `repeat_of` the first sitting; carried grades compared, never written). `NS` → `NOT_SAT`: `complete_with_repeats` keeps the year's row (still owed), `decide` reads a lone NS as REPEAT, the portal notes "Not sat at the repeat … — still owed". `ResultCorrection` (0082; 0081 left exactly as committed): made by the exam office, the Principal and DPARC told (your rule, same day: they wait for the exam office's report). Portal's current enrollment ordered by year, semester. The college's sheets: 27 of 27 through the import, NS rule = an unchanged failed grade not shown green (NP0947 and NS4111 changed grades in red count, as NACTVET's GPA shows). 1224 tests |
| 2026-10-25 | Your questions: a blank cell records nothing, and now NA for an enrolled module too (`save_rows`); the portal's `_result_statements` adds an enrolled module with no result to a semester whose results are shown — grade empty, INCOMPLETE — and an INCOMPLETE semester shows no GPA or comment (the template shows only the remark). Corrections are the exam office's alone (`authority.correct_grade`: applied at once, `remove=True` empties a grade, the Principal and DPARC notified; a confirmed review's proposal is refreshed). Re-confirming (`confirm_review`, `prior` ≠ outcome) cancels the open repeats of that semester these results no longer fail and returns the places called off; the exam office may confirm again a review whose results changed since, as they say (`reconfirming`; pending → provisional), the Principal and DPARC told; Semester Review ▸ "Results now differ from the decision" (`differs=1`). `plan_advance`: a Level 4/5 student with no next level in their programme is blocked, never completed (the single-level PST04/PST05 programmes did it before the merge). A correction refreshes the student's review for that semester whatever its state (its GPA and proposal are a stored copy; everything else recomputes from the grades). `decide` returns no GPA for INCOMPLETE (waiting) — NACTVET prints NA; the Results Summary's average leaves them out. 1228 tests |
