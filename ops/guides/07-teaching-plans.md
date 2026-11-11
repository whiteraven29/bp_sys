# 07 — Teaching plans, on their own branch until they are ready

**Status:** built on 8–9 November 2026, **not on master**. It stays on the branch
`feature/teaching-plans` until you have tried it and have no doubts; then it is
merged and deployed (section C). Nothing here changes marks, results, attendance
or money.

## What it is

- **Teaching plans.** A tutor's plan for one module in one semester. The curriculum gives the
  outcomes (SEOs), their related tasks, the hours a week of lecture, tutorial, practical and
  assignment, and how the module is assessed. The almanac gives the weeks. The tutor says which
  days they teach; the plan is laid out from that (outcomes in curriculum order, tests in the CAT
  weeks, around holidays and the recess), and the tutor adjusts it. After each class the tutor
  marks it done; the attendance register of that day is the evidence.
- **Measured against itself.** Classes done of those due by today: 90% or more on track, 70–89%
  behind, below 70% far behind. A 4-credit and a 20-credit module are judged alike.
- **Approval and moderation.** The Head of Department approves the plan and moderates each test's
  setting plan before it is given (assessment plan, Part IV). DPARC does it for a module with no
  Head of Department, or one the Head of Department teaches. Nobody approves their own.
- **Who follows it.** The Principal, DPARC and QA see every plan; a Head of Department, their
  department's; QA leaves review notes. The CQI supervision dates show the plans as they stood
  that day.
- **The curriculum loaded.** NTA Levels 4–6 Pharmaceutical Sciences (2022): 33 modules with their
  SEOs, related tasks, weekly hours and assessment schemes, read from the national curricula (Word)
  and assessment plans (PDF). Where those documents disagree with each other, it is noted on the
  module. CA is 40% and SE 60% everywhere (your note: the curriculum's 60/40 is the authority's
  typing error).
- **The old curriculum, Levels 5–6 (11 November 2026).** 23 modules read from the old assessment
  plans of March 2019 (the old curricula of February 2015 are not on hand): their SEOs and related
  tasks, and each part of CA and SE with the tasks it is drawn from. Weekly hours and credits are
  not in those plans, so they show *not given*, and a plan starts from the usual guess of a week.
  A Level 5–6 plan keeps the weights results use today, so it agrees with the mark sheet; the
  assessment plan adds which tasks each CAT, assignment and examination must cover.
- **The teaching calendar.** From the almanac, kept by DPARC. Semester 1 teaching started on
  2 November 2026 (the almanac's "12 May 2027" is a misprint).
- **Quality Assurance.** A new office, given by the Principal. QA follows every teaching plan and
  runs the student and staff evaluation forms (your note, 9 November). The Principal, DPARC and the
  Heads of Department keep the evaluation forms they had; service requests stay theirs.

Menu: **Teaching plans** ▸ My plans (tutors) · To approve (HOD, DPARC) · Plan progress (HOD, DPARC,
Principal, QA) · Teaching calendar · Curriculum.

**When a tutor sees their plans (checked 11 November 2026).** Nobody approves a tutor before they see
the menu; two things decide what they find in it:

1. **The Tutor office** on their account (given under User Accounts). It puts *Teaching plans* in their
   menu, with My plans, Teaching calendar and Curriculum.
2. **Being one of a module's tutors** in the semester that is open (the Head of Department gives
   modules under Modules). My plans lists only those modules. The first time, a module shows
   **Start the plan**; any of its tutors may start it, and every tutor of the module works on the
   same plan. A tutor sees no other tutor's plans, and none from another semester.

A tutor given no module yet sees "You teach no module in … Your Head of Department gives you your
modules." Until 11 November the server refused them instead (an error message), because it let in
only accounts that had a module; it now lets in the Tutor office as well. A plan needs no approval
to be seen or worked on: approval by the Head of Department (or DPARC) is what makes it the
approved plan. On the laptop's database no module of 2026/27 Semester 1 has a tutor yet, so
nobody would see a plan there; the server has its own assignments.

