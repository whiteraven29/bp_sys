"""Departments, programmes and semester registrations — and what they change
about billing.

Fees differ by programme, the level a student is billed at comes from their
registration, a readmitted student is billed afresh, and an exam declaration is
charged per module at the accountant's rate.
"""

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase

from . import finance
from .models import (
    AcademicYear, ChargeType, ClassLevel, Department, FeeStructure, Module, NextOfKin,
    Programme, SemesterRegistration, Semester, Student, StudentCharge, StudentProfile,
)

User = get_user_model()
TZS = Decimal


class ProgrammeBillingBase(TestCase):
    def setUp(self):
        self.year = AcademicYear.objects.create(name='2026/2027', is_active=True)
        self.sem1 = Semester.objects.create(academic_year=self.year, number=1, is_active=True)
        self.sem2 = Semester.objects.create(academic_year=self.year, number=2)
        self.level4 = ClassLevel.objects.create(name='NTA Level 4', order=4)
        self.level5 = ClassLevel.objects.create(name='NTA Level 5', order=5)

        self.department = Department.objects.create(name='Pharmaceutical Sciences', code='PST')
        self.pst = Programme.objects.create(department=self.department, name='Pharmaceutical Sciences', code='PST')
        self.other = Programme.objects.create(department=self.department, name='Other Programme', code='OTH')

        self.accountant = User.objects.create_user('accountant', password='pw')
        self.tuition = ChargeType.objects.create(name='Tuition Fee', family=ChargeType.FEE)

    def module(self, code, level, semester=None, programme=None):
        return Module.objects.create(
            name=code, code=code, teacher='T', class_level=level,
            semester=semester or self.sem1, programme=programme,
        )

    def fee(self, charge_type, level, amount, *, programme=None, due=date(2026, 10, 30),
            period=FeeStructure.ACADEMIC_YEAR):
        structure = FeeStructure.objects.create(
            charge_type=charge_type, programme=programme, class_level=level,
            academic_year=self.year, amount=TZS(amount), billing_period=period, installments=1,
        )
        finance.set_installment_schedule(structure, [due])
        return structure

    def enrol(self, reg_no, *modules):
        rows = [Student.objects.create(nactvet_reg_no=reg_no, name='Asha Juma', module=m) for m in modules]
        return finance.profile_for_student(rows[0]), rows


class LevelLookupTests(ProgrammeBillingBase):
    def test_a_level_5_student_repeating_a_level_4_module_is_billed_at_level_5(self):
        # The level 4 enrollment is created first, so it is what the old lookup
        # — enrollments in their default order, which is by name — returned.
        repeat = self.module('PST04101', self.level4, programme=self.pst)
        current = self.module('PST05101', self.level5, programme=self.pst)
        profile, _ = self.enrol('REG/001', repeat, current)

        self.assertEqual(finance.class_level_for(profile, self.year), self.level5)

    def test_the_latest_semester_decides_the_level(self):
        last_sem = self.module('PST04101', self.level4, semester=self.sem1, programme=self.pst)
        this_sem = self.module('PST05201', self.level5, semester=self.sem2, programme=self.pst)
        profile, _ = self.enrol('REG/001', last_sem, this_sem)

        self.assertEqual(finance.class_level_for(profile, self.year), self.level5)

    def test_a_registration_overrides_what_the_enrollments_suggest(self):
        module = self.module('PST05101', self.level5, programme=self.pst)
        profile, _ = self.enrol('REG/001', module)
        SemesterRegistration.objects.create(
            profile=profile, semester=self.sem1, programme=self.other,
            class_level=self.level4, kind=SemesterRegistration.CONTINUING,
        )

        self.assertEqual(finance.class_level_for(profile, self.year), self.level4)
        self.assertEqual(finance.programme_for(profile, self.year), self.other)

    def test_the_programme_comes_from_the_modules_without_a_registration(self):
        profile, _ = self.enrol('REG/001', self.module('PST04101', self.level4, programme=self.pst))

        self.assertEqual(finance.programme_for(profile, self.year), self.pst)

    def test_enrolling_a_registered_student_in_a_lower_level_module_bills_their_own_level(self):
        # Billing happens automatically when an enrollment is created. It used
        # to bill at the module's level, so a level 5 student's first enrollment
        # being a level 4 repeat charged them level 4 fees for the year.
        self.fee(self.tuition, self.level4, '1000000', programme=self.pst)
        self.fee(self.tuition, self.level5, '1200000', programme=self.pst)
        profile = StudentProfile.objects.create(nactvet_reg_no='REG/009', name='Asha Juma')
        SemesterRegistration.objects.create(profile=profile, semester=self.sem1, programme=self.pst,
                                            class_level=self.level5, kind=SemesterRegistration.CONTINUING)

        Student.objects.create(nactvet_reg_no='REG/009', name='Asha Juma',
                               module=self.module('PST04101', self.level4, programme=self.pst))

        charges = StudentCharge.objects.filter(profile=profile, charge_type=self.tuition)
        self.assertEqual([c.amount for c in charges], [TZS('1200000.00')])


