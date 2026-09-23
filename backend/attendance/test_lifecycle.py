"""Postponement, finishing, and invoicing any fee.

The college's rules, each one a test:

  * a student asks to postpone from the portal, or records writes down a
    paper request; only the Principal decides, and declining says why;
  * semester 1 postpones the whole year, semester 2 only semester 2;
  * approval takes them off the semester(s) — nothing deleted, marks kept —
    reverses the fees for the period, and what they paid is credit that
    settles the bill when they come back;
  * a finished student is cleared by finance first (nothing owing), then
    declared cleared by the Principal, which archives them and closes the
    portal;
  * a student can be invoiced for tuition, direct costs, and each other fee
    on its own — the supplementary, the special exam, a repeat module, the
    TPH book — each only when they are billed for it.
"""

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from . import admissions, finance, lifecycle
from .models import (
    AcademicYear, Application, ChargeType, FeeStructure, Invoice, Module, Payment, Postponement,
    SemesterRegistration, Student, StudentCharge, StudentProfile, StudentResult, StudentStanding,
)
from .test_payment_schedule import ScheduleBase
from .views import set_roles

User = get_user_model()


class LifecycleBase(ScheduleBase):
    def setUp(self):
        super().setUp()
        self.principal_user = User.objects.create_user('boss', password='pw')
        set_roles(self.principal_user, ['principal'], full_name='Principal')
        self.records_user = User.objects.create_user('rec', password='pw')
        set_roles(self.records_user, ['records_officer'], full_name='Records')
        self.principal = APIClient(); self.principal.force_authenticate(self.principal_user)
        self.records = APIClient(); self.records.force_authenticate(self.records_user)
        self.m1 = Module.objects.create(name='Anatomy', code='PST04101', teacher='T', class_level=self.level4,
                                        semester=self.sem1, programme=self.pst, credits=3)
        self.m2 = Module.objects.create(name='Dispensing', code='PST04201', teacher='T', class_level=self.level4,
                                        semester=self.sem2, programme=self.pst, credits=3)

    def enrolled_student(self, reg_no='NIT/PST/2026/0500', *, semester=None):
        semester = semester or self.sem1
        profile = StudentProfile.objects.create(nactvet_reg_no=reg_no, name='Halima Studying')
        for each in ([self.sem1, self.sem2] if semester.number == 2 else [self.sem1]):
            SemesterRegistration.objects.create(profile=profile, semester=each, programme=self.pst,
                                                class_level=self.level4, kind=SemesterRegistration.NEW)
        enrollment = Student.objects.create(nactvet_reg_no=reg_no, name=profile.name, profile=profile,
                                            module=self.m1)
        if semester.number == 2:
            Student.objects.create(nactvet_reg_no=reg_no, name=profile.name, profile=profile, module=self.m2)
        enrollment.set_portal_pin('Portal#2026', require_change=False)
        enrollment.save()
        return profile, enrollment

    def portal_for(self, enrollment):
        session = self.client.session
        session['student_id'] = enrollment.id
        session.save()


