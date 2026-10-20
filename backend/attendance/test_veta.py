"""VETA courses, each rule a test:

  * the Principal (or DPARC) defines a course and chooses its structure: a
    short course, or an NVA long course (Level I–III), with its duration;
  * the accountant sets the course's fees: which charge (and so which account),
    how much, in which instalments;
  * the admission officer opens an intake, and says how the ID number looks;
  * a student goes through the same four desks as a PST first year: records
    needs a phone and a next of kin (no documents); finance states day or
    hostel and raises the fees; admission admits, which gives the college ID
    (the VETA pattern), the enrollment and a portal password;
  * one course at a time; an intake's capacity holds;
  * the student signs in with their college ID and sees their course;
  * PST is untouched.
"""

from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from rest_framework.test import APIClient

from . import veta
from .models import (
    AcademicYear, BankAccount, ChargeType, CollegeIdFormat, CourseApplication, CourseEnrollment,
    Department, Intake, Programme, Semester, StudentCharge, StudentProfile,
)
from .views import set_roles

User = get_user_model()


class VetaBase(TestCase):
    def office(self, username, *roles):
        user = User.objects.create_user(username, password='pw')
        set_roles(user, list(roles), full_name=username.title())
        client = APIClient()
        client.force_authenticate(user)
        return user, client

    def setUp(self):
        self.year = AcademicYear.objects.create(name='2026/2027', is_active=True)
        Semester.objects.create(academic_year=self.year, number=1, is_active=True)
        self.department = Department.objects.create(name='Vocational Training', code='VOC')
        self.principal_user, self.principal = self.office('boss', 'principal')
        self.accountant_user, self.accountant = self.office('money', 'accountant')
        self.admission_user, self.admission = self.office('adm', 'admission_officer')
        self.records_user, self.records = self.office('rec', 'records_officer')
        self.coordinator_user, self.coordinator = self.office('coord', 'coordinator')
        self.dparc_user, self.dparc = self.office('dparc', 'dparc')
        self.exam_user, self.exam = self.office('exam', 'exam_officer')
        self.account = BankAccount.objects.create(account_name='BPHACOH Training', account_number='0150000000001',
                                                  purpose='Training fees')
        self.tuition = ChargeType.objects.create(name='Course Tuition', family=ChargeType.FEE,
                                                 applies=ChargeType.ON_REQUEST, bank_account=self.account)
        self.hostel = ChargeType.objects.create(name='Course Hostel', family=ChargeType.OTHER,
                                                applies=ChargeType.ON_REQUEST, bank_account=self.account)
        CollegeIdFormat.objects.create(academic_year=self.year, pattern='{COLLEGE}/{PROG}/{YEAR}/{SEQ}',
                                       veta_pattern='{COLLEGE}/VETA/{PROG}/{YY}/{SEQ}')

    def define_course(self, client=None, **extra):
        data = {'department': self.department.id, 'name': 'Hair Cutting', 'code': 'HCT',
                'structure': 'short', 'duration_months': 3, **extra}
        return (client or self.principal).post('/api/programmes/', data, format='json')

    def course_with_fees(self):
        course = Programme.objects.get(id=self.define_course().data['id'])
        self.accountant.post('/api/course-fees/', {
            'programme': course.id, 'academic_year_id': self.year.id, 'charge_type': self.tuition.id,
            'amount': '300000', 'instalments': [{'due_after_days': 0, 'amount': '200000'},
                                                {'due_after_days': 30, 'amount': '100000'}]}, format='json')
        self.accountant.post('/api/course-fees/', {
            'programme': course.id, 'academic_year_id': self.year.id, 'charge_type': self.hostel.id,
            'amount': '150000', 'hostel_only': True}, format='json')
        intake = self.admission.post('/api/intakes/', {
            'programme': course.id, 'academic_year_id': self.year.id,
            'starts_on': '2026-10-19', 'capacity': 2}, format='json')
        return course, Intake.objects.get(id=intake.data['id'])

    def apply(self, intake, name='Neema Juma'):
        made = self.admission.post('/api/course-applications/', {
            'intake': intake.id, 'name': name, 'phone': '0712000000'}, format='json')
        self.assertEqual(made.status_code, 201, made.data)
        return made.data['id']

    def through_the_desks(self, intake, name='Neema Juma', residence='day'):
        app = self.apply(intake, name)
        self.admission.post(f'/api/course-applications/{app}/clear/', {}, format='json')
        saved = self.records.post(f'/api/course-applications/{app}/details/', {'gender': 'F', 'fee_payer': 'family',
            'phone': '0712000000', 'next_of_kin': [
                {'name': 'Mama Juma', 'phone': '0713000000', 'relationship': 'parent'}]}, format='json')
        self.assertEqual(saved.status_code, 200, saved.data)
        self.records.post(f'/api/course-applications/{app}/clear/', {}, format='json')
        self.accountant.post(f'/api/course-applications/{app}/fees/', {'residence': residence}, format='json')
        self.accountant.post(f'/api/course-applications/{app}/clear/', {}, format='json')
        return app, self.admission.post(f'/api/course-applications/{app}/clear/', {}, format='json')


