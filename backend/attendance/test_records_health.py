"""What records takes down beyond the basics, each rule a test:

  * a PST student's Form Four index number: written the way NECTA prints it,
    one person per number, and records cannot clear a first year without it;
    a VETA course student is not asked for it;
  * gender is needed at both records desks, PST and VETA;
  * a health problem is written down only against a medical certificate that
    records has verified;
  * the Dean of Students is told, reads every health record and its
    certificate, and changes none; finance and tutors see none;
  * a condition that no longer applies is closed with a reason, never deleted.
"""

from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient

from . import admissions
from .models import Application, HealthRecord, Notification, StudentDocument, StudentProfile
from .test_admissions import AdmissionBase, User, next_index
from .views import set_roles


class RecordsBase(AdmissionBase):
    def office(self, username, *roles):
        user = User.objects.create_user(username, password='pw')
        set_roles(user, list(roles), full_name=username.title())
        client = APIClient()
        client.force_authenticate(user)
        return user, client

    def setUp(self):
        super().setUp()
        self.records_user, self.records = self.office('records', 'records_officer')
        self.dean_user, self.dean = self.office('dean', 'dean')
        self.principal_user, self.principal = self.office('boss', 'principal')
        self.accountant_user, self.accountant = self.office('money', 'accountant')
        self.tutor_user, self.tutor = self.office('teacher', 'tutor')
        self.anatomy = self.module('PST04101', self.level4, self.sem1)
        self.profile = self.student('NIT/PST/2026/0300', 'Zuhura Ally')

    def student(self, reg_no, name):
        """A college student — records only looks up people the college has."""
        from .models import Student
        profile = self.applicant(reg_no, name)
        Student.objects.create(nactvet_reg_no=reg_no, name=name, profile=profile, module=self.anatomy)
        return profile

    def certificate(self, *, kind='medical', verify=True, profile=None):
        scan = SimpleUploadedFile('medical.pdf', b'%PDF-1.4 a certificate', content_type='application/pdf')
        made = self.records.post('/api/student-documents/', {
            'profile': (profile or self.profile).id, 'kind': kind, 'file': scan}, format='multipart')
        self.assertEqual(made.status_code, 201, made.data)
        if verify:
            self.records.post(f'/api/student-documents/{made.data["id"]}/verify/', {}, format='json')
        return StudentDocument.objects.get(pk=made.data['id'])

    def add(self, certificate, client=None, condition='Asthma'):
        return (client or self.records).post('/api/health-records/', {
            'profile_id': self.profile.id, 'certificate_id': certificate.id,
            'condition': condition, 'notes': 'Keeps an inhaler; excuse from heavy practicals.'},
            format='json')


class FormFourIndexTests(RecordsBase):
    def test_it_is_kept_the_way_necta_prints_it(self):
        saved = self.records.post(f'/api/student-records/{self.profile.id}/',
                                  {'form_four_index': ' s0123-0045-2021 '}, format='json')
        self.assertEqual(saved.status_code, 200, saved.data)
        self.assertEqual(saved.data['form_four_index'], 'S0123/0045/2021')

    def test_something_else_is_refused(self):
        refused = self.records.post(f'/api/student-records/{self.profile.id}/',
                                    {'form_four_index': '12345'}, format='json')
        self.assertEqual(refused.status_code, 400)
        self.assertIn('S0123/0045/2021', refused.data['detail'])

    def test_one_person_per_number(self):
        other = self.student('NIT/PST/2026/0301', 'Someone Else')
        number = self.profile.form_four_index
        refused = self.records.post(f'/api/student-records/{other.id}/', {'form_four_index': number},
                                    format='json')
        self.assertEqual(refused.status_code, 400)
        self.assertIn('Zuhura Ally', refused.data['detail'])

    def test_records_cannot_clear_a_first_year_without_it(self):
        newcomer = admissions.capture_student(nactvet_reg_no='', name='First Year', phone='07',
                                              gender='M', actor=self.officer,
                                              date_of_birth='2006-01-01',
                                              next_of_kin=[{'name': 'A', 'phone': '1'},
                                                           {'name': 'B', 'phone': '2'}])
        application = admissions.open_application(
            profile=newcomer, semester=self.sem1, programme=self.pst, class_level=self.level4,
            kind=Application.NEW, actor=self.officer)
        admissions.complete_step(application, actor=self.officer)           # intake → records
        with self.assertRaisesMessage(admissions.AdmissionError, 'Form Four index number'):
            admissions.complete_step(application, actor=self.officer)
        admissions.record_details(newcomer, form_four_index=next_index(), actor=self.officer)
        self.assertEqual(admissions.complete_step(application, actor=self.officer).state,
                         Application.FINANCE)

    def test_intake_may_take_it_down_too(self):
        profile = admissions.capture_student(nactvet_reg_no='', name='New Comer',
                                             form_four_index='P0999/0012/2023', actor=self.officer)
        self.assertEqual(profile.form_four_index, 'P0999/0012/2023')

    def test_a_veta_student_is_not_asked_for_it_but_is_asked_their_gender(self):
        from .models import CourseApplication, Intake, Programme
        from datetime import date
        course = Programme.objects.create(department=self.department, name='Hair Cutting', code='HCT',
                                          structure=Programme.SHORT, duration_months=3)
        intake = Intake.objects.create(programme=course, academic_year=self.year,
                                       starts_on=date(2026, 10, 19), ends_on=date(2027, 1, 19))
        person = StudentProfile.objects.create(nactvet_reg_no='VETA/TEMP/1', name='Kinyozi', phone='07')
        CourseApplication.objects.create(profile=person, intake=intake)
        self.assertEqual(admissions.missing_details(person), ['gender', 'next of kin'])


