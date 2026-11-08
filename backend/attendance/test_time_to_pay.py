"""Said 6 November 2026: time to pay. A waived student's pass runs to the day
they said they would pay, and DPFPA may move that day; a student may pay tuition
month by month, as DPFPA agreed, with seven days' grace, or go back to the normal
instalments. The bill never changes, so what auditors read stays true.
"""

from datetime import date, timedelta
from decimal import Decimal
from unittest import mock

from django.test import Client

from . import admissions, finance, semester_pass, time_to_pay
from .models import (
    Application, ChargeType, FinanceAuditLog, Module, MonthlyPlan, Notification, Payment, Student, StudentCharge,
    WaiverRequest,
)
from .test_finance_desk_only import SemesterPassTests, WaiverDesk, office

NOV = date(2026, 11, 1)


class Base(SemesterPassTests):
    """Tuition 1,000,000: 500,000 due 15 Oct, 500,000 due 1 Mar."""

    def setUp(self):
        super().setUp()
        self.dpfpa_user, self.dpfpa = office('dpf', 'dpfpa')
        self.accounts = office('acc2', 'accountant')[1]

    def tuition_charges(self):
        return list(StudentCharge.objects.filter(profile=self.profile, charge_type=self.tuition).order_by('due_date'))

    def waiver(self, amount, pay_by, instalment=1, family=ChargeType.FEE):
        return WaiverRequest.objects.create(profile=self.profile, academic_year=self.year, family=family,
                                            installment_number=instalment, amount=Decimal(amount), reason='Sponsor',
                                            status=WaiverRequest.APPROVED, pay_by=pay_by, first_pay_by=pay_by)

    def owed(self):
        return finance.balance_for(self.profile, self.year)['balance']


for _name in [n for n in dir(SemesterPassTests) if n.startswith('test_')]:
    setattr(Base, _name, None)


class WaivedPassTests(Base):
    def test_the_pass_runs_to_the_day_they_said(self):
        self.pay('450000', 'P1')
        self.assertFalse(self.status(NOV)['ok'])                                 # 50,000 short, no waiver
        self.waiver('50000', date(2026, 12, 10))
        result = self.status(NOV)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['valid_until'], date(2026, 12, 10))
        self.assertIn('to be paid by 10 Dec 2026', result['basis'])

    def test_the_money_stays_owed_and_the_bill_does_not_move(self):
        self.pay('450000', 'P1')
        before = finance.balance_for(self.profile, self.year)
        self.waiver('50000', date(2026, 12, 10))
        self.assertEqual(finance.balance_for(self.profile, self.year), before)
        self.assertEqual(sum(c.waived_amount for c in self.tuition_charges()), Decimal('0'))

    def test_a_whole_instalment_given_time_needs_no_payment_first(self):
        # Refused before: "Not paid up", 500,000 asked for in March, months early.
        self.waiver('500000', date(2026, 12, 10))
        self.assertEqual(self.status(NOV)['valid_until'], date(2026, 12, 10))

    def test_the_whole_year_given_time(self):
        self.waiver('500000', date(2026, 12, 10))
        self.waiver('500000', date(2026, 12, 20), instalment=2)
        self.assertEqual(self.status(NOV)['valid_until'], date(2026, 12, 10))

    def test_past_the_day_the_pass_stops_and_the_student_owes_it_again(self):
        self.pay('450000', 'P1')
        self.waiver('50000', date(2026, 12, 10))
        self.assertTrue(self.status(date(2026, 12, 10))['ok'])                    # the day itself still counts
        late = self.status(date(2026, 12, 11))
        self.assertFalse(late['ok'])
        self.assertIn('50,000.00', late['reason'])

    def test_a_short_waiver_does_not_cover_a_bigger_shortfall(self):
        self.pay('300000', 'P1')
        self.waiver('50000', date(2026, 12, 10))                                  # 200,000 short
        self.assertFalse(self.status(NOV)['ok'])

    def test_it_never_outruns_the_next_instalment(self):
        self.pay('450000', 'P1')
        self.waiver('50000', date(2027, 4, 1))
        self.assertEqual(self.status(NOV)['valid_until'], date(2027, 2, 28))

    def test_paid_it_is_as_if_it_never_was(self):
        self.pay('450000', 'P1')
        waiver = self.waiver('50000', date(2026, 12, 10))
        self.pay('50000', 'P2', on=date(2026, 11, 20))
        self.assertEqual(self.status(NOV)['valid_until'], date(2027, 2, 28))
        self.assertEqual(time_to_pay.outstanding_on(waiver), Decimal('0.00'))
        self.assertEqual(time_to_pay.waiver_state(waiver, NOV), 'paid')

    def test_clearance_follows_the_same_date(self):
        waiver = self.waiver('500000', date(2026, 12, 10))
        ChargeType.objects.filter(name='Tuition fee').update(blocks_registration=True)
        check = lambda day: finance.exam_clearance(self.profile, self.year, ChargeType.REGISTRATION, today=day)
        batch = lambda day: finance.clearance_map([self.profile], self.year, [ChargeType.REGISTRATION], today=day)[
            self.profile.id][ChargeType.REGISTRATION]
        self.assertTrue(check(NOV)['cleared'])
        self.assertTrue(batch(NOV)['cleared'])
        self.assertFalse(check(date(2026, 12, 11))['cleared'])
        self.assertFalse(batch(date(2026, 12, 11))['cleared'])
        self.assertEqual(time_to_pay.waiver_state(waiver, date(2026, 12, 11)), 'expired')


