"""Admitting a student: the three desks, and what the last one does.

The college's rules, each one a test:

  * a first year is written down, billed, checked and only then admitted;
  * a continuing or readmitted student starts at finance, because the college
    already knows who they are;
  * the college ID is issued once and kept — a readmitted student keeps theirs;
  * a requirement the student does not have is charged at the accountant's rate;
  * admitting is the one moment a registration, an enrollment and a bill exist;
  * nobody is admitted twice for the same semester.
"""

from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from . import admissions, finance
from .models import (
    AcademicYear, AdmissionRequirement, AdmissionWindow, Application, ApplicationStep,
    ChargeType, ClassLevel, CollegeIdFormat, Department, FeeStructure, Module, NextOfKin,
    Programme, RequirementCheck, Semester, SemesterRegistration, SemesterReview, Student,
    StudentCharge, StudentDocument, StudentProfile, StudentStanding,
)

User = get_user_model()


class AdmissionBase(TestCase):
    def setUp(self):
        self.year = AcademicYear.objects.create(name='2026/2027', is_active=True)
        self.sem1 = Semester.objects.create(academic_year=self.year, number=1, is_active=True)
        self.sem2 = Semester.objects.create(academic_year=self.year, number=2)
        self.level4 = ClassLevel.objects.create(name='NTA Level 4', order=4)
        self.level5 = ClassLevel.objects.create(name='NTA Level 5', order=5)

        self.department = Department.objects.create(name='Pharmaceutical Sciences', code='PST')
        self.pst = Programme.objects.create(
            department=self.department, name='Pharmaceutical Sciences', code='PST')
        self.pst.levels.set([self.level4, self.level5])

        self.officer = User.objects.create_user('admissions', password='pw')
        today = timezone.localdate()
        self.window = AdmissionWindow.objects.create(
            semester=self.sem1,
            opens_on=today - timedelta(days=1),
            closes_on=today + timedelta(days=30),
        )

    # ── fixtures ──────────────────────────────────────────────────────────────

    def module(self, code, level, semester):
        return Module.objects.create(
            name=code, code=code, teacher='T', class_level=level,
            semester=semester, programme=self.pst, credits=3)

    def fee(self, name, amount, level=None, *, frequency=ChargeType.EACH_YEAR,
            period=FeeStructure.ACADEMIC_YEAR, applies=ChargeType.AUTOMATIC):
        charge_type = ChargeType.objects.create(
            name=name, family=ChargeType.FEE, frequency=frequency, applies=applies)
        structure = FeeStructure.objects.create(
            charge_type=charge_type, programme=self.pst, class_level=level or self.level4,
            academic_year=self.year, amount=Decimal(amount),
            billing_period=period, installments=1)
        finance.set_installment_schedule(structure, [date(2026, 11, 30)])
        return charge_type

    def applicant(self, reg_no='NACTVET/PENDING/001', name='Rehema Mtui'):
        return admissions.capture_student(
            nactvet_reg_no=reg_no, name=name, phone='0712000111', gender='F',
            date_of_birth=date(2006, 4, 12), actor=self.officer,
            next_of_kin=[{'name': 'Mama Mtui', 'phone': '0713000222', 'relationship': 'parent'},
                         {'name': 'Baba Mtui', 'phone': '0714000333', 'relationship': 'parent'}],
        )


# ── the route each kind takes ─────────────────────────────────────────────────

class RouteTests(AdmissionBase):
    def test_a_first_year_is_written_down_and_waits_at_intake(self):
        profile = self.applicant()
        self.assertEqual(profile.next_of_kin.count(), 2)

        application = admissions.open_application(
            profile=profile, semester=self.sem1, programme=self.pst,
            class_level=self.level4, kind=Application.NEW, actor=self.officer)

        # The admission office takes them on first; no desk is stamped by the
        # act of opening the application.
        self.assertEqual(application.state, Application.INTAKE)
        self.assertEqual(application.steps.count(), 0)

    def test_a_continuing_student_starts_at_finance(self):
        profile = self.applicant('NIT/PST/2025/001', 'Continuing Student')

        application = admissions.open_application(
            profile=profile, semester=self.sem1, programme=self.pst,
            class_level=self.level5, kind=Application.CONTINUING, actor=self.officer)

        self.assertEqual(application.state, Application.FINANCE)
        self.assertEqual(application.steps.count(), 0)

    def test_the_desks_are_worked_in_order(self):
        profile = self.applicant()
        application = admissions.open_application(
            profile=profile, semester=self.sem1, programme=self.pst,
            class_level=self.level4, kind=Application.NEW, actor=self.officer)

        admissions.complete_step(application, actor=self.officer, note='Number NIT/… issued.')
        self.assertEqual(application.state, Application.INFORMATION)
        admissions.complete_step(application, actor=self.officer, note='Certificates scanned.')
        self.assertEqual(application.state, Application.FINANCE)
        admissions.set_residence(application, 'day', actor=self.officer)
        admissions.complete_step(application, actor=self.officer, note='Paid 400,000 at CRDB.')
        self.assertEqual(application.state, Application.ADMISSION)
        admissions.complete_step(application, actor=self.officer)
        self.assertEqual(application.state, Application.ADMITTED)
        self.assertEqual([step.step for step in application.steps.all()],
                         [Application.INTAKE, Application.INFORMATION,
                          Application.FINANCE, Application.ADMISSION])

    def test_an_application_can_be_sent_back_with_a_reason(self):
        profile = self.applicant()
        application = admissions.open_application(
            profile=profile, semester=self.sem1, programme=self.pst,
            class_level=self.level4, kind=Application.NEW, actor=self.officer)
        admissions.complete_step(application, actor=self.officer)   # intake
        admissions.complete_step(application, actor=self.officer)   # records → finance

        admissions.send_back(application, to=Application.INFORMATION, actor=self.officer,
                             reason='The certificate scanned belongs to another student.')

        self.assertEqual(application.state, Application.INFORMATION)
        self.assertIn('Sent back', application.steps.last().note)

    def test_sending_an_application_back_needs_a_reason(self):
        profile = self.applicant()
        application = admissions.open_application(
            profile=profile, semester=self.sem1, programme=self.pst,
            class_level=self.level4, kind=Application.NEW, actor=self.officer)
        admissions.complete_step(application, actor=self.officer)

        with self.assertRaises(admissions.AdmissionError):
            admissions.send_back(application, to=Application.INTAKE, actor=self.officer)

    def test_admissions_closed_means_no_application(self):
        self.window.is_active = False
        self.window.save(update_fields=['is_active'])
        profile = self.applicant()

        with self.assertRaises(admissions.AdmissionError):
            admissions.open_application(
                profile=profile, semester=self.sem1, programme=self.pst,
                class_level=self.level4, kind=Application.NEW, actor=self.officer)

    def test_nobody_has_two_applications_for_one_semester(self):
        profile = self.applicant()
        admissions.open_application(
            profile=profile, semester=self.sem1, programme=self.pst,
            class_level=self.level4, kind=Application.NEW, actor=self.officer)

        with self.assertRaises(admissions.AdmissionError):
            admissions.open_application(
                profile=profile, semester=self.sem1, programme=self.pst,
                class_level=self.level4, kind=Application.NEW, actor=self.officer)


# ── money ─────────────────────────────────────────────────────────────────────

class ChargeTests(AdmissionBase):
    def test_the_charges_are_raised_on_the_way_into_finance(self):
        tuition = self.fee('Tuition Fee', '1000000')
        profile = self.applicant()

        application = admissions.open_application(
            profile=profile, semester=self.sem1, programme=self.pst,
            class_level=self.level4, kind=Application.NEW, actor=self.officer)
        admissions.complete_step(application, actor=self.officer)   # intake
        admissions.complete_step(application, actor=self.officer)   # records → finance

        # There has to be something to pay before the desk can be cleared.
        charge = StudentCharge.objects.get(profile=profile, charge_type=tuition)
        self.assertEqual(charge.amount, Decimal('1000000.00'))

    def test_a_first_year_is_billed_the_one_time_charges_too(self):
        self.fee('Tuition Fee', '1000000')
        admission_fee = self.fee('Admission Fee', '20000', frequency=ChargeType.ONCE,
                                 period=FeeStructure.ONCE)
        profile = self.applicant()

        application = admissions.open_application(
            profile=profile, semester=self.sem1, programme=self.pst,
            class_level=self.level4, kind=Application.NEW, actor=self.officer)
        admissions.complete_step(application, actor=self.officer)   # intake
        admissions.complete_step(application, actor=self.officer)   # records → finance

        self.assertTrue(StudentCharge.objects.filter(
            profile=profile, charge_type=admission_fee).exists())

    def test_a_missing_requirement_is_charged_at_the_accountant_s_rate(self):
        book = self.fee('TPH Book', '35000', applies=ChargeType.ON_REQUEST,
                        period=FeeStructure.ONCE)
        requirement = AdmissionRequirement.objects.create(
            name='TPH Book', charge_type=book,
            frequency=AdmissionRequirement.EVERY_SEMESTER)
        profile = self.applicant()
        application = admissions.open_application(
            profile=profile, semester=self.sem1, programme=self.pst,
            class_level=self.level4, kind=Application.NEW, actor=self.officer)

        check = admissions.record_requirement(
            application, requirement, RequirementCheck.MISSING, actor=self.officer)

        self.assertIsNotNone(check.charge)
        self.assertEqual(check.charge.amount, Decimal('35000.00'))
        self.assertIn('TPH Book', check.charge.note)

    def test_a_requirement_the_student_has_is_not_charged(self):
        book = self.fee('TPH Book', '35000', applies=ChargeType.ON_REQUEST,
                        period=FeeStructure.ONCE)
        requirement = AdmissionRequirement.objects.create(name='TPH Book', charge_type=book)
        profile = self.applicant()
        application = admissions.open_application(
            profile=profile, semester=self.sem1, programme=self.pst,
            class_level=self.level4, kind=Application.NEW, actor=self.officer)

        check = admissions.record_requirement(
            application, requirement, RequirementCheck.HAS_IT, actor=self.officer)

        self.assertIsNone(check.charge)
        self.assertFalse(StudentCharge.objects.filter(charge_type=book).exists())

    def test_a_requirement_checked_once_is_not_asked_for_again(self):
        requirement = AdmissionRequirement.objects.create(
            name='Birth certificate', frequency=AdmissionRequirement.ONCE)
        profile = self.applicant()
        first = admissions.open_application(
            profile=profile, semester=self.sem1, programme=self.pst,
            class_level=self.level4, kind=Application.NEW, actor=self.officer)
        admissions.record_requirement(first, requirement, RequirementCheck.HAS_IT,
                                      actor=self.officer)
        AdmissionWindow.objects.create(
            semester=self.sem2, opens_on=date.today(), closes_on=date.today() + timedelta(days=5))
        second = Application.objects.create(
            profile=profile, semester=self.sem2, programme=self.pst,
            class_level=self.level4, kind=Application.CONTINUING, state=Application.FINANCE)

        self.assertIn(requirement, admissions.requirements_for(first))
        self.assertNotIn(requirement, admissions.requirements_for(second))


