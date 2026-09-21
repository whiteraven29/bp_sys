"""The password policy: six months, then change it, then see the office.

The same clock for a member of staff and for a student, told the same way:
the dashboards count it down, the door enforces it, and an account that let
the grace run out is the administrator's to reset.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from . import passwords
from .models import (
    AcademicYear, ClassLevel, Module, PasswordStatus, Programme, Department,
    Semester, Student, StudentProfile,
)
from .views import set_roles

User = get_user_model()


def age(user, days):
    """Backdate when this account's password was set."""
    status = passwords.status_for(user)
    status.changed_at = timezone.now() - timedelta(days=days)
    status.save(update_fields=['changed_at'])
    return status


class StaffPasswordTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('tutor', password='pass12345')
        self.admin = User.objects.create_superuser('exam', 'e@x.com', 'pass12345')

    def test_a_fresh_password_is_good_for_six_months(self):
        reading = passwords.reading_for(self.user)

        self.assertEqual(reading['state'], passwords.OK)
        self.assertEqual(reading['days_left'], 181)
        self.assertFalse(reading['must_change'])
        self.assertIn('another', reading['message'])

    def test_the_last_fortnight_says_so(self):
        age(self.user, 175)

        reading = passwords.reading_for(self.user)

        self.assertEqual(reading['state'], passwords.EXPIRING)
        self.assertIn('expires in', reading['message'])

    def test_after_six_months_it_must_be_changed(self):
        age(self.user, 183)

        reading = passwords.reading_for(self.user)

        self.assertEqual(reading['state'], passwords.EXPIRED)
        self.assertTrue(reading['must_change'])
        self.assertIn('Change it now', reading['message'])

    def test_when_the_grace_runs_out_only_the_office_can_help(self):
        age(self.user, 183 + 15)

        reading = passwords.reading_for(self.user)

        self.assertEqual(reading['state'], passwords.LOCKED)
        self.assertIn('See the administrator', reading['message'])
        self.assertTrue(passwords.is_locked(self.user))

    def test_an_expired_account_is_sent_to_the_change_page_and_nowhere_else(self):
        age(self.user, 185)
        self.client.force_login(self.user)

        anywhere = self.client.get('/')

        self.assertEqual(anywhere.status_code, 302)
        self.assertEqual(anywhere.url, reverse('password-change'))
        # And that page itself opens, or there would be no way back.
        self.assertEqual(self.client.get(reverse('password-change')).status_code, 200)

    def test_a_locked_account_is_signed_out_at_the_next_page(self):
        age(self.user, 400)
        self.client.force_login(self.user)

        response = self.client.get('/', follow=True)

        self.assertEqual(response.redirect_chain[-1][0], reverse('login') + '?expired=1')
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_changing_the_password_starts_the_six_months_again(self):
        age(self.user, 185)
        api = APIClient()
        api.force_authenticate(self.user)

        response = api.post('/api/change-password/',
                            {'current_password': 'pass12345', 'new_password': 'Fresh#2026pw'},
                            format='json')

        self.assertEqual(response.status_code, 200, response.data)
        reading = passwords.reading_for(self.user)
        self.assertEqual(reading['state'], passwords.OK)
        self.assertFalse(reading['must_change'])

    def test_the_office_resets_a_locked_account_and_the_holder_picks_their_own(self):
        age(self.user, 400)
        api = APIClient()
        api.force_authenticate(self.admin)

        response = api.post(f'/api/staff-accounts/{self.user.id}/reset-password/', {}, format='json')

        self.assertEqual(response.status_code, 200, response.data)
        given = response.data['password']
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(given))
        reading = passwords.reading_for(self.user)
        # Not locked any more, but the given password is temporary.
        self.assertEqual(reading['state'], passwords.EXPIRED)
        self.assertTrue(reading['must_change'])
        self.assertEqual(PasswordStatus.objects.get(user=self.user).reset_by, self.admin)

    def test_a_tutor_cannot_reset_somebody_else_s_password(self):
        other = User.objects.create_user('other', password='pass12345')
        api = APIClient()
        api.force_authenticate(self.user)

        response = api.post(f'/api/staff-accounts/{other.id}/reset-password/', {}, format='json')

        self.assertEqual(response.status_code, 403)

    def test_the_dashboard_says_when_it_expires(self):
        age(self.user, 175)
        api = APIClient()
        api.force_authenticate(self.user)

        response = api.get('/api/dashboard/')

        self.assertEqual(response.data['password']['state'], passwords.EXPIRING)
        self.assertIn('expires_on', response.data['password'])

    @override_settings(PASSWORD_MAX_AGE_DAYS=30, PASSWORD_GRACE_DAYS=3)
    def test_the_college_can_change_the_policy(self):
        age(self.user, 31)
        self.assertEqual(passwords.reading_for(self.user)['state'], passwords.EXPIRED)
        age(self.user, 35)
        self.assertEqual(passwords.reading_for(self.user)['state'], passwords.LOCKED)


