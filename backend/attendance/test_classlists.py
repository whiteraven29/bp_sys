"""A level's class list and the CAT exam numbers given from it (said 12
October 2026):

Class list
  * every student of the level this semester, by name, with the NACTVET
    registration number and "First sitting" or "Repeater";
  * a repeater's repeat modules are listed;
  * a student enrolled before registrations were kept is still on it, and
    reading the list writes nothing;
  * provisional while the admission window is open, final once it closes;
  * the admission office and the academic offices read it; others do not.

CAT exam numbers
  * the examination officer sets the format: college initials, level,
    semester, CAT and the running number;
  * each level's CAT 1 and CAT 2 run from 1; numbers once given are kept and a
    student added later continues the run;
  * a format without {SEQ}, or one that gives two levels the same numbers, is
    refused; only the examination officer generates.
"""

from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from . import classlists
from .models import (
    AcademicYear, AdmissionWindow, CatExamNumber, ClassLevel, Department, Module, Programme, Semester,
    SemesterRegistration, Student, StudentProfile,
)
from .views import set_roles

User = get_user_model()


def office(username, *roles):
    user = User.objects.create_user(username, password='pw')
    set_roles(user, list(roles), full_name=username.title())
    client = APIClient()
    client.force_authenticate(user)
    return user, client


class ClassListSetup(TestCase):
    def setUp(self):
        self.year = AcademicYear.objects.create(name='2026/2027', is_active=True)
        self.sem = Semester.objects.create(academic_year=self.year, number=1, is_active=True)
        self.l4 = ClassLevel.objects.create(name='NTA Level 4', order=4)
        self.l5 = ClassLevel.objects.create(name='NTA Level 5', order=5)
        self.pst = Programme.objects.create(department=Department.objects.create(name='PST', code='PST'),
                                            name='PST', code='PST')
        self.m1, self.m2 = [self.module(code, self.l4) for code in ('PST04101', 'PST04102')]
        self.m5 = self.module('PST05101', self.l5)
        self.zainab = self.person('NS0003/0001/2025', 'Zainab Ally', SemesterRegistration.NEW,
                                  [(self.m1, Student.NORMAL), (self.m2, Student.NORMAL)])
        self.baraka = self.person('NS0001/0001/2024', 'Baraka Juma', SemesterRegistration.REPEATING,
                                  [(self.m2, Student.REPEAT)])
        # Imported before registrations were kept, with no profile yet.
        self.old = Student(nactvet_reg_no='ns0002/0001/2025', name='Amina Said', module=self.m1)
        self.old.skip_auto_billing = True
        self.old.save()
        self.level5 = self.person('NS0004/0001/2024', 'Level Five', SemesterRegistration.CONTINUING,
                                  [(self.m5, Student.NORMAL)], level=self.l5)
        self.exam_user, self.exam = office('exam', 'exam_officer')

    def module(self, code, level):
        return Module.objects.create(name=code, code=code, teacher='T', class_level=level,
                                     semester=self.sem, programme=self.pst, credits=4)

    def person(self, reg_no, name, kind, sittings, level=None):
        profile = StudentProfile.objects.create(nactvet_reg_no=reg_no, name=name)
        SemesterRegistration.objects.create(profile=profile, semester=self.sem, programme=self.pst,
                                            class_level=level or self.l4, kind=kind)
        for module, attempt in sittings:
            Student.objects.create(nactvet_reg_no=reg_no, name=name, profile=profile, module=module,
                                   attempt=attempt)
        return profile

    def listed(self, client=None, level=None):
        return (client or self.exam).get('/api/class-list/', {'semester_id': self.sem.id,
                                                              'class_level_id': (level or self.l4).id})


class ClassListTests(ClassListSetup):
    def test_the_level_by_name_with_first_sitting_or_repeater(self):
        data = self.listed().data
        self.assertEqual([(r['name'], r['reg_no'], r['status'], r['modules_repeated']) for r in data['rows']], [
            ('Amina Said', 'NS0002/0001/2025', 'First sitting', []),
            ('Baraka Juma', 'NS0001/0001/2024', 'Repeater', ['PST04102']),
            ('Zainab Ally', 'NS0003/0001/2025', 'First sitting', []),
        ])
        self.assertEqual((data['first_sitting'], data['repeaters']), (2, 1))

    def test_another_level_is_not_on_it(self):
        self.assertEqual([r['name'] for r in self.listed(level=self.l5).data['rows']], ['Level Five'])

    def test_reading_writes_nothing(self):
        before = StudentProfile.objects.count()
        self.listed()
        self.assertEqual(StudentProfile.objects.count(), before)
        self.assertIsNone(Student.objects.get(id=self.old.id).profile_id)

    def test_a_cancelled_registration_or_withdrawn_sitting_is_off_the_list(self):
        SemesterRegistration.objects.filter(profile=self.zainab).update(status=SemesterRegistration.CANCELLED)
        Student.objects.filter(id=self.old.id).update(withdrawn_at=date(2026, 10, 1))
        self.assertEqual([r['name'] for r in self.listed().data['rows']], ['Baraka Juma'])

    def test_provisional_while_admissions_are_open(self):
        today = date.today()
        window = AdmissionWindow.objects.create(semester=self.sem, opens_on=today - timedelta(days=3),
                                                closes_on=today + timedelta(days=10))
        self.assertFalse(self.listed().data['final'])
        window.closes_on = today - timedelta(days=1)
        window.save()
        self.assertTrue(self.listed().data['final'])

    def test_who_reads_it(self):
        _, admission = office('adm', 'admission_officer')
        _, accounts = office('money', 'accountant')
        _, teacher = office('teach', 'tutor')
        self.assertEqual(self.listed(admission).status_code, 200)
        self.assertEqual(self.listed(accounts).status_code, 200)    # the admission offices
        self.assertEqual(self.listed(teacher).status_code, 403)

    def test_a_bad_id_is_a_clear_refusal(self):
        response = self.exam.get('/api/class-list/', {'semester_id': 'x', 'class_level_id': self.l4.id})
        self.assertEqual(response.status_code, 400)