class PostponementTests(LifecycleBase):
    def setUp(self):
        super().setUp()
        self.profile, self.enrollment = self.enrolled_student()
        StudentResult.objects.create(student=self.enrollment, assign1=8, cat1_theory=10)
        # Awamu 1 paid before they fell ill.
        finance.record_payment(self.profile, Decimal('945000'), date(2026, 10, 15),
                               recorded_by=self.officer, bank_reference='CRDB-1')

    def ask_from_portal(self, reason='Long illness; medical letter at records.'):
        self.portal_for(self.enrollment)
        return self.client.post('/api/my-postponement/', {'reason': reason}, content_type='application/json')

    def test_the_student_asks_and_the_principal_approves_the_whole_year(self):
        asked = self.ask_from_portal()
        request = Postponement.objects.get(profile=self.profile)

        decided = self.principal.post(f'/api/postponements/{request.id}/decide/',
                                      {'decision': 'approve'}, format='json')

        self.assertEqual(asked.status_code, 201, asked.data)
        self.assertEqual(decided.status_code, 200, decided.data)
        self.assertEqual(decided.data['scope'], 'The whole year')
        standing = StudentStanding.objects.get(profile=self.profile)
        self.assertEqual(standing.status, StudentStanding.POSTPONED)
        self.assertEqual((standing.return_year.name, standing.return_semester_number), ('2027/2028', 1))
        registration = SemesterRegistration.objects.get(profile=self.profile, semester=self.sem1)
        self.assertEqual(registration.status, SemesterRegistration.CANCELLED)
        # Off the class list, but the enrollment and its marks are kept.
        self.enrollment.refresh_from_db()
        self.assertIsNotNone(self.enrollment.withdrawn_at)
        self.assertTrue(StudentResult.objects.filter(student=self.enrollment, cat1_theory=10).exists())

    def test_the_year_s_fees_are_reversed_and_what_was_paid_is_credit(self):
        self.ask_from_portal()
        request = Postponement.objects.get(profile=self.profile)
        once_billed = sum(c.amount for c in StudentCharge.objects.filter(
            profile=self.profile, charge_type__frequency=ChargeType.ONCE))

        self.principal.post(f'/api/postponements/{request.id}/decide/', {'decision': 'approve'}, format='json')

        request.refresh_from_db()
        balance = finance.balance_for(self.profile, self.year)
        # Everything but the one-time charges comes off: 2,495,000 − 290,000.
        self.assertEqual(request.fees_reversed, Decimal('2495000.00') - once_billed)
        self.assertEqual(balance['billed'] - balance['waived'], once_billed)
        self.assertLess(balance['balance'], 0)                      # in credit
        # Nothing is deleted.
        self.assertEqual(StudentCharge.objects.filter(profile=self.profile).count(),
                         StudentCharge.objects.filter(profile=self.profile, academic_year=self.year).count())

    def test_semester_2_postpones_semester_2_only(self):
        profile, enrollment = self.enrolled_student('NIT/PST/2026/0501', semester=self.sem2)
        postponement = lifecycle.request_postponement(profile, self.sem2, 'Family emergency',
                                                      entered_by=self.records_user)

        lifecycle.decide_postponement(postponement, approve=True, actor=self.principal_user)

        self.assertEqual(SemesterRegistration.objects.get(profile=profile, semester=self.sem1).status,
                         SemesterRegistration.ACTIVE)
        self.assertEqual(SemesterRegistration.objects.get(profile=profile, semester=self.sem2).status,
                         SemesterRegistration.CANCELLED)
        reversed_dates = {c.due_date for c in StudentCharge.objects.filter(profile=profile, waived_amount__gt=0)}
        self.assertEqual(reversed_dates, {date(2027, 3, 25), date(2027, 5, 30)})
        self.assertEqual(postponement.return_semester_number, 2)

    def test_records_writes_down_a_paper_request_and_only_the_principal_decides(self):
        entered = self.records.post('/api/postponements/', {
            'reg_no': self.profile.nactvet_reg_no, 'reason': 'Letter dated 3 November'}, format='json')
        request = Postponement.objects.get(profile=self.profile)

        by_records = self.records.post(f'/api/postponements/{request.id}/decide/',
                                       {'decision': 'approve'}, format='json')
        bare_decline = self.principal.post(f'/api/postponements/{request.id}/decide/',
                                           {'decision': 'decline'}, format='json')
        declined = self.principal.post(f'/api/postponements/{request.id}/decide/',
                                       {'decision': 'decline', 'note': 'Exams are in two weeks'}, format='json')

        self.assertEqual(entered.status_code, 201, entered.data)
        self.assertFalse(entered.data['by_student'])
        self.assertEqual(by_records.status_code, 403)
        self.assertEqual(bare_decline.status_code, 400)
        self.assertEqual(declined.data['status'], Postponement.DECLINED)
        self.assertEqual(StudentStanding.objects.filter(profile=self.profile,
                                                        status=StudentStanding.POSTPONED).count(), 0)

    def test_one_request_at_a_time_and_only_for_a_semester_they_are_in(self):
        self.ask_from_portal()

        again = self.ask_from_portal()
        stranger = StudentProfile.objects.create(nactvet_reg_no='X/1', name='Not Registered')

        self.assertEqual(again.status_code, 400)
        with self.assertRaises(lifecycle.LifecycleError):
            lifecycle.request_postponement(stranger, self.sem1, 'Reason')

    def test_the_portal_says_where_they_stand(self):
        self.ask_from_portal()
        lifecycle.decide_postponement(Postponement.objects.get(profile=self.profile), approve=True,
                                      actor=self.principal_user)

        page = self.client.get('/student-dashboard/')

        self.assertContains(page, 'Postponed — due back')
        self.assertContains(page, '2027/2028')

    def test_they_come_back_and_what_they_paid_settles_the_new_bill(self):
        self.ask_from_portal()
        lifecycle.decide_postponement(Postponement.objects.get(profile=self.profile), approve=True,
                                      actor=self.principal_user)
        credit = -finance.balance_for(self.profile, self.year)['balance']
        next_year = AcademicYear.objects.get(name='2027/2028')
        finance.copy_schedules(self.year, next_year)
        from .models import Semester
        next_sem1 = Semester.objects.create(academic_year=next_year, number=1)
        for structure in FeeStructure.objects.filter(academic_year=self.year):
            copy = FeeStructure.objects.create(
                charge_type=structure.charge_type, class_level=structure.class_level, academic_year=next_year,
                amount=structure.amount, billing_period=structure.billing_period,
                installments=structure.installments)
            finance.set_installment_schedule(copy, [finance._next_year(row.due_date)
                                                    for row in structure.installment_schedule.all()])

        self.assertIn(self.profile.id, [s.profile_id for s in admissions.due_back(next_sem1)])
        application = admissions.open_application(
            profile=self.profile, semester=next_sem1, programme=self.pst, class_level=self.level4,
            kind=Application.CONTINUING, actor=self.officer, ignore_window=True)

        next_bill = finance.balance_for(self.profile, next_year)
        self.assertEqual(finance.balance_for(self.profile, self.year)['balance'], 0)
        self.assertEqual(next_bill['balance'], next_bill['billed'] - next_bill['waived'] - credit)
        carried = Payment.objects.get(profile=self.profile, channel=Payment.CREDIT)
        self.assertEqual(carried.amount, 0)
        self.assertEqual(sum(a.amount for a in carried.allocations.all()), 0)
        self.assertEqual(application.state, Application.FINANCE)


