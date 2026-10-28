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
(1001 passing, including `test_end_to_end`, `test_offices`, `test_records_health`, `test_security`, `test_returning`, `test_classlists`, `test_registers`, `test_one_view` and `test_inventory_lifecycle`; committed up to `75cd214`, the offices change not committed yet; nothing of this release deployed yet).

---

## At a glance (2 October 2026)

### Done

| Area | What it gives the college | Committed / live? |
|---|---|---|
| Phase 1 | Departments, programmes, fees by programme, college ID import | yes — `e1ec004` |
| Phase 3a | Semester review (propose → confirm), repeats, discontinuation, supplementary rules, year-end advance, module carry-forward, 500-student scale | yes — `f491501`, `0b86e96` |
| Password policy | Six-month passwords, expiry shown on every dashboard, forced change, admin reset | yes — `b5d8556` |
| Phase 2 — admissions | First year: intake → records → finance → admission; continuing: finance → records → admission; NACTVET number, college ID, portal PIN, holds | committed (`b5d8556`, `2b737db`, `75cd214`) · not live |
| Offices | Each office sees only its own desks and menus; the exam officer is kept out of admissions and records; the Principal sees everything | committed · not live |
| Student record | Records keeps all details and next of kin; students see them in My Profile; O-level / A-level certificate uploads | committed · not live |
| Required items | TPH book, calculator, rim paper, insurance: defined by admission, checked by finance, a debt and never a hold; semester 2 recheck; the admission desk holds or admits | committed · not live |
| Payment schedule | The awamu form as an editable template per year (new / continuing, day / hostel), `seed_payment_schedule`, copy to next year; registering needs the first instalment | committed · not live |
| Hostel | Day or hostel stated at the finance desk; students apply from the portal; finance grants or declines | committed · not live |
| Phase 3b | Postponement (portal or paper request, the Principal decides, fees reset, credit carried to the return); finishing (statement of every year, finance clears, the Principal declares, archived); standing banner on the portal | committed · not live |
| Invoices | Tuition fee, direct costs, and one invoice for each other fee (supplementary, special, repeat module, TPH…) | committed · not live |
| Authority results | Upload fixed: D and F are fails, only the chosen semester is written, only the exam office uploads. Results Summary (GPAs, pass / supp / repeat / disco counts, module pass rates). Manual **Withhold** button: the portal shows "Withheld" instead of grades; `*W*` / `*N*` read as words | committed · not live |
| Testing and release | End-to-end test of a student's whole year; release guide `ops/guides/06-release-2026-27.md` | committed · not live |
| College offices | From the org chart: DPARC (all academic, confirms reviews, advances the year, told of postponements), DPFPA (executive finance summary, college property), ICT officer (accounts, passwords, open roles; Django admin with every table), the exam officer as an office of its own (no accounts, no advance; examinations only — modules, enrollments, staff, services and sick records are the HOD's, DPARC's and the Principal's). Senior roles only from the Principal, every change logged. Marks go by module: whoever teaches it enters its CA. Migration 0055 keeps today's exam officer working. Checked: 39 new tests, every office signed in and its menus, landing page and buttons checked in a browser | **not committed** · not live |

### Waiting list, in order

1. **VETA courses — skeleton built 3 Oct (not committed).** Define a course (short, or NVA
   Level I–III), its fees and instalments, the VETA ID pattern, intakes; admit through the
   four desks; bill; the student's own portal; the course coordinator's modules. Next: test
   marks and attendance (before the first tests, ~mid-November), completion and certificate
   numbers (before ~19 January). Still open in `diagrams/questions.md`: Q8 what must be paid
   before starting (admission sees what is owed but is not blocked), QV9 CRDB or GePG.
2. **Finance expenses — built 3 Oct (not committed).** Categories and yearly budgets;
   the accountant requests, the Principal or DPFPA approves (one is enough) or queries it
   and the accountant answers, the accountant pays; office funds or petty cash (a float
   with top-ups, paid in cash, never beyond it); reasons for rejections, cancellations and
   over-budget approvals; receipts; audit trail; Executive Summary ▸ Spending.
2d. **Returning students from before the system — 12 Oct (not committed).** Readmissions,
   repeaters and postponement returns entered from last year's papers on Returning Students
   (exam office, Principal, DPARC; one by one or an Excel sheet), then started from Admissions ▸
   Expected back and billed correctly (release guide C4b).
2e. **Class lists and CAT exam numbers — 12 Oct (not committed).** Admissions ▸ Class lists:
   a level's students this semester with NACTVET number and First sitting / Repeater (a
   repeater's modules listed), provisional until the admission window closes, Excel. CAT Exam
   Numbers (examination officer): the year's format (`{COLLEGE}{LEVEL}{SEM}{CAT}{SEQ}` …),
   CAT 1 and CAT 2 per level, kept once given, Excel. Repeaters tagged and listed last on the
   attendance roster, the module list and CA entry (release guide C4c).
2f. **Printed class attendance register — 12 Oct (not committed).** Take Attendance ▸ Class
   attendance register: the tutor declares the module's sessions (kept on the module) and downloads
   a Word register to the college's template (cover with crest, level, code, name; names and
   NACTVET numbers, 5 sessions a page, 35 students a page; summary and signatures), exactly that
   many sessions. Warns while admissions are open. Department line from Departments — rename
   "Pharmaceutical science" to "Pharmaceutical Sciences" for the template's wording.
