"""Year-end progression: what the results do to the student.

The college's rules, each one a test:

  * everything passed → the student moves up a level;
  * GPA below 2.0 → discontinued, and expected back for readmission into the
    exact semester they failed;
  * a module failed after its supplementary → they repeat that module only, at
    the same level, until it is passed — level 6 included;
  * a semester 1 module failed that way → no semester 2 until it is passed;
  * a supplementary still unmarked → the semester stays pending and the student
    carries on;
  * level 6, all clear → finished, and the portal closes only once they are
    archived.

Throughout: no result is ever rewritten. A repeat sitting is a new enrollment
with its own result, and the failed one stays exactly as it was.
"""

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from . import finance, progression
from .models import (
    AcademicYear, ChargeType, ClassLevel, Department, FeeStructure, Module,
    OutstandingRepeat, Programme, Semester, SemesterRegistration, SemesterReview,
    Student, StudentCharge, StudentProfile, StudentResult, StudentStanding,
)

User = get_user_model()


class ProgressionBase(TestCase):
    def setUp(self):
        self.year = AcademicYear.objects.create(name='2025/2026', is_active=True)
        self.sem1 = Semester.objects.create(academic_year=self.year, number=1)
        self.sem2 = Semester.objects.create(academic_year=self.year, number=2, is_active=True)
        self.level4 = ClassLevel.objects.create(name='NTA Level 4', order=4)
        self.level5 = ClassLevel.objects.create(name='NTA Level 5', order=5)
        self.level6 = ClassLevel.objects.create(name='NTA Level 6', order=6)

        self.department = Department.objects.create(name='Pharmaceutical Sciences', code='PST')
        self.pst = Programme.objects.create(
            department=self.department, name='Pharmaceutical Sciences', code='PST')
        self.pst.levels.set([self.level4, self.level5, self.level6])

        self.officer = User.objects.create_user('records', password='pw')

    # ── fixtures ──────────────────────────────────────────────────────────────

    def module(self, code, level, semester, *, credits=1):
        return Module.objects.create(
            name=code, code=code, teacher='T', class_level=level,
            semester=semester, programme=self.pst, credits=credits)

    def student(self, reg_no, name='Asha Juma'):
        return StudentProfile.objects.create(nactvet_reg_no=reg_no, name=name)

    def register(self, profile, semester, level, kind=SemesterRegistration.CONTINUING):
        return SemesterRegistration.objects.create(
            profile=profile, semester=semester, programme=self.pst,
            class_level=level, kind=kind)

    def enrol(self, profile, module, **kwargs):
        return Student.objects.create(
            nactvet_reg_no=profile.nactvet_reg_no, name=profile.name,
            profile=profile, module=module, **kwargs)

    def grade(self, enrollment, mark=None, *, ca=None, end=None, supp=None, approved=True):
        """One result, published.

        Theory-only weighting: the four CA marks carry 40 between them and the
        end examination carries 60, so `mark` used alone is the final total.
        `ca` and `end` are given separately where the test needs a total that
        the end examination alone would not produce — a pass mark in the paper
        with weak coursework, which is what a D is.
        """
        ca = mark if ca is None else ca
        end = mark if end is None else end
        return StudentResult.objects.create(
            student=enrollment, assign1=ca, assign2=ca,
            cat1_theory=ca, cat2_theory=ca, end_theory=end,
            supplementary_mark=supp, final_approved=approved, ca_approved=approved)

    def repeat_fee(self, year, level, amount='30000'):
        charge_type = ChargeType.objects.create(
            name='Repeat Module Fee', family=ChargeType.FEE,
            declaration=ChargeType.REPEAT_MODULE)
        structure = FeeStructure.objects.create(
            charge_type=charge_type, programme=self.pst, class_level=level,
            academic_year=year, amount=Decimal(amount),
            billing_period=FeeStructure.ONCE, installments=1)
        finance.set_installment_schedule(structure, [date(2026, 11, 30)])
        return charge_type


# ── reading the results ───────────────────────────────────────────────────────

