"""A student's status (said 19 October 2026):

  * failing academically (GPA below 2.0, a repeat failed again) is FAIL and is
    readmitted as before; "discontinued" is the Principal's disciplinary
    decision, after which the student applies again as new;
  * deceased and absconded (ABS) are recorded by records or admission;
    suspension, termination and discontinuation by the Principal, DPARC told;
  * deceased and terminated are never admitted again, and their portal closes;
  * absconded may come back only after two years, as new; a postponement over
    two years, a disciplinary discontinuation and a Level 5 finish come back
    as new too — one-time charges again, the same record and college ID;
  * a suspension keeps the place and the fees, stops examinations, and ends by
    itself on its end date.
"""

from datetime import date, timedelta
from decimal import Decimal
from unittest import mock

from django.utils import timezone

from . import admissions, finance, lifecycle, progression
from .models import (
    Application, ChargeType, FeeStructure, Notification, SemesterRegistration, Student, StudentCharge,
    StudentStanding,
)
from .test_admissions import OfficeClients, complete_record


class StatusBase(OfficeClients):
    def setUp(self):
        super().setUp()
        self.admission_fee = self.fee('Admission fee', '50000', frequency=ChargeType.ONCE,
                                      period=FeeStructure.ONCE)
        self.profile = self.applicant('NS0100/0001/2024', 'Juma Status')
        self.profile.college_id = 'BPH/PST/2024/0100'
        self.profile.save(update_fields=['college_id'])

    def stand(self, status, since=None, until=None, level=None):
        progression.set_standing(self.profile, status, class_level=level or self.level4,
                                 programme=self.pst, since=since, until=until, reason='test')
        self.profile.refresh_from_db()

    def record(self, client, status, **extra):
        return client.post('/api/student-statuses/', {
            'profile_id': self.profile.id, 'status': status, 'reason': 'As the letter says', **extra}, format='json')


class WhoRecordsTests(StatusBase):
    def test_records_and_admission_record_deceased_and_absconded_only(self):
        self.assertEqual(self.record(self.records, 'absconded').status_code, 201)
        for status in ('suspended', 'terminated', 'discontinued'):
            self.assertEqual(self.record(self.admission, status, until='2027-01-31').status_code, 403, status)
        self.assertEqual(self.record(self.accounts, 'deceased').status_code, 403)

    def test_the_principal_decides_and_dparc_is_told(self):
        made = self.record(self.principal, 'terminated')
        self.assertEqual(made.status_code, 201, made.data)
        self.assertEqual(StudentStanding.objects.get(profile=self.profile).status, StudentStanding.TERMINATED)
        self.assertTrue(Notification.objects.filter(kind=Notification.STATUS_RECORDED).exists())

    def test_a_reason_and_a_real_date(self):
        refused = self.principal.post('/api/student-statuses/', {
            'profile_id': self.profile.id, 'status': 'deceased'}, format='json')
        self.assertEqual(refused.status_code, 400)
        future = self.record(self.principal, 'deceased', on_date=str(date.today() + timedelta(days=3)))
        self.assertEqual(future.status_code, 400)
        no_end = self.record(self.principal, 'suspended')
        self.assertEqual(no_end.status_code, 400)


class NeverAgainTests(StatusBase):
    def test_deceased_and_terminated_are_never_admitted(self):
        for status in (StudentStanding.DECEASED, StudentStanding.TERMINATED):
            self.stand(status)
            with self.assertRaises(admissions.AdmissionError):
                admissions.open_application(profile=self.profile, semester=self.sem1, programme=self.pst,
                                            class_level=self.level4, kind=Application.NEW, actor=self.officer)

    def test_recording_takes_them_off_the_class(self):
        registration = SemesterRegistration.objects.create(
            profile=self.profile, semester=self.sem1, programme=self.pst, class_level=self.level4,
            kind=SemesterRegistration.NEW)
        enrollment = Student.objects.create(nactvet_reg_no=self.profile.nactvet_reg_no, name=self.profile.name,
                                            profile=self.profile, module=self.module('PST04102', self.level4,
                                                                                     self.sem1))
        lifecycle.record_status(self.profile, StudentStanding.DECEASED, reason='Family letter', actor=self.officer)
        registration.refresh_from_db()
        enrollment.refresh_from_db()
        self.assertEqual(registration.status, SemesterRegistration.CANCELLED)
        self.assertIsNotNone(enrollment.withdrawn_at)
        self.assertTrue(StudentStanding.objects.get(profile=self.profile).blocks_portal)


