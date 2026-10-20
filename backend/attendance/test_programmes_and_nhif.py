"""From 20 October 2026:

  * PST is one programme across Levels 4–6; three set up one per level are
    merged (manage.py merge_programmes): a dry run changes nothing; applied,
    students, modules, applications and fees move, the levels join, and a
    Level 4 student moves up to Level 5 at the year end instead of finishing;
  * names are taken in their parts — first, middle, last — and the whole name
    follows; a name written whole is split;
  * an item for new students only (uniforms, the clinical coat) is asked of a
    first year at any level, never of a continuing student; Has it takes the
    charge off before the invoice;
  * NHIF's two sheets are filled from the records, for those paying the
    college's medical fee;
  * a new student is taken on into a year of the course — first, second,
    third — and the year gives the level; the desks name them "Second year
    (new) — joins at NTA Level 5", not to be taken for a continuing student.
"""

from datetime import date
from decimal import Decimal
from io import StringIO

from django.core.management import call_command

from . import admissions, finance, progression
from .models import (
    AdmissionRequirement, Application, ChargeType, ClassLevel, FeeStructure, Module, Programme,
    RequirementCheck, SemesterRegistration, StudentCharge, StudentProfile,
)
from .test_admissions import OfficeClients, complete_record


class MergeProgrammesTests(OfficeClients):
    def setUp(self):
        super().setUp()
        level6 = ClassLevel.objects.create(name='NTA Level 6', order=6)
        self.btc = Programme.objects.create(department=self.department, name='Basic Technician Certificate',
                                            code='BTC')
        self.btc.levels.set([self.level4])
        self.od = Programme.objects.create(department=self.department, name='Ordinary Diploma', code='OD')
        self.od.levels.set([level6])
        self.btc_module = Module.objects.create(name='M', code='PST04199', teacher='T', class_level=self.level4,
                                                semester=self.sem1, programme=self.btc, credits=3)
        self.student = complete_record(StudentProfile.objects.create(nactvet_reg_no='NS0001/0001/2026', name='Asha'))
        SemesterRegistration.objects.create(profile=self.student, semester=self.sem1, programme=self.btc,
                                            class_level=self.level4, kind=SemesterRegistration.NEW)
        self.newcomer = self.applicant('NACTVET/PENDING/060', 'Juma Level Six')
        self.application = admissions.open_application(profile=self.newcomer, semester=self.sem1, programme=self.od,
                                                       class_level=level6, kind=Application.NEW, actor=self.officer)
        fee = ChargeType.objects.create(name='Programme fee', family=ChargeType.FEE)
        FeeStructure.objects.create(charge_type=fee, programme=self.btc, class_level=self.level4,
                                    academic_year=self.year, amount=Decimal('100'),
                                    billing_period=FeeStructure.ACADEMIC_YEAR, installments=1)

    def merge(self, *extra):
        out = StringIO()
        call_command('merge_programmes', '--into', 'OD', '--from', 'BTC', '--code', 'PST2',
                     '--name', 'Ordinary Diploma in PST', *extra, stdout=out)
        return out.getvalue()

    def test_a_dry_run_changes_nothing(self):
        report = self.merge()
        self.assertIn('DRY RUN', report)
        self.assertIn('1 registrations', report)
        self.assertTrue(Programme.objects.filter(code='BTC').exists())
        self.assertEqual(SemesterRegistration.objects.get(profile=self.student).programme, self.btc)

    def test_applied_everything_moves_and_the_levels_join(self):
        self.merge('--apply')
        merged = Programme.objects.get(code='PST2')
        self.assertEqual(merged.name, 'Ordinary Diploma in PST')
        self.assertFalse(Programme.objects.filter(code__in=['BTC', 'OD']).exists())
        self.assertEqual(SemesterRegistration.objects.get(profile=self.student).programme, merged)
        self.assertEqual(Application.objects.get(pk=self.application.pk).programme, merged)
        self.assertEqual(Module.objects.get(pk=self.btc_module.pk).programme, merged)
        self.assertEqual(FeeStructure.objects.get(charge_type__name='Programme fee').programme, merged)
        self.assertEqual(sorted(merged.levels.values_list('order', flat=True)), [4, 6])

    def test_after_the_merge_level_four_moves_up_not_out(self):
        self.merge('--apply')
        merged = Programme.objects.get(code='PST2')
        merged.levels.add(self.level5)
        cache = {}
        self.assertEqual(progression._next_level_cached(merged, self.level4, cache), self.level5)
        # The three-programme set-up finished them at Level 4:
        self.assertIsNone(progression._next_level_cached(self.pst_single_level(), self.level4, {}))

    def pst_single_level(self):
        alone = Programme.objects.create(department=self.department, name='Level 4 only', code='L4')
        alone.levels.set([self.level4])
        return alone

    def test_issued_college_ids_are_kept(self):
        self.student.college_id = 'BPH/BTC/2026/001'
        self.student.save(update_fields=['college_id'])
        self.merge('--apply')
        self.student.refresh_from_db()
        self.assertEqual(self.student.college_id, 'BPH/BTC/2026/001')

    def test_into_an_existing_pst_programme(self):
        """The server's case: PST04/05/06 merged into a programme already coded PST."""
        pst = Programme.objects.create(department=self.department, name='Old PST',
                                       code='PSTX', is_active=False)
        FeeStructure.objects.create(charge_type=ChargeType.objects.get(name='Programme fee'), programme=pst,
                                    class_level=self.level4, academic_year=self.year, amount=Decimal('90'),
                                    billing_period=FeeStructure.ACADEMIC_YEAR, installments=1)
        out = StringIO()
        call_command('merge_programmes', '--into', 'PSTX', '--from', 'BTC', 'OD',
                     '--name', 'Ordinary Diploma in PST', stdout=out)
        report = out.getvalue()
        self.assertIn('NOT active', report)
        self.assertIn('BTC has 100, PSTX has 90', report)              # a price clash is shown first
        call_command('merge_programmes', '--into', 'PSTX', '--from', 'BTC', 'OD', '--apply', stdout=StringIO())
        pst.refresh_from_db()
        self.assertTrue(pst.is_active)                                 # admission can pick it
        self.assertEqual(sorted(pst.levels.values_list('order', flat=True)), [4, 6])
        self.assertEqual(SemesterRegistration.objects.get(profile=self.student).programme, pst)

    def test_admitted_without_modules_shows_at_the_password_desk_and_the_merge_enrolls_them(self):
        """The server's case: admitted under PST04 while the modules sat under PST — no
        enrollment, so no password to give and nothing at the desk said why."""
        pst = Programme.objects.create(department=self.department, name='Old PST', code='PSTX')
        pst.levels.set([self.level4])
        Module.objects.filter(pk=self.btc_module.pk).update(programme=pst)      # modules under PST
        ict = self.office_user('ict', 'ict_officer')
        desk = ict.get('/api/student-passwords/issue/').data
        self.assertEqual(desk['count'], 0)
        self.assertEqual([(r['name'], r['programme']) for r in desk['no_modules']], [('Asha', 'BTC')])

        report = StringIO()
        call_command('merge_programmes', '--into', 'PSTX', '--from', 'BTC', '--apply', stdout=report)
        self.assertIn('enrolled in their modules: Asha (1)', report.getvalue())
        desk = ict.get('/api/student-passwords/issue/').data
        self.assertEqual((desk['count'], desk['no_modules']), (1, []))
        again = StringIO()
        call_command('merge_programmes', '--into', 'PSTX', '--from', 'OD', '--apply', stdout=again)
        self.assertNotIn('Asha', again.getvalue())                               # never twice

    def office_user(self, username, role):
        from django.contrib.auth import get_user_model
        from rest_framework.test import APIClient
        from .views import set_roles
        user = get_user_model().objects.create_user(username, password='pw')
        set_roles(user, [role], full_name=username.title())
        client = APIClient()
        client.force_login(user)
        return client


