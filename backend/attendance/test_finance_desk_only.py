"""From 20 October 2026: admission and finance make a student ours.

  * the year's bill is raised once, at the finance desk; a module links a
    student and bills nothing (the repeat-module charge aside);
  * an admitted student gets a portal password before any module exists, and
    the modules that come later take it on;
  * money paid beyond the bill — Level 5 and 6 tuition at once — is credit on
    account, shown on the statement and used for the next bill raised at the
    finance desk, only for charges paid into the same bank account;
  * the semester pass: registered, still studying, and paid up to the
    semester's first instalment; its check code catches an edited copy.
"""

import json
import re
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import Client
from rest_framework.test import APIClient

from . import admissions, finance, passwords, progression, semester_pass
from .models import (
    AcademicYear, Application, BankAccount, ChargeType, ClassLevel, FeeStructure, Module, PaymentSchedule,
    PaymentStep, Semester, SemesterRegistration, Student, StudentCharge, StudentPass, StudentProfile,
)
from .test_admissions import AdmissionBase, complete_record
from .views import set_roles

User = get_user_model()


def office(username, *roles):
    user = User.objects.create_user(username, password='pw')
    set_roles(user, list(roles), full_name=username.title())
    client = APIClient()
    client.force_login(user)
    return user, client


class Accounts(AdmissionBase):
    def setUp(self):
        super().setUp()
        self.tuition_account = BankAccount.objects.create(
            bank_name='CRDB', account_name='BPHIST', account_number='0150417961300', purpose='Tuition fee')
        self.other_account = BankAccount.objects.create(
            bank_name='CRDB', account_name='BPHIST', account_number='0150417961301', purpose='Other charges')
        self.tuition = ChargeType.objects.create(
            name='Tuition fee', family=ChargeType.FEE, applies=ChargeType.AUTOMATIC,
            bank_account=self.tuition_account, invoice_group='Tuition fee', blocks_registration=True)
        self.direct = ChargeType.objects.create(
            name='Direct costs', family=ChargeType.DIRECT_COST, applies=ChargeType.AUTOMATIC,
            bank_account=self.other_account, invoice_group='Direct costs', blocks_registration=True)

    def price(self, charge_type, amount, year, level, dates):
        structure = FeeStructure.objects.create(
            charge_type=charge_type, programme=self.pst, class_level=level, academic_year=year,
            amount=Decimal(amount), billing_period=FeeStructure.ACADEMIC_YEAR, installments=len(dates))
        finance.set_installment_schedule(structure, dates)
        return structure