class ComingBackAsNewTests(StatusBase):
    def back(self, kind=Application.READMISSION):
        return admissions.open_application(profile=self.profile, semester=self.sem1, programme=self.pst,
                                           class_level=self.level4, kind=kind, actor=self.officer)

    def test_absconded_not_before_two_years(self):
        self.stand(StudentStanding.ABSCONDED, since=timezone.localdate() - timedelta(days=300))
        with self.assertRaisesMessage(admissions.AdmissionError, 'may come back'):
            self.back()

    def test_absconded_after_two_years_comes_as_new_with_one_time_charges(self):
        StudentCharge.objects.create(profile=self.profile, charge_type=self.admission_fee,
                                     academic_year=self.year, amount=Decimal('50000'),
                                     due_date=date(2023, 10, 1), installment_number=1)
        StudentCharge.objects.filter(profile=self.profile).update(academic_year=self._old_year())
        self.stand(StudentStanding.ABSCONDED, since=timezone.localdate() - timedelta(days=800))
        application = self.back(kind=Application.CONTINUING)
        self.assertEqual(application.kind, Application.NEW)
        self.assertIn('absconded more than two years ago', application.note)
        # Through intake and records, then the bill: the one-time charge again.
        complete_record(self.profile)
        while application.state != Application.FINANCE:
            admissions.complete_step(application, actor=self.officer)
        self.assertTrue(StudentCharge.objects.filter(profile=self.profile, charge_type=self.admission_fee,
                                                     academic_year=self.year).exists())

    def _old_year(self):
        from .models import AcademicYear
        return AcademicYear.objects.create(name='2023/2024')

    def test_a_disciplinary_discontinuation_comes_as_new_keeping_the_id(self):
        self.stand(StudentStanding.DISCONTINUED)
        application = self.back()
        self.assertEqual(application.kind, Application.NEW)
        self.assertEqual(admissions.issue_college_id(self.profile, self.year, self.pst), 'BPH/PST/2024/0100')

    def test_a_postponement_over_two_years_comes_as_new(self):
        self.stand(StudentStanding.POSTPONED, since=timezone.localdate() - timedelta(days=760))
        self.assertEqual(self.back(kind=Application.CONTINUING).kind, Application.NEW)

    def test_a_recent_postponement_comes_back_as_it_was(self):
        self.stand(StudentStanding.POSTPONED, since=timezone.localdate() - timedelta(days=200), level=self.level5)
        self.assertEqual(self.back(kind=Application.CONTINUING).kind, Application.CONTINUING)

    def test_a_level_4_postponement_comes_back_as_new(self):
        # Said 27 October 2026: only Level 5 and 6 come back as continuing.
        self.stand(StudentStanding.POSTPONED, since=timezone.localdate() - timedelta(days=200))
        application = self.back(kind=Application.CONTINUING)
        self.assertEqual(application.kind, Application.NEW)
        self.assertIn('postponed at Level 4', application.note)

    def test_a_level_4_semester_2_postponement_comes_back_as_it_was(self):
        # Semester 1 was studied and paid for: back into semester 2 as before.
        self.stand(StudentStanding.POSTPONED, since=timezone.localdate() - timedelta(days=200))
        StudentStanding.objects.filter(profile=self.profile).update(return_semester_number=2)
        self.profile.refresh_from_db()
        self.assertEqual(self.back(kind=Application.CONTINUING).kind, Application.CONTINUING)

    def test_a_level_five_finisher_comes_back_as_new(self):
        self.stand(StudentStanding.COMPLETED, level=self.level5)
        self.assertEqual(self.back(kind=Application.CONTINUING).kind, Application.NEW)

    def test_fail_is_readmitted_as_before(self):
        self.stand(StudentStanding.FAILED)
        self.assertEqual(self.back().kind, Application.READMISSION)


class SuspensionTests(StatusBase):
    def test_suspended_keeps_the_place_and_ends_by_itself(self):
        registration = SemesterRegistration.objects.create(
            profile=self.profile, semester=self.sem1, programme=self.pst, class_level=self.level4,
            kind=SemesterRegistration.NEW)
        until = timezone.localdate() + timedelta(days=30)
        lifecycle.record_status(self.profile, StudentStanding.SUSPENDED, reason='Misconduct', until=until,
                                actor=self.officer)
        registration.refresh_from_db()
        self.assertEqual(registration.status, SemesterRegistration.ACTIVE)          # place kept
        self.assertTrue(lifecycle.is_suspended(self.profile))
        from .models import StudentProfile
        banner = lifecycle.standing_banner(StudentProfile.objects.get(pk=self.profile.pk))
        self.assertIn('Suspended until', banner['text'])
        with mock.patch('django.utils.timezone.localdate', return_value=until + timedelta(days=1)):
            self.assertFalse(lifecycle.is_suspended(self.profile))
        self.assertEqual(StudentStanding.objects.get(profile=self.profile).status, StudentStanding.ACTIVE)

    def test_no_examination_while_suspended(self):
        from .views import ca_eligibility_for_student
        enrollment = Student.objects.create(nactvet_reg_no=self.profile.nactvet_reg_no, name=self.profile.name,
                                            profile=self.profile, module=self.module('PST04103', self.level4,
                                                                                     self.sem1))
        with mock.patch('attendance.views._ca_eligibility', return_value=(30, True, 'ok')):
            self.assertTrue(ca_eligibility_for_student(enrollment)[1])
            lifecycle.record_status(self.profile, StudentStanding.SUSPENDED, reason='Misconduct',
                                    until=timezone.localdate() + timedelta(days=10), actor=self.officer)
            self.profile.refresh_from_db()
            enrollment.refresh_from_db()
            total, eligible, note = ca_eligibility_for_student(enrollment)
        self.assertFalse(eligible)
        self.assertIn('Suspended', note)


class FailIsNotDiscontinuedTests(StatusBase):
    def test_the_banner_says_fail(self):
        self.stand(StudentStanding.FAILED)
        self.assertTrue(lifecycle.standing_banner(self.profile)['text'].startswith('FAIL'))