class NamesTests(OfficeClients):
    def test_admission_takes_the_name_in_parts(self):
        made = self.admission.post('/api/applications/capture/', {
            'kind': 'new', 'first_name': 'Neema', 'middle_name': 'Salmu', 'last_name': 'Iddy',
            'semester_id': self.sem1.id, 'programme_id': self.pst.id, 'class_level_id': self.level4.id},
            format='json')
        self.assertEqual(made.status_code, 201, made.data)
        profile = Application.objects.get(pk=made.data['id']).profile
        self.assertEqual((profile.first_name, profile.middle_name, profile.last_name, profile.name),
                         ('Neema', 'Salmu', 'Iddy', 'Neema Salmu Iddy'))

    def test_first_and_last_are_needed(self):
        refused = self.admission.post('/api/applications/capture/', {
            'kind': 'new', 'first_name': 'Neema', 'semester_id': self.sem1.id, 'programme_id': self.pst.id,
            'class_level_id': self.level4.id}, format='json')
        self.assertEqual(refused.status_code, 400)

    def test_a_whole_name_is_split_and_records_corrects_the_parts(self):
        profile = StudentProfile.objects.create(nactvet_reg_no='X/1', name='Asha Mohamedi Juma Ally')
        self.assertEqual((profile.first_name, profile.middle_name, profile.last_name),
                         ('Asha', 'Mohamedi Juma', 'Ally'))
        admissions.record_details(profile, first_name='Asha', middle_name='Mohamedi', last_name='Juma Ally',
                                  actor=self.officer)
        profile.refresh_from_db()
        self.assertEqual(profile.name, 'Asha Mohamedi Juma Ally')
        self.assertEqual(profile.last_name, 'Juma Ally')


