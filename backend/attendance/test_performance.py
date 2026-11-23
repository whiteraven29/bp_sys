"""Performance summaries and trends.

The college could always see one student's marks and one module's marks, and
nothing above that. These tests are about the questions that could not be asked
before: is NTA 5 doing better than last semester, is CAT 2 always worse than
CAT 1, and which module is failing half its class.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from . import analytics
from .models import (
    AcademicYear, ClassLevel, HeadOfDepartmentProfile, Module, Semester, Student,
    StudentResult, TeacherProfile,
)

User = get_user_model()


class PerformanceTestBase(TestCase):
    def setUp(self):
        self.year = AcademicYear.objects.create(name='2026/2027', is_active=True)
        self.last_year = AcademicYear.objects.create(name='2025/2026')
        self.sem = Semester.objects.create(academic_year=self.year, number=1, is_active=True)
        self.old_sem = Semester.objects.create(academic_year=self.last_year, number=1)
        self.level = ClassLevel.objects.create(name='NTA Level 4', order=4)

        self.module = Module.objects.create(name='Pharmaceutics', code='PHM101', teacher='T',
                                            class_level=self.level, semester=self.sem)
        self.old_module = Module.objects.create(name='Pharmaceutics', code='PHM101', teacher='T',
                                                class_level=self.level, semester=self.old_sem)

        self.admin = User.objects.create_superuser('admin', 'a@b.c', 'pw')
        self.api = APIClient()
        self.api.force_authenticate(self.admin)

    def _mark(self, module, reg, **marks):
        student = Student.objects.create(nactvet_reg_no=reg, name=f'Student {reg}',
                                         module=module)
        return StudentResult.objects.create(student=student, **marks)

    def _complete(self, module, reg, score, end=None):
        """A student with every CA component and, optionally, the end exam."""
        return self._mark(module, reg, assign1=score, assign2=score,
                          cat1_theory=score, cat2_theory=score,
                          end_theory=end if end is not None else score)


class MeasuringOneAssessmentTests(PerformanceTestBase):
    def test_a_cat_is_averaged_across_its_components(self):
        self._mark(self.module, 'R1', cat1_theory=80)
        self._mark(self.module, 'R2', cat1_theory=40)

        data = analytics.summary(assessment=analytics.CAT1)
        self.assertEqual(data['headline']['assessed'], 2)
        self.assertEqual(data['headline']['mean'], 60.0)
        self.assertEqual(data['headline']['highest'], 80.0)
        self.assertEqual(data['headline']['lowest'], 40.0)

    def test_a_student_who_has_not_sat_it_is_not_counted(self):
        self._mark(self.module, 'R1', cat1_theory=80)
        self._mark(self.module, 'R2')                       # nothing recorded

        data = analytics.summary(assessment=analytics.CAT1)
        self.assertEqual(data['headline']['assessed'], 1)
        self.assertEqual(data['headline']['mean'], 80.0)

    def test_an_absent_student_scores_zero_rather_than_vanishing(self):
        """Dropping them would flatter the module — they sat nothing."""
        self._mark(self.module, 'R1', cat1_theory=80)
        self._mark(self.module, 'R2', cat1_theory_absent=True)

        data = analytics.summary(assessment=analytics.CAT1)
        self.assertEqual(data['headline']['assessed'], 2)
        self.assertEqual(data['headline']['mean'], 40.0)

    def test_every_assessment_is_reported_as_a_percentage(self):
        """A CAT out of 100, a CA out of 40 and a final out of 100 have to sit
        on the same axis or a trend across them means nothing."""
        self._complete(self.module, 'R1', 60, end=60)

        for assessment in (analytics.CAT1, analytics.CAT2, analytics.CA,
                           analytics.END, analytics.FINAL):
            data = analytics.summary(assessment=assessment)
            self.assertEqual(data['headline']['mean'], 60.0, assessment)

    def test_a_continuous_assessment_is_passed_at_half_of_it(self):
        """The college's own CA eligibility rule is 20 out of 40, not 40%."""
        self._complete(self.module, 'R1', 45)
        self._complete(self.module, 'R2', 55)

        ca = analytics.summary(assessment=analytics.CA)
        self.assertEqual(ca['pass_mark'], 50)
        self.assertEqual(ca['headline']['pass_rate'], 50.0)

        final = analytics.summary(assessment=analytics.FINAL)
        self.assertEqual(final['pass_mark'], 40)
        self.assertEqual(final['headline']['pass_rate'], 100.0)

    def test_an_assessment_nobody_defined_is_refused(self):
        with self.assertRaises(ValueError):
            analytics.summary(assessment='vibes')


