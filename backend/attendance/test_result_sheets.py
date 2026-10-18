"""NACTVET's two result sheets, and the returning student's college ID (said
18 October 2026):

  * the continuing sheet (template and Results Summary) is only for students
    sitting every module of their programme at the level this semester, all as
    first sittings; anybody missing a module is on the repeaters' sheet;
  * "every module" is every module the class sits: a module nobody is on yet,
    or another programme's module, takes nobody off the continuing sheet;
  * a returning student is recorded with the college ID they already carry —
    never over a different one, never one another student holds.
"""

from . import progression
from .models import Department, Module, Programme, Student, StudentProfile
from .test_returning import ReturningBase


class ResultSheetTests(ReturningBase):
    def setUp(self):
        super().setUp()
        self.unused = self.module('PST04104', self.level4, self.sem1)   # nobody on it yet
        self.full = self.enrol('NS0001/0001/2025', 'Full Class', [self.m1, self.m2, self.m3])
        self.partial = self.enrol('NS0002/0001/2025', 'Part Semester', [self.m1, self.m2])
        self.repeater = self.enrol('NS0003/0001/2024', 'Repeating One', [self.m3], attempt=Student.REPEAT)

    def enrol(self, reg_no, name, modules, attempt=Student.NORMAL):
        profile = StudentProfile.objects.create(nactvet_reg_no=reg_no, name=name)
        for module in modules:
            Student.objects.create(nactvet_reg_no=reg_no, name=name, profile=profile, module=module,
                                   attempt=attempt)
        return profile

    def sheet(self, which):
        summary = progression.results_summary(self.sem1, self.level4, sheet=which)
        return {row['reg_no']: row for row in summary['students']}

    def test_only_a_student_with_every_module_is_a_first_sitting(self):
        self.assertEqual(set(self.sheet('continuing')), {'NS0001/0001/2025'})
        self.assertEqual(set(self.sheet('repeaters')), {'NS0002/0001/2025', 'NS0003/0001/2024'})

    def test_a_student_missing_a_module_fills_only_what_they_sit(self):
        row = self.sheet('repeaters')['NS0002/0001/2025']
        self.assertEqual(row['sitting'], ['PST04101', 'PST04102'])
        self.assertEqual(row['repeats'], [])
        self.assertEqual(row['origin'], self.sem1.label)
        self.assertEqual(self.sheet('repeaters')['NS0003/0001/2024']['repeats'], ['PST04103'])

    def test_another_programmes_modules_take_nobody_off_the_sheet(self):
        hims = Programme.objects.create(
            department=Department.objects.create(name='Health Information', code='HIM'),
            name='Health Information Management', code='HIM')
        other = Module.objects.create(name='HIM04101', code='HIM04101', teacher='T', class_level=self.level4,
                                      semester=self.sem1, programme=hims, credits=3)
        self.enrol('NS0004/0001/2025', 'Other Programme', [other])
        self.assertEqual(set(self.sheet('continuing')), {'NS0001/0001/2025', 'NS0004/0001/2025'})

    def test_the_api_serves_the_same_sheets(self):
        for which, expected in (('continuing', {'NS0001/0001/2025'}),
                                ('repeaters', {'NS0002/0001/2025', 'NS0003/0001/2024'})):
            response = self.exam.get(f'/api/results-summary/?semester_id={self.sem1.id}'
                                     f'&class_level_id={self.level4.id}&sheet={which}')
            self.assertEqual(response.status_code, 200)
            self.assertEqual({row['reg_no'] for row in response.data['students']}, expected)


class ReturningCollegeIdTests(ReturningBase):
    def test_the_college_id_is_recorded_and_listed(self):
        result = self.record(kind='postponed', college_id='BPH/PST/2024/0099').data['results'][0]
        self.assertTrue(result['ok'], result)
        profile = StudentProfile.objects.get(nactvet_reg_no='NS0099/0001/2024')
        self.assertEqual(profile.college_id, 'BPH/PST/2024/0099')
        listed = self.exam.get('/api/returning-students/').data
        self.assertEqual(listed[0]['college_id'], 'BPH/PST/2024/0099')

    def test_an_id_another_student_holds_is_refused(self):
        StudentProfile.objects.create(nactvet_reg_no='NS0100/0001/2024', name='Holder',
                                      college_id='BPH/PST/2024/0099')
        result = self.record(kind='postponed', college_id='bph/pst/2024/0099').data['results'][0]
        self.assertFalse(result['ok'])
        self.assertIn('NS0100/0001/2024', result['error'])
        # Nothing of the row is kept.
        self.assertFalse(StudentProfile.objects.filter(nactvet_reg_no='NS0099/0001/2024').exists())

    def test_a_different_id_on_file_is_not_changed(self):
        StudentProfile.objects.create(nactvet_reg_no='NS0099/0001/2024', name='Juma Returning',
                                      college_id='BPH/PST/2024/0001')
        result = self.record(kind='postponed', college_id='BPH/PST/2024/0099').data['results'][0]
        self.assertFalse(result['ok'])
        self.assertIn('BPH/PST/2024/0001', result['error'])
        same = self.record(kind='postponed', college_id='BPH/PST/2024/0001').data['results'][0]
        self.assertTrue(same['ok'], same)

    def test_the_id_is_optional(self):
        self.assertTrue(self.record(kind='postponed').data['results'][0]['ok'])
        self.assertIsNone(StudentProfile.objects.get(nactvet_reg_no='NS0099/0001/2024').college_id)
