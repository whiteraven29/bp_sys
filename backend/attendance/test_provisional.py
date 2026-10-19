"""A year opened before the results are out, and finishing after Level 5 (said
18 October 2026):

  * the advance no longer stops for results that are not out: those students
    are placed provisionally where a pass takes them; a student whose results
    so far show a failure, or who would finish the programme, waits instead;
  * when the results arrive they are settled — passes in one go; a repeat or
    discontinuation takes the place away and reverses its fees — and the
    student is told;
  * a provisional student sits no end-of-semester exam until settled; the
    examination officer sets the cut-off, after which the offices are warned;
  * a student who passed Level 5 may ask, from the portal, to finish there; only
    the Principal approves; the next level is called off and they finish
    through the finishing route;
  * three months after admission opened, the admission office decides about
    continuing students who never reported.
"""

from datetime import date, timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from . import admissions, lifecycle, progression
from .models import (
    AdmissionWindow, Application, ExitRequest, Notification, SemesterRegistration, SemesterReview, Student,
    StudentStanding,
)
from . import test_progression
from .views import ca_eligibility_for_student, set_roles

User = get_user_model()


def office(username, *roles):
    user = User.objects.create_user(username, password='pw')
    set_roles(user, list(roles), full_name=username.title())
    client = APIClient()
    client.force_authenticate(user)
    return user, client


class ProvisionalBase(test_progression.AdvanceTests):
    # The AdvanceTests' own tests are not run again from here.
    def setUp(self):
        super().setUp()
        self.principal_user, self.principal = office('boss', 'principal')
        self.dparc_user, self.dparc = office('dparc', 'dparc')
        self.exam_user, self.exam = office('exam', 'exam_officer')

    def waiting_student(self, reg_no, level=None, *, failed=False):
        """Results not out yet: the marks are in but not published."""
        profile = self.student(reg_no)
        level = level or self.level4
        self.register(profile, self.sem2, level)
        self.grade(self.enrol(profile, self.module(f'{reg_no[-3:]}A', level, self.sem2)), 80, approved=False)
        if failed:
            # Failed after the supplementary: a failure, not a supplementary still to come.
            self.grade(self.enrol(profile, self.module(f'{reg_no[-3:]}B', level, self.sem2)), 10, supp=10)
        return profile

    def open_year(self):
        progression.build_reviews(self.sem2)
        return self.advance()

    def publish(self, profile, mark):
        for result in Student.objects.filter(profile=profile, module__semester=self.sem2):
            result.result.end_theory = mark
            for field in ('assign1', 'assign2', 'cat1_theory', 'cat2_theory'):
                setattr(result.result, field, mark)
            result.result.final_approved = True
            result.result.save()


for _name in [n for n in dir(test_progression.AdvanceTests) if n.startswith('test_')]:
    setattr(ProvisionalBase, _name, None)


class OpeningTheYearTests(ProvisionalBase):
    def test_results_not_out_are_placed_provisionally_not_blocked(self):
        profile = self.waiting_student('REG/101')
        progression.build_reviews(self.sem2)
        plan = progression.plan_advance(self.sem2)
        self.assertEqual(plan['blocked'], [])
        self.assertTrue(plan['moves'][0]['provisional'])
        summary = self.advance()
        self.assertEqual(summary['provisional'], 1)
        review = SemesterReview.objects.get(profile=profile, semester=self.sem2)
        self.assertEqual(review.confirmed, SemesterReview.PROVISIONAL)
        application = Application.objects.get(profile=profile, semester=self.next_sem1)
        self.assertEqual(application.class_level, self.level5)
        self.assertIn(profile.id, progression.provisional_profile_ids())
        # Finance reads it as provisional, not as a pass and not as a supplementary.
        results = admissions.academic_summary(application)['results']
        self.assertIn('placed provisionally', results)
        self.assertNotIn('supplementary', results)

    def test_everybody_goes_on_provisionally_and_only_finishing_waits(self):
        # Said 19 October 2026: one advance for every level and programme —
        # a failure so far goes on provisionally too, and is settled (the place
        # taken away) when the results are in. Finishing has no next level.
        failing = self.waiting_student('REG/102', failed=True)
        finishing = self.waiting_student('REG/103', self.level6)
        progression.build_reviews(self.sem2)
        plan = progression.plan_advance(self.sem2)
        self.assertEqual(plan['blocked'], [])
        self.assertEqual({row['reg_no'] for row in plan['waiting']}, {'REG/103'})
        summary = self.advance()
        self.assertEqual((summary['provisional'], summary['waiting']), (1, 1))
        self.assertEqual(Application.objects.get(profile=failing, semester=self.next_sem1).class_level, self.level5)
        self.assertEqual(SemesterReview.objects.get(profile=failing).confirmed, SemesterReview.PROVISIONAL)
        self.assertFalse(Application.objects.filter(profile=finishing).exists())
        self.assertFalse(SemesterReview.objects.get(profile=finishing).is_confirmed)

    def test_a_failure_so_far_loses_the_place_when_settled(self):
        failing = self.waiting_student('REG/104', failed=True)
        self.open_year()
        review = SemesterReview.objects.get(profile=failing, semester=self.sem2)
        self.assertEqual(review.confirmed, SemesterReview.PROVISIONAL)
        self.publish(failing, 10)
        progression.refresh_provisional()
        review.refresh_from_db()
        self.assertIn(review.proposed, (SemesterReview.REPEAT, SemesterReview.DISCONTINUED))
        progression.confirm_review(review, review.proposed, actor=self.principal_user)
        self.assertFalse(Application.objects.filter(profile=failing, semester=self.next_sem1)
                         .exclude(state__in=[Application.CANCELLED, Application.REJECTED]).exists())


