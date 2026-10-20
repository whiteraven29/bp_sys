"""Students coming back from before the system — readmissions, repeaters and
postponement returns whose results the system never held — entered from last
year's papers, then taken back by admission the usual way:

  * recorded by the examination officer (or the Principal / DPARC), with the
    paper it came from; nobody else;
  * they appear in admission's "Expected back" list for the right semester;
  * a readmission sits the semester afresh and is billed as a new student;
  * a repeater sits only the modules owed, as repeat sittings, billed at the
    repeat rate and the repeaters' direct cost — and failing one again is a
    discontinuation, as for any repeat;
  * a postponement return comes back as a continuing student;
  * a student whose results the system holds, or a module not offered, is refused.
"""

from datetime import date
from decimal import Decimal

from rest_framework.test import APIClient

from . import admissions, finance, progression
from .models import (
    AcademicYear, Application, ChargeType, FeeStructure, OutstandingRepeat, Semester,
    SemesterRegistration, SemesterReview, Student, StudentCharge, StudentProfile, StudentStanding,
)
from .test_admissions import AdmissionBase, User, complete_record
from .views import set_roles


class ReturningBase(AdmissionBase):
    def setUp(self):
        super().setUp()
        self.last_year = AcademicYear.objects.create(name='2025/2026')
        for number in (1, 2):
            Semester.objects.create(academic_year=self.last_year, number=number)
        self.m1 = self.module('PST04101', self.level4, self.sem1)
        self.m2 = self.module('PST04102', self.level4, self.sem1)
        self.m3 = self.module('PST04103', self.level4, self.sem1)
        self.tuition = self.fee('Tuition Fee', '1000000')
        self.admission_fee = self.fee('Admission fee', '50000', frequency=ChargeType.ONCE,
                                      period=FeeStructure.ONCE)
        self.repeat_type = ChargeType.objects.create(
            name='Repeat Module', family=ChargeType.OTHER, declaration=ChargeType.REPEAT_MODULE,
            applies=ChargeType.ON_REQUEST)
        self.direct = ChargeType.objects.create(
            name='Repeat direct costs', family=ChargeType.DIRECT_COST, applies=ChargeType.ON_REQUEST,
            for_repeaters=True)
        for charge_type, amount in ((self.repeat_type, '200000'), (self.direct, '280000')):
            structure = FeeStructure.objects.create(
                charge_type=charge_type, programme=self.pst, class_level=self.level4,
                academic_year=self.year, amount=Decimal(amount),
                billing_period=FeeStructure.ACADEMIC_YEAR, installments=1)
            finance.set_installment_schedule(structure, [date(2026, 11, 30)])
        self.exam_user, self.exam = self.office('exam', 'exam_officer')

    def office(self, username, *roles):
        user = User.objects.create_user(username, password='pw')
        set_roles(user, list(roles), full_name=username.title())
        client = APIClient()
        client.force_authenticate(user)
        return user, client

    def record(self, client=None, **row):
        row = {'reg_no': 'NS0099/0001/2024', 'name': 'Juma Returning', 'programme_id': self.pst.id,
               'class_level_id': self.level4.id, 'return_semester': 1, **row}
        return (client or self.exam).post('/api/returning-students/', {
            'reference': 'NACTVET results, semester 1 2025/26', 'students': [row]}, format='json')

    def take_back(self, kind):
        """What admission does from the Expected-back list."""
        profile = complete_record(StudentProfile.objects.get(nactvet_reg_no='NS0099/0001/2024'))
        application = admissions.open_application(
            profile=profile, semester=self.sem1, programme=self.pst, class_level=self.level4,
            kind=kind, actor=self.officer)
        return profile, application

    def admit(self, application):
        while application.is_open:
            if application.state == Application.FINANCE and application.kind != Application.CONTINUING:
                admissions.set_residence(application, 'day', actor=self.officer)
            admissions.complete_step(application, actor=self.officer)
        return application