class EvaluateTests(ProgressionBase):
    def test_passing_everything_is_clear(self):
        profile = self.student('REG/001')
        for code in ('PST04201', 'PST04202'):
            self.grade(self.enrol(profile, self.module(code, self.level4, self.sem2)), 80)

        outcome = progression.evaluate(profile, self.sem2)

        self.assertEqual(outcome['proposed'], SemesterReview.CLEAR)
        self.assertEqual(outcome['gpa'], 4.0)

    def test_a_gpa_below_two_discontinues(self):
        profile = self.student('REG/002')
        # A pass in the paper with weak coursework is a D — one point. A module
        # failed after its supplementary is zero.
        self.grade(self.enrol(profile, self.module('PST04201', self.level4, self.sem2)), ca=25, end=50)
        self.grade(self.enrol(profile, self.module('PST04202', self.level4, self.sem2)), 40, supp=30)

        outcome = progression.evaluate(profile, self.sem2)

        self.assertEqual(outcome['proposed'], SemesterReview.DISCONTINUED)
        self.assertLess(outcome['gpa'], progression.PASS_GPA)

    def test_failing_a_supplementary_with_a_sound_gpa_is_a_repeat(self):
        profile = self.student('REG/003')
        self.grade(self.enrol(profile, self.module('PST04201', self.level4, self.sem2)), 85)
        self.grade(self.enrol(profile, self.module('PST04202', self.level4, self.sem2)), 85)
        self.grade(self.enrol(profile, self.module('PST04203', self.level4, self.sem2)), 40, supp=30)

        outcome = progression.evaluate(profile, self.sem2)

        self.assertEqual(outcome['proposed'], SemesterReview.REPEAT)
        self.assertEqual(outcome['failed'], ['PST04203'])
        self.assertGreaterEqual(outcome['gpa'], progression.PASS_GPA)

    def test_an_unmarked_supplementary_leaves_the_semester_pending(self):
        # Semester 1 supplementaries are sat and marked weeks into semester 2.
        # Until the mark arrives the student is neither passed nor failed.
        profile = self.student('REG/004')
        self.grade(self.enrol(profile, self.module('PST04101', self.level4, self.sem1)), 85)
        self.grade(self.enrol(profile, self.module('PST04102', self.level4, self.sem1)), 40)

        outcome = progression.evaluate(profile, self.sem1)

        self.assertEqual(outcome['proposed'], SemesterReview.PENDING)
        self.assertEqual(outcome['pending'], ['PST04102'])

    def test_marks_nobody_has_approved_are_not_results(self):
        profile = self.student('REG/005')
        self.grade(self.enrol(profile, self.module('PST04201', self.level4, self.sem2)), 85,
                   approved=False)

        outcome = progression.evaluate(profile, self.sem2)

        self.assertEqual(outcome['proposed'], SemesterReview.PENDING)
        self.assertIsNone(outcome['gpa'])

    def test_a_clear_level_six_semester_two_is_finished(self):
        profile = self.student('REG/006')
        registration = self.register(profile, self.sem2, self.level6)
        self.grade(self.enrol(profile, self.module('PST06201', self.level6, self.sem2)), 80)

        outcome = progression.evaluate(profile, self.sem2, registration=registration)

        self.assertEqual(outcome['proposed'], SemesterReview.COMPLETED)

    def test_level_six_is_not_finished_while_a_module_is_still_unpassed(self):
        profile = self.student('REG/007')
        registration = self.register(profile, self.sem2, self.level6)
        # Failed in semester 1 and still open; this semester went perfectly.
        failed = self.enrol(profile, self.module('PST06101', self.level6, self.sem1))
        OutstandingRepeat.objects.create(
            profile=profile, module_code='PST06101', class_level=self.level6,
            semester_number=1, origin_semester=self.sem1, origin_enrollment=failed)
        self.grade(self.enrol(profile, self.module('PST06201', self.level6, self.sem2)), 80)

        outcome = progression.evaluate(profile, self.sem2, registration=registration)

        self.assertEqual(outcome['proposed'], SemesterReview.REPEAT)
        self.assertIn('PST06101', outcome['reason'])

    def test_credits_weight_the_gpa(self):
        profile = self.student('REG/008')
        self.grade(self.enrol(profile, self.module('PST04201', self.level4, self.sem2, credits=3)), 85)
        self.grade(self.enrol(profile, self.module('PST04202', self.level4, self.sem2, credits=1)),
                   ca=25, end=50)

        outcome = progression.evaluate(profile, self.sem2)

        self.assertEqual(outcome['gpa'], round((4 * 3 + 1 * 1) / 4, 2))


# ── confirming, and what confirming does ──────────────────────────────────────

class ConfirmTests(ProgressionBase):
    def test_confirming_a_repeat_holds_the_student_to_the_module(self):
        profile = self.student('REG/010')
        self.register(profile, self.sem2, self.level4)
        self.grade(self.enrol(profile, self.module('PST04201', self.level4, self.sem2)), 85)
        self.grade(self.enrol(profile, self.module('PST04202', self.level4, self.sem2)), 40, supp=30)
        review = progression.build_reviews(self.sem2)[0]

        progression.confirm_review(review, review.proposed, actor=self.officer)

        repeat = OutstandingRepeat.objects.get(profile=profile)
        self.assertEqual(repeat.module_code, 'PST04202')
        self.assertEqual(repeat.status, OutstandingRepeat.OPEN)
        self.assertEqual(repeat.origin_semester, self.sem2)
        self.assertEqual(profile.standing.status, StudentStanding.REPEATING)
        self.assertEqual(profile.standing.return_semester_number, 2)

    def test_failing_semester_one_takes_the_student_off_semester_two(self):
        profile = self.student('REG/011')
        self.register(profile, self.sem1, self.level4)
        semester_two = self.register(profile, self.sem2, self.level4)
        self.grade(self.enrol(profile, self.module('PST04101', self.level4, self.sem1)), 85)
        self.grade(self.enrol(profile, self.module('PST04102', self.level4, self.sem1)), 40, supp=30)
        review = progression.build_reviews(self.sem1)[0]

        progression.confirm_review(review, review.proposed, actor=self.officer)

        semester_two.refresh_from_db()
        self.assertEqual(semester_two.status, SemesterRegistration.CANCELLED)
        self.assertIn('no further semester', semester_two.cancelled_reason.lower())

    def test_a_discontinued_student_is_expected_back_at_the_semester_they_failed(self):
        profile = self.student('REG/012')
        self.register(profile, self.sem2, self.level4)
        self.grade(self.enrol(profile, self.module('PST04201', self.level4, self.sem2)), ca=25, end=50)
        self.grade(self.enrol(profile, self.module('PST04202', self.level4, self.sem2)), 40, supp=30)
        next_year = AcademicYear.objects.create(name='2026/2027')
        review = progression.build_reviews(self.sem2)[0]

        progression.confirm_review(review, review.proposed, actor=self.officer)

        standing = profile.standing
        self.assertEqual(standing.status, StudentStanding.DISCONTINUED)
        self.assertEqual(standing.return_semester_number, 2)
        self.assertEqual(standing.return_year, next_year)
        self.assertEqual(standing.class_level, self.level4)

    def test_pending_is_not_a_decision(self):
        profile = self.student('REG/013')
        self.register(profile, self.sem1, self.level4)
        self.grade(self.enrol(profile, self.module('PST04101', self.level4, self.sem1)), 40)
        review = progression.build_reviews(self.sem1)[0]

        with self.assertRaises(progression.ProgressionError):
            progression.confirm_review(review, SemesterReview.PENDING, actor=self.officer)

    def test_a_confirmed_review_is_not_recalculated_when_results_change(self):
        profile = self.student('REG/014')
        self.register(profile, self.sem2, self.level4)
        enrollment = self.enrol(profile, self.module('PST04201', self.level4, self.sem2))
        self.grade(enrollment, 85)
        review = progression.build_reviews(self.sem2)[0]
        progression.confirm_review(review, SemesterReview.PROVISIONAL, actor=self.officer,
                                   reason='Awaiting a transfer record.')

        progression.build_reviews(self.sem2)

        review.refresh_from_db()
        self.assertEqual(review.confirmed, SemesterReview.PROVISIONAL)
        self.assertEqual(review.outcome, SemesterReview.PROVISIONAL)

    def test_every_move_of_a_standing_is_written_down(self):
        profile = self.student('REG/015')
        self.register(profile, self.sem2, self.level4)
        self.grade(self.enrol(profile, self.module('PST04201', self.level4, self.sem2)), ca=25, end=50)
        self.grade(self.enrol(profile, self.module('PST04202', self.level4, self.sem2)), 40, supp=30)
        review = progression.build_reviews(self.sem2)[0]

        progression.confirm_review(review, review.proposed, actor=self.officer,
                                   reason='Senate decision 14/2026')

        change = profile.standing_changes.get()
        self.assertEqual(change.to_status, StudentStanding.DISCONTINUED)
        self.assertEqual(change.changed_by, self.officer)
        self.assertEqual(change.semester, self.sem2)


