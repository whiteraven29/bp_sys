"""Said 24 November 2026: the examination officer wants a simple summary of
how many students sit a supplementary, with the incomplete ones, that can be
downloaded."""

from io import BytesIO

from django.contrib.auth import get_user_model
from django.test import TestCase
from openpyxl import load_workbook
from rest_framework.test import APIClient

from . import analytics
from .models import AcademicYear, ClassLevel, Module, Semester, Student, StudentResult, TeacherProfile

User = get_user_model()


class SuppListTests(TestCase):
    def setUp(self):
        last = AcademicYear.objects.create(name='2025/2026')
        self.closing = Semester.objects.create(academic_year=last, number=2)
        year = AcademicYear.objects.create(name='2026/2027', is_active=True)
        Semester.objects.create(academic_year=year, number=1, is_active=True)
        self.level = ClassLevel.objects.create(name='NTA Level 4', order=4)
        self.modules = [Module.objects.create(name=n, code=c, teacher='T', class_level=self.level,
                                              semester=self.closing, credits=10)
                        for c, n in (('PST04201', 'Dispensing'), ('PST04202', 'Law'), ('PST04203', 'Anatomy'),
                                     ('PST04204', 'Chemistry'))]
        students = {
            'NS0001/0001/2025': ['C*', 'B', 'B', 'B'],     # supplementary in PST04201
            'NS0002/0001/2025': ['C*', 'B', 'C**', 'A'],   # supplementaries in PST04201 and PST04203
            'NS0003/0001/2025': ['', 'B', 'B', 'B'],       # PST04201 still missing: incomplete
            'NS0004/0001/2025': ['A', 'A', 'B', 'B'],      # passed
            'NS0005/0001/2025': ['C*', 'D*', 'C*', 'B'],   # more than half failed: repeat, not supplementary
        }
        for reg, grades in students.items():
            for module, grade in zip(self.modules, grades):
                enrollment = Student.objects.create(nactvet_reg_no=reg, name=f'Student {reg[2:6]}', module=module)
                if grade:
                    StudentResult.objects.create(student=enrollment, authority_grade=grade, final_approved=True)
        self.api = APIClient()
        self.api.force_authenticate(User.objects.create_superuser('exam', 'e@x.y', 'pw'))

    def test_the_numbers_by_level_and_module_and_the_students(self):
        data = analytics.supp_list(self.closing)
        [line] = data['by_level']
        self.assertEqual((line['students'], line['supp_students'], line['supp_papers'],
                          line['incomplete_students'], line['missing_results']), (5, 2, 3, 1, 1))
        modules = {m['code']: (m['supp'], m['waiting']) for m in data['by_module']}
        self.assertEqual(modules, {'PST04201': (2, 1), 'PST04203': (1, 0)})
        self.assertEqual([(s['reg_no'], s['remark']) for s in data['students']],
                         [('NS0001/0001/2025', 'SUPP'), ('NS0002/0001/2025', 'SUPP'), ('NS0003/0001/2025', 'INCOMPLETE')])
        self.assertEqual(data['students'][1]['supp'], ['PST04201', 'PST04203'])

    def test_it_opens_on_the_semester_whose_supplementaries_come_now(self):
        data = self.api.get('/api/results/supp-list/').json()
        self.assertEqual((data['semester_id'], data['totals']['supp_students']), (self.closing.id, 2))

    def test_the_download(self):
        reply = self.api.get(f'/api/results/supp-list/download/?semester_id={self.closing.id}')
        self.assertEqual(reply.status_code, 200)
        self.assertIn('Supplementary-list-2025-2026-Sem2.xlsx', reply['Content-Disposition'])
        book = load_workbook(BytesIO(reply.content))
        self.assertEqual(book.sheetnames, ['Summary', 'By module', 'Students'])
        self.assertEqual([c.value for c in book['Summary'][5]], ['NTA Level 4', 5, 2, 3, 1, 1])
        self.assertEqual([c.value for c in book['Students'][6]][1:9],
                         ['NS0002/0001/2025', 'Student 0002', 'NTA Level 4', 'Continuing', 'Supplementary', 2.7,
                          'PST04201, PST04203', None])

    def test_for_the_examination_office_not_a_tutor(self):
        tutor = User.objects.create_user('tutor', password='pw')
        TeacherProfile.objects.create(user=tutor, full_name='Tutor')
        client = APIClient()
        client.force_authenticate(tutor)
        self.assertEqual(client.get('/api/results/supp-list/').status_code, 403)