class NewStudentItemsAndNhifTests(OfficeClients):
    def setUp(self):
        super().setUp()
        self.uniform = self.fee('Uniforms', '100000', level=self.level5, frequency=ChargeType.ONCE,
                                period=FeeStructure.ONCE)
        self.medical = self.fee('Medical fees (Health Insurance)', '60000', level=self.level5)
        self.item = AdmissionRequirement.objects.create(name='Uniforms', charge_type=self.uniform,
                                                        new_students_only=True, frequency=AdmissionRequirement.ONCE)
        self.direct = self.applicant('NACTVET/PENDING/070', 'Grace Direct Entry')
        self.direct.national_id = '20070314-12345-00001-21'
        self.direct.marital_status = 'single'
        self.direct.save(update_fields=['national_id', 'marital_status'])
        self.application = admissions.open_application(profile=self.direct, semester=self.sem1, programme=self.pst,
                                                       class_level=self.level5, kind=Application.NEW,
                                                       actor=self.officer)

    def test_a_level_five_newcomer_is_asked_and_has_it_takes_the_uniform_off(self):
        self.assertIn('Uniforms', [r.name for r in admissions.requirements_for(self.application)])
        while self.application.state != Application.FINANCE:
            admissions.complete_step(self.application, actor=self.officer)
        charge = StudentCharge.objects.get(profile=self.direct, charge_type=self.uniform)
        admissions.record_requirement(self.application, self.item, RequirementCheck.HAS_IT, actor=self.officer)
        charge.refresh_from_db()
        self.assertEqual(finance.charge_balance(charge), Decimal('0'))

    def test_a_continuing_student_is_not_asked(self):
        continuing = complete_record(StudentProfile.objects.create(nactvet_reg_no='NS0002/0001/2025', name='Old Hand'))
        application = admissions.open_application(profile=continuing, semester=self.sem1, programme=self.pst,
                                                  class_level=self.level5, kind=Application.CONTINUING,
                                                  actor=self.officer)
        self.assertNotIn('Uniforms', [r.name for r in admissions.requirements_for(application)])

    def admit(self):
        while self.application.is_open:
            if self.application.state == Application.FINANCE:
                admissions.set_residence(self.application, 'day', actor=self.officer)
                for charge in StudentCharge.objects.filter(profile=self.direct):
                    finance.waive_charge(charge, charge.amount, 'test: let through', actor=self.officer) \
                        if charge.charge_type != self.medical else None
                finance.record_payment(self.direct, Decimal('60000'), date.today(), recorded_by=self.officer,
                                       bank_reference='NHIF-T')
            admissions.complete_step(self.application, actor=self.officer)

    def test_the_new_students_sheet_is_filled_from_the_record(self):
        self.admit()
        data = self.records.get(f'/api/nhif-sheet/?academic_year_id={self.year.id}&sheet=new').data
        self.assertEqual(len(data['rows']), 1, data)
        row = data['rows'][0]['row']
        self.assertEqual((row['FirstName'], row['MiddleName'], row['LastName']), ('Grace', 'Direct', 'Entry'))
        self.assertEqual(row['DateOfBirth'], '12/04/2006')
        self.assertEqual((row['Gender'], row['MaritalStatus']), ('Female', 'Single'))
        self.assertEqual((row['YearOfStudy'], row['CourseDuration']), (2, 2))     # Level 5 of 4–5
        self.assertEqual(row['CourseName'], self.pst.name)
        self.assertTrue(row['AdmissionNo'])
        self.assertEqual(self.records.get(f'/api/nhif-sheet/?sheet=continuing').data['rows'], [])

    def test_own_nhif_card_leaves_them_off(self):
        self.admit()
        for charge in StudentCharge.objects.filter(profile=self.direct, charge_type=self.medical):
            finance.waive_charge(charge, charge.amount, 'Own NHIF card', actor=self.officer)
        data = self.records.get(f'/api/nhif-sheet/?academic_year_id={self.year.id}&sheet=new').data
        self.assertEqual(data['rows'], [])

    def test_only_the_record_offices_build_it(self):
        self.assertEqual(self.accounts.get('/api/nhif-sheet/').status_code, 403)


