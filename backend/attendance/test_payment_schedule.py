"""When fees fall due, and the hostel.

The college publishes its instalment dates on one form for the year (Fomu ya
tarehe za awamu za malipo ya ada na michango mingine, 2026/2027): new students
pay in five instalments, continuing students in four, and a hostel resident
adds the hostel fee in two. The tests hold the system to that form's subtotals:

  * a new day student owes 945,000 · 550,000 · 200,000 · 400,000 · 400,000,
    and in the hostel 1,145,000 · 550,000 · 200,000 · 600,000 · 400,000;
  * a continuing day student owes 1,005,000 · 400,000 · 400,000 · 400,000,
    and in the hostel 1,205,000 · 400,000 · 600,000 · 400,000;
  * a continuing student is never billed the one-time charges again;
  * registering means paying the first instalment, even before its date;
  * the finance desk records day or hostel for a new student before passing
    them on, and a day student can apply for a hostel place from the portal.
"""

from datetime import date, timedelta
from decimal import Decimal
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from rest_framework.test import APIClient

from . import admissions, finance
from .models import (
    AcademicYear, Application, ChargeType, ClassLevel, Department, HostelApplication, Module,
    PaymentSchedule, Programme, Semester, SemesterRegistration, Student, StudentCharge,
    StudentProfile, StudentResidence,
)

User = get_user_model()


class ScheduleBase(TestCase):
    def setUp(self):
        self.last_year = AcademicYear.objects.create(name='2025/2026')
        self.year = AcademicYear.objects.create(name='2026/2027', is_active=True)
        self.sem1 = Semester.objects.create(academic_year=self.year, number=1, is_active=True)
        self.sem2 = Semester.objects.create(academic_year=self.year, number=2)
        self.last_sem2 = Semester.objects.create(academic_year=self.last_year, number=2)
        for name, order in [('NTA Level 4', 4), ('NTA Level 5', 5), ('NTA Level 6', 6)]:
            ClassLevel.objects.create(name=name, order=order)
        self.level4 = ClassLevel.objects.get(order=4)
        self.level5 = ClassLevel.objects.get(order=5)
        department = Department.objects.create(name='Pharmaceutical Sciences', code='PST')
        self.pst = Programme.objects.create(department=department, name='Pharmaceutical Sciences',
                                            code='PST')
        self.pst.levels.set(ClassLevel.objects.all())
        call_command('seed_fee_structure', year='2026/2027', first_due='2026-09-30', verbosity=0)
        self.out = StringIO()
        call_command('seed_payment_schedule', year='2026/2027', stdout=self.out)

        self.officer = User.objects.create_user('officer', password='pw')
        self.accountant = User.objects.create_user('money', password='pw')
        from .views import set_roles
        set_roles(self.accountant, ['accountant'], full_name='Accountant')
        self.accounts = APIClient(); self.accounts.force_authenticate(self.accountant)

    def by_due_date(self, profile):
        totals = {}
        for charge in StudentCharge.objects.filter(profile=profile, academic_year=self.year):
            totals[charge.due_date] = totals.get(charge.due_date, Decimal(0)) + charge.payable
        return [int(totals[day]) for day in sorted(totals)]

    def new_student(self, reg_no='NIT/PST/2026/0001', residence=StudentResidence.DAY):
        profile = StudentProfile.objects.create(nactvet_reg_no=reg_no, name='New Student')
        if residence:
            StudentResidence.objects.create(profile=profile, academic_year=self.year, residence=residence)
        finance.generate_charges(profile, self.year, class_level=self.level4, programme=None,
                                 entry=PaymentSchedule.NEW)
        return profile

    def continuing_student(self, reg_no='NIT/PST/2025/0001'):
        profile = StudentProfile.objects.create(nactvet_reg_no=reg_no, name='Continuing Student')
        # Studied here last year; last year's fees were kept in Excel, so no
        # charges exist for it.
        SemesterRegistration.objects.create(profile=profile, semester=self.last_sem2, programme=self.pst,
                                            class_level=self.level4, kind=SemesterRegistration.NEW)
        return profile