class DistributionAndModulesTests(PerformanceTestBase):
    def test_marks_are_banded_by_the_colleges_own_grades(self):
        for reg, score in (('R1', 90), ('R2', 70), ('R3', 55), ('R4', 30)):
            self._complete(self.module, reg, score, end=score)

        bands = {row['grade']: row['count']
                 for row in analytics.summary(assessment=analytics.FINAL)['distribution']}
        self.assertEqual(bands['A'], 1)     # NTA 4: A at 80
        self.assertEqual(bands['B'], 1)     # 65–79
        self.assertEqual(bands['C'], 1)     # 50–64
        self.assertEqual(bands['F'], 1)     # under 40

    def test_the_weakest_module_is_listed_first(self):
        strong = Module.objects.create(name='Anatomy', code='ANA101', teacher='T',
                                       class_level=self.level, semester=self.sem)
        self._complete(self.module, 'R1', 30, end=30)
        self._complete(strong, 'R2', 90, end=90)

        rows = analytics.summary(assessment=analytics.FINAL)['by_module']
        self.assertEqual([row['code'] for row in rows], ['PHM101', 'ANA101'])
        self.assertEqual(rows[0]['mean'], 30.0)
        self.assertEqual(rows[0]['pass_rate'], 0.0)
        self.assertEqual(rows[1]['pass_rate'], 100.0)

    def test_a_module_with_no_marks_is_left_out_rather_than_shown_as_zero(self):
        Module.objects.create(name='Anatomy', code='ANA101', teacher='T',
                              class_level=self.level, semester=self.sem)
        self._complete(self.module, 'R1', 60, end=60)

        rows = analytics.summary(assessment=analytics.FINAL)['by_module']
        self.assertEqual([row['code'] for row in rows], ['PHM101'])


class TrendTests(PerformanceTestBase):
    def test_the_trend_runs_oldest_first_across_semesters(self):
        self._complete(self.old_module, 'R1', 50, end=50)
        self._complete(self.module, 'R2', 70, end=70)

        points = analytics.summary(assessment=analytics.FINAL)['trend']
        self.assertEqual([p['academic_year'] for p in points], ['2025/2026', '2026/2027'])
        self.assertEqual([p['mean'] for p in points], [50.0, 70.0])

    def test_a_module_is_followed_by_its_code_and_not_its_row(self):
        """A module belongs to one semester, so following *this* row would give a
        single point and call it a trend. PHM101 taught again is the comparison."""
        self._complete(self.old_module, 'R1', 50, end=50)
        self._complete(self.module, 'R2', 70, end=70)

        points = analytics.summary(module=self.module, assessment=analytics.FINAL)['trend']
        self.assertEqual(len(points), 2)
        self.assertEqual([p['mean'] for p in points], [50.0, 70.0])

    def test_filtering_to_one_semester_still_leaves_the_trend_whole(self):
        """A single semester is a point, not a trend."""
        self._complete(self.old_module, 'R1', 50, end=50)
        self._complete(self.module, 'R2', 70, end=70)

        data = analytics.summary(semester=self.sem, assessment=analytics.FINAL)
        self.assertEqual(data['headline']['mean'], 70.0)      # the filter holds here
        self.assertEqual(len(data['trend']), 2)               # and is dropped here

    def test_a_year_filter_narrows_the_trend_to_that_year(self):
        self._complete(self.old_module, 'R1', 50, end=50)
        self._complete(self.module, 'R2', 70, end=70)

        points = analytics.summary(academic_year=self.year,
                                   assessment=analytics.FINAL)['trend']
        self.assertEqual([p['academic_year'] for p in points], ['2026/2027'])