class MoreTimeTests(Base):
    def setUp(self):
        super().setUp()
        self.pay('450000', 'P1')
        future = date.today() + timedelta(days=5)
        self.record = self.waiver('50000', future)
        self.new_date = (date.today() + timedelta(days=30)).isoformat()

    def extend(self, client=None, **data):
        return (client or self.dpfpa).post(f'/api/waiver-requests/{self.record.id}/extend/',
                                           {'pay_by': self.new_date, 'reason': 'Sponsor wrote', **data}, format='json')

    def test_dpfpa_gives_more_time_and_the_first_date_is_kept(self):
        first = self.record.pay_by
        done = self.extend()
        self.assertEqual(done.status_code, 200, done.data)
        self.record.refresh_from_db()
        self.assertEqual((self.record.pay_by.isoformat(), self.record.first_pay_by, self.record.times_extended),
                         (self.new_date, first, 1))
        self.assertTrue(FinanceAuditLog.objects.filter(action='waiver.extend', entity_id=self.record.id).exists())
        self.assertTrue(Notification.objects.filter(profile=self.profile, title='More time to pay').exists())

    def test_it_can_follow_a_date_already_passed(self):
        WaiverRequest.objects.filter(pk=self.record.pk).update(pay_by=date.today() - timedelta(days=3))
        self.assertEqual(self.extend().status_code, 200)

    def test_only_dpfpa_with_a_reason_and_a_later_day(self):
        self.assertEqual(self.extend(self.accounts).status_code, 403)
        self.assertEqual(self.extend(office('boss', 'principal')[1]).status_code, 403)
        self.assertEqual(self.extend(reason=' ').status_code, 400)
        self.assertEqual(self.extend(pay_by=self.record.pay_by.isoformat()).status_code, 400)
        self.assertEqual(self.extend(pay_by='').status_code, 400)

    def test_nothing_to_extend_once_paid(self):
        self.pay('50000', 'P2')
        self.assertEqual(self.extend().status_code, 400)


class MonthlyPlanBase(Base):
    def plan(self, monthly='250000', first=date(2026, 11, 15), today=NOV):
        plan = time_to_pay.propose_plan(self.profile, self.year, Decimal(monthly), first, 'Salary monthly',
                                        actor=self.accountant, today=today)
        return time_to_pay.decide_plan(plan, approve=True, actor=self.dpfpa_user, today=today)


