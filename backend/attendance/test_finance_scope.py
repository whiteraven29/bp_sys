"""Income kept apart by department and programme (said 19 October 2026): the
accountant's debtors list, collections and the executive summary filter by
department and programme, and say how much each brought in. A VETA course
student, who has no standing, counts under their intake's programme.
"""

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from . import finance
from .models import (
    AcademicYear, ChargeType, ClassLevel, CourseApplication, Department, Intake, Programme, Semester,
    SemesterRegistration, StudentProfile,
)
from .views import set_roles

User = get_user_model()


class FinanceScopeTests(TestCase):
    def setUp(self):
        self.year = AcademicYear.objects.create(name='2026/2027', is_active=True)
        self.sem = Semester.objects.create(academic_year=self.year, number=1, is_active=True)
        level = ClassLevel.objects.create(name='NTA Level 4', order=4)
        self.pst_dept = Department.objects.create(name='Pharmaceutical Sciences', code='PST')
        self.vet_dept = Department.objects.create(name='Vocational', code='VET')
        self.pst = Programme.objects.create(department=self.pst_dept, name='Pharm', code='PST')
        self.hair = Programme.objects.create(department=self.vet_dept, name='Hair dressing', code='HD',
                                             structure=Programme.SHORT, duration_months=3)
        self.accountant = User.objects.create_user('money', password='Strong#2026x')
        set_roles(self.accountant, ['accountant'], full_name='Money')
        self.api = APIClient()
        self.api.force_authenticate(self.accountant)
        fee = ChargeType.objects.create(name='Fee', family=ChargeType.FEE, applies=ChargeType.ON_REQUEST)

        self.asha = StudentProfile.objects.create(nactvet_reg_no='NS0001/0001/2026', name='Asha')
        SemesterRegistration.objects.create(profile=self.asha, semester=self.sem, programme=self.pst,
                                            class_level=level, kind=SemesterRegistration.NEW)
        self.neema = StudentProfile.objects.create(nactvet_reg_no='VETA/0001', name='Neema')
        intake = Intake.objects.create(programme=self.hair, academic_year=self.year, name='Oct 2026',
                                       starts_on=date(2026, 10, 19), ends_on=date(2027, 1, 19))
        CourseApplication.objects.create(profile=self.neema, intake=intake, state=CourseApplication.ADMITTED)
        for profile, amount in ((self.asha, '1000000'), (self.neema, '300000')):
            finance.raise_charge(profile, fee, self.year, Decimal(amount), date(2026, 11, 30),
                                 semester=self.sem, actor=self.accountant)
            finance.record_payment(profile, Decimal(amount) / 2, date.today(), recorded_by=self.accountant,
                                   bank_reference=f'REF-{profile.id}')

    def test_the_debtors_list_filters_by_department_and_programme(self):
        everyone = self.api.get('/api/finance/students/').data
        self.assertEqual({r['programme_code'] for r in everyone['rows']} >= {'PST', 'HD'}, True)
        vet = self.api.get(f'/api/finance/students/?department_id={self.vet_dept.id}').data
        self.assertEqual([r['name'] for r in vet['rows']], ['Neema'])
        self.assertEqual(Decimal(vet['totals']['billed']), Decimal('300000'))
        pst = self.api.get(f'/api/finance/students/?programme_id={self.pst.id}').data
        self.assertEqual([r['name'] for r in pst['rows']], ['Asha'])

    def test_collections_say_what_each_department_brought_in(self):
        data = self.api.get('/api/finance/collections/').data
        self.assertEqual(Decimal(data['by_department']['Pharmaceutical Sciences']), Decimal('500000'))
        self.assertEqual(Decimal(data['by_department']['Vocational']), Decimal('150000'))
        self.assertEqual(Decimal(data['by_programme']['HD']), Decimal('150000'))
        only_vet = self.api.get(f'/api/finance/collections/?department_id={self.vet_dept.id}').data
        self.assertEqual(Decimal(only_vet['total']), Decimal('150000'))

    def test_the_executive_summary_by_department(self):
        data = self.api.get('/api/executive/finance/').data
        departments = {row['department']: row for row in data['by_department']}
        self.assertEqual(Decimal(departments['Vocational']['paid']), Decimal('150000'))
        programme_only = self.api.get(f'/api/executive/finance/?programme_id={self.hair.id}').data
        self.assertEqual(Decimal(programme_only['totals']['billed']), Decimal('300000'))
