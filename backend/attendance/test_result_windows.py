"""When marks may be entered, and by whom.

Marks used to be enterable whenever anyone happened to open the page, so a CA
mark could be revised weeks after the results were published and nobody would
know. The examination officer now says when the books are open.
"""

from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from .models import (
    AcademicYear, ClassLevel, HeadOfDepartmentProfile, Module, ResultEntryWindow,
    Semester, Student, StudentResult, TeacherProfile,
)

User = get_user_model()
TODAY = date.today()


class WindowTestBase(TestCase):
    def setUp(self):
        self.year = AcademicYear.objects.create(name='2026/2027', is_active=True)
        self.sem = Semester.objects.create(academic_year=self.year, number=1, is_active=True)
        self.level = ClassLevel.objects.create(name='NTA Level 4', order=4)
        self.module = Module.objects.create(name='Pharmaceutics', code='PHM101', teacher='T',
                                            class_level=self.level, semester=self.sem)
        self.student = Student.objects.create(nactvet_reg_no='BPH/2026/001', name='Asha Juma',
                                              module=self.module)

        self.officer = User.objects.create_superuser('officer', 'a@b.c', 'pw')
        self.api = APIClient()
        self.api.force_authenticate(self.officer)

        self.tutor = User.objects.create_user('tutor', password='pw')
        TeacherProfile.objects.create(user=self.tutor, full_name='Tutor')
        self.module.teachers.add(self.tutor)
        self.tutor_api = APIClient()
        self.tutor_api.force_authenticate(self.tutor)

    def _window(self, kind=ResultEntryWindow.CA, opens=-1, closes=7, **kw):
        return ResultEntryWindow.objects.create(
            semester=self.sem, kind=kind,
            opens_on=TODAY + timedelta(days=opens),
            closes_on=TODAY + timedelta(days=closes),
            declared_by=self.officer, **kw)

    def _tutor_enters(self, **marks):
        return self.tutor_api.post('/api/results/',
                                   {'student': self.student.id, **marks}, format='json')


class DeclaringAWindowTests(WindowTestBase):
    def test_only_the_examination_officer_declares_one(self):
        payload = {'semester': self.sem.id, 'kind': 'ca',
                   'opens_on': str(TODAY), 'closes_on': str(TODAY + timedelta(days=7))}

        made = self.api.post('/api/result-windows/', payload, format='json')
        self.assertEqual(made.status_code, 201, made.data)
        self.assertEqual(made.data['status'], 'open')
        self.assertEqual(ResultEntryWindow.objects.get().declared_by, self.officer)

        self.assertEqual(
            self.tutor_api.post('/api/result-windows/', payload, format='json').status_code, 403)

    def test_a_head_of_department_cannot_declare_one_either(self):
        hod = User.objects.create_user('hod', password='pw', is_staff=True)
        HeadOfDepartmentProfile.objects.create(user=hod, full_name='HoD')
        api = APIClient()
        api.force_authenticate(hod)

        refused = api.post('/api/result-windows/', {
            'semester': self.sem.id, 'kind': 'ca',
            'opens_on': str(TODAY), 'closes_on': str(TODAY + timedelta(days=7)),
        }, format='json')
        self.assertEqual(refused.status_code, 403)

    def test_a_tutor_can_read_the_dates(self):
        """Being told after typing a screenful of marks is no use."""
        self._window(note='Submit CAT 2 by Friday')
        listed = self.tutor_api.get('/api/result-windows/')
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.data[0]['note'], 'Submit CAT 2 by Friday')
        self.assertEqual(listed.data[0]['status'], 'open')

    def test_a_closing_date_before_the_opening_date_is_refused(self):
        refused = self.api.post('/api/result-windows/', {
            'semester': self.sem.id, 'kind': 'ca',
            'opens_on': str(TODAY), 'closes_on': str(TODAY - timedelta(days=1)),
        }, format='json')
        self.assertEqual(refused.status_code, 400)

    def test_one_window_per_kind_per_semester(self):
        self._window()
        clash = self.api.post('/api/result-windows/', {
            'semester': self.sem.id, 'kind': 'ca',
            'opens_on': str(TODAY), 'closes_on': str(TODAY + timedelta(days=7)),
        }, format='json')
        self.assertEqual(clash.status_code, 400)
        # The other kind is a different window and is fine.
        self.assertEqual(self.api.post('/api/result-windows/', {
            'semester': self.sem.id, 'kind': 'end',
            'opens_on': str(TODAY), 'closes_on': str(TODAY + timedelta(days=7)),
        }, format='json').status_code, 201)

    def test_the_status_reads_off_the_dates_and_the_switch(self):
        window = self._window(opens=2, closes=9)
        self.assertEqual(window.status(), 'upcoming')
        window.opens_on, window.closes_on = TODAY - timedelta(days=9), TODAY - timedelta(days=2)
        self.assertEqual(window.status(), 'closed')
        window.opens_on, window.closes_on = TODAY - timedelta(days=1), TODAY + timedelta(days=1)
        self.assertEqual(window.status(), 'open')
        window.is_active = False
        self.assertEqual(window.status(), 'closed')