class MonthlyPlanPassTests(MonthlyPlanBase):
    def test_from_approval_the_pass_runs_to_the_month_end_plus_grace(self):
        plan = self.plan()
        self.assertEqual(plan.first_due, date(2026, 11, 30))
        self.assertEqual(plan.total, Decimal('1000000.00'))
        result = self.status(NOV)
        self.assertEqual(result['valid_until'], date(2026, 12, 7))
        self.assertIn('monthly plan: month 1 of 4', result['basis'])

    def test_each_month_paid_moves_it_on_a_month(self):
        self.plan()
        self.pay('250000', 'M1', on=date(2026, 11, 28))
        self.assertEqual(self.status(date(2026, 11, 29))['valid_until'], date(2027, 1, 7))

    def test_within_the_grace_is_in_time(self):
        plan = self.plan()
        self.assertEqual(time_to_pay.position(plan, date(2026, 12, 3))['state'], time_to_pay.IN_GRACE)
        self.assertTrue(self.status(date(2026, 12, 7))['ok'])
        self.pay('250000', 'M1', on=date(2026, 12, 7))
        where = time_to_pay.position(plan, date(2026, 12, 20))
        self.assertEqual((where['state'], where['next']['number']), (time_to_pay.ON_TRACK, 2))

    def test_missed_past_the_grace_it_lapses_back_to_the_normal_instalments(self):
        plan = self.plan()
        day = date(2026, 12, 8)
        self.assertEqual(time_to_pay.position(plan, day)['state'], MonthlyPlan.LAPSED)
        self.assertFalse(self.status(day)['ok'])                                 # 500,000 from October again
        self.assertIn('500,000.00', self.status(day)['reason'])
        time_to_pay.sync_plans(today=day)
        plan.refresh_from_db()
        self.assertEqual((plan.status, plan.ended_on), (MonthlyPlan.LAPSED, day - timedelta(days=1)))
        self.assertTrue(Notification.objects.filter(profile=self.profile, title='Monthly plan ended').exists())

    def test_money_after_a_lapse_does_not_revive_it(self):
        plan = self.plan()
        self.pay('250000', 'LATE', on=date(2026, 12, 20))
        self.assertEqual(time_to_pay.position(plan, date(2026, 12, 21))['state'], MonthlyPlan.LAPSED)

    def test_a_slip_paid_on_time_and_recorded_late_keeps_it_going(self):
        plan = self.plan()
        self.pay('250000', 'ONTIME', on=date(2026, 11, 29))                      # recorded weeks after, dated in time
        self.assertEqual(time_to_pay.position(plan, date(2026, 12, 20))['state'], time_to_pay.ON_TRACK)

    def test_a_reversed_slip_counts_for_nothing(self):
        plan = self.plan()
        self.pay('250000', 'BAD', on=date(2026, 11, 29))
        finance.reverse_payment(Payment.objects.get(bank_reference='BAD'), 'Bounced', actor=self.accountant)
        self.assertEqual(time_to_pay.position(plan, date(2026, 12, 20))['state'], MonthlyPlan.LAPSED)

    def test_paid_off_it_is_done_and_the_normal_rule_takes_over(self):
        plan = self.plan()
        self.pay('1000000', 'ALL', on=date(2026, 11, 20))
        self.assertEqual(time_to_pay.position(plan, date(2026, 11, 21))['state'], MonthlyPlan.COMPLETED)
        time_to_pay.sync_plans(today=date(2026, 11, 21))
        plan.refresh_from_db()
        self.assertEqual(plan.status, MonthlyPlan.COMPLETED)
        self.assertTrue(self.status(date(2026, 11, 21))['ok'])

    def test_a_tuition_bill_reversed_closes_it_not_as_paid(self):
        plan = self.plan()
        finance.reset_fees(self.profile, self.year, reason='Postponed', actor=self.accountant)
        self.assertEqual(time_to_pay.position(plan, NOV)['state'], MonthlyPlan.CANCELLED)

    def test_the_plan_is_tuition_only(self):
        direct = ChargeType.objects.get(name='Direct costs')
        finance.raise_charge(self.profile, direct, self.year, Decimal('100000'), date(2026, 10, 15),
                             source=StudentCharge.STRUCTURE)
        self.plan()
        result = self.status(NOV)
        self.assertFalse(result['ok'])                                           # direct costs still by instalment
        self.assertIn('100,000.00', result['reason'])

    def test_clearance_while_it_runs_and_after(self):
        self.plan()
        ChargeType.objects.filter(name='Tuition fee').update(blocks_registration=True)
        check = lambda day: finance.exam_clearance(self.profile, self.year, ChargeType.REGISTRATION, today=day)
        self.assertTrue(check(NOV)['cleared'])
        self.assertFalse(check(date(2026, 12, 8))['cleared'])

    def test_the_bill_never_moves(self):
        before = finance.balance_for(self.profile, self.year)
        dates = [c.due_date for c in self.tuition_charges()]
        self.plan()
        self.assertEqual(finance.balance_for(self.profile, self.year), before)
        self.assertEqual([c.due_date for c in self.tuition_charges()], dates)