class JoiningYearTests(OfficeClients):
    def capture(self, **extra):
        return self.admission.post('/api/applications/capture/', {
            'kind': 'new', 'first_name': 'Baraka', 'last_name': 'Mussa', 'semester_id': self.sem1.id,
            'programme_id': self.pst.id, **extra}, format='json')

    def test_second_year_joins_at_the_second_level_as_a_new_student(self):
        made = self.capture(year_of_study=2, class_level_id=self.level4.id)    # the year decides
        self.assertEqual(made.status_code, 201, made.data)
        application = Application.objects.get(pk=made.data['id'])
        self.assertEqual((application.kind, application.class_level), (Application.NEW, self.level5))
        self.assertEqual(application.state, Application.INTAKE)             # the new students' route
        self.assertEqual(made.data['kind_label'], 'Second year (new) — joins at NTA Level 5')

    def test_first_year_reads_as_before(self):
        made = self.capture(year_of_study=1)
        self.assertEqual(made.data['kind_label'], 'First year')
        self.assertEqual(Application.objects.get(pk=made.data['id']).class_level, self.level4)

    def test_no_year_past_the_programme(self):
        refused = self.capture(year_of_study=3)                            # PST here runs Levels 4–5
        self.assertEqual(refused.status_code, 400)
        self.assertIn('no year 3', refused.data['detail'])

    def test_a_level_the_programme_does_not_run_is_refused(self):
        level6 = ClassLevel.objects.create(name='NTA Level 6', order=6)
        refused = self.capture(class_level_id=level6.id)
        self.assertEqual(refused.status_code, 400)
        self.assertIn('does not run', refused.data['detail'])

    def test_continuing_and_readmission_are_unchanged(self):
        old = complete_record(StudentProfile.objects.create(nactvet_reg_no='NS0003/0001/2025', name='Old Hand'))
        application = admissions.open_application(profile=old, semester=self.sem1, programme=self.pst,
                                                  class_level=self.level5, kind=Application.CONTINUING,
                                                  actor=self.officer)
        self.assertEqual(application.kind_label, 'Continuing student')
        listed = self.admission.get('/api/applications/').data
        rows = listed.get('results', listed) if isinstance(listed, dict) else listed
        self.assertEqual({r['kind_label'] for r in rows if r['id'] == application.id}, {'Continuing student'})


