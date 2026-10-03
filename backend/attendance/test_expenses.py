"""Expenses, each rule a test:

  * the accountant keeps the categories and the year's budget for each;
  * the accountant records an expense; it waits for approval, and the
    Principal and DPFPA are told;
  * the Principal or DPFPA approves or rejects — one decision is enough, a
    rejection says why, and an approval that goes over budget says why;
  * nobody approves an expense they recorded;
  * only an approved expense is paid, with the method, and (cash aside) the
    account and the reference;
  * a wrong entry is cancelled with a reason, never deleted;
  * the Principal or DPFPA may query a waiting expense instead; the accountant
    answers (correcting it if need be) and it waits for approval again;
  * the money comes from office funds or petty cash; petty cash is paid in
    cash, never beyond the float, which the accountant tops up from a bank;
  * every step is in the audit trail; the Executive Summary shows the spending.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIClient

from .models import (
    AcademicYear, BankAccount, Expense, ExpenseCategory, FinanceAuditLog, Notification, PettyCashTopUp,
)
from .views import set_roles

User = get_user_model()


class ExpenseBase(TestCase):
    def office(self, username, *roles):
        user = User.objects.create_user(username, password='pw')
        set_roles(user, list(roles), full_name=username.title())
        client = APIClient()
        client.force_authenticate(user)
        return user, client

    def setUp(self):
        self.year = AcademicYear.objects.create(name='2026/2027', is_active=True)
        self.accountant_user, self.accountant = self.office('money', 'accountant')
        self.principal_user, self.principal = self.office('boss', 'principal')
        self.dpfpa_user, self.dpfpa = self.office('dpfpa', 'dpfpa')
        self.dparc_user, self.dparc = self.office('dparc', 'dparc')
        self.account = BankAccount.objects.create(account_name='BPHACOH', account_number='0150000000009',
                                                  purpose='Operations')
        made = self.accountant.post('/api/expense-categories/', {'name': 'Stationery'}, format='json')
        self.category = ExpenseCategory.objects.get(id=made.data['id'])
        self.accountant.post('/api/expense-budgets/', {'category': self.category.id, 'amount': '500000'},
                             format='json')

    def record(self, amount='120000', client=None, source=None):
        data = {'category': self.category.id, 'description': 'Printing paper, 20 reams',
                'payee': 'Kariakoo Stationers', 'amount': amount, 'incurred_on': '2026-10-05'}
        if source:
            data['source'] = source
        return (client or self.accountant).post('/api/expenses/', data, format='json')

    def act(self, client, expense_id, action, **data):
        return client.post(f'/api/expenses/{expense_id}/{action}/', data, format='json')


class RecordingTests(ExpenseBase):
    def test_the_accountant_records_and_it_waits(self):
        made = self.record()
        self.assertEqual(made.status_code, 201, made.data)
        self.assertEqual(made.data['status'], 'pending')
        told = Notification.objects.filter(kind=Notification.EXPENSE_SUBMITTED)
        self.assertEqual({n.user_id for n in told}, {self.principal_user.id, self.dpfpa_user.id})

    def test_nobody_else_records(self):
        self.assertEqual(self.record(client=self.principal).status_code, 403)

    def test_with_a_receipt(self):
        made = self.accountant.post('/api/expenses/', {
            'category': self.category.id, 'description': 'Toner', 'payee': 'Office Mart',
            'amount': '45000', 'incurred_on': '2026-10-05',
            'attachment': SimpleUploadedFile('receipt.pdf', b'%PDF-1.4 receipt', content_type='application/pdf')},
            format='multipart')
        self.assertEqual(made.status_code, 201, made.data)
        self.assertTrue(made.data['has_attachment'])
        self.assertEqual(self.dpfpa.get(f'/api/expenses/{made.data["id"]}/attachment/').status_code, 200)
        self.assertEqual(self.dparc.get(f'/api/expenses/{made.data["id"]}/attachment/').status_code, 403)

    def test_dparc_does_not_see_the_register(self):
        self.assertEqual(self.dparc.get('/api/expenses/').status_code, 403)


class ApprovingTests(ExpenseBase):
    def test_dpfpa_approves_and_one_approval_is_enough(self):
        expense_id = self.record().data['id']
        approved = self.act(self.dpfpa, expense_id, 'decide', decision='approve')
        self.assertEqual(approved.status_code, 200, approved.data)
        self.assertEqual(approved.data['status'], 'approved')
        again = self.act(self.principal, expense_id, 'decide', decision='approve')
        self.assertEqual(again.status_code, 400)
        self.assertIn('already', again.data['detail'])
        self.assertTrue(Notification.objects.filter(kind=Notification.EXPENSE_DECIDED,
                                                    user=self.accountant_user).exists())

    def test_the_principal_may_approve_instead(self):
        expense_id = self.record().data['id']
        self.assertEqual(self.act(self.principal, expense_id, 'decide', decision='approve').data['status'],
                         'approved')

    def test_a_rejection_says_why(self):
        expense_id = self.record().data['id']
        self.assertEqual(self.act(self.dpfpa, expense_id, 'decide', decision='reject').status_code, 400)
        rejected = self.act(self.dpfpa, expense_id, 'decide', decision='reject', note='Get a second quote')
        self.assertEqual(rejected.data['status'], 'rejected')
        self.assertEqual(rejected.data['decision_note'], 'Get a second quote')

    def test_going_over_budget_needs_a_reason(self):
        expense_id = self.record('600000').data['id']
        refused = self.act(self.dpfpa, expense_id, 'decide', decision='approve')
        self.assertEqual(refused.status_code, 400)
        self.assertIn('over its budget', refused.data['detail'])
        approved = self.act(self.dpfpa, expense_id, 'decide', decision='approve', note='Exam season')
        self.assertEqual(approved.data['status'], 'approved')

    def test_nobody_approves_their_own(self):
        both, client = self.office('both', 'accountant', 'principal')
        expense_id = self.record(client=client).data['id']
        refused = self.act(client, expense_id, 'decide', decision='approve')
        self.assertEqual(refused.status_code, 403)

    def test_the_accountant_cannot_approve(self):
        expense_id = self.record().data['id']
        self.assertEqual(self.act(self.accountant, expense_id, 'decide', decision='approve').status_code, 403)


class PayingTests(ExpenseBase):
    def approved(self):
        expense_id = self.record().data['id']
        self.act(self.dpfpa, expense_id, 'decide', decision='approve')
        return expense_id

    def test_only_an_approved_expense_is_paid(self):
        expense_id = self.record().data['id']
        refused = self.act(self.accountant, expense_id, 'pay', method='cash')
        self.assertEqual(refused.status_code, 400)

    def test_a_bank_payment_names_the_account_and_reference(self):
        expense_id = self.approved()
        self.assertEqual(self.act(self.accountant, expense_id, 'pay', method='bank').status_code, 400)
        paid = self.act(self.accountant, expense_id, 'pay', method='bank', bank_account=self.account.id,
                        reference='TRX-001', paid_on='2026-10-07')
        self.assertEqual(paid.status_code, 200, paid.data)
        self.assertEqual(paid.data['status'], 'paid')
        self.assertIn('0150000000009', paid.data['bank_account'])

    def test_cash_needs_no_account(self):
        paid = self.act(self.accountant, self.approved(), 'pay', method='cash')
        self.assertEqual(paid.data['status'], 'paid')

    def test_only_the_accountant_pays(self):
        self.assertEqual(self.act(self.dpfpa, self.approved(), 'pay', method='cash').status_code, 403)


class CancellingTests(ExpenseBase):
    def test_a_wrong_entry_is_cancelled_with_a_reason_and_kept(self):
        expense_id = self.record().data['id']
        self.assertEqual(self.act(self.accountant, expense_id, 'cancel').status_code, 400)
        cancelled = self.act(self.accountant, expense_id, 'cancel', reason='Entered twice')
        self.assertEqual(cancelled.data['status'], 'cancelled')
        self.assertTrue(Expense.objects.filter(id=expense_id).exists())

    def test_a_decided_expense_cannot_be_cancelled(self):
        expense_id = self.record().data['id']
        self.act(self.dpfpa, expense_id, 'decide', decision='approve')
        self.assertEqual(self.act(self.accountant, expense_id, 'cancel', reason='x').status_code, 400)


class TheBudgetAndTheSummaryTests(ExpenseBase):
    def test_the_position_counts_what_is_committed(self):
        first = self.record('120000').data['id']
        self.act(self.dpfpa, first, 'decide', decision='approve')
        self.act(self.accountant, first, 'pay', method='cash')
        self.record('80000')                                     # still waiting
        position = self.principal.get('/api/expense-budgets/').data
        row = next(r for r in position['rows'] if r['category'] == 'Stationery')
        self.assertEqual(Decimal(row['budget']), Decimal('500000'))
        self.assertEqual(Decimal(row['paid']), Decimal('120000'))
        self.assertEqual(Decimal(row['pending']), Decimal('80000'))
        self.assertEqual(Decimal(row['left']), Decimal('380000'))
        self.assertEqual(position['waiting'], 1)

    def test_the_executive_summary_shows_spending(self):
        first = self.record('120000').data['id']
        self.act(self.dpfpa, first, 'decide', decision='approve')
        self.act(self.accountant, first, 'pay', method='cash')
        spending = self.dpfpa.get('/api/executive/finance/').data['spending']
        self.assertEqual(Decimal(spending['paid_out']), Decimal('120000'))
        self.assertEqual(Decimal(spending['net']), Decimal('-120000'))

    def test_only_the_accountant_sets_a_budget(self):
        refused = self.dpfpa.post('/api/expense-budgets/', {'category': self.category.id, 'amount': '1'},
                                  format='json')
        self.assertEqual(refused.status_code, 403)

    def test_every_step_is_in_the_audit_trail(self):
        expense_id = self.record().data['id']
        self.act(self.dpfpa, expense_id, 'decide', decision='approve')
        self.act(self.accountant, expense_id, 'pay', method='cash')
        actions = set(FinanceAuditLog.objects.filter(entity='Expense', entity_id=expense_id)
                      .values_list('action', flat=True))
        self.assertEqual(actions, {'expense.record', 'expense.approve', 'expense.pay'})


class QueryTests(ExpenseBase):
    def test_dpfpa_queries_and_the_accountant_answers(self):
        expense_id = self.record().data['id']
        self.assertEqual(self.act(self.dpfpa, expense_id, 'query').status_code, 400)   # no question
        queried = self.act(self.dpfpa, expense_id, 'query', note='Why 20 reams? We bought 10 in September.')
        self.assertEqual(queried.status_code, 200, queried.data)
        self.assertEqual(queried.data['status'], 'queried')
        self.assertTrue(Notification.objects.filter(kind=Notification.EXPENSE_QUERIED,
                                                    user=self.accountant_user).exists())
        # Nobody approves it while the question stands.
        blocked = self.act(self.principal, expense_id, 'decide', decision='approve')
        self.assertEqual(blocked.status_code, 400)
        self.assertIn('queried', blocked.data['detail'])

        answered = self.act(self.accountant, expense_id, 'answer', note='Exam season; cut to 10.',
                            amount='60000')
        self.assertEqual(answered.status_code, 200, answered.data)
        self.assertEqual(answered.data['status'], 'pending')
        self.assertEqual(Decimal(answered.data['amount']), Decimal('60000'))
        self.assertEqual(answered.data['answer_note'], 'Exam season; cut to 10.')
        told = Notification.objects.filter(kind=Notification.EXPENSE_QUERIED,
                                           title__startswith='Query answered')
        self.assertEqual({n.user_id for n in told}, {self.principal_user.id, self.dpfpa_user.id})
        approved = self.act(self.principal, expense_id, 'decide', decision='approve')
        self.assertEqual(approved.data['status'], 'approved')

    def test_only_the_approvers_query_and_only_the_accountant_answers(self):
        expense_id = self.record().data['id']
        self.assertEqual(self.act(self.accountant, expense_id, 'query', note='?').status_code, 403)
        self.act(self.dpfpa, expense_id, 'query', note='Quote?')
        self.assertEqual(self.act(self.dpfpa, expense_id, 'answer', note='x').status_code, 403)
        self.assertEqual(self.act(self.accountant, expense_id, 'answer').status_code, 400)  # no answer

    def test_a_queried_expense_may_be_withdrawn(self):
        expense_id = self.record().data['id']
        self.act(self.dpfpa, expense_id, 'query', note='Duplicate of last week?')
        cancelled = self.act(self.accountant, expense_id, 'cancel', reason='Yes, entered twice')
        self.assertEqual(cancelled.data['status'], 'cancelled')

    def test_query_and_answer_are_in_the_audit_trail(self):
        expense_id = self.record().data['id']
        self.act(self.dpfpa, expense_id, 'query', note='Quote?')
        self.act(self.accountant, expense_id, 'answer', note='Attached')
        actions = set(FinanceAuditLog.objects.filter(entity='Expense', entity_id=expense_id)
                      .values_list('action', flat=True))
        self.assertTrue({'expense.query', 'expense.answer'} <= actions)


class PettyCashTests(ExpenseBase):
    def top_up(self, amount='100000', **extra):
        return self.accountant.post('/api/petty-cash/', {
            'amount': amount, 'bank_account': self.account.id, 'reference': 'CHQ 0041',
            'received_on': '2026-10-02', **extra}, format='json')

    def approved_petty(self, amount):
        expense_id = self.record(amount, source='petty_cash').data['id']
        self.act(self.dpfpa, expense_id, 'decide', decision='approve')
        return expense_id

    def test_an_expense_says_where_the_money_comes_from(self):
        self.assertEqual(self.record().data['source'], 'office')
        self.assertEqual(self.record(source='petty_cash').data['source_display'], 'Petty cash')
        self.assertEqual(self.record(source='mattress').status_code, 400)

    def test_petty_cash_is_paid_in_cash_out_of_the_float(self):
        self.top_up('100000')
        paid = self.act(self.accountant, self.approved_petty('30000'), 'pay', method='bank')
        self.assertEqual(paid.status_code, 200, paid.data)
        self.assertEqual(paid.data['payment_method'], 'Cash')
        self.assertEqual(paid.data['bank_account'], '')
        float_now = self.dpfpa.get('/api/petty-cash/').data
        self.assertEqual(Decimal(float_now['balance']), Decimal('70000'))
        self.assertEqual(Decimal(float_now['spent']), Decimal('30000'))

    def test_never_beyond_the_float(self):
        self.top_up('20000')
        refused = self.act(self.accountant, self.approved_petty('30000'), 'pay', method='cash')
        self.assertEqual(refused.status_code, 400)
        self.assertIn('Top the float up', refused.data['detail'])

    def test_only_the_accountant_tops_up_from_a_named_account(self):
        refused = self.dpfpa.post('/api/petty-cash/', {'amount': '5000', 'bank_account': self.account.id},
                                  format='json')
        self.assertEqual(refused.status_code, 403)
        self.assertEqual(self.accountant.post('/api/petty-cash/', {'amount': '5000'}, format='json')
                         .status_code, 400)
        self.assertEqual(self.top_up().status_code, 200)

    def test_a_wrong_top_up_is_reversed_with_a_reason(self):
        self.top_up('100000')
        self.assertEqual(self.accountant.post('/api/petty-cash/', {'amount': '-100000'}, format='json')
                         .status_code, 400)
        reversed_ = self.accountant.post('/api/petty-cash/', {'amount': '-100000', 'note': 'Typed twice'},
                                         format='json')
        self.assertEqual(Decimal(reversed_.data['balance']), Decimal('0'))
        self.assertEqual(PettyCashTopUp.objects.count(), 2)

    def test_the_summary_shows_the_float(self):
        self.top_up('100000')
        spending = self.principal.get('/api/executive/finance/').data['spending']
        self.assertEqual(Decimal(spending['petty_cash']['balance']), Decimal('100000'))