class ProgrammeFeeTests(ProgrammeBillingBase):
    def charged(self, profile):
        return {c.charge_type_id: c.amount for c in StudentCharge.objects.filter(profile=profile)}

    def test_a_programme_own_fee_wins_over_the_every_programme_amount(self):
        self.fee(self.tuition, self.level4, '1000000')
        self.fee(self.tuition, self.level4, '1600000', programme=self.pst)
        profile, _ = self.enrol('REG/001', self.module('PST04101', self.level4, programme=self.pst))

        finance.generate_charges(profile, self.year, actor=self.accountant)

        self.assertEqual(self.charged(profile), {self.tuition.id: TZS('1600000.00')})

    def test_a_programme_without_its_own_fee_pays_the_every_programme_amount(self):
        self.fee(self.tuition, self.level4, '1000000')
        self.fee(self.tuition, self.level4, '1600000', programme=self.pst)
        profile, _ = self.enrol('REG/002', self.module('OTH04101', self.level4, programme=self.other))

        finance.generate_charges(profile, self.year, actor=self.accountant)

        self.assertEqual(self.charged(profile), {self.tuition.id: TZS('1000000.00')})

    def test_a_student_not_yet_linked_to_a_programme_pays_the_every_programme_amount(self):
        self.fee(self.tuition, self.level4, '1000000')
        self.fee(self.tuition, self.level4, '1600000', programme=self.pst)
        profile, _ = self.enrol('REG/003', self.module('MOD04101', self.level4))

        finance.generate_charges(profile, self.year, actor=self.accountant)

        self.assertEqual(self.charged(profile), {self.tuition.id: TZS('1000000.00')})

    def test_the_same_fee_cell_cannot_be_entered_twice(self):
        self.fee(self.tuition, self.level4, '1000000')
        with self.assertRaises(IntegrityError), transaction.atomic():
            FeeStructure.objects.create(charge_type=self.tuition, class_level=self.level4,
                                        academic_year=self.year, amount=TZS('1'))

        self.fee(self.tuition, self.level4, '1600000', programme=self.pst)
        with self.assertRaises(IntegrityError), transaction.atomic():
            FeeStructure.objects.create(charge_type=self.tuition, programme=self.pst,
                                        class_level=self.level4, academic_year=self.year, amount=TZS('1'))


class ReadmissionBillingTests(ProgrammeBillingBase):
    def setUp(self):
        super().setUp()
        self.caution = ChargeType.objects.create(name='Caution money', family=ChargeType.DIRECT_COST)
        self.fee(self.caution, self.level4, '50000', period=FeeStructure.ONCE)
        self.last_year = AcademicYear.objects.create(name='2025/2026')
        # Charged caution money when first admitted, last year — recorded before
        # this year's enrollment, because enrolling bills the student at once.
        self.profile = StudentProfile.objects.create(nactvet_reg_no='REG/001', name='Asha Juma')
        StudentCharge.objects.create(profile=self.profile, charge_type=self.caution,
                                     academic_year=self.last_year, amount=TZS('50000'),
                                     due_date=date(2025, 10, 30))
        self.module4 = self.module('PST04101', self.level4, programme=self.pst)
        self.enrol('REG/001', self.module4)

    def register(self, kind):
        SemesterRegistration.objects.create(profile=self.profile, semester=self.sem1,
                                            programme=self.pst, class_level=self.level4, kind=kind)

    def caution_charges_this_year(self):
        return StudentCharge.objects.filter(profile=self.profile, charge_type=self.caution,
                                            academic_year=self.year).count()

    def test_a_continuing_student_is_not_charged_a_once_only_fee_again(self):
        self.register(SemesterRegistration.CONTINUING)
        finance.generate_charges(self.profile, self.year, actor=self.accountant)
        self.assertEqual(self.caution_charges_this_year(), 0)

    def test_a_readmitted_student_is_charged_it_again_once(self):
        self.register(SemesterRegistration.READMISSION)
        finance.generate_charges(self.profile, self.year, actor=self.accountant)
        finance.generate_charges(self.profile, self.year, actor=self.accountant)
        self.assertEqual(self.caution_charges_this_year(), 1)

    def test_the_accountant_can_exempt_a_charge_from_readmission(self):
        self.caution.charged_again_on_readmission = False
        self.caution.save()
        self.register(SemesterRegistration.READMISSION)
        finance.generate_charges(self.profile, self.year, actor=self.accountant)
        self.assertEqual(self.caution_charges_this_year(), 0)


