"""Said 24 October 2026: earlier years' NACTVET results the system never held,
the "not sat" mark, a grade never quietly replaced, and the emergency
correction.

A repeat is sat in a later semester but shows under the year it clears — the
same for every repeat, from now on. Checked end to end on the college's own
sheets (27 students on NACTVET's repeat sheets: all as NACTVET printed them,
the two it printed POSTPONED read REPEAT, by your decision).
"""

from django.test import Client

from . import progression
from .grading import parse_authority_grade
from .models import (
    AcademicYear, AuthorityGradeClearing, Module, Notification, ResultCorrection, Semester, SemesterReview,
    Student, StudentProfile, StudentResult,
)
from .test_authority_results import AuthorityBase
from .test_finance_desk_only import office
from .testkit import show_all_results

MODULES = [{'code': 'PST04208', 'name': 'Law', 'credits': 4}, {'code': 'PST04209', 'name': 'Compounding', 'credits': 20},
           {'code': 'PST04211', 'name': 'Pharmacology', 'credits': 12}]


def sheet(reg, name, **grades):
    return [{'reg_no': reg, 'name': name, 'module_code': code, 'grade': grade} for code, grade in grades.items()]


class EarlierBase(AuthorityBase):
    def setUp(self):
        super().setUp()
        self.sem2_2526 = Semester.objects.create(academic_year=self.last_year, number=2)
        for row in MODULES:      # 2025/26 semester 2 runs these modules (the repeats were sat there)
            Module.objects.create(code=row['code'], name=row['name'], teacher='T', class_level=self.level4,
                                  semester=self.sem2_2526, programme=self.pst, credits=row['credits'])

    def earlier(self, sitting, rows, *, year='2024/2025', sat_in=None, modules=MODULES, client=None):
        return (client or self.exam).post('/api/results/earlier/', {
            'year': year, 'semester_number': 2, 'class_level_id': self.level4.id, 'sitting': sitting,
            'sat_in_semester_id': sat_in.id if sat_in else None, 'modules': modules, 'rows': rows}, format='json')

    def history(self, reg, name, first, supp=None, repeat=None):
        self.assertEqual(self.earlier('first', sheet(reg, name, **first)).status_code, 200)
        if supp:
            self.assertEqual(self.earlier('supp', sheet(reg, name, **supp)).status_code, 200)
        if repeat:
            done = self.earlier('repeat', sheet(reg, name, **repeat), sat_in=self.sem2_2526)
            self.assertEqual(done.status_code, 200, done.data)
            return done.data

    def repeaters(self, reg):
        rows = progression.results_summary(self.sem2_2526, self.level4, sheet='repeaters')['students']
        return next(row for row in rows if row['reg_no'] == reg)


