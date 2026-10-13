"""The dashboard keeps one function to a menu item, and new students come in
through Admissions only (said 13 October 2026):

  * one Students menu — Find a student, Enrollments, College ID import — not a
    Students menu and a Student Records menu for the same people;
  * no "Upload / Register": adding someone to a module is only for a student
    already admitted, refused on the server for anybody else;
  * Results: the authority grade import and the mark entry windows are their own
    items, and every card says which items it belongs to; the role rule that
    showed office-only cards in every item no longer overrides that;
  * Eligibility: the final eligibility export is its own view.

The student portal, the same way:
  * the password alert is at the top of the dashboard, not inside the profile;
  * Request a Service offers its options — hostel, postponement, each open
    service — and opens one at a time;
  * My Profile offers personal info, academic info, next of kin, documents and
    password, one at a time.
"""

import re

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from datetime import timedelta

from django.utils import timezone

from .models import AcademicYear, ClassLevel, Module, Semester, Student, StudentProfile
from .views import set_roles

User = get_user_model()


def office(username, *roles):
    user = User.objects.create_user(username, password='pw')
    set_roles(user, list(roles), full_name=username.title())
    client = APIClient()
    client.force_login(user)
    return user, client


class AdmittedOnlyTests(TestCase):
    def setUp(self):
        year = AcademicYear.objects.create(name='2026/2027', is_active=True)
        sem = Semester.objects.create(academic_year=year, number=1, is_active=True)
        self.module = Module.objects.create(name='M', code='PST04101', teacher='T', semester=sem, credits=4,
                                            class_level=ClassLevel.objects.create(name='NTA Level 4', order=4))
        _, self.hod = office('hod', 'hod')

    def bulk(self, reg_no):
        return self.hod.post('/api/students/bulk_create/', {
            'module': self.module.id, 'students': [{'nactvet_reg_no': reg_no, 'name': 'Someone'}]}, format='json')

    def test_nobody_new_through_the_roster(self):
        response = self.bulk('NS9999/0001/2026')
        self.assertEqual((response.data['added'], response.data['not_admitted']), (0, ['NS9999/0001/2026']))
        self.assertIn('Admissions', response.data['detail'])
        self.assertFalse(Student.objects.filter(nactvet_reg_no='NS9999/0001/2026').exists())

    def test_nobody_new_one_at_a_time_either(self):
        response = self.hod.post('/api/students/', {'nactvet_reg_no': 'NS9999/0002/2026', 'name': 'Y',
                                                    'module': self.module.id}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('Admissions', str(response.data))

    def test_an_admitted_student_missing_a_module_is_added(self):
        StudentProfile.objects.create(nactvet_reg_no='NS0001/0001/2026', name='Admitted')
        self.assertEqual(self.bulk('ns0001/0001/2026').data['added'], 1)


class OneMenuItemOneViewTests(TestCase):
    def setUp(self):
        AcademicYear.objects.create(name='2026/2027', is_active=True)
        _, self.principal = office('boss', 'principal')
        self.html = self.principal.get('/').content.decode()

    def test_one_students_menu(self):
        self.assertNotIn('records-nav-group', self.html)
        self.assertEqual(self.html.count('id="students-nav-group"'), 1)
        group = self.html.split('id="students-nav-group"')[1].split('</div>\n    </div>')[0]
        self.assertEqual(re.findall(r'>([^<>]+)</button>', group),
                         ['Find a student', 'Enrollments', 'College ID import'])
        self.assertNotIn('Upload / Register', self.html)
        self.assertNotIn('Bulk Upload via Excel', self.html)

    def test_results_items_and_their_cards(self):
        for section in ('ca', 'final', 'field', 'analysis', 'authority', 'summary', 'windows'):
            self.assertIn(f'data-results-section="{section}"', self.html)
        page = self.html.split('id="page-results"')[1].split('<div class="page" id=')[0]
        for card in ('result-window-card', 'authority-grade-card', 'results-summary-card', 'cat-analysis-card'):
            tag = re.search(rf'<div [^>]*id="{card}"[^>]*>', page).group(0)
            self.assertIn('data-res-view=', tag, card)
            self.assertNotIn('display:none', tag, card)          # the switcher decides, not a fixed style
        self.assertIn('body.is-admin .admin-only.card:not(.sec-off)', self.html)

    def test_final_eligibility_is_its_own_view(self):
        page = self.html.split('id="page-eligibility"')[1].split('<div class="page" id=')[0]
        filters = page.split('id="elig-final-card"')[0]
        self.assertNotIn('final-elig-scope', filters)
        self.assertIn('elig-final-view sec-off', page)

    def test_no_stale_spending_card_under_finance(self):
        self.assertNotIn('Spending is not recorded in the system yet', self.html)


class PortalOptionsTests(TestCase):
    def setUp(self):
        year = AcademicYear.objects.create(name='2026/2027', is_active=True)
        sem = Semester.objects.create(academic_year=year, number=1, is_active=True)
        module = Module.objects.create(name='M', code='PST04101', teacher='T', semester=sem, credits=4,
                                       class_level=ClassLevel.objects.create(name='NTA Level 4', order=4))
        StudentProfile.objects.create(nactvet_reg_no='NS0001/0001/2026', name='Asha')
        self.student = Student.objects.create(nactvet_reg_no='NS0001/0001/2026', name='Asha', module=module)
        self.student.set_portal_pin('Portal#2026x', require_change=False)
        self.student.save()
        session = self.client.session
        session['student_id'] = self.student.id
        session.save()

    def page(self):
        return self.client.get('/student-dashboard/').content.decode()

    def test_the_password_alert_is_on_the_dashboard(self):
        Student.objects.filter(id=self.student.id).update(portal_pin_set_at=timezone.now() - timedelta(days=170))
        html = self.page()
        dashboard = html.split('id="view-dashboard"')[1].split('<section class="view"')[0]
        self.assertIn('id="password-alert"', dashboard)
        self.assertLess(dashboard.index('id="password-alert"'), dashboard.index('Welcome,'))
        profile = html.split('id="view-profile"')[1].split('</main>')[0]
        self.assertNotIn('password-alert', profile)

    def test_no_alert_while_the_password_is_fresh(self):
        self.assertNotIn('id="password-alert"', self.page())

    def test_services_are_options(self):
        services = self.page().split('id="view-services"')[1].split('id="view-my-requests"')[0]
        self.assertIn('data-service="hostel"', services)
        self.assertIn('data-service="postpone"', services)
        self.assertIn('data-service-pane="hostel"', services)
        self.assertNotIn('What you can ask for', services)

    def test_the_profile_is_options(self):
        profile = self.page().split('id="view-profile"')[1].split('</main>')[0]
        options = re.findall(r'data-profile-option="([a-z]+)"', profile)
        self.assertEqual(options, ['personal', 'academic', 'kin', 'documents', 'password'])
        self.assertEqual(profile.count('class="pane on"'), 1)
