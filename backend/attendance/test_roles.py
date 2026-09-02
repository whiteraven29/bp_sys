"""Who may do what.

The college's separation of duties is the point of these roles, not a detail of
them: the person who runs the department is not the person who decides who sits
an exam, and neither of them is the person who decides whether the money
arrived. Each of those boundaries is a test here, because a role that quietly
gains a power nobody granted it is exactly the failure nobody notices.
"""

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from rest_framework.test import APIClient

from .models import (
    AcademicYear, AccountantProfile, ClassLevel, EstateOfficerProfile,
    HeadOfDepartmentProfile, Module, PrincipalProfile, Semester, Student,
    StudentResult,
)

User = get_user_model()


class RoleTestBase(TestCase):
    def setUp(self):
        self.year = AcademicYear.objects.create(name='2026/2027', is_active=True)
        self.sem = Semester.objects.create(academic_year=self.year, number=1, is_active=True)
        self.level = ClassLevel.objects.create(name='NTA Level 4', order=4)
        self.module = Module.objects.create(name='Pharmaceutics', code='PHM101', teacher='T',
                                            class_level=self.level, semester=self.sem)
        Student.objects.create(nactvet_reg_no='BPH/2026/001', name='Asha Juma',
                               module=self.module)

        self.admin = User.objects.create_superuser('admin', 'a@b.c', 'pw')
        self.api = APIClient()
        self.api.force_authenticate(self.admin)

    def _account(self, role, username):
        made = self.api.post('/api/staff-accounts/', {
            'role': role, 'full_name': f'{role.title()} Person',
            'username': username, 'password': 'secret123',
        }, format='json')
        self.assertEqual(made.status_code, 201, made.data)
        user = User.objects.get(username=username)
        client = APIClient()
        client.force_authenticate(user)
        return user, client

    def _browser(self, user):
        """The Excel exports are plain Django views behind @login_required, so
        they need a real session rather than DRF's forced authentication."""
        browser = Client()
        browser.force_login(user)
        return browser


class CreatingTheAccountsTests(RoleTestBase):
    def test_the_principal_is_created_with_admin_rights(self):
        user, _ = self._account('principal', 'principal')
        self.assertTrue(user.is_staff)
        self.assertTrue(PrincipalProfile.objects.filter(user=user).exists())

    def test_the_head_of_department_is_created_with_admin_rights(self):
        user, _ = self._account('hod', 'hod')
        self.assertTrue(user.is_staff)
        self.assertTrue(HeadOfDepartmentProfile.objects.filter(user=user).exists())

    def test_both_appear_in_the_staff_list_under_their_own_names(self):
        self._account('principal', 'principal')
        self._account('hod', 'hod')
        listed = {row['username']: row['role'] for row in self.api.get('/api/staff-accounts/').json()}
        self.assertEqual(listed['principal'], 'principal')
        self.assertEqual(listed['hod'], 'hod')

    def test_a_role_nobody_has_defined_is_refused(self):
        refused = self.api.post('/api/staff-accounts/', {
            'role': 'vice-chancellor', 'full_name': 'Someone',
            'username': 'someone', 'password': 'secret123',
        }, format='json')
        self.assertEqual(refused.status_code, 400)
        self.assertFalse(User.objects.filter(username='someone').exists())


class PrincipalTests(RoleTestBase):
    """Everything academic; nothing to do with money or college property."""

    def setUp(self):
        super().setUp()
        self.user, self.client_api = self._account('principal', 'principal')

    def test_the_principal_owns_examinations(self):
        self.assertEqual(self.client_api.get('/api/results/').status_code, 200)
        self.assertEqual(self.client_api.get('/api/eligibility/').status_code, 200)
        self.assertEqual(self.client_api.get('/api/finance-obligations/').status_code, 200)
        download = self._browser(self.user).get(
            f'/api/results/download/?module_id={self.module.id}')
        self.assertEqual(download.status_code, 200)
        self.assertIn('spreadsheetml', download['Content-Type'])

    def test_the_principal_runs_the_department(self):
        for route in ('/api/modules/', '/api/students/', '/api/forms/', '/api/sick-records/'):
            self.assertEqual(self.client_api.get(route).status_code, 200, route)

    def test_the_principal_is_kept_out_of_the_ledger(self):
        for route in ('/api/charge-types/', '/api/payments/', '/api/finance-audit/',
                      '/api/invoices/'):
            self.assertEqual(self.client_api.get(route).status_code, 403, route)

    def test_the_principal_is_kept_out_of_college_property(self):
        for route in ('/api/assets/', '/api/asset-transfers/', '/api/inventory-locations/'):
            self.assertEqual(self.client_api.get(route).status_code, 403, route)

    def test_the_dashboard_says_who_they_are(self):
        payload = self.client_api.get('/api/dashboard/').json()
        self.assertTrue(payload['is_principal'])
        self.assertTrue(payload['can_manage_exams'])
        self.assertFalse(payload['is_head_of_department'])
        self.assertFalse(payload['is_accountant'])


