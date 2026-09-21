# EduTrack rebuild — what is done, what is not

The working list for the 2026/27 rebuild driven by [diagrams/flows.md](diagrams/flows.md)
and the rules in it. The college's year starts **12 October 2026**.

Keep it honest: tick an item only when it has been built **and** checked. Where a
thing is half done, say which half.

- `[x]` done and verified — how it was verified is written next to it
- `[~]` partly done — what is missing is written next to it
- `[ ]` not started
- `[!]` blocked — what it is waiting for is written next to it

Tests: `cd backend && PSYCOPG_IMPL=python PYTHONPATH=backend/venv/lib/python3.13/site-packages /usr/bin/python3.13 manage.py test attendance --settings=edutrack.test_settings`
(634 passing; everything since `5aefbc4` is uncommitted).

---

## Phase 1 — departments, programmes, fees by programme, college IDs
Commit `e1ec004`. 44 tests (`test_programmes.py`, `test_departments_api.py`), walked
through in Chromium as exam officer, accountant, HOD and records officer.

- [x] `Department` (head, staff, programmes) and `Programme` (code, levels) + Departments screen
- [x] Records officer and admission officer roles, with their own access
- [x] `SemesterRegistration` — one row per student per semester: programme, level, footing
- [x] Student record: college ID (unique, issued once), phone, gender, date of birth, **two** next of kin
- [x] Fees by programme as well as level; a programme amount overrides the college-wide one
- [x] Exam declarations (supplementary / special / repeat) charged per module through the ledger
  — they used to be written to a table nothing read, so nobody was ever billed
- [x] Billing level fix: charges follow the student's registration, not whichever module came first
- [x] College ID import from Excel (check first, then record; never overwrites a different ID)
- [x] `link_existing_records` command — links old modules and enrollments to a programme, adds only

## Phase 3a — year-end progression
Commits `f86bf22`, `5aefbc4`. 52 tests (`test_progression.py`), driven in Chromium as
records officer and examination officer against a copy of the college at the end of
2025/26, and again mid-semester-2 with a failed supplementary.

- [x] `StudentStanding` — studying / repeating / postponed / discontinued / finished / archived,
      with the level and semester an absent student comes back to
- [x] `StandingChange` — every move, with its reason and who decided it
- [x] `SemesterReview` — the system proposes from the results, the officer confirms;
      an override needs a reason and both are kept
- [x] `OutstandingRepeat` — the module still to be passed, tied to the failed result,
      open across academic years until a repeat sitting passes it
- [x] Repeat sittings: `Student.attempt` / `repeat_of`, billed at the accountant's
      per-module repeat rate, and the failed result is kept untouched as history
- [x] Semester Review screen — **the Principal and the examination officer decide**;
      any admin reads it, the records officer does not (their work is the student
      record itself: keeping, updating and retrieving it)
- [x] Advance restricted to Principal and examination officer (the HOD could press it)
- [x] Advance preview — every student's move and the modules to be carried, before committing
- [x] Advance refuses to run until every student in the closing semester is confirmed
- [x] Module list carried forward into the new year (same modules unless the authority changes them)
- [x] Registered students enrolled automatically, in either order (register first or create the module first)
- [x] A decision re-reads the results when it is taken, so a supplementary marked weeks
      later still acts — and takes an already-promoted student off that registration
- [x] Archived students refused the portal, at the door and mid-session
- [x] A module with marks in it can no longer be deleted (the cascade silently took
      enrollments, attendance and results with it)