class AModuleBillsNothingTests(Accounts):
    def setUp(self):
        super().setUp()
        self.price(self.tuition, '1000000', self.year, self.level4, [date(2026, 11, 30)])
        self.profile = complete_record(StudentProfile.objects.create(nactvet_reg_no='NS0100/0001/2026',
                                                                     name='Anitha Steven'))
        self.registration = SemesterRegistration.objects.create(
            profile=self.profile, semester=self.sem1, programme=self.pst, class_level=self.level4,
            kind=SemesterRegistration.NEW)

    def test_linking_modules_later_raises_no_charge(self):
        finance.generate_charges(self.profile, self.year, class_level=self.level4)    # the finance desk
        before = list(StudentCharge.objects.filter(profile=self.profile).values_list('id', 'amount'))
        # The schedule changes after admission — what billed a part twice before.
        FeeStructure.objects.filter(charge_type=self.tuition).update(installments=2)
        finance.set_installment_schedule(FeeStructure.objects.get(charge_type=self.tuition),
                                         [date(2026, 11, 30), date(2027, 3, 1)])
        _, hod = office('hod', 'hod')
        made = hod.post('/api/modules/', {'name': 'Anatomy', 'code': 'PST04101', 'teacher': 'T', 'credits': 3,
                                          'class_level': self.level4.id, 'semester': self.sem1.id,
                                          'programme': self.pst.id}, format='json')
        self.assertEqual(made.status_code, 201, made.data)
        self.assertTrue(Student.objects.filter(profile=self.profile, module_id=made.data['id']).exists())
        self.assertEqual(list(StudentCharge.objects.filter(profile=self.profile).values_list('id', 'amount')),
                         before)

    def test_an_admitted_student_gets_a_password_before_any_module_and_keeps_it(self):
        self.assertIn(self.profile, passwords.students_without_password())
        principal, _ = office('boss', 'principal')
        pin = passwords.reset_student_password(self.profile, actor=principal, first=True)
        portal = Client()
        signed_in = portal.post('/login/', {'identifier': self.profile.nactvet_reg_no, 'secret': pin})
        self.assertEqual(signed_in.get('Location'), '/student-dashboard/')       # the dashboard, modules or not
        # First sign-in: choose their own password, then the dashboard itself.
        changed = portal.post('/api/change-password/', {'current_password': pin, 'new_password': 'Anitha#2026x'},
                              content_type='application/json')
        self.assertEqual(changed.status_code, 200, changed.content)
        page = portal.get('/student-dashboard/')
        self.assertEqual(page.status_code, 200)
        self.assertTemplateUsed(page, 'student_dashboard.html')
        self.assertContains(page, self.profile.name)
        pin = 'Anitha#2026x'
        # The module set up later takes the same password: the dashboard opens.
        module = Module.objects.create(name='Anatomy', code='PST04101', teacher='T', class_level=self.level4,
                                       semester=self.sem1, programme=self.pst, credits=3)
        progression.enroll_module(module)
        # Still signed in: the module shows on the next page, no new sign-in needed.
        self.assertContains(portal.get('/student-dashboard/'), 'PST04101')
        signed_in = Client().post('/login/', {'identifier': self.profile.nactvet_reg_no, 'secret': pin})
        self.assertEqual(signed_in.get('Location'), '/student-dashboard/')
        self.assertNotIn(self.profile, passwords.students_without_password())

    def test_nobody_unadmitted_gets_one(self):
        stranger = StudentProfile.objects.create(nactvet_reg_no='NS0100/0002/2026', name='Not Admitted')
        principal, _ = office('boss', 'principal')
        with self.assertRaises(passwords.PasswordResetError):
            passwords.reset_student_password(stranger, actor=principal)


class OnAccountTests(Accounts):
    def setUp(self):
        super().setUp()
        self.price(self.tuition, '1000000', self.year, self.level4, [date(2026, 11, 30)])
        self.price(self.direct, '500000', self.year, self.level4, [date(2026, 11, 30)])
        self.profile = complete_record(StudentProfile.objects.create(nactvet_reg_no='NS0200/0001/2026',
                                                                     name='Hanna Margwe'))
        finance.generate_charges(self.profile, self.year, class_level=self.level4, programme=self.pst)
        self.assertEqual(finance.balance_for(self.profile, self.year)['billed'], Decimal('1500000.00'))
        self.accountant, _ = office('money', 'accountant')

    def pay(self, amount, account, reference):
        return finance.record_payment(self.profile, Decimal(amount), date(2026, 10, 20),
                                      recorded_by=self.accountant, bank_account=account,
                                      bank_reference=reference)

    def owed(self, charge_type, year):
        return sum((finance.charge_balance(c) for c in finance.with_balances(
            StudentCharge.objects.filter(profile=self.profile, charge_type=charge_type, academic_year=year))),
            Decimal('0'))

    def test_two_years_of_tuition_at_once_leaves_credit_for_the_next(self):
        self.pay('2000000', self.tuition_account, 'CRDB-L5L6')
        self.assertEqual(self.owed(self.tuition, self.year), Decimal('0'))
        self.assertEqual(finance.on_account_total(self.profile), Decimal('1000000.00'))

        # Next year, at the finance desk: the credit pays the tuition only.
        next_year = AcademicYear.objects.create(name='2027/2028')
        next_sem = Semester.objects.create(academic_year=next_year, number=1)
        self.price(self.tuition, '1000000', next_year, self.level5, [date(2027, 11, 30)])
        self.price(self.direct, '500000', next_year, self.level5, [date(2027, 11, 30)])
        application = admissions.open_application(
            profile=self.profile, semester=next_sem, programme=self.pst, class_level=self.level5,
            kind=Application.CONTINUING, actor=self.officer, ignore_window=True)
        self.assertEqual(application.state, Application.FINANCE)
        self.assertEqual(self.owed(self.tuition, next_year), Decimal('0'))
        self.assertEqual(self.owed(self.direct, next_year), Decimal('500000.00'))   # tuition money stays tuition
        self.assertEqual(self.owed(self.direct, self.year), Decimal('500000.00'))
        self.assertEqual(finance.on_account_total(self.profile), Decimal('0.00'))

    def test_the_whole_year_at_the_first_instalment_clears_the_year(self):
        self.pay('1000000', self.tuition_account, 'CRDB-T')
        self.pay('500000', self.other_account, 'CRDB-D')
        self.assertEqual(finance.balance_for(self.profile, self.year)['balance'], Decimal('0.00'))
        self.assertEqual(finance.on_account_total(self.profile), Decimal('0.00'))

    def test_a_reversed_payment_leaves_no_credit(self):
        payment = self.pay('2000000', self.tuition_account, 'CRDB-X')
        finance.reverse_payment(payment, 'wrong student', actor=self.accountant)
        self.assertEqual(finance.on_account_total(self.profile), Decimal('0.00'))

    def test_the_accountant_sees_who_paid_beyond_the_invoice(self):
        invoice = finance.issue_invoice(self.profile, list(StudentCharge.objects.filter(
            profile=self.profile, charge_type=self.tuition)), self.year)
        finance.record_payment(self.profile, Decimal('1600000'), date(2026, 10, 20), recorded_by=self.accountant,
                               invoice=invoice, bank_reference='CRDB-INV')
        _, accounts = office('money3', 'accountant')
        rows = accounts.get('/api/finance/overpayments/').data
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]['invoice'], Decimal(rows[0]['invoice_total']), Decimal(rows[0]['excess'])),
                         (invoice.reference, Decimal('1000000.00'), Decimal('600000.00')))
        _, records = office('rec3', 'records_officer')
        self.assertEqual(records.get('/api/finance/overpayments/').status_code, 403)

    def test_the_statement_shows_it(self):
        self.pay('1500000', self.tuition_account, 'CRDB-Y')
        _, accounts = office('money2', 'accountant')
        statement = accounts.get(f'/api/finance/statement/{self.profile.id}/').data
        self.assertEqual(Decimal(statement['on_account']), Decimal('500000.00'))