class TheFormTests(ScheduleBase):
    def test_the_seed_matches_every_subtotal_on_the_form(self):
        output = self.out.getvalue()

        self.assertEqual(output.count('matches the form'), 9)
        new = PaymentSchedule.objects.get(academic_year=self.year, entry=PaymentSchedule.NEW)
        self.assertEqual([step.due_date for step in new.steps.all()],
                         [date(2026, 10, 19), date(2026, 12, 18), date(2027, 1, 31),
                          date(2027, 3, 25), date(2027, 5, 30)])
        self.assertEqual([step.semester_number for step in new.steps.all()], [1, 1, 1, 2, 2])
        self.assertEqual(finance.schedule_mismatches(new), [])

    def test_a_new_day_student_is_billed_the_form_s_instalments(self):
        profile = self.new_student()

        self.assertEqual(self.by_due_date(profile), [945_000, 550_000, 200_000, 400_000, 400_000])

    def test_a_new_hostel_student_is_billed_the_form_s_instalments(self):
        profile = self.new_student(residence=StudentResidence.HOSTEL)

        self.assertEqual(self.by_due_date(profile), [1_145_000, 550_000, 200_000, 600_000, 400_000])

    def test_a_continuing_day_student_is_billed_the_form_s_instalments(self):
        profile = self.continuing_student()

        finance.generate_charges(profile, self.year, class_level=self.level5)

        self.assertEqual(self.by_due_date(profile), [1_005_000, 400_000, 400_000, 400_000])

    def test_a_continuing_hostel_student_is_billed_the_form_s_instalments(self):
        profile = self.continuing_student()
        StudentResidence.objects.create(profile=profile, academic_year=self.last_year,
                                        residence=StudentResidence.HOSTEL)
        finance.carry_residence(profile, self.year, class_level=self.level5)

        finance.generate_charges(profile, self.year, class_level=self.level5)

        self.assertEqual(self.by_due_date(profile), [1_205_000, 400_000, 600_000, 400_000])
        self.assertEqual(finance.residence_for(profile, self.year).source, StudentResidence.CARRIED)

    def test_a_continuing_student_is_not_billed_the_one_time_charges_again(self):
        profile = self.continuing_student()

        finance.generate_charges(profile, self.year, class_level=self.level5)

        once = StudentCharge.objects.filter(profile=profile, charge_type__frequency=ChargeType.ONCE)
        self.assertFalse(once.exists())

    def test_registering_needs_the_first_instalment_even_before_its_date(self):
        profile = self.new_student()
        before = date(2026, 10, 12)          # college opens; awamu 1 is due by 19/10

        unpaid = finance.exam_clearance(profile, self.year, ChargeType.REGISTRATION,
                                        semester=self.sem1, today=before)
        finance.record_payment(profile, Decimal('945000'), before, recorded_by=self.officer,
                               bank_reference='CRDB-1')
        paid = finance.exam_clearance(profile, self.year, ChargeType.REGISTRATION,
                                      semester=self.sem1, today=before)

        self.assertFalse(unpaid['cleared'])
        self.assertTrue(paid['cleared'])

    def test_a_schedule_that_does_not_add_up_falls_back_to_the_fee_s_own_dates(self):
        tuition = ChargeType.objects.get(name='Tuition Fee')
        schedule = PaymentSchedule.objects.get(academic_year=self.year, entry=PaymentSchedule.NEW)
        line = schedule.steps.get(number=5).amounts.get(charge_type=tuition)
        line.amount = Decimal('100000')
        line.save()

        profile = self.new_student()

        charged = StudentCharge.objects.filter(profile=profile, charge_type=tuition)
        self.assertEqual(sum(charge.amount for charge in charged), Decimal('1600000.00'))
        structure = charged.first().fee_structure
        self.assertEqual(sorted(charge.due_date for charge in charged),
                         sorted(row.due_date for row in structure.installment_schedule.all()))
        self.assertEqual(finance.schedule_mismatches(schedule)[0]['charge_type'], 'Tuition Fee')

    def test_next_year_starts_from_this_year_a_year_on(self):
        next_year = AcademicYear.objects.create(name='2027/2028')

        response = self.accounts.post('/api/finance/payment-schedules/copy/',
                                      {'academic_year': next_year.id}, format='json')

        self.assertEqual(response.data['copied'], 2)
        copied = PaymentSchedule.objects.get(academic_year=next_year, entry=PaymentSchedule.NEW)
        self.assertEqual(copied.steps.first().due_date, date(2027, 10, 19))

    def test_the_accountant_edits_a_schedule(self):
        tuition = ChargeType.objects.get(name='Tuition Fee')
        response = self.accounts.post('/api/finance/payment-schedules/', {
            'academic_year': self.year.id, 'entry': PaymentSchedule.CONTINUING,
            'steps': [{'due_date': '2026-10-19', 'semester_number': 1, 'amounts': {tuition.id: '800000'}},
                      {'due_date': '2027-03-25', 'semester_number': 2, 'amounts': {tuition.id: '800000'}}],
        }, format='json')

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(response.data['steps']), 2)
        read = self.accounts.get(f'/api/finance/payment-schedules/?academic_year={self.year.id}').data
        self.assertEqual(len(read['schedules']['continuing']['steps']), 2)
        self.assertTrue(any(row['name'] == 'Accommodation' and row['is_hostel'] for row in read['charge_types']))

    def test_only_finance_reads_or_edits_the_schedule(self):
        other = User.objects.create_user('someone', password='pw')
        client = APIClient(); client.force_authenticate(other)

        self.assertEqual(client.get('/api/finance/payment-schedules/').status_code, 403)


