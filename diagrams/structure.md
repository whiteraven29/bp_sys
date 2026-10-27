# EduTrack — how the system is built today

**As of:** 2 October 2026 — commit `75cd214` plus the offices change (not yet committed or deployed).
**Read from:** the code in `backend/` and `ops/`. Every box below is something
that exists in the code now. Planned work lives in `design.md`, open questions
in `questions.md`.

When the structure changes, update this file in the same change.

---

## 1. The whole system at a glance

```text
   STAFF (browser)                         STUDENTS (browser / phone)
   index.html — one page app               student_dashboard.html — the portal
        │                                         │
        │  /api/...  (JSON)                        │  /student-dashboard/
        ▼                                         ▼
  ┌───────────────────────────────────────────────────────────────┐
  │                  Django 5  +  Django REST Framework            │
  │                                                                │
  │   edutrack/          settings, top-level URLs                  │
  │   attendance/        the one app — everything lives here       │
  │      models.py       the tables                                │
  │      views.py        every API endpoint and page               │
  │      admissions.py   the desks: intake → records → finance →   │
  │                      admission                                 │
  │      finance.py      charges, schedules, invoices, payments,   │
  │                      clearance, hostel                         │
  │      progression.py  semester review, repeats, year advance,   │
  │                      results summary                           │
  │      classlists.py   a level's class list, CAT exam numbers    │
  │      registers.py    the printed class attendance register     │
  │      inventory.py    college property's life; the lab stock    │
  │      lifecycle.py    postponement, finishing, clearance        │
  │      roles.py        what each office may do (looked up once   │
  │                      a request: OfficeLookupMiddleware)        │
  │      veta.py         VETA courses: intakes, desks, fees        │
  │      expenses.py     the expenses register and its rules       │
  │      veta_views.py   the API for both                          │
  │      grading.py      grades, points, GPA, authority grades     │
  │      passwords.py    six-month passwords (staff and students)  │
  │      evaluations.py  forms and service requests                │
  │      analytics.py    dashboard figures                         │
  └───────────────────────────────┬───────────────────────────────┘
                                  │
                                  ▼
                      PostgreSQL  (edutrack_db)
                      SQLite only in the tests

  SERVER (Contabo VPS)    ops/ — deploy.sh, backups (restic → Google Drive),
                          Ansible, guides 01–06
```

Printable pages (no API): `invoice/<ref>/`, `statement/<profile>/`,
`request/<pk>/`.

---

## 2. Who uses it, and what each office sees

Following the college's organisational chart (`organisational-chart.pdf`).
Every power is defined once, in `attendance/roles.py`.

```text
                              ┌─────────────────────┐
                              │      PRINCIPAL      │ sees everything; appoints senior
                              └──────────┬──────────┘ roles; decides postponements;
                                         │            reads college property;
                                         │            declares finishers cleared;
                                         │            confirms reviews, advances year;
                                         │            finance in summary only
                 ┌───────────────────────┴───────────────────────┐
                 ▼                                               ▼
       ┌───────────────────┐                           ┌───────────────────┐
       │       DPARC       │ all academic areas;       │       DPFPA       │ executive finance
       │                   │ confirms reviews,         │                   │ summary; college
       │                   │ advances the year;        │                   │ property in figures;
       │                   │ reads admissions (no      │                   │ records nothing
       │                   │ money); told of           │                   │
       │                   │ postponements             │                   │
       └─────────┬─────────┘                           └─────────┬─────────┘
     ┌──────────┬┴──────────┬───────────┐               ┌────────┴────────┐
     ▼          ▼           ▼           ▼               ▼                 ▼
 ┌────────┐ ┌──────────┐ ┌─────────┐ ┌─────────┐   ┌────────────┐   ┌──────────┐
 │  HOD   │ │ADMISSION │ │  EXAM   │ │ RECORDS │   │ ACCOUNTANT │   │  ESTATE  │
 │        │ │ OFFICER  │ │ OFFICER │ │ OFFICER │   │            │   │ MANAGER  │
 └───┬────┘ └────┬─────┘ └────┬────┘ └────┬────┘   └─────┬──────┘   └────┬─────┘
 runs its    whole queue, results, windows, own desks,  own desk, fees,  the asset
 department: window & IDs, declarations,   admitted    payments, items, register
 modules,   required      eligibility,     students,   hostel, invoices
 enrollment, items,       authority upload, details
 staff,     admits/holds  withholding;
 forms,                   prepares reviews;
 sick records             reads this semester's
                          modules and class lists
     │
     ▼
  TUTORS: attendance, and CA for the modules they teach (so does any office
  holder who teaches). End-of-semester marks and approvals: exam officer only.

  Reporting to DPFPA, though not on the chart: records officer, ICT officer, secretary.
  ICT OFFICER → staff accounts, passwords, the open roles (tutor, secretary);
                Django /admin/ with every table (the role makes the account a superuser)
  SECRETARY   → service requests
```