class AskingForAPlanTests(MonthlyPlanBase):
    def ask(self, **data):
        return self.accounts.post('/api/monthly-plans/', {
            'profile_id': self.profile.id, 'academic_year_id': self.year.id, 'monthly_amount': '250000',
            'first_month': f'{date.today():%Y-%m}', 'reason': 'Salary monthly', **data}, format='json')

    def test_what_the_desk_may_not_ask(self):
        for bad in ({'monthly_amount': '0'}, {'monthly_amount': 'x'}, {'monthly_amount': '9000000'},
                    {'first_month': ''}, {'first_month': '2020-01'}, {'reason': ' '}):
            self.assertEqual(self.ask(**bad).status_code, 400, bad)
        self.assertEqual(self.ask(monthly_amount='1000').status_code, 400)       # would run past the year
        self.assertEqual(self.dpfpa.post('/api/monthly-plans/', {}, format='json').status_code, 403)

    def test_only_one_at_a_time_and_not_over_a_tuition_waiver(self):
        self.plan()
        with self.assertRaises(time_to_pay.PlanError):
            self.plan()
        with self.assertRaises(finance.WaiverError):
            finance.request_shortfall_waiver(self.profile, self.year, ChargeType.FEE, 1, Decimal('1000'), 'x',
                                             pay_by=date.today() + timedelta(days=9))

    def test_a_plan_replaces_an_approved_tuition_waiver(self):
        """Said 8 November 2026: a student given time by a waiver changes to paying
        monthly. The plan takes the waiver over; the money stays owed, in the plan."""
        self.pay('450000', 'P1')
        waiver = self.waiver('50000', date(2026, 12, 10))
        pending = time_to_pay.propose_plan(self.profile, self.year, Decimal('110000'), date(2026, 11, 15), 'Salary',
                                           actor=self.accountant, today=NOV)
        self.assertEqual(len(time_to_pay.plan_row(pending, NOV)['replaces']), 1)
        plan = time_to_pay.decide_plan(pending, approve=True, actor=self.dpfpa_user, today=NOV)
        waiver.refresh_from_db()
        self.assertEqual(waiver.status, WaiverRequest.REPLACED)
        self.assertIn('Replaced by the monthly plan', waiver.decision_note)
        self.assertEqual(plan.total, Decimal('550000.00'))                       # the 50,000 is in it
        relief, caps = time_to_pay.relief_for([self.profile.id], self.year, NOV)
        self.assertEqual([cap for _, cap in caps[self.profile.id]], ['monthly plan: month 1 of 5, 110,000.00 due 30 Nov 2026'])
        self.assertEqual(self.status(NOV)['valid_until'], date(2026, 12, 7))      # the plan's date now
        self.assertEqual(sum(c.waived_amount for c in self.tuition_charges()), Decimal('0'))
        self.assertTrue(FinanceAuditLog.objects.filter(action='waiver.replace', entity_id=waiver.id).exists())
        self.assertEqual(time_to_pay.summary(self.year, NOV)['waivers'], [])     # followed under the plan now

    def test_a_tuition_waiver_still_waiting_is_decided_first(self):
        WaiverRequest.objects.create(profile=self.profile, academic_year=self.year, family=ChargeType.FEE,
                                     installment_number=1, amount=Decimal('50000'), reason='x')
        with self.assertRaises(time_to_pay.PlanError) as held:
            self.plan()
        self.assertIn('waiting for DPFPA', str(held.exception))

    def test_a_direct_costs_waiver_is_left_alone(self):
        waiver = self.waiver('50000', date(2026, 12, 10), family=ChargeType.DIRECT_COST)
        self.plan()
        waiver.refresh_from_db()
        self.assertEqual(waiver.status, WaiverRequest.APPROVED)

    def test_an_old_waiver_stays_forgiven_and_the_plan_covers_the_rest(self):
        # Approved before 6 November 2026: the money came off the bill, no date.
        charge = self.tuition_charges()[0]
        finance.waive_charge(charge, Decimal('50000'), 'Sponsor — approved by Dpf', actor=self.accountant)
        WaiverRequest.objects.create(profile=self.profile, academic_year=self.year, family=ChargeType.FEE,
                                     installment_number=1, amount=Decimal('50000'), reason='Sponsor',
                                     status=WaiverRequest.APPROVED)
        plan = self.plan()
        self.assertEqual(plan.total, Decimal('950000.00'))

    def test_after_a_lapse_a_new_plan_may_be_asked(self):
        self.plan()
        day = date(2026, 12, 8)
        plan = time_to_pay.propose_plan(self.profile, self.year, Decimal('250000'), date(2026, 12, 15), 'Again',
                                        actor=self.accountant, today=day)
        self.assertEqual(plan.status, MonthlyPlan.PENDING)
        self.assertEqual(MonthlyPlan.objects.filter(status=MonthlyPlan.LAPSED).count(), 1)

    def test_dpfpa_alone_decides_and_a_refusal_needs_a_reason(self):
        plan = time_to_pay.propose_plan(self.profile, self.year, Decimal('250000'), date(2026, 11, 15), 'Salary',
                                        actor=self.accountant, today=NOV)
        url = f'/api/monthly-plans/{plan.id}/decide/'
        self.assertEqual(self.accounts.post(url, {'approve': True}, format='json').status_code, 403)
        self.assertEqual(office('boss', 'principal')[1].post(url, {'approve': True}, format='json').status_code, 403)
        self.assertEqual(self.dpfpa.post(url, {'approve': False}, format='json').status_code, 400)
        self.assertEqual(self.dpfpa.post(url, {'approve': False, 'note': 'Pay normally'}, format='json').data['status'],
                         MonthlyPlan.REFUSED)
        self.assertFalse(self.status(NOV)['ok'])


