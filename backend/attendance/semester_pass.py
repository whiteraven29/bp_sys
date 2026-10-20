"""The semester pass (said 20 October 2026).

At the start of a semester every student comes back through the desks with
their payment, and the admission office gives them a pass to enter class. The
pass is given only to a student registered for the semester, still studying,
and paid up to what the payment schedule says is due by the semester's first
instalment — the second semester's instalment for semester 2. A student who
has not paid it gets no pass, and is blocked from that semester's exams by the
same clearance rule (finance.exam_clearance).

The admission office prints it; the student may download it from the portal,
to spare the office the queue. Either way it carries a check code made from
the college's secret key, the student and the semester: a pass edited by hand
— another name, another semester — does not match, and anybody on the staff
can check a pass at /semester-pass/check/.
"""

from datetime import date

from django.utils.crypto import constant_time_compare, salted_hmac

from . import finance
from .models import (
    ChargeType, PaymentStep, Semester, SemesterRegistration, StudentCharge, StudentProfile, StudentStanding,
)

STUDYING = {StudentStanding.ACTIVE, StudentStanding.REPEATING}


def active_semester():
    return Semester.objects.filter(is_active=True).select_related('academic_year').first()


def check_code(profile, semester):
    """Ten characters only the college can make for this student and semester."""
    digest = salted_hmac('edutrack.semester-pass', f'{profile.pk}:{semester.pk}').hexdigest().upper()
    return f'{digest[:5]}-{digest[5:10]}'


def due_by(profile, semester, today=None):
    """The date the pass is measured at: the semester's first instalment in the
    student's payment schedule (new or continuing), or today if later."""
    today = today or date.today()
    entry = finance.entry_for(profile, semester.academic_year)
    first = (PaymentStep.objects.filter(schedule__academic_year=semester.academic_year,
                                        schedule__entry=entry, semester_number=semester.number)
             .order_by('due_date').values_list('due_date', flat=True).first())
    return max(today, first) if first else today


def status(profile, semester=None, today=None):
    """Whether this student may have the pass, and if not, why not."""
    semester = semester or active_semester()
    if semester is None:
        return {'ok': False, 'reason': 'No semester is open.'}
    registration = (SemesterRegistration.objects.filter(profile=profile, semester=semester)
                    .exclude(status=SemesterRegistration.CANCELLED)
                    .select_related('programme', 'class_level').first())
    base = {'semester': str(semester), 'semester_id': semester.id, 'name': profile.name,
            'college_id': profile.college_id or '', 'reg_no': profile.nactvet_reg_no,
            'profile_id': profile.id}
    if registration is None:
        return {**base, 'ok': False,
                'reason': f'Not registered for {semester}: admitted students only.'}
    standing = StudentStanding.objects.filter(profile=profile).first()
    if standing is not None and standing.status not in STUDYING:
        return {**base, 'ok': False, 'reason': f'Their status is {standing.get_status_display()}.'}
    if not StudentCharge.objects.filter(profile=profile, academic_year=semester.academic_year).exists():
        # Nothing owed is not the same as paid: the year's bill, raised at the
        # finance desk, is what makes them a registered student.
        return {**base, 'ok': False,
                'reason': f'No bill has been raised for {semester.academic_year} — see the finance desk.'}
    as_of = due_by(profile, semester, today)
    cleared = finance.exam_clearance(profile, semester.academic_year, ChargeType.REGISTRATION,
                                     semester=semester, today=as_of)
    base.update(programme=registration.programme.name, programme_code=registration.programme.code,
                class_level=registration.class_level.name, due_by=as_of)
    if not cleared['cleared']:
        return {**base, 'ok': False, 'balance': cleared['balance'],
                'reason': f'Not paid what is due by {as_of:%d %b %Y}: {cleared["reason"]}.'}
    return {**base, 'ok': True, 'code': check_code(profile, semester),
            'overridden': cleared.get('overridden', False)}


def find_profile(identifier):
    """A student by registration number or college ID."""
    from django.db.models import Q
    value = str(identifier or '').strip()
    if not value:
        return None
    return StudentProfile.objects.filter(Q(nactvet_reg_no__iexact=value) | Q(college_id__iexact=value)).first()


def verify(identifier, code, today=None):
    """Check a pass someone shows: the code must be the one the college made
    for this student and this semester, and the student must still qualify."""
    profile = find_profile(identifier)
    semester = active_semester()
    if profile is None or semester is None:
        return {'genuine': False, 'reason': 'No such student.'}
    genuine = constant_time_compare(str(code or '').strip().upper(), check_code(profile, semester))
    if not genuine:
        return {'genuine': False, 'reason': f'This code was not issued to {profile.name} for {semester}.'}
    return {'genuine': True, 'now': status(profile, semester, today), 'profile': profile}