2g. **New name everywhere — 12 Oct (not committed).** Blue Pharma Institute of Science and
   Technology (BPHIST) replaces Blue Pharma College of Health: results and field sheet headings,
   class list and CAT Excel, the student form page, the college profile on invoices (migration
   0063, only if still the old name), the fee and form seeds, unanswered forms' questions, the
   CAT initials (BPHIST). Not renamed: bank account holder names, email/website (questions Q33, Q34).
2h. **One menu item, one view — 13 Oct (not committed).** Audited every sidebar item for every
   office in the browser. Fixed: Results showed six cards in every item (an `admin-only` CSS rule
   overrode the switcher) — now each card belongs to its items, with Authority Grades and Entry
   Windows (exam office only) as items of their own; Eligibility ▸ Final Eligibility its own view;
   a stale "spending not recorded" card under Executive Summary ▸ Finance removed. Students and
   Student Records merged into one Students menu; Upload / Register removed and the server refuses
   unadmitted registration numbers. Portal: password alert on the dashboard, services and profile
   as options, a repeater sees only their repeat modules.
2i. **One service request at a time — 16 Oct (not committed).** A student could send the same
   request again while the first was waiting. Now refused on the server (row-locked, so a double
   tap cannot slip through) until the college approves or declines it — forwarded counts as
   waiting; the portal tile says "Asked — waiting for the college's answer".
2j. **Health reports, the Dean told, inventory review — 16 Oct (not committed).** Students report
   a health problem with the hospital's document from the portal; records checks the original and
   records it or says why not; every health record names the hospital and the document's date
   (migration 0064). Health records moved under the Students menu. The Dean of Students is notified
   of requests, postponements and hostel applications, and reads requests and postponements. Inventory
   reviewed: split batches kept their item type (fixed); the rest listed for a decision in
   `diagrams/questions.md` Q35.
2k. **College property through its life, and the laboratory — 17 Oct (not committed).** Assets have a
   status (in use, under repair, missing, disposal proposed, disposed), a custodian chosen from staff and
   value data (cost, purchase date, supplier, serial, funding, warranty). Disposed assets leave the
   register; DPFPA approves disposals (Executive Summary ▸ Asset disposals), the estate officer carries
   them out; part of a batch can be disposed. History is not edited; assets are not deleted. Repairs
   mark the item under repair and complete as good / unserviceable. Inspections apply their results on
   closing (the review) and are then locked. Custody & Handover moves what one person holds to another.
   Lab Technician role (open, given by ICT) with the laboratory stock book: receive, issue, write off,
   balances, low stock, expiry; DPFPA and the Principal read it. Migration 0065; `attendance/inventory.py`.
2l. **Opening the year while results are out (option B), finishing after Level 5, did not report —
   18 Oct (not committed).** The advance no longer stops for missing results: a student with nothing
   failed so far is placed provisionally (*Provisional* on the desks, lists, registers and portal; no
   end-of-semester exam until settled); a failure already in, or Level 6, waits — **changed 19 Oct:
   everybody unconfirmed goes provisionally in one advance, only Level 6 finishing waits**. Semester Review ▸
   Provisional places: the exam officer's cut-off, what the results now say, *Settle the passes*
   (Principal / DPARC). A late failure is a repeat of **its own semester** (2025/26); the provisional
   place, registration, applications and fees are called off, marks kept but not counted; a
   supplementary still to come keeps the student provisional. Finishing after Level 5: portal only,
   the Principal decides, no refund, return any later year. Admissions ▸ Did not report from three
   months after the window opened. New endpoints swept for access (`test_security_new.py`); health
   uploads checked by content. Migration 0066; `test_provisional.py`.
   - [x] Built and tested (1023 tests), checked in the browser
2m. **Enrolment form details and passport photo — 18 Oct (not committed).** From the paper Student
   Enrolment Form: address, guarantor / sponsor and phone, schools before (O-level, A-level, college:
   school, years, index number) — kept by records, read on the application and the portal profile.
   Passport photo: the student uploads it (My Profile ▸ Personal info) or records / the Principal at
   Find a student; resized in the browser to 300×300 JPEG, the server takes only real JPG / PNG up to
   512 KB, served only through the app; small on the dashboard, large on the profile, on the staff
   record and the application. Manuals with screenshots for the admission office, examination officer,
   accountant and students (`ops/guides/manuals/index.html`, also published as a private claude.ai page).
   Migration 0067; `test_passport.py`.
   - [x] Built and tested (1034 tests), checked in the browser at desktop and phone width
   - [ ] Names stay one field (First Middle Surname); the form's declarations and signatures stay on paper
2n. **Result sheets and returning students' college ID — 18 Oct (not committed).** Authority grade
   templates: modules in code order; no Sitting column; the continuing sheet only for students sitting
   every module as first sittings, anybody missing a module on the repeaters' sheet
   (`progression.sheets_for`, shared with Results Summary). Returning Students record the college ID
   (form and Excel column), refused over a different ID or one another student holds.
   `test_result_sheets.py`.
   - [x] Built and tested (1042 tests), both templates checked in the browser
