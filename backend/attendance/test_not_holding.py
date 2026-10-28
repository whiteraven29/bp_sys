"""Said 28 October 2026: repeaters repeating one module read INCOMPLETE —
field practice held them, though they had cleared it the year before. An
ungraded first sitting of a module already passed, or one a student was taken
off, holds nobody's results. Nothing is changed in the record.
"""

from django.utils import timezone

from . import progression
from .models import AcademicYear, Semester, SemesterRegistration, SemesterReview, Student
from .test_progression import ProgressionBase


class NotHoldingTests(ProgressionBase):
    def setUp(self):
        super().setUp()
        old = Semester.objects.create(academic_year=AcademicYear.objects.create(name='2024/2025'), number=2)
        self.profile = self.student('NS0900/0001/2023')
        # 2024/25: field practice passed, Pharmacology failed after its supplementary.
        self.grade(self.enrol(self.profile, self.module('PST04210', self.level4, old, credits=5)), 70)
        failed = self.enrol(self.profile, self.module('PST04211', self.level4, old, credits=12))
        self.grade(failed, 10, supp=10)
        # 2025/26 semester 2: the repeat, passed — and the class's field practice, never graded.
        self.register(self.profile, self.sem2, self.level4, kind=SemesterRegistration.REPEATING)
        repeat = self.enrol(self.profile, self.module('PST04211', self.level4, self.sem2, credits=12),
                            attempt=Student.REPEAT, repeat_of=failed)
        self.grade(repeat, 70)
        self.field = self.enrol(self.profile, self.module('PST04210', self.level4, self.sem2, credits=5))

    def test_a_field_practice_cleared_before_does_not_hold_the_repeat(self):
        self.assertEqual(progression.not_holding(list(progression.enrollments_for(self.profile, self.sem2))),
                         {self.field.id})
        result = progression.evaluate(self.profile, self.sem2)
        self.assertEqual(result['proposed'], SemesterReview.CLEAR)
        self.assertEqual([row['code'] for row in result['modules']], ['PST04211'])
        self.assertFalse(Student.objects.get(pk=self.field.pk).withdrawn_at)     # nothing changed

    def test_graded_after_all_it_counts_as_any_grade(self):
        self.grade(self.field, 30)                                              # sat and failed now
        self.assertNotEqual(progression.evaluate(self.profile, self.sem2)['proposed'], SemesterReview.CLEAR)

    def test_a_module_never_passed_still_holds(self):
        Student.objects.filter(module__code='PST04210', module__semester__academic_year__name='2024/2025').delete()
        self.assertEqual(progression.evaluate(self.profile, self.sem2)['proposed'], SemesterReview.PENDING)

    def test_a_module_they_were_taken_off_holds_nothing(self):
        other = self.student('NS0901/0001/2023', 'Taken Off')
        self.register(other, self.sem2, self.level4)
        self.grade(self.enrol(other, self.module('PST04212', self.level4, self.sem2, credits=10)), 70)
        dropped = self.enrol(other, self.module('PST04213', self.level4, self.sem2, credits=10))
        Student.objects.filter(pk=dropped.pk).update(withdrawn_at=timezone.now(), withdrawn_reason='test')
        self.assertEqual(progression.evaluate(other, self.sem2)['proposed'], SemesterReview.CLEAR)

    def test_a_first_sitter_is_held_by_field_practice_even_if_passed_long_ago(self):
        """Said the same day: a first sitter's field module with no result yet reads
        INCOMPLETE, rightly — a readmitted student sitting the semester afresh too."""
        Student.objects.filter(attempt=Student.REPEAT).update(attempt=Student.NORMAL, repeat_of=None)
        self.assertEqual(progression.not_holding(list(progression.enrollments_for(self.profile, self.sem2))), set())
        self.assertEqual(progression.evaluate(self.profile, self.sem2)['proposed'], SemesterReview.PENDING)