class RecordingTests(ReturningBase):
    def test_only_the_exam_office_principal_or_dparc_records(self):
        _, records = self.office('records', 'records_officer')
        self.assertEqual(self.record(client=records, kind='readmission').status_code, 403)
        _, dparc = self.office('dparc', 'dparc')
        self.assertTrue(self.record(client=dparc, kind='readmission').data['results'][0]['ok'])

    def test_the_paper_is_named(self):
        response = self.exam.post('/api/returning-students/', {'students': [{
            'reg_no': 'NS0099/0001/2024', 'name': 'X', 'programme_id': self.pst.id,
            'class_level_id': self.level4.id, 'return_semester': 1, 'kind': 'postponed'}]}, format='json')
        self.assertIn('which paper', response.data['results'][0]['error'])

    def test_a_student_whose_results_the_system_holds_is_refused(self):
        profile = StudentProfile.objects.create(nactvet_reg_no='NS0099/0001/2024', name='Known')
        SemesterReview.objects.create(profile=profile, semester=Semester.objects.get(
            academic_year=self.last_year, number=1), class_level=self.level4, programme=self.pst)
        result = self.record(kind='readmission').data['results'][0]
        self.assertFalse(result['ok'])
        self.assertIn('already holds', result['error'])

    def test_a_module_not_offered_is_refused(self):
        result = self.record(kind='repeat', modules='PST04101, PST04999').data['results'][0]
        self.assertFalse(result['ok'])
        self.assertIn('PST04999', result['error'])
        self.assertFalse(OutstandingRepeat.objects.exists())

    def test_rows_are_refused_one_by_one(self):
        response = self.exam.post('/api/returning-students/', {
            'reference': 'NACTVET results 2025/26', 'students': [
                {'reg_no': 'NS0099/0001/2024', 'name': 'Good', 'programme_id': self.pst.id,
                 'class_level_id': self.level4.id, 'return_semester': 1, 'kind': 'postponed'},
                {'reg_no': '', 'name': 'No number', 'programme_id': self.pst.id,
                 'class_level_id': self.level4.id, 'return_semester': 1, 'kind': 'postponed'}]},
            format='json')
        self.assertEqual((response.data['recorded'], response.data['refused']), (1, 1))


class ReadmissionTests(ReturningBase):
    def test_a_readmission_sits_afresh_and_is_billed_as_new(self):
        self.assertTrue(self.record(kind='readmission').data['results'][0]['ok'])
        due = admissions.due_back(self.sem1)
        self.assertEqual([(s.profile.nactvet_reg_no, s.status) for s in due],
                         [('NS0099/0001/2024', StudentStanding.FAILED)])

        profile, application = self.take_back(Application.READMISSION)
        billed = set(StudentCharge.objects.filter(profile=profile).values_list('charge_type__name', flat=True))
        self.assertEqual(billed, {'Tuition Fee', 'Admission fee'})       # a new student's bill
        self.admit(application)

        sittings = Student.objects.filter(profile=profile, module__semester=self.sem1)
        self.assertEqual(sorted(s.module.code for s in sittings), ['PST04101', 'PST04102', 'PST04103'])
        self.assertEqual({s.attempt for s in sittings}, {Student.NORMAL})
        self.assertEqual(admissions.due_back(self.sem1), [])


