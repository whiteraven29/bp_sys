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


class YearReportTests(PerformanceTestBase):
    """Said 23 November 2026: a table of the year's results — each module, the
    students registered for it with the repeaters, pass, supp, repeat, failed
    and incomplete in percent, field modules left out — and last year beside it."""

    def _sit(self, module, reg, grade='', attempt=Student.NORMAL, **extra):
        student = Student.objects.create(nactvet_reg_no=reg, name=f'Student {reg}', module=module,
                                         attempt=attempt, **extra)
        if grade:
            StudentResult.objects.create(student=student, authority_grade=grade, final_approved=True)
        return student

    def _a_semester(self):
        """Seven students of a whole semester (four modules, the field module
        among them), two repeating PHM101, one taken off part-way."""
        from django.utils import timezone
        others = [Module.objects.create(name=n, code=c, teacher='T', class_level=self.level, semester=self.sem,
                                        credits=10) for c, n in (('ANA101', 'Anatomy'), ('LAW101', 'Law'))]
        field = Module.objects.create(name='Field Practice', code='PST04211', teacher='T', class_level=self.level,
                                      semester=self.sem, is_field_module=True, credits=10)
        self.module.credits = 10
        self.module.save()
        modules = [self.module, *others, field]
        students = {
            'S1': ['C*', 'B', 'B', 'B'],      # GPA 2.7, 1 of 4 failed        → SUPP
            'S2': ['C*', 'D*', 'C*', 'B'],    # GPA 2.0, 3 of 4 failed        → REPEAT, no supplementary
            'S3': ['D', 'D', 'C', 'C'],       # GPA 1.5                        → FAIL (discontinued)
            'S4': ['D', 'B', 'B', 'A'],       # GPA 2.7, failed outright       → REPEAT
            'S5': ['A', 'A', 'A', 'A'],       #                                → PASS
            'S6': ['', 'A', 'A', 'A'],        # no result yet                  → INCOMPLETE
            'S7': ['C*', 'D*', 'A', 'D*'],    # GPA 2.0, 3 of 4 with the field → REPEAT
        }
        for reg, grades in students.items():
            for module, grade in zip(modules, grades):
                self._sit(module, reg, grade)
        self._sit(self.module, 'S8', 'D', attempt=Student.REPEAT)
        self._sit(self.module, 'S9', 'B', attempt=Student.REPEAT)
        self._sit(self.module, 'S10', withdrawn_at=timezone.now())        # taken off part-way: not counted
        self._sit(self.old_module, 'O1', 'A')
        self._sit(self.old_module, 'O2', 'A')

    def test_a_module_is_read_on_its_own_grades(self):
        """Said 23 November 2026: don't mix the module's performance with the
        students' overall pass, repeat and fail."""
        self._a_semester()
        report = analytics.year_report(self.year)
        self.assertEqual(report['previous_year'], '2025/2026')
        self.assertNotIn('PST04211', [r['code'] for r in report['rows']])              # field: when asked for
        self.assertIn('PST04211', [r['code'] for r in analytics.year_report(self.year, include_field=True)['rows']])
        row = next(r for r in report['rows'] if r['code'] == 'PHM101')
        self.assertEqual((row['registered'], row['repeaters']), (9, 2))
        # S1, S2 and S7 starred → supp, whatever their semester; S3, S4 and S8 (a repeat sitting) → fail.
        self.assertEqual({name: row[name] for name in analytics.YEAR_OUTCOMES},
                         {'pass': 2, 'supp': 3, 'fail': 3, 'incomplete': 1})
        self.assertEqual((row['pass_pct'], row['supp_pct']), (22.2, 33.3))
        self.assertEqual((row['previous']['pass_pct'], row['change']), (100.0, -77.8))
        self.assertTrue(row['previous']['same_name'])
        law = next(r for r in report['rows'] if r['code'] == 'LAW101')
        self.assertEqual((law['pass'], law['supp'], law['fail']), (6, 1, 0))

    def test_the_students_overall_remarks_are_apart_and_as_the_results_summary(self):
        self._a_semester()
        overall = analytics.year_report(self.year)['overall']
        row = next(r for r in overall['rows'] if r['sheet'] == 'continuing')
        self.assertEqual((row['semester'], row['level'], row['students']), (1, 'NTA Level 4', 7))
        self.assertEqual({name: row[name] for _remark, name in analytics.OVERALL_OUTCOMES},
                         {'pass': 1, 'supp': 1, 'repeat': 3, 'fail': 1, 'incomplete': 1})
        self.assertEqual((row['previous']['pass_pct'], row['change']), (100.0, -85.7))
        self.assertIn('repeaters', [r['sheet'] for r in overall['rows']])               # S8 and S9
        self.assertIsNone(analytics.year_report(self.year, modules=Module.objects.filter(id=self.module.id))['overall'])

    def test_a_code_reused_for_another_module_is_flagged_and_one_gone_is_listed(self):
        Module.objects.create(name='Old Module', code='OLD101', teacher='T', class_level=self.level,
                              semester=self.old_sem)
        self._sit(Module.objects.get(code='OLD101'), 'O1', 'C')
        self.old_module.name = 'Another Name'
        self.old_module.save()
        self._sit(self.old_module, 'O2', 'A')
        self._sit(self.module, 'N1', 'B')
        report = analytics.year_report(self.year)
        self.assertFalse(report['rows'][0]['previous']['same_name'])
        self.assertEqual([r['code'] for r in report['not_this_year']], ['OLD101'])

    def test_the_endpoint_answers_for_the_chosen_year(self):
        self._sit(self.module, 'N1', 'B')
        data = self.api.get(f'/api/performance/year/?academic_year_id={self.year.id}').json()
        self.assertEqual((data['academic_year'], data['rows'][0]['pass_pct']), ('2026/2027', 100.0))

    def test_the_download_is_a_workbook_with_the_table_and_a_chart(self):
        from io import BytesIO

        from openpyxl import load_workbook
        self._sit(self.old_module, 'O1', 'D')
        self._sit(self.module, 'N1', 'B')
        reply = self.api.get(f'/api/performance/year/download/?academic_year_id={self.year.id}')
        self.assertEqual(reply.status_code, 200)
        self.assertIn('Module-results-2026-2027.xlsx', reply['Content-Disposition'])
        book = load_workbook(BytesIO(reply.content))
        self.assertEqual(book.sheetnames, ['Modules 2026-2027', 'Overall 2026-2027', 'Comparison'])
        table, compare = book['Modules 2026-2027'], book['Comparison']
        self.assertEqual([c.value for c in table[4]][:7],
                         ['Level', 'Module code', 'Module', 'Semester', 'Registered', 'of whom repeaters', 'Pass %'])
        # Each percentage with its number in brackets, as the page shows it.
        self.assertEqual([c.value for c in table[5]][1:8], ['PHM101', 'Pharmaceutics', 1, 1, 0, '100 (1)', '0 (0)'])
        self.assertEqual(table[5][10].value, '0 (0)')                           # last year: O1 failed
        self.assertEqual([c.value for c in compare[2]], ['PHM101', 'Pharmaceutics', 0, 100, 100])
        self.assertEqual(len(compare._charts), 1)
        fills = [series.graphicalProperties.solidFill.srgbClr for series in compare._charts[0].series]
        self.assertEqual([getattr(fill, 'val', fill) for fill in fills], ['E9B11B', 'C62F2F'])   # yellow then, red now
