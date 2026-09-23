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
(732 passing, including `test_end_to_end`; everything after `b5d8556` is uncommitted).

---

## At a glance (23 September 2026)

### Done

| Area | What it gives the college | Committed? |
|---|---|---|
| Phase 1 | Departments, programmes, fees by programme, college ID import | yes — `e1ec004` |
| Phase 3a | Semester review (propose → confirm), repeats, discontinuation, supplementary rules, year-end advance, module carry-forward, 500-student scale | yes — `f491501`, `0b86e96` |
| Password policy | Six-month passwords, expiry shown on every dashboard, forced change, admin reset | yes — `b5d8556` |
| Phase 2 — admissions | First year: intake → records → finance → admission; continuing: finance → records → admission; NACTVET number, college ID, portal PIN, holds | partly — the base in `b5d8556`, the rest below uncommitted |
| Offices | Each office sees only its own desks and menus; the exam officer is kept out of admissions and records; the Principal sees everything | no |
| Student record | Records keeps all details and next of kin; students see them in My Profile; O-level / A-level certificate uploads | no |
| Required items | TPH book, calculator, rim paper, insurance: defined by admission, checked by finance, a debt and never a hold; semester 2 recheck; the admission desk holds or admits | no |
| Payment schedule | The awamu form as an editable template per year (new / continuing, day / hostel), `seed_payment_schedule`, copy to next year; registering needs the first instalment | no |
| Hostel | Day or hostel stated at the finance desk; students apply from the portal; finance grants or declines | no |
| Phase 3b | Postponement (portal or paper request, the Principal decides, fees reset, credit carried to the return); finishing (statement of every year, finance clears, the Principal declares, archived); standing banner on the portal | no |
| Invoices | Tuition fee, direct costs, and one invoice for each other fee (supplementary, special, repeat module, TPH…) | no |
| Authority results | Upload fixed: D and F are fails, only the chosen semester is written, only the exam office uploads. Results Summary (GPAs, pass / supp / repeat / disco counts, module pass rates). Manual **Withhold** button: the portal shows "Withheld" instead of grades; `*W*` / `*N*` read as words | no |
| Testing and release | End-to-end test of a student's whole year; release guide `ops/guides/06-release-2026-27.md` | no |

### Waiting list, in order

1. **Finance expenses** — the next piece of work: categories, a yearly budget, spending,
   and an income and expense summary. Needs your answer: who approves spending, and up to
   what amount?
2. **Commit and deploy** (you) — follow `ops/guides/06-release-2026-27.md`. Add the new
   untracked files; migrations 0050–0054 aren't committed yet.
3. **Before 12 October, in the app** (no code):
   - the accountant confirms which charges make up the new students' 350,000 second
     instalment;
   - the accountant creates the TPH Book / Calculator / Rim Paper charge types and
     rates; the admission officer adds them, and Insurance, as required items;
   - the admission window, and the admission and records officer accounts;
   - continuing students queued at finance, and existing hostel students marked Hostel.
4. **Later (you said):** withholding results automatically for students with unpaid fees —
   for now it is the manual Withhold button in Results Summary.