# ── the college's own number ──────────────────────────────────────────────────

class CollegeIdTests(AdmissionBase):
    def test_the_office_says_what_the_number_looks_like_and_where_it_starts(self):
        fmt = admissions.id_format_for(self.year)
        fmt.pattern = '{COLLEGE}/{PROG}/{YY}/{SEQ}'
        fmt.college_code = 'BPHACOH'
        fmt.sequence_width = 4
        fmt.starts_at = 120
        fmt.next_number = 120
        fmt.save()
        profile = self.applicant()

        issued = admissions.issue_college_id(profile, self.year, self.pst)

        self.assertEqual(issued, 'BPHACOH/PST/26/0120')
        fmt.refresh_from_db()
        self.assertEqual(fmt.next_number, 121)

    def test_the_counter_runs_on_for_the_next_student(self):
        first = self.applicant('REG/001', 'First Student')
        second = self.applicant('REG/002', 'Second Student')

        admissions.issue_college_id(first, self.year, self.pst)
        admissions.issue_college_id(second, self.year, self.pst)

        self.assertEqual(first.college_id, 'BPH/PST/2026/001')
        self.assertEqual(second.college_id, 'BPH/PST/2026/002')

    def test_a_student_who_has_a_number_keeps_it(self):
        profile = self.applicant()
        profile.college_id = 'BPH/PST/2024/017'
        profile.save(update_fields=['college_id'])

        issued = admissions.issue_college_id(profile, self.year, self.pst)

        self.assertEqual(issued, 'BPH/PST/2024/017')
        self.assertEqual(admissions.id_format_for(self.year).next_number, 1)

    def test_a_number_already_in_use_is_skipped(self):
        StudentProfile.objects.create(nactvet_reg_no='OLD/001', name='Already Here',
                                      college_id='BPH/PST/2026/001')
        profile = self.applicant()

        issued = admissions.issue_college_id(profile, self.year, self.pst)

        self.assertEqual(issued, 'BPH/PST/2026/002')

    def test_next_year_inherits_the_shape_and_restarts_the_counter(self):
        fmt = admissions.id_format_for(self.year)
        fmt.college_code = 'BPHACOH'
        fmt.next_number = 84
        fmt.save()
        next_year = AcademicYear.objects.create(name='2027/2028')

        carried = admissions.id_format_for(next_year)

        self.assertEqual(carried.college_code, 'BPHACOH')
        self.assertEqual(carried.next_number, 1)


# ── admitting ─────────────────────────────────────────────────────────────────

class AdmitTests(AdmissionBase):
    def setUp(self):
        super().setUp()
        self.tuition = self.fee('Tuition Fee', '1000000')
        self.module('PST04101', self.level4, self.sem1)
        self.module('PST04102', self.level4, self.sem1)

    def admit_a_first_year(self, reg_no='NACTVET/PENDING/001'):
        profile = self.applicant(reg_no)
        application = admissions.open_application(
            profile=profile, semester=self.sem1, programme=self.pst,
            class_level=self.level4, kind=Application.NEW, actor=self.officer)
        admissions.complete_step(application, actor=self.officer)   # intake
        admissions.complete_step(application, actor=self.officer)   # records
        admissions.set_residence(application, 'day', actor=self.officer)
        admissions.complete_step(application, actor=self.officer)   # finance
        admissions.complete_step(application, actor=self.officer)   # admission → admitted
        return profile, application

    def test_admitting_registers_enrolls_and_numbers_the_student(self):
        profile, application = self.admit_a_first_year()

        self.assertEqual(application.state, Application.ADMITTED)
        registration = SemesterRegistration.objects.get(profile=profile, semester=self.sem1)
        self.assertEqual(registration.kind, SemesterRegistration.NEW)
        self.assertEqual(application.registration, registration)
        self.assertEqual(
            sorted(row.module.code for row in Student.objects.filter(profile=profile)),
            ['PST04101', 'PST04102'])
        self.assertTrue(profile.college_id)
        self.assertEqual(profile.standing.status, StudentStanding.ACTIVE)

    def test_nothing_is_registered_before_the_last_desk(self):
        profile = self.applicant()
        application = admissions.open_application(
            profile=profile, semester=self.sem1, programme=self.pst,
            class_level=self.level4, kind=Application.NEW, actor=self.officer)
        admissions.complete_step(application, actor=self.officer)   # intake
        admissions.complete_step(application, actor=self.officer)   # records
        admissions.set_residence(application, 'day', actor=self.officer)
        admissions.complete_step(application, actor=self.officer)   # finance

        self.assertEqual(application.state, Application.ADMISSION)
        self.assertFalse(SemesterRegistration.objects.filter(profile=profile).exists())
        self.assertFalse(Student.objects.filter(profile=profile).exists())
        self.assertFalse(profile.college_id)

    def test_a_readmitted_student_keeps_their_number_and_is_billed_afresh(self):
        profile, _ = self.admit_a_first_year()
        number = profile.college_id
        # They are discontinued, and come back the following year.
        next_year = AcademicYear.objects.create(name='2027/2028')
        next_sem1 = Semester.objects.create(academic_year=next_year, number=1)
        AdmissionWindow.objects.create(semester=next_sem1, opens_on=date.today(),
                                       closes_on=date.today() + timedelta(days=10))
        charge_type = ChargeType.objects.create(
            name='Admission Fee', family=ChargeType.FEE, frequency=ChargeType.ONCE)
        structure = FeeStructure.objects.create(
            charge_type=charge_type, programme=self.pst, class_level=self.level4,
            academic_year=next_year, amount=Decimal('20000'),
            billing_period=FeeStructure.ONCE, installments=1)
        finance.set_installment_schedule(structure, [date(2027, 11, 30)])

        application = admissions.open_application(
            profile=profile, semester=next_sem1, programme=self.pst,
            class_level=self.level4, kind=Application.READMISSION, actor=self.officer)

        profile.refresh_from_db()
        self.assertEqual(profile.college_id, number)
        self.assertEqual(application.kind, Application.READMISSION)
        # A readmitted student pays the one-time charges again.
        self.assertTrue(StudentCharge.objects.filter(
            profile=profile, charge_type=charge_type, academic_year=next_year).exists())

    def test_a_refused_application_registers_nobody(self):
        profile = self.applicant()
        application = admissions.open_application(
            profile=profile, semester=self.sem1, programme=self.pst,
            class_level=self.level4, kind=Application.NEW, actor=self.officer)

        admissions.refuse(application, actor=self.officer, reason='Did not meet entry requirements.')

        self.assertEqual(application.state, Application.REJECTED)
        self.assertFalse(SemesterRegistration.objects.filter(profile=profile).exists())
        self.assertFalse(application.is_open)

    def test_an_admitted_student_is_not_admitted_twice(self):
        profile, application = self.admit_a_first_year()

        with self.assertRaises(admissions.AdmissionError):
            admissions.complete_step(application, actor=self.officer)
        with self.assertRaises(admissions.AdmissionError):
            admissions.open_application(
                profile=profile, semester=self.sem1, programme=self.pst,
                class_level=self.level4, kind=Application.CONTINUING, actor=self.officer)


# ── who the college is expecting back ─────────────────────────────────────────

class DueBackTests(AdmissionBase):
    def test_the_discontinued_are_listed_for_the_semester_they_left(self):
        from . import progression

        profile = self.applicant('NIT/PST/2025/010', 'Came Back')
        progression.set_standing(
            profile, StudentStanding.DISCONTINUED, actor=self.officer,
            class_level=self.level4, programme=self.pst,
            return_year=self.year, return_semester_number=1)

        waiting = admissions.due_back(self.sem1)

        self.assertEqual([standing.profile_id for standing in waiting], [profile.id])
        # Not in semester 2: they left in semester 1 and that is what they return to.
        self.assertEqual(admissions.due_back(self.sem2), [])

    def test_somebody_who_has_applied_drops_off_the_list(self):
        from . import progression

        profile = self.applicant('NIT/PST/2025/011', 'Already Applied')
        progression.set_standing(
            profile, StudentStanding.DISCONTINUED, actor=self.officer,
            class_level=self.level4, programme=self.pst,
            return_year=self.year, return_semester_number=1)
        admissions.open_application(
            profile=profile, semester=self.sem1, programme=self.pst,
            class_level=self.level4, kind=Application.READMISSION, actor=self.officer)

        self.assertEqual(admissions.due_back(self.sem1), [])


# ── the desks, through the API ────────────────────────────────────────────────

