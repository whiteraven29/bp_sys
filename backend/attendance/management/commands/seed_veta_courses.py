"""Load the cosmetology short courses and their fees, from the college's form.

The form ("Kozi zinazotolewa", page 3) lists two courses:

    Ususi na Urembo    (hair dressing and beauty)   4 months
    Mafunzo ya Kunyoa  (hair cutting)               3 months
    Entry: the applicant can read and write. No documents are asked for.

and the fees ("Mchanganuo wa ada na michango mingine"):

                           Day (kutwa)                  Hostel (bweni)
                     Ada    Awamu 1  Awamu 2      Hostel  Awamu 1  Awamu 2  Total
    Ususi na Urembo  450,000 300,000 150,000     250,000  500,000  200,000  700,000
    Mafunzo ya Kunyoa 400,000 300,000 100,000    250,000  450,000  200,000  650,000

    Awamu 1 is paid when the student reports to register; awamu 2 after the
    first month. So the hostel fee's own share of each instalment is the
    hostel column less the day column: 200,000 + 50,000 (Ususi) and 150,000 +
    100,000 (Kunyoa).

    Every student, day or hostel, also pays once: uniform (sare) 50,000,
    identity card 10,000, caution money (tahadhari) 20,000, examinations
    (mitihani) 20,000 — 100,000 in all.

The money goes into the same two CRDB accounts as PST: the course fee to the
tuition account, everything else to the other-charges account. The charge types
are the VETA courses' own, so nothing PST bills or checks is touched.

Awamu 1 of the course fee and of the hostel fee is marked "to be paid before
admission", as the form says it is paid on reporting. The once-only charges are
due at the start and not marked: the accountant decides, and can change either
under Fee Setup ▸ Course Fees.

    python manage.py seed_veta_courses --year 2026/2027 --department VOC
"""

from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from attendance.models import (
    AcademicYear, BankAccount, ChargeType, CourseFee, CourseFeeInstalment, Department, Programme,
)

TUITION_ACCOUNT = '0150417961300'
OTHER_ACCOUNT = '0150417961301'

# (code, name, months, course fee parts, hostel fee parts) — parts are
# (days after the start, amount, before admission)
COURSES = [
    ('HDB', 'Hair Dressing and Beautician (Ususi na Urembo)', 4,
     [(0, 300_000, True), (30, 150_000, False)],
     [(0, 200_000, True), (30, 50_000, False)]),
    ('HCT', 'Hair Cutting (Mafunzo ya Kunyoa)', 3,
     [(0, 300_000, True), (30, 100_000, False)],
     [(0, 150_000, True), (30, 100_000, False)]),
]
# The form's own figures, to check against: day total, hostel total,
# hostel awamu 1, hostel awamu 2.
FORM = {
    'HDB': {'day': 450_000, 'hostel': 700_000, 'hostel_1': 500_000, 'hostel_2': 200_000},
    'HCT': {'day': 400_000, 'hostel': 650_000, 'hostel_1': 450_000, 'hostel_2': 200_000},
}
ONCE = [('Uniform (VETA)', 50_000), ('Identity card (VETA)', 10_000),
        ('Caution money (VETA)', 20_000), ('Examinations (VETA)', 20_000)]
ONCE_TOTAL = 100_000

# name, family, invoice group, account
CHARGE_TYPES = {
    'Course fee (VETA)': (ChargeType.FEE, 'Course fee', TUITION_ACCOUNT),
    'Hostel (VETA)': (ChargeType.DIRECT_COST, 'Course charges', OTHER_ACCOUNT),
    **{name: (ChargeType.DIRECT_COST, 'Course charges', OTHER_ACCOUNT) for name, _ in ONCE},
}