class DeskWaitsForAPlanTests(WaiverDesk):
    def test_the_student_waits_at_finance_for_dpfpa(self):
        time_to_pay.propose_plan(self.profile, self.year, Decimal('250000'), date(2026, 11, 15), 'Salary',
                                 actor=self.accountant, today=NOV)
        with self.assertRaises(admissions.AdmissionError) as held:
            admissions.complete_step(self.application, actor=self.officer)
        self.assertIn('monthly plan', str(held.exception))

    def test_a_plan_running_lets_them_through_the_desk(self):
        ChargeType.objects.filter(name='Tuition fee').update(blocks_registration=True)
        plan = time_to_pay.propose_plan(self.profile, self.year, Decimal('250000'),
                                        date.today(), 'Salary', actor=self.accountant)
        time_to_pay.decide_plan(plan, approve=True, actor=self.dpfpa_user)
        admissions.complete_step(self.application, actor=self.officer)
        self.application.refresh_from_db()
        self.assertEqual(self.application.state, Application.ADMISSION)


class SummaryTests(MonthlyPlanBase):
    def setUp(self):
        super().setUp()
        self.pay('100000', 'FIRST')

    def test_finance_and_dpfpa_follow_the_money(self):
        self.plan(first=date.today(), today=date.today())
        self.waiver('50000', date.today() + timedelta(days=3), family=ChargeType.DIRECT_COST)
        for client in (self.dpfpa, self.accounts, office('boss', 'principal')[1]):
            data = client.get('/api/time-to-pay/').data
            self.assertEqual(data['plans'][0]['paid'], '0.00')
            self.assertEqual(data['plans'][0]['total'], '900000.00')
            self.assertEqual(data['totals']['plans_running'], 1)
            self.assertEqual(data['totals']['plans_remaining'], '900000.00')
        self.assertTrue(self.dpfpa.get('/api/time-to-pay/').data['can_extend'])
        self.assertFalse(self.accounts.get('/api/time-to-pay/').data['can_extend'])
        self.assertEqual(office('exam', 'exam_officer')[1].get('/api/time-to-pay/').status_code, 403)

    def test_a_month_is_tracked_as_it_is_paid(self):
        plan = self.plan(monthly='300000')
        self.pay('450000', 'M', on=date(2026, 11, 20))
        months = time_to_pay.plan_row(plan, date(2026, 11, 21))['months']
        self.assertEqual([(m['state'], m['paid']) for m in months],
                         [('paid', '300000.00'), ('part', '150000.00'), ('unpaid', '0.00')])
        self.assertEqual(time_to_pay.plan_row(plan, date(2026, 11, 21))['paid'], '450000.00')

    def test_lapsed_and_passed_dates_are_counted_apart(self):
        self.plan()
        self.waiver('40000', date(2026, 11, 2), instalment=2, family=ChargeType.FEE)
        summary = time_to_pay.summary(self.year, date(2026, 12, 8))
        self.assertEqual(summary['totals']['plans_lapsed'], 1)
        self.assertEqual(summary['totals']['lapsed_owed'], '900000.00')
        self.assertEqual(summary['totals']['waivers_expired'], '40000.00')
        self.assertEqual(summary['totals']['plans_running'], 0)