5. **Later, not asked for yet:** moving out of the hostel mid-year; a library;
   a read-only fee summary on the Departments page; online applications (the college's
   current system can't be reached from here).
6. **Blocked:** removing the old finance tables needs the production row counts.
7. **Server:** a restore drill on the real server; a fresh Ansible `--check` run.
8. **Open question:** your certificate message stopped at "…ordinary level or advance
   level and". What was the rest?

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
Committed in `f491501` and `0b86e96`. 52 tests (`test_progression.py`), driven in Chromium as
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

- [x] **Postponement requests**: the student asks from the portal (Request a Service ▸
      Postpone my studies), or records writes down a paper request (Postpone & Finish);
      one waiting at a time; the student can withdraw it
- [x] **The Principal decides**; declining needs a reason the student reads
- [x] Semester 1 postpones the whole year (back to semester 1 next year); semester 2 only
      semester 2; a repeating student may postpone and still owes the repeat
- [x] Approval cancels the registration(s) with a reason, withdraws the enrollments (a date,
      never a delete — marks kept), cancels a waiting application, sets the standing to
      Postponed with the return year and semester
- [x] **Fees reset**: the period's charges waived in full (one-time charges stay), audit
      logged; what was paid becomes credit, carried onto the bill on their return by a
      zero-sum "credit carried forward" entry (never counted as income)
- [x] Expected back → "Start their return" opens a continuing application; a semester 2
      return (or readmission) is billed semester 2 only
- [x] **Completion clearance**: Postpone & Finish ▸ Finishing students — the accountant
      prints the statement of every year and every payment, clears when nothing is owed;
      the Principal then declares cleared → archived (portal closed, records kept)
- [x] Standing banner on the portal ("Repeating PST04202", "Postponed — due back: …",
      "Discontinued — readmission to …", "Finished — awaiting …")

## Invoices for any fee

- [x] **Tuition fee** and **Direct costs** invoices, and under **Other fees** one invoice for
      each fee on its own — supplementary exam, special exam, repeat module, TPH book,
      anything raised on request — each offered only to a student billed for it
- [x] Portal Generate Invoice and the accountant's statement both offer them

## Authority results, summary and withholding

- [x] Authority grades: `D` / `F` without stars are fails (a repeat, or a discontinuation below
      GPA 2.0); with stars a supplementary; the upload writes only to the semester chosen, and
      only the examination officer or the Principal uploads (the Head of Department reads)
- [x] Results ▸ Results Summary: per semester and level — each student's GPA and remark,
      counts of pass / supp / repeat / discontinued / waiting, average GPA, each module's
      pass rate
- [x] Withhold / Release per student and semester: the portal says "Results withheld"
      instead of grades and GPA; the results stand; the record of who and why is kept
- [x] **GPA while a supplementary is pending counts it as C** (2 points), whatever the first
      sitting was (B*, C*, D*, F*…), in both marks and authority grades — so nobody shows as
      discontinued before sitting a supplementary they may pass. After it: pass → C, fail →
      repeat (F, 0). The results export leaves the grade point blank until then
- [ ] Automatic withholding for unpaid fees (later)

## Deployment

- [x] `ops/guides/06-release-2026-27.md`: tests, commit (with the new files), rehearsal on
      a copy, deploy, the two seed commands, setup in the app, live checks
- [ ] Deploy (you)

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
- [x] **Who works admissions**: the admission, records and finance offices and the
      Principal. The examination officer sees neither Admissions nor Student Records — they
      take students over once admission has registered them. Windows, the college ID format
      and the requirement list are the admission officer's (and the Principal's)
- [x] **Records keeps the details**: admission takes a student on with a name, programme and
      level; records takes down phone, gender, date of birth and two next of kin, and cannot
      clear its desk until that is complete. Finance and admission read the details
- [x] **Finance sees the results**: every application shows what the confirmed semester review
      said, the student's standing, the modules they will sit and the bill that follows —
      a year's fees, or the repeat rate per module. A repeater is billed per module at the
      finance desk and admitted onto a repeat registration (failed modules only)
- [x] **Continuing students go through admissions each new year**: the year-end advance no
      longer registers anyone into a new academic year — it opens an application at finance
      at the level the results earned. They pay, records double-checks them, admission
      confirms they are here and registers them. Semester 1 → 2 within a year still carries
      on automatically (they were admitted for the year)
- [x] **Not paying is the only hold**: the finance desk cannot be cleared while charges marked
      "blocks registration" are unpaid, by the college's clearance rule — an accountant's
      override lets a student through. Missing details and missing items never hold a
      continuing student (items are charged instead); a first year still needs their
      details recorded before records can pass them on
- [x] A late supplementary failure also withdraws an application still waiting at admissions
- [x] **Send continuing students to finance** (Admissions ▸ My desk, admission officer or
      Principal): for a year that was opened without the advance, it queues everyone last
      year's confirmed semester 2 review sent on, at the level it gave them. Students
      without a confirmed review are listed, not guessed at; anyone already applied or
      registered is left alone; safe to press twice
- [x] **Each office sees only its own**: records sees the records desks (first-year details,
      continuing double-check) and the admitted; finance sees its desk and the admitted;
      only admission and the Principal see intake, the admission desk and who is due back.
      Enforced by the server (list, detail, desk counts, Find a student, documents)