class FinanceDeskResidenceTests(ScheduleBase):
    def setUp(self):
        super().setUp()
        from .models import AdmissionWindow
        AdmissionWindow.objects.create(semester=self.sem1, opens_on=date.today() - timedelta(days=1),
                                       closes_on=date.today() + timedelta(days=30))
        self.module = Module.objects.create(name='Anatomy', code='PST04101', teacher='T',
                                            class_level=self.level4, semester=self.sem1,
                                            programme=self.pst, credits=3)
        self.profile = admissions.capture_student(
            nactvet_reg_no='NIT/PST/2026/0100', name='Asha Hostel', phone='07', gender='F',
            date_of_birth=date(2006, 1, 1), actor=self.officer,
            next_of_kin=[{'name': 'A', 'phone': '1', 'relationship': 'parent'},
                         {'name': 'B', 'phone': '2', 'relationship': 'parent'}])
        self.application = admissions.open_application(
            profile=self.profile, semester=self.sem1, programme=self.pst, class_level=self.level4,
            kind=Application.NEW, actor=self.officer)
        admissions.complete_step(self.application, actor=self.officer)   # intake
        admissions.complete_step(self.application, actor=self.officer)   # records → finance

    def hostel_charges(self):
        return list(finance.with_balances(StudentCharge.objects.filter(
            profile=self.profile, charge_type__is_hostel=True)))

    def test_finance_does_not_pass_a_new_student_on_without_day_or_hostel(self):
        with self.assertRaises(admissions.AdmissionError) as held:
            admissions.complete_step(self.application, actor=self.accountant)

        self.assertIn('hostel', str(held.exception))

    def test_hostel_adds_the_hostel_instalments(self):
        response = self.accounts.post(f'/api/applications/{self.application.id}/residence/',
                                      {'residence': 'hostel'}, format='json')

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['residence']['residence'], 'hostel')
        self.assertEqual(sorted((c.due_date, int(c.amount)) for c in self.hostel_charges()),
                         [(date(2026, 10, 19), 200_000), (date(2027, 3, 25), 200_000)])
        self.assertEqual(self.by_due_date(self.profile), [1_145_000, 550_000, 200_000, 600_000, 400_000])

    def test_changing_to_day_takes_the_unpaid_hostel_fee_off(self):
        admissions.set_residence(self.application, 'hostel', actor=self.accountant)

        admissions.set_residence(self.application, 'day', actor=self.accountant)

        self.assertTrue(all(finance.charge_balance(charge) == 0 for charge in self.hostel_charges()))
        self.assertEqual(self.by_due_date(self.profile), [945_000, 550_000, 200_000, 400_000, 400_000])
        # And back again.
        admissions.set_residence(self.application, 'hostel', actor=self.accountant)
        self.assertEqual(self.by_due_date(self.profile), [1_145_000, 550_000, 200_000, 600_000, 400_000])

    def test_only_finance_states_it(self):
        other = User.objects.create_user('records1', password='pw')
        from .views import set_roles
        set_roles(other, ['records_officer'], full_name='Records')
        client = APIClient(); client.force_authenticate(other)

        response = client.post(f'/api/applications/{self.application.id}/residence/',
                               {'residence': 'hostel'}, format='json')

        self.assertIn(response.status_code, (403, 404))
        self.assertFalse(self.hostel_charges())