**Tests on the branch:** `Ran 1355 tests … OK` (11 November 2026; 1330 on master).

## A. Opening the branch (once, now)

The work is in your working copy, uncommitted. A new branch takes uncommitted changes with it,
so open the branch first, then commit there; master is not touched.

```bash
cd ~/Documents/BPHACOH/bp_sys
git status                                  # the files listed at the end of this guide
git switch -c feature/teaching-plans        # new branch, from where master is now
git add -A
git commit -m "feat: teaching plans, curricula, teaching calendar, QA office"
git push -u origin feature/teaching-plans   # a copy on GitHub; master and the server untouched
```

Check you are back on master before any other fix: `git branch --show-current`.

## B. While it is on the branch

**A fix for the live system** goes on master as usual:

```bash
git switch master                           # commit or stash branch work first
# … the fix, tested, committed, pushed, deployed as before …
git switch feature/teaching-plans
git merge master                            # bring the fix into the branch
```

**Migrations: the one thing to watch.** The branch adds `0086_teaching_plans`. If master needs a
migration of its own meanwhile, it will also be 0086. When you merge master into the branch, Django
will see two 0086s. Then, on the branch:

```bash
cd backend
PSYCOPG_IMPL=python PYTHONPATH=venv/lib/python3.13/site-packages /usr/bin/python3.13 \
  manage.py makemigrations --merge --settings=edutrack.test_settings
```

and commit the merge migration it writes. Never renumber a migration the server has already applied.