class PerformanceApiTests(PerformanceTestBase):
    def test_the_endpoint_answers_with_everything_the_page_draws(self):
        self._complete(self.module, 'R1', 60, end=60)
        payload = self.api.get('/api/performance/?assessment=cat1').json()

        self.assertEqual(payload['assessment'], 'cat1')
        self.assertEqual(payload['assessment_label'], 'CAT 1')
        for key in ('headline', 'distribution', 'by_module', 'trend', 'options'):
            self.assertIn(key, payload)
        self.assertEqual(payload['headline']['mean'], 60.0)

    def test_the_filters_offered_are_the_ones_with_something_behind_them(self):
        options = self.api.get('/api/performance/').json()['options']
        self.assertEqual([y['name'] for y in options['academic_years']],
                         ['2026/2027', '2025/2026'])
        self.assertIn('PHM101', [m['code'] for m in options['modules']])
        self.assertEqual([a['value'] for a in options['assessments']],
                         ['result', 'cat1', 'cat2', 'ca', 'end', 'final'])

    def test_filters_narrow_the_population(self):
        other = ClassLevel.objects.create(name='NTA Level 5', order=5)
        other_module = Module.objects.create(name='Dispensing', code='DSP201', teacher='T',
                                             class_level=other, semester=self.sem)
        self._complete(self.module, 'R1', 40, end=40)
        self._complete(other_module, 'R2', 80, end=80)

        narrowed = self.api.get(
            f'/api/performance/?assessment=final&class_level_id={other.id}').json()
        self.assertEqual(narrowed['headline']['assessed'], 1)
        self.assertEqual(narrowed['headline']['mean'], 80.0)

    def test_an_assessment_nobody_defined_is_a_bad_request(self):
        self.assertEqual(self.api.get('/api/performance/?assessment=vibes').status_code, 400)

    def test_everyone_who_teaches_gets_one(self):
        """Reading a summary is not the same power as changing a mark. The
        admin roles see the college; a tutor sees their own modules."""
        hod = User.objects.create_user('hod', password='pw', is_staff=True)
        HeadOfDepartmentProfile.objects.create(user=hod, full_name='HoD')
        hod_api = APIClient()
        hod_api.force_authenticate(hod)
        self.assertEqual(hod_api.get('/api/performance/').json()['scope'], 'college')

        tutor = User.objects.create_user('tutor', password='pw')
        TeacherProfile.objects.create(user=tutor, full_name='Tutor')
        self.module.teachers.add(tutor)
        tutor_api = APIClient()
        tutor_api.force_authenticate(tutor)
        self.assertEqual(tutor_api.get('/api/performance/').json()['scope'], 'my-modules')