class FinishingTests(LifecycleBase):
    def setUp(self):
        super().setUp()
        self.accounts_client = self.accounts
        self.profile, self.enrollment = self.enrolled_student('NIT/PST/2024/0900')
        from . import progression
        progression.set_standing(self.profile, StudentStanding.COMPLETED, class_level=self.level4,
                                 programme=self.pst, reason='Finished the programme.')
        self.owed = finance.balance_for(self.profile)['balance']

    def test_finance_will_not_clear_a_student_who_owes(self):
        response = self.accounts.post(f'/api/finishing/{self.profile.id}/finance-clear/', {}, format='json')

        self.assertEqual(response.status_code, 400)
        self.assertIn('still owed', response.data['detail'])

    def test_finance_clears_then_the_principal_declares_and_they_are_archived(self):
        finance.record_payment(self.profile, self.owed, date.today(), recorded_by=self.officer,
                               bank_reference='CRDB-9')
        too_early = self.principal.post(f'/api/finishing/{self.profile.id}/declare/', {}, format='json')
        not_the_accountant = self.accounts.post(f'/api/finishing/{self.profile.id}/declare/', {}, format='json')

        cleared = self.accounts.post(f'/api/finishing/{self.profile.id}/finance-clear/', {}, format='json')
        declared = self.principal.post(f'/api/finishing/{self.profile.id}/declare/', {}, format='json')

        self.assertEqual(too_early.status_code, 400)
        self.assertEqual(not_the_accountant.status_code, 403)
        self.assertEqual(cleared.status_code, 200, cleared.data)
        self.assertEqual(declared.data['status'], StudentStanding.ARCHIVED)
        # The portal closes; the record stays.
        response = self.client.post('/login/', {'identifier': self.profile.nactvet_reg_no,
                                                'secret': 'Portal#2026'})
        self.assertNotIn('student_id', self.client.session)
        self.assertTrue(StudentCharge.objects.filter(profile=self.profile).exists())

    def test_the_statement_covers_every_year_and_is_for_finance_and_the_principal(self):
        earlier = self.last_year
        charge_type = ChargeType.objects.get(name='Tuition Fee')
        finance.raise_charge(self.profile, charge_type, earlier, Decimal('100000'), date(2026, 1, 1))

        def as_user(user):
            from django.test import Client
            client = Client()
            client.force_login(user)
            return client.get(f'/statement/{self.profile.id}/')

        page = as_user(self.accountant)
        by_principal = as_user(self.principal_user)
        by_records = as_user(self.records_user)

        self.assertEqual(page.status_code, 200)
        self.assertContains(page, '2025/2026')
        self.assertContains(page, '2026/2027')
        self.assertEqual(by_principal.status_code, 200)
        self.assertEqual(by_records.status_code, 403)

    def test_both_offices_see_the_list(self):
        rows = self.principal.get('/api/finishing/').data

        self.assertEqual([row['reg_no'] for row in rows], ['NIT/PST/2024/0900'])
        self.assertEqual(self.records.get('/api/finishing/').status_code, 403)