# ── carrying the modules into the new year ────────────────────────────────────

class CarryModulesTests(ProgressionBase):
    def setUp(self):
        super().setUp()
        self.next_year = AcademicYear.objects.create(name='2026/2027')
        self.next_sem1 = Semester.objects.create(academic_year=self.next_year, number=1)

    def test_the_new_year_is_taught_from_last_year_s_list(self):
        self.module('PST04101', self.level4, self.sem1, credits=3)
        self.module('PST05101', self.level5, self.sem1)

        carried = progression.carry_modules(self.next_sem1)

        self.assertEqual(sorted(carried['created']), ['PST04101', 'PST05101'])
        copy = Module.objects.get(semester=self.next_sem1, code='PST04101')
        self.assertEqual(copy.credits, 3)
        self.assertEqual(copy.class_level, self.level4)
        self.assertEqual(copy.programme, self.pst)

    def test_carrying_twice_creates_nothing_the_second_time(self):
        self.module('PST04101', self.level4, self.sem1)
        progression.carry_modules(self.next_sem1)

        again = progression.carry_modules(self.next_sem1)

        self.assertEqual(again['created'], [])
        self.assertEqual(Module.objects.filter(semester=self.next_sem1).count(), 1)

    def test_carrying_again_would_restore_a_module_that_was_deleted(self):
        # Worth knowing rather than hiding: the carry copies whatever last year
        # taught, so a module the examination officer deleted from the new year
        # comes back if the carry is run again. It is run once, by the advance,
        # and the officer's edits come after it — which is why carrying is not a
        # button anybody can press twice.
        self.module('PST04101', self.level4, self.sem1)
        self.module('PST04102', self.level4, self.sem1)
        progression.carry_modules(self.next_sem1)
        Module.objects.filter(semester=self.next_sem1, code='PST04102').delete()

        again = progression.carry_modules(self.next_sem1)

        self.assertEqual(again['created'], ['PST04102'])

    def test_the_source_year_is_never_touched(self):
        original = self.module('PST04101', self.level4, self.sem1)
        progression.carry_modules(self.next_sem1)

        original.refresh_from_db()
        self.assertEqual(original.semester, self.sem1)
        self.assertEqual(Module.objects.filter(semester=self.sem1).count(), 1)


# ── the advance ───────────────────────────────────────────────────────────────