class DefiningACourseTests(VetaBase):
    def test_the_principal_defines_a_short_course(self):
        made = self.define_course()
        self.assertEqual(made.status_code, 201, made.data)
        self.assertEqual(made.data['structure'], 'short')
        self.assertTrue(made.data['is_veta'])

    def test_dparc_may_too_and_the_exam_officer_may_not(self):
        self.assertEqual(self.define_course(self.dparc, code='HDB', name='Hair Dressing').status_code, 201)
        self.assertEqual(self.define_course(self.exam, code='MPM', name='Mobile Phones').status_code, 403)

    def test_a_vetA_course_needs_its_duration_and_a_long_course_its_top_level(self):
        self.assertEqual(self.define_course(duration_months=None).status_code, 400)
        refused = self.define_course(structure='nva', code='ICT', name='Computer', duration_months=24)
        self.assertEqual(refused.status_code, 400)
        self.assertIn('nva_top_level', refused.data)
        made = self.define_course(structure='nva', code='ICT', name='Computer', duration_months=24, nva_top_level=2)
        self.assertEqual(made.status_code, 201, made.data)

    def test_pst_programmes_stay_semester_based(self):
        pst = Programme.objects.create(department=self.department, name='Pharmaceutical Sciences', code='PST')
        self.assertEqual(pst.structure, Programme.SEMESTER)
        self.assertFalse(pst.is_veta)
        refused = self.admission.post('/api/intakes/', {'programme': pst.id, 'starts_on': '2026-10-19'},
                                      format='json')
        self.assertEqual(refused.status_code, 400)


class CourseFeeTests(VetaBase):
    def test_the_accountant_sets_fees_and_their_instalments(self):
        course, _ = self.course_with_fees()
        fees = self.accountant.get(f'/api/course-fees/?programme_id={course.id}').data
        tuition = next(f for f in fees if f['charge_type'] == self.tuition.id)
        self.assertEqual([i['due_after_days'] for i in tuition['instalments']], [0, 30])
        self.assertIn('0150000000001', tuition['account'])

    def test_instalments_must_add_up(self):
        course = Programme.objects.get(id=self.define_course().data['id'])
        refused = self.accountant.post('/api/course-fees/', {
            'programme': course.id, 'charge_type': self.tuition.id, 'amount': '300000',
            'instalments': [{'due_after_days': 0, 'amount': '100000'}]}, format='json')
        self.assertEqual(refused.status_code, 400)
        self.assertIn('add up', refused.data['detail'])

    def test_only_the_accountant_sets_them(self):
        course = Programme.objects.get(id=self.define_course().data['id'])
        self.assertEqual(self.admission.post('/api/course-fees/', {
            'programme': course.id, 'charge_type': self.tuition.id, 'amount': '1'}, format='json').status_code, 403)


