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
    Programme, RequirementCheck, Semester, SemesterRegistration, Student, StudentCharge,
    StudentDocument, StudentProfile, StudentStanding,
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
        payload = {
            'kind': Application.NEW, 'name': 'Rehema Mtui',
            'nactvet_reg_no': 'NACTVET/PENDING/009', 'phone': '0712000111', 'gender': 'F',
            'semester_id': self.sem1.id, 'programme_id': self.pst.id,
            'class_level_id': self.level4.id,
            'next_of_kin': [{'name': 'Mama Mtui', 'phone': '0713', 'relationship': 'parent'}],
            **extra,
        }
        return (client or self.records).post('/api/applications/capture/', payload, format='json')

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
        self.records.post(f'/api/applications/{application_id}/complete/', {}, format='json')

        refused = self.records.post(f'/api/applications/{application_id}/complete/', {}, format='json')
        allowed = self.accounts.post(f'/api/applications/{application_id}/complete/',
                                     {'note': 'Receipt 4417 seen.'}, format='json')

        self.assertEqual(refused.status_code, 403)
        self.assertIn('accountant', refused.data['detail'])
        self.assertEqual(allowed.status_code, 200, allowed.data)
        self.assertEqual(allowed.data['state'], Application.ADMISSION)

    def test_the_whole_queue_is_worked_desk_by_desk(self):
        application_id = self.capture().data['id']

        self.admissions_api.post(f'/api/applications/{application_id}/nactvet-number/',
                                 {'nactvet_reg_no': 'NIT/PST/2026/0101'}, format='json')
        self.admissions_api.post(f'/api/applications/{application_id}/complete/',
                                 {'note': 'Taken on; number issued.'}, format='json')
        self.records.post(f'/api/applications/{application_id}/complete/',
                          {'note': 'Certificates scanned.'}, format='json')
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

        listed = self.records.get(f'/api/applications/{application_id}/requirements/')
        recorded = self.records.post(f'/api/applications/{application_id}/requirements/',
                                     {'requirement_id': requirement.id,
                                      'status': RequirementCheck.MISSING}, format='json')

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

        recorded = self.records.post(f'/api/applications/{application_id}/requirements/',
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

        self.assertEqual(refused.status_code, 403)
        self.assertIn('admission officer', refused.data['detail'])
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