The staff page hides menu items with `data-roles` and body classes
(`is-exam-office`, `can-manage-accounts`, …); the API checks the same rules
(`roles.py`, `visible_application_states`, `visible_profiles`). Django's
`is_staff`/`is_superuser` flags only open Django's own admin site, which is the
ICT officer's.

---

## 3. The data, in seven groups

### 3a. The year, the student and their modules (the academic core)

```text
  AcademicYear  "2026/2027"
       │ 1
       │ has exactly two
       ▼ n
  Semester  (number 1 | 2, attendance cutoffs)
       │
       │ every module belongs to ONE semester and ONE level
       ▼
  Module ───────────── ClassLevel  (NTA 4, 5, 6)
   │  code, credits,        ▲
   │  programme (optional)  │
   │                        │
   │ n                 Programme ──── Department
   ▼                   (PST …)        (HOD, staff)
  Student  ◄── one row per person PER MODULE  (an enrollment)
   │  nactvet_reg_no, attempt (normal / repeat), portal PIN
   │
   ├──► StudentResult      CA + final marks, or the authority grade
   │                       (+ authority_supp_grade from the supplementary sheet)
   ├──► AttendanceRecord   per Session
   └──► profile ──► StudentProfile   one row per PERSON
                      nactvet_reg_no (unique), college_id, phone,
                      gender, DOB, form_four_index (PST, unique),
                      address, guarantor / sponsor, passport photo
                      (media/passports/, served only through the app),
                      NextOfKin ×2, StudentEducation (O-level, A-level,
                      college), StudentDocument,
                      HealthRecord ── certificate (a verified medical
                                      StudentDocument); read by the Dean
```

`Student` is an enrollment, not a person. Money, applications and standing hang
off `StudentProfile`; attendance and results hang off `Student`.

**Portal sign-in** looks up `Student` rows by NACTVET number and checks the PIN
stored on them (`views.login_view`). A person with no module enrollment cannot
sign in.

### 3b. Admissions

```text
  AdmissionWindow ── one per Semester (opens / closes)
  CollegeIdFormat ── one per AcademicYear  {COLLEGE}/{PROG}/{YEAR}/{SEQ}
  AdmissionRequirement ── billed: TPH book, calculator, rim paper, insurance
                          (missing → on the bill, never a hold)
                          checked only: certificates, signed forms
                          (missing → held at the admission desk)
       │  each points at a ChargeType; every semester / year / once
       ▼
  RequirementCheck  (profile + semester + requirement: has it / charged)

  Application  (profile, semester, programme, class_level, kind, state)
       ├── ApplicationStep  one row per desk passed
       └── on_hold / hold_reason   set by the admission officer
```

### 3c. Standing and progression

```text
  SemesterRegistration   profile + semester + programme + level + kind
                         (new / continuing / repeating / readmission)
  StudentStanding        where the person is now: active, repeating,
                         postponed, FAIL (academic — readmission), discontinued
                         (disciplinary — back as new), completed, archived,
                         did not report, absconded (ABS — back after two years
                         as new), suspended (until a date), deceased, terminated
                         — with `since` (the two-year rules) and `until`
  StudentDetailChange    every correction to a student's details: was, now,
                         who, when, why
  Programme              one course of study across its levels (PST: 4, 5, 6) —
                         the year end moves a student to the next level of it;
                         never one programme per level; level_for_year(n) /
                         year_of(level) — a new student joins in year 1, 2, 3
  Payment.bank_account   the account the money went into; money beyond the
                         bill is credit on it (finance.on_account)
  StudentProfile.portal_pin_hash   VETA students' password, and every admitted
                         PST student's too (signs in before modules exist)
  NextOfKin.relationship mother | father | guardian | husband | wife | sister |
                         brother | relative | other  (parent / spouse / sibling:
                         old records only, to be corrected)
  WaiverRequest          a shortfall waiver: tuition fee or direct costs, the
                         year and instalment, what is short (or, the first
                         requests, one charge), reason, pending | approved |
                         refused — DPFPA decides
  StudentPass            a pass issued: random code, valid_until, basis,
                         source (office / portal), issued_by
  semester_pass.py       the pass: coverage (windows paid), status, issue, verify by code
  authority.py           NACTVET grades in: the upload's rule, earlier years' sheets
                         (first, supplementary, repeat), corrections
  Application.kind_label  "First year" | "Second year (new) — joins at NTA
                         Level 5" | "Continuing student" | "Readmission"
  Semester.provisional_cutoff   the exam officer's date for late results
  StandingChange         history of every change
  SemesterReview         proposed → confirmed outcome per semester
  OutstandingRepeat      a failed module still to be passed
  Postponement           request → Principal decides
  ExitRequest            finish after Level 5: portal → Principal decides
  CompletionClearance    finance clear → Principal declares cleared
  ResultWithholding      hides one semester's results on the portal
  ResultCorrection       one grade corrected: old and new, reason, NACTVET
                         reference — exam office proposes, DPARC / Principal
  AuthorityGradeClearing uploaded grades taken off to upload a corrected sheet:
                         every grade kept, who, why
  ResultRelease          a semester's results shown to a level's students
                         (hidden until the examination office shows them)
```