2o. **One advance for everybody, and the server's 500 — 19 Oct (not committed).** The advance on the server
   failed with a server error: a review confirmed before its modules had a programme kept none.
   `progression.programme_and_level` reads review → standing → registration → modules → the only
   programme offering the level, and fills the blank on the review; anyone still unplaceable is named in
   the preview; a refusal is a 400 with the reason, nothing changed. Your rule: one advance places every
   unconfirmed student provisionally, every level and programme; only Level 6 finishing waits.
   *Send continuing students to finance* marks them provisional the same way. A 500 now logs its
   traceback (`django.request` → journalctl). `test_advance_programme.py`.
   - [x] Built and tested (1046 tests)
   - [x] New application without a programme answered 500 (`filter(id='')`): `views.pk_or_none` on every id
     read from a request (41 places), so a blank or wrong id gets the view's own message; programme lists
     reload when New application, Returning Students, Class lists, the fee grid or the module form opens,
     so a programme added since the page loaded shows. 1048 tests
2p. **Items checked only — 19 Oct (not committed).** Certificates, signed forms: `AdmissionRequirement.kind`
   (billed / check, migration 0068 — items saved with nothing to charge become check-only). Check-only
   items bill nothing, are checked only at the admission desk before admitting (records checks nothing;
   finance only billed items, whose paid / not paid the admission desk sees), and when missing hold the student
   at the admission desk (`admissions.sync_item_hold`, "Missing: …"); admitting is refused while one is
   missing or unchecked; Has it / Waive lifts only that hold. Billed items unchanged. `test_check_items.py`.
   - [x] Built and tested (1057 tests), checked in the browser
   - [x] Counted items (rim paper: 2 a year — Brought 1 asks again next semester, 2 clears the year; never
     billed) and typed items (the NACTVET admission code, kept on the student's details), migration 0069;
     Items check opened to the admission office for checked-only items. 1063 tests, checked in the browser
2q. **VETA identity card numbers — 19 Oct (not committed).** The VETA pattern (Window & ID numbers) is now a
   series of its own: its own counter and starts-at (`CollegeIdFormat.veta_next_number` / `veta_starts_at`,
   migration 0070), refused when identical to PST's, carried into the next year (it was dropped before).
   - [x] Built and tested (1066 tests)
2r. **First day of use, batch 2 — 19–20 Oct (not committed).** Your list of 13 and the statuses:
   - [x] 1 VETA card numbers counted course by course (`CollegeIdCounter`; the pattern needs `{PROG}`)
   - [x] 2 VETA records desk takes the admission form (Fomu ya Kujiunga): birth year/place, religion,
     education level, parent/guardian with residence, who pays, health declared — the shared form
   - [x] 3 department and programme filters: Finance Overview, Balances, Collections, Executive Summary
   - [x] 4 VETA items (*Asked of*), checked at the VETA admission desk; missing/unchecked holds
   - [x] 5 next of kin and who pays compulsory at every records desk (continuing and VETA); sponsor named
   - [x] 6 every correction logged (`StudentDetailChange`), shown under the record with the reason
   - [x] 7 invoices raised from the application window at the finance desk
   - [x] 8 HOD sees only their department's staff (no accounts), gives only their modules
   - [x] 9 no self-claiming of modules; My Profile is the password only
   - [x] 10 thirty minutes without use signs out (staff and students; keep-alive while typing) — five
     at first, made thirty on 20 Oct as too short
   - [x] 11 staff passwords to the students' rule, changed once at the next sign-in
   - [x] 12 CSRF: sign-in never cached, always sets its cookie, a refusal explained and logged;
     `CSRF_TRUSTED_ORIGINS` in `.env`
   - [x] 13 statuses: FAIL (academic, readmitted), Discontinued (disciplinary, back as new), Absconded
     (back after two years as new), Suspended (place kept, no exams, ends by itself), Deceased,
     Terminated (never again); postponed > 2 years and Level 5 finishers back as new — one-time
     charges again, same ID. Postpone & Finish ▸ Student status.
   - [x] Migrations 0071–0073; 1102 tests; every new screen checked in the browser
2s. **PST as one programme, names, NHIF, opt-outs — 20 Oct (not committed).**
   - [x] `manage.py merge_programmes` (dry run, then --apply): three per-level PST programmes into one
     across Levels 4–6 — rehearsed on a copy of the live situation (Level 4 admitted, Level 6 in process)
   - [x] Names in three parts at admission, records and VETA intake; existing names split (migration 0074)
   - [x] Items for new students only (uniforms, clinical coat at Levels 5/6); NHIF card → *Insurance* item
   - [x] NHIF New / Continuous students sheets (Students ▸ NHIF sheets); marital status, NIDA, NHIF card No.
   - [x] New application kind: *First / Second / Third year* (new to the college; the year sets the
     level) apart from *Continuing* and *Readmission* — the admission officer's suggestion
   - [x] `merge_programmes` into an existing programme (server: PST04/05/06 → PST): shows departments,
     active state and fee clashes; keeps the merged programme active
   - [x] PST students sign in with the college ID too (the password slip said so; only VETA could)
   - [x] A module with no programme is the level's at admission; editing a module's programme / level /
     semester links the class
2t. **Admission and finance make a student ours — 20 Oct (not committed).**
   - [x] The year's bill only at the finance desk: a module links and bills nothing (repeat module aside);
     the enrollment billing hook removed; a repeater back to a full semester 2 goes to finance
   - [x] Closed: add a student to a module (single, bulk, moving), the accountant's *Generate charges*;
     `enroll_registered` and the merge's enrolment removed
   - [x] An admitted student gets a password with no modules (kept on their record; portal page with
     programme, bill, details); later modules take it on
   - [x] Paid beyond the bill: credit on account (statement, portal), used on the next bill at the
     finance desk, same bank account only (migration 0075: the payment's account)
   - [x] Semester pass: admission desk prints, students download; registered, studying, billed, paid
     up to the semester's first instalment; check code catches an edited copy (*Check a pass*)
   - [x] 1138 tests; screens checked in the browser (password with no modules, pass, check, forged code)
2u. **Student pass, overpayments, first password, menus — 21 Oct (not committed).**
   - [x] Pass as an ID card (85.6 × 54 mm, prints as one page at that size), stamp box, no link
   - [x] Valid until follows the student's own paid windows; one full payment → a year; override → its date
   - [x] Passes kept with random codes (migration 0076); checked by the code alone
   - [x] Finance ▸ Payments ▸ Overpayments: paid beyond an invoice's full fee
   - [x] Admission records an item brought after admission (Has it only), PST and VETA
   - [x] Menus split: Admissions (desk, new, VETA, pending, applications, expected back, settings);
     Students gains pass, class lists, items check, did not report
   - [x] First password = last name in capitals at admission, change forced; no ICT issuing step
   - [x] Compulsory evaluations ask only students who studied the period; newcomers not stopped
   - [x] 1155 tests; screens checked in the browser
2v. **Design system — 21 Oct (not committed).** Primary blue #123F80, light blue #EFF6FF, white,
   cool grey #F1F5F9 on every dashboard and page.
   - [x] Staff dashboard: white top bar, blue sidebar, rounded cards with soft shadows, tile counters
     with icons, quiet tables, status pills, help text as callouts; dark mode and phone width kept
   - [x] Admission desks: rows with initials, kind and desk pills, money; the application window as
     summary tiles and section panels
   - [x] Student portal, VETA/no-module portal, sign-in, password, compulsory form, registration,
     pass pages: the same tokens and shapes
   - [x] Student portal: Generate Invoice and What I Owe — padded panels, action rows, the invoice
     result as a coloured message (working / ready / failed), money with thousands separators
   - [x] My Profile: every option as plain two-column tables (one per next of kin, school, document,
     health record); the upload, health-report and password forms in the same two columns
2w. **Next-of-kin relationships — 21 Oct (not committed).** Mother, Father, Guardian, Husband, Wife,
   Sister, Brother, Other relative, Other (migration 0077). Parent / Sibling / Spouse kept readable on
   old records, never written anew, listed for records to correct; checked in the browser; 1170 tests
2x. **DPFPA approves shortfall waivers; menus apart; tutors from a list — 21 Oct (not committed).**
   - [x] Tuition / direct-cost waiver = a request to DPFPA (migration 0078); applied only when approved;
     refusal needs a reason; the student waits at finance (PST and VETA); both sides notified
   - [x] *Waive a shortfall* in the application window at finance; *Shortfall waivers* list for the accountant;
     *Finance summary ▸ Waiver approvals* for DPFPA
   - [x] DPFPA menus: Finance summary and Inventory summary apart
   - [x] Module form: Tutor responsible from the department's tutors (name and responsibility from one
     choice); the staff assignment kept for emergencies
   - [x] 1179 tests; the whole flow checked in the browser
2z. **Every admitted student signs in to the dashboard; plain session message — 22 Oct (not committed).**
   - [x] An admitted PST student with no module yet opens the normal student dashboard (not the
     "My Programme" page): a stand-in for the enrollment from their record and registration; modules
     appear on the next page once added, without signing in again (VETA keeps its course portal)
   - [x] Idle sign-out says only "Your session expired. Sign in again." — not how long the limit is
   - [x] Waivers & Overrides gets *Waive a shortfall* (DPFPA approves); the override card is labelled
     *Let through or hold — override* (saved at once, takes nothing off the bill), so the two are not confused
   - [x] The accountant's money menu renamed *Student accounts* — no second *Students* menu; a test keeps
     every menu name unique
   - [x] The accountant's sidebar, most used first: Admissions, Payments, Student accounts, then Finance
     Overview, Fee Setup, Expenses, Postpone & Finish, Finance summary, Audit Trail; they land on
     Admissions ▸ My desk; no Students menu for them — Items check and Class lists back under Admissions
   - [x] 1179 tests; checked in the browser (desktop and phone, every portal view, no errors)
3a. **Speed; no flash of other menus; waivers by instalment — 22 Oct (not committed).**
   - [x] Measured on 300 students (1,800 enrollments, 1,200 charges, 300 applications): the admission
     list ran 3,313 queries (2.7 s) — 11 a row; now 20 (0.33 s). Admissions sign-in 3,937 → 157 queries
     (4.0 s → 1.0 s); Principal 3,883 → 385; tutor 320 → 102; accountant 372 → 204
   - [x] How: staff names looked up once per response; next of kin, education, VETA applications fetched
     with the list; balances, residence and typed items read for the whole page at once; module counts
     in the list's own query; the staff list reads each office table once; finance's student list no
     longer loads every charge and enrollment (0.94 s → 0.24 s); an office ("is this the Principal?")
     looked up once per request, forgotten the moment one is given or taken
   - [x] Slow-request log: any request over `SLOW_REQUEST_MS` (default 1000) is written to the service log
     with its query count — `journalctl -u edutrack | grep "Slow request"`; the path only, never a search
   - [x] Signing in: the page stays hidden behind a loader until the menus are trimmed to the person's
     own — no second of other offices' menus (checked: tutor and accountant, nothing shown while loading)
   - [x] Waive a shortfall: *Category* (Tuition fee / Direct costs) → *Instalment that is short* (due date,
     what is owed) → *What is short* → *Why* → Send to DPFPA (migration 0079). Approved, it comes off that
     instalment's charges in turn; paid meanwhile, DPFPA is told to refuse it. Requests made before keep
     their charge
   - [x] Tests keep the lists flat (three rows cost what thirty do); 1192 tests; checked in the browser
3b. **Results reach students when the college chooses; every year on the portal — 24 Oct (not committed).**
   - [x] NACTVET's new sheets checked through the system (scratch database): the three 2025/26 first-sitting
     sheets (exam numbers MTS1) match in all 90 rows; the older-year sheets (MTS2) are the 2025/26 repeat
     sittings printed under the original year — 19 of 26 match; the rest are repeats not re-sat (grade
     carried, red), which the system would read as FAIL — those sheets wait (`NS` mark, past-results import)
   - [x] *Results Summary* says whether students see the semester (level by level): **Show to students** /
     **Hide from students** (examination office; migration 0080). Hidden until shown; what students could
     see on 24 Oct stays shown
   - [x] Withhold several at once (tick them, *Withhold selected*); repeaters' sheet shows who is withheld; a
     withheld repeat keeps back the semester it completes
   - [x] The portal's *End of Semester* reads every year, each semester under its own year and classed at its
     own level; nothing of an earlier year counts in this year's lists, GPA or eligibility
   - [x] A provisional student is told so at the top of the portal (and, once results are shown, what is
     still to come — e.g. the supplementary to pass)
   - [x] The examination officer settles provisional students: the passes in one go, a repeat or FAIL one by one,
     as the results say (overruling them stays with the Principal / DPARC)
   - [x] A settled student is told only once that semester's results are shown to their level (held back
     until then, sent when the exam office presses *Show to students*)
   - [x] The authority grade template lists students by name, A to Z (registration number between equal names)
   - [x] 1210 tests; checked in the browser (show / hide, withhold several, settle passes, the portal's notice and
     2025/26 statement, the template's order)
3c. **Server error on uploading grades — fixed 24 Oct (not committed).** On the server, an upload that brought in
   provisional students' results answered 500 (`NameError: Notification` in `authority_grades`, written 18 Oct, never
   imported). The grades were saved before the error (no request-wide transaction); only the Principal / DPARC notice
   and the reply failed. Fixed (import), with a test through the upload itself; the whole app scanned for other names
   never defined (none). 1210 tests