class IntakeTests(VetaBase):
    def test_the_end_date_follows_the_duration(self):
        _, intake = self.course_with_fees()
        self.assertEqual(intake.ends_on, date(2027, 1, 19))

    def test_only_the_admission_office_opens_one(self):
        course = Programme.objects.get(id=self.define_course().data['id'])
        self.assertEqual(self.records.post('/api/intakes/', {
            'programme': course.id, 'starts_on': '2026-10-19'}, format='json').status_code, 403)


class TheFourDesksTests(VetaBase):
    def test_a_student_is_admitted_billed_and_numbered(self):
        _, intake = self.course_with_fees()
        app, admitted = self.through_the_desks(intake)
        self.assertEqual(admitted.status_code, 200, admitted.data)
        self.assertEqual(admitted.data['state'], 'admitted')
        self.assertFalse(admitted.data.get('portal_password'))   # the ICT desk gives it
        self.assertEqual(admitted.data['college_id'], 'BPH/VETA/HCT/26/001')
        enrollment = CourseEnrollment.objects.get(application_id=app)
        self.assertEqual(enrollment.status, CourseEnrollment.STUDYING)
        charges = StudentCharge.objects.filter(profile=enrollment.profile, intake=intake).order_by('due_date')
        self.assertEqual([(c.amount, c.due_date) for c in charges],
                         [(Decimal('200000.00'), date(2026, 10, 19)), (Decimal('100000.00'), date(2026, 11, 18))])

    def test_hostel_students_pay_the_hostel_fee(self):
        _, intake = self.course_with_fees()
        app, _ = self.through_the_desks(intake, residence='hostel')
        profile = CourseApplication.objects.get(id=app).profile
        self.assertTrue(StudentCharge.objects.filter(profile=profile, charge_type=self.hostel).exists())

    def test_records_needs_a_next_of_kin_but_no_documents(self):
        _, intake = self.course_with_fees()
        app = self.apply(intake)
        self.admission.post(f'/api/course-applications/{app}/clear/', {}, format='json')
        refused = self.records.post(f'/api/course-applications/{app}/clear/', {}, format='json')
        self.assertEqual(refused.status_code, 400)
        self.assertIn('next of kin', refused.data['detail'])

    def test_finance_raises_the_fees_before_clearing(self):
        _, intake = self.course_with_fees()
        app = self.apply(intake)
        self.admission.post(f'/api/course-applications/{app}/clear/', {}, format='json')
        self.records.post(f'/api/course-applications/{app}/details/', {'gender': 'F', 'fee_payer': 'family',
            'next_of_kin': [{'name': 'Baba', 'phone': '07', 'relationship': 'parent'}]}, format='json')
        self.records.post(f'/api/course-applications/{app}/clear/', {}, format='json')
        refused = self.accountant.post(f'/api/course-applications/{app}/clear/', {}, format='json')
        self.assertEqual(refused.status_code, 400)
        self.assertIn('day or hostel', refused.data['detail'])

    def test_each_desk_is_its_own_offices(self):
        """Records does not even see an application still at intake (as on
        the PST desks); the admission office sees the finance desk but cannot
        clear it."""
        _, intake = self.course_with_fees()
        app = self.apply(intake)
        self.assertEqual(self.records.post(f'/api/course-applications/{app}/clear/', {},
                                           format='json').status_code, 404)
        self.admission.post(f'/api/course-applications/{app}/clear/', {}, format='json')
        self.records.post(f'/api/course-applications/{app}/details/', {'gender': 'F', 'fee_payer': 'family',
            'next_of_kin': [{'name': 'Baba', 'phone': '07', 'relationship': 'parent'}]}, format='json')
        self.records.post(f'/api/course-applications/{app}/clear/', {}, format='json')
        self.assertEqual(CourseApplication.objects.get(id=app).state, 'finance')
        self.assertEqual(self.admission.post(f'/api/course-applications/{app}/clear/', {},
                                             format='json').status_code, 403)

    def test_dparc_reads_without_the_money_and_works_no_desk(self):
        _, intake = self.course_with_fees()
        app = self.apply(intake)
        opened = self.dparc.get(f'/api/course-applications/{app}/').data
        self.assertNotIn('money', opened)
        self.assertFalse(opened['can_work'])
        self.assertIn('money', self.admission.get(f'/api/course-applications/{app}/').data)

    def test_one_course_at_a_time(self):
        course, intake = self.course_with_fees()
        self.through_the_desks(intake)
        later = Intake.objects.create(programme=course, academic_year=self.year,
                                      starts_on=date(2027, 2, 1), ends_on=date(2027, 5, 1))
        profile = StudentProfile.objects.get(name='Neema Juma')
        with self.assertRaises(veta.VetaError):
            veta.open_application(intake=later, name='Neema Juma', profile=profile)

    def test_the_intake_capacity_holds(self):
        _, intake = self.course_with_fees()          # capacity 2
        self.through_the_desks(intake, 'A One')
        self.through_the_desks(intake, 'B Two')
        _, third = self.through_the_desks(intake, 'C Three')
        self.assertEqual(third.status_code, 400)
        self.assertIn('full', third.data['detail'])

    def test_refusing_waives_what_was_not_paid(self):
        _, intake = self.course_with_fees()
        app = self.apply(intake)
        self.admission.post(f'/api/course-applications/{app}/clear/', {}, format='json')
        self.records.post(f'/api/course-applications/{app}/details/', {'gender': 'F', 'fee_payer': 'family',
            'next_of_kin': [{'name': 'Baba', 'phone': '07', 'relationship': 'parent'}]}, format='json')
        self.records.post(f'/api/course-applications/{app}/clear/', {}, format='json')
        self.accountant.post(f'/api/course-applications/{app}/fees/', {'residence': 'day'}, format='json')
        self.admission.post(f'/api/course-applications/{app}/refuse/', {'note': 'Withdrew'}, format='json')
        profile = CourseApplication.objects.get(id=app).profile
        for charge in StudentCharge.objects.filter(profile=profile):
            self.assertEqual(charge.payable, Decimal('0'))