### 3d. Money

```text
  BankAccount ◄── ChargeType  (family: tuition | direct | other; is_hostel;
                              for_repeaters: the repeaters' yearly direct cost)
                     │
                     ▼
  FeeStructure  charge × level × year (× programme), period, instalments
     └── FeeInstallment  due dates
  PaymentSchedule (year × new | continuing)
     └── PaymentStep (awamu, due date)
           └── PaymentStepAmount (charge type, amount, hostel or day)
  StudentResidence (profile × year: hostel | day)    HostelApplication

  StudentCharge   one debt: profile, charge type, year, semester (optional),
     │            instalment, amount, due date, source
     ▼
  Invoice ── InvoiceLine          one reference per payment per year
  Payment ── PaymentAllocation ──► StudentCharge
  FinanceOverride (waivers / clearance overrides)   FinanceAuditLog
```

### 3e. Offices

```text
  User ── one profile row per office held (a person may hold several):
          PrincipalProfile · DeputyAcademicProfile (DPARC) · DeputyFinanceProfile (DPFPA)
          HeadOfDepartmentProfile · ExamOfficerProfile · ICTOfficerProfile
          AccountantProfile · AdmissionOfficerProfile · RecordsOfficerProfile
          EstateOfficerProfile · SecretaryProfile · TeacherProfile (tutor)
          TrainingCoordinatorProfile (course coordinator, VETA courses)
          DeanOfStudentsProfile (reads health records)
  RoleChange   who was given / lost which role, by whom, when
  PasswordReset  every password the ICT desk reset (staff or student), by whom
  SignInFailure  failed sign-ins, briefly, for the lockout (shared by all workers)
  attendance/security.py  the API's defaults: session-only auth, CSRF for students,
                          "signed in" by default, throttles, security headers
```

Older finance tables (`PaymentCategory`, `StudentFinanceObligation`,
`StudentPayment`, `StudentFinanceClearance`) are still present and unused. They
are dropped once the production row counts are checked.

### 3f. VETA courses (a skeleton, built 3 October 2026)

```text
  Programme.structure = semester (PST, as before) | short | nva (Level I–III)
     ├── CourseModule        the course's modules (NVA level on long courses)
     ├── CourseFee           per year: charge type (= account), amount, hostel only
     │      └── CourseFeeInstalment   days after the intake starts
     └── Intake              one run: start, end (from the duration), capacity, open
            ├── CourseApplication ── CourseApplicationStep
            │      intake → information → finance → admission → admitted
            ├── CourseEnrollment   one person, one intake, never two at once
            └── StudentCharge.intake   the fees, in the same ledger as PST
  StudentProfile: nva_number; portal password for VETA students
  CollegeIdFormat.veta_pattern: a VETA student's number, same counter
```

### 3g. Expenses

```text
  ExpenseCategory ── ExpenseBudget (category × year)
  Expense: category, year, payee, amount, receipt, source (office | petty cash);
           pending → approved | rejected → paid;  pending ⇄ queried;
           pending | queried → cancelled
  PettyCashTopUp: the float's money in (from a bank account); the float =
           top-ups − petty-cash expenses paid
```

---

## 4. The main flows

### 4a. A first year is admitted