class SettlingTests(ProvisionalBase):
    def setUp(self):
        super().setUp()
        self.profile = self.waiting_student('REG/111')
        self.open_year()
        self.admit_waiting(self.profile, self.next_sem1)

    def test_a_pass_is_settled_in_one_go_and_the_place_stands(self):
        self.publish(self.profile, 80)
        rows = progression.refresh_provisional()
        self.assertEqual((rows[0]['proposed'], rows[0]['results_in']), (SemesterReview.CLEAR, True))
        settled = self.principal.post('/api/provisional/settle-passes/', {}, format='json')
        self.assertEqual(settled.data['settled'], 1)
        self.assertNotIn(self.profile.id, progression.provisional_profile_ids())
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.standing.class_level, self.level5)      # not put back to level 4
        self.assertTrue(SemesterRegistration.objects.get(profile=self.profile, semester=self.next_sem1)
                        .status == SemesterRegistration.ACTIVE)
        self.assertTrue(Notification.objects.filter(profile=self.profile, kind=Notification.PROVISIONAL_SETTLED).exists())

    def test_only_the_principal_or_dparc_settles(self):
        self.assertEqual(self.exam.post('/api/provisional/settle-passes/', {}, format='json').status_code, 403)
        self.assertEqual(self.exam.get('/api/provisional/').status_code, 200)

    def test_a_failure_takes_the_place_away_and_reverses_its_fees(self):
        self.publish(self.profile, 20)
        progression.refresh_provisional()
        review = SemesterReview.objects.get(profile=self.profile, semester=self.sem2)
        self.assertIn(review.proposed, (SemesterReview.REPEAT, SemesterReview.DISCONTINUED))
        with mock.patch('attendance.finance.reset_fees') as reset:
            progression.confirm_review(review, review.proposed, actor=self.principal_user)
        registration = SemesterRegistration.objects.get(profile=self.profile, semester=self.next_sem1)
        self.assertEqual(registration.status, SemesterRegistration.CANCELLED)
        reset.assert_called_once()
        self.assertEqual(reset.call_args.args[1], self.next_year)
        self.assertEqual(reset.call_args.kwargs['from_semester_number'], 1)

    def test_no_end_of_semester_exam_while_provisional(self):
        enrollment = Student.objects.filter(profile=self.profile, module__semester=self.next_sem1).first()
        self.assertIsNotNone(enrollment)
        result = self.grade(enrollment, 90)
        enrollment.refresh_from_db()
        _total, eligible, note = ca_eligibility_for_student(enrollment)
        self.assertFalse(eligible)
        self.assertIn('Provisional', note)
        self.publish(self.profile, 80)
        progression.settle_passes(actor=self.principal_user)
        self.assertTrue(ca_eligibility_for_student(enrollment)[1])

    def test_the_exam_officer_sets_the_cut_off_and_the_offices_are_warned(self):
        refused = self.principal.post('/api/provisional/cutoff/', {'semester_id': self.next_sem1.id,
                                                                    'cutoff': '2026-11-15'}, format='json')
        self.assertEqual(refused.status_code, 403)
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        made = self.exam.post('/api/provisional/cutoff/', {'semester_id': self.next_sem1.id, 'cutoff': yesterday},
                              format='json')
        self.assertEqual(made.status_code, 200, made.data)
        self.assertEqual(len(progression.provisional_past_cutoff()), 1)
        self.assertEqual(self.principal.get('/api/dashboard/').data['provisional_overdue'], 1)


