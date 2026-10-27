"""Said 27 October 2026: a student — often one who passed — transferring to
another college. The admission office finds them by NACTVET number, sees their
details and last results, and approves the transfer: the portal closes, their
places from now on are called off, the year's fees are reversed to nothing (so
no year total counts them), and the admission office keeps a record with their
details and last results for reference.
"""

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from . import admissions, finance
from .models import (
    Application, ChargeType, FeeStructure, Notification, Semester, SemesterRegistration, SemesterReview, Student,
    StudentCharge, StudentStanding, Transfer,
)
from .test_admissions import AdmissionBase
from .views import set_roles

User = get_user_model()


class TransferTests(AdmissionBase):
    def setUp(self):
        super().setUp()
        self.tuition = self.fee('Tuition Fee', '1000000', self.level5)
        self.admission_fee = self.fee('Admission fee', '50000', frequency=ChargeType.ONCE, period=FeeStructure.ONCE)
        self.admission_user, self.admission = self.office('adm', 'admission_officer')
        self.principal_user, self.principal = self.office('boss', 'principal')
        self.dparc_user, self.dparc = self.office('dparc', 'dparc')
        self.records_user, self.records = self.office('rec', 'records_officer')
        self.accountant_user, self.accountant = self.office('acc', 'accountant')
        self.exam_user, self.exam = self.office('exam', 'exam_officer')

        self.profile = self.applicant('NS0500/0001/2025', 'Halima Hamisi')
        self.profile.college_id = 'BPH/PST/2025/0500'
        self.profile.save(update_fields=['college_id'])
        # Their last results: 2025/26 semester 2, passed.
        from .models import AcademicYear
        self.last_year = AcademicYear.objects.create(name='2025/2026')
        last = Semester.objects.create(academic_year=self.last_year, number=2)
        SemesterReview.objects.create(
            profile=self.profile, semester=last, class_level=self.level4, programme=self.pst, gpa=Decimal('3.4'),
            proposed=SemesterReview.CLEAR, confirmed=SemesterReview.CLEAR,
            modules=[{'code': 'PST04201', 'name': 'Pharmacology', 'grade': 'B', 'status': 'PASS'},
                     {'code': 'PST04202', 'name': 'Law', 'grade': 'C', 'status': 'PASS'}])
        # This year: at the desks, billed (a one-time charge too), part paid.
        self.application = admissions.open_application(
            profile=self.profile, semester=self.sem1, programme=self.pst, class_level=self.level5,
            kind=Application.CONTINUING, actor=self.officer)
        StudentCharge.objects.create(profile=self.profile, charge_type=self.admission_fee, academic_year=self.year,
                                     amount=Decimal('50000'), due_date=date(2026, 11, 30), installment_number=1)
        finance.record_payment(self.profile, Decimal('300000'), date(2026, 10, 20), recorded_by=self.officer,
                               bank_reference='CRDB-T1')

    def office(self, username, *roles):
        user = User.objects.create_user(username, password='pw')
        set_roles(user, list(roles), full_name=username.title())
        client = APIClient()
        client.force_authenticate(user)
        return user, client

    def transfer(self, client=None, **extra):
        return (client or self.admission).post('/api/transfers/', {
            'reg_no': 'NS0500/0001/2025', 'to_college': 'Kilimanjaro Pharmacy College',
            'reference': 'Letter 12/10', 'reason': 'Moving with family',
            # 2025/26's fees were never entered: finance's word is written down.
            'finance_confirmed': 'Accountant, 2025/26 ledger', **extra}, format='json')

    def last_year_charge(self, *, settled):
        return StudentCharge.objects.create(
            profile=self.profile, charge_type=self.tuition, academic_year=self.last_year, amount=Decimal('900000'),
            waived_amount=Decimal('900000') if settled else Decimal('0'), due_date=date(2025, 11, 30),
            installment_number=1)

    def test_found_by_number_with_details_and_last_results(self):
        found = self.admission.get('/api/transfers/lookup/?reg_no=ns0500/0001/2025')
        self.assertEqual(found.status_code, 200, found.data)
        self.assertEqual(found.data['details']['college_id'], 'BPH/PST/2025/0500')
        self.assertEqual(len(found.data['details']['next_of_kin']), 2)
        results = found.data['last_results']
        self.assertEqual((results['semester'], results['gpa'], len(results['modules'])), ('Sem 2 · 2025/2026', '3.40', 2))
        self.assertTrue(found.data['can_transfer'])
        self.assertEqual(self.admission.get('/api/transfers/lookup/?reg_no=BPH/PST/2025/0500').status_code, 200)
        self.assertEqual(self.admission.get('/api/transfers/lookup/?reg_no=NS9999/1/2020').status_code, 404)

    def test_approved_the_portal_closes_places_go_and_the_year_is_zero(self):
        done = self.transfer()
        self.assertEqual(done.status_code, 201, done.data)
        standing = StudentStanding.objects.get(profile=self.profile)
        self.assertEqual(standing.status, StudentStanding.TRANSFERRED)
        self.assertTrue(standing.blocks_portal)
        self.application.refresh_from_db()
        self.assertEqual(self.application.state, Application.CANCELLED)
        bill = finance.balance_for(self.profile, self.year)
        self.assertEqual(bill['billed'] - bill['waived'], 0)            # one-time charge too
        record = Transfer.objects.get(profile=self.profile)
        # Paid towards the reversed fees, and the rest on account: all of it theirs.
        self.assertEqual(-bill['balance'] + finance.on_account_total(self.profile), Decimal('300000.00'))
        self.assertEqual((record.to_college, record.credit, record.fees_reversed),
                         ('Kilimanjaro Pharmacy College', Decimal('300000.00'), Decimal('1050000.00')))
        self.assertEqual(record.last_results['modules'][0]['grade'], 'B')
        self.assertEqual(record.details['name'], 'Halima Hamisi')
        told = set(Notification.objects.filter(kind=Notification.TRANSFERRED).values_list('user__username', flat=True))
        self.assertEqual(told, {'boss', 'dparc', 'acc'})

    def test_a_registered_student_is_taken_off_the_class(self):
        from .models import Module
        Module.objects.create(code='PST05101', name='Pharmaceutics', teacher='T', class_level=self.level5,
                              semester=self.sem1, programme=self.pst, credits=10)
        while self.application.is_open:
            admissions.complete_step(self.application, actor=self.officer)
        module_enrollments = Student.objects.filter(profile=self.profile, module__semester=self.sem1)
        self.assertTrue(SemesterRegistration.objects.filter(profile=self.profile, semester=self.sem1).exists())
        self.assertEqual(module_enrollments.filter(withdrawn_at__isnull=True).count(), 1)
        self.transfer()
        self.assertFalse(SemesterRegistration.objects.filter(profile=self.profile, semester=self.sem1)
                         .exclude(status=SemesterRegistration.CANCELLED).exists())
        self.assertFalse(module_enrollments.filter(withdrawn_at__isnull=True).exists())

    def test_who_approves_and_who_reads(self):
        self.assertEqual(self.transfer(self.exam).status_code, 403)
        self.assertEqual(self.transfer(self.records).status_code, 403)
        self.assertEqual(self.transfer(self.principal).status_code, 201)
        for client in (self.admission, self.principal, self.dparc, self.records, self.accountant):
            rows = client.get('/api/transfers/').data
            self.assertEqual([row['reg_no'] for row in rows], ['NS0500/0001/2025'])
        self.assertEqual(self.exam.get('/api/transfers/').status_code, 403)
        self.assertEqual(len(self.admission.get('/api/transfers/?q=kilimanjaro').data), 1)
        self.assertEqual(len(self.admission.get('/api/transfers/?q=nobody').data), 0)

    def test_once_only_and_the_college_is_needed(self):
        self.assertEqual(self.transfer(to_college='  ').status_code, 400)
        self.assertEqual(self.transfer().status_code, 201)
        again = self.transfer()
        self.assertEqual(again.status_code, 400)
        self.assertIn('transferred', again.data['detail'])
        self.assertFalse(self.admission.get('/api/transfers/lookup/?reg_no=NS0500/0001/2025').data['can_transfer'])

    def test_coming_back_is_as_new(self):
        self.transfer()
        from .models import StudentProfile
        self.assertEqual(admissions.returns_as_new(StudentProfile.objects.get(pk=self.profile.pk)),
                         'transferred to another college')


