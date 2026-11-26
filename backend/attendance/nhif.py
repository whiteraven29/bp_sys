"""Who goes on the NHIF sheets, read once for records and for the accountant
(said 20 October and 26 November 2026), so the two lists tally.

Every PST student registered in the year is in one of three places:

  college   the medical fee is billed and not fully waived: the college insures
            them — on the NHIF sheets, unless records took them off as a mistake;
  own       insurance marked "Has it" and nothing of the medical fee left to pay
            (the mark waived it): their own card;
  none      no medical fee, or one waived for another reason.

The money decides, as the sheets always did (said 26 November 2026). For one
day "Has it" decided instead, and it took off students who had paid: a
continuing student holding last year's college NHIF card is often marked "Has
it", and a new one was marked so by mistake. A student marked "Has it" who
paid stays on the sheets; if the card really is their own, records take them
off (NhifRemoval) and the accountant settles the money paid.
"""

from decimal import Decimal

from django.db.models import Q

from . import finance
from .models import (
    AdmissionRequirement, NhifRemoval, Programme, RequirementCheck, SemesterRegistration, StudentCharge,
)

COLLEGE, OWN, NONE = 'college', 'own', 'none'
LABELS = {COLLEGE: 'College NHIF', OWN: 'Own card', NONE: 'Not billed'}


def medical_charges():
    return (Q(charge_type__name__icontains='medical') | Q(charge_type__name__icontains='insurance')
            | Q(charge_type__name__icontains='nhif'))


def insurance_requirements():
    """The admission items that are insurance: named so, or billed as the medical fee."""
    medical = (Q(charge_type__name__icontains='medical') | Q(charge_type__name__icontains='insurance')
               | Q(charge_type__name__icontains='nhif'))
    return AdmissionRequirement.objects.filter(
        Q(name__icontains='insurance') | Q(name__icontains='nhif') | medical)


def first_registrations(year, class_level=None):
    """Each PST student's first registration of the year — it decides the
    sheet (new or continuing) and the year of study."""
    registrations = (SemesterRegistration.objects
                     .filter(semester__academic_year=year, programme__structure=Programme.SEMESTER)
                     .exclude(status=SemesterRegistration.CANCELLED)
                     .select_related('profile', 'programme__department', 'class_level')
                     .order_by('created_at'))
    first = {}
    for registration in registrations:
        first.setdefault(registration.profile_id, registration)
    if class_level is not None:
        first = {pid: r for pid, r in first.items() if r.class_level_id == class_level.id}
    return first


def _marked_by(check):
    """Who marked insurance "Has it", and from which office — the admission
    desk could, until 26 November 2026."""
    if check is None:
        return None
    from .roles import is_accountant, is_admission_officer, is_principal
    user = check.checked_by
    if user is None:
        return {'name': '', 'office': '', 'at': check.checked_at}
    office = ('finance' if is_accountant(user) else 'admission' if is_admission_officer(user)
              else 'principal' if is_principal(user) else 'other')
    name = (user.get_full_name() or user.username)
    return {'name': name, 'office': office, 'at': check.checked_at}


def roster(year, class_level=None):
    """{profile id: row} for every student of the year: where they stand for
    NHIF, the medical fee (billed, waived, paid) and any removal by records."""
    first = first_registrations(year, class_level)
    money = {}
    for charge in finance.with_balances(StudentCharge.objects.filter(
            medical_charges(), academic_year=year, profile_id__in=first)):
        total = money.setdefault(charge.profile_id, {'billed': Decimal('0'), 'waived': Decimal('0'),
                                                     'paid': Decimal('0')})
        total['billed'] += charge.amount
        total['waived'] += charge.waived_amount or 0
        total['paid'] += charge.amount - (charge.waived_amount or 0) - finance.charge_balance(charge)
    # The "Has it" mark, kept with where it was made: the accountant can turn a
    # wrong one round from the NHIF list (said 26 November 2026).
    own = {}
    for check in RequirementCheck.objects.filter(
            profile_id__in=first, semester__academic_year=year, requirement__in=insurance_requirements(),
            status=RequirementCheck.HAS_IT).select_related('checked_by').order_by('checked_at'):
        own[check.profile_id] = check
    insurance = (insurance_requirements().filter(kind=AdmissionRequirement.BILLED)
                 .exclude(charge_type__isnull=True).order_by('id').first())
    removals = {r.profile_id: r for r in NhifRemoval.objects.filter(
        academic_year=year, profile_id__in=first, put_back_at__isnull=True).select_related('removed_by')}
    rows = {}
    for profile_id, registration in first.items():
        fee = money.get(profile_id, {'billed': Decimal('0'), 'waived': Decimal('0'), 'paid': Decimal('0')})
        if fee['billed'] - fee['waived'] > 0:
            place = COLLEGE
        elif profile_id in own:
            place = OWN
        else:
            place = NONE
        removal = removals.get(profile_id)
        # The one correction for a wrong mark, made as on Admissions ▸ Items check:
        # own card → "Missing — on the bill" (the medical fee comes back, the
        # college insures them); college with nothing paid → "Has it" (the fee
        # comes off). Once money is paid the mark moves nothing: records take a
        # student with their own card off the sheets instead.
        if place == OWN:
            check = own[profile_id]
            fix = {'semester_id': check.semester_id, 'requirement_id': check.requirement_id, 'status': 'missing'}
        elif place == COLLEGE and insurance is not None and fee['paid'] <= 0 and profile_id not in own:
            fix = {'semester_id': registration.semester_id, 'requirement_id': insurance.id, 'status': 'has_it'}
        else:
            fix = None
        rows[profile_id] = {
            'fix': fix, 'marked_own': profile_id in own,
            'marked_by': _marked_by(own.get(profile_id)),
            'registration': registration, 'place': place, 'fee': fee, 'removal': removal,
            'on_sheet': place == COLLEGE and removal is None,
            'new': registration.kind == SemesterRegistration.NEW,
        }
    return rows
