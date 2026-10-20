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

from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import Client
from rest_framework.test import APIClient

from . import admissions, finance, passwords, progression, semester_pass
from .models import (
    AcademicYear, Application, BankAccount, ChargeType, ClassLevel, FeeStructure, Module, PaymentSchedule,
    PaymentStep, Semester, SemesterRegistration, Student, StudentCharge, StudentProfile,
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
        signed_in = Client().post('/login/', {'identifier': self.profile.nactvet_reg_no, 'secret': pin})
        self.assertEqual(signed_in.get('Location'), '/course-portal/')
        # The module set up later takes the same password: the dashboard opens.
        module = Module.objects.create(name='Anatomy', code='PST04101', teacher='T', class_level=self.level4,
                                       semester=self.sem1, programme=self.pst, credits=3)
        progression.enroll_module(module)
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

    def test_the_statement_shows_it(self):
        self.pay('1500000', self.tuition_account, 'CRDB-Y')
        _, accounts = office('money2', 'accountant')
        statement = accounts.get(f'/api/finance/statement/{self.profile.id}/').data
        self.assertEqual(Decimal(statement['on_account']), Decimal('500000.00'))


class SemesterPassTests(Accounts):
    def setUp(self):
        super().setUp()
        # Two instalments: semester 1's and semester 2's.
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
        Semester.objects.filter(pk=self.sem1.pk).update(is_active=False)
        Semester.objects.filter(pk=self.sem2.pk).update(is_active=True)

    def pay(self, amount, reference):
        finance.record_payment(self.profile, Decimal(amount), date(2026, 10, 10), recorded_by=self.accountant,
                               bank_account=self.tuition_account, bank_reference=reference)

    def test_semester_two_needs_the_second_instalment(self):
        self.pay('500000', 'CRDB-1')
        when = date(2027, 3, 2)
        refused = semester_pass.status(self.profile, self.sem2, today=when)
        self.assertFalse(refused['ok'])
        self.assertIn('500000', refused['reason'].replace(',', ''))
        self.pay('500000', 'CRDB-2')
        granted = semester_pass.status(self.profile, self.sem2, today=when)
        self.assertTrue(granted['ok'])
        self.assertEqual(granted['code'], semester_pass.check_code(self.profile, self.sem2))

    def test_the_schedules_semester_two_date_is_the_yardstick_even_before_it(self):
        schedule = PaymentSchedule.objects.create(academic_year=self.year, entry=PaymentSchedule.NEW)
        PaymentStep.objects.create(schedule=schedule, number=1, due_date=date(2026, 10, 15), semester_number=1)
        PaymentStep.objects.create(schedule=schedule, number=2, due_date=date(2027, 3, 1), semester_number=2)
        self.pay('500000', 'CRDB-1')
        early = semester_pass.status(self.profile, self.sem2, today=date(2027, 2, 20))
        self.assertFalse(early['ok'])                          # semester 2's instalment is asked for now
        self.assertEqual(early['due_by'], date(2027, 3, 1))

    def test_an_edited_pass_fails_the_check(self):
        self.pay('1000000', 'CRDB-ALL')
        code = semester_pass.check_code(self.profile, self.sem2)
        self.assertTrue(semester_pass.verify('BPH/PST/2026/0300', code)['genuine'])
        other = StudentProfile.objects.create(nactvet_reg_no='NS0300/0002/2026', name='Forger',
                                              college_id='BPH/PST/2026/0301')
        self.assertFalse(semester_pass.verify('BPH/PST/2026/0301', code)['genuine'])   # someone else's code
        self.assertFalse(semester_pass.verify('BPH/PST/2026/0300', 'AAAAA-BBBBB')['genuine'])
        self.assertNotEqual(semester_pass.check_code(self.profile, self.sem1), code)    # another semester

    def test_not_registered_not_billed_or_not_studying_gets_none(self):
        stranger = StudentProfile.objects.create(nactvet_reg_no='NS0300/0003/2026', name='Stranger')
        self.assertFalse(semester_pass.status(stranger, self.sem2)['ok'])
        # Registered but never billed: owing nothing is not having paid.
        unbilled = StudentProfile.objects.create(nactvet_reg_no='NS0300/0004/2026', name='Unbilled')
        SemesterRegistration.objects.create(profile=unbilled, semester=self.sem2, programme=self.pst,
                                            class_level=self.level4, kind=SemesterRegistration.NEW)
        refused = semester_pass.status(unbilled, self.sem2)
        self.assertFalse(refused['ok'])
        self.assertIn('No bill', refused['reason'])
        self.pay('1000000', 'CRDB-ALL')
        progression.set_standing(self.profile, 'suspended', reason='Discipline.')
        self.assertIn('Suspended', semester_pass.status(self.profile, self.sem2)['reason'])

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
        self.assertContains(page, semester_pass.check_code(self.profile, self.sem2))
        records = User.objects.create_user('rec', password='pw')
        set_roles(records, ['records_officer'], full_name='Records')
        other = Client()
        other.force_login(records)
        self.assertEqual(other.get(f'/semester-pass/{self.profile.id}/').status_code, 403)

    def test_the_student_downloads_their_own(self):
        module = Module.objects.create(name='Dispensing', code='PST04201', teacher='T', class_level=self.level4,
                                       semester=self.sem2, programme=self.pst, credits=3)
        enrollment = Student.objects.create(nactvet_reg_no=self.profile.nactvet_reg_no, name=self.profile.name,
                                            profile=self.profile, module=module)
        portal = Client()
        session = portal.session
        session['student_id'] = enrollment.id
        session.save()
        self.assertContains(portal.get('/my-semester-pass/'), 'No semester pass')
        self.pay('1000000', 'CRDB-ALL')
        self.assertContains(portal.get('/my-semester-pass/'), semester_pass.check_code(self.profile, self.sem2))

    def test_checking_a_pass_is_for_staff(self):
        self.pay('1000000', 'CRDB-ALL')
        code = semester_pass.check_code(self.profile, self.sem2)
        url = f'/semester-pass/check/?id=BPH/PST/2026/0300&code={code}'
        self.assertEqual(Client().get(url).status_code, 302)                      # sign in first
        tutor = User.objects.create_user('tutor', password='pw')
        set_roles(tutor, ['tutor'], full_name='Tutor')
        staff = Client()
        staff.force_login(tutor)
        self.assertContains(staff.get(url), 'Genuine')
        self.assertContains(staff.get(url.replace(code, 'AAAAA-BBBBB')), 'Not genuine')