class HeadOfDepartmentTests(RoleTestBase):
    """Runs the department. Not examinations, not money, not property."""

    def setUp(self):
        super().setUp()
        self.user, self.client_api = self._account('hod', 'hod')

    def test_the_head_of_department_runs_the_department(self):
        for route in ('/api/modules/', '/api/students/', '/api/forms/', '/api/sick-records/',
                      '/api/sessions/', '/api/academic-years/', '/api/announcements/'):
            self.assertEqual(self.client_api.get(route).status_code, 200, route)

    def test_examinations_are_theirs_to_read(self):
        """Running a department means watching how its students are doing."""
        self.assertEqual(self.client_api.get('/api/results/').status_code, 200)
        self.assertEqual(self.client_api.get('/api/eligibility/').status_code, 200)
        self.assertEqual(self.client_api.get('/api/finance-obligations/').status_code, 200)
        self.assertEqual(self.client_api.get('/api/performance/').status_code, 200)

    def test_examinations_are_theirs_to_export(self):
        browser = self._browser(self.user)
        for route in ('/api/results/download/', '/api/results/download/final/',
                      '/api/eligibility/download/', '/api/eligibility/final/download/'):
            self.assertEqual(browser.get(route).status_code, 200, route)

    def test_exam_declarations_are_not_theirs_to_make(self):
        self.assertEqual(
            self.client_api.post('/api/finance-obligations/', {}, format='json').status_code, 403)

    def test_a_head_of_department_cannot_enter_a_mark(self):
        student = Student.objects.get(nactvet_reg_no='BPH/2026/001')
        refused = self.client_api.post('/api/results/', {
            'student': student.id, 'cat1_theory': 30,
        }, format='json')
        self.assertEqual(refused.status_code, 403)

    def test_a_head_of_department_cannot_approve_a_mark(self):
        student = Student.objects.get(nactvet_reg_no='BPH/2026/001')
        result = StudentResult.objects.create(student=student, cat1_theory=30)
        refused = self.client_api.patch(f'/api/results/{result.id}/',
                                        {'ca_approved': True}, format='json')
        self.assertEqual(refused.status_code, 403)
        result.refresh_from_db()
        self.assertFalse(result.ca_approved)

    def test_money_and_property_are_not_theirs_either(self):
        for route in ('/api/charge-types/', '/api/payments/', '/api/assets/',
                      '/api/inventory-locations/'):
            self.assertEqual(self.client_api.get(route).status_code, 403, route)

    def test_the_dashboard_says_who_they_are(self):
        payload = self.client_api.get('/api/dashboard/').json()
        self.assertTrue(payload['is_head_of_department'])
        self.assertFalse(payload['can_manage_exams'])
        self.assertFalse(payload['is_principal'])

    def test_the_exam_officer_still_has_examinations(self):
        """Narrowing the Head of Department must not narrow anybody else."""
        self.assertEqual(self.api.get('/api/results/').status_code, 200)
        self.assertEqual(self.api.get('/api/eligibility/').status_code, 200)
        self.assertEqual(
            self._browser(self.admin).get('/api/results/download/').status_code, 200)


class ResultsDownloadRouteTests(RoleTestBase):
    """`/api/results/download/` used to be swallowed by the router's detail
    route and resolve to a lookup for a result with the id "download", so the
    CA results export answered 404 for everybody."""

    def test_the_ca_results_export_reaches_its_own_view(self):
        browser = Client()
        browser.force_login(self.admin)
        exported = browser.get(f'/api/results/download/?module_id={self.module.id}')

        self.assertEqual(exported.status_code, 200)
        self.assertIn('spreadsheetml', exported['Content-Type'])
        self.assertIn('attachment', exported['Content-Disposition'])

    def test_a_result_is_still_reachable_by_its_id(self):
        self.assertEqual(self.api.get('/api/results/1/').status_code, 404)  # none exist yet
        self.assertEqual(self.api.get('/api/results/').status_code, 200)


class FinanceAndEstateAreUnchangedTests(RoleTestBase):
    """The two roles that already existed keep exactly what they had."""

    def test_the_accountant_still_has_the_ledger_and_not_the_register(self):
        user = User.objects.create_user('accounts', password='pw')
        AccountantProfile.objects.create(user=user, full_name='Accountant')
        api = APIClient()
        api.force_authenticate(user)

        self.assertEqual(api.get('/api/charge-types/').status_code, 200)
        self.assertEqual(api.get('/api/assets/').status_code, 403)

    def test_the_estate_officer_still_has_the_asset_register(self):
        user = User.objects.create_user('estate', password='pw')
        EstateOfficerProfile.objects.create(user=user, full_name='Estate')
        api = APIClient()
        api.force_authenticate(user)

        self.assertEqual(api.get('/api/assets/').status_code, 200)
        self.assertEqual(api.get('/api/charge-types/').status_code, 403)