class ModulesWithoutProgrammeTests(OfficeClients):
    """The server on 20 October 2026: Level 4's modules had no programme and
    Level 5 had none at all; two students admitted under PST were in no module,
    so the password desk had no one to give a password to."""

    def setUp(self):
        super().setUp()
        self.loose = Module.objects.create(name='Loose', code='PST04150', teacher='T', class_level=self.level4,
                                           semester=self.sem1, programme=None, credits=3)
        self.student = complete_record(StudentProfile.objects.create(nactvet_reg_no='NS0009/0001/2026',
                                                                     name='Anitha Steven'))
        self.registration = SemesterRegistration.objects.create(
            profile=self.student, semester=self.sem1, programme=self.pst, class_level=self.level4,
            kind=SemesterRegistration.NEW)

    def enrolled_codes(self, profile=None):
        from .models import Student
        return sorted(Student.objects.filter(profile=profile or self.student).values_list('module__code', flat=True))

    def test_admission_takes_a_module_with_no_programme(self):
        progression.enroll_registration(self.registration)
        self.assertIn('PST04150', self.enrolled_codes())

    def test_the_command_catches_up_who_was_admitted_before(self):
        dry = StringIO()
        call_command('enroll_registered', stdout=dry)
        self.assertIn('Anitha Steven', dry.getvalue())
        self.assertEqual(self.enrolled_codes(), [])                       # a dry run writes nothing
        call_command('enroll_registered', '--apply', stdout=StringIO())
        self.assertIn('PST04150', self.enrolled_codes())
        again = StringIO()
        call_command('enroll_registered', '--apply', stdout=again)
        self.assertIn('Nobody was missing', again.getvalue())             # never twice

    def test_giving_a_module_its_level_enrolls_the_class(self):
        level5_student = complete_record(StudentProfile.objects.create(nactvet_reg_no='NS0009/0002/2026',
                                                                       name='Hanna Margwe'))
        SemesterRegistration.objects.create(profile=level5_student, semester=self.sem1, programme=self.pst,
                                            class_level=self.level5, kind=SemesterRegistration.NEW)
        made = self.principal.patch(f'/api/modules/{self.loose.id}/',
                                    {'class_level': self.level5.id, 'programme': self.pst.id}, format='json')
        self.assertEqual(made.status_code, 200, made.data)
        self.assertEqual(self.enrolled_codes(level5_student), ['PST04150'])

    def test_a_new_level_five_module_enrolls_her(self):
        level5_student = complete_record(StudentProfile.objects.create(nactvet_reg_no='NS0009/0003/2026',
                                                                       name='Hanna Two'))
        SemesterRegistration.objects.create(profile=level5_student, semester=self.sem1, programme=self.pst,
                                            class_level=self.level5, kind=SemesterRegistration.NEW)
        made = self.principal.post('/api/modules/', {
            'name': 'Pharmaceutics I', 'code': 'PST05101', 'teacher': 'T', 'class_level': self.level5.id,
            'semester': self.sem1.id, 'programme': self.pst.id, 'credits': 4}, format='json')
        self.assertEqual(made.status_code, 201, made.data)
        self.assertEqual(self.enrolled_codes(level5_student), ['PST05101'])