class FinishingAfterLevelFiveTests(ProvisionalBase):
    def setUp(self):
        super().setUp()
        self.profile = self.clear_student('REG/121', self.level5)
        self.confirm_all(self.sem2)
        self.advance()                                    # handed to admissions for Level 6
        enrollment = Student.objects.filter(profile=self.profile).first()
        enrollment.set_portal_pin('Portal#2026', require_change=False)
        enrollment.save()
        session = self.client.session
        session['student_id'] = enrollment.id
        session.save()

    def ask(self):
        return self.client.post('/api/my-exit/', {'reason': 'Going to work'}, content_type='application/json')

    def test_the_student_asks_and_the_principal_approves(self):
        mine = self.client.get('/api/my-exit/').json()
        self.assertEqual((mine['can_ask'], mine['level']), (True, 'NTA Level 5'))
        self.assertEqual(self.ask().status_code, 201)
        request = ExitRequest.objects.get()
        self.assertTrue(Notification.objects.filter(kind=Notification.EXIT_REQUESTED, user=self.principal_user).exists())
        self.assertEqual(self.dparc.post(f'/api/exit-requests/{request.id}/decide/', {'decision': 'approve'},
                                         format='json').status_code, 403)       # the Principal's alone
        with mock.patch('attendance.finance.reset_fees') as reset:
            approved = self.principal.post(f'/api/exit-requests/{request.id}/decide/', {'decision': 'approve'},
                                           format='json')
        self.assertEqual(approved.status_code, 200, approved.data)
        self.profile.refresh_from_db()
        self.assertEqual((self.profile.standing.status, self.profile.standing.class_level),
                         (StudentStanding.COMPLETED, self.level5))
        self.assertEqual(Application.objects.get(profile=self.profile, semester=self.next_sem1).state,
                         Application.CANCELLED)
        reset.assert_called_once()
        self.assertIn(self.profile.id, [row['standing'].profile_id for row in lifecycle.finishing_students()])
        self.assertTrue(Notification.objects.filter(profile=self.profile, kind=Notification.EXIT_DECIDED).exists())

    def test_declined_needs_a_reason(self):
        self.ask()
        request = ExitRequest.objects.get()
        self.assertEqual(self.principal.post(f'/api/exit-requests/{request.id}/decide/', {'decision': 'decline'},
                                             format='json').status_code, 400)

    def test_not_after_level_four_and_not_while_provisional(self):
        level_four = self.clear_student('REG/122', self.level4, codes=('PST04209',))
        review = progression.build_reviews(self.sem2)
        self.assertIsNone(lifecycle.exit_review(level_four)[0])
        waiting = self.waiting_student('REG/123', self.level5)
        progression.build_reviews(self.sem2)
        review = SemesterReview.objects.get(profile=waiting)
        progression.confirm_review(review, SemesterReview.PROVISIONAL, actor=self.principal_user, reason='x')
        self.assertIn('not out yet', lifecycle.exit_review(waiting)[1])


class DidNotReportTests(ProvisionalBase):
    def setUp(self):
        super().setUp()
        self.profile = self.clear_student('REG/131', self.level4)
        self.confirm_all(self.sem2)
        self.advance()
        self.application = Application.objects.get(profile=self.profile, semester=self.next_sem1)
        self.admission_user, self.admission = office('adm', 'admission_officer')

    def window(self, days_ago):
        AdmissionWindow.objects.update_or_create(semester=self.next_sem1, defaults={
            'opens_on': date.today() - timedelta(days=days_ago), 'closes_on': date.today() + timedelta(days=30)})

    def test_not_before_three_months(self):
        self.window(30)
        listed = self.admission.get('/api/not-reported/', {'semester_id': self.next_sem1.id}).data
        self.assertFalse(listed['rows'][0]['can_decide'])
        refused = self.admission.post(f'/api/not-reported/{self.application.id}/', {'decision': 'not_reported'},
                                      format='json')
        self.assertEqual(refused.status_code, 400)

    def test_still_expected_or_did_not_report(self):
        self.window(100)
        self.assertEqual(self.admission.post(f'/api/not-reported/{self.application.id}/',
                                             {'decision': 'expected', 'note': 'Phoned — coming next week'},
                                             format='json').status_code, 200)
        self.assertIsNotNone(admissions.not_reported(self.next_sem1)[0]['still_expected_on'])
        with mock.patch('attendance.finance.reset_fees') as reset:
            done = self.admission.post(f'/api/not-reported/{self.application.id}/', {'decision': 'not_reported'},
                                       format='json')
        self.assertEqual(done.status_code, 200, done.data)
        self.application.refresh_from_db()
        self.assertEqual(self.application.state, Application.CANCELLED)
        reset.assert_called_once()
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.standing.status, StudentStanding.NOT_REPORTED)
        from .models import AcademicYear, Semester
        later = Semester.objects.create(academic_year=AcademicYear.objects.get(name='2027/2028'), number=1)
        self.assertIn(self.profile.id, [s.profile_id for s in admissions.due_back(later)])

    def test_only_the_admission_office_or_the_principal(self):
        self.window(100)
        self.assertEqual(self.exam.get('/api/not-reported/', {'semester_id': self.next_sem1.id}).status_code, 403)