class AdvanceTests(ProgressionBase):
    def setUp(self):
        super().setUp()
        self.next_year = AcademicYear.objects.create(name='2026/2027')
        self.next_sem1 = Semester.objects.create(academic_year=self.next_year, number=1)
        self.next_sem2 = Semester.objects.create(academic_year=self.next_year, number=2)
        # The modules this year was taught from, which next year inherits.
        self.module('PST04101', self.level4, self.sem1)
        self.module('PST04102', self.level4, self.sem1)
        self.module('PST05101', self.level5, self.sem1)
        self.module('PST06101', self.level6, self.sem1)

    def clear_student(self, reg_no, level, codes=('PST04201',)):
        profile = self.student(reg_no)
        self.register(profile, self.sem2, level)
        for code in codes:
            self.grade(self.enrol(profile, self.module(code, level, self.sem2)), 80)
        return profile

    def advance(self):
        progression.carry_modules(self.next_sem1)
        return progression.apply_advance(self.sem2, self.next_sem1, actor=self.officer)

    def confirm_all(self, semester):
        for review in progression.build_reviews(semester):
            progression.confirm_review(review, review.proposed, actor=self.officer)

    def test_a_clear_student_moves_up_a_level_and_is_enrolled(self):
        profile = self.clear_student('REG/020', self.level4)
        self.confirm_all(self.sem2)

        summary = self.advance()

        registration = SemesterRegistration.objects.get(profile=profile, semester=self.next_sem1)
        self.assertEqual(registration.class_level, self.level5)
        self.assertEqual(registration.kind, SemesterRegistration.CONTINUING)
        self.assertEqual(summary['promoted'], 1)
        enrolled = Student.objects.filter(profile=profile, module__semester=self.next_sem1)
        self.assertEqual([row.module.code for row in enrolled], ['PST05101'])
        self.assertEqual(profile.standing.status, StudentStanding.ACTIVE)

    def test_a_repeating_student_stays_at_their_level_and_sits_only_what_they_failed(self):
        profile = self.student('REG/021')
        self.register(profile, self.sem1, self.level4)
        # Passed one semester 1 module, failed the other after the supplementary.
        self.grade(self.enrol(profile, Module.objects.get(semester=self.sem1, code='PST04101')), 85)
        self.grade(self.enrol(profile, Module.objects.get(semester=self.sem1, code='PST04102')), 40, supp=30)
        self.confirm_all(self.sem1)
        # And cleared semester 2 — which does not release them from the repeat.
        self.register(profile, self.sem2, self.level4)
        self.grade(self.enrol(profile, self.module('PST04201', self.level4, self.sem2)), 80)
        self.confirm_all(self.sem2)
        self.repeat_fee(self.next_year, self.level4)

        summary = self.advance()

        registration = SemesterRegistration.objects.get(profile=profile, semester=self.next_sem1)
        self.assertEqual(registration.kind, SemesterRegistration.REPEATING)
        self.assertEqual(registration.class_level, self.level4)
        self.assertEqual(summary['repeating'], 1)
        sittings = list(Student.objects.filter(profile=profile, module__semester=self.next_sem1))
        self.assertEqual([row.module.code for row in sittings], ['PST04102'])
        self.assertEqual(sittings[0].attempt, Student.REPEAT)
        self.assertIsNotNone(sittings[0].repeat_of)
        # Billed at the accountant's per-module repeat rate, not a fresh year of fees.
        charge = StudentCharge.objects.get(profile=profile, module=sittings[0].module)
        self.assertEqual(charge.amount, Decimal('30000.00'))

    def test_a_discontinued_student_is_not_registered_and_keeps_their_record(self):
        profile = self.student('REG/022')
        self.register(profile, self.sem2, self.level4)
        failed = self.enrol(profile, self.module('PST04201', self.level4, self.sem2))
        result = self.grade(failed, 40, supp=30)
        self.confirm_all(self.sem2)

        summary = self.advance()

        self.assertEqual(summary['discontinued'], 1)
        self.assertFalse(SemesterRegistration.objects.filter(
            profile=profile, semester=self.next_sem1).exists())
        self.assertEqual(profile.standing.status, StudentStanding.DISCONTINUED)
        self.assertEqual(profile.standing.return_semester_number, 2)
        # Their enrollment and marks are exactly where they were.
        result.refresh_from_db()
        self.assertEqual(result.supplementary_mark, 30)
        self.assertTrue(Student.objects.filter(pk=failed.pk).exists())

    def test_a_finished_level_six_student_is_not_registered_again(self):
        profile = self.clear_student('REG/023', self.level6, codes=('PST06201',))
        self.confirm_all(self.sem2)

        summary = self.advance()

        self.assertEqual(summary['completed'], 1)
        self.assertFalse(SemesterRegistration.objects.filter(
            profile=profile, semester=self.next_sem1).exists())
        self.assertEqual(profile.standing.status, StudentStanding.COMPLETED)

    def test_a_student_repeating_a_semester_two_module_waits_for_semester_two(self):
        profile = self.student('REG/024')
        self.register(profile, self.sem2, self.level4)
        self.grade(self.enrol(profile, self.module('PST04201', self.level4, self.sem2)), 85)
        self.grade(self.enrol(profile, self.module('PST04202', self.level4, self.sem2)), 40, supp=30)
        self.confirm_all(self.sem2)

        summary = self.advance()

        self.assertEqual(summary['repeating'], 1)
        self.assertFalse(SemesterRegistration.objects.filter(
            profile=profile, semester=self.next_sem1).exists())
        standing = profile.standing
        self.assertEqual(standing.status, StudentStanding.REPEATING)
        self.assertEqual(standing.return_semester_number, 2)
        self.assertEqual(standing.class_level, self.level4)

    def test_the_advance_refuses_to_run_while_a_student_is_unreviewed(self):
        self.clear_student('REG/025', self.level4)

        plan = progression.plan_advance(self.sem2)

        self.assertEqual(len(plan['blocked']), 1)
        with self.assertRaises(progression.ProgressionError):
            progression.apply_advance(self.sem2, self.next_sem1, actor=self.officer)

    def test_the_plan_says_what_would_happen_before_anybody_presses_it(self):
        self.clear_student('REG/026', self.level4)
        self.confirm_all(self.sem2)

        plan = progression.plan_advance(self.sem2)

        move = plan['moves'][0]
        self.assertEqual(plan['blocked'], [])
        self.assertEqual(move['action'], 'promoted')
        self.assertEqual(move['to_level'], 'NTA Level 5')

    def test_advancing_twice_does_not_enroll_anybody_twice(self):
        profile = self.clear_student('REG/027', self.level4)
        self.confirm_all(self.sem2)
        self.advance()

        self.advance()

        self.assertEqual(Student.objects.filter(
            profile=profile, module__semester=self.next_sem1).count(), 1)
        self.assertEqual(SemesterRegistration.objects.filter(
            profile=profile, semester=self.next_sem1).count(), 1)

    def test_a_module_added_after_registration_enrolls_the_class(self):
        profile = self.clear_student('REG/028', self.level4)
        self.confirm_all(self.sem2)
        self.advance()

        late = self.module('PST05102', self.level5, self.next_sem1)
        progression.enroll_module(late)

        self.assertTrue(Student.objects.filter(profile=profile, module=late).exists())


# ── the repeat sitting itself ─────────────────────────────────────────────────

class RepeatSittingTests(ProgressionBase):
    def test_passing_the_repeat_closes_it_and_leaves_the_old_result_alone(self):
        profile = self.student('REG/030')
        self.register(profile, self.sem1, self.level4)
        failed = self.enrol(profile, self.module('PST04101', self.level4, self.sem1))
        old = self.grade(failed, 40, supp=30)
        self.register(profile, self.sem2, self.level4)
        progression.confirm_review(
            progression.build_reviews(self.sem1)[0], SemesterReview.REPEAT, actor=self.officer)

        # The repeat sitting: a new enrollment in the module's new offering.
        next_year = AcademicYear.objects.create(name='2026/2027')
        next_sem1 = Semester.objects.create(academic_year=next_year, number=1)
        again = self.module('PST04101', self.level4, next_sem1)
        sitting = self.enrol(profile, again, attempt=Student.REPEAT, repeat_of=failed)
        self.grade(sitting, 60)
        self.register(profile, next_sem1, self.level4, kind=SemesterRegistration.REPEATING)

        review = [r for r in progression.build_reviews(next_sem1) if r.profile_id == profile.id][0]
        progression.confirm_review(review, review.proposed, actor=self.officer)

        repeat = OutstandingRepeat.objects.get(profile=profile)
        self.assertEqual(repeat.status, OutstandingRepeat.PASSED)
        self.assertEqual(repeat.attempt_enrollment, sitting)
        # The failed sitting is untouched — it is the history of what happened.
        old.refresh_from_db()
        self.assertEqual(old.end_theory, 40)
        self.assertEqual(old.supplementary_mark, 30)
        self.assertEqual(old.student, failed)

    def test_failing_the_repeat_again_keeps_it_open(self):
        profile = self.student('REG/031')
        self.register(profile, self.sem1, self.level4)
        failed = self.enrol(profile, self.module('PST04101', self.level4, self.sem1))
        self.grade(failed, 40, supp=30)
        progression.confirm_review(
            progression.build_reviews(self.sem1)[0], SemesterReview.REPEAT, actor=self.officer)

        next_year = AcademicYear.objects.create(name='2026/2027')
        next_sem1 = Semester.objects.create(academic_year=next_year, number=1)
        sitting = self.enrol(profile, self.module('PST04101', self.level4, next_sem1),
                             attempt=Student.REPEAT, repeat_of=failed)
        self.grade(sitting, 40, supp=35)
        self.register(profile, next_sem1, self.level4, kind=SemesterRegistration.REPEATING)

        review = [r for r in progression.build_reviews(next_sem1) if r.profile_id == profile.id][0]
        progression.confirm_review(review, review.proposed, actor=self.officer)

        repeat = OutstandingRepeat.objects.get(profile=profile)
        self.assertEqual(repeat.status, OutstandingRepeat.OPEN)
        self.assertEqual(repeat.attempt_enrollment, sitting)