class OtherFeesInvoiceTests(LifecycleBase):
    def setUp(self):
        super().setUp()
        self.profile, self.enrollment = self.enrolled_student('NIT/PST/2026/0700')
        self.portal_for(self.enrollment)

    def raise_other_fees(self):
        tph = ChargeType.objects.create(name='TPH Book', family=ChargeType.OTHER, applies=ChargeType.ON_REQUEST,
                                        bank_account=ChargeType.objects.get(name='Registration fees').bank_account)
        supp = ChargeType.objects.get(name='Supplementary Exam')
        finance.raise_charge(self.profile, tph, self.year, Decimal('35000'), date(2026, 11, 1))
        finance.raise_charge(self.profile, supp, self.year, Decimal('40000'), date(2027, 4, 30))

    def test_each_other_fee_is_its_own_option_only_when_billed(self):
        before = [row['group'] for row in self.client.get('/api/my-fees/invoice/options/').json()['payments']]
        self.raise_other_fees()
        after = [row['group'] for row in self.client.get('/api/my-fees/invoice/options/').json()['payments']]

        self.assertEqual(before, ['Tuition Fee', 'Direct Costs'])
        self.assertEqual(after, ['Tuition Fee', 'Direct Costs', 'Supplementary Exam', 'TPH Book'])

    def test_the_supplementary_and_the_tph_book_each_get_their_own_invoice(self):
        self.raise_other_fees()

        supp = self.client.post('/api/my-fees/invoice/', {'group': 'Supplementary Exam'},
                                content_type='application/json')
        every_other = self.client.post('/api/my-fees/invoice/', {'family': ChargeType.OTHER},
                                       content_type='application/json')

        self.assertEqual(supp.status_code, 201, supp.content)
        self.assertEqual([(i['invoice_group'], Decimal(i['total'])) for i in supp.json()],
                         [('Supplementary Exam', Decimal('40000.00'))])
        invoices = {i['invoice_group']: i for i in every_other.json()}
        self.assertEqual(sorted(invoices), ['Supplementary Exam', 'TPH Book'])
        self.assertEqual(invoices['Supplementary Exam']['reference'], supp.json()[0]['reference'])
        self.assertNotEqual(invoices['TPH Book']['reference'], supp.json()[0]['reference'])

    def test_the_accountant_raises_the_invoice_for_one_fee(self):
        self.raise_other_fees()
        statement = self.accounts.get(f'/api/finance/statement/{self.profile.id}/').data

        response = self.accounts.post('/api/finance/issue-invoice/',
                                      {'profile': self.profile.id, 'group': 'TPH Book'}, format='json')
        refused = self.accounts.post('/api/finance/issue-invoice/',
                                     {'profile': self.profile.id, 'group': 'Special Exam'}, format='json')

        self.assertEqual([kind['label'] for kind in statement['payment_kinds']],
                         ['Tuition fee', 'Direct costs', 'Supplementary Exam', 'TPH Book'])
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual([(i['invoice_group'], Decimal(i['total'])) for i in response.data],
                         [('TPH Book', Decimal('35000.00'))])
        self.assertEqual(refused.status_code, 400)