class AdmissionApiTests(AdmissionBase):
    def setUp(self):
        super().setUp()
        from rest_framework.test import APIClient
        from .views import set_roles

        self.tuition = self.fee('Tuition Fee', '1000000')
        self.module('PST04101', self.level4, self.sem1)

        self.admin = User.objects.create_superuser('exam', 'e@x.com', 'pw')
        self.api = APIClient(); self.api.force_authenticate(self.admin)

        def account(username, role):
            user = User.objects.create_user(username, password='pw')
            set_roles(user, [role], full_name=f'{role} person')
            client = APIClient(); client.force_authenticate(user)
            return user, client

        self.records_user, self.records = account('records', 'records_officer')
        self.admissions_user, self.admissions_api = account('admission', 'admission_officer')
        self.accountant_user, self.accounts = account('money', 'accountant')

    def capture(self, client=None, **extra):
        """The admission office takes a first year on: a name, a programme and a
        level. Who they are in full is the records desk's to take down."""
        payload = {
            'kind': Application.NEW, 'name': 'Rehema Mtui',
            'nactvet_reg_no': 'NACTVET/PENDING/009',
            'semester_id': self.sem1.id, 'programme_id': self.pst.id,
            'class_level_id': self.level4.id,
            **extra,
        }
        return (client or self.admissions_api).post('/api/applications/capture/', payload, format='json')

    def record(self, application_id):
        """The records desk takes the student's details down."""
        response = self.records.post(f'/api/applications/{application_id}/details/', {
            'name': 'Rehema Mtui', 'phone': '0712000111', 'gender': 'F',
            'date_of_birth': '2006-04-12',
            'next_of_kin': [{'name': 'Mama Mtui', 'phone': '0713000222', 'relationship': 'parent'},
                            {'name': 'Baba Mtui', 'phone': '0714000333', 'relationship': 'parent'}],
        }, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        return response

    def test_an_application_opens_at_the_admission_office_owing_nothing(self):
        response = self.capture()

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['state'], Application.INTAKE)
        self.assertEqual(response.data['student_name'], 'Rehema Mtui')
        # Intake and records handle no money; finance is two desks away.
        self.assertEqual(response.data['balance'], '0.00')

    def test_only_the_desk_holding_an_application_may_clear_it(self):
        application_id = self.capture().data['id']
        self.admissions_api.post(f'/api/applications/{application_id}/complete/', {}, format='json')
        self.record(application_id)
        self.records.post(f'/api/applications/{application_id}/complete/', {}, format='json')

        # Admission sees every desk but does not hold finance's; records no
        # longer even sees a student who has gone on to finance.
        refused = self.admissions_api.post(f'/api/applications/{application_id}/complete/', {}, format='json')
        unseen = self.records.post(f'/api/applications/{application_id}/complete/', {}, format='json')
        self.accounts.post(f'/api/applications/{application_id}/residence/', {'residence': 'day'},
                           format='json')
        allowed = self.accounts.post(f'/api/applications/{application_id}/complete/',
                                     {'note': 'Receipt 4417 seen.'}, format='json')

        self.assertEqual(refused.status_code, 403)
        self.assertIn('accountant', refused.data['detail'])
        self.assertEqual(unseen.status_code, 404)
        self.assertEqual(allowed.status_code, 200, allowed.data)
        self.assertEqual(allowed.data['state'], Application.ADMISSION)

    def test_the_whole_queue_is_worked_desk_by_desk(self):
        application_id = self.capture().data['id']

        self.admissions_api.post(f'/api/applications/{application_id}/nactvet-number/',
                                 {'nactvet_reg_no': 'NIT/PST/2026/0101'}, format='json')
        self.admissions_api.post(f'/api/applications/{application_id}/complete/',
                                 {'note': 'Taken on; number issued.'}, format='json')
        self.record(application_id)
        self.records.post(f'/api/applications/{application_id}/complete/',
                          {'note': 'Certificates scanned.'}, format='json')
        self.accounts.post(f'/api/applications/{application_id}/residence/', {'residence': 'day'},
                           format='json')
        self.accounts.post(f'/api/applications/{application_id}/complete/',
                           {'note': 'Receipt 4417 seen.'}, format='json')
        admitted = self.admissions_api.post(f'/api/applications/{application_id}/complete/',
                                            {}, format='json')

        self.assertEqual(admitted.status_code, 200, admitted.data)
        self.assertEqual(admitted.data['state'], Application.ADMITTED)
        self.assertEqual(admitted.data['reg_no'], 'NIT/PST/2026/0101')
        self.assertTrue(admitted.data['college_id'])
        self.assertIsNotNone(admitted.data['registration'])
        self.assertEqual([step['step'] for step in admitted.data['steps']],
                         [Application.INTAKE, Application.INFORMATION,
                          Application.FINANCE, Application.ADMISSION])

    def test_the_head_of_department_cannot_touch_the_queue(self):
        from rest_framework.test import APIClient
        from .views import set_roles

        hod_user = User.objects.create_user('hod', password='pw')
        set_roles(hod_user, ['hod'], full_name='Head')
        hod = APIClient(); hod.force_authenticate(hod_user)
        application_id = self.capture().data['id']

        self.assertEqual(hod.get('/api/applications/').status_code, 403)
        self.assertEqual(
            hod.post(f'/api/applications/{application_id}/complete/', {}, format='json').status_code,
            403)

    def test_each_office_sees_what_is_on_its_own_desk(self):
        application_id = self.capture().data['id']

        admission_queue = self.admissions_api.get('/api/applications/?mine=1')
        finance_queue = self.accounts.get('/api/applications/?mine=1')

        # It opens on the admission office's desk, not the accountant's.
        self.assertEqual(len(admission_queue.data), 1)
        self.assertEqual(finance_queue.data, [])

        self.admissions_api.post(f'/api/applications/{application_id}/complete/', {}, format='json')
        self.record(application_id)
        self.records.post(f'/api/applications/{application_id}/complete/', {}, format='json')

        self.assertEqual(len(self.accounts.get('/api/applications/?mine=1').data), 1)

    def test_the_queue_counts_tell_the_dashboards_where_the_work_is(self):
        self.capture()

        queue = self.admissions_api.get('/api/applications/queue/')

        self.assertTrue(queue.data['open'])
        self.assertEqual(queue.data['by_desk'], {Application.INTAKE: 1})
        self.assertEqual(queue.data['by_kind'], {Application.NEW: 1})

    def test_the_admission_officer_says_what_the_college_id_looks_like(self):
        response = self.admissions_api.get('/api/college-id-formats/current/')
        self.assertEqual(response.status_code, 200)

        saved = self.admissions_api.patch(
            f'/api/college-id-formats/{response.data["id"]}/',
            {'pattern': '{COLLEGE}/{PROG}/{YY}/{SEQ}', 'college_code': 'BPHACOH',
             'sequence_width': 4, 'starts_at': 50, 'next_number': 50}, format='json')

        self.assertEqual(saved.status_code, 200, saved.data)
        self.assertEqual(saved.data['example'], 'BPHACOH/PST/26/0050')

    def test_a_pattern_without_a_running_number_is_refused(self):
        current = self.admissions_api.get('/api/college-id-formats/current/').data

        refused = self.admissions_api.patch(f'/api/college-id-formats/{current["id"]}/',
                                            {'pattern': '{COLLEGE}/{PROG}/{YEAR}'}, format='json')

        self.assertEqual(refused.status_code, 400)

    def test_the_accountant_cannot_set_the_admission_rules(self):
        refused = self.accounts.post('/api/admission-requirements/',
                                     {'name': 'Calculator'}, format='json')

        self.assertEqual(refused.status_code, 403)

    def test_a_requirement_is_recorded_and_charged_from_the_desk(self):
        book = self.fee('TPH Book', '35000', applies=ChargeType.ON_REQUEST,
                        period=FeeStructure.ONCE)
        requirement = AdmissionRequirement.objects.create(name='TPH Book', charge_type=book)
        application_id = self.capture().data['id']
        self.admissions_api.post(f'/api/applications/{application_id}/complete/', {}, format='json')
        at_records = self.records.post(f'/api/applications/{application_id}/requirements/',
                                       {'requirement_id': requirement.id,
                                        'status': RequirementCheck.MISSING}, format='json')
        self.record(application_id)
        self.records.post(f'/api/applications/{application_id}/complete/', {}, format='json')

        listed = self.accounts.get(f'/api/applications/{application_id}/requirements/')
        recorded = self.accounts.post(f'/api/applications/{application_id}/requirements/',
                                      {'requirement_id': requirement.id,
                                       'status': RequirementCheck.MISSING}, format='json')

        # Records handles no money: items are checked at the finance desk.
        self.assertEqual(at_records.status_code, 400)
        self.assertEqual([row['name'] for row in listed.data], ['TPH Book'])
        self.assertEqual(recorded.status_code, 200, recorded.data)
        self.assertEqual(recorded.data['charge_amount'], '35000.00')

    def test_the_due_back_list_is_what_admission_works_from(self):
        from . import progression

        returning = self.applicant('NIT/PST/2025/077', 'Coming Back')
        progression.set_standing(
            returning, StudentStanding.DISCONTINUED, actor=self.admin,
            class_level=self.level4, programme=self.pst,
            return_year=self.year, return_semester_number=1)

        response = self.admissions_api.get('/api/applications/due-back/')

        self.assertEqual([row['reg_no'] for row in response.data], ['NIT/PST/2025/077'])

    def test_marking_something_missing_with_no_rate_says_so(self):
        requirement = AdmissionRequirement.objects.create(name='Rim paper')  # no charge type
        application_id = self.capture().data['id']
        self.admissions_api.post(f'/api/applications/{application_id}/complete/', {}, format='json')
        self.record(application_id)
        self.records.post(f'/api/applications/{application_id}/complete/', {}, format='json')

        recorded = self.accounts.post(f'/api/applications/{application_id}/requirements/',
                                     {'requirement_id': requirement.id,
                                      'status': RequirementCheck.MISSING}, format='json')

        self.assertEqual(recorded.status_code, 200)
        self.assertIn('nothing was charged', recorded.data['warning'])


# ── the first year's route: admission, records, finance, admission ───────────

