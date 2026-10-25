"""Students the year end left behind (said 25 October 2026).

PST was three programmes of one level each when 2026/27 opened. The year end
moves a student to the next level of their programme: Level 4 and Level 5
students had none, so they were left waiting or marked finished. Only Level 6
finishes now, and `place_left_behind` places the ones left behind once the
programmes are merged — the year end again, for them alone.
"""

from io import StringIO

from django.core.management import call_command

from . import progression
from .models import (
    Application, ExitRequest, Programme, SemesterRegistration, SemesterReview, StandingChange, StudentStanding,
)
from .test_provisional import ProvisionalBase


class LeftBehindTests(ProvisionalBase):
    def setUp(self):
        super().setUp()
        # As it was: one programme per level.
        self.pst04 = Programme.objects.create(department=self.department, name='PST Level 4', code='PST04')
        self.pst04.levels.set([self.level4])
        self.pst05 = Programme.objects.create(department=self.department, name='PST Level 5', code='PST05')
        self.pst05.levels.set([self.level5])
        self.waiting = self.waiting_student('REG/401', self.level4)          # results not out
        self.passed = self.clear_student('REG/501', self.level5, codes=('PST05201',))
        self.finishing = self.clear_student('REG/601', self.level6, codes=('PST06201',))
        self.exited = self.clear_student('REG/502', self.level5, codes=('PST05202',))
        SemesterRegistration.objects.filter(profile=self.waiting).update(programme=self.pst04)
        SemesterRegistration.objects.filter(profile__in=[self.passed, self.exited]).update(programme=self.pst05)
        progression.build_reviews(self.sem2)
        for profile in (self.passed, self.finishing, self.exited):
            review = SemesterReview.objects.get(profile=profile, semester=self.sem2)
            progression.confirm_review(review, review.proposed, actor=self.officer)
        self.advance()
        # The old year end marked the Level 5 pass finished; the exit route finished the other.
        progression.set_standing(self.passed, StudentStanding.COMPLETED, actor=self.officer, semester=self.sem2,
                                 class_level=self.level5, programme=self.pst05,
                                 reason=progression.FINISHED_BY_THE_ADVANCE)
        progression.set_standing(self.exited, StudentStanding.COMPLETED, actor=self.officer, semester=self.sem2,
                                 class_level=self.level5, programme=self.pst05, reason='Finished after Level 5.')
        ExitRequest.objects.create(profile=self.exited, class_level=self.level5, status=ExitRequest.APPROVED,
                                   review=SemesterReview.objects.get(profile=self.exited, semester=self.sem2))

    def merge(self):
        """What merge_programmes does: everything moves to PST, which runs 4 to 6."""
        for model in (SemesterRegistration, SemesterReview, StudentStanding, Application):
            model.objects.filter(programme__in=[self.pst04, self.pst05]).update(programme=self.pst)

    def place(self, *args):
        out = StringIO()
        call_command('place_left_behind', *args, stdout=out)
        return out.getvalue()

    def activate_next_year(self):
        self.sem2.is_active = False
        self.sem2.save(update_fields=['is_active'])
        self.next_sem1.is_active = True
        self.next_sem1.save(update_fields=['is_active'])

    def test_the_year_end_no_longer_finishes_level_4_or_5(self):
        self.assertFalse(StandingChange.objects.filter(profile=self.waiting, to_status=StudentStanding.COMPLETED).exists())
        self.assertFalse(Application.objects.filter(profile__in=[self.waiting, self.passed]).exists())
        self.assertEqual(self.finishing.standing.status, StudentStanding.COMPLETED)        # Level 6 does finish

    def test_placed_after_the_merge_as_everybody_else_was(self):
        self.merge()
        self.activate_next_year()
        dry = self.place()
        self.assertIn('2 student(s) left behind', dry)
        self.assertIn('REG/401', dry)
        self.assertIn('REG/501', dry)
        self.assertIn('Level 6 — finishes', dry)
        self.assertIn('chose to finish', dry)
        self.assertFalse(Application.objects.filter(semester=self.next_sem1, profile=self.waiting).exists())  # dry

        self.place('--apply')
        waiting_app = Application.objects.get(profile=self.waiting, semester=self.next_sem1)
        passed_app = Application.objects.get(profile=self.passed, semester=self.next_sem1)
        self.assertEqual((waiting_app.class_level, passed_app.class_level), (self.level5, self.level6))
        self.assertIn(self.waiting.id, progression.provisional_profile_ids())      # settled when the results are in
        self.passed.standing.refresh_from_db()
        self.assertEqual((self.passed.standing.status, self.passed.standing.class_level),
                         (StudentStanding.ACTIVE, self.level6))
        self.exited.standing.refresh_from_db()
        self.assertEqual(self.exited.standing.status, StudentStanding.COMPLETED)   # left as they are
        self.assertFalse(Application.objects.filter(profile__in=[self.exited, self.finishing],
                                                    semester=self.next_sem1).exists())
        self.assertIn('0 student(s) left behind', self.place())                     # safe to run twice

    def test_before_the_merge_it_says_why_and_places_nobody(self):
        self.activate_next_year()
        out = self.place('--apply')
        self.assertIn('still has no level after', out)
        self.assertFalse(Application.objects.filter(semester=self.next_sem1,
                                                    profile__in=[self.waiting, self.passed]).exists())
