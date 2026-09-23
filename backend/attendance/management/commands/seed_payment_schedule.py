"""Load the college's instalment dates — "Fomu ya tarehe za awamu za malipo ya
ada na michango mingine" — for an academic year.

    python manage.py seed_payment_schedule --year 2026/2027
    python manage.py seed_payment_schedule --year 2026/2027 --replace

Two schedules, as the form has them: one for new students (level 4, and new
entrants at levels 5 and 6), one for continuing students at levels 5 and 6. The
hostel fee has its own instalments inside each; a day student simply is not
charged them.

The amounts are not typed in here. Each charge type's whole fee is read from
the year's fee structure (`seed_fee_structure` loads it) and placed at the
instalment the form puts it in; tuition is split as the form splits it. The
result is then checked against every subtotal the form prints, day and hostel,
and nothing is saved unless all of them match.

The one thing the form does not say is which of the "michango mingine" make up
the new student's second instalment of 350,000. The three charges for later in
the year — research/field fees, the semester II national examination and
graduation — add up to exactly that, so they are placed there. The accountant
can move any of them in Fee Setup ▸ Payment schedule.

For the next year, copy this one forward from that screen ("Start from last
year") and edit the dates and amounts, rather than running this again.
"""

from datetime import date
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from attendance import finance
from attendance.models import (
    AcademicYear, ChargeType, ClassLevel, FeeStructure, PaymentSchedule,
)

TUITION = 'Tuition Fee'

#: The new students' instalments: (due by, semester), and the tuition due at each.
NEW_STEPS = [
    (date(2026, 10, 19), 1), (date(2026, 12, 18), 1), (date(2027, 1, 31), 1),
    (date(2027, 3, 25), 2), (date(2027, 5, 30), 2),
]
NEW_TUITION = [400_000, 200_000, 200_000, 400_000, 400_000]
#: Other charges a new student pays at the second instalment; the rest are due
#: at the first.
NEW_LATER = {'Research/field fees', 'National Examination (Semester II)', 'Graduation fees'}
NEW_HOSTEL = {1: 200_000, 4: 200_000}

CONTINUING_STEPS = [
    (date(2026, 10, 19), 1), (date(2027, 1, 31), 1), (date(2027, 3, 25), 2), (date(2027, 5, 30), 2),
]
CONTINUING_TUITION = [400_000, 400_000, 400_000, 400_000]
CONTINUING_HOSTEL = {1: 200_000, 3: 200_000}

#: The SUB TOTAL rows of the form, per instalment: day, then hostel.
FORM_TOTALS = {
    PaymentSchedule.NEW: ([945_000, 550_000, 200_000, 400_000, 400_000],
                          [1_145_000, 550_000, 200_000, 600_000, 400_000]),
    PaymentSchedule.CONTINUING: ([1_005_000, 400_000, 400_000, 400_000],
                                 [1_205_000, 400_000, 600_000, 400_000]),
}


class Command(BaseCommand):
    help = "Load the college's instalment dates (awamu za malipo) for new and continuing students."

    def add_arguments(self, parser):
        parser.add_argument('--year', required=True, help='Academic year, e.g. 2026/2027')
        parser.add_argument('--replace', action='store_true',
                            help='Rewrite schedules that already exist for the year.')

    def handle(self, *args, **options):
        year = AcademicYear.objects.filter(name=options['year']).first()
        if year is None:
            raise CommandError(f"Academic year {options['year']} does not exist.")
        if year.name != '2026/2027':
            self.stdout.write(self.style.WARNING(
                f'The dates in this command are the 2026/2027 form\'s. For {year.name}, copy the '
                'previous year forward in Fee Setup ▸ Payment schedule and edit it there.'))
        existing = PaymentSchedule.objects.filter(academic_year=year)
        if existing.exists() and not options['replace']:
            raise CommandError(f'{year} already has a payment schedule. Use --replace to rewrite it.')

        hostel = finance.hostel_charge_type()
        if hostel is None:
            raise CommandError('No charge type is marked as the hostel fee. Run seed_fee_structure first.')

        with transaction.atomic():
            new = self.build(year, PaymentSchedule.NEW, self.level(4), NEW_STEPS, NEW_TUITION,
                             NEW_HOSTEL, hostel, later=NEW_LATER, include_once=True)
            continuing = self.build(year, PaymentSchedule.CONTINUING, self.level(5), CONTINUING_STEPS,
                                    CONTINUING_TUITION, CONTINUING_HOSTEL, hostel)
            ok = self.reconcile(new, hostel) & self.reconcile(continuing, hostel)
            if not ok:
                transaction.set_rollback(True)
                raise CommandError('The schedule does not match the form; nothing was saved. '
                                   'Check the fee structure amounts for the year.')
        self.stdout.write(self.style.SUCCESS(f'Payment schedules saved for {year}.'))
        self.stdout.write('Other charges placed at the new students\' second instalment: '
                          + ', '.join(sorted(NEW_LATER)) + ' — confirm with the accountant.')

    # ── building ──────────────────────────────────────────────────────────────

    def level(self, order):
        level = ClassLevel.objects.filter(order=order).first()
        if level is None:
            raise CommandError(f'No NTA level {order}.')
        return level

    def build(self, year, entry, level, steps, tuition, hostel_parts, hostel, *, later=(), include_once=False):
        structures = (FeeStructure.objects
                      .filter(academic_year=year, class_level=level, is_active=True, programme__isnull=True,
                              charge_type__is_active=True, charge_type__applies=ChargeType.AUTOMATIC)
                      .select_related('charge_type'))
        amounts = [dict() for _ in steps]
        for structure in structures:
            charge_type = structure.charge_type
            if structure.billing_period == FeeStructure.ONCE and not include_once:
                continue
            if charge_type.name == TUITION:
                if Decimal(sum(tuition)) != structure.amount:
                    raise CommandError(f'Tuition is {structure.amount} for {year}; the form splits '
                                       f'{sum(tuition)}.')
                for index, part in enumerate(tuition):
                    amounts[index][charge_type.id] = Decimal(part)
            else:
                index = 1 if charge_type.name in later else 0
                amounts[index][charge_type.id] = structure.amount
        for number, part in hostel_parts.items():
            amounts[number - 1][hostel.id] = Decimal(part)

        return finance.save_schedule(year, entry, [
            {'due_date': due, 'semester_number': semester, 'amounts': amounts[index]}
            for index, (due, semester) in enumerate(steps)
        ], note='From the 2026/2027 instalment form')

    # ── checking against the form ─────────────────────────────────────────────

    def reconcile(self, schedule, hostel):
        day_form, hostel_form = FORM_TOTALS[schedule.entry]
        self.stdout.write(self.style.MIGRATE_HEADING(f'\n{schedule.get_entry_display()}'))
        ok = True
        for step, day_expected, hostel_expected in zip(schedule.steps.all(), day_form, hostel_form):
            day = sum((line.amount for line in step.amounts.all() if line.charge_type_id != hostel.id),
                      Decimal(0))
            with_hostel = sum((line.amount for line in step.amounts.all()), Decimal(0))
            match = day == day_expected and with_hostel == hostel_expected
            ok &= match
            self.stdout.write(f'  awamu {step.number} by {step.due_date:%d/%m/%Y} (semester {step.semester_number}): '
                              f'day {day:,.0f} / hostel {with_hostel:,.0f}  '
                              + (self.style.SUCCESS('matches the form') if match else self.style.ERROR(
                                  f'form says {day_expected:,} / {hostel_expected:,}')))
        return ok