class FirstYearRouteTests(AdmissionBase):
    def test_a_first_year_starts_at_the_admission_desk(self):
        profile = self.applicant()

        application = admissions.open_application(
            profile=profile, semester=self.sem1, programme=self.pst,
            class_level=self.level4, kind=Application.NEW, actor=self.officer)

        self.assertEqual(application.state, Application.INTAKE)
        self.assertEqual(application.route,
                         [Application.INTAKE, Application.INFORMATION,
                          Application.FINANCE, Application.ADMISSION])

    def test_a_first_year_finishes_where_they_started(self):
        profile = self.applicant()
        application = admissions.open_application(
            profile=profile, semester=self.sem1, programme=self.pst,
            class_level=self.level4, kind=Application.NEW, actor=self.officer)

        admissions.complete_step(application, actor=self.officer)   # intake
        self.assertEqual(application.state, Application.INFORMATION)
        admissions.complete_step(application, actor=self.officer)   # records
        self.assertEqual(application.state, Application.FINANCE)
        admissions.set_residence(application, 'day', actor=self.officer)
        admissions.complete_step(application, actor=self.officer)   # finance
        self.assertEqual(application.state, Application.ADMISSION)
        admissions.complete_step(application, actor=self.officer)
        self.assertEqual(application.state, Application.ADMITTED)

    def test_nobody_is_billed_before_finance(self):
        tuition = self.fee('Tuition Fee', '1000000')
        profile = self.applicant()
        application = admissions.open_application(
            profile=profile, semester=self.sem1, programme=self.pst,
            class_level=self.level4, kind=Application.NEW, actor=self.officer)

        # Intake and records handle no money at all.
        self.assertFalse(StudentCharge.objects.filter(profile=profile).exists())
        admissions.complete_step(application, actor=self.officer)   # intake → records
        self.assertFalse(StudentCharge.objects.filter(profile=profile).exists())
        admissions.complete_step(application, actor=self.officer)   # records → finance

        self.assertTrue(StudentCharge.objects.filter(profile=profile, charge_type=tuition).exists())

    def test_a_student_with_no_number_yet_is_held_under_one_of_the_college_s_own(self):
        profile = admissions.capture_student(name='Yet Unnumbered', actor=self.officer,
                                             nactvet_reg_no='')

        self.assertTrue(admissions.is_temporary(profile.nactvet_reg_no))
        self.assertTrue(profile.nactvet_reg_no.startswith('TEMP/'))

    def test_the_number_the_authority_issues_replaces_the_temporary_one(self):
        profile = admissions.capture_student(name='Yet Unnumbered', actor=self.officer,
                                             nactvet_reg_no='')
        was = profile.nactvet_reg_no
        module = self.module('PST04101', self.level4, self.sem1)
        enrollment = Student.objects.create(nactvet_reg_no=was, name=profile.name,
                                            profile=profile, module=module)

        admissions.set_nactvet_number(profile, 'nit/pst/2026/0031', actor=self.officer)

        profile.refresh_from_db(); enrollment.refresh_from_db()
        self.assertEqual(profile.nactvet_reg_no, 'NIT/PST/2026/0031')
        # The enrollment carries a copy of the number, so it moves with it.
        self.assertEqual(enrollment.nactvet_reg_no, 'NIT/PST/2026/0031')

    def test_a_number_that_belongs_to_somebody_else_is_refused(self):
        StudentProfile.objects.create(nactvet_reg_no='NIT/PST/2026/0031', name='Somebody Else')
        profile = admissions.capture_student(name='Yet Unnumbered', actor=self.officer,
                                             nactvet_reg_no='')

        with self.assertRaises(admissions.AdmissionError):
            admissions.set_nactvet_number(profile, 'NIT/PST/2026/0031', actor=self.officer)

    def test_the_admission_office_holds_the_intake_desk(self):
        from rest_framework.test import APIClient
        from .views import set_roles

        records_user = User.objects.create_user('records2', password='pw')
        set_roles(records_user, ['records_officer'], full_name='Records')
        records = APIClient(); records.force_authenticate(records_user)
        admission_user = User.objects.create_user('admission2', password='pw')
        set_roles(admission_user, ['admission_officer'], full_name='Admission')
        admission = APIClient(); admission.force_authenticate(admission_user)

        profile = self.applicant()
        application = admissions.open_application(
            profile=profile, semester=self.sem1, programme=self.pst,
            class_level=self.level4, kind=Application.NEW, actor=self.officer)

        refused = records.post(f'/api/applications/{application.id}/complete/', {}, format='json')
        numbered = admission.post(f'/api/applications/{application.id}/nactvet-number/',
                                  {'nactvet_reg_no': 'NIT/PST/2026/0044'}, format='json')
        allowed = admission.post(f'/api/applications/{application.id}/complete/', {}, format='json')

        # Records does not see a first year still at intake at all.
        self.assertEqual(refused.status_code, 404)
        self.assertEqual(numbered.status_code, 200, numbered.data)
        self.assertEqual(numbered.data['reg_no'], 'NIT/PST/2026/0044')
        self.assertEqual(allowed.data['state'], Application.INFORMATION)


# ── the papers a student hands in ─────────────────────────────────────────────

class StudentDocumentTests(AdmissionBase):
    def setUp(self):
        super().setUp()
        from rest_framework.test import APIClient
        from .views import set_roles

        self.records_user = User.objects.create_user('records3', password='pw')
        set_roles(self.records_user, ['records_officer'], full_name='Records')
        self.records = APIClient(); self.records.force_authenticate(self.records_user)

        self.profile = self.applicant('NIT/PST/2026/0200', 'Zuhura Ally')
        self.enrollment = Student.objects.create(
            nactvet_reg_no=self.profile.nactvet_reg_no, name=self.profile.name,
            profile=self.profile, module=self.module('PST04101', self.level4, self.sem1))
        self.enrollment.set_portal_pin('Portal#2026', require_change=False)
        self.enrollment.save()

    def upload(self, client, url, **extra):
        from django.core.files.uploadedfile import SimpleUploadedFile

        scan = SimpleUploadedFile('certificate.pdf', b'%PDF-1.4 a scan', content_type='application/pdf')
        return client.post(url, {'kind': 'certificate', 'file': scan, **extra}, format='multipart')

    def test_the_records_desk_keeps_a_document_against_the_student(self):
        response = self.upload(self.records, '/api/student-documents/',
                               profile=self.profile.id, note='Form four certificate')

        self.assertEqual(response.status_code, 201, response.data)
        document = StudentDocument.objects.get(profile=self.profile)
        self.assertEqual(document.kind, StudentDocument.CERTIFICATE)
        self.assertEqual(document.uploaded_by, self.records_user)
        self.assertFalse(document.uploaded_by_student)
        self.assertEqual(document.original_name, 'certificate.pdf')

    def test_a_student_uploads_their_own_from_the_portal(self):
        session = self.client.session
        session['student_id'] = self.enrollment.id
        session.save()

        response = self.upload(self.client, '/api/my-documents/')

        self.assertEqual(response.status_code, 201, response.data)
        document = StudentDocument.objects.get(profile=self.profile)
        self.assertTrue(document.uploaded_by_student)
        self.assertIsNone(document.uploaded_by)
        # And they see their own back.
        listed = self.client.get('/api/my-documents/')
        self.assertEqual([row['id'] for row in listed.data], [document.id])

    def test_the_records_desk_verifies_what_a_student_uploaded(self):
        session = self.client.session
        session['student_id'] = self.enrollment.id
        session.save()
        self.upload(self.client, '/api/my-documents/')
        document = StudentDocument.objects.get(profile=self.profile)

        verified = self.records.post(f'/api/student-documents/{document.id}/verify/', {}, format='json')

        self.assertEqual(verified.status_code, 200)
        document.refresh_from_db()
        self.assertTrue(document.is_verified)
        self.assertEqual(document.verified_by, self.records_user)

    def test_one_student_cannot_read_another_student_s_papers(self):
        self.upload(self.records, '/api/student-documents/', profile=self.profile.id)
        document = StudentDocument.objects.get(profile=self.profile)
        other = self.applicant('NIT/PST/2026/0201', 'Someone Else')
        other_enrollment = Student.objects.create(
            nactvet_reg_no=other.nactvet_reg_no, name=other.name, profile=other,
            module=self.module('PST04102', self.level4, self.sem1))
        session = self.client.session
        session['student_id'] = other_enrollment.id
        session.save()

        response = self.client.get(reverse('student-document-download', args=[document.id]))

        self.assertEqual(response.status_code, 404)

    def test_the_owner_downloads_their_own(self):
        self.upload(self.records, '/api/student-documents/', profile=self.profile.id)
        document = StudentDocument.objects.get(profile=self.profile)
        session = self.client.session
        session['student_id'] = self.enrollment.id
        session.save()

        response = self.client.get(reverse('student-document-download', args=[document.id]))

        self.assertEqual(response.status_code, 200)

    def test_a_signed_out_stranger_gets_nothing(self):
        self.upload(self.records, '/api/student-documents/', profile=self.profile.id)
        document = StudentDocument.objects.get(profile=self.profile)

        response = self.client.get(f'/student-documents/{document.id}/file/')

        self.assertEqual(response.status_code, 404)


# ── the password a student leaves with ────────────────────────────────────────