class RepeaterTests(ReturningBase):
    def test_a_repeater_sits_the_modules_owed_and_pays_the_repeat_rates(self):
        recorded = self.record(kind='repeat', modules='pst04101 PST04103').data['results'][0]
        self.assertTrue(recorded['ok'], recorded)
        owed = OutstandingRepeat.objects.filter(profile__nactvet_reg_no='NS0099/0001/2024')
        self.assertEqual(sorted(owed.values_list('module_code', flat=True)), ['PST04101', 'PST04103'])
        self.assertTrue(all('NACTVET results' in row.note for row in owed))
        self.assertEqual([s.status for s in admissions.due_back(self.sem1)], [StudentStanding.REPEATING])

        profile, application = self.take_back(Application.CONTINUING)
        billed = sorted(StudentCharge.objects.filter(profile=profile).values_list('amount', flat=True))
        self.assertEqual(billed, [Decimal('200000.00'), Decimal('200000.00'), Decimal('280000.00')])
        self.admit(application)

        registration = SemesterRegistration.objects.get(profile=profile, semester=self.sem1)
        self.assertEqual(registration.kind, SemesterRegistration.REPEATING)
        sittings = Student.objects.filter(profile=profile, module__semester=self.sem1)
        self.assertEqual(sorted(s.module.code for s in sittings), ['PST04101', 'PST04103'])
        self.assertEqual({s.attempt for s in sittings}, {Student.REPEAT})

    def test_failing_a_repeat_from_the_paper_record_is_a_discontinuation(self):
        self.record(kind='repeat', modules='PST04101')
        profile, application = self.take_back(Application.CONTINUING)
        self.admit(application)
        exam = APIClient()
        exam.force_authenticate(self.exam_user)
        exam.post('/api/results/authority-grades/', {'semester_id': self.sem1.id, 'rows': [
            {'reg_no': 'NS0099/0001/2024', 'module_code': 'PST04101', 'grade': 'C*'}]}, format='json')

        self.assertEqual(progression.evaluate(profile, self.sem1)['proposed'], SemesterReview.DISCONTINUED)


class PostponementReturnTests(ReturningBase):
    def test_a_postponement_return_comes_back_as_continuing(self):
        self.record(kind='postponed', reg_no='NS0099/0001/2024', return_semester=1)
        self.assertEqual([s.status for s in admissions.due_back(self.sem1)], [StudentStanding.POSTPONED])
        profile, application = self.take_back(Application.CONTINUING)
        billed = set(StudentCharge.objects.filter(profile=profile).values_list('charge_type__name', flat=True))
        self.assertEqual(billed, {'Tuition Fee'})          # continuing: no one-time charges
        self.admit(application)
        self.assertEqual(Student.objects.filter(profile=profile, module__semester=self.sem1).count(), 3)

    def test_due_back_only_in_the_semester_they_return_to(self):
        self.record(kind='postponed', return_semester=2)
        self.assertEqual(admissions.due_back(self.sem1), [])
        self.assertEqual(len(admissions.due_back(self.sem2)), 1)


class PostponedRepeaterTests(ReturningBase):
    """Said 12 October 2026: some who postponed were repeating modules. They come
    back as repeaters for those modules — not a full year."""

    def test_a_postponement_from_paper_can_carry_the_modules_owed(self):
        recorded = self.record(kind='postponed', modules='PST04102').data['results'][0]
        self.assertTrue(recorded['ok'], recorded)
        due = admissions.due_back(self.sem1)
        self.assertEqual([s.status for s in due], [StudentStanding.POSTPONED])

        profile, application = self.take_back(Application.CONTINUING)
        billed = sorted(StudentCharge.objects.filter(profile=profile).values_list('amount', flat=True))
        self.assertEqual(billed, [Decimal('200000.00'), Decimal('280000.00')])
        self.admit(application)
        registration = SemesterRegistration.objects.get(profile=profile, semester=self.sem1)
        self.assertEqual(registration.kind, SemesterRegistration.REPEATING)
        sittings = Student.objects.filter(profile=profile, module__semester=self.sem1)
        self.assertEqual([(s.module.code, s.attempt) for s in sittings], [('PST04102', Student.REPEAT)])

    def test_the_systems_own_postponed_repeater_comes_back_as_a_repeater(self):
        # What a postponement leaves behind for a repeater (lifecycle._postpone):
        # standing "postponed", the module still owed.
        profile = StudentProfile.objects.create(nactvet_reg_no='NS0099/0001/2024', name='Was Repeating')
        OutstandingRepeat.objects.create(
            profile=profile, module_code='PST04101', module_name='PST04101', class_level=self.level4,
            semester_number=1, origin_semester=Semester.objects.get(academic_year=self.last_year, number=1))
        progression.set_standing(profile, StudentStanding.POSTPONED, class_level=self.level4,
                                 programme=self.pst, return_year=self.year, return_semester_number=1)

        self.assertTrue(admissions.is_repeat_only(profile))
        _, application = self.take_back(Application.CONTINUING)
        billed = sorted(StudentCharge.objects.filter(profile=profile).values_list('amount', flat=True))
        self.assertEqual(billed, [Decimal('200000.00'), Decimal('280000.00')])

    def test_a_postponement_without_modules_is_a_full_year_still(self):
        self.record(kind='postponed')
        profile = StudentProfile.objects.get(nactvet_reg_no='NS0099/0001/2024')
        self.assertFalse(admissions.is_repeat_only(profile))

    def test_a_readmission_carries_no_modules(self):
        result = self.record(kind='readmission', modules='PST04101').data['results'][0]
        self.assertFalse(result['ok'])
        self.assertIn('afresh', result['error'])


