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
    StudentResult, TeacherProfile,
)
from .views import roles_for as roles_of

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


class ChangingSomebodysRoleTests(RoleTestBase):
    """A tutor promoted to Head of Department is the same person.

    Making a second account for them loses the modules they teach and gives the
    college two rows for one member of staff, so a role is something an account
    gains rather than something it is replaced by.
    """

    def setUp(self):
        super().setUp()
        self.user, self.client_api = self._account('tutor', 'jmwangi')
        self.module.teachers.add(self.user)

    def _roles(self, *roles, actor=None):
        return (actor or self.api).post(f'/api/staff-accounts/{self.user.id}/roles/',
                                        {'roles': list(roles)}, format='json')

    def test_a_tutor_promoted_keeps_the_modules_they_teach(self):
        promoted = self._roles('tutor', 'hod')
        self.assertEqual(promoted.status_code, 200, promoted.data)
        self.assertEqual(promoted.data['roles'], ['hod', 'tutor'])

        self.user.refresh_from_db()
        self.assertTrue(self.user.is_staff)
        self.assertTrue(HeadOfDepartmentProfile.objects.filter(user=self.user).exists())
        # The whole point: nothing they already had was taken away.
        self.assertIn(self.module, self.user.modules_taught.all())
        self.assertEqual(self.user.profile.full_name, 'Tutor Person')

    def test_moving_them_out_of_tutoring_still_leaves_the_modules_alone(self):
        """Module assignments live on the module. Dropping the tutor profile is
        the college saying "this is no longer their job", not erasing history."""
        self._roles('principal')

        self.user.refresh_from_db()
        self.assertFalse(hasattr(self.user, 'profile'))
        self.assertTrue(PrincipalProfile.objects.filter(user=self.user).exists())
        self.assertIn(self.module, self.user.modules_taught.all())

    def test_an_office_brings_its_access_with_it(self):
        self.assertFalse(self.user.is_staff)
        self._roles('tutor', 'principal')
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_staff)

        payload = self.client_api.get('/api/dashboard/').json()
        self.assertTrue(payload['is_principal'])
        self.assertTrue(payload['can_manage_exams'])

    def test_taking_the_office_away_takes_the_access_with_it(self):
        self._roles('tutor', 'principal')
        self._roles('tutor')

        self.user.refresh_from_db()
        self.assertFalse(self.user.is_staff)
        self.assertFalse(PrincipalProfile.objects.filter(user=self.user).exists())
        self.assertEqual(self.client_api.get('/api/results/').status_code, 200)  # own modules
        self.assertEqual(self.client_api.get('/api/charge-types/').status_code, 403)

    def test_the_exam_officer_can_be_handed_on(self):
        """It is the one role with no profile of its own — is_staff and neither
        office — so the editor has to be able to grant and revoke it by name."""
        self._roles('tutor', 'exam_officer')
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_staff)
        self.assertIn('exam_officer', roles_of(self.user))

        self._roles('tutor')
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_staff)

    def test_an_account_may_hold_several_offices(self):
        answered = self._roles('tutor', 'hod', 'secretary')
        self.assertEqual(answered.status_code, 200)
        self.assertEqual(answered.data['roles'], ['hod', 'secretary', 'tutor'])
        self.assertEqual(answered.data['role'], 'hod')       # listed under the senior one

    def test_an_account_cannot_be_left_with_no_role(self):
        refused = self._roles()
        self.assertEqual(refused.status_code, 400)
        self.assertIn('at least one role', refused.data['detail'])

    def test_a_role_nobody_defined_is_refused(self):
        refused = self._roles('tutor', 'chancellor')
        self.assertEqual(refused.status_code, 400)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_staff)

    def test_only_the_administrator_may_change_roles(self):
        refused = self._roles('principal', actor=self.client_api)
        self.assertEqual(refused.status_code, 403)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_staff)

    def test_nobody_edits_their_own_roles(self):
        """Which also means an administrator cannot lock themselves out — the
        rule that stops self-promotion stops self-demotion with it."""
        refused = self.api.post(f'/api/staff-accounts/{self.admin.id}/roles/',
                                {'roles': ['tutor']}, format='json')
        self.assertEqual(refused.status_code, 403)
        self.assertIn('your own roles', refused.data['detail'])
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_staff)

    def test_every_account_is_listed_including_ones_with_no_profile(self):
        """The examination officer has no profile row, so they used to be
        invisible on the very screen that hands the job to somebody else."""
        listed = {row['username']: row for row in self.api.get('/api/staff-accounts/').json()}
        self.assertIn('admin', listed)
        self.assertEqual(listed['admin']['roles'], ['exam_officer'])
        self.assertTrue(listed['admin']['is_superuser'])
        self.assertEqual(listed['jmwangi']['roles'], ['tutor'])