class TheCoursePortalTests(VetaBase):
    def test_the_student_signs_in_with_their_college_id_and_sees_the_course(self):
        _, intake = self.course_with_fees()
        _, admitted = self.through_the_desks(intake)
        ict = User.objects.create_user('ict', password='pw')
        set_roles(ict, ['ict_officer'], full_name='ICT')
        desk = APIClient(); desk.force_authenticate(ict)
        reset = desk.post('/api/student-passwords/reset/', {'identifier': admitted.data['college_id']},
                          format='json')
        self.assertEqual(reset.status_code, 200, reset.data)
        password = reset.data['password']
        browser = Client()
        signed = browser.post('/login/', {'identifier': admitted.data['college_id'], 'secret': password})
        self.assertRedirects(signed, '/course-portal/', fetch_redirect_response=False)
        page = browser.get('/course-portal/')
        self.assertContains(page, 'Choose your own password')
        changed = browser.post('/course-portal/', {'current_password': password,
                                                   'new_password': 'Kata#2026nywele'})
        self.assertContains(changed, 'Password changed')
        self.assertContains(changed, 'Hair Cutting')
        self.assertContains(changed, '300,000.00')

    def test_a_wrong_password_is_refused(self):
        _, intake = self.course_with_fees()
        _, admitted = self.through_the_desks(intake)
        refused = Client().post('/login/', {'identifier': admitted.data['college_id'], 'secret': 'nope'})
        self.assertEqual(refused.status_code, 200)
        self.assertContains(refused, 'Invalid credentials')

    def test_without_signing_in_the_portal_sends_you_to_sign_in(self):
        self.assertRedirects(Client().get('/course-portal/'), '/login/', fetch_redirect_response=False)