class StartingAtLevelFiveOrSixTests(ReturningBase):
    """A new student who starts at level 5 or 6 (they hold the level below from
    elsewhere): a first-year's bill — the one-time charges too, as the fee form
    says for "new entrants at 5 and 6" — that level's modules, as first sittings."""

    def start_new(self, level, reg_no):
        modules = [self.module(f'PST0{level.order}10{n}', level, self.sem1) for n in (1, 2)]
        for name, amount, extra in (('Tuition Fee', '1500000', {}),
                                    ('Admission fee', '50000', {'frequency': ChargeType.ONCE,
                                                                'period': FeeStructure.ONCE})):
            charge_type = ChargeType.objects.get(name=name)
            structure = FeeStructure.objects.create(
                charge_type=charge_type, programme=self.pst, class_level=level, academic_year=self.year,
                amount=Decimal(amount), billing_period=extra.get('period', FeeStructure.ACADEMIC_YEAR),
                installments=1)
            finance.set_installment_schedule(structure, [date(2026, 11, 30)])
        profile = self.applicant(reg_no, 'Direct Entrant')
        application = admissions.open_application(
            profile=profile, semester=self.sem1, programme=self.pst, class_level=level,
            kind=Application.NEW, actor=self.officer)
        self.admit(application)
        return profile, modules

    def test_a_new_student_starts_at_level_five(self):
        profile, modules = self.start_new(self.level5, 'NS5555/0001/2022')
        billed = sorted(StudentCharge.objects.filter(profile=profile).values_list('charge_type__name', 'amount'))
        self.assertEqual(billed, [('Admission fee', Decimal('50000.00')), ('Tuition Fee', Decimal('1500000.00'))])
        registration = SemesterRegistration.objects.get(profile=profile, semester=self.sem1)
        self.assertEqual((registration.kind, registration.class_level), (SemesterRegistration.NEW, self.level5))
        sittings = Student.objects.filter(profile=profile)
        self.assertEqual(sorted(s.module.code for s in sittings), sorted(m.code for m in modules))
        self.assertEqual({s.attempt for s in sittings}, {Student.NORMAL})
        self.assertEqual(profile.standing.class_level, self.level5)

    def test_a_new_student_starts_at_level_six(self):
        level6 = self.level5.__class__.objects.create(name='NTA Level 6', order=6)
        self.pst.levels.add(level6)
        profile, modules = self.start_new(level6, 'NS6666/0001/2021')
        self.assertEqual(SemesterRegistration.objects.get(profile=profile).class_level, level6)
        self.assertEqual(Student.objects.filter(profile=profile).count(), 2)
        self.assertTrue(StudentCharge.objects.filter(profile=profile, charge_type__name='Admission fee').exists())