class Command(BaseCommand):
    help = 'Load the cosmetology short courses (Ususi na Urembo, Mafunzo ya Kunyoa) and their fees.'

    def add_arguments(self, parser):
        parser.add_argument('--year', required=True, help='Academic year, e.g. 2026/2027')
        parser.add_argument('--department', required=True,
                            help='Code of the department the courses belong to (create it first '
                                 'under Departments).')

    @transaction.atomic
    def handle(self, *args, **options):
        year = AcademicYear.objects.filter(name=options['year']).first()
        if year is None:
            raise CommandError(f'No academic year {options["year"]}.')
        department = Department.objects.filter(code__iexact=options['department']).first()
        if department is None:
            raise CommandError(f'No department with code {options["department"]}. The Principal adds it '
                               f'under Departments first.')
        accounts = {number: BankAccount.objects.filter(account_number=number).first()
                    for number in (TUITION_ACCOUNT, OTHER_ACCOUNT)}
        missing = [number for number, account in accounts.items() if account is None]
        if missing:
            raise CommandError(f'The PST bank accounts are not set up ({", ".join(missing)}). '
                               f'Run seed_fee_structure first.')

        # Check the figures against the form before anything is saved.
        for code, _name, _months, course, hostel in COURSES:
            day = sum(amount for _, amount, _ in course)
            hostel_total = day + sum(amount for _, amount, _ in hostel)
            first = course[0][1] + hostel[0][1]
            second = course[1][1] + hostel[1][1]
            form = FORM[code]
            if (day, hostel_total, first, second) != (form['day'], form['hostel'], form['hostel_1'], form['hostel_2']):
                raise CommandError(f'{code} does not match the form; nothing saved.')
            self.stdout.write(f'{code}: day {day:,} · hostel {hostel_total:,} '
                              f'(awamu 1 {first:,}, awamu 2 {second:,}) — matches the form')
        if sum(amount for _, amount in ONCE) != ONCE_TOTAL:
            raise CommandError('The once-only charges do not add up to 100,000; nothing saved.')
        self.stdout.write(f'Once-only charges: {ONCE_TOTAL:,} — matches the form')

        types = {}
        for name, (family, group, account) in CHARGE_TYPES.items():
            types[name], _ = ChargeType.objects.update_or_create(
                name=name, defaults={
                    'family': family, 'invoice_group': group, 'bank_account': accounts[account],
                    'applies': ChargeType.ON_REQUEST, 'frequency': ChargeType.ONCE,
                    'is_hostel': False, 'is_active': True,
                    'blocks_registration': False, 'blocks_cat1': False, 'blocks_cat2': False,
                    'blocks_final': False, 'blocks_results': False,
                })

        for code, name, months, course, hostel in COURSES:
            programme = Programme.objects.filter(code=code).first()
            if programme is None:
                programme = Programme.objects.create(
                    department=department, name=name, code=code, structure=Programme.SHORT,
                    duration_months=months)
                self.stdout.write(f'  course {code} added to {department.name}')
            elif programme.structure != Programme.SHORT:
                raise CommandError(f'{code} already exists and is not a VETA short course.')
            fees = [('Course fee (VETA)', course, False), ('Hostel (VETA)', hostel, True)]
            fees += [(once_name, [(0, amount, False)], False) for once_name, amount in ONCE]
            for charge_name, parts, hostel_only in fees:
                fee, _ = CourseFee.objects.update_or_create(
                    programme=programme, academic_year=year, charge_type=types[charge_name],
                    defaults={'amount': Decimal(sum(amount for _, amount, _ in parts)),
                              'hostel_only': hostel_only})
                fee.instalments.all().delete()
                for number, (days, amount, before) in enumerate(parts, start=1):
                    CourseFeeInstalment.objects.create(fee=fee, number=number, amount=Decimal(amount),
                                                       due_after_days=days, before_admission=before)
        self.stdout.write(self.style.SUCCESS(
            f'Cosmetology courses and fees saved for {year}. The accountant can change any of it '
            f'under Fee Setup ▸ Course Fees (VETA).'))
