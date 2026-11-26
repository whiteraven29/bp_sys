"""Who goes on the NHIF sheets, read once for records and for the accountant
(said 20 October and 26 November 2026), so the two lists tally.

Every PST student registered in the year is in one of three places:

  college   the medical fee is billed and not fully waived, and insurance is not
            marked "Has it": the college insures them — on the NHIF sheets,
            unless records took them off as a mistake;
  own       insurance marked "Has it" at finance or admission: their own card;
  none      no medical fee billed, or waived for another reason.

"Has it" wins over money: a student who paid the medical fee and then showed
their own card is "own" (the accountant settles the money paid).
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
    own = set(RequirementCheck.objects.filter(
        profile_id__in=first, semester__academic_year=year, requirement__in=insurance_requirements(),
        status=RequirementCheck.HAS_IT).values_list('profile_id', flat=True))
    removals = {r.profile_id: r for r in NhifRemoval.objects.filter(
        academic_year=year, profile_id__in=first, put_back_at__isnull=True).select_related('removed_by')}
    rows = {}
    for profile_id, registration in first.items():
        fee = money.get(profile_id, {'billed': Decimal('0'), 'waived': Decimal('0'), 'paid': Decimal('0')})
        if profile_id in own:
            place = OWN
        elif fee['billed'] - fee['waived'] > 0:
            place = COLLEGE
        else:
            place = NONE
        removal = removals.get(profile_id)
        rows[profile_id] = {
            'registration': registration, 'place': place, 'fee': fee, 'removal': removal,
            'on_sheet': place == COLLEGE and removal is None,
            'new': registration.kind == SemesterRegistration.NEW,
        }
    return rows