class PortalPinTests(AdmissionBase):
    def setUp(self):
        super().setUp()
        from rest_framework.test import APIClient
        from .views import set_roles

        self.fee('Tuition Fee', '1000000')
        self.module('PST04101', self.level4, self.sem1)
        self.admission_user = User.objects.create_user('admission4', password='pw')
        set_roles(self.admission_user, ['admission_officer'], full_name='Admission')
        self.api = APIClient(); self.api.force_authenticate(self.admission_user)

    def admit(self, reg_no='NACTVET/PENDING/300'):
        profile = self.applicant(reg_no)
        application = admissions.open_application(
            profile=profile, semester=self.sem1, programme=self.pst,
            class_level=self.level4, kind=Application.NEW, actor=self.officer)
        # intake → records → finance → admission, which admits them.
        for _ in range(4):
            if application.state == Application.FINANCE:
                admissions.set_residence(application, 'day', actor=self.officer)
            admissions.complete_step(application, actor=self.officer)
        return profile, application

    def test_an_admitted_student_leaves_with_a_password(self):
        profile, application = self.admit()

        pin = application.portal_pin
        self.assertTrue(pin, 'the admission should hand over a password')
        enrollment = Student.objects.get(profile=profile)
        self.assertTrue(enrollment.check_portal_pin(pin))
        # And they are made to change it the first time they sign in.
        self.assertTrue(enrollment.must_change_portal_password)

    def test_the_password_actually_signs_them_in(self):
        profile, application = self.admit()

        response = self.client.post('/login/', {'identifier': profile.nactvet_reg_no,
                                                'secret': application.portal_pin})

        self.assertEqual(response.status_code, 302)
        self.assertIn('student_id', self.client.session)

    def test_the_desk_is_shown_the_password_once(self):
        profile = self.applicant('NACTVET/PENDING/301')
        application = admissions.open_application(
            profile=profile, semester=self.sem1, programme=self.pst,
            class_level=self.level4, kind=Application.NEW, actor=self.officer)
        admissions.complete_step(application, actor=self.officer)   # intake
        admissions.complete_step(application, actor=self.officer)   # records
        admissions.set_residence(application, 'day', actor=self.officer)
        admissions.complete_step(application, actor=self.officer)   # finance

        admitted = self.api.post(f'/api/applications/{application.id}/complete/', {}, format='json')
        again = self.api.get(f'/api/applications/{application.id}/')

        self.assertEqual(admitted.status_code, 200, admitted.data)
        self.assertTrue(admitted.data['portal_pin'])
        # Only a hash is kept, so reading the application again shows nothing.
        self.assertNotIn('portal_pin', again.data)

    def test_a_returning_student_keeps_the_password_they_know(self):
        profile, application = self.admit()
        theirs = application.portal_pin
        enrollment = Student.objects.get(profile=profile)
        enrollment.set_portal_pin('TheirOwn#2026', require_change=False)
        enrollment.save()

        issued = admissions.issue_portal_pin(profile)

        self.assertIsNone(issued)
        enrollment.refresh_from_db()
        self.assertTrue(enrollment.check_portal_pin('TheirOwn#2026'))
        self.assertFalse(enrollment.check_portal_pin(theirs))

    def test_a_lost_password_is_replaced_from_the_desk(self):
        profile, application = self.admit()
        was = application.portal_pin

        response = self.api.post(f'/api/applications/{application.id}/reset-pin/', {}, format='json')

        self.assertEqual(response.status_code, 200, response.data)
        fresh = response.data['portal_pin']
        self.assertNotEqual(fresh, was)
        enrollment = Student.objects.get(profile=profile)
        self.assertTrue(enrollment.check_portal_pin(fresh))
        self.assertFalse(enrollment.check_portal_pin(was))

    def test_the_accountant_does_not_reset_passwords(self):
        from rest_framework.test import APIClient
        from .views import set_roles

        accountant = User.objects.create_user('money4', password='pw')
        set_roles(accountant, ['accountant'], full_name='Accounts')
        client = APIClient(); client.force_authenticate(accountant)
        _, application = self.admit()

        response = client.post(f'/api/applications/{application.id}/reset-pin/', {}, format='json')

        self.assertEqual(response.status_code, 403)

    def test_the_password_is_readable_when_it_is_read_aloud(self):
        # No letters that argue with digits: I/O against 1/0.
        pins = [admissions.generate_portal_pin() for _ in range(200)]

        self.assertTrue(all(len(pin) == 9 and pin[4] == '-' for pin in pins))
        self.assertFalse(any(set('IO01') & set(pin) for pin in pins))
        self.assertGreater(len(set(pins)), 190, 'the passwords should not repeat')


# ── who works admissions, and what each desk sees ─────────────────────────────

class OfficeClients(AdmissionBase):
    """One signed-in client per office."""

    def setUp(self):
        super().setUp()
        from rest_framework.test import APIClient
        from .views import set_roles

        self.fee('Tuition Fee', '1000000')
        self.module('PST04101', self.level4, self.sem1)

        def client(user):
            api = APIClient(); api.force_authenticate(user); return api

        def account(username, role):
            user = User.objects.create_user(username, password='pw')
            set_roles(user, [role], full_name=role)
            return client(user)

        self.exam = client(User.objects.create_superuser('exam9', 'x9@x.com', 'pw'))
        self.principal = account('principal9', 'principal')
        self.admission = account('admission9', 'admission_officer')
        self.records = account('records9', 'records_officer')
        self.accounts = account('money9', 'accountant')

    def open_first_year(self, client=None):
        response = (client or self.admission).post('/api/applications/capture/', {
            'kind': Application.NEW, 'name': 'Tumaini Laizer',
            'semester_id': self.sem1.id, 'programme_id': self.pst.id,
            'class_level_id': self.level4.id}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        return response.data['id']


class OfficeBoundaryTests(OfficeClients):
    def test_the_exam_officer_does_not_see_admissions_or_records(self):
        application_id = self.open_first_year()

        self.assertEqual(self.exam.get('/api/applications/').status_code, 403)
        self.assertEqual(self.exam.post(f'/api/applications/{application_id}/complete/',
                                        {}, format='json').status_code, 403)
        self.assertEqual(self.exam.get('/api/student-records/search/?search=Tumaini').status_code, 403)
        self.assertEqual(self.exam.get('/api/student-documents/').data, [])

    def test_the_principal_can_work_any_desk(self):
        application_id = self.open_first_year()

        response = self.principal.post(f'/api/applications/{application_id}/complete/', {}, format='json')

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['state'], Application.INFORMATION)

    def test_the_admission_officer_sets_the_window_and_the_exam_officer_cannot(self):
        today = date.today()
        payload = {'semester': self.sem2.id, 'opens_on': str(today), 'closes_on': str(today + timedelta(days=9))}

        refused = self.exam.post('/api/admission-windows/', payload, format='json')
        allowed = self.admission.post('/api/admission-windows/', payload, format='json')

        self.assertEqual(refused.status_code, 403)
        self.assertEqual(allowed.status_code, 201, allowed.data)

    def test_records_cannot_clear_its_desk_until_the_details_are_down(self):
        application_id = self.open_first_year()
        self.admission.post(f'/api/applications/{application_id}/complete/', {}, format='json')

        blocked = self.records.post(f'/api/applications/{application_id}/complete/', {}, format='json')

        self.assertEqual(blocked.status_code, 400)
        self.assertIn('gender', blocked.data['detail'])
        self.assertIn('next of kin', blocked.data['detail'])

    def test_records_takes_the_details_down_and_every_desk_reads_them(self):
        application_id = self.open_first_year()
        self.admission.post(f'/api/applications/{application_id}/complete/', {}, format='json')

        saved = self.records.post(f'/api/applications/{application_id}/details/', {
            'phone': '0715000100', 'gender': 'M', 'date_of_birth': '2005-11-02',
            'next_of_kin': [{'name': 'Mama Laizer', 'phone': '0715000101', 'relationship': 'parent'},
                            {'name': 'Kaka Laizer', 'phone': '0715000102', 'relationship': 'sibling'}],
        }, format='json')
        cleared = self.records.post(f'/api/applications/{application_id}/complete/', {}, format='json')
        seen_by_finance = self.accounts.get(f'/api/applications/{application_id}/')

        self.assertEqual(saved.status_code, 200, saved.data)
        self.assertEqual(saved.data['missing_details'], [])
        self.assertEqual(cleared.data['state'], Application.FINANCE)
        details = seen_by_finance.data['details']
        self.assertEqual(details['gender'], 'M')
        self.assertEqual([kin['name'] for kin in details['next_of_kin']], ['Mama Laizer', 'Kaka Laizer'])

    def test_only_records_writes_the_details(self):
        application_id = self.open_first_year()

        for client in (self.admission, self.exam):
            response = client.post(f'/api/applications/{application_id}/details/',
                                   {'gender': 'F'}, format='json')
            self.assertEqual(response.status_code, 403)
        # Finance does not see a first year at intake.
        self.assertEqual(self.accounts.post(f'/api/applications/{application_id}/details/',
                                            {'gender': 'F'}, format='json').status_code, 404)

    def test_finance_sees_a_first_year_is_billed_full_fees(self):
        application_id = self.open_first_year()
        application = Application.objects.get(pk=application_id)
        admissions.record_details(application.profile, phone='0715000100', gender='M',
                                  date_of_birth=date(2005, 11, 2), actor=self.officer,
                                  next_of_kin=[{'name': 'A', 'phone': '1', 'relationship': 'parent'},
                                               {'name': 'B', 'phone': '2', 'relationship': 'parent'}])
        admissions.complete_step(application, actor=self.officer)       # intake
        admissions.complete_step(application, actor=self.officer)       # records → finance

        academic = self.accounts.get(f'/api/applications/{application_id}/').data['academic']

        self.assertIn('first year', academic['billing'])
        self.assertEqual(academic['module_count'], 1)
        self.assertTrue(academic['results_confirmed'])