class DeclarationChargeTests(ProgrammeBillingBase):
    def setUp(self):
        super().setUp()
        self.supp = ChargeType.objects.create(
            name='Supplementary Exam', family=ChargeType.OTHER, applies=ChargeType.ON_REQUEST,
            declaration=ChargeType.SUPP_EXAM,
        )
        self.module_a = self.module('PST04101', self.level4, programme=self.pst)
        self.module_b = self.module('PST04102', self.level4, programme=self.pst)
        self.profile, self.rows = self.enrol('REG/001', self.module_a, self.module_b)

    def test_a_declaration_is_charged_at_the_programme_and_level_rate(self):
        self.fee(self.supp, self.level4, '20000')
        self.fee(self.supp, self.level4, '30000', programme=self.pst)

        charge, created = finance.declare_exam_charge(self.rows[0], ChargeType.SUPP_EXAM, actor=self.accountant)

        self.assertTrue(created)
        self.assertEqual(charge.amount, TZS('30000.00'))
        self.assertEqual(charge.module, self.module_a)
        self.assertEqual(charge.semester, self.sem1)
        self.assertEqual(charge.source, StudentCharge.ON_REQUEST)

    def test_each_module_is_charged_once_however_often_it_is_declared(self):
        self.fee(self.supp, self.level4, '30000', programme=self.pst)

        finance.declare_exam_charge(self.rows[0], ChargeType.SUPP_EXAM, actor=self.accountant)
        again, created = finance.declare_exam_charge(self.rows[0], ChargeType.SUPP_EXAM, actor=self.accountant)
        finance.declare_exam_charge(self.rows[1], ChargeType.SUPP_EXAM, actor=self.accountant)

        self.assertFalse(created)
        self.assertEqual(StudentCharge.objects.filter(profile=self.profile, charge_type=self.supp).count(), 2)
        self.assertEqual(finance.balance_for(self.profile, self.year)['balance'], TZS('60000.00'))

    def test_no_linked_charge_type_is_refused_with_a_reason(self):
        with self.assertRaisesMessage(finance.DeclarationError, 'No charge type is linked'):
            finance.declare_exam_charge(self.rows[0], ChargeType.SPECIAL_EXAM, actor=self.accountant)
        self.assertFalse(StudentCharge.objects.exists())

    def test_no_rate_set_is_refused_with_a_reason(self):
        with self.assertRaisesMessage(finance.DeclarationError, 'has not set a rate'):
            finance.declare_exam_charge(self.rows[0], ChargeType.SUPP_EXAM, actor=self.accountant)
        self.assertFalse(StudentCharge.objects.exists())

    def test_the_declaration_screen_can_see_who_is_already_declared(self):
        self.fee(self.supp, self.level4, '30000', programme=self.pst)
        finance.declare_exam_charge(self.rows[0], ChargeType.SUPP_EXAM, actor=self.accountant)

        self.assertEqual(finance.declared_charges(self.module_a), {self.profile.id: [ChargeType.SUPP_EXAM]})
        self.assertEqual(finance.declared_charges(self.module_b), {})

    def test_only_one_charge_type_may_answer_a_declaration(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            ChargeType.objects.create(name='Another supp', declaration=ChargeType.SUPP_EXAM)


class StudentRecordTests(TestCase):
    def test_many_students_can_wait_for_a_college_id_but_no_two_share_one(self):
        StudentProfile.objects.create(nactvet_reg_no='A', name='A')
        StudentProfile.objects.create(nactvet_reg_no='B', name='B')
        StudentProfile.objects.create(nactvet_reg_no='C', name='C', college_id='BPH/PST/26/0001')
        with self.assertRaises(IntegrityError), transaction.atomic():
            StudentProfile.objects.create(nactvet_reg_no='D', name='D', college_id='BPH/PST/26/0001')

    def test_a_student_has_at_most_two_next_of_kin(self):
        profile = StudentProfile.objects.create(nactvet_reg_no='A', name='A')
        for position in (1, 2):
            NextOfKin.objects.create(profile=profile, position=position, name='K', phone='0700',
                                     relationship=NextOfKin.PARENT)
        with self.assertRaises(IntegrityError), transaction.atomic():
            NextOfKin.objects.create(profile=profile, position=3, name='K', phone='0700',
                                     relationship=NextOfKin.PARENT)
        with self.assertRaises(IntegrityError), transaction.atomic():
            NextOfKin.objects.create(profile=profile, position=1, name='K', phone='0700',
                                     relationship=NextOfKin.PARENT)