class EarlierResultsTests(EarlierBase):
    def test_a_repeat_cleared_reads_under_the_year_it_clears(self):
        # NS1039's own sheets: SUPP at the first sitting, PST04209 passed at the
        # supplementary, PST04211 failed there, passed at the repeat.
        done = self.history('NS1039/0002/2023', 'Agripina Juwal',
                            first={'PST04208': 'C', 'PST04209': 'C***', 'PST04211': 'C*'},
                            supp={'PST04208': 'C', 'PST04209': 'C', 'PST04211': 'D*'},
                            repeat={'PST04208': 'C', 'PST04209': 'C', 'PST04211': 'C'})
        self.assertEqual((done['repeats_added'], done['saved'], done['owed']['NS1039/0002/2023']),
                         (1, 1, ['PST04211']))
        row = self.repeaters('NS1039/0002/2023')
        self.assertEqual((row['origin'], row['remark']), ('Sem 2 · 2024/2025', 'PASS'))
        # A record of an earlier year: no portal password, so it never opens the portal.
        self.assertFalse(Student.objects.filter(nactvet_reg_no='NS1039/0002/2023').exclude(portal_pin_hash='').exists())

    def test_ns_keeps_the_year_s_grade_and_the_debt(self):
        self.history('NS3522/0015/2013', 'Repeat Not Sat', first={'PST04208': 'C*', 'PST04209': 'A', 'PST04211': 'B'},
                     supp={'PST04208': 'C*', 'PST04209': 'A', 'PST04211': 'B'},
                     repeat={'PST04208': 'NS', 'PST04209': 'A', 'PST04211': 'B'})
        row = self.repeaters('NS3522/0015/2013')
        self.assertEqual((row['remark'], row['grades']['PST04208'], row['failed']), ('REPEAT', 'C*', ['PST04208']))

    def test_failed_again_at_the_repeat_is_a_fail(self):
        self.history('NS1129/0008/2023', 'Failed Again', first={'PST04208': 'C', 'PST04209': 'B', 'PST04211': 'C*'},
                     supp={'PST04208': 'C', 'PST04209': 'B', 'PST04211': 'C*'},
                     repeat={'PST04208': 'C', 'PST04209': 'B', 'PST04211': 'D*'})
        self.assertEqual(self.repeaters('NS1129/0008/2023')['remark'], 'DISCO')

    def test_the_portal_reads_the_year_it_clears(self):
        self.history('NS3522/0015/2013', 'Repeat Not Sat', first={'PST04208': 'C*', 'PST04209': 'A', 'PST04211': 'B'},
                     supp={'PST04208': 'C*', 'PST04209': 'A', 'PST04211': 'B'},
                     repeat={'PST04208': 'NS', 'PST04209': 'A', 'PST04211': 'B'})
        repeat = Student.objects.get(nactvet_reg_no='NS3522/0015/2013', attempt=Student.REPEAT)
        repeat.set_portal_pin('Portal#2026', require_change=False); repeat.save()
        client = Client(); session = client.session; session['student_id'] = repeat.id; session.save()
        show_all_results()
        statement = client.get('/student-dashboard/').context['result_statements'][0]
        self.assertEqual((statement['label'], statement['remark']), ('Sem 2 · 2024/2025', 'REPEAT'))
        row = next(m for m in statement['modules'] if m['code'] == 'PST04208')
        self.assertEqual(row['grade'], 'C*')
        self.assertIn('Not sat at the repeat, Sem 2 · 2025/2026', row['note'])

    def test_enrolled_with_the_class_is_recorded_as_the_repeat_nactvet_says(self):
        """NS1314 on the server (25 October 2026): enrolled in 2025/26 Sem 2 with
        the class, as first sittings — NACTVET's repeat sheet says the owed
        modules were repeats. They become repeats; the ones passed before are
        taken off (withdrawn, kept)."""
        self.history('NS1314/0060/2021', 'Class Enrolled', first={'PST04208': 'B', 'PST04209': 'A', 'PST04211': 'C*'},
                     supp={'PST04208': 'B', 'PST04209': 'A', 'PST04211': 'D*'})
        profile = StudentProfile.objects.get(nactvet_reg_no='NS1314/0060/2021')
        for module in Module.objects.filter(semester=self.sem2_2526):
            Student.objects.create(nactvet_reg_no=profile.nactvet_reg_no, name=profile.name, profile=profile, module=module)
        done = self.earlier('repeat', sheet('NS1314/0060/2021', 'Class Enrolled', PST04208='B', PST04209='A',
                                            PST04211='C'), sat_in=self.sem2_2526)
        self.assertEqual(done.status_code, 200, done.data)
        self.assertEqual((done.data['repeats_converted'], done.data['saved'], len(done.data['withdrawn'])), (1, 1, 2))
        sitting = Student.objects.get(nactvet_reg_no='NS1314/0060/2021', module__semester=self.sem2_2526,
                                      module__code='PST04211')
        self.assertEqual((sitting.attempt, sitting.repeat_of.module.semester.academic_year.name),
                         (Student.REPEAT, '2024/2025'))
        row = self.repeaters('NS1314/0060/2021')
        self.assertEqual((row['origin'], row['remark']), ('Sem 2 · 2024/2025', 'PASS'))
        self.assertEqual(Student.objects.filter(nactvet_reg_no='NS1314/0060/2021', module__semester=self.sem2_2526,
                                                withdrawn_at__isnull=False).count(), 2)

    def test_a_class_enrollment_already_graded_is_left_alone(self):
        self.history('NS1315/0001/2021', 'Graded Already', first={'PST04208': 'B', 'PST04209': 'A', 'PST04211': 'C*'},
                     supp={'PST04208': 'B', 'PST04209': 'A', 'PST04211': 'D*'})
        profile = StudentProfile.objects.get(nactvet_reg_no='NS1315/0001/2021')
        module = Module.objects.get(semester=self.sem2_2526, code='PST04211')
        sitting = Student.objects.create(nactvet_reg_no=profile.nactvet_reg_no, name=profile.name, profile=profile,
                                         module=module)
        StudentResult.objects.create(student=sitting, authority_grade='B', final_approved=True)
        done = self.earlier('repeat', sheet('NS1315/0001/2021', 'Graded Already', PST04211='C'), sat_in=self.sem2_2526)
        self.assertIn('already graded', done.data['warnings'][0])
        sitting.refresh_from_db()
        self.assertEqual(sitting.attempt, Student.NORMAL)

    def test_carried_grades_are_checked_never_written(self):
        done = self.history('NS2182/0032/2023', 'Carried', first={'PST04208': 'C', 'PST04209': 'B***', 'PST04211': 'B'},
                            supp={'PST04208': 'C', 'PST04209': 'C***', 'PST04211': 'B'},
                            repeat={'PST04208': 'B', 'PST04209': 'B', 'PST04211': 'B'})
        self.assertEqual(len(done['warnings']), 1)           # PST04208: B on the sheet, C on record
        self.assertIn('PST04208', done['warnings'][0])
        first = StudentResult.objects.get(student__nactvet_reg_no='NS2182/0032/2023', student__module__code='PST04208',
                                          student__attempt=Student.NORMAL)
        self.assertEqual(first.authority_grade, 'C')

    def test_not_this_year_and_not_without_credits(self):
        self.assertEqual(self.earlier('first', sheet('X/1', 'X', PST04208='A'), year='2026/2027').status_code, 400)
        refused = self.earlier('first', sheet('X/1', 'X', PST04999='A'), modules=[{'code': 'PST04999', 'name': 'New'}])
        self.assertEqual(refused.status_code, 400)
        self.assertIn('credits', refused.data['detail'])
        _, principal = office('boss', 'principal')
        self.assertEqual(self.earlier('first', sheet('X/1', 'X', PST04208='A'), client=principal).status_code, 403)

    def test_a_record_of_an_earlier_year_is_never_the_portal_s_current_one(self):
        from .views import get_logged_student, PORTAL_PROFILE
        profile = StudentProfile.objects.create(nactvet_reg_no='NS7000/0001/2024', name='Now Here')
        current = Student.objects.create(nactvet_reg_no=profile.nactvet_reg_no, name=profile.name, profile=profile,
                                         module=self.anatomy)
        self.history('NS7000/0001/2024', 'Now Here', first={'PST04208': 'A', 'PST04209': 'A', 'PST04211': 'A'})
        request = type('R', (), {'session': {PORTAL_PROFILE: profile.id}})()
        self.assertEqual(get_logged_student(request).id, current.id)