class FinanceSeesResultsTests(AdmissionBase):
    """A continuing student reaches finance first. Finance has to know what the
    results said — moving on, or repeating — because that decides the bill."""

    def setUp(self):
        super().setUp()
        from rest_framework.test import APIClient
        from .views import set_roles
        from . import progression

        self.progression = progression
        self.fee('Tuition Fee', '1000000')
        charge_type = ChargeType.objects.create(
            name='Repeat Module Fee', family=ChargeType.FEE,
            declaration=ChargeType.REPEAT_MODULE, applies=ChargeType.ON_REQUEST)
        structure = FeeStructure.objects.create(
            charge_type=charge_type, programme=self.pst, class_level=self.level4,
            academic_year=self.year, amount=Decimal('50000'),
            billing_period=FeeStructure.ONCE, installments=1)
        finance.set_installment_schedule(structure, [date(2026, 11, 30)])
        self.repeat_type = charge_type

        # Last year, in the same shape the year end leaves it.
        self.old_year = AcademicYear.objects.create(name='2025/2026')
        self.old_sem1 = Semester.objects.create(academic_year=self.old_year, number=1)
        failed_module = Module.objects.create(name='PST04101', code='PST04101', teacher='T',
                                              class_level=self.level4, semester=self.old_sem1,
                                              programme=self.pst, credits=3)
        self.module('PST04101', self.level4, self.sem1)
        self.module('PST04102', self.level4, self.sem1)

        self.student = self.applicant('NIT/PST/2025/0300', 'Repeating Student')
        failed = Student.objects.create(nactvet_reg_no=self.student.nactvet_reg_no,
                                        name=self.student.name, profile=self.student,
                                        module=failed_module)
        from .models import OutstandingRepeat
        OutstandingRepeat.objects.create(
            profile=self.student, module_code='PST04101', module_name='PST04101',
            class_level=self.level4, semester_number=1,
            origin_semester=self.old_sem1, origin_enrollment=failed)
        progression.set_standing(self.student, StudentStanding.REPEATING,
                                 class_level=self.level4, programme=self.pst)
        review = SemesterReview.objects.create(
            profile=self.student, semester=self.old_sem1, class_level=self.level4,
            programme=self.pst, gpa=Decimal('2.33'), proposed=SemesterReview.REPEAT,
            confirmed=SemesterReview.REPEAT)

        accountant = User.objects.create_user('money10', password='pw')
        set_roles(accountant, ['accountant'], full_name='Accounts')
        self.accounts = APIClient(); self.accounts.force_authenticate(accountant)

    def test_finance_sees_the_results_and_that_it_bills_per_module(self):
        application = admissions.open_application(
            profile=self.student, semester=self.sem1, programme=self.pst,
            class_level=self.level4, kind=Application.CONTINUING, actor=self.officer)

        academic = self.accounts.get(f'/api/applications/{application.id}/').data['academic']

        self.assertIn('Repeats the failed module', academic['results'])
        self.assertIn('2.33', academic['results'])
        self.assertTrue(academic['repeat_only'])
        self.assertEqual(academic['billing'], 'Repeat rate × 1 module(s)')
        self.assertEqual([m['code'] for m in academic['modules']], ['PST04101'])

    def test_a_repeater_is_billed_the_repeat_rate_at_finance_not_a_year(self):
        application = admissions.open_application(
            profile=self.student, semester=self.sem1, programme=self.pst,
            class_level=self.level4, kind=Application.CONTINUING, actor=self.officer)

        charges = list(StudentCharge.objects.filter(profile=self.student, academic_year=self.year))

        self.assertEqual([(c.charge_type_id, c.amount) for c in charges],
                         [(self.repeat_type.id, Decimal('50000.00'))])
        self.assertEqual(application.state, Application.FINANCE)

    def test_admitting_a_repeater_enrolls_the_failed_module_only_and_bills_it_once(self):
        application = admissions.open_application(
            profile=self.student, semester=self.sem1, programme=self.pst,
            class_level=self.level4, kind=Application.CONTINUING, actor=self.officer)
        for _ in range(3):
            admissions.complete_step(application, actor=self.officer)

        registration = SemesterRegistration.objects.get(profile=self.student, semester=self.sem1)
        self.assertEqual(registration.kind, SemesterRegistration.REPEATING)
        sat = Student.objects.filter(profile=self.student, module__semester=self.sem1)
        self.assertEqual([row.module.code for row in sat], ['PST04101'])
        self.assertEqual(StudentCharge.objects.filter(
            profile=self.student, charge_type=self.repeat_type).count(), 1)

    def test_an_unconfirmed_review_is_shown_as_unconfirmed(self):
        SemesterReview.objects.filter(profile=self.student).update(confirmed='')
        application = admissions.open_application(
            profile=self.student, semester=self.sem1, programme=self.pst,
            class_level=self.level4, kind=Application.CONTINUING, actor=self.officer)

        academic = self.accounts.get(f'/api/applications/{application.id}/').data['academic']

        self.assertFalse(academic['results_confirmed'])
        self.assertIn('not confirmed', academic['results'])


# ── what holds a continuing student: not having paid, and nothing else ───────

class ContinuingHoldTests(AdmissionBase):
    """A continuing student pays, records double-checks them, and admission
    confirms they are here. Not having paid is the one thing that holds them;
    a gap in their details or a missing item does not."""

    def setUp(self):
        super().setUp()
        from datetime import date as _date
        self.module('PST05101', self.level5, self.sem1)
        # Tuition that must be paid before registering, already due.
        self.tuition = ChargeType.objects.create(
            name='Tuition Fee', family=ChargeType.FEE, blocks_registration=True)
        structure = FeeStructure.objects.create(
            charge_type=self.tuition, programme=self.pst, class_level=self.level5,
            academic_year=self.year, amount=Decimal('500000'),
            billing_period=FeeStructure.ACADEMIC_YEAR, installments=1)
        finance.set_installment_schedule(structure, [_date.today() - timedelta(days=1)])
        # A continuing student the college already has — details incomplete.
        self.student = StudentProfile.objects.create(nactvet_reg_no='NIT/PST/2025/0400',
                                                     name='Continuing Student')
        self.application = admissions.open_application(
            profile=self.student, semester=self.sem1, programme=self.pst,
            class_level=self.level5, kind=Application.CONTINUING, actor=self.officer)

    def test_not_having_paid_holds_them_at_finance(self):
        with self.assertRaises(admissions.AdmissionError) as held:
            admissions.complete_step(self.application, actor=self.officer)

        self.assertIn('Not paid', str(held.exception))
        self.assertIn('Tuition Fee', str(held.exception))
        self.assertEqual(self.application.state, Application.FINANCE)

    def test_paying_lets_them_through(self):
        finance.record_payment(self.student, Decimal('500000'), date.today(),
                               recorded_by=self.officer, bank_reference='CRDB-1')

        admissions.complete_step(self.application, actor=self.officer)

        self.assertEqual(self.application.state, Application.RECORDS)

    def test_an_accountant_override_lets_them_through_unpaid(self):
        from .models import FinanceOverride
        FinanceOverride.objects.create(
            profile=self.student, academic_year=self.year, period=ChargeType.REGISTRATION,
            reason='Sponsor letter received.', approved_by=self.officer)

        admissions.complete_step(self.application, actor=self.officer)

        self.assertEqual(self.application.state, Application.RECORDS)

    def test_missing_details_and_items_do_not_hold_a_continuing_student(self):
        from .models import FinanceOverride
        FinanceOverride.objects.create(
            profile=self.student, academic_year=self.year, period=ChargeType.REGISTRATION,
            reason='Paid at the bank; receipt to follow.', approved_by=self.officer)
        requirement = AdmissionRequirement.objects.create(name='Calculator')
        admissions.complete_step(self.application, actor=self.officer)        # finance
        admissions.record_requirement(self.application, requirement,
                                      RequirementCheck.MISSING, actor=self.officer)
        self.assertTrue(admissions.missing_details(self.student))            # still incomplete

        admissions.complete_step(self.application, actor=self.officer)        # records
        admissions.complete_step(self.application, actor=self.officer)        # admission

        self.assertEqual(self.application.state, Application.ADMITTED)
        self.assertTrue(SemesterRegistration.objects.filter(
            profile=self.student, semester=self.sem1).exists())


class OfficeVisibilityTests(OfficeClients):
    """Records sees the students at its own desks and those admitted; finance
    the same for its desk. Who is at intake, at another office or at the
    admission desk is the admission office's to see — and the Principal's."""

    def setUp(self):
        super().setUp()
        self.at_intake = Application.objects.get(pk=self.open_first_year())

        self.at_records_desk = Application.objects.get(pk=self.open_first_year())
        admissions.complete_step(self.at_records_desk, actor=self.officer)   # → information

        self.at_finance = Application.objects.get(pk=self.open_first_year())
        admissions.record_details(
            self.at_finance.profile, phone='0715000100', gender='F',
            date_of_birth=date(2006, 1, 5), actor=self.officer,
            next_of_kin=[{'name': 'A', 'phone': '1', 'relationship': 'parent'},
                         {'name': 'B', 'phone': '2', 'relationship': 'parent'}])
        self.at_finance.profile.name = 'Finance Waiting'
        self.at_finance.profile.save(update_fields=['name'])
        admissions.complete_step(self.at_finance, actor=self.officer)       # → information
        admissions.complete_step(self.at_finance, actor=self.officer)       # → finance

    def ids(self, client, query=''):
        response = client.get('/api/applications/' + query)
        self.assertEqual(response.status_code, 200, response.data)
        rows = response.data['results'] if isinstance(response.data, dict) else response.data
        return {row['id'] for row in rows}

    def test_records_sees_only_its_desk_and_the_admitted(self):
        seen = self.ids(self.records)

        self.assertEqual(seen, {self.at_records_desk.id})
        self.assertEqual(self.records.get(f'/api/applications/{self.at_intake.id}/').status_code, 404)
        self.assertEqual(self.records.get(f'/api/applications/{self.at_finance.id}/').status_code, 404)

    def test_finance_sees_only_its_desk(self):
        self.assertEqual(self.ids(self.accounts), {self.at_finance.id})

    def test_admission_and_the_principal_see_every_desk(self):
        everyone = {self.at_intake.id, self.at_records_desk.id, self.at_finance.id}

        self.assertEqual(self.ids(self.admission), everyone)
        self.assertEqual(self.ids(self.principal), everyone)

    def test_the_queue_counts_only_what_the_office_may_see(self):
        records = self.records.get('/api/applications/queue/').data
        admission = self.admission.get('/api/applications/queue/').data

        self.assertEqual(records['by_desk'], {Application.INFORMATION: 1})
        self.assertIsNone(records['due_back'])
        self.assertEqual(admission['by_desk'], {Application.INTAKE: 1, Application.INFORMATION: 1,
                                                Application.FINANCE: 1})
        self.assertEqual(self.records.get('/api/applications/due-back/').status_code, 403)

    def test_records_finds_a_continuing_student_but_not_a_first_year_elsewhere(self):
        continuing = StudentProfile.objects.create(nactvet_reg_no='NIT/PST/2025/0900',
                                                   name='Finance Continuing')
        Student.objects.create(nactvet_reg_no=continuing.nactvet_reg_no, name=continuing.name,
                               profile=continuing, module=self.module('PST04199', self.level4, self.sem1))
        admissions.open_application(profile=continuing, semester=self.sem1, programme=self.pst,
                                    class_level=self.level4, kind=Application.CONTINUING,
                                    actor=self.officer)

        found = self.records.get('/api/student-records/search/?search=Finance').data

        # The continuing student is the college's already, so records can
        # update them while they are still paying; the first year at finance
        # is not records' to see until admitted.
        self.assertEqual([row['name'] for row in found], ['Finance Continuing'])
        self.assertEqual(self.records.get(
            f'/api/student-records/{self.at_finance.profile_id}/').status_code, 404)
        self.assertEqual(len(self.admission.get('/api/student-records/search/?search=Finance').data), 2)


