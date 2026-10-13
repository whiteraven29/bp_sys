"""The college's offices, from its organisational chart, each one a test.

  * the examination officer is an office of its own: results, windows and
    declarations — not accounts, not confirming a review, not the advance;
  * DPARC reads everything academic, confirms reviews and runs the advance,
    follows postponements, sees admissions without the money, and touches no
    ledger;
  * DPFPA reads the college's money in summary and its property, and records
    nothing;
  * the ICT officer keeps the accounts and gives only the open roles; a senior
    role is the Principal's to give, and the Principal's own the Board's;
  * every role change is written down;
  * marks go by module: whoever teaches it enters its CA, whatever office they
    also hold, and nobody else but the examination officer;
  * the deploy gives every existing exam-officer account its office before
    `is_staff` stops meaning anything.
"""

import importlib
from datetime import date, timedelta
from decimal import Decimal

from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.test import Client
from rest_framework.test import APIClient

from . import finance
from .models import (
    Application, ExamOfficerProfile, Notification, Postponement, PrincipalProfile,
    ResultEntryWindow, RoleChange, StudentCharge, StudentProfile, StudentResult,
)
from .test_lifecycle import LifecycleBase
from .views import set_roles

User = get_user_model()


class OfficesBase(LifecycleBase):
    def office(self, username, *roles):
        user = User.objects.create_user(username, password='pw')
        set_roles(user, list(roles), full_name=username.title())
        client = APIClient()
        client.force_authenticate(user)
        return user, client

    def setUp(self):
        super().setUp()
        self.exam_user, self.exam = self.office('exams', 'exam_officer')
        self.dparc_user, self.dparc = self.office('dparc', 'dparc')
        self.dpfpa_user, self.dpfpa = self.office('dpfpa', 'dpfpa')
        self.ict_user, self.ict = self.office('ict', 'ict_officer')

    def open_ca_window(self):
        ResultEntryWindow.objects.create(
            semester=self.sem1, kind=ResultEntryWindow.CA,
            opens_on=timezone.localdate() - timedelta(days=1),
            closes_on=timezone.localdate() + timedelta(days=7), declared_by=self.exam_user)


class ExamOfficerTests(OfficesBase):
    def test_holds_the_examinations(self):
        payload = self.exam.get('/api/dashboard/').json()
        self.assertTrue(payload['is_exam_officer'])
        self.assertTrue(payload['can_manage_exams'])
        self.assertTrue(payload['is_staff'])               # the academic side

    def test_is_no_longer_the_system_administrator(self):
        self.exam_user.refresh_from_db()
        self.assertFalse(self.exam_user.is_staff)
        made = self.exam.post('/api/staff-accounts/', {
            'role': 'tutor', 'full_name': 'New Tutor', 'username': 'newtutor', 'password': 'secret123',
        }, format='json')
        self.assertEqual(made.status_code, 403)

    def test_reviews_the_results_but_neither_confirms_nor_advances(self):
        self.assertEqual(self.exam.post('/api/semester-reviews/build/',
                                        {'semester_id': self.sem1.id}, format='json').status_code, 200)
        refused = self.exam.post('/api/semester-reviews/confirm-proposed/',
                                 {'semester_id': self.sem1.id}, format='json')
        self.assertEqual(refused.status_code, 403)
        self.assertEqual(self.exam.get('/api/academic-years/advance-preview/').status_code, 403)

    def test_does_not_change_departments(self):
        refused = self.exam.post('/api/departments/', {'name': 'Nursing', 'code': 'NUR'}, format='json')
        self.assertEqual(refused.status_code, 403)