class NotSatAndGuardTests(EarlierBase):
    def test_ns_is_only_for_a_repeat_sitting(self):
        self.assertEqual(parse_authority_grade('NS', self.level4)['status'], 'NOT_SAT')
        self.student('NIT/40', 'First Timer')
        done = self.upload(self.exam, [('NIT/40', 'PST04101', 'NS')]).data
        self.assertIn('only for a repeat', done['errors'][0])

    def test_a_grade_in_is_never_quietly_replaced(self):
        self.student('NIT/41', 'Guarded')
        self.upload(self.exam, [('NIT/41', 'PST04101', 'B')])
        again = self.upload(self.exam, [('NIT/41', 'PST04101', 'B'), ('NIT/41', 'PST04101', 'A')]).data
        self.assertEqual(again['unchanged'], 1)
        self.assertIn('already B', again['errors'][0])
        self.assertEqual(StudentResult.objects.get(student__nactvet_reg_no='NIT/41').authority_grade, 'B')

    def test_a_repeat_alone_not_sat_still_reads_repeat(self):
        self.assertEqual(progression.decide([{'code': 'PST04211', 'status': 'NOT_SAT', 'points': None, 'credits': 12,
                                              'attempt': Student.REPEAT}])['remark'], 'REPEAT')


class CorrectionTests(EarlierBase):
    def setUp(self):
        super().setUp()
        self.profile, (self.enrollment, _) = self.student('NIT/50', 'Wrong Grade')
        self.upload(self.exam, [('NIT/50', 'PST04101', 'C'), ('NIT/50', 'PST04102', 'B')])
        self.dparc_user, self.dparc = office('dparc', 'dparc')

    def propose(self, new='A', client=None, **extra):
        return (client or self.exam).post('/api/result-corrections/', {
            'semester_id': self.sem1.id, 'reg_no': 'NIT/50', 'module_code': 'PST04101', 'field': 'grade',
            'new_value': new, 'reason': 'NACTVET re-issued', 'reference': 'NACTE/2026/14', **extra}, format='json')

    def test_the_exam_office_corrects_at_once_and_the_offices_are_told(self):
        """Said 25 October 2026: results are the examination officer's — no
        approval; DPARC and the Principal are told."""
        made = self.propose()
        self.assertEqual(made.status_code, 201, made.data)
        result = StudentResult.objects.get(student=self.enrollment)
        self.assertEqual((result.authority_grade, result.authority_status), ('A', 'PASS'))
        self.assertEqual(ResultCorrection.objects.get().old_value, 'C')               # the old grade kept
        told = Notification.objects.get(user=self.dparc_user, kind=Notification.CORRECTION_DECIDED)
        self.assertIn('C → A', told.body)
        self.assertEqual(self.propose(new='B', client=self.dparc).status_code, 403)   # DPARC reads, does not correct
        self.assertEqual(len(self.dparc.get('/api/result-corrections/').data['rows']), 1)

    def test_the_gpa_follows_the_correction_everywhere(self):
        """C and B, three credits each: 2.5. Corrected to A and B: 3.5 — on the
        Results Summary, the portal and a review not yet confirmed."""
        from django.test import Client
        review = SemesterReview.objects.create(profile=self.profile, semester=self.sem1, class_level=self.level4,
                                               proposed=SemesterReview.CLEAR, gpa='2.5')
        self.assertEqual(self.propose(new='A').status_code, 201)
        review.refresh_from_db()
        self.assertEqual(float(review.gpa), 3.5)
        summary = self.exam.get(f'/api/results-summary/?semester_id={self.sem1.id}'
                                f'&class_level_id={self.level4.id}').data
        self.assertEqual(next(s['gpa'] for s in summary['students'] if s['reg_no'] == 'NIT/50'), 3.5)
        self.enrollment.set_portal_pin('Portal#2026', require_change=False); self.enrollment.save()
        client = Client(); session = client.session; session['student_id'] = self.enrollment.id; session.save()
        show_all_results()
        self.assertEqual(client.get('/student-dashboard/').context['result_statements'][0]['gpa'], 3.5)

    def test_what_may_not_be_corrected(self):
        self.assertEqual(self.propose(new='C').status_code, 400)               # the grade already there
        self.assertEqual(self.propose(new='NS').status_code, 400)              # NS only on a repeat
        self.assertEqual(self.propose(new='Z').status_code, 400)
        self.assertEqual(self.propose(reason=' ').status_code, 400)

    def test_a_grade_written_against_a_module_not_sat_is_removed(self):
        done = self.propose(new='', remove=True)
        self.assertEqual(done.status_code, 201, done.data)
        result = StudentResult.objects.get(student=self.enrollment)
        self.assertEqual((result.authority_grade, result.final_approved), ('', False))
        row = progression.module_row(Student.objects.get(pk=self.enrollment.pk))
        self.assertEqual(row['status'], 'INCOMPLETE')
        self.assertEqual(self.propose(new='', remove=True).status_code, 400)       # nothing left to remove

    def test_a_confirmed_student_is_confirmed_again_as_the_results_now_say(self):
        from .models import OutstandingRepeat
        _, examiner = office('examiner', 'exam_officer')
        review = SemesterReview.objects.create(profile=self.profile, semester=self.sem1, class_level=self.level4,
                                               proposed=SemesterReview.CLEAR, confirmed=SemesterReview.CLEAR)
        made = self.propose(new='D', client=examiner)   # the module was failed after all: (1 + 3) / 2 = 2.0
        self.assertIn('Confirm it again', made.data['warning'])
        review.refresh_from_db()
        self.assertEqual((review.confirmed, review.proposed), (SemesterReview.CLEAR, SemesterReview.REPEAT))
        again = examiner.post(f'/api/semester-reviews/{review.id}/confirm/', {'outcome': SemesterReview.REPEAT},
                              format='json')
        self.assertEqual(again.status_code, 200, again.data)
        self.assertTrue(OutstandingRepeat.objects.filter(profile=self.profile, module_code='PST04101',
                                                         status=OutstandingRepeat.OPEN).exists())
        self.assertTrue(Notification.objects.filter(user=self.dparc_user, title__startswith='Confirmed again').exists())
        # The grade was right after all: corrected back, confirmed again — the repeat it raised is cancelled.
        self.propose(new='C', client=examiner)
        review.refresh_from_db()
        self.assertEqual(review.proposed, SemesterReview.CLEAR)
        examiner.post(f'/api/semester-reviews/{review.id}/confirm/', {'outcome': review.proposed}, format='json')
        self.assertEqual(OutstandingRepeat.objects.get(module_code='PST04101').status, OutstandingRepeat.CANCELLED)
        # Module A not sat after all: its grade removed — the semester waits, so they
        # are confirmed provisional until the result comes; the repeat stays cancelled.
        removed = self.propose(new='', remove=True, client=examiner)
        self.assertEqual(removed.status_code, 201, removed.data)
        review.refresh_from_db()
        self.assertEqual(review.proposed, SemesterReview.PENDING)
        waiting = examiner.post(f'/api/semester-reviews/{review.id}/confirm/', {'outcome': SemesterReview.PROVISIONAL},
                                format='json')
        self.assertEqual(waiting.status_code, 200, waiting.data)
        self.assertIn(self.profile.id, progression.provisional_profile_ids())
        # Not overruling: the exam office confirms only what the results say.
        refused = examiner.post(f'/api/semester-reviews/{review.id}/confirm/',
                                {'outcome': SemesterReview.REPEAT, 'reason': 'x'}, format='json')
        self.assertEqual(refused.status_code, 403)