class CourseModuleTests(VetaBase):
    def test_the_coordinator_keeps_the_modules(self):
        course = Programme.objects.get(id=self.define_course().data['id'])
        made = self.coordinator.post('/api/course-modules/', {
            'programme': course.id, 'code': 'hct01', 'name': 'Clipper basics'}, format='json')
        self.assertEqual(made.status_code, 201, made.data)
        self.assertEqual(made.data['code'], 'HCT01')
        self.assertEqual(self.accountant.post('/api/course-modules/', {
            'programme': course.id, 'code': 'X', 'name': 'Y'}, format='json').status_code, 403)

    def test_a_long_course_module_names_its_level(self):
        course = Programme.objects.get(id=self.define_course(
            structure='nva', code='ICT', name='Computer', duration_months=24, nva_top_level=2).data['id'])
        self.assertEqual(self.coordinator.post('/api/course-modules/', {
            'programme': course.id, 'code': 'ICT101', 'name': 'Basics'}, format='json').status_code, 400)
        self.assertEqual(self.coordinator.post('/api/course-modules/', {
            'programme': course.id, 'code': 'ICT101', 'name': 'Basics', 'nva_level': 3},
            format='json').status_code, 400)
        self.assertEqual(self.coordinator.post('/api/course-modules/', {
            'programme': course.id, 'code': 'ICT101', 'name': 'Basics', 'nva_level': 1},
            format='json').status_code, 201)


class TheIdFormatTests(VetaBase):
    def test_the_veta_pattern_falls_back_to_the_main_one(self):
        fmt = CollegeIdFormat.objects.get(academic_year=self.year)
        course = Programme.objects.get(id=self.define_course().data['id'])
        self.assertEqual(fmt.render(7, course), 'BPH/VETA/HCT/26/007')
        fmt.veta_pattern = ''
        self.assertEqual(fmt.render(7, course), 'BPH/HCT/2026/007')
        pst = Programme.objects.create(department=self.department, name='Pharm', code='PST')
        fmt.veta_pattern = '{COLLEGE}/VETA/{PROG}/{YY}/{SEQ}'
        self.assertEqual(fmt.render(7, pst), 'BPH/PST/2026/007')

    def test_veta_card_numbers_are_a_series_of_their_own(self):
        # Said 19 October 2026: a VETA student's identity card number differs from
        # a PST student's, and runs on its own counter.
        from . import admissions
        from .models import StudentProfile
        course = Programme.objects.get(id=self.define_course().data['id'])
        pst = Programme.objects.create(department=self.department, name='Pharm', code='PST')
        def issue(reg, programme):
            profile = StudentProfile.objects.create(nactvet_reg_no=reg, name=reg)
            return admissions.issue_college_id(profile, self.year, programme)
        computer = Programme.objects.create(department=self.department, name='Computer basics', code='CB',
                                            structure='short', duration_months=3)
        self.assertEqual(issue('P1', pst), 'BPH/PST/2026/001')
        self.assertEqual(issue('V1', course), 'BPH/VETA/HCT/26/001')
        self.assertEqual(issue('P2', pst), 'BPH/PST/2026/002')
        self.assertEqual(issue('V2', course), 'BPH/VETA/HCT/26/002')
        # Each VETA course counts on its own (said 19 October 2026).
        self.assertEqual(issue('C1', computer), 'BPH/VETA/CB/26/001')
        self.assertEqual(issue('V3', course), 'BPH/VETA/HCT/26/003')

    def test_the_veta_pattern_carries_into_the_next_year(self):
        from . import admissions
        nxt = AcademicYear.objects.create(name='2027/2028')
        fmt = admissions.id_format_for(nxt)
        self.assertEqual(fmt.veta_pattern, '{COLLEGE}/VETA/{PROG}/{YY}/{SEQ}')
        self.assertEqual(fmt.next_number, 1)
        self.assertFalse(fmt.counters.exists())

    def test_the_veta_pattern_must_differ_and_starts_where_the_office_says(self):
        fmt = CollegeIdFormat.objects.get(academic_year=self.year)
        self.define_course()
        same = self.admission.patch(f'/api/college-id-formats/{fmt.id}/',
                                    {'veta_pattern': '{COLLEGE}/{PROG}/{YEAR}/{SEQ}'}, format='json')
        self.assertEqual(same.status_code, 400)
        without_code = self.admission.patch(f'/api/college-id-formats/{fmt.id}/',
                                            {'veta_pattern': '{COLLEGE}/VETA/{YY}/{SEQ}'}, format='json')
        self.assertEqual(without_code.status_code, 400)        # each course needs its own code
        saved = self.admission.patch(f'/api/college-id-formats/{fmt.id}/', {'veta_starts_at': 500}, format='json')
        self.assertEqual(saved.status_code, 200, saved.data)
        self.assertEqual(saved.data['veta_example'], 'BPH/VETA/HCT/26/500')
        self.assertEqual(saved.data['veta_counters'], [{'programme': 'HCT', 'next': 'BPH/VETA/HCT/26/500'}])