class EnteringMarksTests(WindowTestBase):
    def test_a_tutor_can_enter_ca_marks_while_the_window_is_open(self):
        self._window()
        entered = self._tutor_enters(cat1_theory=70)
        self.assertEqual(entered.status_code, 201, entered.data)
        self.assertEqual(StudentResult.objects.get().cat1_theory, 70)

    def test_nothing_is_open_until_the_officer_says_so(self):
        """Saying nothing is not the same as saying yes."""
        refused = self._tutor_enters(cat1_theory=70)
        self.assertEqual(refused.status_code, 403)
        self.assertIn('has not opened', refused.data['detail'])
        self.assertFalse(StudentResult.objects.exists())

    def test_a_closed_window_says_when_it_was_open(self):
        self._window(opens=-14, closes=-7)
        refused = self._tutor_enters(cat1_theory=70)
        self.assertEqual(refused.status_code, 403)
        self.assertIn('is closed', refused.data['detail'])
        self.assertIn('examination officer', refused.data['detail'])

    def test_closing_the_switch_shuts_the_books_immediately(self):
        window = self._window()
        self.assertEqual(self._tutor_enters(cat1_theory=70).status_code, 201)

        window.is_active = False
        window.save()
        StudentResult.objects.all().delete()
        self.assertEqual(self._tutor_enters(cat1_theory=70).status_code, 403)

    def test_a_tutor_cannot_revise_a_mark_once_the_window_closes(self):
        window = self._window()
        self._tutor_enters(cat1_theory=70)
        result = StudentResult.objects.get()

        window.closes_on = TODAY - timedelta(days=1)
        window.opens_on = TODAY - timedelta(days=8)
        window.save()

        refused = self.tutor_api.patch(f'/api/results/{result.id}/',
                                       {'cat1_theory': 95}, format='json')
        self.assertEqual(refused.status_code, 403)
        result.refresh_from_db()
        self.assertEqual(result.cat1_theory, 70)

    def test_a_tutor_enters_continuous_assessment_and_nothing_else(self):
        """The CATs and assignments they set and marked. The end of semester
        paper is the examination office's, whatever the calendar says."""
        self._window(kind=ResultEntryWindow.CA)
        self.assertEqual(self._tutor_enters(cat1_theory=70).status_code, 201)
        result = StudentResult.objects.get()

        for payload in ({'end_theory': 60}, {'end_practical': 60},
                        {'end_theory_absent': True}, {'supplementary_mark': 50}):
            refused = self.tutor_api.patch(f'/api/results/{result.id}/', payload, format='json')
            self.assertEqual(refused.status_code, 403, payload)

        result.refresh_from_db()
        self.assertIsNone(result.end_theory)

    def test_opening_an_end_of_semester_window_does_not_let_a_tutor_in(self):
        """That window is the college's published deadline, not a permission."""
        self._window(kind=ResultEntryWindow.CA)
        self._window(kind=ResultEntryWindow.END)
        self._tutor_enters(cat1_theory=70)
        result = StudentResult.objects.get()

        refused = self.tutor_api.patch(f'/api/results/{result.id}/',
                                       {'end_theory': 60}, format='json')
        self.assertEqual(refused.status_code, 403)
        self.assertIn('examination officer', refused.data['detail'])
        self.assertIn('CATs and assignments', refused.data['detail'])

    def test_approving_is_never_a_tutors_to_do_open_or_not(self):
        self._window()
        self._tutor_enters(cat1_theory=70)
        result = StudentResult.objects.get()

        refused = self.tutor_api.patch(f'/api/results/{result.id}/',
                                       {'ca_approved': True}, format='json')
        self.assertEqual(refused.status_code, 403)
        self.assertIn('approve or publish', refused.data['detail'])
        result.refresh_from_db()
        self.assertFalse(result.ca_approved)

    def test_the_examination_officer_writes_whatever_the_window_says(self):
        """Somebody has to be able to correct a mark after the books close."""
        result = StudentResult.objects.create(student=self.student, cat1_theory=40)
        for payload in ({'cat1_theory': 70}, {'end_theory': 60}, {'ca_approved': True}):
            answered = self.api.patch(f'/api/results/{result.id}/', payload, format='json')
            self.assertEqual(answered.status_code, 200, (payload, answered.data))

        result.refresh_from_db()
        self.assertEqual(result.cat1_theory, 70)
        self.assertTrue(result.ca_approved)

    def test_a_head_of_department_is_refused_whatever_the_window_says(self):
        self._window()
        hod = User.objects.create_user('hod', password='pw', is_staff=True)
        HeadOfDepartmentProfile.objects.create(user=hod, full_name='HoD')
        api = APIClient()
        api.force_authenticate(hod)

        refused = api.post('/api/results/',
                           {'student': self.student.id, 'cat1_theory': 70}, format='json')
        self.assertEqual(refused.status_code, 403)
        self.assertIn('examination officer', refused.data['detail'])


