"""The API around departments and programmes, the two new offices, exam
declarations charged to the ledger, and recording existing college ID numbers.
"""

from datetime import date
from decimal import Decimal
from io import BytesIO, StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from openpyxl import Workbook
from rest_framework.test import APIClient

from . import finance
from .models import (
    AcademicYear, ChargeType, ClassLevel, Department, FeeStructure, Module, Programme,
    SemesterRegistration, Semester, Student, StudentCharge, StudentProfile, StudentResult,
)
from .views import roles_for

User = get_user_model()


class ApiBase(TestCase):
    def setUp(self):
        self.year = AcademicYear.objects.create(name='2026/2027', is_active=True)
        self.sem = Semester.objects.create(academic_year=self.year, number=1, is_active=True)
        self.level4 = ClassLevel.objects.create(name='NTA Level 4', order=4)
        self.level5 = ClassLevel.objects.create(name='NTA Level 5', order=5)
        self.level6 = ClassLevel.objects.create(name='NTA Level 6', order=6)

        self.admin = User.objects.create_superuser('admin', 'a@b.c', 'pw')
        self.api = APIClient()
        self.api.force_authenticate(self.admin)

    def account(self, role, username):
        made = self.api.post('/api/staff-accounts/', {
            'role': role, 'full_name': f'{role} person', 'username': username, 'password': 'secret123',
        }, format='json')
        self.assertEqual(made.status_code, 201, made.data)
        user = User.objects.get(username=username)
        client = APIClient()
        client.force_authenticate(user)
        return user, client

    def department(self, **extra):
        response = self.api.post('/api/departments/', {'name': 'Pharmaceutical Sciences', 'code': 'pst', **extra},
                                 format='json')
        self.assertEqual(response.status_code, 201, response.data)
        return Department.objects.get(pk=response.data['id'])

    def programme(self, department, levels=None, code='PST'):
        response = self.api.post('/api/programmes/', {
            'department': department.id, 'name': 'Pharmaceutical Sciences', 'code': code,
            'levels': [level.id for level in (levels or [])],
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        return Programme.objects.get(pk=response.data['id'])


class NewOfficesTests(ApiBase):
    def test_records_and_admission_officers_can_be_created_without_admin_rights(self):
        records, records_client = self.account('records_officer', 'records')
        admission, _ = self.account('admission_officer', 'admission')

        self.assertEqual(roles_for(records), ['records_officer'])
        self.assertEqual(roles_for(admission), ['admission_officer'])
        self.assertFalse(records.is_staff)
        self.assertFalse(admission.is_staff)

        dash = records_client.get('/api/dashboard/').data
        self.assertTrue(dash['is_records_officer'])
        self.assertFalse(dash['is_admission_officer'])
        self.assertTrue(dash['can_edit_student_records'])

    def test_the_new_roles_can_be_added_to_an_existing_account(self):
        tutor, _ = self.account('tutor', 'tutor')
        response = self.api.post(f'/api/staff-accounts/{tutor.id}/roles/',
                                 {'roles': ['tutor', 'records_officer']}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(set(roles_for(tutor)), {'tutor', 'records_officer'})


class DepartmentTests(ApiBase):
    def test_a_department_and_its_programme_are_registered_with_uppercase_codes(self):
        department = self.department()
        programme = self.programme(department, levels=[self.level4, self.level5, self.level6], code='pst')

        self.assertEqual(department.code, 'PST')
        self.assertEqual(programme.code, 'PST')
        listed = self.api.get('/api/departments/').data
        self.assertEqual(listed[0]['programmes'][0]['level_names'], ['NTA Level 4', 'NTA Level 5', 'NTA Level 6'])

    def test_a_code_already_in_use_is_refused_whatever_its_case(self):
        department = self.department()
        self.programme(department, code='PST')
        again = self.api.post('/api/departments/', {'name': 'Another', 'code': 'Pst'}, format='json')
        self.assertEqual(again.status_code, 400)
        self.assertIn('already in use', str(again.data))

        clash = self.api.post('/api/programmes/', {'department': department.id, 'name': 'Other', 'code': 'pst'},
                              format='json')
        self.assertEqual(clash.status_code, 400)
        self.assertIn('already in use', str(clash.data))

    def test_the_head_must_already_hold_the_role(self):
        tutor, _ = self.account('tutor', 'tutor')
        refused = self.api.post('/api/departments/', {'name': 'Pharmaceutical Sciences', 'code': 'PST',
                                                      'hod': tutor.id}, format='json')
        self.assertEqual(refused.status_code, 400)
        self.assertIn('does not hold the Head of Department role', str(refused.data))
        # Nor did trying give them the role.
        self.assertEqual(roles_for(tutor), ['tutor'])

        hod, _ = self.account('hod', 'hod')
        department = self.department(hod=hod.id, staff=[tutor.id])
        self.assertEqual(department.hod, hod)
        self.assertEqual(list(department.staff.all()), [tutor])

    def test_staff_read_departments_but_only_the_principal_or_exam_officer_change_them(self):
        department = self.department()
        _, accountant = self.account('accountant', 'accountant')
        _, hod = self.account('hod', 'hod')

        self.assertEqual(accountant.get('/api/departments/').status_code, 200)
        self.assertEqual(accountant.post('/api/departments/', {'name': 'X', 'code': 'X'}, format='json').status_code, 403)
        self.assertEqual(hod.patch(f'/api/departments/{department.id}/', {'name': 'Y'}, format='json').status_code, 403)
        self.assertEqual(APIClient().get('/api/departments/').status_code, 403)

    def test_a_department_with_programmes_cannot_be_deleted(self):
        department = self.department()
        self.programme(department)
        response = self.api.delete(f'/api/departments/{department.id}/')
        self.assertEqual(response.status_code, 403)
        self.assertIn('inactive', str(response.data))
        self.assertTrue(Department.objects.filter(pk=department.id).exists())


class LinkingModulesTests(ApiBase):
    def setUp(self):
        super().setUp()
        self.pst = self.programme(self.department(), levels=[self.level4, self.level5])
        self.m1 = Module.objects.create(name='Pharmacology', code='PST04101', teacher='T',
                                        class_level=self.level4, semester=self.sem)
        self.m6 = Module.objects.create(name='Level six', code='PST06101', teacher='T',
                                        class_level=self.level6, semester=self.sem)
        self.other = Module.objects.create(name='Other', code='HIM04101', teacher='T',
                                           class_level=self.level4, semester=self.sem)

    def test_a_prefix_links_only_matching_unlinked_modules_the_programme_is_taught_at(self):
        response = self.api.post(f'/api/programmes/{self.pst.id}/link-modules/', {'code_prefix': 'pst'},
                                 format='json')

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['modules'], ['PST04101'])
        self.assertEqual(response.data['skipped'][0]['module'], 'PST06101')
        self.m1.refresh_from_db(), self.m6.refresh_from_db(), self.other.refresh_from_db()
        self.assertEqual(self.m1.programme, self.pst)
        self.assertIsNone(self.m6.programme)
        self.assertIsNone(self.other.programme)

    def test_a_prefix_never_moves_a_module_out_of_another_programme(self):
        second = self.programme(Department.objects.create(name='Second', code='SEC'), code='PSX')
        Module.objects.filter(pk=self.m1.pk).update(programme=second)

        self.api.post(f'/api/programmes/{self.pst.id}/link-modules/', {'code_prefix': 'PST'}, format='json')

        self.m1.refresh_from_db()
        self.assertEqual(self.m1.programme, second)

    def test_a_module_cannot_join_a_programme_not_taught_at_its_level(self):
        response = self.api.post('/api/modules/', {
            'name': 'Research', 'code': 'PST06102', 'teacher': 'T', 'class_level': self.level6.id,
            'semester': self.sem.id, 'programme': self.pst.id,
        }, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('not taught at NTA Level 6', str(response.data))


class ProgrammeFeeApiTests(ApiBase):
    def setUp(self):
        super().setUp()
        self.pst = self.programme(self.department(), levels=[self.level4])
        _, self.accountant = self.account('accountant', 'accountant')
        self.tuition = ChargeType.objects.create(name='Tuition Fee', family=ChargeType.FEE)

    def post_fee(self, amount, programme=None):
        return self.accountant.post('/api/fee-structures/', {
            'charge_type': self.tuition.id, 'class_level': self.level4.id, 'academic_year': self.year.id,
            'programme': programme.id if programme else None, 'amount': amount, 'installments': 1,
            'due_dates': ['2026-10-30'],
        }, format='json')

    def test_a_duplicate_fee_cell_is_refused_with_a_message_not_a_server_error(self):
        self.assertEqual(self.post_fee('1000000').status_code, 201)
        self.assertEqual(self.post_fee('1600000', self.pst).status_code, 201)

        again = self.post_fee('900000')
        self.assertEqual(again.status_code, 400)
        self.assertIn('already has an amount for every programme', str(again.data))
        again = self.post_fee('900000', self.pst)
        self.assertEqual(again.status_code, 400)
        self.assertIn('already has an amount for PST', str(again.data))

    def test_a_programme_sheet_shows_what_its_students_inherit(self):
        self.post_fee('1000000')

        sheet = self.accountant.get(f'/api/fee-structures/grid/?academic_year_id={self.year.id}'
                                    f'&programme_id={self.pst.id}').data
        cell = sheet['rows'][0]['cells'][0]
        self.assertEqual(sheet['programme']['code'], 'PST')
        self.assertIsNone(cell['amount'])
        self.assertEqual(cell['inherited_amount'], '1000000.00')

        self.post_fee('1600000', self.pst)
        cell = self.accountant.get(f'/api/fee-structures/grid/?academic_year_id={self.year.id}'
                                   f'&programme_id={self.pst.id}').data['rows'][0]['cells'][0]
        self.assertEqual(cell['amount'], '1600000.00')
        self.assertIsNone(cell['inherited_amount'])

        every = self.accountant.get(f'/api/fee-structures/grid/?academic_year_id={self.year.id}').data
        self.assertEqual(every['rows'][0]['cells'][0]['amount'], '1000000.00')

    def test_the_accountant_links_a_charge_type_to_a_declaration(self):
        response = self.accountant.post('/api/charge-types/', {
            'name': 'Supplementary Exam', 'family': ChargeType.OTHER, 'applies': ChargeType.ON_REQUEST,
            'declaration': ChargeType.SUPP_EXAM, 'charged_again_on_readmission': False,
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['declaration_display'], 'Supplementary exam')
        self.assertFalse(response.data['charged_again_on_readmission'])


class ExamDeclarationApiTests(ApiBase):
    def setUp(self):
        super().setUp()
        self.pst = self.programme(self.department(), levels=[self.level4])
        self.module = Module.objects.create(name='Pharmacology', code='PST04101', teacher='T',
                                            class_level=self.level4, semester=self.sem, programme=self.pst)
        self.asha = Student.objects.create(nactvet_reg_no='REG/001', name='Asha', module=self.module)
        self.juma = Student.objects.create(nactvet_reg_no='REG/002', name='Juma', module=self.module)
        self.supp = ChargeType.objects.create(name='Supplementary Exam', family=ChargeType.OTHER,
                                              applies=ChargeType.ON_REQUEST, declaration=ChargeType.SUPP_EXAM)

    def set_rate(self):
        structure = FeeStructure.objects.create(charge_type=self.supp, programme=self.pst, class_level=self.level4,
                                                academic_year=self.year, amount=Decimal('30000'), installments=1)
        finance.set_installment_schedule(structure, [date(2026, 12, 1)])

    def declare(self, client=None, kind=ChargeType.SUPP_EXAM, students=None):
        students = students or [{'id': self.asha.id, 'reason': 'Theory below 50'}, {'id': self.juma.id}]
        return (client or self.api).post('/api/exam-declarations/', {'kind': kind, 'students': students},
                                         format='json')

    def test_declaring_charges_each_student_once_at_the_rate(self):
        self.set_rate()
        response = self.declare()
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data, {'created': 2, 'already_declared': 0})

        again = self.declare()
        self.assertEqual(again.data, {'created': 0, 'already_declared': 2})
        charges = StudentCharge.objects.filter(charge_type=self.supp)
        self.assertEqual(charges.count(), 2)
        self.assertEqual({c.amount for c in charges}, {Decimal('30000.00')})
        self.assertEqual(charges.get(profile__nactvet_reg_no='REG/001').note, 'Theory below 50')

    def test_the_screen_is_told_who_is_already_declared(self):
        self.set_rate()
        self.declare(students=[{'id': self.asha.id}])
        declared = self.api.get(f'/api/exam-declarations/?module_id={self.module.id}').data['declared']
        self.assertEqual(declared, {str(self.asha.id): [ChargeType.SUPP_EXAM]})

    def test_without_a_rate_nobody_in_the_batch_is_charged(self):
        response = self.declare()
        self.assertEqual(response.status_code, 400)
        self.assertIn('has not set a rate', response.data['detail'])
        self.assertFalse(StudentCharge.objects.filter(charge_type=self.supp).exists())

    def test_discontinuation_is_not_something_declared_for_a_charge(self):
        response = self.declare(kind='discontinuation')
        self.assertEqual(response.status_code, 400)
        self.assertIn('not charged', response.data['detail'])

    def test_declaring_is_the_examination_officers_and_reading_is_open_to_finance(self):
        self.set_rate()
        _, hod = self.account('hod', 'hod')
        _, accountant = self.account('accountant', 'accountant')
        self.assertEqual(self.declare(hod).status_code, 403)
        self.assertEqual(self.declare(accountant).status_code, 403)
        self.assertEqual(accountant.get(f'/api/exam-declarations/?module_id={self.module.id}').status_code, 200)
        self.assertFalse(StudentCharge.objects.filter(charge_type=self.supp).exists())


class CollegeIdImportTests(ApiBase):
    def setUp(self):
        super().setUp()
        module = Module.objects.create(name='M', code='PST04101', teacher='T', class_level=self.level4, semester=self.sem)
        for reg in ('REG/001', 'REG/002', 'REG/003'):
            Student.objects.create(nactvet_reg_no=reg, name=reg, module=module)
        StudentProfile.objects.filter(nactvet_reg_no='REG/003').update(college_id='BPH/PST/25/0003')
        self.records, self.records_client = self.account('records_officer', 'records')

    def rows(self, *pairs, client=None, dry_run=False):
        return (client or self.records_client).post('/api/student-records/college-ids/', {
            'rows': [{'nactvet_reg_no': r, 'college_id': c} for r, c in pairs], 'dry_run': dry_run,
        }, format='json')

    def test_each_row_is_recorded_or_explained_and_nothing_is_overwritten(self):
        response = self.rows(
            ('reg/001', 'BPH/PST/25/0001'),        # recorded, reg no in any case
            ('REG/002', 'BPH/PST/25/0001'),        # same ID as row 1
            ('REG/003', 'BPH/PST/25/9999'),        # already has a different one
            ('REG/404', 'BPH/PST/25/0404'),        # no such student
            ('REG/002', 'BPH/PST/25/0003'),        # belongs to REG/003
        )
        self.assertEqual(response.status_code, 200, response.data)
        statuses = [(row['status'], row['detail']) for row in response.data['rows']]
        self.assertEqual(statuses[0][0], 'recorded')
        self.assertIn('row 1', statuses[1][1])
        self.assertIn('already has college ID BPH/PST/25/0003', statuses[2][1])
        self.assertIn('No student', statuses[3][1])
        self.assertIn('already belongs to REG/003', statuses[4][1])

        self.assertEqual(StudentProfile.objects.get(nactvet_reg_no='REG/001').college_id, 'BPH/PST/25/0001')
        self.assertIsNone(StudentProfile.objects.get(nactvet_reg_no='REG/002').college_id)
        self.assertEqual(StudentProfile.objects.get(nactvet_reg_no='REG/003').college_id, 'BPH/PST/25/0003')

    def test_a_dry_run_reports_without_saving(self):
        response = self.rows(('REG/001', 'BPH/PST/25/0001'), dry_run=True)
        self.assertEqual(response.data['counts'], {'recorded': 1})
        self.assertIsNone(StudentProfile.objects.get(nactvet_reg_no='REG/001').college_id)

    def test_an_excel_sheet_is_read_and_problems_are_reported_by_its_own_row_numbers(self):
        book = Workbook()
        sheet = book.active
        sheet.append(['NACTVET Reg. No.', 'College ID No.'])
        sheet.append(['REG/001', 'BPH/PST/25/0001'])
        sheet.append(['REG/002', 'BPH/PST/25/0002'])
        sheet.append(['REG/404', 'BPH/PST/25/0404'])
        buffer = BytesIO()
        book.save(buffer)
        buffer.seek(0)
        buffer.name = 'ids.xlsx'

        response = self.records_client.post('/api/student-records/college-ids/', {'file': buffer}, format='multipart')

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['counts'], {'recorded': 2, 'error': 1})
        # Reported by the row number Excel shows, header included.
        self.assertEqual([row['row'] for row in response.data['rows']], [2, 3, 4])

    def test_the_accountant_cannot_record_college_ids(self):
        _, accountant = self.account('accountant', 'accountant')
        self.assertEqual(self.rows(('REG/001', 'X'), client=accountant).status_code, 403)


class LinkExistingRecordsCommandTests(ApiBase):
    def setUp(self):
        super().setUp()
        self.pst = self.programme(self.department(), levels=[self.level4, self.level5])
        self.m4 = Module.objects.create(name='Four', code='PST04101', teacher='T', class_level=self.level4, semester=self.sem)
        self.m5 = Module.objects.create(name='Five', code='PST05101', teacher='T', class_level=self.level5, semester=self.sem)
        # A level 5 student repeating a level 4 module, and a level 4 student.
        self.repeat = Student.objects.create(nactvet_reg_no='REG/001', name='Asha', module=self.m4)
        Student.objects.create(nactvet_reg_no='REG/001', name='Asha', module=self.m5)
        self.first = Student.objects.create(nactvet_reg_no='REG/002', name='Juma', module=self.m4)
        StudentResult.objects.create(student=self.first, cat1_theory=Decimal('71'))

    def snapshot(self):
        return (
            list(Student.objects.order_by('id').values_list('id', 'nactvet_reg_no', 'name', 'module_id')),
            list(StudentResult.objects.order_by('id').values_list('id', 'student_id', 'cat1_theory')),
            list(Module.objects.order_by('id').values_list('id', 'code', 'name', 'class_level_id', 'semester_id')),
        )

    def run_command(self, *args):
        out = StringIO()
        call_command('link_existing_records', '--programme', 'PST', *args, stdout=out)
        return out.getvalue()

    def test_a_dry_run_changes_nothing(self):
        before = self.snapshot()
        output = self.run_command('--dry-run')
        self.assertIn('Would link 2 module(s)', output)
        self.assertFalse(Module.objects.filter(programme__isnull=False).exists())
        self.assertFalse(SemesterRegistration.objects.exists())
        self.assertEqual(self.snapshot(), before)

    def test_it_links_modules_and_records_registrations_at_the_highest_level_only_adding(self):
        before = self.snapshot()
        self.run_command()
        second = self.run_command()

        self.assertIn('Linked 0 module(s)', second)
        self.assertIn('recorded 0 semester registration(s)', second)
        self.assertEqual(Module.objects.filter(programme=self.pst).count(), 2)
        registrations = {r.profile.nactvet_reg_no: r for r in SemesterRegistration.objects.select_related('profile')}
        self.assertEqual(registrations['REG/001'].class_level, self.level5)
        self.assertEqual(registrations['REG/002'].class_level, self.level4)
        self.assertEqual({r.kind for r in registrations.values()}, {SemesterRegistration.IMPORTED})
        self.assertEqual(self.snapshot(), before)