class FakeDate(date):
    @classmethod
    def today(cls):
        return date(2026, 11, 10)


class PortalTests(MonthlyPlanBase):
    def setUp(self):
        super().setUp()
        module = Module.objects.create(name='Anatomy', code='PST04101', teacher='T', class_level=self.level4,
                                       semester=self.sem1, programme=self.pst, credits=3)
        enrollment = Student.objects.create(nactvet_reg_no=self.profile.nactvet_reg_no, name=self.profile.name,
                                            profile=self.profile, module=module)
        enrollment.set_portal_pin('Portal#2026', require_change=False)
        enrollment.save()
        self.portal = Client()
        session = self.portal.session
        session['student_id'] = enrollment.id
        session.save()

    def test_the_student_reads_their_plan_and_their_date(self):
        self.plan()
        with mock.patch('attendance.time_to_pay.date', FakeDate):
            page = self.portal.get('/student-dashboard/')
        self.assertContains(page, 'id="monthly-plan-notice"')
        self.assertContains(page, 'id="monthly-plan-panel"')
        self.assertContains(page, '30 Nov 2026')
        self.assertEqual([r for r in page.context['fee_reminders'] if r['group'] == 'Tuition fee'], [])
        self.assertTrue(semester_pass.status(self.profile, self.sem1, today=NOV)['ok'])

    def test_a_waiver_s_date_on_the_dashboard(self):
        self.waiver('500000', date(2026, 12, 10))
        with mock.patch('attendance.time_to_pay.date', FakeDate):
            page = self.portal.get('/student-dashboard/')
        self.assertContains(page, 'you said you will pay by')
        self.assertContains(page, '10 Dec 2026')


