"""The security review's findings (diagrams/security-review.md), each a test.

The first ones are the review's own reproductions, now expected to fail for
the attacker:

  S1  a tutor cannot take over a student's portal with a password they choose
  S3  signing in pauses after repeated failures; the API refuses HTTP Basic
  S4  an expense receipt cannot be an HTML page, and is served as a download
  S7  a student's request that changes something must carry the CSRF token

and the rest of the fixes:

  S6  only a tutor claims a module          S8  a student's session is renewed
  S9  the ICT officer resets anybody's password — the Principal's and students'
      too — and every reset is recorded and shown to the holder
  S11 the API's default is "signed in", never "anyone"
  S12 medical certificates only for records, the Dean and the Principal
  S13 Excel exports carry no live formulas
      the address used for limits and the audit is nginx's, not the client's
"""

import base64
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, RequestFactory, TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from . import finance, passwords, security
from .models import (
    AcademicYear, ClassLevel, Department, ExpenseCategory, Module, Notification, PasswordReset,
    PasswordStatus, Programme, Semester, SignInFailure, Student, StudentDocument, StudentProfile,
)
from .views import set_roles

User = get_user_model()


class SecurityBase(TestCase):
    def setUp(self):
        cache.clear()
        self.year = AcademicYear.objects.create(name='2026/2027', is_active=True)
        self.sem = Semester.objects.create(academic_year=self.year, number=1, is_active=True)
        self.level = ClassLevel.objects.create(name='NTA Level 4', order=4)
        self.pst = Programme.objects.create(department=Department.objects.create(name='PST', code='PST'),
                                            name='PST', code='PST')
        self.anatomy = self.module('PST04101')
        self.maths = self.module('PST04102')
        self.victim = StudentProfile.objects.create(nactvet_reg_no='NS0001/0001/2024', name='Asha Victim',
                                                    college_id='BPH/PST/2026/001')
        self.victim_row = Student(nactvet_reg_no=self.victim.nactvet_reg_no, name=self.victim.name,
                                  profile=self.victim, module=self.anatomy)
        self.victim_row.set_portal_pin('Victim#2026', require_change=False)
        self.victim_row.save()
        self.tutor = self.office('tutor1', 'tutor')
        self.maths.teachers.add(self.tutor)

    def module(self, code):
        return Module.objects.create(name=code, code=code, teacher='T', class_level=self.level,
                                     semester=self.sem, programme=self.pst)

    def office(self, username, *roles, password='Office#2026x'):
        user = User.objects.create_user(username, password=password)
        set_roles(user, list(roles), full_name=username.title())
        return user

    def api(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def sign_in(self, identifier, secret, client=None, **extra):
        return (client or Client()).post('/login/', {'identifier': identifier, 'secret': secret}, **extra)


class StudentTakeoverTests(SecurityBase):
    """S1: the review's reproduction — a tutor enrolled another student's
    registration number in their own module with a password they chose, and
    signed in to that student's portal with it."""

    def test_the_tutors_password_is_not_taken_and_does_not_open_the_portal(self):
        response = self.api(self.tutor).post('/api/students/bulk_create/', {
            'module': self.maths.id,
            'students': [{'nactvet_reg_no': self.victim.nactvet_reg_no, 'name': 'x', 'portal_pin': 'Chosen#123'}],
        }, format='json')
        # Nobody adds students to modules by hand any more (said 20 October 2026).
        self.assertEqual((response.status_code, response.data['added']), (400, 0))

        signed_in = self.sign_in(self.victim.nactvet_reg_no, 'Chosen#123')
        self.assertNotEqual(signed_in.get('Location'), '/student-dashboard/')
        # The student's own password still signs them in.
        self.assertEqual(self.sign_in(self.victim.nactvet_reg_no, 'Victim#2026').get('Location'),
                         '/student-dashboard/')

    def test_only_the_newest_password_opens_the_portal(self):
        older = Student(nactvet_reg_no=self.victim.nactvet_reg_no, name=self.victim.name,
                        profile=self.victim, module=self.maths)
        older.set_portal_pin('Old#Pass2025', require_change=False)
        older.save()
        Student.objects.filter(pk=older.pk).update(portal_pin_set_at=timezone.now() - timedelta(days=200))

        self.assertIsNone(passwords.student_for_sign_in(self.victim.nactvet_reg_no, 'Old#Pass2025'))
        self.assertEqual(passwords.student_for_sign_in(self.victim.nactvet_reg_no, 'Victim#2026').pk,
                         self.victim_row.pk)

    def test_a_single_add_by_a_tutor_cannot_carry_a_password(self):
        response = self.api(self.tutor).post('/api/students/', {
            'nactvet_reg_no': self.victim.nactvet_reg_no, 'name': 'x', 'module': self.maths.id,
            'portal_pin': 'Chosen#123'}, format='json')
        self.assertEqual(response.status_code, 403)

    def test_the_principal_may_still_issue_a_first_password(self):
        """At the password desk — to an admitted student with no module yet,
        too (said 20 October 2026): admission makes them our student."""
        from .models import SemesterRegistration
        principal = self.office('boss', 'principal')
        new_one = StudentProfile.objects.create(nactvet_reg_no='NS0002/0002/2024', name='New One',
                                                college_id='BPH/PST/2026/0099')
        SemesterRegistration.objects.create(profile=new_one, semester=self.sem, programme=self.pst,
                                            class_level=self.level, kind=SemesterRegistration.NEW)
        issued = self.api(principal).post('/api/student-passwords/issue/', {}, format='json').data['issued']
        pin = {row['reg_no']: row['password'] for row in issued}['NS0002/0002/2024']
        self.assertFalse(Student.objects.filter(nactvet_reg_no='NS0002/0002/2024').exists())   # no module
        for identifier in ('NS0002/0002/2024', 'BPH/PST/2026/0099'):
            self.assertEqual(self.sign_in(identifier, pin).get('Location'), '/course-portal/', identifier)
        self.assertNotEqual(self.sign_in('NS0002/0002/2024', 'Wrong#2026').get('Location'), '/course-portal/')


class SignInTests(SecurityBase):
    """S3 and S8."""

    def test_signing_in_pauses_after_ten_failures_for_a_name(self):
        for _ in range(10):
            self.sign_in('tutor1', 'wrong')
        refused = self.sign_in('tutor1', 'Office#2026x')
        self.assertEqual(refused.status_code, 429)
        self.assertIn('Too many failed attempts', refused.content.decode())

        # A quarter of an hour later it opens again, and success clears the count.
        SignInFailure.objects.update(at=timezone.now() - timedelta(minutes=16))
        self.assertEqual(self.sign_in('tutor1', 'Office#2026x').status_code, 302)
        self.assertFalse(SignInFailure.objects.filter(identifier='tutor1', at__gte=timezone.now() - timedelta(minutes=1)).exists())

    def test_students_are_counted_by_their_number_too(self):
        for _ in range(10):
            self.sign_in(self.victim.nactvet_reg_no, 'wrong')
        self.assertEqual(self.sign_in(self.victim.nactvet_reg_no, 'Victim#2026').status_code, 429)

    def test_one_address_is_allowed_many_names_failing(self):
        # A class shares the college Wi-Fi: 100 failures from one address pause it.
        for n in range(99):
            SignInFailure.objects.create(identifier=f'someone{n}', ip='10.0.0.5')
        self.assertFalse(passwords.sign_in_blocked('tutor1', '10.0.0.5'))
        SignInFailure.objects.create(identifier='someone-else', ip='10.0.0.5')
        self.assertTrue(passwords.sign_in_blocked('tutor1', '10.0.0.5'))

    def test_the_api_refuses_http_basic_passwords(self):
        token = base64.b64encode(b'tutor1:Office#2026x').decode()
        response = Client().get('/api/semesters/', HTTP_AUTHORIZATION=f'Basic {token}')
        self.assertIn(response.status_code, (401, 403))

    def test_a_student_gets_a_new_session_key_at_sign_in(self):
        client = Client()
        client.get('/login/')
        session = client.session
        session['planted'] = 'yes'
        session.save()
        before = session.session_key
        self.sign_in(self.victim.nactvet_reg_no, 'Victim#2026', client=client)
        self.assertNotEqual(client.session.session_key, before)
        self.assertEqual(client.session['student_id'], self.victim_row.id)

    def test_the_address_is_nginxs_not_the_clients(self):
        factory = RequestFactory()
        spoofed = factory.get('/', HTTP_X_FORWARDED_FOR='1.2.3.4, 41.59.10.20', HTTP_X_REAL_IP='41.59.10.20')
        self.assertEqual(finance.client_ip(spoofed), '41.59.10.20')
        no_real = factory.get('/', HTTP_X_FORWARDED_FOR='1.2.3.4, 41.59.10.20')
        self.assertEqual(finance.client_ip(no_real), '41.59.10.20')


class StudentCSRFTests(SecurityBase):
    """S7: the review's reproduction — student POSTs were taken with no token."""

    def student_client(self):
        client = Client(enforce_csrf_checks=True)
        client.get('/login/')                                   # sets the CSRF cookie
        client.post('/login/', {'identifier': self.victim.nactvet_reg_no, 'secret': 'Victim#2026',
                                'csrfmiddlewaretoken': client.cookies['csrftoken'].value})
        self.assertIn('student_id', client.session)
        return client

    def test_a_student_post_without_the_token_is_refused(self):
        client = self.student_client()
        response = client.post('/api/my-notifications/', {}, content_type='application/json')
        self.assertEqual(response.status_code, 403)
        self.assertIn('CSRF', response.json()['detail'])

    def test_with_the_token_it_goes_through(self):
        client = self.student_client()
        token = client.cookies['csrftoken'].value
        response = client.post('/api/my-notifications/', {}, content_type='application/json',
                               HTTP_X_CSRFTOKEN=token)
        self.assertNotEqual(response.status_code, 403)


class DefaultPermissionTests(SecurityBase):
    """S11: a view that forgot its permission class is not open to anybody."""

    def test_the_default_is_signed_in_staff_or_a_student(self):
        from rest_framework.decorators import api_view
        from rest_framework.response import Response

        @api_view(['GET'])
        def forgot_its_permission(request):
            return Response({'ok': True})

        factory = RequestFactory()
        anonymous = factory.get('/')
        anonymous.session = {}
        anonymous.user = mock.Mock(is_authenticated=False)
        self.assertEqual(forgot_its_permission(anonymous).status_code, 403)

        student = factory.get('/')
        student.session = {'student_id': self.victim_row.id}
        student.user = mock.Mock(is_authenticated=False)
        self.assertEqual(forgot_its_permission(student).status_code, 200)


class ThrottleTests(SecurityBase):
    def test_a_student_session_is_throttled_on_its_own(self):
        client = Client()
        self.sign_in(self.victim.nactvet_reg_no, 'Victim#2026', client=client)
        with mock.patch.object(security.PortalRateThrottle, 'THROTTLE_RATES', {'portal': '3/min'}):
            codes = [client.get('/api/my-notifications/').status_code for _ in range(4)]
        self.assertEqual(codes[:3], [200, 200, 200])
        self.assertEqual(codes[3], 429)

    def test_staff_are_throttled_per_account(self):
        client = self.api(self.tutor)
        with mock.patch.object(security.StaffRateThrottle, 'THROTTLE_RATES', {'staff': '2/min'}):
            codes = [client.get('/api/semesters/').status_code for _ in range(3)]
        self.assertEqual(codes, [200, 200, 429])


class ReceiptTests(SecurityBase):
    """S4: the review's reproduction — receipt.html opened inline for the Principal."""

    def setUp(self):
        super().setUp()
        self.accountant = self.office('money', 'accountant')
        self.principal = self.office('boss', 'principal')
        self.category = ExpenseCategory.objects.create(name='Stationery')

    def record(self, upload):
        return self.api(self.accountant).post('/api/expenses/', {
            'category': self.category.id, 'description': 'x', 'payee': 'y', 'amount': '1000',
            'incurred_on': '2026-10-08', 'attachment': upload}, format='multipart')

    def test_an_html_receipt_is_refused(self):
        response = self.record(SimpleUploadedFile('receipt.html', b'<script>alert(1)</script>',
                                                  content_type='text/html'))
        self.assertEqual(response.status_code, 400)
        self.assertIn('PDF, JPG or PNG', response.data['detail'])

    def test_a_disguised_file_is_refused(self):
        response = self.record(SimpleUploadedFile('receipt.pdf', b'<html><script>x</script>',
                                                  content_type='application/pdf'))
        self.assertEqual(response.status_code, 400)
        self.assertIn('not a real PDF', response.data['detail'])

    def test_a_real_receipt_is_served_as_a_download(self):
        made = self.record(SimpleUploadedFile('receipt.pdf', b'%PDF-1.4 a receipt', content_type='application/pdf'))
        self.assertEqual(made.status_code, 201, made.data)
        response = self.api(self.principal).get(f'/api/expenses/{made.data["id"]}/attachment/')
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertTrue(response['Content-Disposition'].startswith('attachment'))
        self.assertEqual(response['Content-Security-Policy'], 'sandbox')


class PasswordDeskTests(SecurityBase):
    """S9, as the college runs it: the ICT officer is the desk for everybody's
    forgotten password — the Principal's and students' too."""

    def setUp(self):
        super().setUp()
        self.ict = self.office('ict', 'ict_officer')
        self.principal = self.office('boss', 'principal')

    def test_the_ict_officer_resets_the_principals_password_and_it_is_recorded(self):
        response = self.api(self.ict).post(f'/api/staff-accounts/{self.principal.id}/reset-password/', {},
                                           format='json')
        self.assertEqual(response.status_code, 200, response.data)
        temporary = response.data['password']
        self.assertEqual(self.sign_in('boss', temporary).status_code, 302)
        self.assertTrue(PasswordStatus.objects.get(user=self.principal).must_change)
        row = PasswordReset.objects.get(user=self.principal)
        self.assertEqual((row.kind, row.done_by), (PasswordReset.STAFF, self.ict))
        told = Notification.objects.get(user=self.principal, kind=Notification.PASSWORD_RESET)
        self.assertIn('Reset by ict', told.body)

    def test_a_password_given_by_hand_must_pass_the_rules(self):
        response = self.api(self.ict).post(f'/api/staff-accounts/{self.principal.id}/reset-password/',
                                           {'password': '123456'}, format='json')
        self.assertEqual(response.status_code, 400)

    def test_the_ict_officer_resets_a_students_password_by_college_id(self):
        response = self.api(self.ict).post('/api/student-passwords/reset/',
                                           {'identifier': 'BPH/PST/2026/001'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        fresh = response.data['password']
        self.assertIsNone(passwords.student_for_sign_in(self.victim.nactvet_reg_no, 'Victim#2026'))
        self.assertIsNotNone(passwords.student_for_sign_in(self.victim.nactvet_reg_no, fresh))
        self.victim_row.refresh_from_db()
        self.assertTrue(self.victim_row.must_change_portal_password)
        self.assertTrue(PasswordReset.objects.filter(profile=self.victim, kind=PasswordReset.STUDENT).exists())
        self.assertTrue(Notification.objects.filter(profile=self.victim, kind=Notification.PASSWORD_RESET).exists())
        listed = self.api(self.ict).get('/api/password-resets/').data
        self.assertEqual(listed[0]['account'], self.victim.nactvet_reg_no)

    def test_nobody_else_resets(self):
        # The ICT officer and the Principal only (said 8 October 2026) — not
        # the records or admission desks either.
        for user in (self.tutor, self.office('money', 'accountant'),
                     self.office('records1', 'records_officer'), self.office('adm1', 'admission_officer')):
            self.assertEqual(self.api(user).post('/api/student-passwords/reset/',
                                                 {'identifier': self.victim.nactvet_reg_no},
                                                 format='json').status_code, 403)
            self.assertEqual(self.api(user).post('/api/student-passwords/issue/', {}, format='json').status_code, 403)
            self.assertEqual(self.api(user).get('/api/password-resets/').status_code, 403)
        self.assertEqual(self.api(self.tutor).post('/api/students/bulk_set_pin/', {'portal_pin': 'Abc#12345'},
                                                   format='json').status_code, 403)


class MedicalDocumentTests(SecurityBase):
    """S12: a medical certificate is health information."""

    def test_finance_does_not_see_or_open_a_medical_certificate(self):
        records = self.office('records1', 'records_officer')
        accountant = self.office('money', 'accountant')
        made = self.api(records).post('/api/student-documents/', {
            'profile': self.victim.id, 'kind': 'medical',
            'file': SimpleUploadedFile('m.pdf', b'%PDF-1.4 cert', content_type='application/pdf')},
            format='multipart')
        self.assertEqual(made.status_code, 201, made.data)
        self.assertEqual([d['id'] for d in self.api(records).get(
            f'/api/student-documents/?profile_id={self.victim.id}').data], [made.data['id']])
        self.assertEqual(self.api(accountant).get(f'/api/student-documents/?profile_id={self.victim.id}').data, [])
        client = Client()
        client.force_login(accountant)
        self.assertEqual(client.get(f'/api/student-documents/{made.data["id"]}/file/').status_code, 404)


class ExportTests(TestCase):
    def test_text_that_looks_like_a_formula_stays_text(self):
        from openpyxl import Workbook
        wb = Workbook()
        wb.active['A1'] = '=HYPERLINK("http://example.com","click")'
        wb.active['A2'] = 'Asha'
        security.neutralise_formulas(wb)
        self.assertEqual((wb.active['A1'].data_type, wb.active['A2'].data_type), ('s', 's'))


class HeaderTests(SecurityBase):
    def test_pages_carry_the_content_security_policy(self):
        response = Client().get('/login/')
        self.assertIn("object-src 'none'", response['Content-Security-Policy'])
        self.assertIn('camera=()', response['Permissions-Policy'])