**Trying it on the laptop** (your own database, not the server's):

```bash
cd backend
PSYCOPG_IMPL=python PYTHONPATH=venv/lib/python3.13/site-packages /usr/bin/python3.13 manage.py migrate
PSYCOPG_IMPL=python PYTHONPATH=venv/lib/python3.13/site-packages /usr/bin/python3.13 manage.py load_curriculum --dry-run
PSYCOPG_IMPL=python PYTHONPATH=venv/lib/python3.13/site-packages /usr/bin/python3.13 manage.py load_curriculum
PSYCOPG_IMPL=python PYTHONPATH=venv/lib/python3.13/site-packages /usr/bin/python3.13 manage.py load_curriculum attendance/curricula/pst_2015.json
PSYCOPG_IMPL=python PYTHONPATH=venv/lib/python3.13/site-packages /usr/bin/python3.13 manage.py load_teaching_calendar
```

Note this migrates your local `edutrack_db`. To go back to master afterwards, the local database
keeps the new tables (harmless; master does not read them).

**Tests** — the whole suite, as always:

```bash
PSYCOPG_IMPL=python PYTHONPATH=venv/lib/python3.13/site-packages /usr/bin/python3.13 \
  manage.py test attendance --settings=edutrack.test_settings --parallel 1
```

## C. When there are no doubts: merge and deploy

```bash
git switch master && git pull
git merge --no-ff feature/teaching-plans
# run the tests again on master
git push
```

Then deploy as in guide 03 (it applies migration 0086). After the deploy, on the server:

1. Load both curricula — read each dry run first:
   ```bash
   sudo -u bphacoh venv/bin/python manage.py load_curriculum --dry-run
   sudo -u bphacoh venv/bin/python manage.py load_curriculum
   sudo -u bphacoh venv/bin/python manage.py load_curriculum attendance/curricula/pst_2015.json --dry-run
   sudo -u bphacoh venv/bin/python manage.py load_curriculum attendance/curricula/pst_2015.json
   ```
   The first lists the 2022 curriculum's 33 modules, the second the old curriculum's 23 (Levels 5
   and 6), with their SEOs, tasks and assessment parts and how many notes each has on where the
   documents disagree.
2. Load the almanac's dates:
   ```bash
   sudo -u bphacoh venv/bin/python manage.py load_teaching_calendar --dry-run
   sudo -u bphacoh venv/bin/python manage.py load_teaching_calendar
   ```
   A semester not yet in the system is skipped and said so (Semester 2: run it again once it exists).
3. **DPARC**, Teaching plans ▸ Teaching calendar: confirm or remove the **Christmas recess
   (21 Dec – 3 Jan)** — it comes from the PST04104 plan, not the almanac — and the **Semester 2
   CAT 1 (3–8 May)**, which the almanac puts before Semester 2 begins.
4. **The Principal**, User accounts ▸ the person ▸ Roles: tick **Quality Assurance**.
5. Open Teaching plans ▸ Curriculum, then a Level 4 module's plan: it should say *Follows …
   (curriculum of 2022)*; a Level 5 or 6 module's, *(curriculum of 2015)*. Modules are matched by
   code, NTA level and name together (PST05101 is a different module in each curriculum). On the
   laptop's copy of the database every 2026/27 Level 5–6 module found its own.

## What does not change, and what waits

- **Results are untouched.** The plan shows the assessment plan's weights; results still use
  today's (A1/A2/CAT1/CAT2, practicals). The scheme for the new curriculum is decided with the exam
  officer once every assessment plan has been compared (needed before Level 4 CAT 1, 18 January 2027).
- **The old curriculum, from its assessment plans only.** Levels 5 and 6 (old curriculum, this year
  and next) now get their outcomes and tasks; their assessments keep the weights results use today,
  where three plans differ (PST05209 18% written and 18% practical tests where results use 16 and 20;
  PST06104 and PST06105 one written test of 30% where results hold two CATs). The old Level 4
  (December 2016, in `Guides/FG LEVEL 4/`) is not loaded: only repeaters would need it. The Ministry of
  Health's letter LB.74/262/01B/172 of 27 October 2026 (passing on NACTVET's BC.220/244/65 of
  26 October) says so: the revised curricula for new NTA Level 4 students from 2026/27, while
  students already in Levels 5 and 6 finish on the earlier one. The 2022 curriculum's note on
  Teaching plans ▸ Curriculum now cites it. The PDFs `NTA4|5|6CURRICULUM.pdf` (11 November) were
  checked against the Word curricula already loaded: the same documents, nothing new to load. The old
  assessment plans (`Guides/oldAP/`, the same files as in `Guides/FG LEVEL 5|6/`) are read by
  `curricula/extract_2015.py`; the old curricula themselves would add the hours and credits.
- **The attendance screen is unchanged.** A class marked done finds the register taken that day
  by itself; picking the plan's class while taking attendance is not built.
- **No notifications yet.** The Head of Department sees plans waiting under *To approve*.

## Also on this branch: the new look (10 November 2026)

You chose a new visual identity for the whole system. It is on this branch, uncommitted, beside
the teaching plans, and goes to master with them. Proposal and screens (Proposal B is the one built):
https://claude.ai/artifact/BQNj7A8xiPh8Ghet4dStWa (private until you share it).

The same morning a first identity, "the ledger" (square sheets, heavy black rules), was built; on
review you chose Proposal B instead, in the colours of a reference you gave (royal blue running into
navy). Proposal B replaced the ledger in every page; nothing of the ledger's look remains.

- **What changes for everyone:** white cards with soft corners and a faint shadow on a cool grey
  ground; the college blue as the base (#1B5EAB, running into navy #152945 on the welcome panels and
  the sign-in page) for buttons, links and where you are; green, amber and red for status only.
  Status words are rounded labels on a pale ground. Table heads are tinted, in sentence case.
  Dark mode is the same layout on the reference's navy.
- **The frame:** the menu runs the full height with EduTrack at its head and is grouped under plain
  headings (Academic, Students, Money, Stores, College). A heading shows only when you see one of
  its items, so each office meets only its own. The accountant's menu still starts at their desk,
  then Money, then Students. The top bar floats over the page: the semester once, the light/dark
  switch, the bell, and one menu under your name with **My profile and password** and **Sign out**
  (Sign out was a separate button). The breadcrumb line is gone on a computer; on a phone the
  semester shows there, because the badge is hidden.