class StudentPasswordTests(TestCase):
    def setUp(self):
        year = AcademicYear.objects.create(name='2026/2027', is_active=True)
        semester = Semester.objects.create(academic_year=year, number=1, is_active=True)
        level = ClassLevel.objects.create(name='NTA Level 4', order=4)
        department = Department.objects.create(name='Pharmaceutical Sciences', code='PST')
        programme = Programme.objects.create(department=department, name='PS', code='PST')
        module = Module.objects.create(name='PST04101', code='PST04101', teacher='T',
                                       class_level=level, semester=semester, programme=programme)
        self.profile = StudentProfile.objects.create(nactvet_reg_no='NIT/PST/2026/1', name='Amina')
        self.student = Student.objects.create(
            nactvet_reg_no=self.profile.nactvet_reg_no, name=self.profile.name,
            profile=self.profile, module=module)
        self.student.set_portal_pin('Portal#2026', require_change=False)
        self.student.save()

    def backdate(self, days):
        Student.objects.filter(pk=self.student.pk).update(
            portal_pin_set_at=timezone.now() - timedelta(days=days))
        self.student.refresh_from_db()

    def sign_in(self):
        return self.client.post('/login/', {'identifier': self.profile.nactvet_reg_no,
                                            'secret': 'Portal#2026'})

    def test_setting_a_portal_password_stamps_the_day(self):
        self.assertIsNotNone(self.student.portal_pin_set_at)
        self.assertEqual(passwords.student_reading(self.student)['state'], passwords.OK)

    def test_a_student_is_warned_before_it_expires(self):
        self.backdate(175)

        reading = passwords.student_reading(self.student)

        self.assertEqual(reading['state'], passwords.EXPIRING)

    def test_an_expired_portal_password_forces_a_change_at_sign_in(self):
        self.backdate(185)

        response = self.sign_in()

        self.assertEqual(response.status_code, 302)
        self.student.refresh_from_db()
        self.assertTrue(self.student.must_change_portal_password)
        # The portal's own gate then puts them on the change page.
        page = self.client.get('/student-dashboard/')
        self.assertContains(page, 'password')

    def test_a_student_who_let_the_grace_run_out_is_sent_to_the_office(self):
        self.backdate(400)

        response = self.sign_in()

        self.assertEqual(response.status_code, 200)
        self.assertNotIn('student_id', self.client.session)
        self.assertIn('See the administrator', response.context['error'])

    def test_changing_it_from_the_portal_starts_the_clock_again(self):
        self.backdate(185)
        self.sign_in()

        response = self.client.post(
            '/api/change-password/',
            {'current_password': 'Portal#2026', 'new_password': 'Mpya#2026pw'},
            content_type='application/json')

        self.assertEqual(response.status_code, 200, response.content)
        self.student.refresh_from_db()
        self.assertEqual(passwords.student_reading(self.student)['state'], passwords.OK)

    def test_the_portal_tells_the_student_where_they_stand(self):
        self.backdate(175)
        self.sign_in()

        response = self.client.get('/student-dashboard/')

        self.assertEqual(response.context['password']['state'], passwords.EXPIRING)
