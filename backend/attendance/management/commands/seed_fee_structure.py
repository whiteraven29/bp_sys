"""Load Blue Pharma College of Health's published fee structure.

Everything here comes from the 2026/2027 Admission Application Form: the two
CRDB accounts, the tuition fee, and both "other charges" tables. The two tables
in that document are not two structures — they are the same structure seen from
different years. What separates a first-year's bill from a continuing
student's is only which items are charged `once`, so one catalogue produces
both, and nobody has to maintain two lists that drift apart.

Safe to re-run: it updates in place rather than duplicating.
"""

from datetime import date
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from attendance import finance
from attendance.models import (
    AcademicYear, BankAccount, ChargeType, ClassLevel, CollegeProfile, FeeStructure,
)

TUITION_ACCOUNT = '0150417961300'
OTHER_ACCOUNT = '0150417961301'

# name, family, frequency, applies, invoice_group, day amount, blocks…
FEE = ChargeType.FEE
DIRECT = ChargeType.DIRECT_COST
OTHER = ChargeType.OTHER
ONCE = ChargeType.ONCE
YEARLY = ChargeType.EACH_YEAR
AUTO = ChargeType.AUTOMATIC
OPTIONAL = ChargeType.OPTIONAL

# blocks: registration, cat1, cat2, final, results
#
# The Other Charges rows below are the admission form's table on page 9, in its
# published order, so the invoice a student receives can be checked against the
# form they were given, row by row. Page 10 is not a second table — it is the
# same one with the `once` rows struck out, which is why there is one catalogue
# here and not two:
#
#     Level 4, and new entrants at 5 and 6      895,000 day · 1,295,000 hostel
#     Continuing students at levels 5 and 6     605,000 day · 1,005,000 hostel
#
# The 400,000 difference in each pair is Accommodation, row 15, which only
# hostel residents are charged. The 290,000 difference between the pairs is the
# six `once` rows, which a continuing student has already paid.
CATALOGUE = [
    # (SN, name, family, frequency, applies, group, amount, account, blocks)
    (0, 'Tuition Fee', FEE, YEARLY, AUTO, 'Tuition Fee', 1_600_000, TUITION_ACCOUNT,
     (True, True, True, True, True)),

    (1, 'Registration fees', DIRECT, YEARLY, AUTO, 'Direct Costs', 10_000, OTHER_ACCOUNT,
     (True, False, False, False, False)),
    (2, 'Examination fees (Semester 1)', DIRECT, YEARLY, AUTO, 'Direct Costs', 150_000, OTHER_ACCOUNT,
     (False, True, True, False, False)),
    (3, 'Medical fees (Health Insurance)', DIRECT, YEARLY, AUTO, 'Direct Costs', 60_000, OTHER_ACCOUNT,
     (True, False, False, False, False)),
    (4, 'Research/field fees', DIRECT, YEARLY, AUTO, 'Direct Costs', 150_000, OTHER_ACCOUNT,
     (False, False, False, True, True)),
    (5, 'Caution money', DIRECT, ONCE, AUTO, 'Direct Costs', 50_000, OTHER_ACCOUNT,
     (True, False, False, False, False)),
    (6, 'Student union', DIRECT, YEARLY, AUTO, 'Direct Costs', 10_000, OTHER_ACCOUNT,
     (True, False, False, False, False)),
    (7, 'Admission fee', DIRECT, ONCE, AUTO, 'Direct Costs', 50_000, OTHER_ACCOUNT,
     (True, False, False, False, False)),
    (8, 'National Examination (Semester II)', DIRECT, YEARLY, AUTO, 'Direct Costs', 150_000, OTHER_ACCOUNT,
     (False, False, False, True, True)),
    (9, 'Identity card', DIRECT, ONCE, AUTO, 'Direct Costs', 10_000, OTHER_ACCOUNT,
     (False, False, False, False, False)),
    (10, 'Clinical coat', DIRECT, ONCE, AUTO, 'Direct Costs', 30_000, OTHER_ACCOUNT,
     (False, False, False, False, False)),
    (11, 'Graduation fees', DIRECT, ONCE, AUTO, 'Direct Costs', 50_000, OTHER_ACCOUNT,
     (False, False, False, False, True)),
    (12, 'Continuous assessment Tests', DIRECT, YEARLY, AUTO, 'Direct Costs', 50_000, OTHER_ACCOUNT,
     (False, True, True, False, False)),
    (13, 'Uniforms', DIRECT, ONCE, AUTO, 'Direct Costs', 100_000, OTHER_ACCOUNT,
     (False, False, False, False, False)),
    (14, 'NACTE Quality Assurance Fee', DIRECT, YEARLY, AUTO, 'Direct Costs', 25_000, OTHER_ACCOUNT,
     (False, False, False, False, True)),
    # Row 15 of the same table — the whole of the difference between its "Day"
    # and "Hostel" columns. Optional, so only students given a hostel place are
    # charged it, and only then does it appear on their direct-costs invoice.
    (15, 'Accommodation', DIRECT, YEARLY, OPTIONAL, 'Direct Costs', 400_000, OTHER_ACCOUNT,
     (True, False, False, False, False)),

    # Not part of the Other Charges table — note 2 on page 11, a service a
    # student opts into, so it is billed and invoiced on its own.
    (20, 'Meals', OTHER, YEARLY, OPTIONAL, 'Meals', 1_500_000, OTHER_ACCOUNT,
     (False, False, False, False, False)),

    # Raised by hand when the examination officer declares them.
    (30, 'Supplementary Exam', OTHER, YEARLY, ChargeType.ON_REQUEST, 'Examination Charges',
     0, OTHER_ACCOUNT, (False, False, False, True, True)),
    (31, 'Special Exam', OTHER, YEARLY, ChargeType.ON_REQUEST, 'Examination Charges',
     0, OTHER_ACCOUNT, (False, False, False, True, True)),
    (32, 'Repeat Module', OTHER, YEARLY, ChargeType.ON_REQUEST, 'Examination Charges',
     0, OTHER_ACCOUNT, (True, False, False, False, False)),
]

