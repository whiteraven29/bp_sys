"""Said 25 November 2026: a one-time command to load an earlier year's
NACTVET sheets (2024/25 semester 2, the whole class) from the upload files."""

import tempfile
from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.test import TestCase
from openpyxl import Workbook

from .models import AcademicYear, ClassLevel, Department, Programme, Semester, StudentResult

MODULES = [('PST04208', 'Law And Ethics In Pharmacy Practice', 4), ('PST04209', 'Compounding', 20)]


def sheet(folder, name, title, students):
    book = Workbook()
    grades = book.active
    grades.title = 'Grades'
    grades.append(['Registration Number', 'Student Name', *[m[0] for m in MODULES]])
    for row in students:
        grades.append(list(row))
    modules = book.create_sheet('Modules')
    modules.append(['Code', 'Name', 'Credits'])
    for row in MODULES:
        modules.append(list(row))
    book.create_sheet('Read me').append([title])
    book.save(Path(folder) / name)


class LoadEarlierResultsTests(TestCase):
    def setUp(self):
        year = AcademicYear.objects.create(name='2026/2027', is_active=True)
        Semester.objects.create(academic_year=year, number=1, is_active=True)
        level = ClassLevel.objects.create(name='NTA Level 4', order=4)
        programme = Programme.objects.create(department=Department.objects.create(name='Pharmacy', code='PST'),
                                             name='Pharmaceutical Sciences', code='PST')
        programme.levels.add(level)
        self.folder = tempfile.mkdtemp()
        first = 'Results ▸ Earlier Results: year 2024/2025, semester 2, NTA Level 4, sitting: First sitting.'
        supp = 'Results ▸ Earlier Results: year 2024/2025, semester 2, NTA Level 4, sitting: Supplementary.'
        # Named so the supplementary sorts first: the command loads first sittings first anyway.
        sheet(self.folder, 'a supplementary.xlsx', supp, [('NS0002/0001/2023', 'Two', 'C', 'C')])
        sheet(self.folder, 'b first.xlsx', first, [('NS0001/0001/2023', 'One', 'B', 'A'),
                                                    ('NS0002/0001/2023', 'Two', 'C', 'C***')])

    def run_it(self, *args):
        out = StringIO()
        call_command('load_earlier_results', self.folder, *args, stdout=out)
        return out.getvalue()

    def test_a_dry_run_writes_nothing(self):
        out = self.run_it()
        self.assertIn('Dry run', out)
        self.assertIn('NTA Level 4: 2 students — PASS 2', out)
        self.assertFalse(AcademicYear.objects.filter(name='2024/2025').exists())
        self.assertEqual(StudentResult.objects.count(), 0)

    def test_apply_loads_first_then_the_supplementary_and_twice_is_safe(self):
        self.run_it('--apply')
        result = StudentResult.objects.get(student__nactvet_reg_no='NS0002/0001/2023', student__module__code='PST04209')
        self.assertEqual((result.authority_grade, result.authority_supp_grade), ('C***', 'C'))
        out = self.run_it('--apply')                                         # again: nothing changes
        self.assertIn('0 grades saved', out)
        self.assertEqual(StudentResult.objects.count(), 4)
