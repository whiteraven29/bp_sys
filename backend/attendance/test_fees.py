"""The fees ledger.

These tests are the specification for the rebuild. Each one fails on the old
model — that is the point. The bugs they pin down were reproduced against a
live database before any of this was written:

  * a student with 4 module enrollments carried 4 separate balances
  * three installments of a 900,000 fee reported 2,700,000 required
  * a student who had paid nothing reported a balance of zero
  * a payment could be edited to any figure and then deleted, with no trace
"""

from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from . import finance
from .models import (
    AcademicYear, ChargeType, ClassLevel, FeeStructure, FinanceAuditLog,
    FinanceOverride, Invoice, Module, Payment, Semester, Student, StudentCharge,
)

User = get_user_model()
TZS = Decimal


class FeesLedgerTests(TestCase):
    def setUp(self):
        self.year = AcademicYear.objects.create(name='2026/2027', is_active=True)
        self.sem1 = Semester.objects.create(
            academic_year=self.year, number=1, is_active=True,
            cat1_cutoff=date(2026, 10, 15), cat2_cutoff=date(2026, 11, 20),
            end_cutoff=date(2026, 12, 10),
        )
        self.level4 = ClassLevel.objects.create(name='NTA Level 4', order=4)
        self.level5 = ClassLevel.objects.create(name='NTA Level 5', order=5)

        # One human being, enrolled in four modules — the case that broke the
        # old model completely.
        self.modules = [
            Module.objects.create(name=f'Module {i}', code=f'MOD{i}', teacher='T',
                                  class_level=self.level4, semester=self.sem1)
            for i in range(1, 5)
        ]
        for module in self.modules:
            Student.objects.create(nactvet_reg_no='REG/001', name='Asha Juma', module=module)
        self.asha = finance.profile_for_student(Student.objects.first())

        self.accountant = User.objects.create_user('accountant', password='pw', is_staff=True)

        # Level 4: fees over 5 installments. Level 5: over 4.
        self.tuition = ChargeType.objects.create(
            name='Tuition Fee', family=ChargeType.FEE,
            blocks_registration=True, blocks_cat1=True, blocks_cat2=True,
            blocks_final=True, blocks_results=True,
        )
        self.gown = ChargeType.objects.create(
            name='Graduation Gown', family=ChargeType.OTHER, blocks_results=True,
        )
        self.hostel = ChargeType.objects.create(
            name='Hostel', family=ChargeType.DIRECT_COST, applies=ChargeType.OPTIONAL,
            blocks_registration=True,
        )

        self.structure = FeeStructure.objects.create(
            charge_type=self.tuition, class_level=self.level4, academic_year=self.year,
            amount=TZS('900000.00'), billing_period=FeeStructure.ACADEMIC_YEAR, installments=5,
        )
        finance.set_installment_schedule(self.structure, [
            date(2026, 9, 30), date(2026, 10, 31), date(2026, 11, 30),
            date(2027, 1, 31), date(2027, 3, 31),
        ])

    # ── the two arithmetic bugs ──────────────────────────────────────────────

    def test_one_person_with_four_enrollments_has_one_balance(self):
        finance.generate_charges(self.asha, self.year, actor=self.accountant)

        self.assertEqual(self.asha.enrollments.count(), 4)
        self.assertEqual(StudentCharge.objects.filter(profile=self.asha).count(), 5)
        self.assertEqual(finance.balance_for(self.asha)['billed'], TZS('900000.00'))
        self.assertEqual(finance.balance_for(self.asha)['balance'], TZS('900000.00'))

    def test_installments_sum_to_the_fee_not_a_multiple_of_it(self):
        finance.generate_charges(self.asha, self.year, actor=self.accountant)
        for charge in StudentCharge.objects.filter(profile=self.asha).order_by('due_date'):
            finance.record_payment(
                self.asha, charge.amount, charge.due_date,
                recorded_by=self.accountant, allocations=[(charge, charge.amount)],
            )

        totals = finance.balance_for(self.asha)
        self.assertEqual(totals['billed'], TZS('900000.00'))
        self.assertEqual(totals['paid'], TZS('900000.00'))
        self.assertEqual(totals['balance'], TZS('0.00'))

    def test_student_who_has_paid_nothing_owes_the_full_fee(self):
        finance.generate_charges(self.asha, self.year, actor=self.accountant)
        totals = finance.balance_for(self.asha)
        self.assertEqual(totals['paid'], TZS('0.00'))
        self.assertEqual(totals['balance'], TZS('900000.00'))

    def test_generating_charges_twice_does_not_double_bill(self):
        finance.generate_charges(self.asha, self.year, actor=self.accountant)
        finance.generate_charges(self.asha, self.year, actor=self.accountant)
        self.assertEqual(StudentCharge.objects.filter(profile=self.asha).count(), 5)
        self.assertEqual(finance.balance_for(self.asha)['billed'], TZS('900000.00'))

    def test_installment_counts_differ_by_level(self):
        level5_structure = FeeStructure.objects.create(
            charge_type=self.tuition, class_level=self.level5, academic_year=self.year,
            amount=TZS('700000.00'), installments=4,
        )
        finance.set_installment_schedule(level5_structure, [
            date(2026, 9, 30), date(2026, 11, 30), date(2027, 1, 31), date(2027, 3, 31),
        ])
        self.assertEqual(self.structure.installment_schedule.count(), 5)
        self.assertEqual(level5_structure.installment_schedule.count(), 4)
        self.assertEqual(
            sum(i.amount for i in level5_structure.installment_schedule.all()),
            TZS('700000.00'),
        )

    def test_uneven_split_still_sums_to_the_total(self):
        odd = FeeStructure.objects.create(
            charge_type=self.gown, class_level=self.level4, academic_year=self.year,
            amount=TZS('1000.00'), installments=3,
        )
        finance.set_installment_schedule(
            odd, [date(2026, 9, 30), date(2026, 10, 31), date(2026, 11, 30)])
        self.assertEqual(
            sum(i.amount for i in odd.installment_schedule.all()), TZS('1000.00'))

    # ── clearance is derived, never clicked ──────────────────────────────────

    def test_paying_clears_the_exam_without_anyone_approving_it(self):
        finance.generate_charges(self.asha, self.year, actor=self.accountant)

        blocked = finance.exam_clearance(self.asha, self.year, ChargeType.CAT1,
                                         semester=self.sem1, today=date(2026, 10, 1))
        self.assertFalse(blocked['cleared'])
        self.assertIn('Tuition Fee', blocked['reason'])

        first = StudentCharge.objects.filter(profile=self.asha).order_by('due_date').first()
        finance.record_payment(self.asha, first.amount, date(2026, 9, 28),
                               recorded_by=self.accountant, allocations=[(first, first.amount)])

        cleared = finance.exam_clearance(self.asha, self.year, ChargeType.CAT1,
                                         semester=self.sem1, today=date(2026, 10, 1))
        self.assertTrue(cleared['cleared'], cleared['reason'])
        self.assertEqual(FinanceOverride.objects.count(), 0)

    def test_only_charges_due_by_the_exam_block_it(self):
        finance.generate_charges(self.asha, self.year, actor=self.accountant)
        first = StudentCharge.objects.filter(profile=self.asha).order_by('due_date').first()
        finance.record_payment(self.asha, first.amount, date(2026, 9, 28),
                               recorded_by=self.accountant, allocations=[(first, first.amount)])

        # Installment 1 paid, 2 not yet due at the CAT 1 cutoff -> cleared.
        self.assertTrue(finance.exam_clearance(
            self.asha, self.year, ChargeType.CAT1, semester=self.sem1,
            today=date(2026, 10, 1))['cleared'])
        # By the CAT 2 cutoff, installment 2 is overdue -> blocked.
        self.assertFalse(finance.exam_clearance(
            self.asha, self.year, ChargeType.CAT2, semester=self.sem1,
            today=date(2026, 12, 1))['cleared'])

    def test_a_gown_debt_does_not_block_an_exam(self):
        finance.raise_charge(self.asha, self.gown, self.year, TZS('50000.00'),
                             date(2026, 9, 1), actor=self.accountant)
        self.assertTrue(finance.exam_clearance(
            self.asha, self.year, ChargeType.FINAL, semester=self.sem1,
            today=date(2026, 12, 20))['cleared'])
        self.assertFalse(finance.exam_clearance(
            self.asha, self.year, ChargeType.RESULTS, semester=self.sem1,
            today=date(2026, 12, 20))['cleared'])

    def test_optional_charges_are_not_billed_to_everyone(self):
        hostel_structure = FeeStructure.objects.create(
            charge_type=self.hostel, class_level=self.level4, academic_year=self.year,
            amount=TZS('200000.00'), installments=2,
        )
        finance.set_installment_schedule(
            hostel_structure, [date(2026, 9, 30), date(2027, 1, 31)])
        finance.generate_charges(self.asha, self.year, actor=self.accountant)
        self.assertFalse(
            StudentCharge.objects.filter(profile=self.asha, charge_type=self.hostel).exists())

    def test_override_beats_the_ledger_and_records_why(self):
        finance.generate_charges(self.asha, self.year, actor=self.accountant)
        FinanceOverride.objects.create(
            profile=self.asha, academic_year=self.year, period=ChargeType.CAT1,
            status=FinanceOverride.CLEARED, reason='HESLB disbursement delayed',
            approved_by=self.accountant,
        )
        result = finance.exam_clearance(self.asha, self.year, ChargeType.CAT1,
                                        semester=self.sem1, today=date(2026, 12, 1))
        self.assertTrue(result['cleared'])
        self.assertTrue(result['overridden'])
        self.assertIn('HESLB', result['reason'])

    def test_an_expired_override_stops_clearing(self):
        finance.generate_charges(self.asha, self.year, actor=self.accountant)
        FinanceOverride.objects.create(
            profile=self.asha, academic_year=self.year, period=ChargeType.CAT1,
            reason='Payment plan for semester 1', approved_by=self.accountant,
            expires_on=date(2026, 11, 1),
        )
        self.assertTrue(finance.exam_clearance(
            self.asha, self.year, ChargeType.CAT1, semester=self.sem1,
            today=date(2026, 10, 20))['cleared'])
        self.assertFalse(finance.exam_clearance(
            self.asha, self.year, ChargeType.CAT1, semester=self.sem1,
            today=date(2026, 11, 2))['cleared'])

    def test_a_waiver_reduces_the_debt_without_looking_like_income(self):
        finance.generate_charges(self.asha, self.year, actor=self.accountant)
        charge = StudentCharge.objects.filter(profile=self.asha).order_by('due_date').first()
        finance.waive_charge(charge, charge.amount, 'Hardship bursary', actor=self.accountant)

        totals = finance.balance_for(self.asha)
        self.assertEqual(totals['waived'], TZS('180000.00'))
        self.assertEqual(totals['paid'], TZS('0.00'))
        self.assertEqual(totals['balance'], TZS('720000.00'))

    # ── invoices ─────────────────────────────────────────────────────────────

    def test_one_reference_covers_every_instalment_of_a_payment(self):
        """The student writes the same number on all of that payment's slips."""
        finance.generate_charges(self.asha, self.year, actor=self.accountant)
        charges = finance.outstanding_charges(self.asha, self.year)
        tuition = [c for c in charges if c.charge_type == self.tuition]
        self.assertGreater(len(tuition), 1, 'tuition should be billed in instalments')

        # Naming one instalment invoices the whole payment.
        invoice = finance.issue_invoice(self.asha, tuition[:1], self.year)
        self.assertTrue(invoice.reference.startswith('BPH-'))
        self.assertEqual(invoice.lines.count(), len(tuition))
        self.assertEqual(invoice.total, sum(c.amount for c in tuition))
        self.assertEqual(finance.invoice_status(invoice, today=date(2026, 8, 1)), 'issued')

    def test_asking_again_returns_the_same_reference(self):
        """Opening the page twice must not mint a second number for one bill."""
        finance.generate_charges(self.asha, self.year, actor=self.accountant)
        charges = finance.outstanding_charges(self.asha, self.year)
        tuition = [c for c in charges if c.charge_type == self.tuition]

        first = finance.issue_invoice(self.asha, tuition[:1], self.year)
        again = finance.issue_invoice(self.asha, tuition[1:2], self.year)
        self.assertEqual(first.reference, again.reference)
        self.assertEqual(first.pk, again.pk)
        self.assertEqual(Invoice.objects.filter(profile=self.asha, cancelled=False).count(), 1)

    def test_an_invoice_expires_when_the_academic_year_does(self):
        """It covers the whole year's instalments, so it cannot expire at the
        first one."""
        finance.generate_charges(self.asha, self.year, actor=self.accountant)
        charges = finance.outstanding_charges(self.asha, self.year)
        invoice = finance.issue_invoice(
            self.asha, [c for c in charges if c.charge_type == self.tuition][:1], self.year)

        self.assertEqual(invoice.due_date, self.year.closes_on)
        self.assertGreater(invoice.due_date, max(c.due_date for c in charges))

    def test_a_charge_raised_later_joins_the_invoice_that_already_exists(self):
        """A supplementary exam declared mid-year must not start a second
        reference for a payment the student is already paying off."""
        first = finance.raise_charge(self.asha, self.gown, self.year, TZS('50000.00'),
                                     date(2026, 9, 1), actor=self.accountant)
        invoice = finance.issue_invoice(self.asha, [first], self.year)

        later = finance.raise_charge(self.asha, self.gown, self.year, TZS('30000.00'),
                                     date(2027, 2, 1), actor=self.accountant)
        refreshed = finance.issue_invoice(self.asha, [later], self.year)

        self.assertEqual(refreshed.reference, invoice.reference)
        self.assertEqual(refreshed.lines.count(), 2)
        self.assertEqual(refreshed.total, TZS('80000.00'))

    def test_a_mistyped_reference_fails_instead_of_matching_someone_else(self):
        self.assertTrue(finance.reference_is_valid(finance.build_reference(1)))
        self.assertTrue(finance.reference_is_valid(finance.build_reference(742)))
        good = finance.build_reference(471)
        wrong = good[:-2] + str((int(good[-2]) + 1) % 10) + good[-1]
        self.assertFalse(finance.reference_is_valid(wrong))

    def test_paying_an_invoice_settles_its_lines_and_marks_it_paid(self):
        finance.generate_charges(self.asha, self.year, actor=self.accountant)
        charges = finance.outstanding_charges(self.asha, self.year)
        invoice = finance.issue_invoice(
            self.asha, [c for c in charges if c.charge_type == self.tuition], self.year)

        finance.record_payment(
            self.asha, invoice.total, date(2026, 9, 29), recorded_by=self.accountant,
            invoice=invoice, channel=Payment.CRDB, bank_reference='CRDB-88291',
            efd_receipt_no='EFD-0034127', payer_name='Juma Hamisi',
            payer_relation=Payment.PARENT,
        )
        self.assertEqual(finance.invoice_status(invoice), 'paid')
        self.assertEqual(finance.balance_for(self.asha)['paid'], invoice.total)

    def test_one_instalment_leaves_the_invoice_part_paid_under_the_same_number(self):
        """Five deposits work one bill off. The accountant has to be able to see
        that, which needs the invoice to outlive the first instalment."""
        finance.generate_charges(self.asha, self.year, actor=self.accountant)
        charges = finance.outstanding_charges(self.asha, self.year)
        tuition = sorted([c for c in charges if c.charge_type == self.tuition],
                         key=lambda c: c.due_date)
        invoice = finance.issue_invoice(self.asha, tuition, self.year)

        first = tuition[0]
        finance.record_payment(
            self.asha, first.amount, date(2026, 9, 29), recorded_by=self.accountant,
            invoice=invoice, allocations=[(first, first.amount)],
        )
        self.assertEqual(finance.invoice_status(invoice), 'partly_paid')
        self.assertEqual(finance.invoice_paid(invoice), first.amount)
        self.assertEqual(finance.issue_invoice(self.asha, tuition, self.year).reference,
                         invoice.reference)

    # ── the parent case ──────────────────────────────────────────────────────

    def test_a_parent_payment_records_who_actually_paid(self):
        finance.generate_charges(self.asha, self.year, actor=self.accountant)
        charge = StudentCharge.objects.filter(profile=self.asha).order_by('due_date').first()
        payment = finance.record_payment(
            self.asha, charge.amount, date(2026, 9, 29), recorded_by=self.accountant,
            payer_name='Juma Hamisi', payer_relation=Payment.PARENT,
            bank_reference='CRDB-88291', efd_receipt_no='EFD-0034127',
        )
        self.assertEqual(payment.payer_name, 'Juma Hamisi')
        self.assertEqual(payment.payer_relation, Payment.PARENT)
        self.assertEqual(payment.profile, self.asha)

    def test_the_slip_date_counts_not_the_day_it_was_keyed_in(self):
        """A student who paid before the deadline stays cleared even if the
        office recorded it a week late."""
        finance.generate_charges(self.asha, self.year, actor=self.accountant)
        charge = StudentCharge.objects.filter(profile=self.asha).order_by('due_date').first()
        finance.record_payment(self.asha, charge.amount, date(2026, 9, 28),
                               recorded_by=self.accountant, allocations=[(charge, charge.amount)])
        self.assertTrue(finance.exam_clearance(
            self.asha, self.year, ChargeType.CAT1, semester=self.sem1,
            today=date(2026, 10, 15))['cleared'])

    # ── integrity ────────────────────────────────────────────────────────────

    def test_a_payment_cannot_be_zero_or_negative(self):
        with self.assertRaises(ValueError):
            finance.record_payment(self.asha, TZS('0.00'), date(2026, 9, 29),
                                   recorded_by=self.accountant)
        with self.assertRaises(ValueError):
            finance.record_payment(self.asha, TZS('-500.00'), date(2026, 9, 29),
                                   recorded_by=self.accountant)

    def test_a_correction_is_a_reversal_that_leaves_the_original_standing(self):
        finance.generate_charges(self.asha, self.year, actor=self.accountant)
        charge = StudentCharge.objects.filter(profile=self.asha).order_by('due_date').first()
        payment = finance.record_payment(
            self.asha, charge.amount, date(2026, 9, 29), recorded_by=self.accountant,
            allocations=[(charge, charge.amount)])
        self.assertEqual(finance.balance_for(self.asha)['balance'], TZS('720000.00'))

        finance.reverse_payment(payment, 'Slip belonged to another student',
                                actor=self.accountant)

        self.assertEqual(finance.balance_for(self.asha)['balance'], TZS('900000.00'))
        self.assertTrue(Payment.objects.filter(id=payment.id).exists())
        self.assertEqual(Payment.objects.count(), 2)

    def test_a_payment_cannot_be_reversed_twice(self):
        finance.generate_charges(self.asha, self.year, actor=self.accountant)
        charge = StudentCharge.objects.filter(profile=self.asha).order_by('due_date').first()
        payment = finance.record_payment(self.asha, charge.amount, date(2026, 9, 29),
                                         recorded_by=self.accountant)
        finance.reverse_payment(payment, 'Keyed twice', actor=self.accountant)
        with self.assertRaises(ValueError):
            finance.reverse_payment(payment, 'Again', actor=self.accountant)

    def test_every_money_action_leaves_an_audit_entry(self):
        finance.generate_charges(self.asha, self.year, actor=self.accountant)
        charge = StudentCharge.objects.filter(profile=self.asha).order_by('due_date').first()
        payment = finance.record_payment(
            self.asha, charge.amount, date(2026, 9, 29), recorded_by=self.accountant,
            bank_reference='CRDB-88291', efd_receipt_no='EFD-0034127')
        finance.reverse_payment(payment, 'Wrong student', actor=self.accountant)
        finance.waive_charge(charge, TZS('1000.00'), 'Goodwill', actor=self.accountant)

        actions = list(FinanceAuditLog.objects.values_list('action', flat=True))
        for expected in ('charge.generate', 'payment.record', 'payment.reverse', 'charge.waive'):
            self.assertIn(expected, actions)

        recorded = FinanceAuditLog.objects.get(action='payment.record')
        self.assertEqual(recorded.actor, self.accountant)
        self.assertIn('EFD-0034127', recorded.summary)

    def test_one_payment_can_settle_several_charges(self):
        finance.generate_charges(self.asha, self.year, actor=self.accountant)
        charges = StudentCharge.objects.filter(profile=self.asha).order_by('due_date')[:2]
        total = sum(c.amount for c in charges)

        payment = finance.record_payment(self.asha, total, date(2026, 9, 29),
                                         recorded_by=self.accountant)
        self.assertEqual(payment.allocations.count(), 2)
        self.assertEqual(finance.balance_for(self.asha)['paid'], total)
        for charge in charges:
            self.assertEqual(finance.charge_balance(charge), TZS('0.00'))

    def test_a_part_payment_settles_the_oldest_charge_first(self):
        finance.generate_charges(self.asha, self.year, actor=self.accountant)
        ordered = list(StudentCharge.objects.filter(profile=self.asha).order_by('due_date'))
        finance.record_payment(self.asha, TZS('200000.00'), date(2026, 9, 29),
                               recorded_by=self.accountant)
        self.assertEqual(finance.charge_balance(ordered[0]), TZS('0.00'))
        self.assertEqual(finance.charge_balance(ordered[1]), TZS('160000.00'))
        self.assertEqual(finance.charge_balance(ordered[2]), TZS('180000.00'))
