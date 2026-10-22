"""Who may do what: one place for every office's powers.

Django's `is_staff` flag used to do three jobs here at once: it was "an admin",
it carried the examination office's powers (any admin account that was neither
Principal nor Head of Department), and it carried account management. So the
exam office was the system administrator too, and the Principal could enter
marks. Every power is now tied to a named office instead, following the
college's organisational chart:

    Principal ─┬─ DPARC  (academic): HoDs, admission, examination, academic staff
               └─ DPFPA  (finance, planning, administration): accounting, estate

Django's own admin site (`/admin/`) belongs to the ICT officer: the role makes
the account a Django superuser, with every table there. Inside EduTrack the
ICT officer is still the ICT officer — accounts and passwords, not marks or
money — so each office's screens stay its own.

A superuser that holds no office at all is the emergency account (one made
with `manage.py createsuperuser` on the server): it passes every academic and
account check, because through `/admin/` it can already change any row. It is
still kept out of money and college property, which need the accountant's or
estate officer's own office. A superuser that does hold an office — the old
shared admin account, now the exam officer's — is treated as that office.

Marks are decided per module, not per office: whoever teaches a module enters
its continuous assessment, and the examination officer the rest (see
views.check_result_entry). Holding a senior office adds no power over marks.
"""

import contextvars

from .models import (
    AccountantProfile, AdmissionOfficerProfile, DeanOfStudentsProfile, DeputyAcademicProfile,
    DeputyFinanceProfile, EstateOfficerProfile, ExamOfficerProfile, HeadOfDepartmentProfile, ICTOfficerProfile,
    LabTechnicianProfile, PrincipalProfile, RecordsOfficerProfile, SecretaryProfile, TeacherProfile,
    TrainingCoordinatorProfile,
)


def _signed_in(user):
    return bool(user and user.is_authenticated)


#: The offices already looked up during this request. A screen asks "is this
#: the Principal?" a dozen times over; each answer was a query. Set fresh for
#: every request by OfficeLookupMiddleware and emptied whenever an office is
#: given or taken (signals.py), so an answer is never older than the request.
_known = contextvars.ContextVar('edutrack_offices', default=None)


def _holds(model, user):
    if not _signed_in(user):
        return False
    known = _known.get()
    if known is None:                       # outside a request: ask every time
        return model.objects.filter(user=user, is_active=True).exists()
    key = (model, user.pk)
    if key not in known:
        known[key] = model.objects.filter(user=user, is_active=True).exists()
    return known[key]


def forget_offices():
    """An office was given or taken: look again."""
    known = _known.get()
    if known is not None:
        known.clear()


class OfficeLookupMiddleware:
    """Keeps the offices looked up for the length of one request only."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        token = _known.set({})
        try:
            return self.get_response(request)
        finally:
            _known.reset(token)


#: Every office a person can hold. TeacherProfile has no is_active switch.
OFFICE_MODELS = (
    PrincipalProfile, DeputyAcademicProfile, DeputyFinanceProfile, HeadOfDepartmentProfile,
    ExamOfficerProfile, ICTOfficerProfile, AccountantProfile, AdmissionOfficerProfile,
    RecordsOfficerProfile, EstateOfficerProfile, SecretaryProfile, TrainingCoordinatorProfile,
    DeanOfStudentsProfile, LabTechnicianProfile,
)


def is_break_glass(user):
    """The emergency account: a Django superuser that holds no office.

    The ICT officer is a superuser too, for Django's admin site, and stays the
    ICT officer inside the app; a superuser holding any other office is that
    office."""
    if not (_signed_in(user) and user.is_superuser):
        return False
    if TeacherProfile.objects.filter(user=user).exists():
        return False
    return not any(model.objects.filter(user=user, is_active=True).exists()
                   for model in OFFICE_MODELS)


# ── the offices ───────────────────────────────────────────────────────────────

def is_principal(user):
    return _holds(PrincipalProfile, user)


def is_dparc(user):
    return _holds(DeputyAcademicProfile, user)


def is_dpfpa(user):
    return _holds(DeputyFinanceProfile, user)


def is_head_of_department(user):
    return _holds(HeadOfDepartmentProfile, user)


def is_exam_officer(user):
    return _holds(ExamOfficerProfile, user)


def is_ict_officer(user):
    return _holds(ICTOfficerProfile, user)


def is_coordinator(user):
    return _holds(TrainingCoordinatorProfile, user)


def is_dean(user):
    return _holds(DeanOfStudentsProfile, user)


def is_accountant(user):
    return _holds(AccountantProfile, user)


def is_estate_officer(user):
    return _holds(EstateOfficerProfile, user)


def is_lab_technician(user):
    return _holds(LabTechnicianProfile, user)


def can_approve_disposals(user):
    """Writing college property off: DPFPA, to whom the estate officer
    reports, approves or rejects what the estate officer proposes (said 17
    October 2026)."""
    return is_dpfpa(user) or is_break_glass(user)


def can_keep_lab_stock(user):
    """The laboratory's stock book: the lab technician's."""
    return is_lab_technician(user)


def can_read_lab_stock(user):
    """Read by the lab technician, and by DPFPA and the Principal."""
    return is_lab_technician(user) or is_dpfpa(user) or is_principal(user) or is_break_glass(user)


def is_secretary(user):
    return _holds(SecretaryProfile, user)


def is_records_officer(user):
    return _holds(RecordsOfficerProfile, user)


def is_admission_officer(user):
    return _holds(AdmissionOfficerProfile, user)


# ── what the offices may do ───────────────────────────────────────────────────

def is_academic_admin(user):
    """Sees the whole college's academic side: every module, class list,
    attendance register, result and form. What used to be `is_staff`.

    The Principal, DPARC, the examination officer and the Head of Department.
    Not DPFPA or the ICT officer: one reads the money, the other runs the
    system, and neither is the academic register's.
    """
    return (is_break_glass(user) or is_principal(user) or is_dparc(user)
            or is_exam_officer(user) or is_head_of_department(user))


def runs_departments(user):
    """Running the departments: registering modules and who teaches them,
    enrolling students, the staff list, the college's forms and service
    requests, sick records. The Principal, DPARC and the Head of Department —
    not the examination officer, who works from the modules and class lists
    the departments set up."""
    return (is_break_glass(user) or is_principal(user) or is_dparc(user)
            or is_head_of_department(user))


def can_manage_exams(user):
    """Entering end-of-semester marks, approving results, opening the mark
    windows, declaring a student for an exam, uploading authority grades,
    withholding results. The examination officer's alone."""
    return is_break_glass(user) or is_exam_officer(user)