class SemesterPassTests(Accounts):
    """Said 21 October 2026: the pass runs window by window, as paid."""

    def setUp(self):
        super().setUp()
        # Two windows: semester 1's instalment and semester 2's.
        self.price(self.tuition, '1000000', self.year, self.level4, [date(2026, 10, 15), date(2027, 3, 1)])
        self.profile = complete_record(StudentProfile.objects.create(
            nactvet_reg_no='NS0300/0001/2026', name='Asha Pass', college_id='BPH/PST/2026/0300'))
        for semester in (self.sem1, self.sem2):
            SemesterRegistration.objects.create(profile=self.profile, semester=semester, programme=self.pst,
                                                class_level=self.level4, kind=SemesterRegistration.NEW)
        progression.set_standing(self.profile, 'active', semester=self.sem1, class_level=self.level4,
                                 programme=self.pst, reason='Admitted.')
        finance.generate_charges(self.profile, self.year, class_level=self.level4)
        self.accountant, _ = office('money', 'accountant')

    def pay(self, amount, reference, on=date(2026, 10, 10)):
        finance.record_payment(self.profile, Decimal(amount), on, recorded_by=self.accountant,
                               bank_account=self.tuition_account, bank_reference=reference)

    def status(self, today):
        return semester_pass.status(self.profile, self.sem1, today=today)

    def test_the_first_window_runs_to_the_day_before_the_second(self):
        self.assertFalse(self.status(date(2026, 10, 20))['ok'])                 # nothing paid
        self.pay('500000', 'CRDB-1')
        first = self.status(date(2026, 11, 1))
        self.assertTrue(first['ok'])
        self.assertEqual(first['valid_until'], date(2027, 2, 28))
        late = self.status(date(2027, 3, 2))
        self.assertFalse(late['ok'])
        self.assertIn('500,000.00', late['reason'])

    def test_every_window_in_parts_runs_a_year_from_the_first(self):
        self.pay('500000', 'CRDB-1')
        self.pay('500000', 'CRDB-2', on=date(2027, 2, 20))
        self.assertEqual(self.status(date(2027, 3, 2))['valid_until'], date(2027, 10, 14))

    def test_the_whole_year_at_once_runs_a_year_from_that_day(self):
        self.pay('1000000', 'CRDB-ALL')
        result = self.status(date(2026, 10, 20))
        self.assertEqual(result['valid_until'], date(2027, 10, 10))
        self.assertIn('at once', result['basis'])

    def test_new_and_continuing_windows_never_mix(self):
        """Each student's windows are their own bill's dates — raised from the
        schedule for their entry — never the other schedule's."""
        continuing = complete_record(StudentProfile.objects.create(
            nactvet_reg_no='NS0300/0009/2025', name='Old Hand'))
        SemesterRegistration.objects.create(profile=continuing, semester=self.sem1, programme=self.pst,
                                            class_level=self.level4, kind=SemesterRegistration.CONTINUING)
        progression.set_standing(continuing, 'active', semester=self.sem1, class_level=self.level4,
                                 programme=self.pst, reason='Continuing.')
        schedule = PaymentSchedule.objects.create(academic_year=self.year, entry=PaymentSchedule.CONTINUING)
        from .models import PaymentStepAmount
        for number, when, amount in ((1, date(2026, 10, 1), '400000'), (2, date(2027, 1, 15), '600000')):
            step = PaymentStep.objects.create(schedule=schedule, number=number, due_date=when,
                                              semester_number=1 if number == 1 else 2)
            PaymentStepAmount.objects.create(step=step, charge_type=self.tuition, amount=Decimal(amount))
        finance.generate_charges(continuing, self.year, class_level=self.level4)
        finance.record_payment(continuing, Decimal('400000'), date(2026, 10, 1), recorded_by=self.accountant,
                               bank_account=self.tuition_account, bank_reference='CRDB-C1')
        self.pay('500000', 'CRDB-N1')
        self.assertEqual(semester_pass.status(continuing, self.sem1, today=date(2026, 11, 1))['valid_until'],
                         date(2027, 1, 14))                                         # the continuing schedule
        self.assertEqual(self.status(date(2026, 11, 1))['valid_until'], date(2027, 2, 28))   # the new one

    def test_an_override_runs_to_its_date(self):
        from .models import FinanceOverride
        FinanceOverride.objects.create(profile=self.profile, academic_year=self.year, period=ChargeType.REGISTRATION,
                                       status=FinanceOverride.CLEARED, reason='Sponsor letter',
                                       expires_on=date(2026, 12, 31), approved_by=self.accountant)
        result = self.status(date(2026, 10, 20))
        self.assertTrue(result['ok'])
        self.assertEqual(result['valid_until'], date(2026, 12, 31))

    def test_the_code_alone_checks_it_and_a_new_payment_gives_a_new_pass(self):
        self.pay('500000', 'CRDB-1')
        first, _ = semester_pass.issue(self.profile, semester=self.sem1, today=date(2026, 11, 1))
        again, _ = semester_pass.issue(self.profile, semester=self.sem1, today=date(2026, 11, 2))
        self.assertEqual(first.pk, again.pk)                                      # the same pass, not a new code
        checked = semester_pass.verify(first.code.lower().replace('-', ''), today=date(2026, 11, 1))
        self.assertTrue(checked['genuine'] and checked['valid'])
        self.assertFalse(semester_pass.verify('AAAA-BBBB')['genuine'])
        self.pay('500000', 'CRDB-2', on=date(2027, 2, 20))
        later, _ = semester_pass.issue(self.profile, semester=self.sem1, today=date(2027, 2, 21))
        self.assertNotEqual(later.code, first.code)
        self.assertEqual(later.valid_until, date(2027, 10, 14))
        old = semester_pass.verify(first.code, today=date(2027, 3, 2))
        self.assertTrue(old['genuine'])
        self.assertFalse(old['valid'])                                            # the old date has passed

    def test_not_registered_not_billed_or_not_studying_gets_none(self):
        stranger = StudentProfile.objects.create(nactvet_reg_no='NS0300/0003/2026', name='Stranger')
        self.assertFalse(semester_pass.status(stranger, self.sem1)['ok'])
        unbilled = StudentProfile.objects.create(nactvet_reg_no='NS0300/0004/2026', name='Unbilled')
        SemesterRegistration.objects.create(profile=unbilled, semester=self.sem1, programme=self.pst,
                                            class_level=self.level4, kind=SemesterRegistration.NEW)
        self.assertIn('No bill', semester_pass.status(unbilled, self.sem1)['reason'])
        self.pay('1000000', 'CRDB-ALL')
        progression.set_standing(self.profile, 'suspended', reason='Discipline.')
        self.assertIn('Suspended', semester_pass.status(self.profile, self.sem1)['reason'])

    def test_the_admission_desk_prints_it_and_records_cannot(self):
        self.pay('1000000', 'CRDB-ALL')
        admission = User.objects.create_user('adm', password='pw')
        set_roles(admission, ['admission_officer'], full_name='Admission')
        desk = Client()
        desk.force_login(admission)
        found = APIClient()
        found.force_login(admission)
        self.assertTrue(found.get('/api/semester-pass/?q=bph/pst/2026/0300').data['ok'])
        page = desk.get(f'/semester-pass/{self.profile.id}/')
        issued = StudentPass.objects.get(profile=self.profile)
        self.assertContains(page, issued.code)
        self.assertNotContains(page, '/semester-pass/check/')                    # no link on the card
        self.assertContains(page, 'Print / save as PDF')                         # the desk prints it
        self.assertNotContains(page, 'id="pass-download"')
        self.assertEqual(issued.issued_by, admission)
        records = User.objects.create_user('rec', password='pw')
        set_roles(records, ['records_officer'], full_name='Records')
        other = Client()
        other.force_login(records)
        self.assertEqual(other.get(f'/semester-pass/{self.profile.id}/').status_code, 403)

    def test_the_student_downloads_their_own(self):
        module = Module.objects.create(name='Anatomy', code='PST04101', teacher='T', class_level=self.level4,
                                       semester=self.sem1, programme=self.pst, credits=3)
        enrollment = Student.objects.create(nactvet_reg_no=self.profile.nactvet_reg_no, name=self.profile.name,
                                            profile=self.profile, module=module)
        enrollment.set_portal_pin('Portal#2026', require_change=False)
        enrollment.save()
        portal = Client()
        session = portal.session
        session['student_id'] = enrollment.id
        session.save()
        self.assertContains(portal.get('/my-semester-pass/'), 'No student pass')
        dashboard = portal.get('/student-dashboard/')
        self.assertNotContains(dashboard, 'id="pass-ready"')                     # not paid up: no offer
        self.assertContains(dashboard, 'id="password-expiry"')                   # always told when it expires
        self.pay('1000000', 'CRDB-ALL')
        self.assertContains(portal.get('/student-dashboard/'), 'id="pass-ready"')
        page = portal.get('/my-semester-pass/')
        self.assertContains(page, StudentPass.objects.get(profile=self.profile, source=StudentPass.PORTAL).code)
        # Said 28 October 2026: students use phones — the pass is downloaded as a picture, not printed.
        self.assertContains(page, 'id="pass-download"')
        self.assertNotContains(page, 'Print / save as PDF')
        data = json.loads(re.search(r'<script id="pass-data" type="application/json">(.*?)</script>',
                                    page.content.decode(), re.S).group(1))
        self.assertEqual((data['reg_no'], data['college_id']), (self.profile.nactvet_reg_no, self.profile.college_id))

    def test_checking_a_pass_is_for_staff_by_code_alone(self):
        self.pay('1000000', 'CRDB-ALL')
        issued, _ = semester_pass.issue(self.profile)
        url = f'/semester-pass/check/?code={issued.code}'
        self.assertEqual(Client().get(url).status_code, 302)                      # sign in first
        tutor = User.objects.create_user('tutor', password='pw')
        set_roles(tutor, ['tutor'], full_name='Tutor')
        staff = Client()
        staff.force_login(tutor)
        self.assertContains(staff.get(url), 'Genuine')
        self.assertContains(staff.get('/semester-pass/check/?code=AAAA-BBBB'), 'Not genuine')