class NobodyLeavesOwingTests(TransferTests):
    """Said 27 October 2026: the period before this one must be paid — last
    academic year in semester 1, semester 1 in semester 2. Last year's fees are
    not in the system: finance's confirmation is written down instead."""

    def test_no_bill_last_year_needs_finance_s_word(self):
        found = self.admission.get('/api/transfers/lookup/?reg_no=NS0500/0001/2025').data
        self.assertEqual(found['debt'], {'period': '2025/2026', 'owed': '0.00', 'in_system': False})
        refused = self.transfer(finance_confirmed='')
        self.assertEqual(refused.status_code, 400)
        self.assertIn("finance's confirmation", refused.data['detail'])
        self.assertEqual(self.transfer().status_code, 201)
        self.assertIn('finance confirmed nothing owed: Accountant, 2025/26 ledger',
                      Transfer.objects.get(profile=self.profile).debt_check)

    def test_owing_last_year_cannot_be_transferred(self):
        self.last_year_charge(settled=False)
        found = self.admission.get('/api/transfers/lookup/?reg_no=NS0500/0001/2025').data
        self.assertFalse(found['can_transfer'])
        self.assertIn('Owes 900,000.00 for 2025/2026', found['why_not'])
        refused = self.transfer()
        self.assertEqual(refused.status_code, 400)
        self.assertIn('settle it at finance first', refused.data['detail'])
        self.assertFalse(Transfer.objects.exists())

    def test_last_year_settled_in_the_system_needs_nothing_more(self):
        self.last_year_charge(settled=True)
        self.assertEqual(self.transfer(finance_confirmed='').status_code, 201)
        self.assertEqual(Transfer.objects.get(profile=self.profile).debt_check,
                         "Nothing owed for 2025/2026 (the system's bill).")

    def test_in_semester_2_semester_1_must_be_paid_and_stays_billed(self):
        Semester.objects.filter(pk=self.sem1.pk).update(is_active=False)
        Semester.objects.filter(pk=self.sem2.pk).update(is_active=True)
        hostel = StudentCharge.objects.create(          # a semester 2 charge
            profile=self.profile, charge_type=self.tuition, academic_year=self.year, semester=self.sem2,
            amount=Decimal('200000'), due_date=date(2027, 3, 1), installment_number=2)
        refused = self.transfer()
        self.assertEqual(refused.status_code, 400)                  # semester 1 not paid up
        self.assertIn('Semester 1 · 2026/2027', refused.data['detail'])
        for charge in StudentCharge.objects.filter(profile=self.profile, academic_year=self.year).exclude(pk=hostel.pk):
            charge.waived_amount = charge.amount                        # settled
            charge.save(update_fields=['waived_amount'])
        self.assertEqual(self.transfer(finance_confirmed='').status_code, 201)
        hostel.refresh_from_db()
        self.assertEqual(hostel.waived_amount, hostel.amount)          # semester 2 reversed
        self.assertEqual(Transfer.objects.get(profile=self.profile).fees_reversed, Decimal('200000.00'))


for _name in [n for n in dir(TransferTests) if n.startswith('test_')]:
    setattr(NobodyLeavesOwingTests, _name, None)          # TransferTests' own tests are not run again here