class QueueContinuingTests(OfficeClients):
    """A year opened without the advance still gets its continuing students
    queued at finance, from last year's confirmed review."""

    def setUp(self):
        super().setUp()
        self.last_year = AcademicYear.objects.create(name='2025/2026')
        self.last_sem2 = Semester.objects.create(academic_year=self.last_year, number=2)
        self.promoted = self.last_years_student('NIT/PST/2025/0001', 'Promoted One', SemesterReview.CLEAR)
        self.undecided = self.last_years_student('NIT/PST/2025/0002', 'Undecided Two', None)

    def last_years_student(self, reg_no, name, outcome):
        profile = StudentProfile.objects.create(nactvet_reg_no=reg_no, name=name)
        SemesterRegistration.objects.create(profile=profile, semester=self.last_sem2,
                                            programme=self.pst, class_level=self.level4,
                                            kind=SemesterRegistration.CONTINUING)
        SemesterReview.objects.create(profile=profile, semester=self.last_sem2, programme=self.pst,
                                      class_level=self.level4, proposed=SemesterReview.CLEAR,
                                      confirmed=outcome or '')
        return profile

    def test_the_promoted_student_waits_at_finance_for_the_next_level(self):
        response = self.admission.post('/api/applications/queue-continuing/', {}, format='json')

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['queued'], 1)
        self.assertEqual([row['reg_no'] for row in response.data['blocked']], ['NIT/PST/2025/0002'])
        application = Application.objects.get(profile=self.promoted)
        self.assertEqual(application.state, Application.FINANCE)
        self.assertEqual(application.kind, Application.CONTINUING)
        self.assertEqual(application.class_level, self.level5)
        self.assertEqual(application.semester, self.sem1)
        self.assertFalse(Application.objects.filter(profile=self.undecided).exists())
        # Nobody is registered by queueing.
        self.assertFalse(SemesterRegistration.objects.filter(semester=self.sem1).exists())

    def test_running_it_again_queues_nobody_twice(self):
        self.admission.post('/api/applications/queue-continuing/', {}, format='json')

        again = self.admission.post('/api/applications/queue-continuing/', {}, format='json').data

        self.assertEqual(again['queued'], 0)
        self.assertEqual(again['already_applied'], 1)
        self.assertEqual(Application.objects.filter(profile=self.promoted).count(), 1)

    def test_a_student_already_registered_is_left_alone(self):
        SemesterRegistration.objects.create(profile=self.promoted, semester=self.sem1,
                                            programme=self.pst, class_level=self.level5,
                                            kind=SemesterRegistration.CONTINUING)

        result = self.admission.post('/api/applications/queue-continuing/', {}, format='json').data

        self.assertEqual(result['queued'], 0)
        self.assertEqual(result['already_registered'], 1)

    def test_only_admission_and_the_principal_queue_them(self):
        for client in (self.records, self.accounts, self.exam):
            response = client.post('/api/applications/queue-continuing/', {}, format='json')
            self.assertEqual(response.status_code, 403)
        self.assertEqual(self.principal.post('/api/applications/queue-continuing/', {},
                                             format='json').status_code, 200)

    def test_finance_sees_them_queued(self):
        self.admission.post('/api/applications/queue-continuing/', {}, format='json')

        queue = self.accounts.get('/api/applications/queue/').data

        self.assertEqual(queue['by_desk'], {Application.FINANCE: 1})


