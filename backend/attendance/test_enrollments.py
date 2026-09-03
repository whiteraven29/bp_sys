"""Who is enrolled, versus who is in this module.

`Student` is an enrollment — this person, in this module — so a student taking
five modules is five rows. That is the right shape for a module register and the
wrong one for "who is enrolled", which is the question the Manage Enrollments
screen is asked before anybody has picked a module.
"""

from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from .models import (
    AcademicYear, AttendanceRecord, ClassLevel, Module, Semester, Session,
    Student, TeacherProfile,
)

User = get_user_model()


class PersonLevelRosterTests(TestCase):
    def setUp(self):
        self.year = AcademicYear.objects.create(name='2026/2027', is_active=True)
        self.sem = Semester.objects.create(academic_year=self.year, number=1, is_active=True)
        self.sem2 = Semester.objects.create(academic_year=self.year, number=2)
        self.level4 = ClassLevel.objects.create(name='NTA Level 4', order=4)
        self.level5 = ClassLevel.objects.create(name='NTA Level 5', order=5)

        self.modules = [
            Module.objects.create(name=f'Module {i}', code=f'M{i}', teacher='T',
                                  class_level=self.level4, semester=self.sem)
            for i in range(5)
        ]
        for module in self.modules:
            Student.objects.create(nactvet_reg_no='BPH/2026/001', name='Asha Juma',
                                   module=module)
        self.other = Student.objects.create(nactvet_reg_no='BPH/2026/002',
                                            name='Neema Paul', module=self.modules[0])

        self.admin = User.objects.create_superuser('admin', 'a@b.c', 'pw')
        self.api = APIClient()
        self.api.force_authenticate(self.admin)

    def _people(self, query=''):
        return self.api.get(f'/api/students/people/{query}').json()

    def test_a_student_in_five_modules_is_one_row_not_five(self):
        self.assertEqual(len(self.api.get('/api/students/').json()), 6)   # enrollments

        people = self._people()
        self.assertEqual([p['name'] for p in people], ['Asha Juma', 'Neema Paul'])
        self.assertEqual(people[0]['modules'], 5)
        self.assertEqual(people[1]['modules'], 1)

    def test_the_row_carries_the_enrolments_behind_it(self):
        asha = self._people()[0]
        self.assertEqual(len(asha['enrollments']), 5)
        self.assertEqual(sorted(e['module_code'] for e in asha['enrollments']),
                         ['M0', 'M1', 'M2', 'M3', 'M4'])
        # Enough of the person's own fields to edit one straight from this list.
        for key in ('id', 'name', 'nactvet_reg_no', 'module', 'class_level_name'):
            self.assertIn(key, asha['enrollments'][0])

    def test_picking_a_module_still_gives_that_modules_register(self):
        register = self.api.get(f'/api/students/?module_id={self.modules[0].id}').json()
        self.assertEqual(sorted(row['name'] for row in register),
                         ['Asha Juma', 'Neema Paul'])

    def test_attendance_is_summed_across_their_enrolments(self):
        """A student is present for a session or absent from it; which module it
        belonged to is not what the person-level figure is about."""
        for index, module in enumerate(self.modules[:2]):
            session = Session.objects.create(
                module=module, date=date(2026, 9, 1), label=f'Week {index}',
                session_type=Session.THEORY)
            AttendanceRecord.objects.create(
                session=session, student=Student.objects.get(
                    nactvet_reg_no='BPH/2026/001', module=module),
                status=AttendanceRecord.PRESENT if index == 0 else AttendanceRecord.ABSENT)

        asha = self._people()[0]
        self.assertEqual(asha['sessions_total'], 2)
        self.assertEqual(asha['sessions_attended'], 1)
        self.assertEqual(asha['sessions_absent'], 1)
        self.assertEqual(asha['attendance_pct'], 50)

    def test_every_level_they_study_at_is_listed(self):
        Student.objects.create(
            nactvet_reg_no='BPH/2026/001', name='Asha Juma',
            module=Module.objects.create(name='Bridge', code='BR1', teacher='T',
                                         class_level=self.level5, semester=self.sem))
        asha = self._people()[0]
        self.assertEqual(sorted(asha['levels']), ['NTA Level 4', 'NTA Level 5'])

    def test_the_filters_still_narrow_the_people_list(self):
        self.assertEqual(len(self._people(f'?class_level_id={self.level5.id}')), 0)
        self.assertEqual(len(self._people(f'?class_level_id={self.level4.id}')), 2)
        self.assertEqual([p['name'] for p in self._people('?search=Neema')], ['Neema Paul'])

    def test_a_portal_password_on_any_enrolment_counts_for_the_person(self):
        """The portal lets them in on whichever enrolment carries it."""
        self.assertFalse(self._people()[0]['has_portal_pin'])

        one = Student.objects.filter(nactvet_reg_no='BPH/2026/001').first()
        one.set_portal_pin('Portal#2026')
        one.save()
        self.assertTrue(self._people()[0]['has_portal_pin'])

    def test_a_tutor_sees_only_their_own_modules_here_too(self):
        tutor = User.objects.create_user('tutor', password='pw')
        TeacherProfile.objects.create(user=tutor, full_name='Tutor')
        self.modules[0].teachers.add(tutor)
        api = APIClient()
        api.force_authenticate(tutor)

        people = api.get('/api/students/people/').json()
        self.assertEqual(sorted(p['name'] for p in people), ['Asha Juma', 'Neema Paul'])
        # Asha is in five modules, but only one of them is this tutor's.
        asha = next(p for p in people if p['name'] == 'Asha Juma')
        self.assertEqual(asha['modules'], 1)

    def test_a_registration_number_is_matched_whatever_its_case(self):
        Student.objects.create(nactvet_reg_no='bph/2026/002', name='Neema Paul',
                               module=self.modules[1])
        neema = next(p for p in self._people() if p['name'] == 'Neema Paul')
        self.assertEqual(neema['modules'], 2)