class OfficialResultTests(PerformanceTestBase):
    """Said 23 November 2026: results come as NACTVET's grades, not marks, and
    the college still wants passes and fails, the best and weakest modules,
    and each against the last time."""

    def _graded(self, module, reg, grade, supp='', attempt=Student.NORMAL):
        student = Student.objects.create(nactvet_reg_no=reg, name=f'Student {reg}', module=module, attempt=attempt)
        return StudentResult.objects.create(student=student, authority_grade=grade, authority_supp_grade=supp,
                                            final_approved=True)

    def test_imported_grades_count_without_any_marks(self):
        for reg, grade in (('R1', 'A'), ('R2', 'B'), ('R3', 'C*'), ('R4', 'D')):
            self._graded(self.module, reg, grade)
        data = analytics.summary(assessment=analytics.RESULT)
        self.assertEqual(data['kind'], 'grades')
        h = data['headline']
        self.assertEqual((h['graded'], h['passed'], h['supp'], h['failed']), (4, 2, 1, 1))
        self.assertEqual(h['pass_rate'], 50.0)
        bands = {row['grade']: row['count'] for row in data['distribution']}
        self.assertEqual((bands['A'], bands['B'], bands['C'], bands['D']), (1, 1, 1, 1))

    def test_the_supplementary_grade_replaces_the_first_sitting(self):
        self._graded(self.module, 'R1', 'C*', supp='C')       # passed the supplementary
        self._graded(self.module, 'R2', 'B*', supp='D*')      # failed it: repeats
        h = analytics.summary(assessment=analytics.RESULT)['headline']
        self.assertEqual((h['passed'], h['supp'], h['failed']), (1, 0, 1))

    def test_only_published_first_sittings_count(self):
        self._complete(self.module, 'R1', 70, end=70)                      # marks, not approved yet
        approved = self._complete(self.module, 'R2', 70, end=70)
        approved.final_approved = True
        approved.save()
        self._graded(self.module, 'R3', 'F', attempt=Student.REPEAT)      # a repeat: the student's second try
        h = analytics.summary(assessment=analytics.RESULT)['headline']
        self.assertEqual((h['graded'], h['passed']), (1, 1))

    def test_best_and_weakest_modules_and_the_last_time_each_was_taught(self):
        strong = Module.objects.create(name='Anatomy', code='ANA101', teacher='T', class_level=self.level,
                                       semester=self.sem)
        middle = Module.objects.create(name='Law', code='LAW101', teacher='T', class_level=self.level,
                                       semester=self.sem)
        for reg, grade in (('O1', 'A'), ('O2', 'D')):                   # last year: half passed
            self._graded(self.old_module, reg, grade)
        for reg, grade in (('N1', 'D'), ('N2', 'D'), ('N3', 'C')):      # this year: a third
            self._graded(self.module, reg, grade)
        for reg in ('S1', 'S2'):
            self._graded(strong, reg, 'A')
        self._graded(middle, 'M1', 'B')
        self._graded(middle, 'M2', 'C*')
        data = analytics.summary(assessment=analytics.RESULT)
        ranking = data['ranking']
        self.assertEqual(ranking['semester'], '2026/2027 · Semester 1')
        self.assertEqual(ranking['overall_pass_rate'], 57.1)                  # 4 of 7 this semester
        self.assertEqual([r['code'] for r in ranking['best']], ['ANA101'])
        self.assertEqual([r['code'] for r in ranking['weakest']], ['PHM101', 'LAW101'])   # weakest first
        row = next(r for r in data['by_module'] if r['code'] == 'PHM101' and r['semester'].startswith('2026'))
        self.assertEqual(row['pass_rate'], 33.3)
        self.assertEqual(row['previous']['label'], '2025/2026 · Semester 1')
        self.assertEqual(row['previous']['pass_rate'], 50.0)
        self.assertEqual(row['previous']['change'], -16.7)

    def test_a_semester_is_compared_with_the_same_semester_a_year_before(self):
        self._graded(self.old_module, 'O1', 'A')
        self._graded(self.old_module, 'O2', 'D')
        self._graded(self.module, 'N1', 'A')
        data = analytics.summary(semester=self.sem, class_level=self.level, assessment=analytics.RESULT)
        previous = data['headline']['previous']
        self.assertEqual((previous['label'], previous['pass_rate'], previous['change']),
                         ('2025/2026 · Semester 1', 50.0, 50.0))
        self.assertEqual([(p['academic_year'], p['pass_rate']) for p in data['trend']],
                         [('2025/2026', 50.0), ('2026/2027', 100.0)])

    def test_the_page_opens_on_the_official_result(self):
        self._graded(self.module, 'R1', 'B')
        payload = self.api.get('/api/performance/').json()
        self.assertEqual(payload['assessment'], 'result')
        self.assertEqual(payload['headline']['passed'], 1)