```text
  ADMISSION OFFICER        RECORDS            ACCOUNTANT          ADMISSION OFFICER
  ┌──────────────┐   ┌──────────────┐   ┌──────────────────┐   ┌──────────────────┐
  │   INTAKE     │──►│ INFORMATION  │──►│     FINANCE      │──►│    ADMISSION     │
  │ name, prog,  │   │ gender, DOB, │   │ hostel or day?   │   │ sees items owed  │
  │ level,       │   │ phone, 2 next│   │ items check →    │   │ ┌──────┐ ┌─────┐ │
  │ NACTVET no.  │   │ of kin, docs │   │ missing = debt   │   │ │ADMIT │ │HOLD │ │
  │ (or temp no.)│   │ (O / A level)│   │ charges raised   │   │ └──┬───┘ └─────┘ │
  └──────────────┘   └──────────────┘   │ first instalment │   └────┼─────────────┘
                                        │ must be paid     │        ▼
                                        └──────────────────┘  college ID issued,
                                                              registration made,
                                                              modules enrolled,
                                                              portal PIN issued
```

### 4b. A continuing student starts a new year

```text
  year-end advance (Principal or DPARC)
          │  opens a CONTINUING application for each student
          ▼
     FINANCE ──────────────► RECORDS ──────────────► ADMISSION ──► registered
     items check,            verify / update          admit or hold
     charges raised          details
     (only "not paid" holds; items are debts, never a block)
```

### 4c. A semester, from marks to the next level

```text
  teachers enter CA + final        exam office uploads
  marks (marks path)        OR     authority grades (no marks)
            │                              │
            └──────────────┬───────────────┘
                           ▼
                    StudentResult
                           │  grading.py: points × credits ÷ credits = GPA,
                           │  every grade at its own letter, cut to 1 decimal
                           │  progression.decide: NACTVET's order (design §19)
                           ▼
              Results Summary (pass / supp / repeat / disco)
              two sheets (progression.sheets_for): continuing = every module
              as a first sitting; repeaters = everybody else
                           │  optional: withhold a semester
                           ▼
       Semester Review: prepared by exam office → confirmed by Principal or DPARC
                           │
          ┌────────────────┼─────────────────┬─────────────────┐
          ▼                ▼                 ▼                 ▼
        PASS          SUPPLEMENTARY       REPEAT          DISCONTINUED
     next semester   sits it next sem;   pays per module,  GPA < 2.0;
     / next level    fail → repeat       stays at level    readmission
                           │
                           ▼
            year-end advance — results not out and nothing failed yet:
            PROVISIONAL at the next level (a failure so far, or Level 6: waits)
            → carries modules forward, opens continuing applications
                           │
                           ▼
            late results uploaded → Provisional places (Principal / DPARC):
            pass → place confirmed; fail → a repeat of ITS OWN semester,
            the later place, registration and fees called off
```

### 4d. Money, from fee to clearance

```text
  Fee Structure + Payment Schedule
          │  generate_charges(entry, residence)
          ▼
   StudentCharge × n  ──►  Invoice (tuition / direct / one per other fee)
                                │  student pays at the bank (2 CRDB accounts)
                                ▼
                       Payment recorded by accountant
                                │  allocated to charges, oldest due first
                                ▼
                   balance  ──►  clearance for registration and exams
                                 (items never count)
```

### 4e. Leaving

```text
  POSTPONE   student asks → Principal decides → that period's fees reversed,
             money paid kept as credit → back on the return semester
  FINISH     level 6 passed → accountant clears debts and prints the statement
             → Principal declares cleared → archived (portal closed, records kept)
  EXIT AT 5  student asks on the portal → Principal approves → finished at Level 5
             (NACTVET's award, no refund) → the FINISH route; may return later
  NO-SHOW    admission, from three months after the window opened → place and
             fees called off, standing "did not report"; may return later
```

---

## 5. The student portal

```text
  Dashboard · Fee reminders · Announcements
  Attendance (Sem 1, Sem 2) · CA Results · End of Semester (or "Withheld")
  Enrolled Modules · Exam Eligibility
  What I Owe · Generate Invoice · My Invoices
  Request a Service (Postpone, Finish after Level 5, Hostel, forms) · My Requests · Forms
  My Profile (passport photo — the student uploads it; details, schools and next of
  kin kept by records); the photo shows small on the dashboard
```

---

## 6. Setup commands

| Command | What it does |
|---|---|
| `seed_levels` | NTA levels 4–6 |
| `seed_fee_structure --year` | the published fee structure |
| `seed_payment_schedule --year` | the awamu form; checks its nine subtotals |
| `link_existing_records` | joins older enrollments to profiles |
| `create_admin` | the first admin account |
| `place_left_behind [--apply]` | once: places the Level 4/5 students the 2026/27 year end left behind (single-level PST04/PST05) |
| `call_off_repeat_places [--apply]` | once: calls off the place a settled REPEAT still holds in a semester they owe nothing in (a repeat not sat, settled before 26 Oct 2026) |

Tests: 1248, run as described in `ops/guides/06-release-2026-27.md`.