# ── the API around it ─────────────────────────────────────────────────────────

class ProgressionApiTests(ProgressionBase):
    def setUp(self):
        super().setUp()
        from rest_framework.test import APIClient
        from .views import set_roles

        self.next_year = AcademicYear.objects.create(name='2026/2027')
        self.admin = User.objects.create_superuser('admin', 'a@b.c', 'pw')
        self.api = APIClient()
        self.api.force_authenticate(self.admin)

        self.hod_user = User.objects.create_user('hod', password='pw')
        set_roles(self.hod_user, ['hod'], full_name='Head of Department')
        self.hod = APIClient()
        self.hod.force_authenticate(self.hod_user)

        self.records_user = User.objects.create_user('officer', password='pw')
        set_roles(self.records_user, ['records_officer'], full_name='Records Officer')
        self.records = APIClient()
        self.records.force_authenticate(self.records_user)

        self.profile = self.student('REG/100')
        self.register(self.profile, self.sem2, self.level4)
        self.grade(self.enrol(self.profile, self.module('PST04201', self.level4, self.sem2)), 80)
        self.module('PST05101', self.level5, self.sem1)

    def test_the_head_of_department_can_no_longer_advance_the_semester(self):
        response = self.hod.post('/api/academic-years/advance/', {}, format='json')

        self.assertEqual(response.status_code, 403)
        self.sem2.refresh_from_db()
        self.assertTrue(self.sem2.is_active)

    def test_the_advance_is_refused_while_a_student_is_unreviewed(self):
        response = self.api.post('/api/academic-years/advance/', {}, format='json')

        self.assertEqual(response.status_code, 400)
        self.assertEqual(len(response.data['blocked']), 1)
        self.sem2.refresh_from_db()
        self.assertTrue(self.sem2.is_active)

    def test_the_preview_shows_the_moves_and_the_modules_before_anybody_commits(self):
        self.records.post('/api/semester-reviews/build/', {}, format='json')
        self.records.post('/api/semester-reviews/confirm-proposed/', {}, format='json')

        response = self.api.get('/api/academic-years/advance-preview/')

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data['can_advance'])
        self.assertEqual(response.data['moves'][0]['action'], 'promoted')
        self.assertIn('PST05101', response.data['modules']['to_create'])
        # A preview creates nothing.
        self.assertFalse(AcademicYear.objects.filter(name='2027/2028').exists())
        self.assertEqual(Module.objects.filter(semester__academic_year=self.next_year).count(), 0)

    def test_the_review_screen_builds_confirms_and_then_the_advance_runs(self):
        built = self.records.post('/api/semester-reviews/build/', {}, format='json')
        self.assertEqual(built.status_code, 200, built.data)
        self.assertEqual(built.data['unconfirmed'], 1)

        confirmed = self.records.post('/api/semester-reviews/confirm-proposed/', {}, format='json')
        self.assertEqual(confirmed.data['confirmed'], 1)

        advanced = self.api.post('/api/academic-years/advance/', {}, format='json')
        self.assertEqual(advanced.status_code, 200, advanced.data)
        self.assertEqual(advanced.data['year'], '2026/2027')
        self.assertEqual(advanced.data['students']['promoted'], 1)
        self.assertEqual(advanced.data['modules_carried'], 1)
        self.assertTrue(SemesterRegistration.objects.filter(
            profile=self.profile, semester__academic_year=self.next_year).exists())

    def test_a_pending_student_is_left_for_the_officer_rather_than_confirmed_in_bulk(self):
        waiting = self.student('REG/101')
        self.register(waiting, self.sem2, self.level4)
        self.grade(self.enrol(waiting, self.module('PST04202', self.level4, self.sem2)), 40)
        self.records.post('/api/semester-reviews/build/', {}, format='json')

        response = self.records.post('/api/semester-reviews/confirm-proposed/', {}, format='json')

        self.assertEqual(response.data['left_for_you'], 1)
        review = SemesterReview.objects.get(profile=waiting, semester=self.sem2)
        self.assertFalse(review.is_confirmed)

    def test_overruling_the_results_needs_a_reason(self):
        self.records.post('/api/semester-reviews/build/', {}, format='json')
        review = SemesterReview.objects.get(profile=self.profile, semester=self.sem2)

        refused = self.records.post(f'/api/semester-reviews/{review.id}/confirm/',
                                    {'outcome': SemesterReview.DISCONTINUED}, format='json')
        self.assertEqual(refused.status_code, 400)

        allowed = self.records.post(f'/api/semester-reviews/{review.id}/confirm/',
                                    {'outcome': SemesterReview.DISCONTINUED,
                                     'reason': 'Withdrew in writing on 4 July.'}, format='json')
        self.assertEqual(allowed.status_code, 200, allowed.data)
        review.refresh_from_db()
        self.assertEqual(review.confirmed, SemesterReview.DISCONTINUED)
        self.assertEqual(review.proposed, SemesterReview.CLEAR)  # what the results said, kept

    def test_the_head_of_department_cannot_confirm_a_review(self):
        self.records.post('/api/semester-reviews/build/', {}, format='json')
        review = SemesterReview.objects.get(profile=self.profile, semester=self.sem2)

        response = self.hod.post(f'/api/semester-reviews/{review.id}/confirm/', {}, format='json')

        self.assertEqual(response.status_code, 403)

    def test_a_module_with_marks_in_it_cannot_be_deleted(self):
        module = Module.objects.get(code='PST04201')

        response = self.api.delete(f'/api/modules/{module.id}/')

        self.assertEqual(response.status_code, 400)
        self.assertIn('results', response.data['detail'].lower())
        self.assertTrue(Module.objects.filter(pk=module.pk).exists())
        self.assertTrue(StudentResult.objects.filter(student__module=module).exists())