class TutorPerformanceTests(WindowTestBase):
    """A tutor sees their own students and their own modules — no more."""

    def setUp(self):
        super().setUp()
        self.other = Module.objects.create(name='Anatomy', code='ANA101', teacher='T',
                                           class_level=self.level, semester=self.sem)
        somebody = Student.objects.create(nactvet_reg_no='BPH/2026/002', name='Neema Paul',
                                          module=self.other)
        StudentResult.objects.create(student=self.student, assign1=70, assign2=70,
                                     cat1_theory=70, cat2_theory=70, end_theory=70)
        StudentResult.objects.create(student=somebody, assign1=30, assign2=30,
                                     cat1_theory=30, cat2_theory=30, end_theory=30)

    def test_a_tutor_sees_their_own_module_and_not_the_others(self):
        mine = self.tutor_api.get('/api/performance/?assessment=final').json()

        self.assertEqual(mine['scope'], 'my-modules')
        self.assertEqual([row['code'] for row in mine['by_module']], ['PHM101'])
        self.assertEqual(mine['headline']['assessed'], 1)
        self.assertEqual(mine['headline']['mean'], 70.0)

    def test_the_college_still_sees_everything(self):
        whole = self.api.get('/api/performance/?assessment=final').json()
        self.assertEqual(whole['scope'], 'college')
        self.assertEqual(sorted(row['code'] for row in whole['by_module']),
                         ['ANA101', 'PHM101'])
        self.assertEqual(whole['headline']['assessed'], 2)

    def test_the_filters_offered_to_a_tutor_are_their_own_modules(self):
        options = self.tutor_api.get('/api/performance/').json()['options']
        self.assertEqual([m['code'] for m in options['modules']], ['PHM101'])

    def test_asking_for_somebody_elses_module_does_not_show_it(self):
        answered = self.tutor_api.get(
            f'/api/performance/?assessment=final&module_id={self.other.id}').json()
        self.assertEqual([row['code'] for row in answered['by_module']], ['PHM101'])

    def test_a_tutor_with_no_modules_sees_nothing_rather_than_everything(self):
        stranger = User.objects.create_user('stranger', password='pw')
        TeacherProfile.objects.create(user=stranger, full_name='Stranger')
        api = APIClient()
        api.force_authenticate(stranger)

        answered = api.get('/api/performance/?assessment=final').json()
        self.assertEqual(answered['headline']['assessed'], 0)
        self.assertEqual(answered['by_module'], [])
        self.assertEqual(answered['trend'], [])