- **The dashboard (academic staff):** a blue welcome panel (good morning and your name, today's date,
  your modules, students and registers taken today, a **Take attendance** button, and the
  semester's attendance as a ring); **New for you** (your unread notifications, each opening where it
  is dealt with); **Attendance by level** as bars (green from 85%, amber from 80%, red below);
  **Recent registers** in five columns (module with level and topic, type, date, present of total,
  rate); announcements.
- **Moved:** the CAT and end-of-semester cut-off dates and **Advance Semester** are now under
  **Semester Review ▸ Academic period**, seen by the same people as before (the academic offices).
  Settings do not belong on a dashboard.
- **The student portal:** the same cards and blue; the welcome in a blue panel with today's date;
  under it three cards that open their pages: **Fees** (not billed / what is owed and the next
  instalment / paid up), **Results** (modules out and the GPA, or CA marks so far) and **Exams**
  (eligible everywhere, not eligible in N modules, or being checked); then the four figures as cards.
  The photo at the top right opens one menu: My profile, Change my password, Sign out (and, on a
  phone, Light or dark). Sign out is no longer a small icon beside the bell.
- **The student portal on a phone:** the menu strip stays on the left (your decision), 64 px wide
  with its names at 12 px (they were 9 px). The top bar names the page and the semester. Nothing to
  press is under 44 px and no text under 12 px, measured on all 13 screens at 390 px; nothing scrolls
  sideways. The profile's tabs wrap instead of scrolling; an odd last figure takes the full row; the
  eligibility notes are a list (CAT 1, CAT 2, ESE) instead of three lines running together.
- **The typeface:** Lato for the whole system (your choice, 10 November 2026), served from the
  college's own server (`backend/static/vendor/lato/`, SIL Open Font License; Regular, Italic, Bold,
  Bold Italic and Black). It replaced Atkinson Hyperlegible Next the same day. Lato's zero is not
  slashed and its capital I and small l are alike, so take care reading registration numbers aloud.
  Text is a sixteenth larger than before.
- **Charts (11 November 2026):** all 16 charts (Finance summary, Spending, Inventory and college
  property, Performance, form results) now match the cards: Lato, a tooltip shaped like a small card,
  round markers in the legends, faint lines along the values only, thin lines and soft bar ends, a
  thin white gap between slices and between the parts of a stacked bar, the chart titles with their
  sign in a pale blue square. Colours by role: blue first, orange second, red only for what is owed,
  failed or over budget, grey for a budget or what was waived, rating scales in shades of one blue
  (light for the first answer, dark for the last), the condition of property from blue (new)
  through grey (fair) to red (unserviceable). **Paid is now blue, not green:** green beside red is
  the pair colour-blind readers cannot tell apart (checked with a colour-blindness validator; blue
  beside red and blue beside orange pass). In dark mode the charts use their own darker shades, and
  switching light/dark repaints charts already on screen. The location map of property got the
  same cards and status colours.
- **Contrast:** every text colour reaches 4.5:1 on its ground and every input edge 3:1, in both themes,
  including the pale text on the blue panels (checked by calculation).
- **The teaching plan screen:** the semester as a strip (a column per teaching week, a cell per class,
  filled as classes are done; the break hatched; CAT weeks, the results deadline and the exams marked);
  one line of figures; this week's classes first, each with its topic on a line of its own and
  Done / Part / Missed under it; earlier and later weeks folded away. Moving a class, marking one
  missed, adding a make-up class and returning a test are forms in the page, not pop-up questions.
  The lists show the strip in miniature.
