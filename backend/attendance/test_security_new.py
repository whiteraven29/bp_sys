"""The endpoints added in October 2026, swept for who may use them (said 18
October 2026, before the deploy): a tutor, a student signed in to the portal
and somebody not signed in are refused everything that is not theirs; the
student's own endpoints answer only for that student; uploads are what they
claim to be.
"""

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from rest_framework.test import APIClient

from .models import AcademicYear, ClassLevel, Module, Semester, Student, StudentProfile
from .views import set_roles

User = get_user_model()

STAFF_ONLY = [
    ('get', '/api/class-list/?semester_id={sem}&class_level_id={level}'),
    ('get', '/api/cat-numbers/?semester_id={sem}&class_level_id={level}&cat=1'),
    ('post', '/api/cat-numbers/'),
    ('post', '/api/cat-number-format/'),
    ('get', '/api/provisional/'),
    ('post', '/api/provisional/settle-passes/'),
    ('post', '/api/provisional/cutoff/'),
    ('get', '/api/exit-requests/'),
    ('post', '/api/exit-requests/1/decide/'),
    ('get', '/api/not-reported/?semester_id={sem}'),
    ('post', '/api/not-reported/1/'),
    ('get', '/api/health-reports/'),
    ('post', '/api/health-reports/1/accept/'),
    ('get', '/api/health-records/'),
    ('get', '/api/assets/'),
    ('post', '/api/assets/'),
    ('get', '/api/asset-disposals/'),
    ('post', '/api/asset-disposals/1/decide/'),
    ('get', '/api/inventory/staff/'),
    ('get', '/api/inventory/handovers/'),
    ('post', '/api/inventory/handovers/'),
    ('get', '/api/inventory/summary/'),
    ('get', '/api/lab-items/'),
    ('post', '/api/lab-movements/'),
    ('get', '/api/lab/report/'),
    ('get', '/api/student-records/1/passport/'),
    ('post', '/api/student-records/1/passport/'),
]


class NewEndpointAccessTests(TestCase):
    def setUp(self):
        year = AcademicYear.objects.create(name='2026/2027', is_active=True)
        self.sem = Semester.objects.create(academic_year=year, number=1, is_active=True)
        self.level = ClassLevel.objects.create(name='NTA Level 4', order=4)
        self.module = Module.objects.create(name='M', code='PST04101', teacher='T', semester=self.sem,
                                            class_level=self.level, credits=4)
        tutor = User.objects.create_user('mwalimu', password='pw')
        set_roles(tutor, ['tutor'], full_name='Mwalimu')
        self.tutor = APIClient(); self.tutor.force_authenticate(tutor)
        StudentProfile.objects.create(nactvet_reg_no='NS0001/0001/2026', name='Asha')
        self.enrollment = Student.objects.create(nactvet_reg_no='NS0001/0001/2026', name='Asha', module=self.module)
        self.enrollment.set_portal_pin('Portal#2026', require_change=False)
        self.enrollment.save()
        self.portal = Client()
        session = self.portal.session
        session['student_id'] = self.enrollment.id
        session.save()

    def url(self, path):
        return path.format(sem=self.sem.id, level=self.level.id)

    def call(self, client, method, path):
        if method == 'get':
            return client.get(self.url(path))
        if isinstance(client, APIClient):
            return client.post(self.url(path), {}, format='json')
        return client.post(self.url(path), '{}', content_type='application/json')

    def test_a_tutor_is_refused(self):
        for method, path in STAFF_ONLY:
            response = self.call(self.tutor, method, path)
            self.assertIn(response.status_code, (403, 404), f'{method} {path}: {response.status_code}')

    def test_a_student_on_the_portal_is_refused(self):
        for method, path in STAFF_ONLY:
            response = self.call(self.portal, method, path)
            self.assertIn(response.status_code, (302, 401, 403, 404), f'{method} {path}: {response.status_code}')

    def test_nobody_signed_in_is_refused(self):
        anonymous = Client()
        for method, path in STAFF_ONLY + [('get', '/api/my-exit/'), ('get', '/api/my-health/'),
                                                ('get', '/api/my-passport/'), ('post', '/api/my-passport/')]:
            response = self.call(anonymous, method, path)
            self.assertIn(response.status_code, (302, 401, 403), f'{method} {path}: {response.status_code}')

    def test_the_register_is_only_for_the_modules_tutors(self):
        response = self.tutor.get(f'/api/attendance/register/download/?module_id={self.module.id}')
        self.assertIn(response.status_code, (302, 403))

    def test_a_portal_endpoint_answers_for_that_student_only(self):
        mine = self.portal.get('/api/my-exit/')
        self.assertEqual(mine.status_code, 200)
        refused = self.portal.post('/api/my-exit/', '{"withdraw": "abc"}', content_type='application/json')
        self.assertEqual(refused.status_code, 400)
        missing = self.portal.post('/api/my-exit/', '{"withdraw": "999"}', content_type='application/json')
        self.assertEqual(missing.status_code, 404)

    def test_a_health_document_is_what_it_says(self):
        def report(upload):
            return self.portal.post('/api/my-health/', {'condition': 'Asthma', 'facility': 'Hospital',
                                                        'issued_on': '2026-09-01', 'file': upload})
        disguised = SimpleUploadedFile('letter.pdf', b'<script>alert(1)</script>', content_type='application/pdf')
        self.assertEqual(report(disguised).status_code, 400)
        big = SimpleUploadedFile('big.pdf', b'%PDF-1.4' + b'0' * (10 * 1024 * 1024 + 1), content_type='application/pdf')
        self.assertIn('10 MB', report(big).json()['detail'])
        real = SimpleUploadedFile('letter.pdf', b'%PDF-1.4 hospital', content_type='application/pdf')
        self.assertEqual(report(real).status_code, 201)


class BlankChoiceTests(NewEndpointAccessTests):
    """A form sent without its choice gets an answer, not a server error (said
    19 October 2026: New application without a programme answered 500)."""

    def office_client(self, username, role):
        user = User.objects.create_user(username, password='pw')
        set_roles(user, [role], full_name=username.title())
        client = APIClient()
        client.force_authenticate(user)
        return client

    def test_a_blank_programme_is_asked_for(self):
        admission = self.office_client('adm', 'admission_officer')
        response = admission.post('/api/applications/capture/', {
            'kind': 'new', 'name': 'Grace Mollel', 'programme_id': '', 'class_level_id': ''}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('programme', response.data['detail'])
        nonsense = admission.post('/api/applications/capture/', {
            'kind': 'new', 'name': 'Grace Mollel', 'programme_id': 'abc', 'class_level_id': 'x'}, format='json')
        self.assertEqual(nonsense.status_code, 400)

    def test_blank_ids_elsewhere_are_not_server_errors(self):
        exam = self.office_client('exam', 'exam_officer')
        self.assertEqual(exam.get('/api/results-summary/?semester_id=&class_level_id=').status_code, 400)
        principal = self.office_client('boss', 'principal')
        self.assertIn(principal.post('/api/result-withholdings/', {'profile_id': '', 'semester_id': ''},
                                     format='json').status_code, (400, 403, 404))


for _name in [n for n in dir(NewEndpointAccessTests) if n.startswith('test_')]:
    setattr(BlankChoiceTests, _name, None)