class TheExamOfficeWorksFromTheDepartmentsListsTests(OfficesBase):
    """Modules, enrollments, staff, forms, requests and sick records are the
    departments' work (HOD, DPARC, Principal). The exam officer reads this
    semester's modules and class lists, and changes none of them."""

    def setUp(self):
        super().setUp()
        self.hod_user, self.hod = self.office('hod2', 'hod')

    def new_module(self, client, code):
        return client.post('/api/modules/', {
            'name': 'Pharmacology', 'code': code, 'teacher': 'T', 'credits': 3,
            'class_level': self.level4.id, 'semester': self.sem1.id}, format='json')

    def test_reads_modules_and_class_lists(self):
        self.enrolled_student()
        self.assertEqual(self.exam.get('/api/modules/').status_code, 200)
        listed = self.exam.get(f'/api/students/?module_id={self.m1.id}')
        self.assertEqual(listed.status_code, 200)

    def test_registers_no_module_and_enrolls_nobody(self):
        self.assertEqual(self.new_module(self.exam, 'PST04150').status_code, 403)
        self.assertEqual(self.exam.post('/api/students/', {
            'nactvet_reg_no': 'NIT/PST/2026/0900', 'name': 'X', 'module': self.m1.id,
        }, format='json').status_code, 403)

    def test_the_departments_still_do(self):
        self.assertEqual(self.new_module(self.hod, 'PST04151').status_code, 201)
        self.assertEqual(self.new_module(self.dparc, 'PST04152').status_code, 201)
        StudentProfile.objects.create(nactvet_reg_no='NIT/PST/2026/0901', name='Y')               # admitted
        self.assertEqual(self.hod.post('/api/students/', {
            'nactvet_reg_no': 'NIT/PST/2026/0901', 'name': 'Y', 'module': self.m1.id,
        }, format='json').status_code, 201)

    def test_no_staff_list_forms_requests_or_sick_records(self):
        self.assertEqual(self.exam.get('/api/staff-accounts/').status_code, 403)
        self.assertEqual(self.hod.get('/api/staff-accounts/').status_code, 200)
        self.assertEqual(self.exam.post('/api/forms/', {}, format='json').status_code, 403)
        self.assertNotEqual(self.hod.post('/api/forms/', {}, format='json').status_code, 403)
        self.assertEqual(self.exam.patch('/api/sick-records/999999/', {}, format='json').status_code, 403)
        self.assertEqual(self.hod.patch('/api/sick-records/999999/', {}, format='json').status_code, 404)
        from .roles import can_decide_requests, can_handle_requests
        self.assertFalse(can_handle_requests(self.exam_user))
        self.assertFalse(can_decide_requests(self.exam_user))


class TheOldAdminAccountTests(OfficesBase):
    """The shared superuser the exam office used to sign in with. The deploy
    gives it the Exam Officer role; from then on it is the exam officer in the
    app, and keeps Django's admin until the ICT officer unticks it."""

    def setUp(self):
        super().setUp()
        self.old = User.objects.create_superuser('oldadmin', 'o@x.com', 'pw')
        importlib.import_module('attendance.migrations.0055_offices_and_role_log') \
            .exam_office_gets_its_own_role(django_apps, None)
        self.client_old = APIClient()
        self.client_old.force_authenticate(self.old)

    def test_it_is_the_exam_officer_in_the_app(self):
        payload = self.client_old.get('/api/dashboard/').json()
        self.assertTrue(payload['is_exam_officer'])
        self.assertTrue(payload['can_manage_exams'])
        self.assertFalse(payload['can_advance_semester'])
        self.assertFalse(payload['can_manage_accounts'])
        self.assertEqual(self.client_old.get('/api/staff-accounts/').status_code, 403)

    def test_and_keeps_django_admin_until_unticked(self):
        browser = Client()
        browser.force_login(self.old)
        self.assertEqual(browser.get('/admin/').status_code, 200)