class LateResultsBelongToTheirOwnSemesterTests(ProvisionalBase):
    """A student who failed semester 2 of 2025/26, whose results arrive after
    2026/27 was opened with them placed provisionally, is a 2025/26 failure —
    never a 2026/27 one (said 18 October 2026)."""

    def setUp(self):
        super().setUp()
        self.profile = self.student('REG/141')
        self.register(self.profile, self.sem2, self.level4)
        self.passed = self.enrol(self.profile, self.module('PST04251', self.level4, self.sem2))
        self.passed_too = self.enrol(self.profile, self.module('PST04253', self.level4, self.sem2))
        self.failed = self.enrol(self.profile, self.module('PST04252', self.level4, self.sem2))
        self.grade(self.passed, 85, approved=False)
        self.grade(self.passed_too, 85, approved=False)
        self.grade(self.failed, 40, approved=False)          # first sitting: a supplementary to come
        self.open_year()
        self.admit_waiting(self.profile, self.next_sem1)
        self.level5_sitting = Student.objects.get(profile=self.profile, module__semester=self.next_sem1)
        self.grade(self.level5_sitting, 70)                   # CA earned while provisional

    def publish_first_sitting(self):
        from .models import StudentResult
        StudentResult.objects.filter(student__in=[self.passed, self.passed_too, self.failed]).update(final_approved=True)

    def test_a_supplementary_still_to_come_keeps_the_place_provisional(self):
        self.publish_first_sitting()
        row = progression.refresh_provisional()[0]
        self.assertEqual((row['proposed'], row['results_in']), (SemesterReview.PENDING, False))
        self.assertEqual(progression.settle_passes(actor=self.principal_user), 0)
        self.assertIn(self.profile.id, progression.provisional_profile_ids())

    def test_passing_the_supplementary_confirms_the_place(self):
        from .models import StudentResult
        self.publish_first_sitting()
        StudentResult.objects.filter(student=self.failed).update(supplementary_mark=60)
        self.assertEqual(progression.settle_passes(actor=self.principal_user), 1)
        self.assertEqual(SemesterRegistration.objects.get(profile=self.profile, semester=self.next_sem1).status,
                         SemesterRegistration.ACTIVE)

    def test_failing_the_supplementary_is_a_2025_26_repeat(self):
        from .models import OutstandingRepeat, StudentResult
        self.publish_first_sitting()
        StudentResult.objects.filter(student=self.failed).update(supplementary_mark=30)
        progression.refresh_provisional()
        review = SemesterReview.objects.get(profile=self.profile, semester=self.sem2)
        self.assertEqual(review.proposed, SemesterReview.REPEAT)
        progression.confirm_review(review, review.proposed, actor=self.principal_user)

        owed = OutstandingRepeat.objects.get(profile=self.profile)
        self.assertEqual((owed.module_code, owed.origin_semester), ('PST04252', self.sem2))        # 2025/26
        self.profile.refresh_from_db()
        standing = self.profile.standing
        self.assertEqual((standing.status, standing.class_level, standing.return_year, standing.return_semester_number),
                         (StudentStanding.REPEATING, self.level4, self.next_year, 2))
        # The 2026/27 place is gone; what was done in it is kept, not counted.
        self.assertEqual(SemesterRegistration.objects.get(profile=self.profile, semester=self.next_sem1).status,
                         SemesterRegistration.CANCELLED)
        self.level5_sitting.refresh_from_db()
        self.assertIsNotNone(self.level5_sitting.withdrawn_at)
        self.assertTrue(StudentResult.objects.filter(student=self.level5_sitting).exists())
        # 2026/27 never reviews them: no 2026/27 result, pass or fail, is invented.
        reviews = progression.build_reviews(self.next_sem1)
        self.assertNotIn(self.profile.id, [r.profile_id for r in reviews])
        # 2025/26's own record still reads the failed sitting, unchanged.
        self.assertEqual(StudentResult.objects.get(student=self.failed).supplementary_mark, 30)
