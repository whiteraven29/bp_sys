"""Two things the first admission season brings (said 12 October 2026):

Results for repeaters
  * the authority sheet lists every module, but a repeater sits only the ones
    they failed: a grade for a module they are not sitting this semester is
    reported as "not sitting", not an error, and their earlier result stands;
  * NA is "not applicable" and records nothing for a module not sat;
  * a registration number nobody sits anything under is still an error.

Money paid during the long holiday, without an invoice
  * the accountant says which CRDB account the slip was paid into, and the
    payment settles only that account's charges;
  * the same bank reference twice is refused unless the accountant confirms
    the slip pays for both; a reversed payment's reference may be used again.
"""

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from . import finance
from .models import (
    AcademicYear, BankAccount, ChargeType, ClassLevel, Department, Module, PaymentAllocation,
    Programme, Semester, Student, StudentCharge, StudentProfile, StudentResult,
)
from .views import set_roles

User = get_user_model()


def office(username, *roles):
    user = User.objects.create_user(username, password='pw')
    set_roles(user, list(roles), full_name=username.title())
    client = APIClient()
    client.force_authenticate(user)
    return user, client


class RepeaterUploadTests(TestCase):
    def setUp(self):
        year = AcademicYear.objects.create(name='2026/2027', is_active=True)
        self.sem = Semester.objects.create(academic_year=year, number=1, is_active=True)
        level = ClassLevel.objects.create(name='NTA Level 4', order=4)
        pst = Programme.objects.create(department=Department.objects.create(name='PST', code='PST'),
                                       name='PST', code='PST')
        self.m1, self.m2 = [Module.objects.create(name=c, code=c, teacher='T', class_level=level,
                                                  semester=self.sem, programme=pst, credits=4)
                            for c in ('PST04101', 'PST04102')]
        profile = StudentProfile.objects.create(nactvet_reg_no='NS0001/0001/2023', name='Repeater')
        self.sitting = Student.objects.create(nactvet_reg_no=profile.nactvet_reg_no, name=profile.name,
                                              profile=profile, module=self.m1, attempt=Student.REPEAT)
        _, self.exam = office('exam', 'exam_officer')

    def upload(self, rows):
        return self.exam.post('/api/results/authority-grades/', {
            'semester_id': self.sem.id,
            'rows': [{'reg_no': r, 'module_code': m, 'grade': g} for r, m, g in rows]}, format='json').data

    def test_a_module_not_sat_is_reported_not_refused(self):
        result = self.upload([('NS0001/0001/2023', 'PST04101', 'C'),
                              ('NS0001/0001/2023', 'PST04102', 'B')])     # passed before; not sat now
        self.assertEqual(result['saved'], 1)
        self.assertEqual(result['errors'], [])
        # A repeater's module not repeated is last time's grade, re-listed: carried,
        # not reported as missing (said 25 October 2026).
        self.assertEqual((result['carried'], result['not_sitting']), (1, []))
        self.assertEqual(StudentResult.objects.get(student=self.sitting).authority_grade, 'C')

    def test_na_records_nothing(self):
        result = self.upload([('NS0001/0001/2023', 'PST04102', 'NA')])
        self.assertEqual((result['saved'], result['errors'], result['not_sitting']), (0, [], []))

    def test_nobody_by_that_number_is_still_an_error(self):
        result = self.upload([('NS9999/0009/2023', 'PST04101', 'B')])
        self.assertIn('not enrolled', result['errors'][0])

    def test_the_class_list_says_which_sittings_are_repeats(self):
        listed = self.exam.get(f'/api/students/?module_id={self.m1.id}').data
        rows = listed.get('results', listed) if isinstance(listed, dict) else listed
        self.assertEqual(rows[0]['attempt'], Student.REPEAT)


class HolidayPaymentTests(TestCase):
    def setUp(self):
        self.year = AcademicYear.objects.create(name='2026/2027', is_active=True)
        self.profile = StudentProfile.objects.create(nactvet_reg_no='NS0002/0002/2023', name='Paid Early')
        self.tuition_account = BankAccount.objects.create(bank_name='CRDB', account_name='BPHACOH',
                                                          account_number='0150417961300', purpose='Tuition')
        self.other_account = BankAccount.objects.create(bank_name='CRDB', account_name='BPHACOH',
                                                        account_number='0150417961301', purpose='Other')
        tuition = ChargeType.objects.create(name='Tuition Fee', family=ChargeType.FEE,
                                            bank_account=self.tuition_account)
        caution = ChargeType.objects.create(name='Caution money', family=ChargeType.DIRECT_COST,
                                            bank_account=self.other_account)
        # The oldest charge is in the other account: "oldest first" would take it.
        self.caution = StudentCharge.objects.create(
            profile=self.profile, charge_type=caution, academic_year=self.year, installment_number=1,
            amount=Decimal('50000'), due_date=date(2026, 10, 1))
        self.tuition = StudentCharge.objects.create(
            profile=self.profile, charge_type=tuition, academic_year=self.year, installment_number=1,
            amount=Decimal('400000'), due_date=date(2026, 10, 19))
        _, self.accounts = office('money', 'accountant')

    def pay(self, amount='400000', **extra):
        return self.accounts.post('/api/payments/record/', {
            'profile': self.profile.id, 'amount': amount, 'payment_date': '2026-09-02',
            'bank_reference': 'CRDB-778812', **extra}, format='json')

    def allocated(self, charge):
        return sum(a.amount for a in PaymentAllocation.objects.filter(charge=charge))

    def test_a_slip_into_the_tuition_account_settles_tuition_only(self):
        response = self.pay(bank_account=self.tuition_account.id)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(self.allocated(self.tuition), Decimal('400000.00'))
        self.assertEqual(self.allocated(self.caution), 0)

    def test_without_saying_it_goes_to_the_oldest_charge(self):
        self.pay()
        self.assertEqual(self.allocated(self.caution), Decimal('50000.00'))      # why the account matters

    def test_the_same_slip_twice_is_refused_unless_confirmed(self):
        self.assertEqual(self.pay('200000', bank_account=self.tuition_account.id).status_code, 201)
        again = self.pay('200000', bank_account=self.tuition_account.id)
        self.assertEqual(again.status_code, 409)
        self.assertIn('already recorded', again.data['detail'])
        self.assertTrue(again.data['same_reference'])
        confirmed = self.pay('200000', bank_account=self.tuition_account.id, allow_same_reference=True)
        self.assertEqual(confirmed.status_code, 201)

    def test_a_reversed_payments_slip_can_be_recorded_again(self):
        first = self.pay(bank_account=self.tuition_account.id).data
        payment = finance.Payment.objects.get(id=first['id'])
        user = User.objects.get(username='money')
        finance.reverse_payment(payment, 'Wrong student', actor=user)
        self.assertEqual(self.pay(bank_account=self.tuition_account.id).status_code, 201)