class PortalRecordTests(AdmissionBase):
    """A student reads the details records keeps on them."""

    def setUp(self):
        super().setUp()
        self.profile = self.applicant('NIT/PST/2026/0300', 'Neema Kileo')
        self.profile.college_id = 'BPH/PST/2026/007'
        self.profile.save(update_fields=['college_id'])
        self.enrollment = Student.objects.create(
            nactvet_reg_no=self.profile.nactvet_reg_no, name=self.profile.name,
            profile=self.profile, module=self.module('PST04101', self.level4, self.sem1))
        self.enrollment.set_portal_pin('Portal#2026', require_change=False)
        self.enrollment.save()
        session = self.client.session
        session['student_id'] = self.enrollment.id
        session.save()

    def test_the_profile_shows_what_records_holds(self):
        page = self.client.get('/student-dashboard/')

        self.assertEqual(page.status_code, 200)
        record = page.context['record']
        self.assertEqual(record['college_id'], 'BPH/PST/2026/007')
        self.assertEqual(record['gender'], 'Female')
        self.assertEqual([kin['name'] for kin in record['next_of_kin']], ['Mama Mtui', 'Baba Mtui'])
        self.assertEqual(record['missing'], [])
        self.assertContains(page, 'Baba Mtui')
        self.assertContains(page, '0713000222')

    def test_a_gap_is_shown_to_the_student(self):
        NextOfKin.objects.filter(profile=self.profile).delete()

        page = self.client.get('/student-dashboard/')

        self.assertIn('next of kin', ', '.join(page.context['record']['missing']))
        self.assertContains(page, 'Not recorded yet')

    def test_an_o_level_certificate_is_filed_as_one(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        scan = SimpleUploadedFile('csee.pdf', b'%PDF-1.4 a scan', content_type='application/pdf')
        response = self.client.post('/api/my-documents/', {'kind': StudentDocument.O_LEVEL, 'file': scan},
                                    format='multipart')

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(StudentDocument.objects.get(profile=self.profile).get_kind_display(),
                         'O-level certificate (CSEE)')


class ItemsTests(OfficeClients):
    """The TPH book, a calculator, rim paper, insurance: finance checks them,
    one missing is a debt and never a hold, the admission desk sees what is
    owed and admits or holds, and semester 2 is checked again."""

    def setUp(self):
        super().setUp()
        self.module('PST05101', self.level5, self.sem1)
        self.module('PST05201', self.level5, self.sem2)
        self.tuition = ChargeType.objects.create(
            name='Tuition Fee 2', family=ChargeType.FEE, blocks_registration=True)
        tuition = FeeStructure.objects.create(
            charge_type=self.tuition, programme=self.pst, class_level=self.level5,
            academic_year=self.year, amount=Decimal('500000'),
            billing_period=FeeStructure.ACADEMIC_YEAR, installments=1)
        finance.set_installment_schedule(tuition, [date.today() - timedelta(days=1)])
        # The medical fee is on everybody's bill, in two instalments, and the
        # admission form marks it as blocking registration.
        self.medical = ChargeType.objects.create(
            name='Medical fees (Health Insurance)', family=ChargeType.DIRECT_COST,
            blocks_registration=True)
        medical = FeeStructure.objects.create(
            charge_type=self.medical, programme=self.pst, class_level=self.level5,
            academic_year=self.year, amount=Decimal('60000'),
            billing_period=FeeStructure.ACADEMIC_YEAR, installments=2)
        finance.set_installment_schedule(medical, [date.today() - timedelta(days=1),
                                                   date.today() + timedelta(days=60)])
        # The TPH book is charged only to a student without one — and even
        # flagged as blocking, it must not hold anybody.
        self.book = ChargeType.objects.create(
            name='TPH Book', family=ChargeType.OTHER, applies=ChargeType.ON_REQUEST,
            blocks_registration=True, blocks_final=True)
        book = FeeStructure.objects.create(
            charge_type=self.book, programme=self.pst, class_level=self.level5,
            academic_year=self.year, amount=Decimal('35000'),
            billing_period=FeeStructure.ACADEMIC_YEAR, installments=1)
        finance.set_installment_schedule(book, [date.today() - timedelta(days=1)])

        self.tph = AdmissionRequirement.objects.create(name='TPH Book', charge_type=self.book)
        self.insurance = AdmissionRequirement.objects.create(
            name='Insurance', charge_type=self.medical,
            frequency=AdmissionRequirement.EVERY_YEAR)

        self.student = StudentProfile.objects.create(nactvet_reg_no='NIT/PST/2025/0500',
                                                     name='Items Student')
        self.application = admissions.open_application(
            profile=self.student, semester=self.sem1, programme=self.pst,
            class_level=self.level5, kind=Application.CONTINUING, actor=self.officer)

    def pay_tuition(self):
        # Tuition, and the medical fee instalment already due: a payment with no
        # invoice settles the oldest charges first.
        finance.record_payment(self.student, Decimal('530000'), date.today(),
                               recorded_by=self.officer, bank_reference='CRDB-9')

    def mark(self, client, requirement, status):
        return client.post(f'/api/applications/{self.application.id}/requirements/',
                           {'requirement_id': requirement.id, 'status': status}, format='json')

    def medical_charges(self):
        return list(finance.with_balances(StudentCharge.objects.filter(
            profile=self.student, charge_type=self.medical)))

    def test_finance_checks_a_continuing_student_s_items_at_its_desk(self):
        response = self.mark(self.accounts, self.tph, RequirementCheck.MISSING)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['charge_amount'], '35000.00')

    def test_a_missing_item_is_a_debt_and_never_holds_them(self):
        self.mark(self.accounts, self.tph, RequirementCheck.MISSING)
        self.mark(self.accounts, self.insurance, RequirementCheck.MISSING)
        self.pay_tuition()

        cleared = self.accounts.post(f'/api/applications/{self.application.id}/complete/', {},
                                     format='json')

        self.assertEqual(cleared.status_code, 200, cleared.data)
        self.assertEqual(cleared.data['state'], Application.RECORDS)
        # Owed all the same, and not a hold on exams either.
        self.assertGreater(finance.balance_for(self.student, self.year)['balance'], 0)
        final = finance.clearance_map([self.student], self.year, [ChargeType.FINAL])
        self.assertTrue(final[self.student.id][ChargeType.FINAL]['cleared'])

    def test_a_student_with_their_own_insurance_has_the_medical_fee_waived(self):
        self.assertEqual(len(self.medical_charges()), 2)

        self.mark(self.accounts, self.insurance, RequirementCheck.HAS_IT)

        self.assertTrue(all(finance.charge_balance(charge) == 0 for charge in self.medical_charges()))
        # Changing their mind puts the fee back.
        self.mark(self.accounts, self.insurance, RequirementCheck.MISSING)
        self.assertEqual(sum(finance.charge_balance(charge) for charge in self.medical_charges()),
                         Decimal('60000.00'))

    def test_an_accountant_s_own_waiver_is_left_alone(self):
        first = self.medical_charges()[0]
        finance.waive_charge(first, Decimal('10000'), 'Bursary', actor=self.officer)

        self.mark(self.accounts, self.insurance, RequirementCheck.MISSING)

        first.refresh_from_db()
        self.assertEqual(first.waived_amount, Decimal('10000.00'))
        self.assertEqual(first.waived_reason, 'Bursary')

    def through_to_admission(self):
        self.pay_tuition()
        admissions.set_residence(self.application, 'day', actor=self.officer)
        admissions.complete_step(self.application, actor=self.officer)   # finance
        admissions.complete_step(self.application, actor=self.officer)   # records
        self.assertEqual(self.application.state, Application.ADMISSION)

    def test_the_admission_desk_sees_what_is_owed_and_holds_or_admits(self):
        self.mark(self.accounts, self.tph, RequirementCheck.MISSING)
        self.through_to_admission()

        opened = self.admission.get(f'/api/applications/{self.application.id}/').data
        no_reason = self.admission.post(f'/api/applications/{self.application.id}/hold/',
                                        {}, format='json')
        held = self.admission.post(f'/api/applications/{self.application.id}/hold/',
                                   {'reason': 'Bring the TPH book'}, format='json')
        admitted = self.admission.post(f'/api/applications/{self.application.id}/complete/',
                                       {}, format='json')

        self.assertEqual([(row['name'], row['balance'], row['paid']) for row in opened['items']['owed']],
                         [('TPH Book', '35000.00', False)])
        self.assertEqual(opened['items']['unchecked'], ['Insurance'])
        self.assertEqual(no_reason.status_code, 400)
        self.assertTrue(held.data['on_hold'])
        self.assertEqual(held.data['hold_reason'], 'Bring the TPH book')
        self.assertEqual(admitted.data['state'], Application.ADMITTED)
        self.assertFalse(admitted.data['on_hold'])

    def test_only_the_admission_desk_holds(self):
        self.through_to_admission()

        for client in (self.records, self.accounts):
            response = client.post(f'/api/applications/{self.application.id}/hold/',
                                   {'reason': 'x'}, format='json')
            self.assertIn(response.status_code, (403, 404))
        early = Application.objects.get(pk=self.open_first_year())
        refused = self.admission.post(f'/api/applications/{early.id}/hold/', {'reason': 'x'},
                                      format='json')
        self.assertEqual(refused.status_code, 400)

    def test_an_item_brought_to_the_admission_desk_takes_it_off_the_bill(self):
        self.mark(self.accounts, self.tph, RequirementCheck.MISSING)
        self.through_to_admission()
        self.admission.post(f'/api/applications/{self.application.id}/hold/',
                            {'reason': 'Bring the TPH book'}, format='json')

        brought = self.mark(self.admission, self.tph, RequirementCheck.HAS_IT)

        self.assertEqual(brought.status_code, 200, brought.data)
        charge = finance.with_balances(StudentCharge.objects.filter(charge_type=self.book)).get()
        self.assertEqual(finance.charge_balance(charge), 0)
        owed = self.admission.get(f'/api/applications/{self.application.id}/').data['items']['owed']
        self.assertEqual(owed, [])

    def test_records_marks_no_items(self):
        self.pay_tuition()
        admissions.complete_step(self.application, actor=self.officer)   # → records

        response = self.mark(self.records, self.tph, RequirementCheck.MISSING)

        self.assertEqual(response.status_code, 400)
        self.assertFalse(StudentCharge.objects.filter(charge_type=self.book).exists())

    # ── semester 2 ────────────────────────────────────────────────────────────

    def admitted_into_semester_2(self):
        self.mark(self.accounts, self.tph, RequirementCheck.HAS_IT)
        self.mark(self.accounts, self.insurance, RequirementCheck.HAS_IT)
        self.through_to_admission()
        admissions.complete_step(self.application, actor=self.officer)   # admitted
        return SemesterRegistration.objects.create(
            profile=self.student, semester=self.sem2, programme=self.pst,
            class_level=self.level5, kind=SemesterRegistration.CONTINUING)

    def test_semester_2_asks_again_for_every_semester_items_only(self):
        self.admitted_into_semester_2()

        listed = self.accounts.get(f'/api/item-checks/?semester_id={self.sem2.id}').data

        row = listed['results'][0]
        self.assertEqual(row['reg_no'], 'NIT/PST/2025/0500')
        # Insurance is checked once a year; the book every semester.
        self.assertEqual([item['name'] for item in row['items']], ['TPH Book'])
        self.assertEqual(row['unchecked'], 1)

    def test_semester_2_missing_item_is_charged_to_semester_2(self):
        self.admitted_into_semester_2()

        response = self.accounts.post('/api/item-checks/', {
            'semester_id': self.sem2.id, 'profile_id': self.student.id,
            'requirement_id': self.tph.id, 'status': RequirementCheck.MISSING}, format='json')

        self.assertEqual(response.status_code, 200, response.data)
        charge = StudentCharge.objects.get(charge_type=self.book)
        self.assertEqual(charge.semester, self.sem2)
        self.assertEqual(charge.amount, Decimal('35000.00'))
        # And the semester 1 check is still there, untouched.
        self.assertEqual(RequirementCheck.objects.get(semester=self.sem1, requirement=self.tph).status,
                         RequirementCheck.HAS_IT)

    def test_only_finance_runs_the_items_check(self):
        self.admitted_into_semester_2()

        for client in (self.records, self.admission, self.exam):
            self.assertEqual(client.get(f'/api/item-checks/?semester_id={self.sem2.id}').status_code, 403)
        self.assertEqual(self.principal.get(f'/api/item-checks/?semester_id={self.sem2.id}').status_code, 200)

    def test_a_student_still_at_admissions_is_checked_on_their_application(self):
        SemesterRegistration.objects.create(
            profile=self.student, semester=self.sem1, programme=self.pst,
            class_level=self.level5, kind=SemesterRegistration.CONTINUING)

        response = self.accounts.post('/api/item-checks/', {
            'semester_id': self.sem1.id, 'profile_id': self.student.id,
            'requirement_id': self.tph.id, 'status': RequirementCheck.MISSING}, format='json')

        self.assertEqual(response.status_code, 400)


class ItemSetupTests(OfficeClients):
    """The admission officer defines the items; the accountant's charge types
    and rates decide what a missing one costs."""

    def test_the_admission_officer_defines_an_item(self):
        book = self.fee('TPH Book', '35000', level=self.level4, applies=ChargeType.ON_REQUEST)

        options = self.admission.get('/api/admission-requirements/charge-types/').data
        created = self.admission.post('/api/admission-requirements/', {
            'name': 'TPH Book', 'charge_type': book.id, 'frequency': AdmissionRequirement.EVERY_SEMESTER,
            'applies_to_levels': [self.level4.id]}, format='json')

        tph = next(row for row in options if row['name'] == 'TPH Book')
        self.assertEqual(tph['rated_levels'], ['NTA Level 4'])
        self.assertEqual(created.status_code, 201, created.data)
        self.assertEqual(created.data['level_names'], ['NTA Level 4'])
        self.assertEqual(created.data['charge_type_name'], 'TPH Book')

    def test_only_admission_and_the_principal_define_items(self):
        for client in (self.records, self.accounts):
            response = client.post('/api/admission-requirements/', {'name': 'Calculator'}, format='json')
            self.assertEqual(response.status_code, 403)
        self.assertEqual(self.principal.post('/api/admission-requirements/', {'name': 'Calculator'},
                                             format='json').status_code, 201)
        self.assertEqual(self.exam.get('/api/admission-requirements/charge-types/').status_code, 403)

    def test_an_item_already_checked_is_switched_off_not_deleted(self):
        used = AdmissionRequirement.objects.create(name='Rim paper')
        unused = AdmissionRequirement.objects.create(name='Lab coat')
        profile = self.applicant()
        admissions.record_item(profile, self.sem1, used, RequirementCheck.HAS_IT,
                               class_level=self.level4, programme=self.pst, actor=self.officer)

        refused = self.admission.delete(f'/api/admission-requirements/{used.id}/')
        deleted = self.admission.delete(f'/api/admission-requirements/{unused.id}/')
        switched_off = self.admission.patch(f'/api/admission-requirements/{used.id}/',
                                            {'is_active': False}, format='json')

        self.assertEqual(refused.status_code, 400)
        self.assertIn('Switch it off', str(refused.data))
        self.assertEqual(deleted.status_code, 204)
        self.assertFalse(switched_off.data['is_active'])
        self.assertEqual(RequirementCheck.objects.filter(requirement=used).count(), 1)