class DparcTests(OfficesBase):
    def test_sees_the_academic_side(self):
        payload = self.dparc.get('/api/dashboard/').json()
        self.assertTrue(payload['is_dparc'])
        self.assertTrue(payload['is_staff'])
        for route in ('/api/modules/', '/api/students/', '/api/semester-reviews/'):
            self.assertEqual(self.dparc.get(route).status_code, 200, route)

    def test_confirms_reviews_and_runs_the_advance(self):
        self.dparc.post('/api/semester-reviews/build/', {'semester_id': self.sem1.id}, format='json')
        confirmed = self.dparc.post('/api/semester-reviews/confirm-proposed/',
                                    {'semester_id': self.sem1.id}, format='json')
        self.assertEqual(confirmed.status_code, 200, confirmed.data)
        self.assertEqual(self.dparc.get('/api/academic-years/advance-preview/').status_code, 200)

    def test_changes_departments(self):
        made = self.dparc.post('/api/departments/', {'name': 'Nursing', 'code': 'NUR'}, format='json')
        self.assertEqual(made.status_code, 201, made.data)

    def test_touches_no_money(self):
        for route in ('/api/charge-types/', '/api/payments/', '/api/finance/students/',
                      '/api/executive/finance/'):
            self.assertEqual(self.dparc.get(route).status_code, 403, route)

    def test_reads_admissions_without_the_balance_and_works_no_desk(self):
        profile, _ = self.enrolled_student()
        application = Application.objects.create(
            kind=Application.CONTINUING, state=Application.FINANCE, profile=profile,
            semester=self.sem1, programme=self.pst, class_level=self.level4)
        listed = self.dparc.get('/api/applications/')
        self.assertEqual(listed.status_code, 200)
        opened = self.dparc.get(f'/api/applications/{application.id}/').json()
        self.assertIsNone(opened['balance'])
        refused = self.dparc.post(f'/api/applications/{application.id}/complete/', {}, format='json')
        self.assertEqual(refused.status_code, 403)

    def test_follows_postponements_and_decides_none(self):
        profile, _ = self.enrolled_student()
        asked = self.records.post('/api/postponements/', {
            'reg_no': profile.nactvet_reg_no, 'semester_id': self.sem1.id, 'reason': 'Illness.',
        }, format='json')
        self.assertEqual(asked.status_code, 201, asked.data)
        listed = self.dparc.get('/api/postponements/')
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(len(listed.data), 1)
        self.assertIsNone(listed.data[0]['fees_reversed'])
        refused = self.dparc.post(f'/api/postponements/{asked.data["id"]}/decide/',
                                  {'decision': 'approve'}, format='json')
        self.assertEqual(refused.status_code, 403)
        self.assertEqual(self.dparc.post('/api/postponements/', {
            'reg_no': profile.nactvet_reg_no, 'semester_id': self.sem1.id, 'reason': 'x',
        }, format='json').status_code, 403)


class PostponementNoticeTests(OfficesBase):
    def test_the_principal_and_dparc_are_told_of_a_request_and_dparc_of_the_decision(self):
        profile, _ = self.enrolled_student()
        asked = self.records.post('/api/postponements/', {
            'reg_no': profile.nactvet_reg_no, 'semester_id': self.sem1.id, 'reason': 'Illness.',
        }, format='json')
        requested = Notification.objects.filter(kind=Notification.POSTPONEMENT_REQUESTED)
        self.assertEqual({n.user_id for n in requested}, {self.principal_user.id, self.dparc_user.id})

        self.principal.post(f'/api/postponements/{asked.data["id"]}/decide/',
                            {'decision': 'approve'}, format='json')
        self.assertEqual(Postponement.objects.get().status, Postponement.APPROVED)
        decided = Notification.objects.filter(kind=Notification.POSTPONEMENT_DECIDED)
        self.assertEqual({n.user_id for n in decided if n.user_id}, {self.dparc_user.id})
        told = [n for n in decided if n.profile_id == profile.id]
        self.assertEqual(len(told), 1)
        self.assertIn('return', told[0].body)


class DpfpaTests(OfficesBase):
    def test_reads_the_money_in_summary_and_names_nobody(self):
        profile = self.new_student()
        finance.record_payment(profile, Decimal('500000'), date(2026, 10, 15),
                               recorded_by=self.accountant, bank_reference='CRDB-9')
        summary = self.dpfpa.get('/api/executive/finance/')
        self.assertEqual(summary.status_code, 200)
        totals = summary.data['totals']
        billed = sum((c.amount for c in StudentCharge.objects.filter(profile=profile)), Decimal(0))
        self.assertEqual(Decimal(totals['billed']), billed)
        self.assertEqual(Decimal(totals['paid']), Decimal('500000'))
        self.assertEqual(Decimal(totals['outstanding']), billed - Decimal('500000'))
        self.assertEqual(totals['students'], 1)
        self.assertEqual(totals['owing'], 1)
        self.assertEqual(sum(Decimal(row['paid']) for row in summary.data['by_month']), Decimal('500000'))
        self.assertNotIn(profile.name, str(summary.data))
        self.assertNotIn(profile.nactvet_reg_no, str(summary.data))

    def test_the_principal_reads_it_too_and_records_nothing(self):
        self.assertEqual(self.principal.get('/api/executive/finance/').status_code, 200)
        for client in (self.dpfpa, self.principal):
            self.assertEqual(client.get('/api/payments/').status_code, 403)
            self.assertEqual(client.post('/api/charge-types/', {}, format='json').status_code, 403)

    def test_reads_college_property_and_changes_none(self):
        self.assertEqual(self.dpfpa.get('/api/assets/').status_code, 200)
        self.assertEqual(self.dpfpa.get('/api/inventory/report/').status_code, 200)
        summary = self.dpfpa.get('/api/inventory/summary/')
        self.assertEqual(summary.status_code, 200)
        self.assertEqual(summary.data['records'], 0)
        self.assertEqual(self.principal.get('/api/inventory/summary/').status_code, 200)
        self.assertEqual(self.accounts.get('/api/inventory/summary/').status_code, 403)
        self.assertEqual(self.dpfpa.post('/api/assets/', {}, format='json').status_code, 403)
        self.assertEqual(self.principal.get('/api/assets/').status_code, 200)
        self.assertEqual(self.principal.post('/api/assets/', {}, format='json').status_code, 403)

    def test_is_not_on_the_academic_side(self):
        payload = self.dpfpa.get('/api/dashboard/').json()
        self.assertTrue(payload['is_dpfpa'])
        self.assertFalse(payload['is_staff'])
        self.assertEqual(self.dpfpa.get('/api/results-summary/').status_code, 403)