class DjangoAdminRoleChangeTests(RoleTestBase):
    """Attaching a profile in Django's own admin is a role change too.

    It sets no `is_staff`, so before this the account carried the title and none
    of the access — you signed in and were still shown a tutor's screen.
    """

    def test_attaching_a_principal_profile_grants_the_access(self):
        user = User.objects.create_user('jmwangi', password='pw')
        TeacherProfile.objects.create(user=user, full_name='J Mwangi')
        self.assertFalse(user.is_staff)

        PrincipalProfile.objects.create(user=user, full_name='J Mwangi')

        user.refresh_from_db()
        self.assertTrue(user.is_staff)

    def test_attaching_a_head_of_department_profile_grants_it_too(self):
        user = User.objects.create_user('amtei', password='pw')
        HeadOfDepartmentProfile.objects.create(user=user, full_name='A Mtei')

        user.refresh_from_db()
        self.assertTrue(user.is_staff)

    def test_an_inactive_profile_grants_nothing(self):
        user = User.objects.create_user('retired', password='pw')
        PrincipalProfile.objects.create(user=user, full_name='Retired', is_active=False)

        user.refresh_from_db()
        self.assertFalse(user.is_staff)

    def test_the_accountant_and_estate_offices_carry_no_admin_rights(self):
        for model, username in ((AccountantProfile, 'accounts'),
                                (EstateOfficerProfile, 'estate')):
            user = User.objects.create_user(username, password='pw')
            model.objects.create(user=user, full_name=username)
            user.refresh_from_db()
            self.assertFalse(user.is_staff, username)


class WearingTwoHatsTests(RoleTestBase):
    """A tutor promoted to Principal does not stop teaching.

    Every admin role sees the whole college, so their own two modules arrive
    buried in a list of every module it runs. Being promoted should not make
    your own classes harder to find.
    """

    def setUp(self):
        super().setUp()
        self.mine = self.module
        self.theirs = Module.objects.create(name='Anatomy', code='ANA101', teacher='T',
                                            class_level=self.level, semester=self.sem)
        Student.objects.create(nactvet_reg_no='BPH/2026/002', name='Neema Paul',
                               module=self.theirs)

        self.user, _ = self._account('tutor', 'jmwangi')
        self.mine.teachers.add(self.user)
        self.api.post(f'/api/staff-accounts/{self.user.id}/roles/',
                      {'roles': ['tutor', 'principal']}, format='json')
        self.user.refresh_from_db()

        self.browser = Client()
        self.browser.force_login(self.user)

    def _codes(self):
        return sorted(m['code'] for m in self.browser.get('/api/modules/').json())

    def _scope(self, scope):
        return self.browser.post('/api/module-scope/', {'scope': scope},
                                 content_type='application/json')

    def test_a_promoted_tutor_sees_the_whole_college_by_default(self):
        self.assertEqual(self._codes(), ['ANA101', 'PHM101'])
        self.assertEqual(self.browser.get('/api/dashboard/').json()['module_scope'], 'college')

    def test_they_can_narrow_to_the_modules_they_teach(self):
        self.assertEqual(self._scope('mine').status_code, 200)
        self.assertEqual(self._codes(), ['PHM101'])
        self.assertEqual(self.browser.get('/api/dashboard/').json()['module_scope'], 'mine')

    def test_and_switch_back(self):
        self._scope('mine')
        self._scope('college')
        self.assertEqual(self._codes(), ['ANA101', 'PHM101'])

    def test_the_switch_narrows_the_view_and_never_the_office(self):
        """A Principal working on their own modules is still the Principal."""
        self._scope('mine')
        payload = self.browser.get('/api/dashboard/').json()
        self.assertTrue(payload['is_principal'])
        self.assertTrue(payload['can_manage_exams'])
        # Still barred from the same places as before — the switch is not a role.
        self.assertEqual(self.browser.get('/api/charge-types/').status_code, 403)
        self.assertEqual(self.browser.get('/api/assets/').status_code, 403)

    def test_the_switch_cannot_widen_anything(self):
        """A plain tutor asking for the whole college gets their own modules,
        because the scope only ever narrows what the account already had."""
        tutor, _ = self._account('tutor', 'atutor')
        self.mine.teachers.add(tutor)
        browser = Client()
        browser.force_login(tutor)
        browser.post('/api/module-scope/', {'scope': 'college'}, content_type='application/json')

        self.assertEqual(sorted(m['code'] for m in browser.get('/api/modules/').json()),
                         ['PHM101'])

    def test_performance_follows_the_same_switch(self):
        StudentResult.objects.create(student=Student.objects.get(nactvet_reg_no='BPH/2026/001'),
                                     assign1=70, assign2=70, cat1_theory=70, cat2_theory=70,
                                     end_theory=70)
        StudentResult.objects.create(student=Student.objects.get(nactvet_reg_no='BPH/2026/002'),
                                     assign1=30, assign2=30, cat1_theory=30, cat2_theory=30,
                                     end_theory=30)

        whole = self.browser.get('/api/performance/?assessment=final').json()
        self.assertEqual(whole['scope'], 'college')
        self.assertEqual(whole['headline']['assessed'], 2)

        self._scope('mine')
        narrowed = self.browser.get('/api/performance/?assessment=final').json()
        self.assertEqual(narrowed['scope'], 'my-modules')
        self.assertEqual([row['code'] for row in narrowed['by_module']], ['PHM101'])
        self.assertEqual(narrowed['headline']['mean'], 70.0)

    def test_somebody_who_teaches_nothing_is_not_offered_the_switch(self):
        payload = self.api.get('/api/dashboard/').json()
        self.assertFalse(payload['teaches'])
        refused = self.client.post('/api/module-scope/', {'scope': 'mine'},
                                   content_type='application/json')
        self.assertIn(refused.status_code, (302, 400, 403))

    def test_asking_for_a_scope_nobody_defined_is_refused(self):
        self.assertEqual(self._scope('everything').status_code, 400)
        self.assertEqual(self._codes(), ['ANA101', 'PHM101'])