class HostelApplicationTests(ScheduleBase):
    def setUp(self):
        super().setUp()
        self.profile = self.new_student('NIT/PST/2026/0200')
        for semester in (self.sem1, self.sem2):
            SemesterRegistration.objects.create(profile=self.profile, semester=semester, programme=self.pst,
                                                class_level=self.level4, kind=SemesterRegistration.NEW)
        module = Module.objects.create(name='Anatomy', code='PST04101', teacher='T', class_level=self.level4,
                                       semester=self.sem1, programme=self.pst, credits=3)
        enrollment = Student.objects.create(nactvet_reg_no=self.profile.nactvet_reg_no,
                                            name=self.profile.name, profile=self.profile, module=module)
        enrollment.set_portal_pin('Portal#2026', require_change=False)
        enrollment.save()
        session = self.client.session
        session['student_id'] = enrollment.id
        session.save()

    def apply(self):
        return self.client.post('/api/my-hostel/', {'reason': 'Home is far'},
                                content_type='application/json')

    def test_a_day_student_applies_and_is_granted_a_place(self):
        applied = self.apply()
        application = HostelApplication.objects.get(profile=self.profile)

        decided = self.accounts.post(f'/api/hostel-applications/{application.id}/decide/',
                                     {'decision': 'approve'}, format='json')

        self.assertEqual(applied.status_code, 201, applied.data)
        self.assertEqual(decided.data['status'], HostelApplication.APPROVED)
        self.assertEqual(finance.residence_for(self.profile, self.year).source, StudentResidence.APPLICATION)
        self.assertEqual(self.by_due_date(self.profile), [1_145_000, 550_000, 200_000, 600_000, 400_000])
        mine = self.client.get('/api/my-hostel/').data
        self.assertEqual(mine['residence'], 'hostel')

    def test_a_place_granted_in_semester_2_is_charged_semester_2_only(self):
        self.sem1.is_active = False
        self.sem1.save()
        self.sem2.is_active = True
        self.sem2.save()
        self.apply()
        application = HostelApplication.objects.get(profile=self.profile)

        self.accounts.post(f'/api/hostel-applications/{application.id}/decide/',
                           {'decision': 'approve'}, format='json')

        hostel = StudentCharge.objects.filter(profile=self.profile, charge_type__is_hostel=True)
        self.assertEqual([(c.due_date, int(c.amount)) for c in hostel], [(date(2027, 3, 25), 200_000)])

    def test_one_waiting_application_at_a_time_and_none_from_the_hostel(self):
        self.apply()
        again = self.apply()

        self.assertEqual(again.status_code, 400)
        application = HostelApplication.objects.get(profile=self.profile)
        self.accounts.post(f'/api/hostel-applications/{application.id}/decide/',
                           {'decision': 'approve'}, format='json')
        self.assertEqual(self.apply().status_code, 400)

    def test_declining_needs_a_reason(self):
        self.apply()
        application = HostelApplication.objects.get(profile=self.profile)

        bare = self.accounts.post(f'/api/hostel-applications/{application.id}/decide/',
                                  {'decision': 'decline'}, format='json')
        declined = self.accounts.post(f'/api/hostel-applications/{application.id}/decide/',
                                      {'decision': 'decline', 'note': 'The hostel is full'}, format='json')

        self.assertEqual(bare.status_code, 400)
        self.assertEqual(declined.data['status'], HostelApplication.DECLINED)
        self.assertFalse(StudentCharge.objects.filter(profile=self.profile, charge_type__is_hostel=True).exists())

    def test_only_the_accountant_or_principal_decides(self):
        self.apply()
        application = HostelApplication.objects.get(profile=self.profile)
        other = User.objects.create_user('someone', password='pw')
        client = APIClient(); client.force_authenticate(other)

        response = client.post(f'/api/hostel-applications/{application.id}/decide/',
                               {'decision': 'approve'}, format='json')

        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.accounts.get('/api/hostel-applications/').data[0]['reason'], 'Home is far')

    def test_a_student_not_studying_this_semester_cannot_apply(self):
        SemesterRegistration.objects.filter(profile=self.profile).update(
            status=SemesterRegistration.CANCELLED)

        response = self.apply()

        self.assertEqual(response.status_code, 400)
        self.assertFalse(self.client.get('/api/my-hostel/').data['can_apply'])
