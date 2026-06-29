from datetime import date
from io import BytesIO

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from openpyxl import load_workbook
from rest_framework.test import APIClient

from .models import (
    AcademicYear,
    AttendanceRecord,
    ClassLevel,
    Module,
    Semester,
    Session,
    Student,
    StudentResult,
)


User = get_user_model()


class AttendanceSecurityTests(TestCase):
    def setUp(self):
        self.year = AcademicYear.objects.create(name='2025/2026', is_active=True)
        self.semester = Semester.objects.create(
            academic_year=self.year, number=1, is_active=True
        )
        self.level = ClassLevel.objects.create(name='NTA Level 4', order=4)
        self.teacher = User.objects.create_user('teacher', password='safe-password')
        self.other_teacher = User.objects.create_user('other', password='safe-password')
        self.admin = User.objects.create_superuser(
            'admin', 'admin@example.com', 'safe-password'
        )
        self.module = Module.objects.create(
            name='Business Mathematics',
            code='BM401',
            teacher='Teacher One',
            class_level=self.level,
            semester=self.semester,
        )
        self.other_module = Module.objects.create(
            name='Communication',
            code='CS401',
            teacher='Teacher Two',
            class_level=self.level,
            semester=self.semester,
        )
        self.module.teachers.add(self.teacher)
        self.other_module.teachers.add(self.other_teacher)
        self.student = Student.objects.create(
            nactvet_reg_no='REG-001',
            name='Asha Mollel',
            module=self.module,
        )
        self.student.set_portal_pin('482913')
        self.student.save(update_fields=['portal_pin_hash'])
        self.client = APIClient()

    def test_student_dashboard_redirects_without_student_session(self):
        response = self.client.get(reverse('student-dashboard'))
        self.assertRedirects(response, reverse('login'))

    def test_student_can_login_with_portal_pin_not_surname(self):
        surname_response = self.client.post(reverse('login'), {
            'identifier': self.student.nactvet_reg_no,
            'secret': 'MOLLEL',
        })
        self.assertEqual(surname_response.status_code, 200)
        self.assertContains(surname_response, 'Invalid credentials')

        pin_response = self.client.post(reverse('login'), {
            'identifier': self.student.nactvet_reg_no,
            'secret': '482913',
        })
        self.assertRedirects(pin_response, reverse('student-dashboard'))
        dashboard_response = self.client.get(reverse('student-dashboard'))
        self.assertEqual(dashboard_response.status_code, 200)
        self.assertContains(dashboard_response, 'CA Results · Semester 1')

    def test_student_dashboard_includes_both_semesters_in_active_year(self):
        semester_two = Semester.objects.create(
            academic_year=self.year, number=2, is_active=False
        )
        semester_two_module = Module.objects.create(
            name='Entrepreneurship',
            code='ENT402',
            teacher='Teacher One',
            class_level=self.level,
            semester=semester_two,
        )
        Student.objects.create(
            nactvet_reg_no=self.student.nactvet_reg_no,
            name=self.student.name,
            module=semester_two_module,
        )
        session = self.client.session
        session['student_id'] = self.student.id
        session.save()

        response = self.client.get(reverse('student-dashboard'))

        self.assertContains(response, 'Business Mathematics')
        self.assertContains(response, 'Entrepreneurship')

    def test_teacher_cannot_create_session_for_unassigned_module(self):
        self.client.force_authenticate(self.teacher)
        response = self.client.post('/api/sessions/', {
            'module': self.other_module.id,
            'session_type': Session.THEORY,
            'exam_period': Session.GENERAL,
            'date': str(date.today()),
            'label': 'Week 1',
            'topic': 'Introduction',
            'records': [],
        }, format='json')
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Session.objects.exists())

    def test_teacher_cannot_move_student_to_unassigned_module(self):
        self.client.force_authenticate(self.teacher)
        response = self.client.patch(
            f'/api/students/{self.student.id}/',
            {'module': self.other_module.id},
            format='json',
        )
        self.assertEqual(response.status_code, 403)
        self.student.refresh_from_db()
        self.assertEqual(self.student.module, self.module)

    def test_teacher_cannot_modify_class_levels_or_academic_years(self):
        self.client.force_authenticate(self.teacher)
        level_response = self.client.post(
            '/api/class-levels/', {'name': 'NTA Level 5', 'order': 5}, format='json'
        )
        year_response = self.client.post(
            '/api/academic-years/', {'name': '2026/2027'}, format='json'
        )
        self.assertEqual(level_response.status_code, 403)
        self.assertEqual(year_response.status_code, 403)

    def test_portal_pin_is_write_only_and_hashed(self):
        self.client.force_authenticate(self.teacher)
        response = self.client.patch(
            f'/api/students/{self.student.id}/',
            {'portal_pin': 'new-pin-42'},
            format='json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('portal_pin', response.data)
        self.student.refresh_from_db()
        self.assertNotEqual(self.student.portal_pin_hash, 'new-pin-42')
        self.assertTrue(self.student.check_portal_pin('new-pin-42'))

    def test_admin_can_set_password_for_filtered_students(self):
        other_level = ClassLevel.objects.create(name='NTA Level 5', order=5)
        other_module = Module.objects.create(
            name='Accounting',
            code='AC501',
            teacher='Teacher Three',
            class_level=other_level,
            semester=self.semester,
        )
        other_student = Student.objects.create(
            nactvet_reg_no='REG-002',
            name='Baraka John',
            module=other_module,
        )
        self.client.force_authenticate(self.admin)

        response = self.client.post('/api/students/bulk_set_pin/', {
            'class_level_id': self.level.id,
            'portal_pin': 'shared-123',
        }, format='json')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['updated'], 1)
        self.student.refresh_from_db()
        other_student.refresh_from_db()
        self.assertTrue(self.student.check_portal_pin('shared-123'))
        self.assertFalse(other_student.check_portal_pin('shared-123'))

    def test_authenticated_user_can_change_own_password(self):
        self.client.force_login(self.teacher)
        response = self.client.post('/api/change-password/', {
            'current_password': 'safe-password',
            'new_password': 'new-safe-password',
        }, format='json')

        self.assertEqual(response.status_code, 200)
        self.teacher.refresh_from_db()
        self.assertTrue(self.teacher.check_password('new-safe-password'))

    def test_student_can_change_pin_for_all_enrollments(self):
        second_enrollment = Student.objects.create(
            nactvet_reg_no=self.student.nactvet_reg_no,
            name=self.student.name,
            module=self.other_module,
        )
        session = self.client.session
        session['student_id'] = self.student.id
        session.save()

        response = self.client.post('/api/change-password/', {
            'current_password': '482913',
            'new_password': 'student-new-pin',
        }, format='json')

        self.assertEqual(response.status_code, 200)
        self.student.refresh_from_db()
        second_enrollment.refresh_from_db()
        self.assertTrue(self.student.check_portal_pin('student-new-pin'))
        self.assertTrue(second_enrollment.check_portal_pin('student-new-pin'))

    def test_bulk_result_save_preserves_omitted_fields(self):
        result = StudentResult.objects.create(
            student=self.student,
            assign1=80,
            assign2=75,
            cat1_theory=70,
            cat2_theory=65,
        )
        self.client.force_authenticate(self.teacher)

        response = self.client.post('/api/results/bulk_save/', [
            {'id': result.id, 'assign1': 90},
        ], format='json')

        self.assertEqual(response.status_code, 200)
        result.refresh_from_db()
        self.assertEqual(float(result.assign1), 90)
        self.assertEqual(float(result.assign2), 75)
        self.assertEqual(float(result.cat1_theory), 70)
        self.assertEqual(float(result.cat2_theory), 65)

    def test_teacher_cannot_approve_ca_results(self):
        result = StudentResult.objects.create(student=self.student, assign1=80)
        self.client.force_authenticate(self.teacher)

        response = self.client.post('/api/results/bulk_save/', [
            {'id': result.id, 'ca_approved': True},
        ], format='json')

        self.assertEqual(response.status_code, 200)
        result.refresh_from_db()
        self.assertFalse(result.ca_approved)
        self.assertTrue(response.data['errors'])

    def test_admin_can_approve_ca_results(self):
        result = StudentResult.objects.create(student=self.student, assign1=80)
        self.client.force_authenticate(self.admin)

        response = self.client.post('/api/results/bulk_save/', [
            {'id': result.id, 'ca_approved': True},
        ], format='json')

        self.assertEqual(response.status_code, 200)
        result.refresh_from_db()
        self.assertTrue(result.ca_approved)

    def test_student_dashboard_hides_ca_results_until_admin_approval(self):
        StudentResult.objects.create(
            student=self.student,
            assign1=80,
            assign2=70,
            cat1_theory=60,
            cat2_theory=50,
            ca_approved=False,
        )
        session = self.client.session
        session['student_id'] = self.student.id
        session.save()

        response = self.client.get(reverse('student-dashboard'))

        self.assertContains(response, 'CA results for Semester 1 have not been published yet.')
        self.assertNotContains(response, '80.00')

    def test_student_dashboard_shows_ca_results_after_admin_approval(self):
        StudentResult.objects.create(
            student=self.student,
            assign1=80,
            assign2=70,
            cat1_theory=60,
            cat2_theory=50,
            ca_approved=True,
        )
        session = self.client.session
        session['student_id'] = self.student.id
        session.save()

        response = self.client.get(reverse('student-dashboard'))

        self.assertContains(response, '80.00')
        self.assertNotContains(response, 'CA results for Semester 1 have not been published yet.')

    def test_student_dashboard_hides_final_results_until_admin_approval(self):
        StudentResult.objects.create(
            student=self.student,
            assign1=80,
            assign2=70,
            cat1_theory=60,
            cat2_theory=50,
            end_theory=90,
            final_approved=False,
        )
        session = self.client.session
        session['student_id'] = self.student.id
        session.save()

        response = self.client.get(reverse('student-dashboard'))

        self.assertContains(response, 'CA results for Semester 1 have not been published yet.')
        self.assertContains(response, 'CA Results · Semester 1')
        self.assertContains(response, 'Final examination marks have not been published yet.')
        self.assertNotContains(response, '80.00')
        self.assertNotContains(response, '90.00')
        self.assertNotContains(response, '78.0')

    def test_student_dashboard_shows_final_results_after_admin_approval(self):
        StudentResult.objects.create(
            student=self.student,
            assign1=80,
            assign2=70,
            cat1_theory=60,
            cat2_theory=50,
            end_theory=90,
            final_approved=True,
        )
        session = self.client.session
        session['student_id'] = self.student.id
        session.save()

        response = self.client.get(reverse('student-dashboard'))

        self.assertContains(response, '90.00')
        self.assertContains(response, '78.0')

    def test_eligibility_api_and_excel_both_require_sick_certificate(self):
        session = Session.objects.create(
            module=self.module,
            session_type=Session.THEORY,
            exam_period=Session.CAT1,
            date=date.today(),
            label='CAT preparation',
        )
        AttendanceRecord.objects.create(
            session=session,
            student=self.student,
            status=AttendanceRecord.SICK,
            certificate_submitted=False,
        )
        self.client.force_authenticate(self.admin)

        api_response = self.client.get('/api/eligibility/', {'module_id': self.module.id})
        self.assertEqual(api_response.status_code, 200)
        self.assertEqual(api_response.data['rows'][0]['cat1_attended'], 0)

        self.client.force_authenticate(user=None)
        self.client.force_login(self.admin)
        excel_response = self.client.get(
            '/api/eligibility/download/', {'module_id': self.module.id}
        )
        self.assertEqual(excel_response.status_code, 200)
        workbook = load_workbook(BytesIO(excel_response.content))
        sheet = workbook.active
        self.assertEqual(sheet.cell(row=2, column=9).value, 0)
