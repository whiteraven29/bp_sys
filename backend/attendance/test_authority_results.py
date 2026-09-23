"""The authority's grades, the results summary, and withholding results.

  * a D or an F from the authority is a fail — the module is repeated; with
    stars it is a supplementary;
  * an upload writes only to the semester it is for — a repeating student's
    earlier attempt is never touched;
  * only the examination officer (or the Principal) uploads; the Head of
    Department reads;
  * the summary gives each student's GPA and standing and each module's pass
    rate, from the same outcome the semester review uses;
  * withholding hides a student's results for a semester on the portal — they
    read "Withheld" — without changing them; the authority's *W* reads
    "Withheld" too.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from . import progression
from .grading import parse_authority_grade
from .models import (
    AcademicYear, ClassLevel, Department, HeadOfDepartmentProfile, Module, Programme,
    ResultWithholding, SemesterReview, Semester, SemesterRegistration, Student, StudentProfile,
    StudentResult,
)

User = get_user_model()


class AuthorityBase(TestCase):
    def setUp(self):
        self.last_year = AcademicYear.objects.create(name='2025/2026')
        self.year = AcademicYear.objects.create(name='2026/2027', is_active=True)
        self.old_sem1 = Semester.objects.create(academic_year=self.last_year, number=1)
        self.sem1 = Semester.objects.create(academic_year=self.year, number=1, is_active=True)
        self.level4 = ClassLevel.objects.create(name='NTA Level 4', order=4)
        department = Department.objects.create(name='Pharmaceutical Sciences', code='PST')
        self.pst = Programme.objects.create(department=department, name='Pharmaceutical Sciences', code='PST')
        self.anatomy = self.module('PST04101', 'Human Anatomy')
        self.maths = self.module('PST04102', 'Pharmaceutical Calculations')

        self.exam_user = User.objects.create_superuser('exam', 'exam@x.com', 'pw')
        self.exam = APIClient(); self.exam.force_authenticate(self.exam_user)
        hod = User.objects.create_user('hod', password='pw', is_staff=True)
        HeadOfDepartmentProfile.objects.create(user=hod, full_name='HoD')
        self.hod = APIClient(); self.hod.force_authenticate(hod)

    def module(self, code, name, semester=None):
        return Module.objects.create(name=name, code=code, teacher='T', class_level=self.level4,
                                     semester=semester or self.sem1, programme=self.pst, credits=3)

    def student(self, reg_no, name, *modules):
        profile = StudentProfile.objects.create(nactvet_reg_no=reg_no, name=name)
        SemesterRegistration.objects.create(profile=profile, semester=self.sem1, programme=self.pst,
                                            class_level=self.level4, kind=SemesterRegistration.NEW)
        enrollments = [Student.objects.create(nactvet_reg_no=reg_no, name=name, profile=profile, module=module)
                       for module in modules or (self.anatomy, self.maths)]
        return profile, enrollments

    def upload(self, client, rows, semester=None):
        return client.post('/api/results/authority-grades/', {
            'rows': [{'reg_no': r, 'module_code': m, 'grade': g} for r, m, g in rows],
            'semester_id': (semester or self.sem1).id}, format='json')


class AuthorityGradeTests(AuthorityBase):
    def test_d_and_f_are_fails_and_stars_are_supplementaries(self):
        read = {grade: parse_authority_grade(grade, self.level4)['status'] for grade in
                ['A', 'B', 'C', 'D', 'F', 'C*', 'D*', 'F***', '*W*', 'FAIL']}

        self.assertEqual(read, {'A': 'PASS', 'B': 'PASS', 'C': 'PASS', 'D': 'FAIL', 'F': 'FAIL',
                                'C*': 'SUPP', 'D*': 'SUPP', 'F***': 'SUPP', '*W*': 'WITHHELD',
                                'FAIL': 'DISCONTINUED'})

    def test_an_f_from_the_authority_means_a_repeat(self):
        profile, _ = self.student('NIT/1', 'Asha')
        # A (4) and F (0): GPA 2.0, so a repeat rather than a discontinuation.
        self.upload(self.exam, [('NIT/1', 'PST04101', 'A'), ('NIT/1', 'PST04102', 'F')])

        outcome = progression.evaluate(profile, self.sem1)

        self.assertEqual(outcome['proposed'], SemesterReview.REPEAT)

    def test_the_upload_writes_only_to_the_semester_named(self):
        old_anatomy = self.module('PST04101', 'Human Anatomy', semester=self.old_sem1)
        profile, (repeat, _) = self.student('NIT/2', 'Baraka')
        first_attempt = Student.objects.create(nactvet_reg_no='NIT/2', name='Baraka', profile=profile,
                                               module=old_anatomy)
        StudentResult.objects.create(student=first_attempt, authority_grade='F', authority_status='FAIL',
                                     final_approved=True)

        response = self.upload(self.exam, [('NIT/2', 'PST04101', 'C')])

        self.assertEqual(response.data['saved'], 1, response.data)
        self.assertEqual(StudentResult.objects.get(student=repeat).authority_grade, 'C')
        self.assertEqual(StudentResult.objects.get(student=first_attempt).authority_grade, 'F')

    def test_the_semester_must_be_named(self):
        self.student('NIT/3', 'Chausiku')

        response = self.exam.post('/api/results/authority-grades/', {
            'rows': [{'reg_no': 'NIT/3', 'module_code': 'PST04101', 'grade': 'A'}]}, format='json')

        self.assertEqual(response.status_code, 400)

    def test_a_student_not_in_that_semester_is_reported_not_guessed(self):
        self.student('NIT/4', 'Daudi')

        response = self.upload(self.exam, [('NIT/4', 'PST04101', 'A')], semester=self.old_sem1)

        self.assertEqual(response.data['saved'], 0)
        self.assertIn('not enrolled', response.data['errors'][0])

    def test_the_head_of_department_reads_and_does_not_upload(self):
        self.student('NIT/5', 'Edina')

        refused = self.upload(self.hod, [('NIT/5', 'PST04101', 'A')])
        summary = self.hod.get(f'/api/results-summary/?semester_id={self.sem1.id}&class_level_id={self.level4.id}')

        self.assertEqual(refused.status_code, 403)
        self.assertEqual(summary.status_code, 200)


class ResultsSummaryTests(AuthorityBase):
    def setUp(self):
        super().setUp()
        self.student('NIT/10', 'Pass One')
        self.student('NIT/11', 'Supp Two')
        self.student('NIT/12', 'Repeat Three')
        self.student('NIT/13', 'Waiting Four')
        self.student('NIT/14', 'Disco Five')
        self.upload(self.exam, [
            ('NIT/10', 'PST04101', 'A'), ('NIT/10', 'PST04102', 'B'),
            ('NIT/11', 'PST04101', 'C'), ('NIT/11', 'PST04102', 'C*'),
            ('NIT/12', 'PST04101', 'A'), ('NIT/12', 'PST04102', 'F'),
            ('NIT/13', 'PST04101', 'B'),
            ('NIT/14', 'PST04101', 'B'), ('NIT/14', 'PST04102', 'F'),
        ])
        response = self.exam.get(f'/api/results-summary/?semester_id={self.sem1.id}&class_level_id={self.level4.id}')
        self.assertEqual(response.status_code, 200, response.data)
        self.data = response.data

    def test_how_many_passed_went_to_supp_or_repeat(self):
        counts = self.data['counts']

        self.assertEqual((counts['total'], counts['PASS'], counts['SUPP'], counts['REPEAT'], counts['DISCO'],
                          counts['INCOMPLETE']), (5, 1, 1, 1, 1, 1))

    def test_the_gpa_is_points_times_credits_over_credits(self):
        by_reg = {row['reg_no']: row for row in self.data['students']}

        # A (4) and B (3), three credits each: (12 + 9) / 6.
        self.assertEqual(by_reg['NIT/10']['gpa'], 3.5)
        self.assertEqual(by_reg['NIT/10']['classification'], 'First Class')
        # A (4) and F (0): 2.0 — the F is repeated.
        self.assertEqual((by_reg['NIT/12']['gpa'], by_reg['NIT/12']['remark']), (2.0, 'REPEAT'))
        # B (3) and F (0): 1.5 — below 2.0 is a discontinuation, not a repeat.
        self.assertEqual((by_reg['NIT/14']['gpa'], by_reg['NIT/14']['remark']), (1.5, 'DISCO'))
        self.assertEqual(by_reg['NIT/11']['supp'], ['PST04102'])

    def test_each_module_s_pass_rate(self):
        modules = {row['code']: row for row in self.data['modules']}

        # PST04101: five graded, all passed.
        self.assertEqual((modules['PST04101']['results'], modules['PST04101']['passed'],
                          modules['PST04101']['pass_rate']), (5, 5, 100.0))
        # PST04102: B, C*, F, F graded and one still waiting — one pass in four.
        self.assertEqual((modules['PST04102']['passed'], modules['PST04102']['supp'],
                          modules['PST04102']['failed'], modules['PST04102']['waiting'],
                          modules['PST04102']['pass_rate']), (1, 1, 2, 1, 25.0))


class WithholdingTests(AuthorityBase):
    def setUp(self):
        super().setUp()
        self.profile, (self.enrollment, _) = self.student('NIT/20', 'Fatuma')
        self.enrollment.set_portal_pin('Portal#2026', require_change=False)
        self.enrollment.save()
        self.upload(self.exam, [('NIT/20', 'PST04101', 'A'), ('NIT/20', 'PST04102', 'B')])
        session = self.client.session
        session['student_id'] = self.enrollment.id
        session.save()

    def statements(self):
        return self.client.get('/student-dashboard/').context['result_statements']

    def test_withheld_results_read_withheld_on_the_portal_and_are_not_changed(self):
        response = self.exam.post('/api/result-withholdings/', {
            'profile_id': self.profile.id, 'semester_id': self.sem1.id, 'reason': 'Fees not paid'}, format='json')

        self.assertEqual(response.status_code, 201, response.data)
        statement = self.statements()[0]
        self.assertTrue(statement['withheld'])
        self.assertEqual(statement['modules'], [])
        self.assertIsNone(statement['gpa'])
        self.assertContains(self.client.get('/student-dashboard/'), 'Results withheld')
        # The results stand, and the review still reads them.
        self.assertEqual(progression.evaluate(self.profile, self.sem1)['gpa'], 3.5)

    def test_releasing_shows_them_again_and_keeps_the_record(self):
        held = self.exam.post('/api/result-withholdings/', {
            'profile_id': self.profile.id, 'semester_id': self.sem1.id}, format='json').data

        self.exam.post(f'/api/result-withholdings/{held["id"]}/release/', {}, format='json')

        statement = self.statements()[0]
        self.assertFalse(statement['withheld'])
        self.assertEqual([row['grade'] for row in statement['modules']], ['A', 'B'])
        self.assertIsNotNone(ResultWithholding.objects.get(pk=held['id']).released_at)

    def test_only_the_examination_office_withholds(self):
        response = self.hod.post('/api/result-withholdings/', {
            'profile_id': self.profile.id, 'semester_id': self.sem1.id}, format='json')

        self.assertEqual(response.status_code, 403)

    def test_the_summary_shows_who_is_withheld(self):
        self.exam.post('/api/result-withholdings/', {
            'profile_id': self.profile.id, 'semester_id': self.sem1.id, 'reason': 'Fees'}, format='json')

        data = self.exam.get(f'/api/results-summary/?semester_id={self.sem1.id}&class_level_id={self.level4.id}').data

        self.assertEqual(data['counts']['withheld'], 1)
        self.assertEqual(data['students'][0]['withheld_reason'], 'Fees')

    def test_the_authority_s_star_w_reads_as_withheld(self):
        self.upload(self.exam, [('NIT/20', 'PST04102', '*W*')])

        grades = {row['code']: row['grade'] for row in self.statements()[0]['modules']}

        self.assertEqual(grades['PST04102'], 'Withheld')


class SupplementaryCountsAsCTests(AuthorityBase):
    """A supplementary still to be sat counts as C in the GPA, whatever the
    first sitting was: passing it earns a C, failing it is a repeat. So nobody
    shows as discontinued before sitting the paper that may pass them."""

    def gpa(self, profile):
        return progression.evaluate(profile, self.sem1)['gpa']

    def test_any_starred_grade_counts_as_c(self):
        level6 = ClassLevel.objects.create(name='NTA Level 6', order=6)

        points = {grade: parse_authority_grade(grade, self.level4)['points'] for grade in ['B*', 'C*', 'D*', 'F***']}

        self.assertEqual(points, {'B*': 2, 'C*': 2, 'D*': 2, 'F***': 2})
        self.assertEqual(parse_authority_grade('A*', level6)['points'], 2)

    def test_a_pending_supplementary_does_not_make_a_student_discontinued(self):
        profile, _ = self.student('NIT/30', 'Gift')
        # Two D*s would be a GPA of 1.0 at face value — a discontinuation.
        self.upload(self.exam, [('NIT/30', 'PST04101', 'D*'), ('NIT/30', 'PST04102', 'D*')])

        outcome = progression.evaluate(profile, self.sem1)

        self.assertEqual(outcome['gpa'], 2.0)
        self.assertEqual(outcome['proposed'], SemesterReview.PENDING)
        summary = self.exam.get(f'/api/results-summary/?semester_id={self.sem1.id}'
                                f'&class_level_id={self.level4.id}').data
        self.assertEqual(summary['students'][0]['remark'], 'SUPP')

    def test_passing_the_supplementary_keeps_the_c_and_failing_it_is_a_repeat(self):
        passed, _ = self.student('NIT/31', 'Hope')
        failed, _ = self.student('NIT/32', 'Imani')
        self.upload(self.exam, [('NIT/31', 'PST04101', 'A'), ('NIT/31', 'PST04102', 'F*'),
                                ('NIT/32', 'PST04101', 'A'), ('NIT/32', 'PST04102', 'F*')])
        self.assertEqual((self.gpa(passed), self.gpa(failed)), (3.0, 3.0))       # (4 + 2) / 2 meanwhile

        # The authority's result after the supplementary.
        self.upload(self.exam, [('NIT/31', 'PST04102', 'C'), ('NIT/32', 'PST04102', 'F')])

        self.assertEqual(self.gpa(passed), 3.0)
        self.assertEqual(progression.evaluate(failed, self.sem1)['proposed'], SemesterReview.REPEAT)

    def test_marks_entered_here_follow_the_same_rule(self):
        profile, (anatomy, maths) = self.student('NIT/33', 'Jabiri')
        StudentResult.objects.create(student=anatomy, assign1=16, assign2=16, cat1_theory=16, cat2_theory=16,
                                     end_theory=80, ca_approved=True, final_approved=True)
        # A failed end exam: supplementary, not yet sat.
        StudentResult.objects.create(student=maths, assign1=16, assign2=16, cat1_theory=16, cat2_theory=16,
                                     end_theory=30, ca_approved=True, final_approved=True)

        rows = {row['code']: row for row in progression.evaluate(profile, self.sem1)['modules']}

        self.assertEqual((rows['PST04102']['status'], rows['PST04102']['points']), ('SUPP', 2))