class RepeatAcrossAPostponementTests(ReturningBase):
    """Said 12 October 2026: a student owes a 2024/25 module, postponed in
    2025/26, and comes back in 2026/27. They come back as a repeater for that
    module; the repeat sitting is on the repeaters' sheet; passing it completes
    their 2024/25 semester, which then reads cleared."""

    def test_the_repeat_completes_the_semester_two_years_back(self):
        from .models import SemesterReview, StudentResult
        from .grading import parse_authority_grade
        older_year = AcademicYear.objects.create(name='2024/2025')
        older = Semester.objects.create(academic_year=older_year, number=1)
        profile = StudentProfile.objects.create(nactvet_reg_no='NS0099/0001/2024', name='Long Way Back')
        failed = None
        for code, grade in (('PST04101', 'A'), ('PST04102', 'B'), ('PST04103', 'D')):
            module = self.module(code, self.level4, older)
            sitting = Student.objects.create(nactvet_reg_no=profile.nactvet_reg_no, name=profile.name,
                                             profile=profile, module=module)
            parsed = parse_authority_grade(grade, self.level4)
            StudentResult.objects.create(student=sitting, authority_grade=parsed['raw'],
                                         authority_status=parsed['status'], final_approved=True)
            if code == 'PST04103':
                failed = sitting
        OutstandingRepeat.objects.create(
            profile=profile, module_code='PST04103', module_name='PST04103', class_level=self.level4,
            semester_number=1, origin_semester=older, origin_enrollment=failed)
        # 2025/26: they postponed (lifecycle._postpone leaves exactly this).
        progression.set_standing(profile, StudentStanding.POSTPONED, class_level=self.level4,
                                 programme=self.pst, return_year=self.year, return_semester_number=1)

        # 2026/27: back through admission, as a repeater for the module owed.
        self.assertEqual([s.profile_id for s in admissions.due_back(self.sem1)], [profile.id])
        _, application = self.take_back(Application.CONTINUING)
        billed = sorted(StudentCharge.objects.filter(profile=profile, academic_year=self.year)
                        .values_list('amount', flat=True))
        self.assertEqual(billed, [Decimal('200000.00'), Decimal('280000.00')])
        self.admit(application)
        repeat = Student.objects.get(profile=profile, module__semester=self.sem1)
        self.assertEqual((repeat.module.code, repeat.attempt, repeat.repeat_of_id),
                         ('PST04103', Student.REPEAT, failed.id))

        # The repeat result: only on the repeaters' sheet, completing 2024/25.
        self.exam.post('/api/results/authority-grades/', {'semester_id': self.sem1.id, 'rows': [
            {'reg_no': profile.nactvet_reg_no, 'module_code': 'PST04103', 'grade': 'C'}]}, format='json')
        sheet = self.exam.get(f'/api/results-summary/?semester_id={self.sem1.id}'
                              f'&class_level_id={self.level4.id}&sheet=repeaters').data['students']
        self.assertEqual(len(sheet), 1)
        self.assertEqual((sheet[0]['origin'], sheet[0]['remark'], sheet[0]['gpa']),
                         (older.label, 'PASS', 3.0))                    # (4 + 3 + 2) / 3
        continuing = self.exam.get(f'/api/results-summary/?semester_id={self.sem1.id}'
                                   f'&class_level_id={self.level4.id}').data['students']
        self.assertEqual(continuing, [])

        # Their review clears the module owed; the 2024/25 failure stays on record.
        review = progression.build_reviews(self.sem1, actor=self.exam_user)
        mine = [r for r in review if r.profile_id == profile.id][0]
        self.assertEqual(mine.proposed, SemesterReview.CLEAR)
        progression.confirm_review(mine, mine.proposed, actor=self.exam_user)
        self.assertEqual(OutstandingRepeat.objects.get(profile=profile).status, OutstandingRepeat.PASSED)
        self.assertEqual(StudentResult.objects.get(student=failed).authority_grade, 'D')

        # The portal reads 2024/25 complete.
        repeat.set_portal_pin('Portal#2026', require_change=False)
        repeat.save()
        session = self.client.session
        session['student_id'] = repeat.id
        session.save()
        statements = self.client.get('/student-dashboard/').context['result_statements']
        self.assertEqual([s['label'] for s in statements], [older.label])
        self.assertEqual(statements[0]['comment'], 'PASS')