- [x] Records can still find and update any **existing** student in Student Records ▸ Find a
      student — continuing students included, while they are still at finance
- [x] Student portal ▸ My Profile shows what records keeps: college ID, gender, date of
      birth, phone, programme, level, next of kin, and what is still missing
- [x] Document kinds: **O-level certificate (CSEE)**, **A-level certificate (ACSEE)**, other
      certificate, result slip, birth certificate, identification, medical form, other
- [x] Records and admission **menus**: Admissions ▸ My desk / New application /
      All applications / Expected back / Window & ID numbers; Student Records ▸ Find a
      student / College ID import — each office sees only its own items, and both offices
      land on My desk
- [x] **Items are finance's to check** (TPH book, calculator, rim paper, insurance): at the
      finance desk for first years and continuing students alike; records marks none.
      Missing → added to the bill at the accountant's rate. **Never a hold**: the charge
      types items bill under are left out of every clearance (registration, CATs, finals,
      results), whatever their own flags say
- [x] **Insurance**: the medical fee stays on everybody's published bill; a student with
      their own insurance has it waived when finance marks "has it" (only what is unpaid;
      the accountant's own waivers are left alone). Link the Insurance item to the medical
      fee charge type, checked once a year
- [x] **Admission desk sees what is owed** for items (paid or not, and anything finance
      has not checked) and chooses: admit, or **put on hold** with a reason; "Lift the
      hold" or admitting ends it; held students are marked on the desk list. A student who
      brings the item while held is marked "has it" there, which takes it off the bill
- [x] **Semester 2 items check** (Admissions ▸ Items check, accountant/Principal): the
      registered class with the items due — every-semester items each semester, yearly
      items once a year, once-only items once. Missing is billed to that semester
- [x] **Required items screen**: Admissions ▸ Window & ID numbers ▸ Required items
      (admission officer or Principal) — add, edit, switch off; pick the accountant's charge
      type (shows which levels have a rate this year), how often it is checked, and levels.
      An item already checked on students cannot be deleted, only switched off
- [ ] Items setup to do in the system before 12 Oct: the accountant creates TPH Book /
      Calculator / Rim Paper charge types with rates per level; the admission officer then
      adds the items, with Insurance billed under the medical fee, once a year
- [ ] Online applications — the college's current online system is not reachable from here
- [x] **Admitting issues the portal password**: a readable one (no letters that argue with
      digits), shown to the officer once in its own dialog with the registration and college
      numbers, copyable and printable. The student must change it at first sign-in, a
      returning student keeps the password they already know, and records or admission can
      issue a replacement when one is lost

## Payment schedule and hostel (from the 2026/27 instalment form)

- [x] **Payment schedule** per year for new and for continuing students (Fee Setup ▸
      Payment Schedule): instalments (awamu) with due dates and semesters, the part of each
      fee due at each, day and hostel subtotals, row check against the fee; a fee not
      covered or not adding up falls due on the Fee Structure's own dates
- [x] `seed_payment_schedule --year 2026/2027` loads the form and refuses to save unless
      all 18 subtotals (new/continuing × day/hostel) match
- [ ] **Confirm with the accountant**: which other charges make up the new students'
      second instalment of 350,000 — seeded as research/field fees, the semester II
      national examination and graduation fees (the only later-in-year set that adds up)
- [x] "Start from last year" copies both schedules to the next year, every date a year on
- [x] A continuing student is never billed the one-time charges again (they were being
      billed them whenever last year's fees were not in the system)
- [x] Registration needs the first instalment's registration-blocking charges paid, even
      before its due date (admission runs the week before 19/10)
- [x] **Day or hostel** stated at the finance desk; a first year cannot pass finance without
      it; a continuing student keeps last year's until changed; hostel adds the hostel
      fee's instalments, day takes the unpaid ones off
- [ ] Continuing students already in the hostel have no 2025/26 residence on record, so
      finance must mark them Hostel at the desk this year (next year it carries over)
- [x] **Hostel applications** from the portal (Request a Service ▸ Hostel); finance grants
      or declines (Students ▸ Hostel); a place granted in semester 2 is charged semester 2's
      instalment only
- [ ] Moving out of the hostel mid-year (not asked for yet)

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
- [ ] Certificates: the message about O-level / A-level was cut off after "and" — what else was meant?
