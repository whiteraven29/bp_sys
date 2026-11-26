"""Said 27 November 2026: the TPH book is a separate thing. Billing it records
that the student has no book of their own, so the college buys one and the
student pays for it apart: it is no part of what they owe in total."""

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from . import finance
from .models import AcademicYear, AdmissionRequirement, ChargeType, StudentProfile

User = get_user_model()


class TphApartTests(TestCase):
    def setUp(self):
        self.year = AcademicYear.objects.create(name='2026/2027', is_active=True)
        self.student = StudentProfile.objects.create(nactvet_reg_no='NS0001/0001/2026', name='Asha Book')
        self.clerk = User.objects.create_superuser('acc', 'a@b.c', 'pw')
        fees = ChargeType.objects.create(name='Tuition fee', family=ChargeType.FEE)
        tph = ChargeType.objects.create(name='TPH book', family=ChargeType.OTHER, applies=ChargeType.ON_REQUEST)
        AdmissionRequirement.objects.create(name='TPH book', charge_type=tph)
        calculator = ChargeType.objects.create(name='Calculator', family=ChargeType.OTHER, applies=ChargeType.ON_REQUEST)
        AdmissionRequirement.objects.create(name='Calculator', charge_type=calculator)
        finance.raise_charge(self.student, fees, self.year, Decimal('100000'), date(2026, 12, 1))
        finance.raise_charge(self.student, tph, self.year, Decimal('20000'), date(2026, 12, 1))
        finance.raise_charge(self.student, calculator, self.year, Decimal('5000'), date(2026, 12, 1))

    def test_the_tph_book_is_no_part_of_the_total_owing(self):
        totals = finance.balance_for(self.student, self.year)
        self.assertEqual((totals['billed'], totals['balance']), (Decimal('105000.00'), Decimal('105000.00')))
        self.assertEqual(finance.apart_balance(self.student, self.year)['balance'], Decimal('20000.00'))
        self.assertEqual(finance.balance_map([self.student], self.year)[self.student.id]['balance'],
                         Decimal('105000.00'))
        self.assertEqual(finance.balances_for([(self.student.id, self.year.id)])[(self.student.id, self.year.id)],
                         Decimal('105000.00'))
        self.assertEqual(finance.executive_summary(self.year)['totals']['outstanding'], '105000.00')

    def test_the_statement_lists_it_apart(self):
        statement = finance.full_statement(self.student)
        [year] = statement['years']
        self.assertEqual(year['billed'], Decimal('105000.00'))
        self.assertEqual([c.charge_type.name for c in year['charges'] if c.apart], ['TPH book'])
        self.assertEqual(statement['apart']['billed'], Decimal('20000.00'))