- [x] **Failing a supplementary stops the student there** (the authority's rule): their
      enrollments are withdrawn, so they leave class lists, the attendance register, mark
      entry and the eligibility exports — every mark already recorded stays, and
      `?include_withdrawn=1` shows the office who left
- [x] A stopped student is not put in front of the officer to confirm, and does not block
      the next advance
- [x] A repeat semester is billed **the module only** — enrolling a repeating student used
      to raise the whole year's programme fees on top of the repeat rate
- [x] Resuming after a passed repeat registers them at the same level and raises that
      semester's charges in full, as a fresh start
- [x] A repeat sitting starts with **new continuous assessment** (a new enrollment, blank
      result); the failed sitting's marks are untouched
- [x] A supplementary pass counts as a C however high the mark (already in `grading.py`)
- [x] A student who stopped mid-year, or who owes a semester 2 module, is brought back
      and enrolled when the module runs again — the advance sweeps for them
- [x] `seed_progression_demo` management command: nine students, one per decision,
      for practising the year end on a development database (`--reset` undoes it)
- [x] Module lists and the dashboard show **this academic year**, so the carried-forward
      list no longer appears twice after an advance; a semester, a year or `all=1` still
      returns the closed years
- [x] Built for a real class size: measured at 500 students — reading the results 0.9s,
      confirming the year group 3.7s, the preview 0.2s, the advance 4.3s. The screen leads
      with counts and a per-class breakdown, defaults to "still to confirm", is paginated
      and searchable, and the preview lists 25 moves rather than 500

## Password policy
Built 2026-09-21 (`attendance/passwords.py`, middleware, migration 0049). 17 tests in
`test_passwords.py`, driven in Chromium for a warning, an expiry, a reset and a lockout.

- [x] Every password lasts **six months**, staff and students alike, measured from the day
      it was set (`PASSWORD_MAX_AGE_DAYS`, set in the environment)
- [x] Both dashboards say when it runs out — a strip on the staff pages and a notice on the
      student's profile — from a fortnight out (`PASSWORD_WARN_DAYS`)
- [x] Once expired, a member of staff can reach **only** the change-password page until they
      set a new one; a student is sent to the portal's own change screen at sign-in
- [x] A fortnight's grace after expiry (`PASSWORD_GRACE_DAYS`); past it the account stops
      opening and says **"See the administrator for a reset"**
- [x] The office resets it: `POST /api/staff-accounts/<id>/reset-password/` for staff
      (Principal or examination officer only), and the admissions screen's
      "New portal password" for students. What is issued is temporary — the holder picks
      their own at the next sign-in, and the six months start again from then
- [x] Existing accounts start their six months from the day the policy arrives, so nobody
      is locked out by the deployment itself

## Phase 3b — postponement and finishing
Not started. Needed soon after the year opens, not on day one.

- [ ] **Student requests postponement from the Services page** (your idea) — through the
      existing request workflow, so the secretary forwards and only the **Principal** decides
- [ ] Records officer can enter a paper postponement request for the Principal to approve
- [ ] `Postponement` record: semester postponed, scope (semester 1 = the whole year,
      semester 2 = returns for semester 2), reason, approver, return year and semester
- [ ] A repeating student may postpone too
- [ ] **Fees reset**: charges for the postponed period reversed (audit-logged, never deleted);
      money already paid stays on the ledger as credit for the return
- [ ] Completion clearance: accountant checks the balance and prints the statement, then the
      Principal declares cleared, which archives the student
- [~] The statement for clearance — `finance_statement` exists but covers **one academic year**;
      clearance needs **every payment since the student started**
- [ ] Standing shown in the student portal ("Repeating PST04202", "Due back: Level 5, Semester 2")

## Phase 2 — admission workflow
**The one dated 12 October.** Engine, API and screen built (`attendance/admissions.py`,
migration 0047, 36 tests in `test_admissions.py`), driven in Chromium through all four
desks as records officer, accountant, admission officer and examination officer.

- [x] Admission application with three types: **first year**, **continuing**, **readmission**
- [x] The desks in order, each stamped with who did it, when and what they said:
  - first year: **intake → records → finance → admission** — they begin and end at the
    admission office, and their NACTVET number is written in at intake
  - continuing / readmission: finance → records → admission
- [x] **Only finance bills.** Intake and records handle no money; charges are raised when
  the application reaches the finance desk
- [x] A first year with no NACTVET number yet is held under a temporary one of the
  college's own; writing in the authority's number moves their record and every
  enrollment onto it at once, and a number belonging to somebody else is refused
- [x] **Student documents**: certificates, result slips, birth certificates and the rest,
  kept against the person. The records desk scans them in at the counter and marks them
  seen; **a student uploads their own from the portal**. Downloads are gated — a student
  reaches only their own, and a signed-out stranger reaches none
- [x] Each desk is its own office — the accountant clears finance, records clears records,
      the admission officer admits; the Principal and exam officer cover every desk, the
      HOD none of them
- [x] Send back to an earlier desk (needs a reason) and refuse (registers nobody)
- [x] Charges raised on the way **into** finance, so there is something to pay before the
      desk can be cleared; a first year is billed the one-time charges too
- [x] Admitting is the one moment: college ID issued, registration created, modules
      enrolled, standing set to studying — nothing exists before it
- [x] Admission window per semester, with the screens saying when it is closed
- [x] College ID format and counter set by the admission officer — pattern, college code,
      width, where the year starts; numbers in use skipped; next year inherits the shape;
      **a readmitted student keeps the number they already had**
- [x] `AdmissionRequirement` + per-application checks: has it / missing (charged at the
      accountant's rate) / waived, re-checked each semester, and the desk is told when
      nothing could be charged because no rate is set
- [x] Due-back list wired to Phase 3a — the discontinued and postponed students due this
      semester, with "Start readmission"
- [x] Student record lookup by name, registration or college ID, so a second record is
      not opened for somebody already on file
- [x] NACTVET number is optional until the authority issues it — the number is obtained at
      the intake desk, which is what that desk is for
- [ ] Records and admission officer **dashboards** (the queue counts exist; the landing
      pages do not)
- [ ] Online applications — the college's current online system is not reachable from here
- [x] **Admitting issues the portal password**: a readable one (no letters that argue with
      digits), shown to the officer once in its own dialog with the registration and college
      numbers, copyable and printable. The student must change it at first sign-in, a
      returning student keeps the password they already know, and records or admission can
      issue a replacement when one is lost

## Finance, still to come

- [ ] Expenses: the accountant defines categories and a budget at the start of the year or
      mid-year; request → department approval → finance review → payment → recorded
- [ ] Income and expense summary for the year (`flows.md` → `expense workflow`)
- [ ] Library: none exists. Issuing and returning books, staff borrow too, only lost items charged
- [!] Remove the old finance tables and routes (`PaymentCategory`, `StudentFinanceObligation`,
      `StudentPayment`, `StudentFinanceClearance`, the FinanceStudent routes) —
      **waiting on the production row counts**, because dropping them is destructive
- [ ] Read-only fee summary on the Departments page (offered, never built)

## Server and operations

- [x] Backup, restore and deploy scripts, systemd timer, guides — `ops/`, commits `e88cd35`, `be00d5b`
- [x] Backups running on the VPS to Google Drive — **you reported this; I have not seen it run**
- [ ] Restore drill on the real server (`ops/backup/restore.sh --drill`) — the only way to know
      the backups are restorable
- [~] Ansible playbook — a `--check` run came back clean before the last template and firewall
      changes; **no clean dry run since, and it has never been run for real**
- [ ] Contabo snapshots: deliberately **not used**, at your instruction

## Decisions on record

The rules the system now follows are yours, not inferred. The full list is in
[diagrams/flows.md](diagrams/flows.md) and in my project memory; the ones that shaped
Phase 3a:

- Everything passed → up a level (4→5, 5→6). Level 6 clear → finished.
- GPA below 2.0 → discontinued, back by readmission into the **exact semester** failed,
  billed as a fresh student, studying all that semester's modules.
- Failed after the supplementary, GPA 2.0 or better → repeats **only that module**, at the
  same level, until it is passed. Level 6 included.
- The authority's sequence: fail the sitting → supplementary; **pass it and it counts as a
  C** however high the mark, and you carry on; **fail it → repeat, and you stop there**.
  You wait for the module to be taught again, sit it with **new continuous assessment**,
  and pay the **repeat rate for that module and nothing else**. Pass it and you continue
  into the next semester or level — whichever the failed module's semester makes it —
  billed that semester **in full, as a fresh start**.
- Semester 1 supplementaries are marked **during** semester 2, so until the mark arrives
  the student carries on and pays as normal, and their semester 1 outcome stays *pending*
  ("provisional" lets the advance proceed). If the mark then fails them, they stop at that
  point: the registration is cancelled, they come off the class lists, and what they had
  already done stays in the record.
- New results replace the old for GPA and transcript; the failed result is **kept**.
- Postponement is the Principal's to approve. Semester 1 = the whole year; semester 2 =
  returns for semester 2. All fees reset.
- "Removed from the system" after clearance means **archived**: portal closed, every record kept.
- Discontinued and postponed students keep the portal (results, fees, requests).
- Modules are the same every year unless the authority adds or removes one.

## Open questions

- [ ] Library: what has to be tracked beyond issuing, returning and charging for losses?
- [ ] Expenses: who approves what, and up to what amount?
- [ ] Production row counts for the four old finance tables, so they can be dropped safely.
