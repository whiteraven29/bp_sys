"""One student's first year, end to end, the way the offices work it.

Before a deployment this is the test that says the pieces still fit together:
admission, records, finance (instalments, hostel, items, invoices, payments),
the portal, results, the semester review, the semester change, the semester 2
items check, and the year end handing the student back to finance as a
continuing student at the next level.

It drives the same endpoints the screens call, as the office that would call
them, with the college's own fee structure and instalment form loaded by the
seed commands the server runs.
"""

from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from rest_framework.test import APIClient

from . import finance
from .models import (
    AcademicYear, AdmissionRequirement, AdmissionWindow, Application, ChargeType, ClassLevel,
    Department, FeeStructure, Invoice, Module, Programme, RequirementCheck, Semester,
    SemesterRegistration, Student, StudentCharge, StudentProfile, StudentResidence, StudentResult,
    StudentStanding,
)
from .views import set_roles

User = get_user_model()


class FirstYearEndToEndTests(TestCase):
    def setUp(self):
        self.year = AcademicYear.objects.create(name='2026/2027', is_active=True)
        self.sem1 = Semester.objects.create(academic_year=self.year, number=1, is_active=True)
        self.sem2 = Semester.objects.create(academic_year=self.year, number=2)
        for order in (4, 5, 6):
            ClassLevel.objects.create(name=f'NTA Level {order}', order=order)
        self.level4 = ClassLevel.objects.get(order=4)
        self.level5 = ClassLevel.objects.get(order=5)
        department = Department.objects.create(name='Pharmaceutical Sciences', code='PST')
        self.pst = Programme.objects.create(department=department, name='Pharmaceutical Sciences', code='PST')
        self.pst.levels.set(ClassLevel.objects.all())
        # What the server is loaded with.
        call_command('seed_fee_structure', year='2026/2027', first_due='2026-09-30', verbosity=0)
        call_command('seed_payment_schedule', year='2026/2027', verbosity=0)

        self.m1 = Module.objects.create(name='Human Anatomy', code='PST04101', teacher='T',
                                        class_level=self.level4, semester=self.sem1, programme=self.pst, credits=3)
        self.m2 = Module.objects.create(name='Dispensing', code='PST04201', teacher='T',
                                        class_level=self.level4, semester=self.sem2, programme=self.pst, credits=3)
        Module.objects.create(name='Pharmacology I', code='PST05101', teacher='T',
                              class_level=self.level5, semester=self.sem1, programme=self.pst, credits=3)
        AdmissionWindow.objects.create(semester=self.sem1, opens_on=date.today() - timedelta(days=1),
                                       closes_on=date.today() + timedelta(days=30))

        def office(username, role):
            user = User.objects.create_user(username, password='pw')
            set_roles(user, [role], full_name=username.title())
            client = APIClient(); client.force_authenticate(user)
            return user, client

        self.exam_user = User.objects.create_superuser('exam', 'exam@x.com', 'pw')
        self.exam = APIClient(); self.exam.force_authenticate(self.exam_user)
        _, self.admission = office('admission', 'admission_officer')
        _, self.records = office('records', 'records_officer')
        self.accountant, self.accounts = office('accountant', 'accountant')
        _, self.principal = office('principal', 'principal')

        # The items, set up as the accountant and admission officer would.
        other_account = ChargeType.objects.get(name='Registration fees').bank_account
        self.tph = ChargeType.objects.create(name='TPH Book', family=ChargeType.OTHER,
                                             applies=ChargeType.ON_REQUEST, bank_account=other_account)
        for level in ClassLevel.objects.all():
            structure = FeeStructure.objects.create(charge_type=self.tph, class_level=level, academic_year=self.year,
                                                    amount=Decimal('35000'), installments=1)
            finance.set_installment_schedule(structure, [date(2026, 10, 19)])
        medical = ChargeType.objects.get(name='Medical fees (Health Insurance)')
        self.admission.post('/api/admission-requirements/', {
            'name': 'TPH Book', 'charge_type': self.tph.id, 'frequency': 'each_semester'}, format='json')
        self.admission.post('/api/admission-requirements/', {
            'name': 'Insurance', 'charge_type': medical.id, 'frequency': 'each_year'}, format='json')

    # ── helpers ───────────────────────────────────────────────────────────────

    def ok(self, response, code=200):
        self.assertEqual(response.status_code, code, getattr(response, 'data', response.content))
        return response.data if hasattr(response, 'data') else response.json()

    def pay_invoice(self, invoice_id, amount, reference):
        return self.ok(self.accounts.post('/api/payments/record/', {
            'profile': self.profile.id, 'amount': str(amount), 'payment_date': str(date.today()),
            'invoice': invoice_id, 'bank_reference': reference, 'efd_receipt_no': f'EFD-{reference}',
        }, format='json'), 201)

    def first_instalment_of(self, invoice):
        charges = [line.charge for line in Invoice.objects.get(pk=invoice['id']).lines.select_related('charge')]
        first = min(charge.due_date for charge in charges)
        return sum((finance.charge_balance(finance.with_balances(StudentCharge.objects.filter(pk=c.pk)).get())
                    for c in charges if c.due_date == first), Decimal('0'))

    def results(self, module, mark):
        rows = self.ok(self.exam.get(f'/api/results/module/?module_id={module.id}'))['results']
        for row in rows:
            self.ok(self.exam.patch(f'/api/results/{row["id"]}/', {
                'assign1': mark, 'assign2': mark, 'cat1_theory': mark, 'cat2_theory': mark,
                'end_theory': mark, 'ca_approved': True, 'final_approved': True}, format='json'))

    def review_and_advance(self):
        self.ok(self.exam.post('/api/semester-reviews/build/', {}, format='json'))
        confirmed = self.ok(self.exam.post('/api/semester-reviews/confirm-proposed/', {}, format='json'))
        self.assertEqual(confirmed['left_for_you'], 0)
        return self.ok(self.exam.post('/api/academic-years/advance/', {}, format='json'))

    # ── the year ──────────────────────────────────────────────────────────────

    def test_a_first_year_from_admission_to_their_second_year(self):
        # 1. The admission desk takes them on and writes in their NACTVET number.
        application = self.ok(self.admission.post('/api/applications/capture/', {
            'kind': 'new', 'name': 'Rehema Mtui', 'semester_id': self.sem1.id,
            'programme_id': self.pst.id, 'class_level_id': self.level4.id}, format='json'), 201)
        app_id = application['id']
        self.ok(self.admission.post(f'/api/applications/{app_id}/nactvet-number/',
                                    {'nactvet_reg_no': 'NIT/PST/2026/0001'}, format='json'))
        self.assertEqual(self.ok(self.admission.post(f'/api/applications/{app_id}/complete/', {},
                                                     format='json'))['state'], Application.INFORMATION)
        self.profile = StudentProfile.objects.get(nactvet_reg_no='NIT/PST/2026/0001')

        # 2. Records takes down who they are; finance was not billed anything yet.
        self.assertFalse(StudentCharge.objects.filter(profile=self.profile).exists())
        self.ok(self.records.post(f'/api/applications/{app_id}/details/', {
            'phone': '0712000111', 'gender': 'F', 'date_of_birth': '2006-04-12',
            'next_of_kin': [{'name': 'Mama Mtui', 'phone': '0713000222', 'relationship': 'parent'},
                            {'name': 'Baba Mtui', 'phone': '0714000333', 'relationship': 'parent'}],
        }, format='json'))
        self.assertEqual(self.ok(self.records.post(f'/api/applications/{app_id}/complete/', {},
                                                   format='json'))['state'], Application.FINANCE)

        # 3. Finance: the bill is the new students' schedule; hostel; the items.
        self.assertEqual(finance.balance_for(self.profile, self.year)['billed'], Decimal('2495000.00'))
        refused = self.accounts.post(f'/api/applications/{app_id}/complete/', {}, format='json')
        self.assertIn('hostel', refused.data['detail'])
        self.ok(self.accounts.post(f'/api/applications/{app_id}/residence/', {'residence': 'hostel'},
                                   format='json'))
        items = {row['name']: row['requirement'] for row in
                 self.ok(self.accounts.get(f'/api/applications/{app_id}/requirements/'))}
        self.ok(self.accounts.post(f'/api/applications/{app_id}/requirements/',
                                   {'requirement_id': items['Insurance'], 'status': 'has_it'}, format='json'))
        self.ok(self.accounts.post(f'/api/applications/{app_id}/requirements/',
                                   {'requirement_id': items['TPH Book'], 'status': 'missing'}, format='json'))
        balance = finance.balance_for(self.profile, self.year)
        # 2,495,000 + hostel 400,000 + TPH 35,000 − own insurance 60,000.
        self.assertEqual(balance['billed'] - balance['waived'], Decimal('2870000.00'))
        not_paid = self.accounts.post(f'/api/applications/{app_id}/complete/', {}, format='json')
        self.assertIn('Not paid', not_paid.data['detail'])

        # They pay the first instalment of tuition and direct costs against
        # the invoices the office raises.
        invoices = {row['invoice_group']: row for row in self.ok(self.accounts.post(
            '/api/finance/issue-invoice/', {'profile': self.profile.id}, format='json'), 201)}
        self.assertEqual(sorted(invoices), ['Direct Costs', 'TPH Book', 'Tuition Fee'])
        for group in ('Tuition Fee', 'Direct Costs'):
            self.pay_invoice(invoices[group]['id'], self.first_instalment_of(invoices[group]), group[:3])
        self.assertEqual(self.ok(self.accounts.post(f'/api/applications/{app_id}/complete/', {},
                                                    format='json'))['state'], Application.ADMISSION)

        # 4. Admission sees the TPH book is owed (never a hold on registering),
        # holds them, then admits them.
        opened = self.ok(self.admission.get(f'/api/applications/{app_id}/'))
        self.assertEqual([(row['name'], row['paid']) for row in opened['items']['owed']], [('TPH Book', False)])
        self.ok(self.admission.post(f'/api/applications/{app_id}/hold/', {'reason': 'Bring the TPH book'},
                                    format='json'))
        admitted = self.ok(self.admission.post(f'/api/applications/{app_id}/complete/', {}, format='json'))
        self.assertEqual(admitted['state'], Application.ADMITTED)
        self.assertTrue(admitted['college_id'])
        registration = SemesterRegistration.objects.get(profile=self.profile, semester=self.sem1)
        self.assertEqual(registration.kind, SemesterRegistration.NEW)
        self.assertTrue(Student.objects.filter(profile=self.profile, module=self.m1).exists())

        # 5. The portal: the PIN signs them in; they invoice the TPH book.
        signed_in = self.client.post('/login/', {'identifier': 'NIT/PST/2026/0001',
                                                 'secret': admitted['portal_pin']})
        self.assertEqual(signed_in.status_code, 302)
        self.assertIn('student_id', self.client.session)
        # A new PIN is changed at the first sign-in before anything else.
        forced = self.client.get('/student-dashboard/')
        self.assertTemplateUsed(forced, 'student_password_change.html')
        changed = self.client.post('/api/change-password/', {
            'current_password': admitted['portal_pin'], 'new_password': 'Rehema#2026'},
            content_type='application/json')
        self.assertEqual(changed.status_code, 200, changed.content)
        options = [row['group'] for row in self.client.get('/api/my-fees/invoice/options/').json()['payments']]
        self.assertEqual(options, ['Tuition Fee', 'Direct Costs', 'TPH Book'])
        other = self.client.post('/api/my-fees/invoice/', {'group': 'TPH Book'}, content_type='application/json')
        self.assertEqual(other.status_code, 201, other.content)
        other_invoice = other.json()[0]
        self.assertEqual(Decimal(other_invoice['total']), Decimal('35000.00'))
        self.assertEqual(other_invoice['reference'], invoices['TPH Book']['reference'])   # one per payment
        self.pay_invoice(other_invoice['id'], Decimal('35000'), 'TPH')
        self.assertEqual(self.ok(self.admission.get(f'/api/applications/{app_id}/'))['items']['owed'][0]['paid'],
                         True)

        # 6. Semester 1 results, the review, and on into semester 2.
        self.results(self.m1, 70)
        advanced = self.review_and_advance()
        self.assertEqual(Semester.objects.get(is_active=True), self.sem2)
        self.assertTrue(Student.objects.filter(profile=self.profile, module=self.m2).exists())
        self.assertTrue(SemesterRegistration.objects.filter(profile=self.profile, semester=self.sem2).exists())
        # The portal shows semester 1's grade, never the marks.
        page = self.client.get('/student-dashboard/')
        statements = page.context['result_statements']
        self.assertEqual([(m['code'], m['grade']) for s in statements for m in s['modules']], [('PST04101', 'B')])
        self.assertNotIn('70.0', ''.join(str(m) for s in statements for m in s['modules']))

        # 7. Semester 2 asks for the TPH book again; missing, it is billed to semester 2.
        listed = self.ok(self.accounts.get(f'/api/item-checks/?semester_id={self.sem2.id}'))
        row = next(row for row in listed['results'] if row['reg_no'] == 'NIT/PST/2026/0001')
        self.assertEqual([item['name'] for item in row['items']], ['TPH Book'])
        tph_item = row['items'][0]['requirement']
        self.ok(self.accounts.post('/api/item-checks/', {'semester_id': self.sem2.id, 'profile_id': self.profile.id,
                                                         'requirement_id': tph_item, 'status': 'missing'},
                                   format='json'))
        self.assertEqual(StudentCharge.objects.filter(profile=self.profile, charge_type=self.tph,
                                                      semester=self.sem2).count(), 1)

        # 8. Semester 2 results; the year end hands them to finance at level 5.
        self.results(self.m2, 75)
        advanced = self.review_and_advance()
        next_year = AcademicYear.objects.get(name='2027/2028')
        self.assertEqual(advanced['year'], '2027/2028')
        self.assertEqual(advanced['students']['applications'], 1)
        waiting = Application.objects.get(profile=self.profile, semester__academic_year=next_year)
        self.assertEqual((waiting.kind, waiting.state, waiting.class_level), (Application.CONTINUING,
                                                                              Application.FINANCE, self.level5))
        # Hostel carried into the new year.
        self.assertEqual(finance.residence_for(self.profile, next_year).residence, StudentResidence.HOSTEL)
        self.assertEqual(StudentStanding.objects.get(profile=self.profile).status, StudentStanding.ACTIVE)