class WaiverDesk(Accounts):
    """A student at the finance desk, owing a year's tuition in two parts."""

    def setUp(self):
        super().setUp()
        self.price(self.tuition, '1000000', self.year, self.level4, [date(2026, 10, 15), date(2027, 3, 1)])
        self.profile = self.applicant('NACTVET/PENDING/900', 'Short Payer')
        self.application = admissions.open_application(profile=self.profile, semester=self.sem1, programme=self.pst,
                                                       class_level=self.level4, kind=Application.NEW,
                                                       actor=self.officer)
        while self.application.state != Application.FINANCE:
            admissions.complete_step(self.application, actor=self.officer)
        admissions.set_residence(self.application, 'day', actor=self.officer)
        self.charge = StudentCharge.objects.filter(profile=self.profile, charge_type=self.tuition).order_by('due_date').first()
        self.accountant, self.accounts = office('money', 'accountant')
        self.dpfpa_user, self.dpfpa = office('dpf', 'dpfpa')
        _, self.principal = office('boss2', 'principal')


class ShortfallWaiverTests(WaiverDesk):
    """Said 21 October 2026: a shortfall waived on tuition or direct costs
    waits for DPFPA; the student waits at the finance desk meanwhile; other
    waivers are as they were. (These ask by one charge, as the first requests
    did; the API still takes them.)"""

    def ask(self, amount='20000', reason='20,000 short on the first instalment'):
        return self.accounts.post(f'/api/student-charges/{self.charge.id}/waive/',
                                  {'amount': amount, 'reason': reason}, format='json')

    def test_it_waits_for_dpfpa_and_changes_nothing_meanwhile(self):
        from .models import Notification, WaiverRequest
        asked = self.ask()
        self.assertEqual(asked.status_code, 202, asked.data)
        self.charge.refresh_from_db()
        self.assertEqual(self.charge.waived_amount, Decimal('0'))
        self.assertEqual(WaiverRequest.objects.get().status, WaiverRequest.PENDING)
        self.assertTrue(Notification.objects.filter(user=self.dpfpa_user, kind=Notification.WAIVER_REQUESTED).exists())
        with self.assertRaises(admissions.AdmissionError) as held:
            admissions.complete_step(self.application, actor=self.officer)
        self.assertIn('DPFPA', str(held.exception))
        self.assertEqual(self.ask('30000').status_code, 400)                    # one at a time on a charge

    def test_dpfpa_approves_and_it_is_applied(self):
        from .models import WaiverRequest
        self.ask()
        waiver = WaiverRequest.objects.get()
        decided = self.dpfpa.post(f'/api/waiver-requests/{waiver.id}/decide/', {'approve': True}, format='json')
        self.assertEqual(decided.status_code, 200, decided.data)
        self.charge.refresh_from_db()
        self.assertEqual(self.charge.waived_amount, Decimal('20000.00'))
        self.assertIn('approved by', self.charge.waived_reason)
        self.assertFalse(finance.pending_waivers(self.profile).exists())

    def test_a_refusal_needs_a_reason_and_leaves_the_bill(self):
        from .models import WaiverRequest
        self.ask()
        waiver = WaiverRequest.objects.get()
        self.assertEqual(self.dpfpa.post(f'/api/waiver-requests/{waiver.id}/decide/', {'approve': False},
                                         format='json').status_code, 400)
        refused = self.dpfpa.post(f'/api/waiver-requests/{waiver.id}/decide/',
                                  {'approve': False, 'note': 'Pay the balance first'}, format='json')
        self.assertEqual(refused.data['status'], WaiverRequest.REFUSED)
        self.charge.refresh_from_db()
        self.assertEqual(self.charge.waived_amount, Decimal('0'))

    def test_only_dpfpa_decides(self):
        from .models import WaiverRequest
        self.ask()
        waiver = WaiverRequest.objects.get()
        for client in (self.accounts, self.principal):
            self.assertEqual(client.post(f'/api/waiver-requests/{waiver.id}/decide/', {'approve': True},
                                         format='json').status_code, 403)
        self.assertEqual(len(self.principal.get('/api/waiver-requests/?status=pending').data), 1)   # can see

    def test_other_charges_are_waived_as_before(self):
        exam = ChargeType.objects.create(name='Supplementary exam', family=ChargeType.OTHER,
                                         applies=ChargeType.ON_REQUEST)
        other = finance.raise_charge(self.profile, exam, self.year, Decimal('50000'), date(2026, 11, 1))
        done = self.accounts.post(f'/api/student-charges/{other.id}/waive/', {'amount': '50000', 'reason': 'Sick'},
                                  format='json')
        self.assertEqual(done.status_code, 200, done.data)
        other.refresh_from_db()
        self.assertEqual(other.waived_amount, Decimal('50000.00'))