class PortalAccessTests(ProgressionBase):
    def setUp(self):
        super().setUp()
        self.profile = self.student('REG/200')
        self.enrollment = self.enrol(self.profile, self.module('PST04201', self.level4, self.sem2))
        self.enrollment.set_portal_pin('Portal#2026', require_change=False)
        self.enrollment.save()

    def sign_in(self):
        return self.client.post('/login/', {'identifier': 'REG/200', 'secret': 'Portal#2026'})

    def test_a_studying_student_signs_in(self):
        response = self.sign_in()

        self.assertEqual(response.status_code, 302)
        self.assertIn('student_id', self.client.session)

    def test_a_discontinued_student_keeps_the_portal(self):
        # They still need their results, their balance and the services they use
        # to ask for readmission.
        progression.set_standing(self.profile, StudentStanding.DISCONTINUED, actor=self.officer)

        response = self.sign_in()

        self.assertEqual(response.status_code, 302)
        self.assertIn('student_id', self.client.session)

    def test_an_archived_student_is_refused(self):
        progression.set_standing(self.profile, StudentStanding.ARCHIVED, actor=self.officer)

        response = self.sign_in()

        self.assertEqual(response.status_code, 200)
        self.assertNotIn('student_id', self.client.session)
        self.assertIn('complete', response.context['error'].lower())

    def test_archiving_a_signed_in_student_ends_their_session(self):
        self.sign_in()

        progression.set_standing(self.profile, StudentStanding.ARCHIVED, actor=self.officer)
        response = self.client.get('/student-dashboard/')

        self.assertEqual(response.status_code, 302)
        self.assertNotIn('student_id', self.client.session)


# ── the supplementary that arrives after the year has turned ─────────────────

class LateSupplementaryTests(ProgressionBase):
    """Semester 1 supplementaries are marked well into semester 2, and semester
    2's after the new year has started. The decision has to be able to arrive
    late without rewriting a single mark."""

    def setUp(self):
        super().setUp()
        self.next_year = AcademicYear.objects.create(name='2026/2027')
        self.next_sem1 = Semester.objects.create(academic_year=self.next_year, number=1)
        self.next_sem2 = Semester.objects.create(academic_year=self.next_year, number=2)
        self.module('PST05101', self.level5, self.next_sem1)
        self.module('PST05201', self.level5, self.next_sem2)

    def test_a_student_promoted_before_the_marks_came_is_taken_off_the_new_level(self):
        profile = self.student('REG/040')
        self.register(profile, self.sem2, self.level4)
        passed = self.enrol(profile, self.module('PST04201', self.level4, self.sem2))
        self.grade(passed, 85)
        waiting = self.enrol(profile, self.module('PST04202', self.level4, self.sem2))
        pending = self.grade(waiting, 40)                      # supplementary not marked
        review = progression.build_reviews(self.sem2)[0]
        self.assertEqual(review.proposed, SemesterReview.PENDING)
        # Confirmed provisionally so the year could turn, and promoted.
        progression.confirm_review(review, SemesterReview.PROVISIONAL, actor=self.officer,
                                   reason='Supplementary sat, marks not back.')
        progression.apply_advance(self.sem2, self.next_sem1, actor=self.officer)
        promoted = SemesterRegistration.objects.get(profile=profile, semester=self.next_sem1)
        self.assertEqual(promoted.class_level, self.level5)

        # The mark arrives: failed. They do not move up until it is passed.
        pending.supplementary_mark = 30
        pending.save(update_fields=['supplementary_mark'])
        progression.build_reviews(self.sem2)
        review.refresh_from_db()
        progression.confirm_review(review, SemesterReview.REPEAT, actor=self.officer,
                                   reason='Failed the supplementary.')

        promoted.refresh_from_db()
        self.assertEqual(promoted.status, SemesterRegistration.CANCELLED)
        self.assertEqual(profile.standing.status, StudentStanding.REPEATING)
        self.assertEqual(profile.standing.class_level, self.level4)
        self.assertEqual(OutstandingRepeat.objects.get(profile=profile).module_code, 'PST04202')
        # Nothing they had done was deleted.
        self.assertTrue(Student.objects.filter(pk=passed.pk).exists())
        self.assertTrue(Student.objects.filter(profile=profile, module__semester=self.next_sem1).exists())

    def test_a_confirmed_provisional_student_is_re_read_when_the_marks_arrive(self):
        profile = self.student('REG/041')
        self.register(profile, self.sem2, self.level4)
        waiting = self.enrol(profile, self.module('PST04202', self.level4, self.sem2))
        pending = self.grade(waiting, 40)
        review = progression.build_reviews(self.sem2)[0]
        progression.confirm_review(review, SemesterReview.PROVISIONAL, actor=self.officer,
                                   reason='Marks not back.')

        pending.supplementary_mark = 70          # passed after all
        pending.save(update_fields=['supplementary_mark'])
        progression.build_reviews(self.sem2)

        review.refresh_from_db()
        # The proposal is not touched while a decision stands — the officer is
        # shown the standing decision and changes it themselves.
        self.assertEqual(review.confirmed, SemesterReview.PROVISIONAL)
        fresh = progression.evaluate(profile, self.sem2)
        self.assertEqual(fresh['proposed'], SemesterReview.CLEAR)

    def test_an_outstanding_repeat_is_sat_when_its_semester_comes_round_again(self):
        # A student carrying one semester 2 module from last year, studying the
        # rest of their new level as normal.
        profile = self.student('REG/042')
        failed = self.enrol(profile, self.module('PST04202', self.level4, self.sem2))
        OutstandingRepeat.objects.create(
            profile=profile, module_code='PST04202', module_name='PST04202',
            class_level=self.level4, semester_number=2,
            origin_semester=self.sem2, origin_enrollment=failed)
        self.module('PST04202', self.level4, self.next_sem2)
        self.repeat_fee(self.next_year, self.level4)
        registration = self.register(profile, self.next_sem2, self.level5)

        progression.enroll_registration(registration, actor=self.officer)

        sat = {row.module.code: row for row in Student.objects.filter(
            profile=profile, module__semester=self.next_sem2)}
        self.assertEqual(sorted(sat), ['PST04202', 'PST05201'])
        self.assertEqual(sat['PST04202'].attempt, Student.REPEAT)
        self.assertEqual(sat['PST05201'].attempt, Student.NORMAL)

    def test_the_officer_is_told_when_a_decision_takes_a_student_off_a_semester(self):
        from rest_framework.test import APIClient
        from .views import set_roles

        officer = User.objects.create_user('records2', password='pw')
        set_roles(officer, ['records_officer'], full_name='Records Officer')
        client = APIClient()
        client.force_authenticate(officer)

        profile = self.student('REG/043')
        self.register(profile, self.sem2, self.level4)
        failed = self.enrol(profile, self.module('PST04202', self.level4, self.sem2))
        self.grade(failed, 40, supp=30)
        self.register(profile, self.next_sem1, self.level5)
        # The records officer must be able to read the screen they work from,
        # and they are not an admin account.
        listed = client.get(f'/api/semester-reviews/?semester_id={self.sem2.id}')
        self.assertEqual(listed.status_code, 200)

        client.post('/api/semester-reviews/build/', {'semester_id': self.sem2.id}, format='json')
        review = SemesterReview.objects.get(profile=profile, semester=self.sem2)
        response = client.post(f'/api/semester-reviews/{review.id}/confirm/', {}, format='json')

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['deferred'], [self.next_sem1.label])