class TheCosmetologyFormTests(VetaBase):
    """seed_veta_courses: the form's two courses and fees, checked against it."""

    def setUp(self):
        super().setUp()
        from io import StringIO
        from django.core.management import call_command
        for number, purpose in (('0150417961300', 'Tuition fee'), ('0150417961301', 'Other charges & accommodation')):
            BankAccount.objects.get_or_create(account_number=number, defaults={'account_name': 'BPHACOH',
                                                                                'purpose': purpose})
        self.out = StringIO()
        call_command('seed_veta_courses', year='2026/2027', department='VOC', stdout=self.out)
        self.hdb = Programme.objects.get(code='HDB')
        self.intake = Intake.objects.create(programme=self.hdb, academic_year=self.year,
                                            starts_on=date(2026, 10, 19), ends_on=date(2027, 2, 19))

    def test_every_figure_matches_the_form(self):
        self.assertEqual(self.out.getvalue().count('matches the form'), 3)
        self.assertEqual(self.hdb.duration_months, 4)
        self.assertEqual(Programme.objects.get(code='HCT').duration_months, 3)

    def test_the_same_accounts_as_pst(self):
        fee = self.hdb.course_fees.get(charge_type__name='Course fee (VETA)')
        self.assertEqual(fee.charge_type.bank_account.account_number, '0150417961300')
        uniform = self.hdb.course_fees.get(charge_type__name='Uniform (VETA)')
        self.assertEqual(uniform.charge_type.bank_account.account_number, '0150417961301')

    def bill(self, residence):
        app = self.apply(self.intake)
        application = CourseApplication.objects.get(id=app)
        application.state = CourseApplication.FINANCE
        application.save()
        veta.raise_fees(application, residence=residence)
        return application, sum((c.amount for c in StudentCharge.objects.filter(profile=application.profile)),
                                Decimal(0))

    def test_a_day_student_pays_the_fee_and_the_once_only_charges(self):
        _, total = self.bill('day')
        self.assertEqual(total, Decimal('550000'))          # 450,000 + 100,000

    def test_a_hostel_student_pays_the_hostel_column_too(self):
        _, total = self.bill('hostel')
        self.assertEqual(total, Decimal('800000'))          # 700,000 + 100,000

    def test_admission_waits_for_the_first_instalment(self):
        from . import finance
        application, _ = self.bill('hostel')
        application.state = CourseApplication.ADMISSION
        application.save()
        refused = self.admission.post(f'/api/course-applications/{application.id}/clear/', {}, format='json')
        self.assertEqual(refused.status_code, 400)
        self.assertIn('before admission', refused.data['detail'])
        # Awamu 1: 300,000 course fee + 200,000 hostel. Paid against those two
        # charges (the once-only ones are due too but not marked).
        for charge in StudentCharge.objects.filter(profile=application.profile, installment_number=1,
                                                   charge_type__name__in=['Course fee (VETA)', 'Hostel (VETA)']):
            finance.record_payment(application.profile, charge.amount, date(2026, 10, 19),
                                   recorded_by=self.accountant_user, bank_reference=f'CRDB-{charge.id}',
                                   allocations=[(charge, charge.amount)])
        admitted = self.admission.post(f'/api/course-applications/{application.id}/clear/', {}, format='json')
        self.assertEqual(admitted.status_code, 200, admitted.data)
        self.assertEqual(admitted.data['state'], 'admitted')