3d. **Clear uploaded grades; skipped grades named — 24 Oct (not committed).**
   - [x] *Results ▸ Authority Grades ▸ Clear uploaded grades…* takes one sheet's uploaded grades off (the semester,
     level, sheet and sitting selected) so a corrected sheet can be uploaded. What it clears is kept
     (`AuthorityGradeClearing`, migration 0081: every grade, who, why). Refused while students can see the results,
     when a supplementary sits on a first sitting (clear that first), and once anybody is confirmed on them
   - [x] An upload now lists every grade it did not save, by name: "not enrolled in that module this semester"
     (it used to be only a count — why filled cells seemed lost) and errors
   - [x] 1210 tests; checked in the browser (cleared 24 grades with a reason, the record kept)
3e. **Earlier years' results; NS; no grade quietly replaced; corrections — 25 Oct (not committed).**
   - [x] *Results ▸ Earlier Results* (exam office): an earlier year's NACTVET sheets the system never held — first
     sitting (with a Modules sheet of credits), supplementary, then the repeat sitting with the semester it was sat
     in. Record-only enrollments (no password, no bill, no class list); a repeat links to the sitting it repeats
   - [x] A repeat shows under the year it clears, everywhere, from now on (repeaters' sheet, portal: "Repeat, Sem 2 ·
     2025/2026"); every upload links a repeat sitting with no recorded origin to the earlier sitting it repeats
   - [x] `NS` = not sat at the repeat: the year's grade stays, still owed, remark REPEAT (portal: "Not sat at the
     repeat … — still owed"); only on a repeat sitting
   - [x] An upload never replaces a grade already in with a different one (listed; clear the sheet or correct it)
   - [x] *Results ▸ Corrections*: the exam office corrects one grade at once (reason, NACTVET reference) — a new grade,
     NS, or *remove* (a grade typed against a module not sat → INCOMPLETE); DPARC and the Principal are told, not asked
     (your rule); the old grade kept (migration 0082)
   - [x] A confirmed student whose corrected results now say otherwise: *Semester Review ▸ Show: Results now differ
     from the decision ▸ Confirm again* (the exam office, as the results say; provisional while a result is missing);
     repeats the wrong decision raised are cancelled; a place it called off stays off — back through admissions
   - [x] The portal's "current" enrollment is the newest by year and semester (a record of an earlier year entered
     later is never taken for this one)
   - [x] Your sheets, end to end through the new import: the 27 students on NACTVET's repeat sheets all read as
     NACTVET printed them (the two POSTPONED read REPEAT, your decision). 11 upload files prepared, 14 NS cells
     (`~/Documents/BPHACOH/RESULTS/upload_earlier/`)
   - [x] 1224 tests; checked in the browser (Level 4's three files uploaded, a correction made)
3f. **INCOMPLETE on the portal; wrong confirmed results; only Level 6 finishes — 25 Oct (not committed).**
   - [x] A blank cell — or NA against a module they are enrolled in — records nothing: the module has no result
   - [x] The portal shows a module with no result empty, INCOMPLETE, and that semester INCOMPLETE — no GPA, no comment
     (it used to leave the module out and show a GPA and class without it)
   - [x] The year-end advance never marks a Level 4 or 5 student finished: a programme with no next level holds them
     with "add the level to the programme" (the PST04 / PST05 single-level programmes did this before the merge)
   - [x] Your example in the browser: a grade typed under module A that was module B's, confirmed as passed → A removed,
     B corrected, confirmed again (provisional: A's result missing); the student reads A empty INCOMPLETE, B A,
     semester INCOMPLETE; DPARC and the Principal told each time
   - [x] A correction recalculates the GPA everywhere: the Results Summary, the repeaters' sheet and the portal work it
     out from the grades each time; the student's semester review (which keeps its own copy) is refreshed too,
     confirmed or not
   - [x] No GPA for an INCOMPLETE student anywhere (Results Summary, Semester Review, portal) — NACTVET prints NA —
     and none in the class average (it used to show a GPA from the modules graded so far)
   - [x] Earlier Results ▸ Repeat sitting follows NACTVET's repeat sheet when the server enrolled a repeater with the
     class (25 Oct, NS1314: "enrolled … as a first sitting, not a repeat — not written"): an owed module enrolled as a
     first sitting, ungraded, is recorded as the repeat (linked to the year it clears); a module passed that year and
     enrolled again, ungraded, is taken off (withdrawn, kept). Anything already graded is left and reported. NS1314
     replayed: 3 recorded as repeats, PST05207 and PST05211 taken off, FAIL 2.1 under Sem 2 · 2023/2024 as printed
   - [x] The year end never finishes a Level 4 or 5 student (left waiting instead, with the reason); and
     `manage.py place_left_behind` places the ones the single-level programmes left behind, after the merge — the
     year end again for them alone (dry run first; Level 6, exits after Level 5 and students away left alone)
   - [x] A repeater's results show when the semester the repeat was sat in is shown — the year it clears comes with
     it (25 Oct: a repeater who failed again saw only "FAIL" and no results, because Sem 2 · 2024/2025 had not
     been shown on its own)
   - [x] The desks see what a provisional student's results say: "Provisional · results: PASS / REPEAT / FAIL / not
     complete" (26 Oct: REPEAT students were at finance — their results were in but not yet settled)
   - [x] Hold until results (DPARC / Principal, Semester Review ▸ Provisional places): a student whose results are not
     complete leaves the desk lists and cannot be cleared until settled, or is let through to start admission; one
     button holds all of them; a pass settled lifts the hold by itself; a REPEAT / FAIL settled cancels the place
   - [x] A repeat not sat (NS) settled REPEAT takes the place away like any REPEAT (26 Oct: four repeaters — NS3522,
     NS5001, NP1042, NS4084 — sat at semester 1's finance desk owing only semester 2 modules: confirming a REPEAT
     that came from NS raised nothing, so nothing took their provisional place). Their debt stays the one already
     open; they come back in semester 2. `manage.py call_off_repeat_places` puts right the ones settled before
     (dry run first; a repeater who owes a module of the semester they are placed in is left)
   - [x] The dry run says what each student will owe (26 Oct, the server's dry run: all four "owe nothing open" —
     their 2024/25 reviews never confirmed; the debt is raised from the repeat sitting's link to 2024/25 Sem 2, and
     that is what brings them back in semester 2). A student whose debt cannot be read is never called off
   - [x] Returning Students: NILL / NIL / NONE / N/A / - in the modules column means none (27 Oct: NP1211, back from a
     postponement owing nothing, was refused for "NILL")
   - [x] A postponement return at Level 4 (into semester 1) comes back as new — first-year route, one-time charges,
     same record and college ID; Level 5 and 6 come back as continuing (27 Oct, NP1211). A Level 4 student back for
     semester 2, or owing modules, comes back as before
   - [x] 1248 tests; checked in the browser (held, off the finance list, let through)
3g. **Transfer to another college — 27 Oct (not committed; migration 0083).**
   - [x] Transfers ▸ Transfer (admission office, Principal): find by NACTVET number or college ID → details, last
     results, this year's bill → approve. Portal closed (standing *Transferred*), places from this semester on
     cancelled (modules withdrawn, marks kept), fees from this semester on reversed to nothing (semester 1: one-time
     charges too); their money held stays as credit. Principal, DPARC and the accountant told. Back later = as new
   - [x] Nobody leaves owing: semester 1 checks last academic year, semester 2 checks semester 1. No bill in the system
     for that period (last year's fees were never entered) → finance's confirmation is written down (who, reference)
   - [x] Transfers ▸ Transferred records (admission, Principal, DPARC, records, the accountant): where
     to, when, fees reversed, money held, and their details and last results as they stood
   - [x] A sidebar menu of its own, **Transfers**, its two items dropping down (your instruction, 27 Oct); the estate
     officer's inventory item "Transfers" is now "Asset transfers" so no two menus share a name
   - [x] 1259 tests; checked in the browser (found, finance's word required, approved, listed; the menu for the
     admission officer, DPARC and the accountant at phone width)
3h. **VETA: a menu of its own — 28 Oct (not committed; no migration).**
   - [x] VETA ▸ Applicants · New application · Admitted students · Intakes · Courses & modules (your request: the
     VETA admission showed too little). Replaces Admissions ▸ VETA applications and the separate VETA Courses button
   - [x] Applicants: filter by intake and desk (default: still at the desks), search name / phone / number; phone,
     gender, course · intake, applied on, desk, what they owe. Admitted students: by intake, college ID, gender,
     phone, first next of kin, day / hostel, status and NVA level, admitted on, what they owe
   - [x] Open → the whole record (details, every next of kin, education, items, fees, each desk passed); DPARC and the
     coordinator see no money. Nothing new is asked of the student — records' form is as it was (3 Oct)
   - [x] 1264 tests; checked in the browser (admission officer, DPARC, coordinator, accountant at phone width)
3i. **Supplementary students' consent form — 28 Oct (not committed; migration 0084).**
   - [x] Your form (Fomu ya kuridhia … mitihani ya marudio): students whose results say SUPP (not yet settled) see it on
     the portal — optional (your choice) — read Sehemu A and B, tick that they agree, fill it in (the soft copy keeps
     the wording they agreed to, their name, NACTVET number and the level they continue to), print it with Sehemu D
     and the stamp box blank, sign it and bring it back
   - [x] Students ▸ Supplementary consent: the list (not filled in / filled in, signed copy not back / signed copy back),
     print any copy, and **Signed copy back** with the Principal's answer (Allowed / Not allowed) — the admission office
     or the Principal (your choice); DPARC and records read. Nothing is held while it is missing (your choice)
   - [x] The wording is edited there each year (your choice); made from the 2025 form with the years moved on and left
     **closed** until the office checks the amounts and opens it
   - [x] 1271 tests; checked in the browser (opened, filled in on a phone, printed, signed copy recorded); a first-visit
     race that made the wording twice (one request failing) found in the browser and fixed
3j. **The student portal on Android phones — 28 Oct (not committed; no migration).**
   - [x] Why it was too large: the 21 Oct design system came after the phone rules and put desktop sizes back; tables
     had a 620–720px minimum width; amounts were 1.6rem in half-width cards. A phone sheet now comes last
   - [x] The menu is an icon rail on the left from sign-in (≤ 820px): small icons with short names (Home, Attend,
     Results, Modules, Exams, Fees, Services, Profile); a group opens the full menu with it expanded; ☰ opens it too
   - [x] Tables become a card per row with each value labelled; totals two to a row, sized to fit; the provisional and
     consent notices on the dashboard only; the module search hidden on phones; profile options scroll in a row
   - [x] Semester pass: the student downloads it as a picture (PNG, ID-card proportions, 1027 × 648) — no print; the
     admission office still prints. The card fits the phone screen
   - [x] 1274 tests; every view checked at 360 and 412px (nothing wider than the screen), tablet 768 and desktop 1366
     unchanged apart from the rail on the tablet
   - [x] Invoices download as a PDF file (`invoice-<ref>.pdf`, your request): `?download=1` serves it; the invoice page
     has *Download PDF* beside *Print*. Written by the system itself (`attendance/pdf.py`, `invoice_pdf.py`) — no new
     package; opened by pdftoppm / mutool without complaint
   - [x] Dashboard, first thing: when the password expires (always, with *Change it*), and *Your semester pass is ready —
     Download pass* once this semester's instalment is paid
   - [x] A repeater is not held INCOMPLETE by a module they passed before (28 Oct: field practice cleared last year,
     enrolled again with the class and never graded) or one they were taken off — repeaters only: a first sitter
     (readmitted ones too) with no field result yet still reads INCOMPLETE, as it should (your note, same day): `progression.not_holding` — the
     review, the desk tag, the Results Summary and the portal all leave such an ungraded first sitting out. Nothing is
     changed; a grade entered for it counts as usual. After the deploy, open Semester Review ▸ Provisional places
     once (it reads the results again)
   - [x] Finance's desk (My desk): *Find a student* by name, NS number or college ID; the tag says what the results say —
     SUPP, INCOMPLETE, PASS, REPEAT, FAIL — instead of "Provisional" (your request, 28 Oct)
   - [x] The rail narrower (52px) with smaller icons; its toggle stands where the EduTrack mark was — ☰ opens the full
     menu, ✕ closes it (your question: how to close it); the top bar's toggle hidden on phones. Desktop unchanged
2y. **On the server, after this deploy (12–25 Oct work) — in this order.**
   - [ ] Migrations to 0083 applied by the deploy (release guide B1–B4); `Ran 1281 tests … OK` on the laptop first
   - [ ] Students ▸ Supplementary consent: check the amounts in Sehemu B (copied from the 2025 form), then Open
   - [ ] REPEAT students at semester 1's finance desk who owe only semester 2 modules:
     `sudo -u bphacoh venv/bin/python manage.py call_off_repeat_places` — read the dry run, then `--apply`. Run it
     again after the provisional students are settled (it should list nobody)
   - [ ] Level 4 / 5 students left behind by the year end (PST04 / PST05 had no next level): after the merge,
     `sudo -u bphacoh venv/bin/python manage.py place_left_behind` — read the dry run (who is placed and how; who is
     left as they are and why) — then the same with `--apply`. Not settled → provisional at the next level, to
     admissions; confirmed pass → promoted, to admissions. Then settle the provisional ones from Provisional places
     once 2025/26 Sem 2 results are in. Level 6, exits after Level 5 and students away are left alone
   - [ ] Upload the three 2025/26 first-sitting sheets (MTS1) into 2025/26 Semester 2, as First sitting; settle the
     provisional students (passes, then repeats / FAILs); withhold who should not see theirs; *Show to students*
     per level — the settled students are told then
   - [ ] The past results of the 23 students waiting on repeat results (your choice, 25 Oct: NS1314 for 2023/24 and the
     22 Level 4 and 5 students for 2024/25): *Results ▸ Earlier Results*, the five files in
     `RESULTS/upload_past_results/` in their numbered order (1 · 2023/24 L5 first sitting; 2–3 · 2024/25 L4 first sitting
     then supplementary; 4–5 · 2024/25 L5 likewise)
   - [ ] Then their current repeat sheets (MTS2) — *Results ▸ Authority Grades*, Sem 2 · 2025/2026, Repeaters, First
     sitting — with NS where NACTVET reprinted the old grade (the filled-in copies with NS are the "repeat sitting"
     files in `RESULTS/upload_earlier/`; check the 14 NS cells against the PDFs). Each repeat is linked to the year
     it clears as it uploads. Checked end to end: all 23 as NACTVET printed them. (NS1314's repeat was sat in
     2025/26 too: the dates on NACTVET's sheets are reprints of the first sitting's.)
   - [ ] Then *Show to students* for Sem 2 · 2024/2025, Sem 2 · 2023/2024 and Sem 2 · 2025/2026, per level
   - [ ] A day after the deploy: `journalctl -u edutrack --since today | grep "Slow request"` — any page
     still slow on real data is named there, with its query count
   - [ ] `merge_programmes` (release guide B6): dry run, read it, then `--apply` — before the next application
   - [ ] B7: HOD sets programme PST on Level 4's modules and creates Level 5's (links the two students
     admitted 19–20 Oct, bills nothing); the accountant confirms no new charges on their statements
   - [ ] Next of kin still "Parent / Sibling / Spouse": list them (release guide command); records corrects
   - [ ] Retake the desk manual's screenshots in the new look and republish it
   - [ ] Backups (21 Oct: Google Drive throttled rclone's shared sign-in, the nightly upload failed): give
     rclone its own Google sign-in — ops/guides/01-backups.md step 5b — and reinstall the scripts after the
     deploy: `sudo install -o root -g root -m 700 /var/www/edutrack/ops/backup/backup.sh /usr/local/sbin/edutrack-backup`
     and the same for `restic.sh` → `/usr/local/sbin/edutrack-restic`; then `sudo systemctl start edutrack-backup`
2c. **Security review fixed — 8 Oct (not committed).** `diagrams/security-review.md` (kept out of
   git): student-portal takeover, stored XSS, brute force, HTML receipts, CSRF on the portal,
   module claims, client-address spoofing, medical documents, Excel formulas; the ICT password
   desk for everybody; Django 5.2 LTS; libraries self-hosted; nginx limits and gunicorn threads.
   Open: a strict CSP (inline handlers), list pagination, background exports.
2b. **PST results match NACTVET — built 8 Oct (not committed).** Checked on 188 real
   students of 2024/25 (187 match; the one is Q30): starred grades at their own letter, GPA
   cut to one decimal and checked first, a starred repeat sitting discontinues, supplementary
   sheets upload as their own sitting, level 6 classes. `diagrams/design.md` section 19.
2a. **Student record and repeaters — built 3 Oct (not committed).** Form Four index
   number (required for a PST first year), gender required at the VETA desk too, health
   records on verified medical certificates read by the new Dean of Students role; a
   repeater pays 280,000 once a year plus 200,000 per module (set by hand on a server
   that already has its fees: release guide C4a). Open: `diagrams/questions.md` Q25–Q29.
3. **Appointment letters** — waiting on `diagrams/questions.md` Q21–Q22 (which roles, and
   how the letters are handled). Until then the Principal gives senior roles in User
   Accounts.
4. **Deploy** (you, when the remaining adjustments are done) — follow
   `ops/guides/06-release-2026-27.md`, including C1–C2: the Principal gives you the ICT
   Officer role (Django admin comes with it) and creates DPARC and DPFPA; untick
   superuser on any old shared account. Committed up to `75cd214`; the
   offices change is still to commit.
5. **Before 12 October, in the app** (no code):
   - the accountant confirms which charges make up the new students' 350,000 second
     instalment;
   - the accountant creates the TPH Book / Calculator / Rim Paper charge types and
     rates; the admission officer adds them, and Insurance, as required items;
   - the admission window, and the admission and records officer accounts;
   - the exam officer opens the CA mark-entry window before tutors enter CA (the sheet's
     Save now keeps to it);
   - continuing students queued at finance, and existing hostel students marked Hostel.
6. **Later (you said):** withholding results automatically for students with unpaid fees —
   for now it is the manual Withhold button in Results Summary.
7. **Later, not asked for yet:** moving out of the hostel mid-year; a library;
   a read-only fee summary on the Departments page; online applications (the college's
   current system can't be reached from here).
8. **Blocked:** removing the old finance tables needs the production row counts.
9. **Server:** a restore drill on the real server; a fresh Ansible `--check` run.
10. **Open questions:** `diagrams/questions.md` (short courses Q1–Q19, offices Q20–Q24,
   records, repeaters and petty cash Q25–Q29). And: your certificate message stopped at "…ordinary level or advance
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
- [ ] Opening a year before the results are out (option B: provisional placement, settlement list, fee reversal,
      cut-off) and finishing after Level 4/5 with that level's award; did-not-report list. Waiting on answers to
      Q42–Q52 (`diagrams/questions.md` section N)
- [ ] Pharmaceutical lab, beyond the basic stock book: item types (chemicals, controlled substances, glassware kits,
      equipment calibration, fridge temperatures, waste), a Lab watch panel, breakage incidents charged through
      finance. Design and open questions in `diagrams/questions.md` section M (Q36–Q41)
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
