"""Lists cost the same number of queries for three rows as for thirty.

Said 22 October 2026: the system felt slow. Measured, the admission list ran
eleven queries a row (3,313 for 300 applications, 2.7 s), the module list five
a module, and every "which office is this?" was a query of its own, asked a
dozen times a request. These keep it from creeping back.
"""

from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext

from . import admissions, roles
from .models import Application
from .test_admissions import AdmissionBase
from .test_finance_desk_only import office
from .views import set_roles

User = get_user_model()


class ListsStayFlatTests(AdmissionBase):
    def setUp(self):
        super().setUp()
        self.fee('Tuition fee', '1000000')
        _, self.desk = office('desk', 'admission_officer')
        _, self.boss = office('boss', 'principal')
        self.made = 0
        self.applications(3)

    def applications(self, how_many):
        for _ in range(how_many):
            self.made += 1
            profile = self.applicant(f'NACTVET/PENDING/{self.made:03d}', f'Student {self.made}')
            application = admissions.open_application(
                profile=profile, semester=self.sem1, programme=self.pst, class_level=self.level4,
                kind=Application.NEW, actor=self.officer)
            admissions.complete_step(application, actor=self.officer)     # a step with a name on it

    def queries(self, client, url):
        client.get(url)          # what a first request warms is not the list's cost
        with CaptureQueriesContext(connection) as ran:
            response = client.get(url)
        self.assertEqual(response.status_code, 200)
        return len(ran), response

    def test_the_application_list(self):
        few, response = self.queries(self.desk, '/api/applications/')
        self.assertEqual(len(response.data), 3)
        self.applications(6)
        many, response = self.queries(self.desk, '/api/applications/')
        self.assertEqual(len(response.data), 9)
        self.assertEqual(many, few)

    def test_the_module_list(self):
        for n in range(3):
            self.module(f'PST04{n}', self.level4, self.sem1)
        few, _ = self.queries(self.boss, '/api/modules/')
        for n in range(3, 9):
            self.module(f'PST04{n}', self.level4, self.sem1)
        many, response = self.queries(self.boss, '/api/modules/')
        self.assertEqual(len(response.data), 9)
        self.assertEqual(many, few)

    def test_the_staff_list_and_its_rows_are_the_same(self):
        from .views import staff_account_row
        few, _ = self.queries(self.boss, '/api/staff-accounts/')
        for n in range(4):
            office(f'tutor{n}', 'tutor')
            office(f'money{n}', 'accountant', 'secretary')
        many, response = self.queries(self.boss, '/api/staff-accounts/')
        self.assertEqual(many, few)
        self.assertGreaterEqual(len(response.data), 10)
        for row in response.data:
            self.assertEqual(row, staff_account_row(User.objects.get(pk=row['id'])))


class OfficeLookupTests(AdmissionBase):
    """Within a request an office is looked up once; given or taken, it is
    seen at once."""

    def test_asked_twice_looked_up_once_and_never_stale(self):
        user = User.objects.create_user('someone', password='pw')
        token = roles._known.set({})
        try:
            self.assertFalse(roles.is_principal(user))
            with self.assertNumQueries(0):
                roles.is_principal(user)
            set_roles(user, ['principal'], full_name='Some One')
            self.assertTrue(roles.is_principal(user))
            set_roles(user, ['tutor'], full_name='Some One')
            self.assertFalse(roles.is_principal(user))
        finally:
            roles._known.reset(token)

    def test_outside_a_request_it_asks_every_time(self):
        user = User.objects.create_user('someone', password='pw')
        with self.assertNumQueries(2):
            roles.is_principal(user)
            roles.is_principal(user)


class SlowRequestLogTests(AdmissionBase):
    """A slow request is written down with its query count — its path only,
    never what was searched for."""

    def test_it_names_the_page_not_the_search(self):
        from django.test import override_settings
        with override_settings(SLOW_REQUEST_MS=1):
            _, desk = office('desk', 'admission_officer')
            with self.assertLogs('edutrack.slow', 'WARNING') as logged:
                desk.get('/api/applications/?search=Rehema')
        line = logged.output[-1]
        self.assertIn('GET /api/applications/ took', line)
        self.assertRegex(line, r'\d+ queries')
        self.assertNotIn('Rehema', line)

    def test_a_quick_request_is_not_written_down(self):
        from django.test import override_settings
        with override_settings(SLOW_REQUEST_MS=60_000):
            _, desk = office('desk', 'admission_officer')
            with self.assertNoLogs('edutrack.slow', 'WARNING'):
                desk.get('/api/applications/')
