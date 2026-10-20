"""Rules from the first day of use (said 19 October 2026):

  * five minutes without use ends a session, staff and student — the page's
    own notification poll does not count as use, a keep-alive does;
  * a form refused for its security token says so and offers the way back,
    instead of Django's bare 403;
  * a Head of Department sees no accounts, only their department's staff, and
    gives only their department's modules to them.
"""

import time

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from rest_framework.test import APIClient

from .models import (
    AcademicYear, ClassLevel, Department, Module, Programme, Semester, Student, StudentProfile,
)
from .views import set_roles

User = get_user_model()


class IdleTimeoutTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('clerk', password='Strong#2026x')
        set_roles(self.user, ['records_officer'], full_name='Clerk')
        self.client = Client()
        self.client.force_login(self.user)

    def idle_for(self, seconds, client=None):
        client = client or self.client
        session = client.session
        session['last_activity'] = time.time() - seconds
        session.save()

    def test_five_minutes_unused_signs_staff_out(self):
        self.assertEqual(self.client.get('/').status_code, 200)
        self.idle_for(301)
        page = self.client.get('/')
        self.assertRedirects(page, '/login/?idle=1', fetch_redirect_response=False)
        self.assertEqual(self.client.get('/').status_code, 302)     # signed out for good

    def test_an_api_call_after_the_limit_says_why(self):
        self.idle_for(301)
        response = self.client.get('/api/notifications/')
        self.assertEqual(response.status_code, 401)
        self.assertTrue(response.json()['idle'])

    def test_under_the_limit_carries_on(self):
        self.idle_for(240)
        self.assertEqual(self.client.get('/').status_code, 200)

    def test_the_notification_poll_is_not_use(self):
        self.idle_for(200)
        before = self.client.session['last_activity']
        self.client.get('/api/notifications/')
        self.assertEqual(self.client.session['last_activity'], before)

    def test_the_keep_alive_is_use(self):
        self.idle_for(200)
        self.assertEqual(self.client.post('/keep-alive/').status_code, 200)
        self.assertLess(time.time() - self.client.session['last_activity'], 5)
        self.assertEqual(Client().post('/keep-alive/').status_code, 401)

    def test_a_student_portal_session_ends_too(self):
        year = AcademicYear.objects.create(name='2026/2027', is_active=True)
        semester = Semester.objects.create(academic_year=year, number=1, is_active=True)
        level = ClassLevel.objects.create(name='NTA Level 4', order=4)
        module = Module.objects.create(name='M', code='PST04101', teacher='T', semester=semester,
                                       class_level=level, credits=4)
        StudentProfile.objects.create(nactvet_reg_no='NS0001/0001/2026', name='Asha')
        enrollment = Student.objects.create(nactvet_reg_no='NS0001/0001/2026', name='Asha', module=module)
        portal = Client()
        session = portal.session
        session['student_id'] = enrollment.id
        session['last_activity'] = time.time() - 400
        session.save()
        page = portal.get('/student-dashboard/')
        self.assertRedirects(page, '/login/?idle=1', fetch_redirect_response=False)
        self.assertNotIn('student_id', portal.session)


class CsrfFailureTests(TestCase):
    def test_a_sign_in_form_without_its_cookie_is_sent_back_to_sign_in(self):
        browser = Client(enforce_csrf_checks=True)
        response = browser.post('/login/', {'identifier': 'x', 'secret': 'y'})
        self.assertRedirects(response, '/login/?csrf=1', fetch_redirect_response=False)
        page = browser.get('/login/?csrf=1')
        self.assertContains(page, 'open too long')
        self.assertIn('csrftoken', page.cookies)                  # the page always sets it
        self.assertIn('no-cache', page['Cache-Control'])           # and is never served from cache


class HeadOfDepartmentTests(TestCase):
    def setUp(self):
        def office(username, role):
            user = User.objects.create_user(username, password='Strong#2026x')
            set_roles(user, [role], full_name=username.title())
            client = APIClient()
            client.force_authenticate(user)
            return user, client

        self.hod_user, self.hod = office('hod', 'hod')
        self.mine_user, _ = office('mine', 'tutor')
        self.other_user, _ = office('other', 'tutor')
        self.boss_user, self.boss = office('boss', 'principal')
        pst = Department.objects.create(name='Pharmaceutical Sciences', code='PST', hod=self.hod_user)
        vet = Department.objects.create(name='Vocational', code='VET')
        pst.staff.add(self.mine_user)
        vet.staff.add(self.other_user)
        year = AcademicYear.objects.create(name='2026/2027', is_active=True)
        semester = Semester.objects.create(academic_year=year, number=1, is_active=True)
        level = ClassLevel.objects.create(name='NTA Level 4', order=4)
        self.pst_module = Module.objects.create(
            name='A', code='PST04101', teacher='T', semester=semester, class_level=level, credits=4,
            programme=Programme.objects.create(department=pst, name='Pharm', code='PST'))
        self.vet_module = Module.objects.create(
            name='B', code='HD101', teacher='T', semester=semester, class_level=level, credits=4,
            programme=Programme.objects.create(department=vet, name='Hair', code='HD'))

    def test_the_hod_sees_only_their_department_staff(self):
        names = {row['username'] for row in self.hod.get('/api/staff-accounts/').data}
        self.assertEqual(names, {'mine'})
        everyone = {row['username'] for row in self.boss.get('/api/staff-accounts/').data}
        self.assertTrue({'mine', 'other', 'hod'} <= everyone)

    def test_the_hod_gives_only_their_modules_to_their_staff(self):
        url = f'/api/staff-accounts/{self.mine_user.id}/set-modules/'
        self.assertEqual(self.hod.post(url, {'module_ids': [self.pst_module.id]}, format='json').status_code, 200)
        self.assertEqual(self.hod.post(url, {'module_ids': [self.vet_module.id]}, format='json').status_code, 403)
        outsider = f'/api/staff-accounts/{self.other_user.id}/set-modules/'
        self.assertEqual(self.hod.post(outsider, {'module_ids': [self.pst_module.id]}, format='json').status_code, 403)

    def test_the_hod_keeps_no_account_powers(self):
        self.assertEqual(self.hod.post('/api/staff-accounts/', {'role': 'tutor', 'username': 'x',
                                                               'full_name': 'X', 'password': 'Strong#2026x'},
                                       format='json').status_code, 403)
        self.assertEqual(self.hod.get('/api/staff-accounts/role-changes/').status_code, 403)
