# EduTrack — Bug & Misbehaving-Feature Audit

**Date:** 2026-08-23 (all findings below fixed same day)
**Scope:** Full repo (`backend/` Django app + root-level static files). Read `models.py`, `serializers.py`, `views.py` (3,580 lines), `grading.py`, `forms.py`, `admin.py`, `settings.py`, URLs, management commands, and spot-checked the frontend templates (`index.html`, `student_dashboard.html`, `attendance_system.html`). Ran the existing test suite.
**Method:** Manual read-through + targeted greps + actually running `manage.py test` and `manage.py check`. Existing automated tests (61) did **not** cover any of the issues below at the time of the audit.

**Status: all 13 findings fixed** (12 from the original pass + 1 more caught while fixing the Students-management filters, reported separately by the user — see #13). 11 regression tests were added for the previously-uncovered fixes; the full suite is **72/72 passing**. Every finding below is annotated `✅ Fixed` with what changed.

Findings are ordered by severity. Each includes the concrete scenario that triggered it and the file/line that was fixed.

---

## Critical

### 1. Open redirect in the login view — ✅ Fixed
**File:** [backend/attendance/views.py:162](backend/attendance/views.py#L162)

```python
user = authenticate(request, username=identifier, password=secret)
if user is not None:
    login(request, user)
    return redirect(request.GET.get('next', 'frontend'))
```

`next` is taken straight from the query string and handed to `redirect()` with no host/scheme validation. `redirect()` treats any string that isn't a resolvable URL name as a raw URL, so `?next=https://evil.example.com/phish` sends an authenticated user's browser straight to an attacker-controlled page immediately after a successful login — classic phishing/open-redirect (CWE-601).

**Fix applied:** [views.py](backend/attendance/views.py) `login_view` now validates `next` with `url_has_allowed_host_and_scheme()` and falls back to `'frontend'` for anything unsafe. Covered by `test_login_next_param_rejects_external_redirect` / `test_login_next_param_honours_a_safe_local_url`.

### 2. Hardcoded default admin credentials, and silent password reset of an existing admin — ✅ Fixed
**File:** [backend/attendance/management/commands/create_admin.py](backend/attendance/management/commands/create_admin.py)

```python
parser.add_argument('--username', default='admin', help='Admin username (default: admin)')
parser.add_argument('--password', default='Admin@1234', help='Admin password (default: Admin@1234)')
...
user, created = User.objects.get_or_create(username=username, defaults={'email': email})
user.set_password(password)
user.is_superuser = True
user.save()
```

Running `python manage.py create_admin` with no arguments — on any environment, including production — creates (or **silently resets**) a superuser `admin` / `Admin@1234`, a password that is now permanently in the git history and easy to guess. Because it's `get_or_create` + unconditional `set_password`, running the command a second time (e.g. by habit, or copy-pasted from old notes) **overwrites whatever password the admin had already changed to**, without any prompt or confirmation — a real account-takeover / lockout risk if anyone other than the intended operator runs it.

**Fix applied:** [create_admin.py](backend/attendance/management/commands/create_admin.py) now requires an explicit `--password` (min 8 chars, `CommandError` otherwise), no longer echoes the password to stdout, and refuses to touch an existing account unless `--force` is passed. Covered by `test_create_admin_requires_an_explicit_password` / `test_create_admin_refuses_to_reset_an_existing_account_without_force`.

---

## High

### 3. Zero-valued marks render as blank cells in the Results Excel exports — ✅ Fixed
**File:** [backend/attendance/views.py:2731-2732](backend/attendance/views.py#L2731) (`download_results`) and [backend/attendance/views.py:2979-2983](backend/attendance/views.py#L2979) (`download_final_results`)

```python
a1w or '', a2w or '', ct1w or '', ct2w or '', cp1w or '', cp2w or '',
t_ca or '', p_ca or '', tot or '',
...
etw or '', epw or '' if hp else 'N/A',
end_exam_total or '',
final or '', fmt(res.supplementary_mark), outcome['grade'] or '',
```

The `x or ''` idiom is used to blank out unset values, but `0.0 or ''` also evaluates to `''` in Python — a real, legitimate weighted mark/total of exactly `0` (a student who scored 0 in a component, or a supplementary-required student whose displayed total genuinely rounds to 0) is indistinguishable from "not yet entered." A grading committee scanning the exported sheet for blanks-to-chase-up will read a failing 0 as "pending," and it's easy to overlook that a student with real zero marks needs sign-off.

Contrast with the correctly-written helper two lines above it: `fmt(v) = float(v) if v is not None else ''` — this pattern (used for the raw mark columns) is null-safe. The weighted/total columns should use the same `is not None` check instead of `or`.

**Fix applied:** both export views now use a `nz(v)` helper (`v if v is not None else ''`) for every weighted/total column, and the earlier `(t_ca or p_ca) is not None` guard in `download_final_results` was corrected to `(t_ca is not None or p_ca is not None)`. Covered by `test_final_results_excel_shows_a_real_zero_mark_not_a_blank_cell`.

### 4. Attendance cutoff dates are enforced on create but not on edit — ✅ Fixed
**File:** [backend/attendance/views.py:835-867](backend/attendance/views.py#L835)

`SessionViewSet.perform_create` explicitly rejects a *new* session whose exam period is past the semester's CAT1/CAT2/end-of-semester cutoff date. `perform_update`, three lines later, only re-checks that the teacher owns the module — it never re-validates the cutoff:

```python
def perform_update(self, serializer):
    module = serializer.validated_data.get('module', serializer.instance.module)
    allowed_module_ids = user_modules(self.request.user).values_list('id', flat=True)
    if module.id not in allowed_module_ids:
        raise PermissionDenied('You may only edit sessions for modules you teach.')
    serializer.save()
```

Any teacher can therefore create a session *before* the cutoff and keep editing its roster/attendance (via `SessionCreateSerializer.update`, which rewrites `AttendanceRecord`s) indefinitely afterward, defeating the entire point of the cutoff feature described in the model's own comment ("teachers cannot record attendance after this date").

**Fix applied:** the cutoff check was factored into `SessionViewSet._check_attendance_cutoff()` and is now called from both `perform_create` and `perform_update`. Covered by `test_session_edit_is_blocked_once_the_cutoff_date_has_passed`.

### 5. Any authenticated teacher can "claim" any module, including ones they don't teach — ✅ Fixed
**File:** [backend/attendance/views.py:710-717](backend/attendance/views.py#L710)

```python
@action(detail=True, methods=['post'], url_path='claim')
def claim(self, request, pk=None):
    try:
        m = Module.objects.get(pk=pk)
    except Module.DoesNotExist:
        return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
    m.teachers.add(request.user)
    return Response({'detail': f'Claimed: {m.name}'})
```

There is no check at all — not staff-only, not restricted to a department/class level. Since `user_modules()` (used everywhere to gate access to students, sessions, attendance and CA marks) is simply `user.modules_taught.all()`, any teacher account can self-grant full edit rights — add/remove students, backdate attendance, enter CA marks — on *any* module in the system, including ones run by other tutors, just by calling this endpoint. This quietly undermines every "Only tutors for this module can..." check elsewhere in the file, because becoming a "tutor" is a single unauthenticated-intent click away.

**Fix applied:** `claim()` now allows staff, or a non-staff user re-claiming a module they already tutor, or claiming a module with **no** tutor yet — but rejects taking over a module that already has a different tutor (`PermissionDenied`). Covered by `test_claiming_an_already_tutored_module_is_forbidden` / `test_claiming_an_unclaimed_module_still_works`.

---

## Medium

### 6. Bulk student import silently resets existing students' portal PINs — ✅ Fixed
**File:** [backend/attendance/serializers.py:783-828](backend/attendance/serializers.py#L783) (`BulkStudentSerializer.create`)

```python
else:
    if portal_pin and len(portal_pin) >= 6:
        student.set_portal_pin(portal_pin)
        student.save(update_fields=['portal_pin_hash', 'must_change_portal_password'])
    skipped += 1
```

When a bulk-uploaded roster row matches a student who **already exists**, and that row happens to carry a `portal_pin` column (e.g. because the same master spreadsheet is re-uploaded, or a new module's roster reuses a template that still has the PIN column filled in from a previous term), the existing student's login PIN is silently overwritten and `must_change_portal_password` is forced back to `True` — logging them out of their own chosen password with zero indication to the staff member doing the upload (the UI only reports counts of "added" vs. "skipped"; this case is bucketed under "skipped").

**Fix applied:** `BulkStudentSerializer.create()` no longer writes a PIN for a student that already existed — only newly-created rows can set a PIN; re-uploading a roster now leaves existing students' PINs untouched. Covered by `test_bulk_create_does_not_reset_an_existing_students_pin`.

### 7. A too-short PIN on a new student discards the whole (valid) student row — ✅ Fixed
**File:** [backend/attendance/serializers.py:814-819](backend/attendance/serializers.py#L814)

```python
if created:
    if portal_pin:
        if len(portal_pin) < 6:
            student.delete()
            skipped += 1
            continue
```

If `reg_no`/`name` are valid but the row's PIN column is shorter than 6 characters, the code deletes the just-created student entirely rather than just skipping the PIN assignment. A row with perfectly good student data gets thrown away because of an unrelated field, with no separate error message distinguishing "student not created" from "student created without a PIN."

**Fix applied:** a too-short PIN on a new row no longer deletes the student — the student is kept, the PIN is simply left unset, and the response now returns a `pin_skipped` count. Covered by `test_bulk_create_keeps_the_student_when_the_pin_is_too_short`.

### 8. Attendance-eligibility percentages disagree across pages for the same student — ✅ Clarified in the UI
**Files:** [backend/attendance/views.py:94-98](backend/attendance/views.py#L94) (`attendance_is_effective`, requires a certificate for a "Sick" mark to count) vs. the dashboard/report/student-dashboard calculations, e.g. [views.py:916](backend/attendance/views.py#L916), [views.py:1632](backend/attendance/views.py#L1632), [views.py:311 (serializers.py)](backend/attendance/serializers.py#L311), which all use `status__in=['P', 'S']` unconditionally.

Only the *Eligibility* view/exports gate a "Sick" record on `certificate_submitted`. The teacher **Dashboard**, the **Report** page, and the **Student Dashboard** count every "Sick" mark as effective attendance regardless of whether a certificate was ever submitted. The same student can therefore see, say, 92% attendance on their dashboard while being flagged "ineligible — attendance below 90%" on the Eligibility report, with no explanation of why the numbers differ. Whether or not this split is intentional, it isn't surfaced anywhere in the UI and reads as a bug to end users ("the two attendance numbers don't match").

**Fix applied:** chose the lower-risk option — the underlying numbers were left as-is (an existing test, `test_eligibility_api_and_excel_both_require_sick_certificate`, confirms the certificate-gated rule is intentionally exam-eligibility-specific, so silently changing the Dashboard/Report math would have been a real behaviour change, not just a bug fix). Instead, added explicit inline notes/tooltips next to the Dashboard "Avg Attendance" tile, the Report "Avg Rate" tile, and the Student Dashboard "Overall attendance" tile explaining that these include uncertified sick days and are **not** the exam-eligibility figure.

### 9. `ModuleSerializer.read_only_fields` is dead code — ✅ Fixed
**File:** [backend/attendance/serializers.py:221](backend/attendance/serializers.py#L221)

```python
class Meta:
    model = Module
    fields = [ ... ]
read_only_fields = ['id', 'created_at']   # ← 4-space indent: sibling of Meta, not inside it
```

`read_only_fields` is indented one level too shallow, so it's a no-op class attribute on `ModuleSerializer` itself rather than a `Meta` option. It currently has no visible effect only because `id` (primary key) and `created_at` (`auto_now_add=True`) are made read-only automatically by DRF for other reasons — but the declaration is broken and would silently fail to protect any other field someone adds to that list later. Every other serializer in the file puts `read_only_fields` correctly inside `Meta`.

**Fix applied:** `read_only_fields` is now correctly indented inside `ModuleSerializer.Meta`.

---

## Low / Housekeeping

### 10. Finance dropdowns build HTML without the app's own escaping helper — ✅ Fixed
**File:** [backend/templates/index.html:4440-4460](backend/templates/index.html#L4440) (`buildFinanceSelects`)

Nearly every other place in `index.html` wraps free-text values in `safeText()` before interpolating them into `innerHTML` (e.g. asset/location names at lines 4787-4792). `buildFinanceSelects()` is the one place that doesn't — student names, category names, and obligation labels are concatenated directly into `<option>...</option>` markup. Modern browsers heavily restrict what a `<select>`'s parsed content can render, so the practical exploitability is low, but it's an inconsistency in the codebase's own security convention and a bad pattern to copy elsewhere.

**Fix applied:** [index.html](backend/templates/index.html) `buildFinanceSelects()` now wraps every free-text value with `safeText()`, matching the rest of the file.

### 11. Orphaned prototype file at repo root — ✅ Fixed
**File:** [attendance_system.html](attendance_system.html)

A 1,114-line, fully self-contained "EduTrack" HTML prototype at the repo root (its own copy of xlsx.js, own inline styles, **zero** `fetch`/`XMLHttpRequest`/`localStorage` calls — anything entered into it vanishes on refresh). It hasn't been touched since 2026-07-14, while the real, backend-wired frontend (`backend/templates/index.html`) has continued active development through 2026-08-15. It isn't linked from any Django template or view. It's effectively dead code that could mislead a future contributor into editing the wrong UI, or into thinking it's a working offline mode.

**Fix applied:** removed (`git rm attendance_system.html`) — it's fully recoverable from git history if it's ever needed again.

### 12. Local venv is out of sync with `requirements.txt` (breaks tests and one download view) — ✅ Fixed
**File:** `backend/venv/`, `backend/requirements.txt`

`backend/venv` was missing `python-docx` and `psycopg[binary]`, both pinned in `requirements.txt`. This meant:
- `python manage.py test` failed outright with `ModuleNotFoundError: No module named 'docx'` before it could run a single test (`attendance/tests.py` imports `docx` at module scope for the CA sign-off tests).
- The live `download_ca_signoff` view (`views.py:2514`, CA acknowledgement-sheet export) does `from docx import Document` at call time — it would 500 on any environment provisioned the same way this venv was.

**Fix applied:** installed the two missing packages into `backend/venv` so the suite could actually run. This doesn't touch your deployed server (worth double-checking there separately), but re-run `pip install -r requirements.txt` in any other environment provisioned before those two packages were added to `requirements.txt`. As a related bit of housekeeping, 22 stray compiled `.pyc` files under `backend/**/__pycache__/` were also found still tracked in git from before `.gitignore` excluded them (my test runs kept regenerating spurious diffs on them) — untracked with `git rm -r --cached` so `.gitignore` can do its job going forward; the files themselves are untouched on disk.

---

## High (found afterwards)

### 13. Students-management "Set Password/PIN for Filtered Students" ignores the Module filter — ✅ Fixed
**File:** [backend/templates/index.html:2579-2584](backend/templates/index.html#L2579) (`bulkSetStudentPins`)

Reported directly by the user: filtering Student Management down to one Module and then using **"Set Password/PIN for Filtered Students"** did not actually scope the reset to that module. The visible table (`loadStudents()`) sends `class_level_id`, `semester_id`, `module_id`, and `search` to `/api/students/`, but the bulk-PIN action built its payload without `module_id`:

```js
const payload = {
  portal_pin: pin.trim(),
  class_level_id: document.getElementById('stu-flt-level').value || null,
  semester_id: document.getElementById('stu-flt-sem').value || null,
  search: document.getElementById('stu-search').value.trim() || null,
};
```

The backend (`_student_scope_for_request` in [views.py](backend/attendance/views.py#L129)) already supports `module_id` — the button's confirmation dialog ("Update the password/PIN for every student currently matched by these filters?") was simply lying: with a Module filter applied, it reset PINs for every student in the whole semester/level, not just the ones on screen.

**Fix applied:** added `module_id: document.getElementById('stu-flt-module').value || null` to the payload, so the bulk reset now matches exactly what the filtered table shows. Covered by `test_bulk_set_pin_respects_the_module_filter`.

---

## What already works
- `python manage.py makemigrations --check` — clean, no drift between models and migrations.
- All 72 unit tests pass (61 original + 11 new regression tests added for the fixes above).
- Grading/eligibility math in `grading.py` and `StudentResultSerializer` was traced end-to-end and is internally consistent (weights, CA-eligibility thresholds, supplementary-exam rules, GPA classification all line up with the documented rules in the `StudentResult` model docstring).
- Permission scoping (`user_modules`, `IsFinanceUser`, `IsEstateOfficer`) is applied consistently across the Inventory and Finance modules' `get_queryset`/`perform_create` methods.