- **Sign-in page:** the blue panel, in order: EduTrack and the college's name; **College management
  system** in bold; one line; five bullets (admissions and records; fees, payments and invoices;
  attendance, results and eligibility; teaching plans and the calendar; notices, requests and
  evaluation forms); the motto. Under the form, three bullets: staff, students, and a forgotten
  password (reset by the ICT officer or the Principal, not "the administrator"). On a phone the bold
  line stays in the header and the bullets go, so the form comes first.
- **The server:** the dashboard API also sends `full_name`, for the greeting. Nothing else in the
  data changes; the screens' steps are the same.
- **Deploy:** nothing extra. `deploy.sh` already runs `collectstatic`, which copies the font files.
  Browsers keep `/static/` for 30 days, but the pages carry their styles inline, so the new look shows
  at once.

## Where the national documents disagree (kept on each module)

The curriculum and the assessment plan spell some codes differently (PST04105/04205 Dispensing,
PST04207/04107 Communication Skills, PST06101/0601 Applied Pharmacology, PST06213 Pharmacy Practice
III called PST06212 in the assessment plan). Some plans name tasks the curriculum does not have
(e.g. PST04104 practical tests "5.3.1 f"), and Basic Microbiology's lists name tasks without their
SEO. PST04106's semester examination is printed as 40% of a 60% total; it is loaded as 60%.

In the old assessment plans: Pharmacology and Therapeutics is headed PST05204, though it is module
four of semester 1 and its facilitator guide is PST 05104 — loaded as PST05104. PST06212 says
checklist 40% and report 60% in its introduction but 40% and 40% in its instructions — loaded
40/60. PST05208 prints SEO 3.3.2's tasks as d–f while its instructions name 3.3.2 a–c; PST06104's
test names SEOs 2.2.1 and 2.2.2, which the module does not have; PST06211's table of tasks has an
SEO 5.3.4 its list of outcomes lacks. Each is noted on the module.

## Files

```text
backend/attendance/models.py                    QualityAssuranceProfile; Curriculum, CurriculumModule,
                                                LearningOutcome, RelatedTask, AssessmentComponent;
                                                TeachingCalendar, CalendarEvent; TeachingPlan, PlanSession,
                                                PlanAssessment, PlanNote, PlanEvent
backend/attendance/migrations/0086_teaching_plans.py
backend/attendance/teaching_plans.py            the rules
backend/attendance/teaching_plan_views.py       the API
backend/attendance/urls.py                      /api/teaching-plans/…, /api/teaching-calendar/…, /api/curricula/…
backend/attendance/roles.py                     is_quality_assurance; the office in OFFICE_MODELS
backend/attendance/views.py                     the QA role in the role editor; ReadStaffWriteForms;
                                                full_name in the dashboard API (the greeting)
backend/attendance/admin.py                     the QA office in Django admin
backend/attendance/curricula/extract.py         reads the national documents into the JSON (laptop only)
backend/attendance/curricula/pst_2022.json      the 2022 PST curriculum and assessment plans
backend/attendance/curricula/extract_2015.py    reads the old assessment plans (.doc, via LibreOffice) into the JSON
backend/attendance/curricula/pst_2015.json      the old curriculum, NTA 5–6, from its assessment plans of 2019
backend/attendance/curricula/almanac_2026_2027.json
backend/attendance/management/commands/load_curriculum.py
backend/attendance/management/commands/load_teaching_calendar.py
backend/attendance/test_teaching_plans.py
backend/templates/index.html                    the Teaching plans menu and page; QA in the role editor,
                                                the header and the evaluation forms; the soft-cards look
                                                layer (replaces the 21 October one); the semester strip;
                                                the grouped menu (groupSidebar), the menu under your name,
                                                the new dashboard; Semester Review ▸ Academic period
backend/templates/student_dashboard.html        the soft-cards look layer for the portal; the welcome panel
backend/templates/login.html, password_change.html, student_password_change.html, register.html,
  student_required_form.html, course_portal.html  the soft-cards tokens; new sign-in wording
backend/static/vendor/lato/                     the typeface (eight woff2 files), lato.css, OFL.txt
```