class IctOfficerTests(OfficesBase):
    def test_django_admin_is_theirs_with_every_table(self):
        self.ict_user.refresh_from_db()
        self.assertTrue(self.ict_user.is_staff)
        self.assertTrue(self.ict_user.is_superuser)
        browser = Client()
        browser.force_login(self.ict_user)
        self.assertEqual(browser.get('/admin/').status_code, 200)
        self.assertEqual(browser.get('/admin/attendance/studentresult/').status_code, 200)

    def test_being_a_superuser_does_not_change_their_screens(self):
        """Superuser for /admin/, ICT officer in the app: no marks, no money,
        no giving the Principal's office."""
        payload = self.ict.get('/api/dashboard/').json()
        self.assertFalse(payload['is_staff'])
        self.assertFalse(payload['can_manage_exams'])
        self.assertFalse(payload['can_appoint'])
        tutor, _ = self.office('tutor9', 'tutor')
        refused = self.ict.post(f'/api/staff-accounts/{tutor.id}/roles/',
                                {'roles': ['tutor', 'principal']}, format='json')
        self.assertEqual(refused.status_code, 403)

    def test_the_admin_site_goes_with_the_role(self):
        handed_on, _ = self.office('ict2', 'tutor')
        self.principal.post(f'/api/staff-accounts/{handed_on.id}/roles/',
                            {'roles': ['tutor', 'ict_officer']}, format='json')
        handed_on.refresh_from_db()
        self.assertTrue(handed_on.is_superuser)
        self.principal.post(f'/api/staff-accounts/{handed_on.id}/roles/',
                            {'roles': ['tutor']}, format='json')
        handed_on.refresh_from_db()
        self.assertFalse(handed_on.is_superuser)
        self.assertFalse(handed_on.is_staff)

    def test_keeps_the_accounts(self):
        made = self.ict.post('/api/staff-accounts/', {
            'role': 'tutor', 'full_name': 'New Tutor', 'username': 'newtutor', 'password': 'secret123',
        }, format='json')
        self.assertEqual(made.status_code, 201, made.data)
        reset = self.ict.post(f'/api/staff-accounts/{made.data["id"]}/reset-password/', {}, format='json')
        self.assertEqual(reset.status_code, 200)
        self.assertEqual(self.ict.get('/api/staff-accounts/role-changes/').status_code, 200)

    def test_gives_no_senior_role(self):
        refused = self.ict.post('/api/staff-accounts/', {
            'role': 'accountant', 'full_name': 'A', 'username': 'acct2', 'password': 'secret123',
        }, format='json')
        self.assertEqual(refused.status_code, 403)
        self.assertIn('Principal', refused.data['detail'])
        tutor, _ = self.office('tutor1', 'tutor')
        refused = self.ict.post(f'/api/staff-accounts/{tutor.id}/roles/',
                                {'roles': ['tutor', 'hod']}, format='json')
        self.assertEqual(refused.status_code, 403)
        given = self.ict.post(f'/api/staff-accounts/{tutor.id}/roles/',
                              {'roles': ['tutor', 'secretary']}, format='json')
        self.assertEqual(given.status_code, 200)

    def test_reads_no_results_and_no_money(self):
        payload = self.ict.get('/api/dashboard/').json()
        self.assertFalse(payload['is_staff'])
        self.assertEqual(self.ict.get('/api/results-summary/').status_code, 403)
        self.assertEqual(self.ict.get('/api/payments/').status_code, 403)
        self.assertEqual(self.ict.get('/api/executive/finance/').status_code, 403)


