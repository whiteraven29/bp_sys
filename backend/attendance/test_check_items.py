"""Items checked only, never billed (said 19 October 2026): a certificate, a
signed form — whatever admission defines.

  * defined without a charge type; a billed item still needs one;
  * checked at the admission desk only, before admitting — records checks
    nothing, finance only the billed items (the TPH book), whose payment the
    admission desk sees;
  * missing holds the student at the admission desk — "Missing: …" — and they
    cannot be admitted; brought (Has it) or waived lifts that hold, but never a
    hold the officer put on by hand;
  * never billed, and never in what the student owes;
  * a billed item missing still never holds anybody.
"""

from .models import AdmissionRequirement, Application, RequirementCheck, StudentCharge
from .test_admissions import ItemsTests
from . import admissions


class CheckOnlyItemTests(ItemsTests):
    def setUp(self):
        super().setUp()
        self.form = AdmissionRequirement.objects.create(
            name='Signed declaration form', kind=AdmissionRequirement.CHECK_ONLY,
            frequency=AdmissionRequirement.ONCE)

    def to_records(self):
        self.pay_tuition()
        for requirement in (self.tph, self.insurance):
            self.assertEqual(self.mark(self.accounts, requirement, 'has_it').status_code, 200)
        self.assertEqual(self.accounts.post(f'/api/applications/{self.application.id}/complete/', {},
                                            format='json').status_code, 200)
        self.application.refresh_from_db()
        self.assertEqual(self.application.state, Application.RECORDS)

    def to_admission(self):
        response = self.records.post(f'/api/applications/{self.application.id}/complete/', {}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.application.refresh_from_db()
        return self.application

    def admit(self):
        return self.admission.post(f'/api/applications/{self.application.id}/complete/', {}, format='json')

    def test_it_is_defined_without_billing_and_a_billed_item_still_needs_a_charge(self):
        made = self.admission.post('/api/admission-requirements/', {
            'name': 'O-level certificate', 'kind': 'check', 'charge_type': self.book.id}, format='json')
        self.assertEqual(made.status_code, 201, made.data)
        self.assertIsNone(made.data['charge_type'])          # checked, never billed
        refused = self.admission.post('/api/admission-requirements/', {
            'name': 'Calculator', 'kind': 'billed'}, format='json')
        self.assertEqual(refused.status_code, 400)
        self.assertIn('charge_type', refused.data)

    def test_only_the_admission_desk_checks_it(self):
        self.assertEqual(self.mark(self.accounts, self.form, 'has_it').status_code, 400)
        self.to_records()
        self.assertEqual(self.mark(self.records, self.form, 'has_it').status_code, 400)
        self.to_admission()
        self.assertEqual(self.mark(self.admission, self.form, 'has_it').status_code, 200)
        rows = {row['name']: row for row in self.admission.get(
            f'/api/applications/{self.application.id}/requirements/').data}
        self.assertEqual(rows['Signed declaration form']['kind'], 'check')
        self.assertEqual(rows['Signed declaration form']['status'], 'has_it')

    def test_missing_holds_the_student_and_bringing_it_lets_them_in(self):
        self.to_records()
        application = self.to_admission()
        self.assertFalse(application.on_hold)
        self.mark(self.admission, self.form, 'missing')
        application.refresh_from_db()
        self.assertTrue(application.on_hold)
        self.assertEqual(application.hold_reason, 'Missing: Signed declaration form')

        refused = self.admit()
        self.assertEqual(refused.status_code, 400)
        self.assertIn('Signed declaration form', str(refused.data))
        self.assertFalse(Application.objects.get(pk=application.pk).state == Application.ADMITTED)

        brought = self.mark(self.admission, self.form, 'has_it')
        self.assertEqual(brought.status_code, 200)
        self.assertFalse(brought.data['on_hold'])
        self.assertEqual(self.admit().status_code, 200)
        self.assertEqual(Application.objects.get(pk=application.pk).state, Application.ADMITTED)

    def test_marking_it_missing_at_the_admission_desk_holds_at_once(self):
        self.to_records()
        self.to_admission()
        self.mark(self.admission, self.form, 'has_it')
        held = self.mark(self.admission, self.form, 'missing')
        self.assertTrue(held.data['on_hold'])
        self.assertIn('Missing: Signed declaration form', held.data['hold_reason'])
        self.assertEqual(self.mark(self.admission, self.form, 'waived').data['on_hold'], False)

    def test_an_officers_own_hold_is_not_lifted_by_the_items(self):
        self.to_records()
        application = self.to_admission()
        self.mark(self.admission, self.form, 'has_it')
        admissions.hold(application, reason='Fees query with the accountant', actor=self.officer)
        self.mark(self.admission, self.form, 'waived')
        application.refresh_from_db()
        self.assertTrue(application.on_hold)
        self.assertEqual(application.hold_reason, 'Fees query with the accountant')

    def test_unchecked_it_is_checked_before_admitting(self):
        self.to_records()
        self.to_admission()
        refused = self.admit()
        self.assertEqual(refused.status_code, 400)
        self.assertIn('Check first', str(refused.data))

    def test_it_is_never_billed_or_owed(self):
        self.to_records()
        self.to_admission()
        self.mark(self.admission, self.form, 'missing')
        check = RequirementCheck.objects.get(requirement=self.form, profile=self.student)
        self.assertIsNone(check.charge_id)
        self.assertEqual(StudentCharge.objects.filter(profile=self.student, note__icontains='declaration').count(), 0)
        self.assertNotIn('Signed declaration form',
                         [row['name'] for row in admissions.items_owed(self.student, self.sem1)])

    def test_a_billed_item_missing_still_holds_nobody(self):
        self.pay_tuition()
        self.mark(self.accounts, self.tph, 'missing')
        self.mark(self.accounts, self.insurance, 'has_it')
        self.accounts.post(f'/api/applications/{self.application.id}/complete/', {}, format='json')
        application = self.to_admission()
        self.mark(self.admission, self.form, 'has_it')
        application.refresh_from_db()
        self.assertFalse(application.on_hold)

    def test_the_admission_desk_sees_whether_the_book_is_paid(self):
        self.pay_tuition()
        self.mark(self.accounts, self.tph, 'missing')
        rows = {row['name']: row for row in self.admission.get(
            f'/api/applications/{self.application.id}/requirements/').data}
        self.assertEqual((rows['TPH Book']['amount'], rows['TPH Book']['paid']), ('35000.00', False))
        self.assertIsNone(rows['Signed declaration form']['paid'])


for _name in [n for n in dir(ItemsTests) if n.startswith('test_')]:
    setattr(CheckOnlyItemTests, _name, None)