class InstalmentWaiverTests(WaiverDesk):
    """Said 22 October 2026: the accountant chooses tuition fee or direct
    costs, which instalment is short, writes what is short, and sends it to
    DPFPA. DPFPA's yes takes it off that instalment's charges."""

    def ask_for(self, family='fee', instalment=1, amount='20000', reason='Sponsor pays the rest in March'):
        return self.accounts.post('/api/waiver-requests/', {
            'profile_id': self.profile.id, 'academic_year_id': self.year.id, 'family': family,
            'installment_number': instalment, 'amount': amount, 'reason': reason}, format='json')

    def test_the_desk_chooses_from_the_instalments_owed_on(self):
        rows = self.accounts.get(f'/api/waiver-requests/shortfalls/?profile_id={self.profile.id}').data
        self.assertEqual([(r['family'], r['family_display'], r['installment_number']) for r in rows],
                         [('fee', 'Tuition fee', 1), ('fee', 'Tuition fee', 2)])
        self.assertEqual(sum(Decimal(r['owed']) for r in rows), Decimal('1000000.00'))
        self.assertEqual(self.dpfpa.get(f'/api/waiver-requests/shortfalls/?profile_id={self.profile.id}').status_code,
                         403)

    def test_it_waits_then_comes_off_the_instalment(self):
        from .models import WaiverRequest
        asked = self.ask_for()
        self.assertEqual(asked.status_code, 201, asked.data)
        self.assertEqual(asked.data['waiver_request']['charge'], 'Tuition fee · instalment 1')
        self.assertEqual(asked.data['waiver_request']['shortfall'], '20000.00')
        self.charge.refresh_from_db()
        self.assertEqual(self.charge.waived_amount, Decimal('0'))
        with self.assertRaises(admissions.AdmissionError):
            admissions.complete_step(self.application, actor=self.officer)
        self.assertEqual(self.ask_for(amount='5000').status_code, 400)          # one at a time
        waiver = WaiverRequest.objects.get()
        decided = self.dpfpa.post(f'/api/waiver-requests/{waiver.id}/decide/', {'approve': True}, format='json')
        self.assertEqual(decided.status_code, 200, decided.data)
        self.charge.refresh_from_db()
        self.assertEqual(self.charge.waived_amount, Decimal('20000.00'))
        second = StudentCharge.objects.get(profile=self.profile, charge_type=self.tuition, installment_number=2)
        self.assertEqual(second.waived_amount, Decimal('0'))

    def test_what_is_short_spreads_over_the_instalments_charges(self):
        from .models import WaiverRequest
        uniform = ChargeType.objects.create(name='Uniform', family=ChargeType.DIRECT_COST,
                                            applies=ChargeType.AUTOMATIC)
        due = date(2026, 10, 15)
        first = finance.raise_charge(self.profile, self.direct, self.year, Decimal('30000'), due,
                                     source=StudentCharge.STRUCTURE)
        then = finance.raise_charge(self.profile, uniform, self.year, Decimal('40000'), due,
                                    source=StudentCharge.STRUCTURE)
        self.assertEqual(self.ask_for('direct_cost', 1, '50000').status_code, 201)
        waiver = WaiverRequest.objects.get()
        self.dpfpa.post(f'/api/waiver-requests/{waiver.id}/decide/', {'approve': True}, format='json')
        first.refresh_from_db()
        then.refresh_from_db()
        self.assertEqual((first.waived_amount, then.waived_amount), (Decimal('30000.00'), Decimal('20000.00')))

    def test_what_the_desk_may_not_ask(self):
        self.assertEqual(self.ask_for(amount='999999999').status_code, 400)      # more than is owed
        self.assertEqual(self.ask_for(amount='0').status_code, 400)
        self.assertEqual(self.ask_for(reason=' ').status_code, 400)
        self.assertEqual(self.ask_for(family='other').status_code, 400)
        self.assertEqual(self.ask_for(instalment=7).status_code, 400)           # no such instalment
        self.assertEqual(self.dpfpa.post('/api/waiver-requests/', {}, format='json').status_code, 403)

    def test_paid_meanwhile_cannot_be_approved(self):
        from .models import WaiverRequest
        self.ask_for()
        finance.waive_charge(self.charge, self.charge.amount, 'Bursary', actor=None)
        waiver = WaiverRequest.objects.get()
        refused = self.dpfpa.post(f'/api/waiver-requests/{waiver.id}/decide/', {'approve': True}, format='json')
        self.assertEqual(refused.status_code, 400)
        self.assertIn('Nothing is owed', refused.data['detail'])