class AppointmentTests(OfficesBase):
    def test_the_principal_appoints_and_it_is_written_down(self):
        tutor, _ = self.office('tutor2', 'tutor')
        given = self.principal.post(f'/api/staff-accounts/{tutor.id}/roles/',
                                    {'roles': ['tutor', 'hod']}, format='json')
        self.assertEqual(given.status_code, 200, given.data)
        change = RoleChange.objects.filter(user=tutor).first()
        self.assertEqual(change.added, ['hod'])
        self.assertEqual(change.removed, [])
        self.assertEqual(change.changed_by, self.principal_user)
        log = self.principal.get('/api/staff-accounts/role-changes/').json()
        self.assertEqual(log[0]['added'], ['Head of Department'])

    def test_the_principals_own_office_is_the_boards(self):
        tutor, _ = self.office('tutor3', 'tutor')
        refused = self.principal.post(f'/api/staff-accounts/{tutor.id}/roles/',
                                      {'roles': ['tutor', 'principal']}, format='json')
        self.assertEqual(refused.status_code, 403)
        self.assertFalse(PrincipalProfile.objects.filter(user=tutor).exists())

    def test_nobody_but_the_principal_and_ict_reads_the_log(self):
        for client in (self.exam, self.dparc, self.dpfpa):
            self.assertEqual(client.get('/api/staff-accounts/role-changes/').status_code, 403)


class MarksGoByModuleTests(OfficesBase):
    """Whoever teaches a module enters its CA, whatever else they are."""

    def setUp(self):
        super().setUp()
        _, self.enrollment = self.enrolled_student()
        self.m1.teachers.add(self.principal_user)

    def enter(self, client, **marks):
        return client.post('/api/results/', {'student': self.enrollment.id, **marks}, format='json')

    def test_the_principal_enters_ca_for_their_own_module(self):
        self.open_ca_window()
        self.assertEqual(self.enter(self.principal, cat1_theory=30).status_code, 201)

    def test_but_not_the_final_exam(self):
        self.open_ca_window()
        self.assertEqual(self.enter(self.principal, end_theory=50).status_code, 403)

    def test_dparc_enters_nothing_on_a_module_they_do_not_teach(self):
        self.open_ca_window()
        self.assertEqual(self.enter(self.dparc, cat1_theory=30).status_code, 403)

    def test_a_head_of_department_who_teaches_enters_their_own_ca(self):
        """They were refused even for their own module before."""
        hod, hod_client = self.office('hod1', 'hod', 'tutor')
        self.m1.teachers.add(hod)
        self.open_ca_window()
        self.assertEqual(self.enter(hod_client, cat1_theory=30).status_code, 201)

    def test_the_examination_officer_enters_the_final_exam(self):
        StudentResult.objects.filter(student=self.enrollment).delete()
        self.assertEqual(self.enter(self.exam, end_theory=50).status_code, 201)


class TheDeployMigrationTests(OfficesBase):
    """Migration 0055: nobody loses their work on the morning after."""

    def run_step(self):
        step = importlib.import_module('attendance.migrations.0055_offices_and_role_log')
        step.exam_office_gets_its_own_role(django_apps, None)

    def test_the_old_exam_officer_account_gets_the_office(self):
        old = User.objects.create_user('oldexam', password='pw', is_staff=True)
        self.run_step()
        old.refresh_from_db()
        self.assertTrue(ExamOfficerProfile.objects.filter(user=old, is_active=True).exists())
        self.assertFalse(old.is_staff)

    def test_the_principal_keeps_their_office_and_gains_no_other(self):
        self.principal_user.is_staff = True
        self.principal_user.save()
        self.run_step()
        self.principal_user.refresh_from_db()
        self.assertFalse(ExamOfficerProfile.objects.filter(user=self.principal_user).exists())
        self.assertFalse(self.principal_user.is_staff)

    def test_the_superuser_keeps_django_admin(self):
        root = User.objects.create_superuser('root', 'r@x.com', 'pw')
        self.run_step()
        root.refresh_from_db()
        self.assertTrue(root.is_staff)