class HealthRecordTests(RecordsBase):
    def test_records_writes_it_against_a_verified_certificate_and_the_dean_is_told(self):
        made = self.add(self.certificate())
        self.assertEqual(made.status_code, 201, made.data)
        self.assertEqual(made.data['condition'], 'Asthma')
        self.assertEqual(made.data['verified_by'], self.records_user.get_full_name() or 'records')
        told = Notification.objects.filter(kind=Notification.HEALTH_RECORDED)
        self.assertEqual([n.user_id for n in told], [self.dean_user.id])

    def test_not_without_the_certificate_verified(self):
        refused = self.add(self.certificate(verify=False))
        self.assertEqual(refused.status_code, 400)
        self.assertIn('seen (verified)', refused.data['detail'])

    def test_not_against_some_other_paper(self):
        refused = self.add(self.certificate(kind='birth_certificate'))
        self.assertEqual(refused.status_code, 400)
        self.assertIn('medical form', refused.data['detail'])

    def test_not_against_another_students_certificate(self):
        other = self.student('NIT/PST/2026/0302', 'Another')
        refused = self.add(self.certificate(profile=other))
        self.assertEqual(refused.status_code, 400)

    def test_the_dean_reads_it_and_the_certificate_but_writes_nothing(self):
        certificate = self.certificate()
        self.add(certificate)
        rows = self.dean.get('/api/health-records/?current=1').data
        self.assertEqual([row['name'] for row in rows], ['Zuhura Ally'])
        self.client.force_login(self.dean_user)
        self.assertEqual(self.client.get(rows[0]['certificate_url']).status_code, 200)
        refused = self.add(self.certificate(), client=self.dean)
        self.assertEqual(refused.status_code, 400)
        self.assertIn('records office', refused.data['detail'])

    def test_the_principal_reads_and_may_correct(self):
        self.add(self.certificate())
        self.assertEqual(len(self.principal.get('/api/health-records/').data), 1)
        self.assertEqual(self.add(self.certificate(), client=self.principal, condition='Epilepsy')
                         .status_code, 201)

    def test_the_dean_cannot_open_other_papers(self):
        other = self.certificate(kind='o_level')
        self.client.force_login(self.dean_user)
        self.assertEqual(self.client.get(f'/api/student-documents/{other.id}/file/').status_code, 404)

    def test_finance_and_tutors_see_none(self):
        self.add(self.certificate())
        self.assertEqual(self.accountant.get('/api/health-records/').status_code, 403)
        self.assertEqual(self.tutor.get('/api/health-records/').status_code, 403)

    def test_a_condition_is_closed_with_a_reason_and_kept(self):
        record_id = self.add(self.certificate()).data['id']
        self.assertEqual(self.records.post(f'/api/health-records/{record_id}/close/', {},
                                           format='json').status_code, 400)
        closed = self.records.post(f'/api/health-records/{record_id}/close/',
                                   {'note': 'Cleared by the doctor, new certificate'}, format='json')
        self.assertEqual(closed.status_code, 200, closed.data)
        self.assertFalse(closed.data['is_current'])
        self.assertTrue(HealthRecord.objects.filter(pk=record_id).exists())
        self.assertEqual(self.dean.post(
            f'/api/health-records/{record_id}/close/', {'note': 'x'}, format='json').status_code, 400)
        self.assertEqual(self.dean.get('/api/health-records/?current=1').data, [])


class DeanRoleTests(RecordsBase):
    def test_the_principal_appoints_the_dean_and_the_ict_officer_cannot(self):
        _, ict = self.office('ict', 'ict_officer')
        person = User.objects.create_user('welfare', password='pw')
        set_roles(person, ['tutor'], full_name='Welfare Person')
        refused = ict.post(f'/api/staff-accounts/{person.id}/roles/', {'roles': ['tutor', 'dean']},
                           format='json')
        self.assertEqual(refused.status_code, 403)
        given = self.principal.post(f'/api/staff-accounts/{person.id}/roles/',
                                    {'roles': ['tutor', 'dean']}, format='json')
        self.assertEqual(given.status_code, 200, given.data)
        self.assertIn('dean', given.data['roles'])