class TimeframeAndPutBackTests(MonthlyPlanBase):
    """Said 8 November 2026: a plan is set by its timeframe — the amount a month or
    the number of months, the other following — and money waived before waivers had
    dates goes back on the bill, into the plan, when DPFPA agrees."""

    def ask(self, **extra):
        data = {'monthly_amount': Decimal('0'), 'first_month': date(2026, 11, 15), 'reason': 'Salary'}
        data.update(extra)
        return time_to_pay.propose_plan(self.profile, self.year, data.pop('monthly_amount'), data.pop('first_month'),
                                        data.pop('reason'), actor=self.accountant, today=NOV, **data)

    def old_waiver(self, amount='50000'):
        charge = self.tuition_charges()[0]
        finance.waive_charge(charge, Decimal(amount), 'Sponsor pays — approved by Dpf', actor=self.accountant)
        return WaiverRequest.objects.create(profile=self.profile, academic_year=self.year, family=ChargeType.FEE,
                                            installment_number=1, amount=Decimal(amount), reason='Sponsor pays',
                                            status=WaiverRequest.APPROVED)

    def test_set_by_months_the_amount_follows(self):
        plan = self.ask(months=3)
        self.assertEqual((plan.monthly_amount, plan.months), (Decimal('333334'), 3))
        self.assertEqual([row['amount'] for row in time_to_pay.plan_rows(plan.monthly_amount, plan.total, plan.first_due)],
                         [Decimal('333334'), Decimal('333334'), Decimal('333332')])

    def test_set_by_amount_the_months_follow(self):
        plan = self.ask(monthly_amount=Decimal('300000'))
        self.assertEqual(len(time_to_pay.plan_rows(plan.monthly_amount, plan.total, plan.first_due)), 4)
        self.assertIsNone(plan.months)

    def test_one_or_the_other(self):
        for bad in ({'months': 3, 'monthly_amount': Decimal('100000')}, {}, {'months': 40}):
            with self.assertRaises(time_to_pay.PlanError):
                self.ask(**bad)
        with self.assertRaises(time_to_pay.PlanError):
            self.ask(months=12)                                                  # would end after the year closes

    def test_set_by_months_it_is_worked_out_again_on_approval(self):
        plan = self.ask(months=4)
        self.assertEqual(plan.monthly_amount, Decimal('250000'))
        self.pay('200000', 'MEANWHILE', on=date(2026, 11, 3))
        plan = time_to_pay.decide_plan(plan, approve=True, actor=self.dpfpa_user, today=NOV)
        self.assertEqual((plan.total, plan.monthly_amount), (Decimal('800000.00'), Decimal('200000')))

    def test_an_old_waiver_goes_back_on_the_bill_into_the_plan(self):
        old = self.old_waiver()
        invoice = finance.issue_invoice(self.profile, self.tuition_charges(), self.year)
        self.assertEqual(finance.money(invoice.total), Decimal('950000.00'))
        plan = self.ask(monthly_amount=Decimal('250000'), restore=[old.id])
        self.assertEqual(plan.total, Decimal('1000000.00'))                     # the 50,000 is in what it pays off
        self.assertEqual(time_to_pay.plan_row(plan, NOV)['puts_back'], ['Tuition fee, instalment 1: 50,000.00, waived before'])
        charge = self.tuition_charges()[0]
        self.assertEqual(charge.waived_amount, Decimal('50000.00'))             # nothing moves before DPFPA agrees
        plan = time_to_pay.decide_plan(plan, approve=True, actor=self.dpfpa_user, today=NOV)
        charge.refresh_from_db()
        old.refresh_from_db()
        invoice.refresh_from_db()
        self.assertEqual(charge.waived_amount, Decimal('0.00'))
        self.assertEqual((old.status, plan.total), (WaiverRequest.REPLACED, Decimal('1000000.00')))
        self.assertIn('Put back on the bill', old.decision_note)
        self.assertEqual(finance.money(invoice.total), Decimal('1000000.00'))   # the invoice says so too
        self.assertTrue(FinanceAuditLog.objects.filter(action='waiver.restore', entity_id=old.id).exists())

    def test_refused_the_old_waiver_stays_off_the_bill(self):
        old = self.old_waiver()
        plan = self.ask(monthly_amount=Decimal('250000'), restore=[old.id])
        time_to_pay.decide_plan(plan, approve=False, note='No', actor=self.dpfpa_user, today=NOV)
        old.refresh_from_db()
        self.assertEqual((old.status, self.tuition_charges()[0].waived_amount), (WaiverRequest.APPROVED, Decimal('50000.00')))

    def test_only_an_old_tuition_waiver_can_be_put_back(self):
        dated = self.waiver('50000', date(2026, 12, 10))
        with self.assertRaises(time_to_pay.PlanError):
            self.ask(monthly_amount=Decimal('250000'), restore=[dated.id])

    def test_the_form_knows_the_old_waivers(self):
        old = self.old_waiver()
        data = self.accounts.get(f'/api/monthly-plans/options/?profile_id={self.profile.id}&academic_year_id={self.year.id}').data
        self.assertEqual((data['tuition_owed'], data['old_waivers'][0]['id'], data['old_waivers'][0]['amount']),
                         ('950000.00', old.id, '50000.00'))
        self.assertEqual(self.dpfpa.get(f'/api/monthly-plans/options/?profile_id={self.profile.id}').status_code, 403)
        asked = self.accounts.post('/api/monthly-plans/', {
            'profile_id': self.profile.id, 'academic_year_id': self.year.id, 'months': '4',
            'first_month': f'{date.today():%Y-%m}', 'reason': 'Salary', 'restore': [old.id]}, format='json')
        self.assertEqual(asked.status_code, 201, asked.data)
        self.assertEqual((asked.data['plan']['total'], asked.data['plan']['months_set']), ('1000000.00', 4))