# ── the authority's rule, end to end ─────────────────────────────────────────

class AuthorityRepeatRuleTests(ProgressionBase):
    """How the education authority runs it, and so how the college does:

    fail the sitting → supplementary; pass it → capped at C and carry on; fail
    it → repeat the module, and **stop there**. Wait for the module to be taught
    again, sit it with new continuous assessment, pay the repeat rate for it and
    nothing else. Pass it and carry on into the next semester or level, billed
    that semester in full, as a fresh start. The level does not move until the
    module is cleared.
    """

    def setUp(self):
        super().setUp()
        self.next_year = AcademicYear.objects.create(name='2026/2027')
        self.next_sem1 = Semester.objects.create(academic_year=self.next_year, number=1)
        self.next_sem2 = Semester.objects.create(academic_year=self.next_year, number=2)

    def tuition(self, year, level, amount='1000000'):
        charge_type = ChargeType.objects.create(
            name=f'Tuition Fee {year.name}', family=ChargeType.FEE,
            applies=ChargeType.AUTOMATIC)
        structure = FeeStructure.objects.create(
            charge_type=charge_type, programme=self.pst, class_level=level,
            academic_year=year, amount=Decimal(amount),
            billing_period=FeeStructure.ACADEMIC_YEAR, installments=1)
        finance.set_installment_schedule(structure, [date(2026, 11, 30)])
        return charge_type

    def test_passing_the_supplementary_is_capped_at_a_c(self):
        profile = self.student('REG/050')
        # A hundred in the supplementary is still a C, by the authority's rule.
        self.grade(self.enrol(profile, self.module('PST04101', self.level4, self.sem1)),
                   85, end=40, supp=100)

        outcome = progression.evaluate(profile, self.sem1)

        self.assertEqual(outcome['modules'][0]['grade'], 'C')
        self.assertEqual(outcome['modules'][0]['points'], 2)
        self.assertEqual(outcome['proposed'], SemesterReview.CLEAR)

    def test_failing_the_supplementary_stops_the_student_there(self):
        profile = self.student('REG/051')
        self.register(profile, self.sem1, self.level4)
        failed = self.enrol(profile, self.module('PST04101', self.level4, self.sem1))
        self.grade(failed, 40, supp=30)
        # They had already started semester 2 — the supplementary is marked
        # weeks into it — and had a mark recorded there.
        semester_two = self.register(profile, self.sem2, self.level4)
        started = self.enrol(profile, self.module('PST04201', self.level4, self.sem2))
        part_way = self.grade(started, 70, approved=False)

        review = progression.build_reviews(self.sem1)[0]
        progression.confirm_review(review, review.proposed, actor=self.officer)

        semester_two.refresh_from_db()
        started.refresh_from_db()
        self.assertEqual(semester_two.status, SemesterRegistration.CANCELLED)
        # Off the class: the register, mark entry and the eligibility lists all
        # read this queryset.
        self.assertIsNotNone(started.withdrawn_at)
        self.assertNotIn(started, Student.objects.studying().filter(module__semester=self.sem2))
        # And nothing of theirs was removed.
        part_way.refresh_from_db()
        self.assertEqual(part_way.cat1_theory, 70)
        self.assertTrue(Student.objects.filter(pk=started.pk).exists())

    def test_a_stopped_student_does_not_block_the_next_advance(self):
        profile = self.student('REG/052')
        self.register(profile, self.sem1, self.level4)
        self.grade(self.enrol(profile, self.module('PST04101', self.level4, self.sem1)), 40, supp=30)
        self.register(profile, self.sem2, self.level4)
        self.enrol(profile, self.module('PST04201', self.level4, self.sem2))
        progression.confirm_review(progression.build_reviews(self.sem1)[0],
                                   SemesterReview.REPEAT, actor=self.officer)

        plan = progression.plan_advance(self.sem2)

        self.assertEqual(plan['blocked'], [])
        self.assertEqual(plan['moves'], [])
        self.assertEqual(profile.standing.status, StudentStanding.REPEATING)
        self.assertEqual(profile.standing.class_level, self.level4)

    def test_a_repeat_semester_is_billed_the_module_and_not_the_year(self):
        profile = self.student('REG/053')
        failed = self.enrol(profile, self.module('PST04101', self.level4, self.sem1))
        OutstandingRepeat.objects.create(
            profile=profile, module_code='PST04101', module_name='PST04101',
            class_level=self.level4, semester_number=1,
            origin_semester=self.sem1, origin_enrollment=failed)
        self.module('PST04101', self.level4, self.next_sem1)
        tuition = self.tuition(self.next_year, self.level4)
        repeat_type = self.repeat_fee(self.next_year, self.level4)
        registration = self.register(profile, self.next_sem1, self.level4,
                                     kind=SemesterRegistration.REPEATING)

        progression.enroll_registration(registration, actor=self.officer)

        charges = {charge.charge_type_id: charge for charge in
                   StudentCharge.objects.filter(profile=profile, academic_year=self.next_year)}
        self.assertIn(repeat_type.id, charges)
        self.assertEqual(charges[repeat_type.id].amount, Decimal('30000.00'))
        # The whole year's fees used to be raised on top of the repeat rate.
        self.assertNotIn(tuition.id, charges)

    def test_the_repeat_sitting_starts_with_new_marks(self):
        profile = self.student('REG/054')
        failed = self.enrol(profile, self.module('PST04101', self.level4, self.sem1))
        old = self.grade(failed, 40, supp=30)
        OutstandingRepeat.objects.create(
            profile=profile, module_code='PST04101', module_name='PST04101',
            class_level=self.level4, semester_number=1,
            origin_semester=self.sem1, origin_enrollment=failed)
        self.module('PST04101', self.level4, self.next_sem1)
        self.repeat_fee(self.next_year, self.level4)
        registration = self.register(profile, self.next_sem1, self.level4,
                                     kind=SemesterRegistration.REPEATING)

        sittings = progression.enroll_registration(registration, actor=self.officer)

        self.assertEqual(len(sittings), 1)
        self.assertEqual(sittings[0].attempt, Student.REPEAT)
        self.assertIsNone(getattr(sittings[0], 'result', None))   # new CAs
        old.refresh_from_db()
        self.assertEqual(old.cat1_theory, 40)                      # the old ones stand

    def test_passing_the_repeat_returns_them_to_a_full_semester_billed_in_full(self):
        profile = self.student('REG/055')
        failed = self.enrol(profile, self.module('PST04101', self.level4, self.sem1))
        self.grade(failed, 40, supp=30)
        OutstandingRepeat.objects.create(
            profile=profile, module_code='PST04101', module_name='PST04101',
            class_level=self.level4, semester_number=1,
            origin_semester=self.sem1, origin_enrollment=failed)
        self.repeat_fee(self.next_year, self.level4)
        tuition = self.tuition(self.next_year, self.level4)
        # The repeat year: semester 1 is the module, and nothing else.
        again = self.module('PST04101', self.level4, self.next_sem1)
        registration = self.register(profile, self.next_sem1, self.level4,
                                     kind=SemesterRegistration.REPEATING)
        sitting = progression.enroll_registration(registration, actor=self.officer)[0]
        self.grade(sitting, 60)
        progression.set_standing(profile, StudentStanding.REPEATING, actor=self.officer,
                                 class_level=self.level4, programme=self.pst)
        # Semester 2 of that year is taught as normal.
        self.module('PST04201', self.level4, self.next_sem2)
        review = [r for r in progression.build_reviews(self.next_sem1)
                  if r.profile_id == profile.id][0]
        progression.confirm_review(review, review.proposed, actor=self.officer)

        progression.apply_advance(self.next_sem1, self.next_sem2, actor=self.officer)

        resumed = SemesterRegistration.objects.get(profile=profile, semester=self.next_sem2)
        self.assertEqual(resumed.kind, SemesterRegistration.CONTINUING)
        self.assertEqual(resumed.class_level, self.level4)      # still level 4
        self.assertEqual(OutstandingRepeat.objects.get(profile=profile).status,
                         OutstandingRepeat.PASSED)
        # Read back from the database: this test has held a standing object
        # since before the advance, and it is that copy that would be stale.
        self.assertEqual(StudentStanding.objects.get(profile=profile).status,
                         StudentStanding.ACTIVE)
        # A fresh start: the semester's charges are raised now.
        self.assertTrue(StudentCharge.objects.filter(
            profile=profile, charge_type=tuition, academic_year=self.next_year).exists())
        self.assertEqual([row.module.code for row in Student.objects.filter(
            profile=profile, module__semester=self.next_sem2)], ['PST04201'])

    def test_a_stopped_student_is_not_put_in_front_of_the_officer_to_confirm(self):
        from rest_framework.test import APIClient
        from .views import set_roles

        officer = User.objects.create_user('records3', password='pw')
        set_roles(officer, ['records_officer'], full_name='Records Officer')
        client = APIClient()
        client.force_authenticate(officer)

        profile = self.student('REG/056')
        self.register(profile, self.sem1, self.level4)
        self.grade(self.enrol(profile, self.module('PST04101', self.level4, self.sem1)), 40, supp=30)
        self.register(profile, self.sem2, self.level4)
        self.enrol(profile, self.module('PST04201', self.level4, self.sem2))
        progression.build_reviews(self.sem2)          # built while they were still in it
        progression.confirm_review(progression.build_reviews(self.sem1)[0],
                                   SemesterReview.REPEAT, actor=self.officer)

        listed = client.get(f'/api/semester-reviews/?semester_id={self.sem2.id}')

        self.assertEqual(listed.status_code, 200)
        rows = listed.data['results'] if isinstance(listed.data, dict) else listed.data
        self.assertEqual([row['reg_no'] for row in rows], [])
        # The row itself is still in the record.
        self.assertTrue(SemesterReview.objects.filter(profile=profile, semester=self.sem2).exists())