class CatNumberTests(ClassListSetup):
    def generate(self, cat=1, level=None, client=None, **extra):
        return (client or self.exam).post('/api/cat-numbers/', {
            'semester_id': self.sem.id, 'class_level_id': (level or self.l4).id, 'cat': cat, **extra},
            format='json')

    def test_numbers_in_the_set_format(self):
        response = self.exam.post('/api/cat-number-format/', {
            'pattern': '{COLLEGE}/{LEVEL}/S{SEM}/CAT{CAT}/{SEQ}', 'college_code': 'BPHIST',
            'sequence_width': 3}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['sample'], 'BPHIST/4/S1/CAT1/007')
        data = self.generate().data
        self.assertEqual(data['numbered_now'], 3)
        self.assertEqual([(r['name'], r['cat_number']) for r in data['rows']], [
            ('Amina Said', 'BPHIST/4/S1/CAT1/001'),
            ('Baraka Juma', 'BPHIST/4/S1/CAT1/002'),
            ('Zainab Ally', 'BPHIST/4/S1/CAT1/003'),
        ])

    def test_the_default_format(self):
        rows = self.generate(cat=2).data['rows']
        self.assertEqual(rows[0]['cat_number'], 'BPHIST4CAT2S1/2026/1')

    def test_each_level_and_cat_runs_from_one(self):
        self.generate(cat=1)
        self.generate(cat=2)
        five = self.generate(cat=1, level=self.l5).data['rows']
        self.assertEqual(five[0]['cat_number'], 'BPHIST5CAT1S1/2026/1')
        self.assertEqual(CatExamNumber.objects.filter(cat=2, sequence=1).count(), 1)

    def test_numbers_are_kept_and_a_late_student_continues_the_run(self):
        self.generate()
        given = dict(CatExamNumber.objects.values_list('profile__name', 'number'))
        late = self.person('NS0000/0001/2025', 'Aaron Late', SemesterRegistration.NEW,
                           [(self.m1, Student.NORMAL)])
        data = self.generate().data
        self.assertEqual(data['numbered_now'], 1)
        self.assertEqual(CatExamNumber.objects.get(profile=late).number, 'BPHIST4CAT1S1/2026/4')
        for name, number in given.items():
            self.assertEqual(CatExamNumber.objects.get(profile__name=name).number, number)
        self.assertEqual(data['rows'][-1]['name'], 'Aaron Late')               # in number order

    def test_the_legacy_student_gets_a_profile_when_numbered(self):
        self.generate()
        self.assertIsNotNone(Student.objects.get(id=self.old.id).profile_id)

    def test_a_format_without_the_running_number_is_refused(self):
        response = self.exam.post('/api/cat-number-format/', {'pattern': '{COLLEGE}{LEVEL}',
                                                               'college_code': 'BPHIST'}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('{SEQ}', response.data['detail'])
        unknown = self.exam.post('/api/cat-number-format/', {'pattern': '{COLLEGE}{LEVL}{SEQ}',
                                                              'college_code': 'BPHIST'}, format='json')
        self.assertIn('{LEVL}', unknown.data['detail'])

    def test_a_format_that_gives_two_levels_the_same_numbers_is_refused(self):
        self.exam.post('/api/cat-number-format/', {'pattern': '{COLLEGE}-{SEQ}', 'college_code': 'BPHIST'},
                       format='json')
        self.generate()
        clash = self.generate(level=self.l5)
        self.assertEqual(clash.status_code, 400)
        self.assertIn('{LEVEL}', clash.data['detail'])
        self.assertFalse(CatExamNumber.objects.filter(class_level=self.l5).exists())

    def test_only_the_exam_officer_generates(self):
        _, principal = office('boss', 'principal')
        self.assertEqual(self.generate(client=principal).status_code, 403)
        self.assertEqual(principal.get('/api/cat-numbers/', {'semester_id': self.sem.id,
                                                             'class_level_id': self.l4.id, 'cat': 1}).status_code, 200)
        self.assertEqual(principal.post('/api/cat-number-format/', {'pattern': '{SEQ}'},
                                        format='json').status_code, 403)

    def test_the_format_carries_into_the_next_year(self):
        classlists.set_cat_format(self.year, pattern='BP{LEVEL}-{SEQ}', college_code='BP')
        nxt = AcademicYear.objects.create(name='2027/2028')
        self.assertEqual(classlists.cat_format_for(nxt).pattern, 'BP{LEVEL}-{SEQ}')