def can_read_exams(user):
    """Marks, eligibility, the exports and the performance analysis."""
    return is_academic_admin(user)


def can_manage_accounts(user):
    """Creating staff accounts, resetting passwords, giving the roles that need
    no appointment. The ICT officer's work, with the Principal able to do it."""
    return is_break_glass(user) or is_ict_officer(user) or is_principal(user)


def can_appoint(user):
    """Giving or taking away a senior role. The Principal appoints, by letter."""
    return is_break_glass(user) or is_principal(user)


def can_manage_departments(user):
    """Departments and programmes are academic structure: the Principal and
    DPARC change them, everybody else reads them."""
    return is_break_glass(user) or is_principal(user) or is_dparc(user)


def can_handle_requests(user):
    """Whose desk a student's request lands on: the secretary's."""
    return is_break_glass(user) or is_secretary(user)


def can_decide_requests(user):
    """Who says yes or no to a request: the Principal, DPARC and the Head of
    Department. Not the secretary, who prepares it."""
    return (is_break_glass(user) or is_principal(user) or is_dparc(user)
            or is_head_of_department(user))


def can_prepare_reviews(user):
    """Working out what a semester's results mean for each student. The
    examination officer reviews the results; the deciders may too."""
    return (is_break_glass(user) or is_exam_officer(user)
            or is_principal(user) or is_dparc(user))


def can_confirm_reviews(user):
    """Deciding a semester review: who is promoted, who repeats, who is
    discontinued. The Principal and DPARC."""
    return is_break_glass(user) or is_principal(user) or is_dparc(user)


def can_record_returning_students(user):
    """Enters last year's position for a returning student the system has no
    results for (readmission, repeat, postponement), from the college's papers:
    the examination officer, who holds NACTVET's results, and the Principal and
    DPARC, who decide reviews."""
    return can_manage_exams(user) or can_confirm_reviews(user)


def can_advance_semester(user):
    """Closing one semester and opening the next. Same desk as confirming the
    reviews it acts on."""
    return can_confirm_reviews(user)


def can_view_finance_summary(user):
    """The executive view of the college's money: the Principal and DPFPA, and
    the accountant whose ledger it is drawn from. Read-only for all three."""
    return is_principal(user) or is_dpfpa(user) or is_accountant(user)


def can_read_inventory(user):
    """College property. The estate manager keeps it; DPFPA, whom they report
    to, and the Principal read it. Neither changes it."""
    return is_estate_officer(user) or is_dpfpa(user) or is_principal(user)


def runs_veta_courses(user):
    """The VETA courses' modules and class lists: the course (training)
    coordinator, with the Principal and DPARC above them."""
    return (is_break_glass(user) or is_coordinator(user) or is_principal(user)
            or is_dparc(user))


def can_approve_expenses(user):
    """Approving or rejecting an expense: the Principal or DPFPA. One
    approval is enough."""
    return is_principal(user) or is_dpfpa(user)


def can_view_expenses(user):
    """The expenses register: the accountant who keeps it, and the two
    offices that approve."""
    return is_accountant(user) or can_approve_expenses(user)


def can_reset_passwords(user):
    """Gives anybody a new password when they have forgotten theirs — staff,
    the Principal included, and students. The ICT officer's desk, with the
    Principal able to step in. Every reset is recorded and shown to the holder
    (passwords.py)."""
    return is_ict_officer(user) or is_principal(user) or is_break_glass(user)


def can_issue_student_passwords(user):
    """Sets a student's portal password — the first one too. The same two
    offices as above and nobody else (said 8 October 2026): not the admission
    or records desks, not tutors or Heads of Department. A password set by
    anybody else is a way into somebody's account, and only these two ever see
    a temporary password."""
    return can_reset_passwords(user)


def can_keep_health_records(user):
    """Writes a student's health record: the records office, which holds the
    certificate. The Principal may correct it."""
    return is_records_officer(user) or is_principal(user)


def can_read_health_records(user):
    """Reads them: the records office, the Dean of Students (for reference,
    looking after welfare), and the Principal. Not finance, not tutors."""
    return can_keep_health_records(user) or is_dean(user) or is_break_glass(user)


def follows_postponements(user):
    """Reads the postponement list. The Principal decides; DPARC and the Dean
    of Students are told."""
    return is_principal(user) or is_dparc(user) or is_dean(user)


#: Roles anybody who manages accounts may give. Every other role is an
#: appointment, given by the Principal.
OPEN_ROLES = frozenset({'tutor', 'secretary', 'lab_technician'})