# Tuition is payable in full or five instalments; other charges and
# accommodation in full or two. (Admission form, "Payment Instalments".)
INSTALMENTS = {'Tuition Fee': 5, 'Accommodation': 2, 'Meals': 2}
DEFAULT_INSTALMENTS = 2

TERMS = """Fees paid to the college are not refunded except under the BPHACOH refund policy.
Deposit into the account shown above and write this invoice number on the pay-in slip.
Payment by M-Pesa, Mixx by Yas, Airtel Money or any other mobile application is NOT accepted.
Bring the original bank pay-in slip to the Accounts Office to be receipted."""


class Command(BaseCommand):
    help = "Load BPHACOH's published fee structure for an academic year."

    def add_arguments(self, parser):
        parser.add_argument('--year', required=True, help='Academic year, e.g. 2026/2027')
        parser.add_argument(
            '--first-due', default=None,
            help='Date the first instalment falls due (YYYY-MM-DD). Later ones are spread '
                 'across the year from there.',
        )

    @transaction.atomic
    def handle(self, *args, **options):
        year = AcademicYear.objects.filter(name=options['year']).first()
        if year is None:
            raise CommandError(
                f"Academic year {options['year']} does not exist. Create it first."
            )
        levels = list(ClassLevel.objects.order_by('order'))
        if not levels:
            raise CommandError('No class levels exist. Create NTA Level 4, 5 and 6 first.')

        first_due = date.fromisoformat(options['first_due']) if options['first_due'] else None

        profile, _ = CollegeProfile.objects.get_or_create(
            id=CollegeProfile.objects.values_list('id', flat=True).first() or None,
            defaults={},
        )
        for field, value in [
            ('name', 'Blue Pharma College of Health'), ('short_name', 'BPHACOH'),
            ('po_box', 'P. O. Box 1570'), ('town', 'Singida'), ('country', 'Tanzania'),
            ('phone', '0743358048, 0620323644, 0786480125'),
            ('email', 'info@bphacoh.ac.tz'), ('website', 'www.bphacoh.ac.tz'),
        ]:
            setattr(profile, field, value)
        if not profile.invoice_terms:
            profile.invoice_terms = TERMS
        profile.save()
        self.stdout.write(self.style.SUCCESS(f'College profile: {profile.name}'))

        accounts = {}
        for number, purpose in [
            (TUITION_ACCOUNT, 'Tuition fee'),
            (OTHER_ACCOUNT, 'Other charges & accommodation'),
        ]:
            account, _ = BankAccount.objects.update_or_create(
                account_number=number,
                defaults={
                    'bank_name': 'CRDB',
                    'account_name': 'BLUE PHARMA COLLEGE OF HEALTH',
                    'purpose': purpose, 'is_active': True,
                },
            )
            accounts[number] = account
            self.stdout.write(f'  account {number} — {purpose}')

        created_types, structures = 0, 0
        for (sn, name, family, frequency, applies, group, amount, account_no, blocks) in CATALOGUE:
            reg, c1, c2, fin, res = blocks
            charge_type, made = ChargeType.objects.update_or_create(
                name=name,
                defaults={
                    'family': family, 'frequency': frequency, 'applies': applies,
                    'sort_order': sn, 'invoice_group': group,
                    'bank_account': accounts[account_no],
                    'blocks_registration': reg, 'blocks_cat1': c1, 'blocks_cat2': c2,
                    'blocks_final': fin, 'blocks_results': res, 'is_active': True,
                },
            )
            created_types += int(made)

            if applies == ChargeType.ON_REQUEST or not amount:
                continue   # priced by the accountant when they raise it

            period = (FeeStructure.ONCE if frequency == ChargeType.ONCE
                      else FeeStructure.ACADEMIC_YEAR)
            count = INSTALMENTS.get(name, DEFAULT_INSTALMENTS)
            for level in levels:
                structure, _ = FeeStructure.objects.update_or_create(
                    charge_type=charge_type, class_level=level, academic_year=year,
                    defaults={
                        'amount': Decimal(amount), 'billing_period': period,
                        'installments': count, 'is_active': True,
                    },
                )
                structures += 1
                if first_due and not structure.installment_schedule.exists():
                    step = max(1, 10 // count)
                    finance.set_installment_schedule(structure, [
                        _add_months(first_due, i * step) for i in range(count)
                    ])

        self.stdout.write(self.style.SUCCESS(
            f'{len(CATALOGUE)} charge types ({created_types} new), '
            f'{structures} fee-structure cells for {year.name}.'
        ))
        if not first_due:
            self.stdout.write(self.style.WARNING(
                'No --first-due given, so no instalment due dates were set. Charges cannot '
                'be generated until every fee has a schedule — set them on the Fee Structure '
                'screen, or re-run with --first-due YYYY-MM-DD.'
            ))


def _add_months(start, months):
    month = start.month - 1 + months
    year = start.year + month // 12
    month = month % 12 + 1
    day = min(start.day, [31, 29 if year % 4 == 0 and (year % 100 or year % 400 == 0) else 28,
                          31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
    return date(year, month, day)