class RoleAdministrationCannotBeUsedToEscalateTests(RoleTestBase):
    """Handing out roles is the one power that hands out every other.

    Both of these were possible before: a Head of Department could grant
    themselves the accountant's role and open the ledger, or drop their own HoD
    profile to become the examination officer and start writing marks — walking
    straight through the two boundaries the college drew around them.
    """

    def setUp(self):
        super().setUp()
        self.hod, self.hod_api = self._account('hod', 'hod')
        self.principal, self.principal_api = self._account('principal', 'principal')

    def _set(self, actor, user, *roles):
        return actor.post(f'/api/staff-accounts/{user.id}/roles/',
                          {'roles': list(roles)}, format='json')

    def test_a_head_of_department_cannot_grant_itself_the_ledger(self):
        self.assertEqual(self.hod_api.get('/api/charge-types/').status_code, 403)
        self.assertEqual(self._set(self.hod_api, self.hod, 'hod', 'accountant').status_code, 403)
        self.assertEqual(self.hod_api.get('/api/charge-types/').status_code, 403)
        self.assertFalse(AccountantProfile.objects.filter(user=self.hod).exists())

    def test_a_head_of_department_cannot_shed_the_office_to_escape_its_limits(self):
        self.assertEqual(self._set(self.hod_api, self.hod, 'exam_officer').status_code, 403)
        self.hod.refresh_from_db()
        self.assertTrue(HeadOfDepartmentProfile.objects.filter(user=self.hod).exists())
        self.assertEqual(
            self.hod_api.post('/api/results/', {'student': 1, 'cat1_theory': 30},
                              format='json').status_code, 403)

    def test_a_head_of_department_cannot_promote_a_colleague_either(self):
        """Otherwise two of them promote each other and the rule is decoration."""
        self.assertEqual(self._set(self.hod_api, self.principal, 'principal', 'accountant')
                         .status_code, 403)
        self.assertFalse(AccountantProfile.objects.filter(user=self.principal).exists())

    def test_a_head_of_department_cannot_mint_an_account_to_log_into(self):
        """The longer route to the same place: make an accountant, know its
        password, sign in as it."""
        refused = self.hod_api.post('/api/staff-accounts/', {
            'role': 'accountant', 'full_name': 'Back Door',
            'username': 'backdoor', 'password': 'secret123',
        }, format='json')
        self.assertEqual(refused.status_code, 403)
        self.assertFalse(User.objects.filter(username='backdoor').exists())

    def test_nobody_changes_their_own_roles_not_even_the_principal(self):
        self.assertEqual(self._set(self.principal_api, self.principal,
                                   'principal', 'accountant').status_code, 403)
        self.assertFalse(AccountantProfile.objects.filter(user=self.principal).exists())
        self.assertEqual(self._set(self.api, self.admin, 'exam_officer', 'accountant')
                         .status_code, 403)

    def test_the_principal_and_the_exam_officer_still_administer_everyone_else(self):
        self.assertEqual(self._set(self.principal_api, self.hod, 'hod', 'secretary')
                         .status_code, 200)
        self.assertEqual(self._set(self.api, self.principal, 'principal', 'tutor')
                         .status_code, 200)

    def test_a_head_of_department_still_reads_the_staff_list(self):
        """They run the department and need to see who is in it — reading is
        not the power that was being abused."""
        self.assertEqual(self.hod_api.get('/api/staff-accounts/').status_code, 200)
