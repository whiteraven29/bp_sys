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
- **The teaching calendar.** From the almanac, kept by DPARC. Semester 1 teaching started on
  2 November 2026 (the almanac's "12 May 2027" is a misprint).
- **Quality Assurance.** A new office, given by the Principal. QA follows every teaching plan and
  runs the student and staff evaluation forms (your note, 9 November). The Principal, DPARC and the
  Heads of Department keep the evaluation forms they had; service requests stay theirs.

Menu: **Teaching plans** ▸ My plans (tutors) · To approve (HOD, DPARC) · Plan progress (HOD, DPARC,
Principal, QA) · Teaching calendar · Curriculum.

**Tests on the branch:** `Ran 1352 tests … OK` (9 November 2026; 1330 on master).

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

1. Load the curriculum — read the dry run first:
   ```bash
   sudo -u bphacoh venv/bin/python manage.py load_curriculum --dry-run
   sudo -u bphacoh venv/bin/python manage.py load_curriculum
   ```
   It lists the 33 modules, their SEOs, tasks and assessment parts, and how many notes each has
   on where the national documents disagree.
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
   (curriculum of 2022)*. Modules are matched by code, NTA level and name together, so a Level 5
   or 6 module of the old curriculum finds nothing, as it should.

## What does not change, and what waits

- **Results are untouched.** The plan shows the assessment plan's weights; results still use
  today's (A1/A2/CAT1/CAT2, practicals). The scheme for the new curriculum is decided with the exam
  officer once every assessment plan has been compared (needed before Level 4 CAT 1, 18 January 2027).
- **The old curriculum is not loaded.** Levels 5 and 6 (old curriculum, this year and next) make
  plans without outcomes; their assessments show the weights results use today.
- **The attendance screen is unchanged.** A class marked done finds the register taken that day
  by itself; picking the plan's class while taking attendance is not built.
- **No notifications yet.** The Head of Department sees plans waiting under *To approve*.

## Where the national documents disagree (kept on each module)

The curriculum and the assessment plan spell some codes differently (PST04105/04205 Dispensing,
PST04207/04107 Communication Skills, PST06101/0601 Applied Pharmacology, PST06213 Pharmacy Practice
III called PST06212 in the assessment plan). Some plans name tasks the curriculum does not have
(e.g. PST04104 practical tests "5.3.1 f"), and Basic Microbiology's lists name tasks without their
SEO. PST04106's semester examination is printed as 40% of a 60% total; it is loaded as 60%.

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
backend/attendance/views.py                     the QA role in the role editor; ReadStaffWriteForms
backend/attendance/admin.py                     the QA office in Django admin
backend/attendance/curricula/extract.py         reads the national documents into the JSON (laptop only)
backend/attendance/curricula/pst_2022.json      the 2022 PST curriculum and assessment plans
backend/attendance/curricula/almanac_2026_2027.json
backend/attendance/management/commands/load_curriculum.py
backend/attendance/management/commands/load_teaching_calendar.py
backend/attendance/test_teaching_plans.py
backend/templates/index.html                    the Teaching plans menu and page; QA in the role editor,
                                                the header and the evaluation forms
```
