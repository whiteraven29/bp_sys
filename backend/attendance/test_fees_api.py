"""The counter flow, exercised through the API.

Walks the scenario the design is built around: the student generates an
invoice, a parent pays at CRDB quoting the reference, the student brings the
slip in, the accountant records it once — and clearance follows on its own.
"""

from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from . import finance
from .models import (
    AcademicYear, AccountantProfile, BankAccount, ChargeType, ClassLevel, CollegeProfile,
    FeeStructure, Invoice, Module, Payment, Semester, Student, StudentCharge,
    StudentProfile, TeacherProfile,
)

User = get_user_model()


class FeesLedgerAPITests(TestCase):
    def setUp(self):
        self.year = AcademicYear.objects.create(name='2025/2026', is_active=True)
        self.sem1 = Semester.objects.create(
            academic_year=self.year, number=1, is_active=True,
            cat1_cutoff=date(2025, 10, 15), cat2_cutoff=date(2025, 11, 20),
            end_cutoff=date(2025, 12, 10),
        )
        self.level4 = ClassLevel.objects.create(name='NTA Level 4', order=4)
        self.module = Module.objects.create(
            name='Anatomy', code='ANA101', teacher='T',
            class_level=self.level4, semester=self.sem1,
        )
        self.enrollment = Student.objects.create(
            nactvet_reg_no='REG/001', name='Asha Juma', module=self.module,
        )
        self.enrollment.set_portal_pin('Portal#2025', require_change=False)
        self.enrollment.save()
        self.asha = finance.profile_for_student(self.enrollment)

        self.admin = User.objects.create_superuser('admin', 'a@b.c', 'pw')
        self.accountant = User.objects.create_user('accountant', password='pw')
        AccountantProfile.objects.create(user=self.accountant, full_name='Finance Officer')
        self.tutor = User.objects.create_user('tutor', password='pw')
        TeacherProfile.objects.create(user=self.tutor, full_name='Tutor')

        # The college banks tuition and other charges separately.
        self.tuition_account = BankAccount.objects.create(
            bank_name='CRDB', account_name='BLUE PHARMA COLLEGE OF HEALTH',
            account_number='0150417961300', purpose='Tuition fee')
        self.other_account = BankAccount.objects.create(
            bank_name='CRDB', account_name='BLUE PHARMA COLLEGE OF HEALTH',
            account_number='0150417961301', purpose='Other charges & accommodation')

        self.api = APIClient()

    def _setup_fees(self):
        self.api.force_authenticate(self.accountant)
        tuition = self.api.post('/api/charge-types/', {
            'name': 'Tuition Fee', 'family': ChargeType.FEE, 'applies': ChargeType.AUTOMATIC,
            'invoice_group': 'Tuition Fee', 'bank_account': self.tuition_account.id,
            'blocks_registration': True, 'blocks_cat1': True, 'blocks_cat2': True,
            'blocks_final': True, 'blocks_results': True, 'is_active': True,
        }, format='json')
        self.assertEqual(tuition.status_code, 201, tuition.data)

        structure = self.api.post('/api/fee-structures/', {
            'charge_type': tuition.data['id'], 'class_level': self.level4.id,
            'academic_year': self.year.id, 'amount': '900000.00',
            'billing_period': FeeStructure.ACADEMIC_YEAR, 'installments': 5,
            'due_dates': ['2025-09-30', '2025-10-31', '2025-11-30', '2026-01-31', '2026-03-31'],
        }, format='json')
        self.assertEqual(structure.status_code, 201, structure.data)
        return tuition.data['id'], structure.data['id']

    # ── access ───────────────────────────────────────────────────────────────

    def test_a_tutor_cannot_reach_the_ledger(self):
        self.api.force_authenticate(self.tutor)
        for url in ('/api/charge-types/', '/api/fee-structures/', '/api/payments/',
                    '/api/finance/students/', '/api/student-charges/'):
            self.assertEqual(self.api.get(url).status_code, 403, url)

    def test_payments_cannot_be_edited_or_deleted_through_the_api(self):
        self._setup_fees()
        self.api.post('/api/finance/generate-charges/',
                      {'academic_year': self.year.id, 'class_level': self.level4.id}, format='json')
        charge = StudentCharge.objects.filter(profile=self.asha).order_by('due_date').first()
        created = self.api.post('/api/payments/record/', {
            'profile': self.asha.id, 'amount': str(charge.amount),
            'payment_date': '2025-09-28', 'efd_receipt_no': 'EFD-1',
        }, format='json')
        self.assertEqual(created.status_code, 201, created.data)
        pid = created.data['id']

        # The old model allowed both of these, with no trace.
        self.assertIn(self.api.patch(f'/api/payments/{pid}/', {'amount': '5.00'},
                                     format='json').status_code, (403, 404, 405))
        self.assertIn(self.api.delete(f'/api/payments/{pid}/').status_code, (403, 404, 405))
        self.assertEqual(Payment.objects.get(id=pid).amount, charge.amount)

    # ── validation at the counter ────────────────────────────────────────────

    def test_a_future_dated_payment_is_refused(self):
        self._setup_fees()
        response = self.api.post('/api/payments/record/', {
            'profile': self.asha.id, 'amount': '1000.00', 'payment_date': '2099-01-01',
        }, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('future', str(response.data).lower())

    def test_a_receipt_can_only_be_banked_once(self):
        self._setup_fees()
        self.api.post('/api/finance/generate-charges/',
                      {'academic_year': self.year.id, 'class_level': self.level4.id}, format='json')
        payload = {
            'profile': self.asha.id, 'amount': '180000.00', 'payment_date': '2025-09-28',
            'bank_reference': 'CRDB-88291', 'efd_receipt_no': 'EFD-0034127',
        }
        self.assertEqual(self.api.post('/api/payments/record/', payload, format='json').status_code, 201)
        duplicate = self.api.post('/api/payments/record/', payload, format='json')
        self.assertEqual(duplicate.status_code, 400)
        self.assertEqual(Payment.objects.count(), 1)

    def test_a_negative_payment_is_refused(self):
        self._setup_fees()
        response = self.api.post('/api/payments/record/', {
            'profile': self.asha.id, 'amount': '-500.00', 'payment_date': '2025-09-28',
        }, format='json')
        self.assertEqual(response.status_code, 400)

    # ── the whole counter flow ───────────────────────────────────────────────

    def test_the_full_scenario_from_invoice_to_clearance(self):
        self._setup_fees()

        # 1. The accountant bills the level.
        generated = self.api.post('/api/finance/generate-charges/', {
            'academic_year': self.year.id, 'class_level': self.level4.id,
        }, format='json')
        self.assertEqual(generated.status_code, 200, generated.data)
        self.assertEqual(generated.data['charges_raised'], 5)

        # 2. The student sees what she owes and is blocked from CAT 1.
        portal = Client()
        portal.post('/login/', {'identifier': 'REG/001', 'secret': 'Portal#2025'})
        fees = portal.get('/api/my-fees/').json()
        self.assertEqual(fees['totals']['billed'], '900000.00')
        self.assertEqual(fees['totals']['balance'], '900000.00')
        self.assertFalse(fees['clearance']['cat1']['cleared'])

        # 3. She generates the invoice for her school fees. One number covers
        #    all five instalments and stands until the year ends, so she quotes
        #    it again on every slip rather than coming back for a new one.
        issued = portal.post(
            '/api/my-fees/invoice/', {'family': ChargeType.FEE},
            content_type='application/json',
        )
        self.assertEqual(issued.status_code, 201, issued.content)
        self.assertEqual(len(issued.json()), 1)
        invoice = issued.json()[0]
        reference = invoice['reference']
        self.assertTrue(finance.reference_is_valid(reference))
        self.assertEqual(invoice['installment_count'], 5)
        self.assertEqual(invoice['total'], '900000.00')
        self.assertEqual(invoice['expires_on'], str(self.year.closes_on))

        # 4. Her father pays the first instalment at CRDB quoting the
        #    reference. The accountant looks it up on the slip and sees whose
        #    it is, and which bill the deposit is working off.
        found = self.api.get(f'/api/invoices/lookup/?reference={reference}')
        self.assertEqual(found.status_code, 200, found.data)
        self.assertEqual(found.data['student_reg_no'], 'REG/001')
        self.assertEqual(found.data['total'], '900000.00')

        # 5. Recorded once, with the payer and the EFD number.
        recorded = self.api.post('/api/payments/record/', {
            'profile': self.asha.id, 'invoice': found.data['id'], 'amount': '180000.00',
            'payment_date': '2025-09-29', 'channel': Payment.CRDB,
            'bank_reference': 'CRDB-88291', 'efd_receipt_no': 'EFD-0034127',
            'payer_name': 'Juma Hamisi', 'payer_relation': Payment.PARENT,
        }, format='json')
        self.assertEqual(recorded.status_code, 201, recorded.data)

        # 6. Nobody touched a clearance screen.
        after = portal.get('/api/my-fees/').json()
        self.assertEqual(after['totals']['paid'], '180000.00')
        self.assertEqual(after['totals']['balance'], '720000.00')
        self.assertTrue(after['clearance']['cat1']['cleared'], after['clearance']['cat1'])
        # One instalment down, four to go — under the same number.
        self.assertEqual(after['invoices'][0]['status'], 'partly_paid')
        self.assertEqual(after['invoices'][0]['paid'], '180000.00')
        self.assertEqual(after['invoices'][0]['outstanding'], '720000.00')
        self.assertEqual(after['invoices'][0]['reference'], reference)

        # 7. The accountant's debtors list agrees with her portal, exactly.
        debtors = self.api.get(f'/api/finance/students/?academic_year_id={self.year.id}').data
        row = debtors['rows'][0]
        self.assertEqual(row['balance'], after['totals']['balance'])
        self.assertTrue(row['clearance']['cat1'])
        self.assertEqual(debtors['totals']['outstanding'], '720000.00')

    def test_a_student_cannot_read_or_invoice_another_students_fees(self):
        self._setup_fees()
        self.api.force_authenticate(self.admin)
        self.api.post('/api/finance/generate-charges/',
                      {'academic_year': self.year.id, 'class_level': self.level4.id}, format='json')

        other_enrollment = Student.objects.create(
            nactvet_reg_no='REG/002', name='Baraka Simon', module=self.module)
        other_enrollment.set_portal_pin('Portal#2025', require_change=False)
        other_enrollment.save()
        other = finance.profile_for_student(other_enrollment)
        finance.generate_charges(other, self.year)

        portal = Client()
        portal.post('/login/', {'identifier': 'REG/001', 'secret': 'Portal#2025'})

        mine = portal.get('/api/my-fees/').json()
        self.assertEqual(mine['profile']['nactvet_reg_no'], 'REG/001')

        their_charge = StudentCharge.objects.filter(profile=other).first()
        attempt = portal.post('/api/my-fees/invoice/', {'charges': [their_charge.id]},
                              content_type='application/json')
        self.assertEqual(attempt.status_code, 400)

    def test_a_student_cannot_print_someone_elses_invoice(self):
        self._setup_fees()
        finance.generate_charges(self.asha, self.year)
        other_enrollment = Student.objects.create(
            nactvet_reg_no='REG/002', name='Baraka Simon', module=self.module)
        other_enrollment.set_portal_pin('Portal#2025', require_change=False)
        other_enrollment.save()
        other = finance.profile_for_student(other_enrollment)
        finance.generate_charges(other, self.year)
        theirs = finance.issue_invoice(other, finance.outstanding_charges(other, self.year), self.year)

        portal = Client()
        portal.post('/login/', {'identifier': 'REG/001', 'secret': 'Portal#2025'})
        self.assertEqual(portal.get(f'/invoice/{theirs.reference}/').status_code, 404)

    def test_the_invoice_prints_with_its_reference_and_bank_details(self):
        self._setup_fees()
        finance.generate_charges(self.asha, self.year)
        invoice = finance.issue_invoice(
            self.asha, finance.outstanding_charges(self.asha, self.year)[:1], self.year)

        portal = Client()
        portal.post('/login/', {'identifier': 'REG/001', 'secret': 'Portal#2025'})
        page = portal.get(f'/invoice/{invoice.reference}/')
        self.assertEqual(page.status_code, 200)
        body = page.content.decode()
        self.assertIn(invoice.reference, body)
        self.assertIn('Asha Juma', body)
        self.assertIn('CRDB', body)
        self.assertIn('0150417961300', body)          # the tuition account, not the other one
        self.assertNotIn('0150417961301', body)
        # School fees is one item paid in instalments: the bill states the
        # amount and the instalment count, and leaves the dates to the portal's
        # reminders rather than printing five rows of the same thing.
        self.assertIn('Amount required', body)
        self.assertIn('payable in 5 instalments', body)
        self.assertNotIn('Item(s) details', body)

    # ── reversal and reporting ───────────────────────────────────────────────

    def test_reversing_a_payment_restores_the_balance_and_keeps_the_record(self):
        self._setup_fees()
        self.api.post('/api/finance/generate-charges/',
                      {'academic_year': self.year.id, 'class_level': self.level4.id}, format='json')

        recorded = self.api.post('/api/payments/record/', {
            'profile': self.asha.id, 'amount': '180000.00', 'payment_date': '2025-09-29',
            'efd_receipt_no': 'EFD-1',
        }, format='json')
        reversal = self.api.post(f'/api/payments/{recorded.data["id"]}/reverse/',
                                 {'reason': 'Slip belonged to another student'}, format='json')
        self.assertEqual(reversal.status_code, 201, reversal.data)

        self.assertEqual(finance.balance_for(self.asha, self.year)['balance'], Decimal('900000.00'))
        self.assertEqual(Payment.objects.count(), 2)

        trail = self.api.get('/api/finance-audit/')
        actions = [row['action'] for row in trail.data]
        self.assertIn('payment.record', actions)
        self.assertIn('payment.reverse', actions)

    def test_collections_report_flags_payments_missing_an_efd_number(self):
        self._setup_fees()
        self.api.post('/api/finance/generate-charges/',
                      {'academic_year': self.year.id, 'class_level': self.level4.id}, format='json')
        self.api.post('/api/payments/record/', {
            'profile': self.asha.id, 'amount': '100000.00', 'payment_date': '2025-09-29',
            'efd_receipt_no': 'EFD-1',
        }, format='json')
        self.api.post('/api/payments/record/', {
            'profile': self.asha.id, 'amount': '80000.00', 'payment_date': '2025-09-30',
        }, format='json')

        report = self.api.get('/api/finance/collections/?from=2025-09-01&to=2025-09-30').data
        self.assertEqual(report['total'], '180000.00')
        self.assertEqual(report['count'], 2)
        self.assertEqual(report['missing_efd'], 1)

    def test_raising_a_charge_returns_a_usable_date(self):
        """The view must parse due_date rather than pass the raw string on.

        Django coerces it on save, but the in-memory instance kept the string
        and every date comparison against it — is_overdue, clearance — raised
        TypeError comparing str to date.
        """
        self._setup_fees()
        supplementary = ChargeType.objects.create(
            name='Supplementary Exam', family=ChargeType.OTHER,
            applies=ChargeType.ON_REQUEST, blocks_final=True,
        )
        response = self.api.post('/api/finance/raise-charge/', {
            'profile': self.asha.id, 'charge_type': supplementary.id,
            'academic_year': self.year.id, 'amount': '30000.00',
            'due_date': '2025-11-15', 'note': 'Supplementary — ANA101',
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['due_date'], '2025-11-15')
        self.assertIs(response.data['is_overdue'], True)

        blocked = finance.exam_clearance(
            self.asha, self.year, ChargeType.FINAL, semester=self.sem1)
        self.assertFalse(blocked['cleared'])
        self.assertIn('Supplementary Exam', blocked['reason'])

    def test_a_malformed_due_date_is_refused(self):
        self._setup_fees()
        supplementary = ChargeType.objects.create(
            name='Repeat Module', family=ChargeType.OTHER, applies=ChargeType.ON_REQUEST)
        response = self.api.post('/api/finance/raise-charge/', {
            'profile': self.asha.id, 'charge_type': supplementary.id,
            'academic_year': self.year.id, 'amount': '30000.00',
            'due_date': 'next Tuesday',
        }, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('YYYY-MM-DD', response.data['detail'])

    def test_the_fee_grid_returns_one_cell_per_level(self):
        self._setup_fees()
        ClassLevel.objects.create(name='NTA Level 5', order=5)
        grid = self.api.get(f'/api/fee-structures/grid/?academic_year_id={self.year.id}').data
        self.assertEqual(len(grid['class_levels']), 2)
        row = grid['rows'][0]
        self.assertEqual(row['charge_type_name'], 'Tuition Fee')
        self.assertEqual(len(row['cells']), 2)
        filled = [c for c in row['cells'] if c['amount']]
        self.assertEqual(len(filled), 1)
        self.assertEqual(filled[0]['installments'], 5)
        self.assertEqual(len(filled[0]['due_dates']), 5)

    def test_a_charge_type_in_use_cannot_be_deleted(self):
        type_id, _ = self._setup_fees()
        self.api.post('/api/finance/generate-charges/',
                      {'academic_year': self.year.id, 'class_level': self.level4.id}, format='json')
        response = self.api.delete(f'/api/charge-types/{type_id}/')
        self.assertEqual(response.status_code, 403)
        self.assertTrue(ChargeType.objects.filter(id=type_id).exists())


class StudentDashboardFeesTests(TestCase):
    """The dashboard totals that used to be wrong in both directions."""

    def setUp(self):
        self.year = AcademicYear.objects.create(name='2025/2026', is_active=True)
        self.sem = Semester.objects.create(academic_year=self.year, number=1, is_active=True)
        self.level = ClassLevel.objects.create(name='NTA Level 4', order=4)
        # Four modules: the multi-enrollment case the old dashboard multiplied.
        for i in range(1, 5):
            module = Module.objects.create(
                name=f'M{i}', code=f'MOD{i}', teacher='T',
                class_level=self.level, semester=self.sem,
            )
            enrollment = Student.objects.create(
                nactvet_reg_no='REG/001', name='Asha Juma', module=module)
            enrollment.set_portal_pin('Portal#2025', require_change=False)
            enrollment.save()
        self.asha = finance.profile_for_student(Student.objects.first())
        self.staff = User.objects.create_superuser('admin2', 'a@b.c', 'pw')

        tuition = ChargeType.objects.create(
            name='Tuition Fee', family=ChargeType.FEE,
            blocks_cat1=True, blocks_cat2=True, blocks_final=True,
        )
        structure = FeeStructure.objects.create(
            charge_type=tuition, class_level=self.level, academic_year=self.year,
            amount=Decimal('900000.00'), installments=3,
        )
        finance.set_installment_schedule(
            structure, [date(2025, 9, 30), date(2025, 11, 30), date(2026, 1, 31)])
        finance.generate_charges(self.asha, self.year, actor=self.staff)

        self.portal = Client()
        self.portal.post('/login/', {'identifier': 'REG/001', 'secret': 'Portal#2025'})

    def test_unpaid_student_sees_the_full_fee_not_zero(self):
        context = self.portal.get('/student-dashboard/').context
        self.assertEqual(context['finance_required'], Decimal('900000.00'))
        self.assertEqual(context['finance_paid'], Decimal('0.00'))
        self.assertEqual(context['finance_balance'], Decimal('900000.00'))

    def test_fully_paid_student_owes_nothing_despite_four_enrollments(self):
        for charge in StudentCharge.objects.filter(profile=self.asha):
            finance.record_payment(
                self.asha, charge.amount, charge.due_date, recorded_by=self.staff,
                allocations=[(charge, charge.amount)],
            )
        context = self.portal.get('/student-dashboard/').context
        self.assertEqual(context['finance_required'], Decimal('900000.00'))
        self.assertEqual(context['finance_paid'], Decimal('900000.00'))
        self.assertEqual(context['finance_balance'], Decimal('0.00'))

    def test_the_dashboard_can_generate_an_invoice(self):
        response = self.portal.post('/api/my-fees/invoice/', {},
                                    content_type='application/json')
        self.assertEqual(response.status_code, 201, response.content)
        self.assertTrue(finance.reference_is_valid(response.json()[0]['reference']))


class FinanceQueryBudgetTests(TestCase):
    """Clearance is per person, per period — never per enrollment.

    Computing it per enrollment cost ~1,200 queries on the eligibility screen
    and ~2,800 on the debtors list at 200 students. Both are batched now, and
    the query count must stay flat as the college grows rather than scaling
    with the roll.
    """

    def setUp(self):
        self.year = AcademicYear.objects.create(name='2025/2026', is_active=True)
        self.sem = Semester.objects.create(
            academic_year=self.year, number=1, is_active=True,
            cat1_cutoff=date(2025, 10, 15), cat2_cutoff=date(2025, 11, 20),
            end_cutoff=date(2025, 12, 10),
        )
        self.level = ClassLevel.objects.create(name='NTA Level 4', order=4)
        self.admin = User.objects.create_superuser('budget-admin', 'a@b.c', 'pw')
        self.accountant = User.objects.create_user('budget-accountant', password='pw')
        AccountantProfile.objects.create(user=self.accountant, full_name='Finance Officer')

        charge_type = ChargeType.objects.create(
            name='Tuition Fee', family=ChargeType.FEE,
            blocks_cat1=True, blocks_cat2=True, blocks_final=True,
        )
        structure = FeeStructure.objects.create(
            charge_type=charge_type, class_level=self.level, academic_year=self.year,
            amount=Decimal('900000.00'), installments=3,
        )
        finance.set_installment_schedule(
            structure, [date(2025, 9, 30), date(2025, 11, 30), date(2026, 1, 31)])

        modules = [
            Module.objects.create(name=f'M{i}', code=f'MOD{i}', teacher='T',
                                  class_level=self.level, semester=self.sem)
            for i in range(6)
        ]
        for i in range(30):
            for module in modules:
                Student.objects.create(
                    nactvet_reg_no=f'REG/{i:03d}', name=f'Student {i}', module=module)
            profile = finance.profile_for_student(
                Student.objects.filter(nactvet_reg_no=f'REG/{i:03d}').first())
            finance.generate_charges(profile, self.year, actor=self.admin)

        self.api = APIClient()
        self.api.force_authenticate(self.accountant)

    def _enrol_more(self, count, start):
        modules = list(Module.objects.filter(semester=self.sem))
        for i in range(start, start + count):
            for module in modules:
                Student.objects.create(
                    nactvet_reg_no=f'REG/{i:03d}', name=f'Student {i}', module=module)
            profile = finance.profile_for_student(
                Student.objects.filter(nactvet_reg_no=f'REG/{i:03d}').first())
            finance.generate_charges(profile, self.year, actor=self.admin)

    def _queries_for(self, url, as_user=None):
        if as_user is not None:
            self.api.force_authenticate(as_user)
        with CaptureQueriesContext(connection) as captured:
            response = self.api.get(url)
        self.assertEqual(response.status_code, 200)
        return len(captured), response

    def test_eligibility_query_count_is_flat_as_the_roll_doubles(self):
        # Eligibility belongs to the examination officer, not the accountant —
        # they are the one with modules to look at.
        before, response = self._queries_for('/api/eligibility/', self.admin)
        self.assertEqual(len(response.data['rows']), 180)    # 30 students x 6 modules

        self._enrol_more(30, start=100)
        after, response = self._queries_for('/api/eligibility/', self.admin)
        self.assertEqual(len(response.data['rows']), 360)    # 60 students x 6 modules

        self.assertEqual(after, before,
                         f'Eligibility went from {before} to {after} queries when the roll '
                         f'doubled — clearance is being computed per student again.')

    def test_debtors_list_query_count_is_flat_as_the_roll_doubles(self):
        before, response = self._queries_for(
            f'/api/finance/students/?academic_year_id={self.year.id}')
        self.assertEqual(len(response.data['rows']), 30)
        self.assertEqual(response.data['totals']['outstanding'], '27000000.00')

        self._enrol_more(30, start=100)
        after, response = self._queries_for(
            f'/api/finance/students/?academic_year_id={self.year.id}')
        self.assertEqual(len(response.data['rows']), 60)

        self.assertEqual(after, before,
                         f'Debtors list went from {before} to {after} queries when the roll '
                         f'doubled — balances are being fetched per student again.')

    def test_batch_and_single_clearance_agree(self):
        """The batch path must never drift from the single-student path."""
        profiles = list(StudentProfile.objects.all()[:5])
        batch = finance.clearance_map(
            profiles, self.year, [ChargeType.CAT1, ChargeType.CAT2, ChargeType.FINAL],
            semester=self.sem, today=date(2025, 12, 1),
        )
        for profile in profiles:
            for period in (ChargeType.CAT1, ChargeType.CAT2, ChargeType.FINAL):
                single = finance.exam_clearance(
                    profile, self.year, period, semester=self.sem, today=date(2025, 12, 1))
                self.assertEqual(batch[profile.id][period]['cleared'], single['cleared'])
                self.assertEqual(batch[profile.id][period]['balance'], single['balance'])
                self.assertEqual(batch[profile.id][period]['reason'], single['reason'])

    def test_batch_and_single_balances_agree(self):
        profiles = list(StudentProfile.objects.all()[:5])
        batch = finance.balance_map(profiles, self.year)
        for profile in profiles:
            self.assertEqual(batch[profile.id], finance.balance_for(profile, self.year))


class FinanceRoleSeparationTests(TestCase):
    """Finance belongs to the accountant alone.

    `is_staff` here is the examination officer / HOD: they own results,
    eligibility and the academic register, and must not see fees, payments or
    balances. The person who decides who sits an exam is not the person who
    decides whether the money arrived.
    """

    def setUp(self):
        self.year = AcademicYear.objects.create(name='2025/2026', is_active=True)
        self.sem = Semester.objects.create(academic_year=self.year, number=1, is_active=True)
        self.level = ClassLevel.objects.create(name='NTA Level 4', order=4)
        self.module = Module.objects.create(
            name='Anatomy', code='ANA101', teacher='T',
            class_level=self.level, semester=self.sem)
        self.enrollment = Student.objects.create(
            nactvet_reg_no='REG/001', name='Asha Juma', module=self.module)
        self.asha = finance.profile_for_student(self.enrollment)

        self.exam_officer = User.objects.create_superuser('hod', 'h@b.c', 'pw')
        self.accountant = User.objects.create_user('acct', password='pw')
        AccountantProfile.objects.create(user=self.accountant, full_name='Finance Officer')
        self.tutor = User.objects.create_user('tutor2', password='pw')
        TeacherProfile.objects.create(user=self.tutor, full_name='Tutor')

        charge_type = ChargeType.objects.create(
            name='Tuition Fee', family=ChargeType.FEE,
            blocks_cat1=True, blocks_cat2=True, blocks_final=True)
        structure = FeeStructure.objects.create(
            charge_type=charge_type, class_level=self.level, academic_year=self.year,
            amount=Decimal('900000.00'), installments=1)
        finance.set_installment_schedule(structure, [date(2025, 9, 30)])
        finance.generate_charges(self.asha, self.year)

        self.api = APIClient()

    FINANCE_URLS = [
        '/api/charge-types/', '/api/fee-structures/', '/api/student-charges/',
        '/api/invoices/', '/api/payments/', '/api/finance-overrides/',
        '/api/finance-audit/', '/api/finance/students/',
        '/api/finance/collections/',
    ]

    def test_the_exam_officer_cannot_see_any_finance_endpoint(self):
        self.api.force_authenticate(self.exam_officer)
        for url in self.FINANCE_URLS:
            self.assertEqual(self.api.get(url).status_code, 403, url)
        self.assertEqual(
            self.api.get(f'/api/finance/statement/{self.asha.id}/').status_code, 403)

    def test_the_exam_officer_cannot_move_money(self):
        self.api.force_authenticate(self.exam_officer)
        self.assertEqual(self.api.post('/api/payments/record/', {
            'profile': self.asha.id, 'amount': '1000.00', 'payment_date': '2025-09-29',
        }, format='json').status_code, 403)
        self.assertEqual(self.api.post('/api/finance/generate-charges/', {
            'academic_year': self.year.id,
        }, format='json').status_code, 403)
        self.assertEqual(self.api.post('/api/finance/raise-charge/', {
            'profile': self.asha.id, 'academic_year': self.year.id,
        }, format='json').status_code, 403)

    def test_the_accountant_can_reach_every_finance_endpoint(self):
        self.api.force_authenticate(self.accountant)
        for url in self.FINANCE_URLS:
            self.assertEqual(self.api.get(url).status_code, 200, url)

    def test_the_accountant_bills_the_level_not_the_exam_officer(self):
        self.api.force_authenticate(self.accountant)
        response = self.api.post('/api/finance/generate-charges/', {
            'academic_year': self.year.id, 'class_level': self.level.id,
        }, format='json')
        self.assertEqual(response.status_code, 200, response.data)

    def test_the_exam_officer_still_owns_results_and_eligibility(self):
        self.api.force_authenticate(self.exam_officer)
        self.assertEqual(self.api.get('/api/eligibility/').status_code, 200)
        self.assertEqual(self.api.get('/api/results/').status_code, 200)
        self.assertEqual(self.api.get('/api/students/').status_code, 200)

    def test_the_exam_officer_sees_clearance_but_never_the_amount(self):
        """They need to know a student is blocked to run the exam. The balance
        itself is not theirs to read."""
        self.api.force_authenticate(self.exam_officer)
        row = self.api.get('/api/eligibility/').data['rows'][0]
        self.assertFalse(row['cat1_finance_eligible'])
        self.assertEqual(row['cat1_finance_note'], 'Pending finance clearance')
        for field in ('cat1_finance_note', 'cat1_exam_note', 'end_exam_note'):
            self.assertNotIn('900000', row[field])

    def test_the_accountant_declares_nothing_academic(self):
        self.api.force_authenticate(self.accountant)
        response = self.api.post('/api/finance-obligations/', {
            'student': self.enrollment.id, 'semester': self.sem.id,
            'obligation_type': 'supp_exam', 'amount_required': '30000.00',
        }, format='json')
        self.assertEqual(response.status_code, 403)

    def test_a_tutor_sees_neither_finance_nor_amounts(self):
        self.module.teachers.add(self.tutor)
        self.api.force_authenticate(self.tutor)
        for url in self.FINANCE_URLS:
            self.assertEqual(self.api.get(url).status_code, 403, url)
        row = self.api.get('/api/eligibility/').data['rows'][0]
        self.assertEqual(row['cat1_finance_note'], 'Pending finance clearance')

    def test_the_student_still_sees_their_own_amount(self):
        """Redaction protects the student from staff, not from themselves."""
        self.enrollment.set_portal_pin('Portal#2025', require_change=False)
        self.enrollment.save()
        portal = Client()
        portal.post('/login/', {'identifier': 'REG/001', 'secret': 'Portal#2025'})
        fees = portal.get('/api/my-fees/').json()
        self.assertEqual(fees['totals']['balance'], '900000.00')
        self.assertIn('900000.00', fees['clearance']['cat1']['reason'])


class StudentInvoiceMessageTests(TestCase):
    """A student clicking "Generate invoice" before the college has set up fees
    was told "Nothing outstanding to invoice." — which reads as "I am paid up"
    and stops them asking."""

    def setUp(self):
        self.year = AcademicYear.objects.create(name='2025/2026', is_active=True)
        self.sem = Semester.objects.create(academic_year=self.year, number=1, is_active=True)
        self.level = ClassLevel.objects.create(name='NTA Level 4', order=4)
        module = Module.objects.create(name='Anatomy', code='ANA101', teacher='T',
                                       class_level=self.level, semester=self.sem)
        self.enrollment = Student.objects.create(
            nactvet_reg_no='REG/001', name='Asha Juma', module=module)
        self.enrollment.set_portal_pin('Portal#2025', require_change=False)
        self.enrollment.save()
        self.asha = finance.profile_for_student(self.enrollment)
        self.staff = User.objects.create_superuser('hod2', 'h@b.c', 'pw')
        self.portal = Client()
        self.portal.post('/login/', {'identifier': 'REG/001', 'secret': 'Portal#2025'})

    def _invoice(self):
        return self.portal.post('/api/my-fees/invoice/', {}, content_type='application/json')

    def test_a_student_never_billed_is_told_so(self):
        response = self._invoice()
        self.assertEqual(response.status_code, 400)
        self.assertIn('not been set up yet', response.json()['detail'])

    def test_a_paid_up_student_is_told_something_different(self):
        charge_type = ChargeType.objects.create(name='Tuition Fee', family=ChargeType.FEE)
        structure = FeeStructure.objects.create(
            charge_type=charge_type, class_level=self.level, academic_year=self.year,
            amount=Decimal('900000.00'), installments=1)
        finance.set_installment_schedule(structure, [date(2025, 9, 30)])
        finance.generate_charges(self.asha, self.year)
        charge = StudentCharge.objects.get(profile=self.asha)
        finance.record_payment(self.asha, charge.amount, date(2025, 9, 29),
                               recorded_by=self.staff, allocations=[(charge, charge.amount)])

        response = self._invoice()
        self.assertEqual(response.status_code, 400)
        self.assertIn('fully paid up', response.json()['detail'])

    def test_the_dashboard_hides_the_button_until_there_is_something_to_pay(self):
        body = self.portal.get('/student-dashboard/').content.decode()
        self.assertIn('have not been set up yet', body)
        self.assertNotIn('Generate invoices for everything outstanding', body)

        charge_type = ChargeType.objects.create(name='Tuition Fee', family=ChargeType.FEE)
        structure = FeeStructure.objects.create(
            charge_type=charge_type, class_level=self.level, academic_year=self.year,
            amount=Decimal('900000.00'), installments=1)
        finance.set_installment_schedule(structure, [date(2025, 9, 30)])
        finance.generate_charges(self.asha, self.year)

        body = self.portal.get('/student-dashboard/').content.decode()
        self.assertIn('Generate invoices for everything outstanding', body)


class InvoiceSplittingTests(TestCase):
    """The college banks tuition and other charges in two different CRDB
    accounts, so one invoice covering both would be unpayable — the student
    would have to split a single deposit across two accounts. Charges are
    invoiced per group, and an invoice never spans two accounts."""

    def setUp(self):
        self.year = AcademicYear.objects.create(name='2025/2026', is_active=True)
        self.sem = Semester.objects.create(academic_year=self.year, number=1, is_active=True)
        self.level = ClassLevel.objects.create(name='NTA Level 4', order=4)
        module = Module.objects.create(name='Anatomy', code='ANA101', teacher='T',
                                       class_level=self.level, semester=self.sem)
        self.enrollment = Student.objects.create(
            nactvet_reg_no='REG/001', name='Asha Juma', module=module)
        self.enrollment.set_portal_pin('Portal#2025', require_change=False)
        self.enrollment.save()
        self.asha = finance.profile_for_student(self.enrollment)
        self.staff = User.objects.create_superuser('hod3', 'h@b.c', 'pw')

        self.tuition_acct = BankAccount.objects.create(
            bank_name='CRDB', account_name='BLUE PHARMA COLLEGE OF HEALTH',
            account_number='0150417961300', purpose='Tuition fee')
        self.other_acct = BankAccount.objects.create(
            bank_name='CRDB', account_name='BLUE PHARMA COLLEGE OF HEALTH',
            account_number='0150417961301', purpose='Other charges & accommodation')

        def bill(name, family, group, account, amount, installments=1, applies=ChargeType.AUTOMATIC,
                 frequency=ChargeType.EACH_YEAR):
            charge_type = ChargeType.objects.create(
                name=name, family=family, invoice_group=group, bank_account=account,
                applies=applies, frequency=frequency, blocks_final=True)
            period = (FeeStructure.ONCE if frequency == ChargeType.ONCE
                      else FeeStructure.ACADEMIC_YEAR)
            structure = FeeStructure.objects.create(
                charge_type=charge_type, class_level=self.level, academic_year=self.year,
                amount=Decimal(amount), installments=installments, billing_period=period)
            finance.set_installment_schedule(
                structure, [date(2025, 9, 30), date(2025, 11, 30), date(2026, 1, 31),
                            date(2026, 3, 31), date(2026, 5, 31)][:installments])
            return charge_type

        self.tuition = bill('Tuition Fee', ChargeType.FEE, 'Tuition Fee',
                            self.tuition_acct, 1_600_000, installments=5)
        bill('Registration fees', ChargeType.DIRECT_COST, 'Direct Costs', self.other_acct, 10_000)
        bill('Examination fees (Semester 1)', ChargeType.DIRECT_COST, 'Direct Costs',
             self.other_acct, 150_000)
        bill('Medical fees (Health Insurance)', ChargeType.DIRECT_COST, 'Direct Costs',
             self.other_acct, 60_000)
        self.caution = bill('Caution money', ChargeType.DIRECT_COST, 'Direct Costs',
                            self.other_acct, 50_000, frequency=ChargeType.ONCE)
        self.hostel = bill('Accommodation', ChargeType.DIRECT_COST, 'Accommodation',
                           self.other_acct, 400_000, installments=2,
                           applies=ChargeType.OPTIONAL)
        finance.generate_charges(self.asha, self.year, actor=self.staff)

    def test_paying_everything_produces_one_invoice_per_group(self):
        invoices = finance.issue_invoices(
            self.asha, finance.outstanding_charges(self.asha, self.year), self.year)
        groups = sorted(i.invoice_group for i in invoices)
        self.assertEqual(groups, ['Direct Costs', 'Tuition Fee'])

        tuition = next(i for i in invoices if i.invoice_group == 'Tuition Fee')
        direct = next(i for i in invoices if i.invoice_group == 'Direct Costs')
        self.assertEqual(tuition.bank_account, self.tuition_acct)
        self.assertEqual(direct.bank_account, self.other_acct)
        self.assertNotEqual(tuition.reference, direct.reference)

    def test_the_direct_costs_invoice_itemises_what_it_covers(self):
        invoices = finance.issue_invoices(
            self.asha, finance.outstanding_charges(self.asha, self.year), self.year)
        direct = next(i for i in invoices if i.invoice_group == 'Direct Costs')
        names = sorted(line.charge.charge_type.name for line in direct.lines.all())
        self.assertEqual(names, ['Caution money', 'Examination fees (Semester 1)',
                                 'Medical fees (Health Insurance)', 'Registration fees'])
        self.assertEqual(direct.total, Decimal('270000.00'))

    def test_one_invoice_may_never_span_two_accounts(self):
        mixed = finance.outstanding_charges(self.asha, self.year)
        with self.assertRaises(ValueError) as caught:
            finance.issue_invoice(self.asha, mixed, self.year)
        self.assertIn('different accounts', str(caught.exception))

    def test_tuition_instalments_share_one_invoice(self):
        tuition_charges = [c for c in finance.outstanding_charges(self.asha, self.year)
                           if c.charge_type == self.tuition]
        self.assertEqual(len(tuition_charges), 5)
        invoice = finance.issue_invoice(self.asha, tuition_charges, self.year)
        self.assertEqual(invoice.lines.count(), 5)
        self.assertEqual(invoice.total, Decimal('1600000.00'))
        self.assertEqual(invoice.bank_account, self.tuition_acct)

    def test_an_optional_charge_is_only_invoiced_once_assigned(self):
        self.assertFalse(
            StudentCharge.objects.filter(profile=self.asha, charge_type=self.hostel).exists())
        finance.raise_charge(self.asha, self.hostel, self.year, Decimal('400000.00'),
                             date(2025, 9, 30), actor=self.staff)
        invoices = finance.issue_invoices(
            self.asha, finance.outstanding_charges(self.asha, self.year), self.year)
        self.assertIn('Accommodation', [i.invoice_group for i in invoices])
        hostel_invoice = next(i for i in invoices if i.invoice_group == 'Accommodation')
        self.assertEqual(hostel_invoice.total, Decimal('400000.00'))

    def test_a_once_charge_is_not_billed_again_the_following_year(self):
        """This is the whole difference between the first-year and continuing
        tables in the prospectus."""
        self.assertTrue(
            StudentCharge.objects.filter(profile=self.asha, charge_type=self.caution).exists())

        next_year = AcademicYear.objects.create(name='2026/2027')
        for charge_type in ChargeType.objects.all():
            structure = FeeStructure.objects.filter(charge_type=charge_type,
                                                    academic_year=self.year).first()
            if not structure:
                continue
            clone = FeeStructure.objects.create(
                charge_type=charge_type, class_level=self.level, academic_year=next_year,
                amount=structure.amount, installments=structure.installments,
                billing_period=structure.billing_period)
            finance.set_installment_schedule(
                clone, [date(2026, 9, 30), date(2026, 11, 30), date(2027, 1, 31),
                        date(2027, 3, 31), date(2027, 5, 31)][:clone.installments])
        finance.generate_charges(self.asha, next_year, actor=self.staff,
                                 class_level=self.level)

        billed_again = StudentCharge.objects.filter(
            profile=self.asha, charge_type=self.caution, academic_year=next_year)
        self.assertFalse(billed_again.exists(),
                         'Caution money is a once-only charge and must not repeat.')
        self.assertTrue(StudentCharge.objects.filter(
            profile=self.asha, charge_type=self.tuition, academic_year=next_year).exists())

    def test_each_invoice_prints_only_its_own_account(self):
        finance.issue_invoices(
            self.asha, finance.outstanding_charges(self.asha, self.year), self.year)
        portal = Client()
        portal.post('/login/', {'identifier': 'REG/001', 'secret': 'Portal#2025'})

        for invoice in Invoice.objects.filter(profile=self.asha):
            body = portal.get(f'/invoice/{invoice.reference}/').content.decode()
            self.assertIn(invoice.bank_account.account_number, body)
            other = (self.other_acct if invoice.bank_account == self.tuition_acct
                     else self.tuition_acct)
            self.assertNotIn(other.account_number, body)
            self.assertIn(invoice.invoice_group, body)


class PublishedFeeStructureTests(TestCase):
    """The seeded catalogue must reproduce the published prospectus exactly.

    The admission form prints four totals. If the catalogue drifts from them,
    students get billed the wrong amount, so those four numbers are the test.
    """

    def setUp(self):
        self.year = AcademicYear.objects.create(name='2026/2027', is_active=True)
        for name, order in [('NTA Level 4', 4), ('NTA Level 5', 5), ('NTA Level 6', 6)]:
            ClassLevel.objects.create(name=name, order=order)
        call_command('seed_fee_structure', year='2026/2027',
                     first_due='2026-09-30', verbosity=0)
        self.level4 = ClassLevel.objects.get(order=4)

    def _other_charges(self, continuing):
        total = Decimal('0.00')
        for structure in FeeStructure.objects.filter(
            class_level=self.level4, charge_type__family=ChargeType.DIRECT_COST,
            charge_type__applies=ChargeType.AUTOMATIC,
        ).select_related('charge_type'):
            if continuing and structure.billing_period == FeeStructure.ONCE:
                continue
            total += structure.amount
        return total

    def test_tuition_matches_the_prospectus(self):
        for level in ClassLevel.objects.all():
            structure = FeeStructure.objects.get(
                charge_type__name='Tuition Fee', class_level=level)
            self.assertEqual(structure.amount, Decimal('1600000.00'))
            self.assertEqual(structure.installments, 5)
            self.assertEqual(structure.installment_schedule.count(), 5)
            self.assertEqual(
                sum(i.amount for i in structure.installment_schedule.all()),
                Decimal('1600000.00'))

    def test_the_four_published_totals(self):
        accommodation = FeeStructure.objects.get(
            charge_type__name='Accommodation', class_level=self.level4).amount

        first_year_day = self._other_charges(continuing=False)
        continuing_day = self._other_charges(continuing=True)

        self.assertEqual(first_year_day, Decimal('895000.00'))
        self.assertEqual(continuing_day, Decimal('605000.00'))
        self.assertEqual(first_year_day + accommodation, Decimal('1295000.00'))
        self.assertEqual(continuing_day + accommodation, Decimal('1005000.00'))

    def test_tuition_and_other_charges_bank_separately(self):
        tuition = ChargeType.objects.get(name='Tuition Fee')
        registration = ChargeType.objects.get(name='Registration fees')
        self.assertEqual(tuition.bank_account.account_number, '0150417961300')
        self.assertEqual(registration.bank_account.account_number, '0150417961301')
        self.assertNotEqual(tuition.bank_account, registration.bank_account)

    def test_the_once_only_charges_are_the_ones_the_prospectus_drops(self):
        once = set(ChargeType.objects.filter(
            frequency=ChargeType.ONCE).values_list('name', flat=True))
        self.assertEqual(once, {
            'Caution money', 'Admission fee', 'Identity card',
            'Clinical coat', 'Graduation fees', 'Uniforms',
        })

    def test_accommodation_and_meals_are_not_billed_to_everyone(self):
        for name in ('Accommodation', 'Meals'):
            self.assertEqual(ChargeType.objects.get(name=name).applies, ChargeType.OPTIONAL)

    def test_the_college_details_are_loaded_for_invoices(self):
        college = CollegeProfile.get()
        self.assertEqual(college.name, 'Blue Pharma College of Health')
        self.assertIn('1570', college.po_box)
        self.assertEqual(college.town, 'Singida')
        self.assertIn('mobile application', college.invoice_terms.lower())

    def test_a_first_year_is_billed_more_than_a_continuing_student(self):
        module = Module.objects.create(
            name='Anatomy', code='ANA101', teacher='T', class_level=self.level4,
            semester=Semester.objects.create(academic_year=self.year, number=1, is_active=True))
        fresher = finance.profile_for_student(Student.objects.create(
            nactvet_reg_no='REG/001', name='Asha', module=module))
        finance.generate_charges(fresher, self.year, class_level=self.level4)

        billed = finance.balance_for(fresher, self.year)['billed']
        # tuition 1,600,000 + first-year other charges 895,000, no hostel
        self.assertEqual(billed, Decimal('2495000.00'))


class OneInvoicePerPaymentTests(TestCase):
    """A payment carries one invoice number for all of its instalments.

    Invoicing whichever instalments the student ticked produced a new reference
    every time, so five tuition deposits arrived under five numbers and nobody
    could see them as one bill being worked off. The student now picks the
    payment — school fees, direct costs, or one of the other payments — and
    gets one number, good until the academic year ends.
    """

    def setUp(self):
        self.year = AcademicYear.objects.create(name='2026/2027', is_active=True)
        self.sem = Semester.objects.create(academic_year=self.year, number=1, is_active=True)
        for name, order in [('NTA Level 4', 4)]:
            ClassLevel.objects.create(name=name, order=order)
        call_command('seed_fee_structure', year='2026/2027',
                     first_due='2026-09-30', verbosity=0)
        self.level = ClassLevel.objects.get(order=4)
        module = Module.objects.create(name='Pharmaceutics', code='PHM101', teacher='T',
                                       class_level=self.level, semester=self.sem)
        self.enrollment = Student.objects.create(
            nactvet_reg_no='BPH/2026/001', name='Asha Juma', module=module)
        self.enrollment.set_portal_pin('Portal#2026', require_change=False)
        self.enrollment.save()
        self.asha = finance.profile_for_student(self.enrollment)
        finance.generate_charges(self.asha, self.year, class_level=self.level)
        self.portal = Client()
        self.portal.post('/login/', {'identifier': 'BPH/2026/001', 'secret': 'Portal#2026'})

    def _generate(self, **payload):
        return self.portal.post('/api/my-fees/invoice/', payload,
                                content_type='application/json')

    # ── the rule ─────────────────────────────────────────────────────────────

    def test_one_number_covers_every_instalment_of_school_fees(self):
        tuition = StudentCharge.objects.filter(
            profile=self.asha, charge_type__name='Tuition Fee')
        self.assertEqual(tuition.count(), 5)

        response = self._generate(family=ChargeType.FEE)
        self.assertEqual(response.status_code, 201, response.content)
        invoices = response.json()
        self.assertEqual(len(invoices), 1)
        self.assertEqual(invoices[0]['invoice_group'], 'Tuition Fee')
        self.assertEqual(invoices[0]['installment_count'], 5)
        self.assertEqual(Decimal(invoices[0]['total']), Decimal('1600000.00'))
        self.assertEqual(invoices[0]['bank_account_number'], '0150417961300')

    def test_generating_twice_keeps_the_same_number(self):
        """The number is already written on slips the student has taken to the
        bank, so opening the page again must not replace it."""
        first = self._generate(family=ChargeType.FEE).json()[0]
        again = self._generate(family=ChargeType.FEE).json()[0]
        self.assertEqual(first['reference'], again['reference'])
        self.assertEqual(
            Invoice.objects.filter(profile=self.asha, invoice_group='Tuition Fee',
                                   cancelled=False).count(), 1)

    def test_naming_one_instalment_still_invoices_the_whole_payment(self):
        first = StudentCharge.objects.filter(
            profile=self.asha, charge_type__name='Tuition Fee').order_by('due_date').first()
        invoices = self._generate(charges=[first.id]).json()
        self.assertEqual(invoices[0]['installment_count'], 5)
        self.assertEqual(Decimal(invoices[0]['total']), Decimal('1600000.00'))

    def test_the_invoice_is_valid_until_the_academic_year_ends(self):
        invoice = self._generate(family=ChargeType.FEE).json()[0]
        self.assertEqual(invoice['expires_on'], '2027-06-30')
        self.assertEqual(self.year.closes_on, date(2027, 6, 30))

        latest = max(c.due_date for c in StudentCharge.objects.filter(
            profile=self.asha, charge_type__name='Tuition Fee'))
        self.assertGreater(date.fromisoformat(invoice['expires_on']), latest)

    def test_the_office_can_set_the_day_the_year_closes(self):
        self.year.end_date = date(2027, 8, 15)
        self.year.save(update_fields=['end_date'])
        invoice = self._generate(family=ChargeType.FEE).json()[0]
        self.assertEqual(invoice['expires_on'], '2027-08-15')

    # ── what each payment is made of ─────────────────────────────────────────

    def test_the_direct_costs_invoice_lists_every_item_in_the_fee_structure(self):
        """Direct costs are a dozen separate items. The bill has to name them,
        or the student cannot tell what the total is made of."""
        response = self._generate(family=ChargeType.DIRECT_COST)
        self.assertEqual(response.status_code, 201, response.content)
        invoices = response.json()
        self.assertEqual(len(invoices), 1)
        invoice = invoices[0]
        self.assertEqual(invoice['invoice_group'], 'Direct Costs')

        billed = StudentCharge.objects.filter(
            profile=self.asha, charge_type__family=ChargeType.DIRECT_COST)
        self.assertEqual(invoice['installment_count'], billed.count())
        self.assertEqual(Decimal(invoice['total']),
                         sum(c.amount for c in billed))

        named = {line['charge_type_name'] for line in invoice['lines']}
        for item in ['Registration fees', 'Medical fees (Health Insurance)', 'Student union',
                     'Caution money', 'Admission fee', 'Uniforms', 'Graduation fees',
                     'NACTE Quality Assurance Fee']:
            self.assertIn(item, named)

        # And every one of them appears on the printed page.
        page = self.portal.get(f"/invoice/{invoice['reference']}/").content.decode()
        for item in named:
            self.assertIn(item, page)

    def test_school_fees_and_direct_costs_are_separate_numbers_and_accounts(self):
        fees = self._generate(family=ChargeType.FEE).json()[0]
        direct = self._generate(family=ChargeType.DIRECT_COST).json()[0]
        self.assertNotEqual(fees['reference'], direct['reference'])
        self.assertEqual(fees['bank_account_number'], '0150417961300')
        self.assertEqual(direct['bank_account_number'], '0150417961301')

    def test_selecting_across_groups_yields_one_invoice_per_account(self):
        tuition = StudentCharge.objects.filter(
            profile=self.asha, charge_type__name='Tuition Fee').order_by('due_date').first()
        registration = StudentCharge.objects.filter(
            profile=self.asha, charge_type__name='Registration fees').first()

        response = self.portal.post(
            '/api/my-fees/invoice/', {'charges': [tuition.id, registration.id]},
            content_type='application/json')
        self.assertEqual(response.status_code, 201, response.content)
        invoices = {i['invoice_group']: i for i in response.json()}
        self.assertEqual(sorted(invoices), ['Direct Costs', 'Tuition Fee'])
        self.assertEqual(invoices['Tuition Fee']['bank_account_number'], '0150417961300')
        self.assertEqual(invoices['Direct Costs']['bank_account_number'], '0150417961301')
        self.assertNotEqual(invoices['Tuition Fee']['reference'],
                            invoices['Direct Costs']['reference'])

    # ── other payments ───────────────────────────────────────────────────────

    def test_an_other_payment_raised_mid_year_joins_its_own_invoice(self):
        """A supplementary exam is declared after the year is under way. It is
        an other payment, not a direct cost, and gets its own number."""
        supp = ChargeType.objects.get(name='Supplementary Exam')
        charge = finance.raise_charge(self.asha, supp, self.year, Decimal('40000.00'),
                                      date(2027, 4, 30))

        response = self._generate(family=ChargeType.OTHER)
        self.assertEqual(response.status_code, 201, response.content)
        invoices = {i['invoice_group']: i for i in response.json()}
        self.assertIn('Examination Charges', invoices)
        exams = invoices['Examination Charges']
        self.assertEqual(Decimal(exams['total']), Decimal('40000.00'))
        self.assertIn(charge.id, [line['charge'] for line in exams['lines']])

        # A second one declared later joins that invoice rather than starting
        # a new reference.
        finance.raise_charge(self.asha, supp, self.year, Decimal('40000.00'),
                             date(2027, 5, 30))
        again = {i['invoice_group']: i
                 for i in self._generate(family=ChargeType.OTHER).json()}
        self.assertEqual(again['Examination Charges']['reference'], exams['reference'])
        self.assertEqual(Decimal(again['Examination Charges']['total']), Decimal('80000.00'))

    def test_a_student_not_billed_for_a_payment_is_told_so(self):
        response = self._generate(family=ChargeType.OTHER)
        self.assertEqual(response.status_code, 400)
        self.assertIn('nothing billed', response.json()['detail'].lower())

    def test_an_unknown_kind_of_payment_is_refused(self):
        response = self._generate(family='satchels')
        self.assertEqual(response.status_code, 400)

    # ── what the student can choose from ─────────────────────────────────────

    def test_the_portal_lists_the_payments_that_can_be_invoiced(self):
        response = self.portal.get('/api/my-fees/invoice/options/')
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()
        self.assertEqual(data['academic_year']['closes_on'], '2027-06-30')

        payments = {p['group']: p for p in data['payments']}
        self.assertEqual(sorted(payments), ['Direct Costs', 'Tuition Fee'])
        self.assertEqual(payments['Tuition Fee']['family'], ChargeType.FEE)
        self.assertEqual(payments['Tuition Fee']['family_display'], 'School Fees')
        self.assertEqual(payments['Tuition Fee']['installments'], 5)
        self.assertEqual(Decimal(payments['Tuition Fee']['outstanding']),
                         Decimal('1600000.00'))
        self.assertIsNone(payments['Tuition Fee']['invoice'])

        # Direct costs arrive itemised, straight from the fee structure.
        items = {i['name'] for i in payments['Direct Costs']['items']}
        self.assertIn('Registration fees', items)
        self.assertIn('Caution money', items)

        # Once generated, the option says which number already covers it.
        reference = self._generate(family=ChargeType.FEE).json()[0]['reference']
        after = {p['group']: p for p in
                 self.portal.get('/api/my-fees/invoice/options/').json()['payments']}
        self.assertEqual(after['Tuition Fee']['invoice']['reference'], reference)

    def test_the_dashboard_offers_generation_and_a_list_of_invoices(self):
        body = self.portal.get('/student-dashboard/').content.decode()
        self.assertIn('Generate Invoice', body)
        self.assertIn('My Invoices', body)
        self.assertIn('data-view="invoice-generate"', body)
        self.assertIn('data-view="invoice-list"', body)
        # Each payment offers its own button, itemised.
        self.assertIn('data-invoice-group="Tuition Fee"', body)
        self.assertIn('data-invoice-group="Direct Costs"', body)
        self.assertIn('Registration fees', body)

        reference = self._generate(family=ChargeType.FEE).json()[0]['reference']
        body = self.portal.get('/student-dashboard/').content.decode()
        self.assertIn(reference, body)
        self.assertIn(f'/invoice/{reference}/?download=1', body)

    def test_downloading_opens_the_save_dialogue(self):
        reference = self._generate(family=ChargeType.FEE).json()[0]['reference']

        viewed = self.portal.get(f'/invoice/{reference}/').content.decode()
        self.assertNotIn('window.print()', viewed.split('<div class="actions">')[1]
                         .split('</div>')[1])

        downloaded = self.portal.get(f'/invoice/{reference}/?download=1').content.decode()
        self.assertIn('setTimeout(() => window.print()', downloaded)
        self.assertIn('Expire date', downloaded)

    # ── how a bill reads ─────────────────────────────────────────────────────

    def test_the_school_fees_bill_states_the_amount_and_the_instalment_count(self):
        """One item paid in instalments. Printing five near-identical rows told
        the student nothing the amount and the count do not already say."""
        reference = self._generate(family=ChargeType.FEE).json()[0]['reference']
        page = self.portal.get(f'/invoice/{reference}/').content.decode()

        self.assertIn('Amount required', page)
        self.assertIn('1600000.00', page)
        self.assertIn('payable in 5 instalments', page)
        self.assertIn('Expire date', page)
        self.assertIn('30-06-2027', page)
        # No item table, and no instalment dates down the page.
        self.assertNotIn('Item(s) details', page)
        self.assertNotIn('Instalment 1 of 5', page)

    def test_the_direct_costs_bill_lists_the_components_of_its_total(self):
        """Fourteen items over two instalments used to print as twenty-eight
        rows saying the same fourteen things twice."""
        invoice = self._generate(family=ChargeType.DIRECT_COST).json()[0]
        page = self.portal.get(f"/invoice/{invoice['reference']}/").content.decode()

        self.assertIn('Item(s) details', page)
        items = ChargeType.objects.filter(family=ChargeType.DIRECT_COST,
                                          charges__profile=self.asha).distinct()
        self.assertEqual(items.count(), 14)
        for item in items:
            self.assertIn(item.name, page)

        # One row per item, not per instalment, and they sum to the total.
        components = finance.invoice_components(
            Invoice.objects.get(reference=invoice['reference']))
        self.assertEqual(len(components), 14)
        self.assertTrue(all(c['installments'] == 2 for c in components))
        self.assertEqual(sum(c['amount'] for c in components), Decimal('895000.00'))
        self.assertIn('Amount in Tshs.', page)
        self.assertIn('Frequency', page)

    def test_the_accountants_item_code_prints_against_the_item(self):
        registration = ChargeType.objects.get(name='Registration fees')
        registration.code = '142201410231'
        registration.save(update_fields=['code'])

        invoice = self._generate(family=ChargeType.DIRECT_COST).json()[0]
        page = self.portal.get(f"/invoice/{invoice['reference']}/").content.decode()
        self.assertIn('142201410231', page)
        self.assertIn('Item code', page)

    def test_the_bill_shows_what_has_been_paid_off_it(self):
        invoice = Invoice.objects.get(
            reference=self._generate(family=ChargeType.FEE).json()[0]['reference'])
        first = StudentCharge.objects.filter(
            profile=self.asha, charge_type__name='Tuition Fee').order_by('due_date').first()
        staff = User.objects.create_superuser('acct2', 'a@b.c', 'pw')
        finance.record_payment(
            self.asha, first.amount, date(2026, 10, 1), recorded_by=staff, invoice=invoice,
            bank_reference='GWX101858970303', efd_receipt_no='REC250068903',
            allocations=[(first, first.amount)])

        rows = finance.invoice_transactions(invoice)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['debit'], Decimal('1600000.00'))     # billed
        self.assertEqual(rows[1]['credit'], Decimal('320000.00'))     # paid
        self.assertEqual(rows[1]['balance'], Decimal('1280000.00'))

        page = self.portal.get(f'/invoice/{invoice.reference}/').content.decode()
        self.assertIn('Invoice transactions', page)
        self.assertIn('GWX101858970303', page)
        self.assertIn('1280000.00', page)

    # ── the dates, where they belong ─────────────────────────────────────────

    def test_instalment_dates_are_reminders_on_the_dashboard(self):
        """The invoice stands all year, so the dates cannot live on it. They
        are a calendar, and the student is told before each one arrives."""
        self._generate(family=ChargeType.FEE)
        reminders = finance.installment_reminders(self.asha, self.year)

        tuition = [r for r in reminders if r['name'] == 'Tuition Fee']
        self.assertEqual(len(tuition), 5)
        self.assertEqual([r['installment_number'] for r in tuition], [1, 2, 3, 4, 5])
        self.assertEqual(tuition[0]['amount'], Decimal('320000.00'))
        self.assertTrue(all(r['reference'] for r in tuition),
                        'a reminder must name the invoice to quote when paying')
        self.assertEqual(reminders, sorted(reminders, key=lambda r: r['due_date']))

        body = self.portal.get('/student-dashboard/').content.decode()
        self.assertIn('Fee reminders', body)
        self.assertIn('instalment 1 of 5', body)

    def test_a_reminder_says_how_near_its_date_is(self):
        charge = StudentCharge.objects.filter(
            profile=self.asha, charge_type__name='Tuition Fee').order_by('due_date').first()
        today = charge.due_date

        by_name = {r['due_date']: r for r
                   in finance.installment_reminders(self.asha, self.year, today=today)}
        self.assertEqual(by_name[charge.due_date]['urgency'], finance.DUE_SOON)
        self.assertEqual(by_name[charge.due_date]['days_label'], 'due today')

        late = finance.installment_reminders(
            self.asha, self.year, today=charge.due_date + timedelta(days=3))
        overdue = [r for r in late if charge in r['charges']][0]
        self.assertEqual(overdue['urgency'], finance.OVERDUE)
        self.assertEqual(overdue['days_label'], '3 days overdue')

    def test_a_settled_instalment_stops_being_reminded_about(self):
        charge = StudentCharge.objects.filter(
            profile=self.asha, charge_type__name='Tuition Fee').order_by('due_date').first()
        staff = User.objects.create_superuser('acct3', 'a@b.c', 'pw')
        finance.record_payment(self.asha, charge.amount, date(2026, 9, 1),
                               recorded_by=staff, allocations=[(charge, charge.amount)])

        remaining = finance.installment_reminders(self.asha, self.year)
        tuition = [r for r in remaining if r['name'] == 'Tuition Fee']
        self.assertNotIn(charge.id, [c.id for r in remaining for c in r['charges']])
        self.assertEqual(len(tuition), 4)
        # Still counted against the whole schedule, not renumbered from one.
        self.assertEqual([r['installment_number'] for r in tuition], [2, 3, 4, 5])
        self.assertTrue(all(r['installments_total'] == 5 for r in tuition))

    def test_a_reminder_is_one_deposit_not_one_item(self):
        """Direct costs are fourteen items falling due on two dates, and the
        student pays them as two deposits. Reminding them twenty-eight times
        for fourteenths of the amount is noise, not a reminder."""
        self._generate(family=ChargeType.DIRECT_COST)
        reminders = finance.installment_reminders(self.asha, self.year)

        direct = [r for r in reminders if r['group'] == 'Direct Costs']
        self.assertEqual(len(direct), 2)
        self.assertEqual([r['installment_number'] for r in direct], [1, 2])
        self.assertTrue(all(r['installments_total'] == 2 for r in direct))
        self.assertTrue(all(r['items'] == 14 for r in direct))
        self.assertEqual(sum(r['amount'] for r in direct), Decimal('895000.00'))
        self.assertTrue(all(r['name'] == 'Direct Costs' for r in direct),
                        'a deposit covering many items is named by the payment')

        # The whole year is a short list, not one row per charge.
        self.assertEqual(len(reminders), 7)
        self.assertLess(len(reminders), StudentCharge.objects.filter(
            profile=self.asha).count())

    def test_the_statement_api_carries_the_reminders(self):
        self._generate(family=ChargeType.FEE)
        data = self.portal.get('/api/my-fees/').json()
        self.assertIn('reminders', data)
        first = data['reminders'][0]
        for key in ('name', 'installment_number', 'installments_total', 'amount',
                    'due_date', 'urgency', 'days_label', 'reference'):
            self.assertIn(key, first)

    def test_a_student_cannot_invoice_charges_that_are_not_theirs(self):
        other_module = Module.objects.create(
            name='Anatomy', code='ANA101', teacher='T',
            class_level=self.level, semester=self.sem)
        other = finance.profile_for_student(Student.objects.create(
            nactvet_reg_no='BPH/2026/002', name='Baraka', module=other_module))
        finance.generate_charges(other, self.year, class_level=self.level)
        theirs = StudentCharge.objects.filter(profile=other).first()

        response = self.portal.post('/api/my-fees/invoice/', {'charges': [theirs.id]},
                                    content_type='application/json')
        self.assertEqual(response.status_code, 400)


class OtherChargesTableTests(TestCase):
    """The direct-costs invoice must be the college's Other Charges table.

    The admission form for 2026/2027 publishes it twice — page 9 for first
    years and new entrants, page 10 for continuing students — with a Day and a
    Hostel column each:

        Level 4, and new entrants at 5 and 6      895,000 day · 1,295,000 hostel
        Continuing students at levels 5 and 6     605,000 day · 1,005,000 hostel

    Those are not four tables. They are one table of fifteen rows: the 400,000
    between each pair is Accommodation, charged only to hostel residents, and
    the 290,000 between the pairs is the six `once` rows a continuing student
    has already paid. A student must be able to hold the invoice next to the
    form and tick down it, so this pins all four totals and the row order.
    """

    #                                     name, amount, frequency
    PUBLISHED = [
        ('Registration fees', 10_000, 'Each year'),
        ('Examination fees (Semester 1)', 150_000, 'Each year'),
        ('Medical fees (Health Insurance)', 60_000, 'Each year'),
        ('Research/field fees', 150_000, 'Each year'),
        ('Caution money', 50_000, 'Once'),
        ('Student union', 10_000, 'Each year'),
        ('Admission fee', 50_000, 'Once'),
        ('National Examination (Semester II)', 150_000, 'Each year'),
        ('Identity card', 10_000, 'Once'),
        ('Clinical coat', 30_000, 'Once'),
        ('Graduation fees', 50_000, 'Once'),
        ('Continuous assessment Tests', 50_000, 'Each year'),
        ('Uniforms', 100_000, 'Once'),
        ('NACTE Quality Assurance Fee', 25_000, 'Each year'),
        ('Accommodation', 400_000, 'Each year'),
    ]

    def setUp(self):
        self.first_year = AcademicYear.objects.create(name='2025/2026')
        self.year = AcademicYear.objects.create(name='2026/2027', is_active=True)
        for year in (self.first_year, self.year):
            Semester.objects.create(academic_year=year, number=1,
                                    is_active=year == self.year)
        for name, order in [('NTA Level 4', 4), ('NTA Level 5', 5), ('NTA Level 6', 6)]:
            ClassLevel.objects.create(name=name, order=order)
        for year in (self.first_year, self.year):
            call_command('seed_fee_structure', year=year.name,
                         first_due='2026-09-30', verbosity=0)
        self.level4 = ClassLevel.objects.get(order=4)
        self.level5 = ClassLevel.objects.get(order=5)
        self.accommodation = ChargeType.objects.get(name='Accommodation')

    def _student(self, reg_no, level, year):
        semester = year.semesters.first()
        module, _ = Module.objects.get_or_create(
            code=f'MOD{level.order}', defaults={'name': 'Pharmaceutics', 'teacher': 'T'},
            class_level=level, semester=semester)
        enrollment = Student.objects.create(nactvet_reg_no=reg_no, name='Asha Juma',
                                            module=module)
        enrollment.set_portal_pin('Portal#2026', require_change=False)
        enrollment.save()
        return finance.profile_for_student(enrollment)

    def _direct_costs(self, profile, year):
        """The direct-costs invoice as the student receives it."""
        invoices = finance.issue_invoices_for_family(profile, year, ChargeType.DIRECT_COST)
        self.assertEqual(len(invoices), 1, 'direct costs is one bill into one account')
        invoice = invoices[0]
        return invoice, finance.invoice_components(invoice)

    # ── page 9: first years, and new entrants at 5 and 6 ─────────────────────

    def test_a_first_year_day_student_is_billed_the_published_895_000(self):
        profile = self._student('BPH/2026/001', self.level4, self.year)
        finance.generate_charges(profile, self.year, class_level=self.level4)

        invoice, items = self._direct_costs(profile, self.year)
        self.assertEqual(finance.money(invoice.total), Decimal('895000.00'))
        self.assertEqual(len(items), 14)
        self.assertNotIn('Accommodation', [i['name'] for i in items],
                         'a day student is never billed for a hostel place')

    def test_a_first_year_hostel_student_is_billed_the_published_1_295_000(self):
        profile = self._student('BPH/2026/002', self.level4, self.year)
        finance.generate_charges(profile, self.year, class_level=self.level4)
        # Accommodation is optional — the office assigns it when a place is taken.
        finance.raise_charge(profile, self.accommodation, self.year,
                             Decimal('400000.00'), date(2026, 9, 30))

        invoice, items = self._direct_costs(profile, self.year)
        self.assertEqual(finance.money(invoice.total), Decimal('1295000.00'))
        self.assertEqual(len(items), 15)
        self.assertEqual(items[-1]['name'], 'Accommodation')   # row 15
        self.assertEqual(items[-1]['amount'], Decimal('400000.00'))

    def test_the_invoice_lists_the_table_in_its_published_order(self):
        profile = self._student('BPH/2026/003', self.level4, self.year)
        finance.generate_charges(profile, self.year, class_level=self.level4)
        finance.raise_charge(profile, self.accommodation, self.year,
                             Decimal('400000.00'), date(2026, 9, 30))
        invoice, items = self._direct_costs(profile, self.year)

        self.assertEqual([i['name'] for i in items], [n for n, _a, _f in self.PUBLISHED])
        self.assertEqual([i['amount'] for i in items],
                         [Decimal(a) for _n, a, _f in self.PUBLISHED])
        self.assertEqual([i['frequency'] for i in items], [f for _n, _a, f in self.PUBLISHED])

        # And it reads that way on the printed page, row 1 through row 15.
        portal = Client()
        portal.post('/login/', {'identifier': 'BPH/2026/003', 'secret': 'Portal#2026'})
        page = portal.get(f'/invoice/{invoice.reference}/').content.decode()
        positions = [page.index(name) for name, _a, _f in self.PUBLISHED]
        self.assertEqual(positions, sorted(positions))

    # ── page 10: continuing students at 5 and 6 ──────────────────────────────

    def test_a_continuing_day_student_is_billed_the_published_605_000(self):
        profile = self._student('BPH/2025/010', self.level4, self.first_year)
        finance.generate_charges(profile, self.first_year, class_level=self.level4)
        # Next year, now at level 5. The `once` rows do not come round again.
        finance.generate_charges(profile, self.year, class_level=self.level5)

        invoice, items = self._direct_costs(profile, self.year)
        self.assertEqual(finance.money(invoice.total), Decimal('605000.00'))
        self.assertEqual(len(items), 8)
        self.assertEqual([i['frequency'] for i in items], ['Each year'] * 8)
        for gone in ('Caution money', 'Admission fee', 'Identity card',
                     'Clinical coat', 'Graduation fees', 'Uniforms'):
            self.assertNotIn(gone, [i['name'] for i in items])

    def test_a_continuing_hostel_student_is_billed_the_published_1_005_000(self):
        profile = self._student('BPH/2025/011', self.level4, self.first_year)
        finance.generate_charges(profile, self.first_year, class_level=self.level4)
        finance.generate_charges(profile, self.year, class_level=self.level5)
        finance.raise_charge(profile, self.accommodation, self.year,
                             Decimal('400000.00'), date(2026, 9, 30))

        invoice, items = self._direct_costs(profile, self.year)
        self.assertEqual(finance.money(invoice.total), Decimal('1005000.00'))
        self.assertEqual(len(items), 9)

    def test_a_new_entrant_at_level_5_is_billed_the_first_year_table(self):
        """Page 9 covers "new students for NTA level 5 and level 6" too — they
        have not paid caution money or bought a clinical coat either."""
        profile = self._student('BPH/2026/020', self.level5, self.year)
        finance.generate_charges(profile, self.year, class_level=self.level5)

        invoice, items = self._direct_costs(profile, self.year)
        self.assertEqual(finance.money(invoice.total), Decimal('895000.00'))
        self.assertEqual(len(items), 14)

    # ── tuition is separate, and the same at every level ─────────────────────

    def test_tuition_is_1_600_000_at_every_level_and_banked_apart(self):
        profile = self._student('BPH/2026/030', self.level4, self.year)
        finance.generate_charges(profile, self.year, class_level=self.level4)

        invoices = finance.issue_invoices_for_family(profile, self.year, ChargeType.FEE)
        self.assertEqual(len(invoices), 1)
        tuition = invoices[0]
        self.assertEqual(finance.money(tuition.total), Decimal('1600000.00'))
        self.assertEqual(tuition.lines.count(), 5)          # payable in five
        self.assertEqual(tuition.bank_account.account_number, '0150417961300')

        direct, _items = self._direct_costs(profile, self.year)
        self.assertEqual(direct.bank_account.account_number, '0150417961301')
        self.assertNotEqual(tuition.reference, direct.reference)
