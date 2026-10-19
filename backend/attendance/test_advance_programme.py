"""The advance and a student with no programme on record (found on the server,
19 October 2026): a review confirmed before its modules were linked to a
programme kept none, and the advance stopped half-way with a server error.

  * the programme and level are read from the review, the standing, the
    registration, then the modules they sit; if one programme alone offers the
    level, it is theirs — and the blank on the review is filled in;
  * a student that still cannot be placed is listed in the preview as blocked,
    with the reason, so the button is not pressed in vain;
  * if the advance is refused anyway, the office gets the reason (400), and
    nothing has changed: the semester is still the active one.
"""

from rest_framework.test import APIClient

from . import progression, test_progression
from .models import (
    Application, Department, Module, Programme, Semester, SemesterReview, Student,
)
from .views import set_roles


class AdvanceWithoutProgrammeTests(test_progression.AdvanceTests):
    def setUp(self):
        super().setUp()
        # Last year's semester 2 module, never linked to a programme.
        self.old_module = Module.objects.create(name='PST04201', code='PST04201', teacher='T',
                                                class_level=self.level4, semester=self.sem2, credits=1)
        self.profile = self.student('NS1026/0043/2012', 'Abdillah Ramadhani')
        self.grade(self.enrol(self.profile, self.old_module), 80)
        self.confirm_all(self.sem2)
        self.review = SemesterReview.objects.get(profile=self.profile, semester=self.sem2)
        self.assertIsNone(self.review.programme)

    def principal(self):
        user = test_progression.User.objects.create_user('boss', password='pw')
        set_roles(user, ['principal'], full_name='Boss')
        client = APIClient()
        client.force_authenticate(user)
        return client

    def test_the_only_programme_at_the_level_is_theirs(self):
        plan = progression.plan_advance(self.sem2)
        self.assertEqual(plan['blocked'], [])
        self.assertEqual(plan['moves'][0]['programme'], 'PST')
        self.advance()
        application = Application.objects.get(profile=self.profile, semester=self.next_sem1)
        self.assertEqual((application.programme, application.class_level), (self.pst, self.level5))
        self.review.refresh_from_db()
        self.assertEqual(self.review.programme, self.pst)

    def test_a_student_who_cannot_be_placed_is_named_before_the_advance(self):
        other = Programme.objects.create(department=Department.objects.create(name='Other', code='OTH'),
                                         name='Other', code='OTH')
        other.levels.set([self.level4, self.level5])
        plan = progression.plan_advance(self.sem2)
        self.assertEqual([row['reg_no'] for row in plan['blocked']], ['NS1026/0043/2012'])
        self.assertIn('no programme', plan['blocked'][0]['reason'])

        client = self.principal()
        preview = client.get('/api/academic-years/advance-preview/')
        self.assertEqual(preview.status_code, 200)
        self.assertFalse(preview.data['can_advance'])
        refused = client.post('/api/academic-years/advance/', {}, format='json')
        self.assertEqual(refused.status_code, 400)
        self.assertIn('NS1026/0043/2012', str(refused.data))
        self.assertTrue(Semester.objects.get(pk=self.sem2.pk).is_active)
        self.assertFalse(Application.objects.filter(profile=self.profile).exists())

    def test_a_refusal_inside_the_advance_is_a_message_not_a_server_error(self):
        client = self.principal()
        with self._raise_in_apply():
            response = client.post('/api/academic-years/advance/', {}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('Not advanced', response.data['detail'])
        self.assertTrue(Semester.objects.get(pk=self.sem2.pk).is_active)
        self.assertFalse(Semester.objects.filter(pk=self.next_sem1.pk, is_active=True).exists())

    def _raise_in_apply(self):
        from unittest import mock
        return mock.patch.object(progression, 'apply_advance',
                                 side_effect=progression.ProgressionError('REG/X: refused.'))



# Only this module's tests: AdvanceTests' own are run from test_progression.
for _name in dir(test_progression.AdvanceTests):
    if _name.startswith('test_') and _name not in vars(AdvanceWithoutProgrammeTests):
        setattr(AdvanceWithoutProgrammeTests, _name, None)