class IncompleteOnThePortalTests(EarlierBase):
    """Said 25 October 2026: a module with no result shows empty, INCOMPLETE —
    and the semester INCOMPLETE, nothing else; a blank or NA records nothing."""

    def setUp(self):
        super().setUp()
        self.profile, (self.enrollment, _) = self.student('NIT/60', 'Half Done')
        self.enrollment.set_portal_pin('Portal#2026', require_change=False)
        self.enrollment.save()

    def statement(self):
        from django.test import Client
        client = Client(); session = client.session; session['student_id'] = self.enrollment.id; session.save()
        show_all_results()
        return client.get('/student-dashboard/').context['result_statements'][0]

    def test_a_blank_or_na_records_nothing_and_reads_incomplete(self):
        done = self.upload(self.exam, [('NIT/60', 'PST04101', 'A'), ('NIT/60', 'PST04102', 'NA')]).data
        self.assertEqual(done['saved'], 1)
        self.assertFalse(StudentResult.objects.filter(student__module__code='PST04102', student__nactvet_reg_no='NIT/60')
                         .exclude(authority_grade='').exists())
        statement = self.statement()
        self.assertEqual(statement['remark'], 'INCOMPLETE')
        self.assertIsNone(statement['gpa'])
        self.assertEqual(statement['comment'], '')
        rows = {row['code']: (row['grade'], row['status']) for row in statement['modules']}
        self.assertEqual(rows['PST04102'], ('', 'INCOMPLETE'))
        self.assertEqual(rows['PST04101'][0], 'A')

    def test_no_gpa_for_an_incomplete_student_and_none_in_the_average(self):
        """NACTVET prints NA: no GPA from part of a semester, on any screen."""
        self.student('NIT/61', 'All Done')
        self.upload(self.exam, [('NIT/60', 'PST04101', 'A'),
                                ('NIT/61', 'PST04101', 'B'), ('NIT/61', 'PST04102', 'B')])
        summary = self.exam.get(f'/api/results-summary/?semester_id={self.sem1.id}'
                                f'&class_level_id={self.level4.id}').data
        by_reg = {row['reg_no']: row for row in summary['students']}
        self.assertEqual((by_reg['NIT/60']['remark'], by_reg['NIT/60']['gpa']), ('INCOMPLETE', None))
        self.assertEqual(summary['average_gpa'], 3.0)                       # NIT/61 alone


class OnlyLevelSixFinishesTests(EarlierBase):
    def test_a_programme_with_no_next_level_holds_the_student_not_finishes_them(self):
        from .models import Programme, SemesterRegistration
        one_level = Programme.objects.create(department=self.pst.department, name='PST Level 4', code='PST04')
        one_level.levels.set([self.level4])
        profile = StudentProfile.objects.create(nactvet_reg_no='NIT/70', name='Not Finished')
        sem2 = Semester.objects.create(academic_year=self.year, number=2)
        SemesterRegistration.objects.create(profile=profile, semester=sem2, programme=one_level,
                                            class_level=self.level4, kind=SemesterRegistration.CONTINUING)
        SemesterReview.objects.create(profile=profile, semester=sem2, class_level=self.level4, programme=one_level,
                                      proposed=SemesterReview.CLEAR, confirmed=SemesterReview.CLEAR)
        plan = progression.plan_advance(sem2)
        self.assertFalse([m for m in plan['moves'] if m['reg_no'] == 'NIT/70' and m['action'] == 'completed'])
        self.assertIn('no level after', next(b['reason'] for b in plan['blocked'] if b['reg_no'] == 'NIT/70'))
