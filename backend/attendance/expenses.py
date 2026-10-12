"""What the college spends, and who allowed it.

The way it runs:

    accountant requests ──►  WAITING  ──►  Principal or DPFPA  ──►  APPROVED  ──►  accountant pays  ──►  PAID
    (category, payee,         │  ▲         (one approval is          │
     amount, receipt,         │  │          enough)                  └─► the payment: date, method,
     office funds or          │  │                                       account, reference
     petty cash)              │  └── accountant answers (may correct the request)
                              ├──► QUERIED by the Principal or DPFPA (a question)
                              ├──► REJECTED (with the reason)
                              └──► CANCELLED by the accountant (a wrong entry, with the reason)

Where the money comes from:

  * office funds — paid from a college bank account (or cash from the office);
  * petty cash — the float the accountant holds for small and emergency
    spending, always paid in cash, and never more than the float holds. The
    float is topped up from a bank account (PettyCashTopUp).

Rules kept here, not in the screens:

  * nobody approves an expense they recorded themselves;
  * a rejection, a cancellation, and an approval that takes a category over its
    budget, each need a written reason;
  * a query needs the question written down, and the accountant's answer
    sends it back to waiting; an expense under query cannot be approved;
  * only an approved expense can be paid; cash aside, the bank account it was
    paid from is named; petty cash is paid in cash, within the float;
  * nothing is deleted. Every step is written to the finance audit trail.
"""

from datetime import date
from decimal import Decimal

from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from . import finance, notifications
from .models import Expense, ExpenseBudget, ExpenseCategory, Notification, PettyCashTopUp
from .roles import can_approve_expenses, is_accountant

ZERO = Decimal('0.00')


class ExpenseError(ValueError):
    """Something about this expense the rules do not allow; the message says what."""


#: What a receipt or quotation may be, by extension and by its first bytes.
#: Anything else — an HTML page above all — could run in the approver's
#: browser when it is opened.
RECEIPT_TYPES = {
    'pdf': (b'%PDF',), 'png': (b'\x89PNG',), 'jpg': (b'\xff\xd8\xff',), 'jpeg': (b'\xff\xd8\xff',),
}
RECEIPT_MAX_BYTES = 10 * 1024 * 1024


def check_receipt(upload):
    """A receipt is a PDF, JPG or PNG of at most 10 MB, and is what its name
    says it is. Raises ExpenseError otherwise."""
    if not upload:
        return
    ext = str(getattr(upload, 'name', '')).rsplit('.', 1)[-1].lower()
    if ext not in RECEIPT_TYPES:
        raise ExpenseError('A receipt or quotation is a PDF, JPG or PNG.')
    if getattr(upload, 'size', 0) > RECEIPT_MAX_BYTES:
        raise ExpenseError('A receipt must be 10 MB or smaller.')
    head = upload.read(8)
    upload.seek(0)
    if not head.startswith(RECEIPT_TYPES[ext]):
        raise ExpenseError(f'That file is not a real {ext.upper()}.')


# ── where a category stands against its budget ───────────────────────────────

#: Spending that counts against the budget: approved (committed) and paid.
COMMITTED = (Expense.APPROVED, Expense.PAID)


def category_position(category, academic_year, *, leaving_out=None):
    """Budget, committed spending and what is left for one category."""
    budget = (ExpenseBudget.objects.filter(category=category, academic_year=academic_year)
              .values_list('amount', flat=True).first())
    spent = Expense.objects.filter(category=category, academic_year=academic_year,
                                   status__in=COMMITTED)
    if leaving_out is not None:
        spent = spent.exclude(pk=leaving_out.pk)
    committed = spent.aggregate(total=Sum('amount'))['total'] or ZERO
    return {
        'budget': budget,
        'committed': committed,
        'left': (budget - committed) if budget is not None else None,
    }


def would_exceed(expense):
    """By how much approving this expense takes its category over budget; zero
    when it does not, or when the category has no budget set."""
    position = category_position(expense.category, expense.academic_year, leaving_out=expense)
    if position['budget'] is None:
        return ZERO
    over = position['committed'] + expense.amount - position['budget']
    return over if over > ZERO else ZERO


def budget_position(academic_year):
    """Every category for the year: budget, waiting, committed, paid, left.
    What the Executive Summary shows, and the accountant's register totals."""
    rows = []
    totals = {'budget': ZERO, 'pending': ZERO, 'approved': ZERO, 'paid': ZERO, 'committed': ZERO}
    budgets = {b.category_id: b.amount for b in
               ExpenseBudget.objects.filter(academic_year=academic_year)}
    sums = {}
    for row in (Expense.objects.filter(academic_year=academic_year)
                .values('category_id', 'status').annotate(total=Sum('amount'))):
        sums.setdefault(row['category_id'], {})[row['status']] = row['total'] or ZERO
    used = set(budgets) | set(sums)
    for category in ExpenseCategory.objects.filter(Q(is_active=True) | Q(id__in=used)):
        mine = sums.get(category.id, {})
        pending = mine.get(Expense.PENDING, ZERO) + mine.get(Expense.QUERIED, ZERO)
        approved = mine.get(Expense.APPROVED, ZERO)
        paid = mine.get(Expense.PAID, ZERO)
        committed = approved + paid
        budget = budgets.get(category.id)
        rows.append({
            'category_id': category.id, 'category': category.name,
            'budget': str(finance.money(budget)) if budget is not None else None,
            'pending': str(finance.money(pending)),
            'approved': str(finance.money(approved)),
            'paid': str(finance.money(paid)),
            'committed': str(finance.money(committed)),
            'left': str(finance.money(budget - committed)) if budget is not None else None,
            'over_budget': bool(budget is not None and committed > budget),
        })
        totals['budget'] += budget or ZERO
        totals['pending'] += pending
        totals['approved'] += approved
        totals['paid'] += paid
        totals['committed'] += committed
    return {
        'rows': rows,
        'totals': {key: str(finance.money(value)) for key, value in totals.items()},
        'waiting': Expense.objects.filter(academic_year=academic_year, status=Expense.PENDING).count(),
        'queried': Expense.objects.filter(academic_year=academic_year, status=Expense.QUERIED).count(),
        'petty_cash': petty_cash_position(academic_year),
    }


# ── the petty cash float ──────────────────────────────────────────────────────

def petty_cash_balance():
    """What the float holds now: every top-up, less every petty-cash expense
    paid out of it. Cash in a drawer does not know about academic years."""
    topped = PettyCashTopUp.objects.aggregate(total=Sum('amount'))['total'] or ZERO
    spent = (Expense.objects.filter(source=Expense.PETTY_CASH, status=Expense.PAID)
             .aggregate(total=Sum('amount'))['total'] or ZERO)
    return topped - spent


def petty_cash_position(academic_year):
    """The float now, and what went in and out of it this year."""
    topped = (PettyCashTopUp.objects.filter(academic_year=academic_year)
              .aggregate(total=Sum('amount'))['total'] or ZERO)
    spent = (Expense.objects.filter(academic_year=academic_year, source=Expense.PETTY_CASH,
                                    status=Expense.PAID).aggregate(total=Sum('amount'))['total'] or ZERO)
    return {'balance': str(finance.money(petty_cash_balance())),
            'topped_up': str(finance.money(topped)), 'spent': str(finance.money(spent))}


@transaction.atomic
def top_up_petty_cash(*, academic_year, amount, received_on=None, bank_account=None, reference='',
                      note='', actor=None, ip=None):
    """The accountant puts money into the float — or takes a wrong top-up back
    out with a negative amount, which needs a reason."""
    if not is_accountant(actor):
        raise ExpenseError('The accountant keeps the petty cash.')
    amount = finance.money(amount)
    if amount == ZERO:
        raise ExpenseError('Give the amount.')
    note = (note or '').strip()
    if amount < ZERO:
        if not note:
            raise ExpenseError('Say why the top-up is reversed.')
        if petty_cash_balance() + amount < ZERO:
            raise ExpenseError('The float does not hold that much.')
    elif bank_account is None:
        raise ExpenseError('Name the bank account the cash was drawn from.')
    topup = PettyCashTopUp.objects.create(
        academic_year=academic_year, amount=amount, received_on=received_on or date.today(),
        bank_account=bank_account, reference=(reference or '').strip()[:100], note=note[:300],
        recorded_by=actor)
    finance.audit('pettycash.topup', 'PettyCashTopUp', actor=actor, entity_id=topup.id,
                  summary=f'Petty cash {"+" if amount > 0 else ""}{amount}'
                          + (f' from {bank_account.account_number}' if bank_account else '')
                          + (f' — {note}' if note else ''), ip=ip)
    return topup


# ── the steps ─────────────────────────────────────────────────────────────────

def _audit(action, expense, actor, summary, ip=None):
    finance.audit(action, 'Expense', actor=actor, entity_id=expense.id, summary=summary, ip=ip)


@transaction.atomic
def record(*, academic_year, category, description, payee, amount, incurred_on,
           supplier_reference='', attachment=None, source=Expense.OFFICE, actor=None, ip=None):
    """The accountant requests an expense. It waits for approval."""
    if not is_accountant(actor):
        raise ExpenseError('Expenses are recorded by the accountant.')
    amount = finance.money(amount)
    if amount <= ZERO:
        raise ExpenseError('The amount must be more than zero.')
    if not (description or '').strip() or not (payee or '').strip():
        raise ExpenseError('Say what it is for and who is paid.')
    if not category.is_active:
        raise ExpenseError(f'{category} is no longer in use.')
    if source not in dict(Expense.SOURCE_CHOICES):
        raise ExpenseError('Say where the money comes from: office funds or petty cash.')
    check_receipt(attachment)
    expense = Expense.objects.create(
        source=source,
        academic_year=academic_year, category=category, description=description.strip()[:300],
        payee=payee.strip()[:200], amount=amount, incurred_on=incurred_on,
        supplier_reference=(supplier_reference or '').strip()[:100],
        attachment=attachment or '', recorded_by=actor,
    )
    _audit('expense.record', expense, actor,
           f'{category} · {expense.payee} · {amount} ({expense.get_source_display()}) — waiting for approval', ip)
    over = would_exceed(expense)
    title = f'Expense to approve: {expense.payee}'
    body = f'{category} · {amount:,.2f} from {expense.get_source_display().lower()}. {expense.description}'
    if over > ZERO:
        body += f' Approving it takes {category} {over:,.2f} over its budget.'
    notifications.notify(notifications.expense_approvers(), kind=Notification.EXPENSE_SUBMITTED,
                         title=title, body=body, link='executive:spending')
    return expense


@transaction.atomic
def decide(expense, *, approve, actor=None, note='', ip=None):
    """The Principal or DPFPA approves or rejects. One decision is enough."""
    expense = Expense.objects.select_for_update().get(pk=expense.pk)
    if not can_approve_expenses(actor):
        raise ExpenseError('The Principal or DPFPA approves expenses.')
    if expense.status == Expense.QUERIED:
        raise ExpenseError('This expense is queried: the accountant answers first.')
    if expense.status != Expense.PENDING:
        raise ExpenseError(f'This expense is already {expense.get_status_display().lower()}.')
    if expense.recorded_by_id and actor is not None and expense.recorded_by_id == actor.id:
        raise ExpenseError('Nobody approves an expense they recorded themselves.')
    note = (note or '').strip()
    if not approve and not note:
        raise ExpenseError('Say why it is rejected — the accountant reads it.')
    over = would_exceed(expense) if approve else ZERO
    if over > ZERO and not note:
        raise ExpenseError(
            f'This takes {expense.category} {over:,.2f} over its budget. Approve it with a note '
            f'saying why, or reject it.')
    expense.status = Expense.APPROVED if approve else Expense.REJECTED
    expense.decided_by = actor
    expense.decided_at = timezone.now()
    expense.decision_note = note[:300]
    expense.save(update_fields=['status', 'decided_by', 'decided_at', 'decision_note'])
    verdict = 'approved' if approve else 'rejected'
    _audit(f'expense.{"approve" if approve else "reject"}', expense, actor,
           f'{expense.payee} · {expense.amount} {verdict}' + (f' — {note}' if note else ''), ip)
    if expense.recorded_by_id:
        notifications.notify([expense.recorded_by], kind=Notification.EXPENSE_DECIDED,
                             title=f'Expense {verdict}: {expense.payee}',
                             body=note or f'{expense.amount:,.2f}', link='finance:expenses')
    return expense


@transaction.atomic
def query(expense, *, note, actor=None, ip=None):
    """The Principal or DPFPA asks the accountant about a waiting expense,
    instead of approving or rejecting it."""
    expense = Expense.objects.select_for_update().get(pk=expense.pk)
    if not can_approve_expenses(actor):
        raise ExpenseError('The Principal or DPFPA approves expenses.')
    if expense.status != Expense.PENDING:
        raise ExpenseError(f'Only a waiting expense is queried; this one is '
                           f'{expense.get_status_display().lower()}.')
    note = (note or '').strip()
    if not note:
        raise ExpenseError('Write the question — the accountant answers it.')
    expense.status = Expense.QUERIED
    expense.query_note = note[:300]
    expense.queried_by = actor
    expense.queried_at = timezone.now()
    expense.answer_note = ''
    expense.save(update_fields=['status', 'query_note', 'queried_by', 'queried_at', 'answer_note'])
    _audit('expense.query', expense, actor, f'{expense.payee} · {expense.amount} queried — {note}', ip)
    if expense.recorded_by_id:
        notifications.notify([expense.recorded_by], kind=Notification.EXPENSE_QUERIED,
                             title=f'Expense queried: {expense.payee}', body=note,
                             link='finance:expenses')
    return expense


#: What the accountant may correct when answering a query.
CORRECTABLE = ('category', 'description', 'payee', 'amount', 'incurred_on', 'supplier_reference',
               'source', 'attachment')


@transaction.atomic
def answer(expense, *, note, changes=None, actor=None, ip=None):
    """The accountant answers the query — correcting the request if need be —
    and it goes back to waiting for approval."""
    expense = Expense.objects.select_for_update().get(pk=expense.pk)
    if not is_accountant(actor):
        raise ExpenseError('The accountant answers a query.')
    if expense.status != Expense.QUERIED:
        raise ExpenseError('This expense is not queried.')
    note = (note or '').strip()
    if not note:
        raise ExpenseError('Write the answer — the Principal or DPFPA reads it.')
    changed = []
    for field, value in (changes or {}).items():
        if field not in CORRECTABLE or value in (None, ''):
            continue
        if field == 'amount':
            value = finance.money(value)
            if value <= ZERO:
                raise ExpenseError('The amount must be more than zero.')
        if field == 'source' and value not in dict(Expense.SOURCE_CHOICES):
            raise ExpenseError('Office funds or petty cash.')
        if field == 'category' and not value.is_active:
            raise ExpenseError(f'{value} is no longer in use.')
        if field == 'attachment':
            check_receipt(value)
        if field in ('description', 'payee', 'supplier_reference'):
            value = str(value).strip()[:300 if field == 'description' else 200 if field == 'payee' else 100]
        if getattr(expense, field) != value:
            changed.append(field)
            setattr(expense, field, value)
    expense.status = Expense.PENDING
    expense.answer_note = note[:300]
    expense.save(update_fields=['status', 'answer_note', *changed])
    _audit('expense.answer', expense, actor,
           f'{expense.payee} · {expense.amount} answered — {note}'
           + (f' (corrected: {", ".join(changed)})' if changed else ''), ip)
    notifications.notify(notifications.expense_approvers(), kind=Notification.EXPENSE_QUERIED,
                         title=f'Query answered: {expense.payee}',
                         body=f'{note} — {expense.category} · {expense.amount:,.2f}',
                         link='executive:spending')
    return expense


@transaction.atomic
def pay(expense, *, paid_on=None, method, bank_account=None, reference='', actor=None, ip=None):
    """The accountant records that an approved expense was paid."""
    expense = Expense.objects.select_for_update().get(pk=expense.pk)
    if not is_accountant(actor):
        raise ExpenseError('The accountant records the payment.')
    if expense.status != Expense.APPROVED:
        raise ExpenseError('Only an approved expense is paid.')
    if expense.source == Expense.PETTY_CASH:
        # Out of the drawer: cash, and no more than it holds.
        method, bank_account, reference = Expense.CASH, None, reference
        if petty_cash_balance() < expense.amount:
            raise ExpenseError(f'Petty cash holds {petty_cash_balance():,.2f}; this needs '
                               f'{expense.amount:,.2f}. Top the float up first.')
    if method not in dict(Expense.METHOD_CHOICES):
        raise ExpenseError('Say how it was paid: bank transfer, cheque, cash or mobile money.')
    if method != Expense.CASH and bank_account is None:
        raise ExpenseError('Name the bank account it was paid from.')
    if method != Expense.CASH and not (reference or '').strip():
        raise ExpenseError('Give the transfer, cheque or transaction number.')
    expense.status = Expense.PAID
    expense.paid_on = paid_on or date.today()
    expense.paid_by = actor
    expense.payment_method = method
    expense.bank_account = bank_account if method != Expense.CASH else None
    expense.payment_reference = (reference or '').strip()[:100]
    expense.save(update_fields=['status', 'paid_on', 'paid_by', 'payment_method',
                                'bank_account', 'payment_reference'])
    _audit('expense.pay', expense, actor,
           f'{expense.payee} · {expense.amount} paid {expense.paid_on} by '
           f'{expense.get_payment_method_display()} {expense.payment_reference}', ip)
    return expense


@transaction.atomic
def cancel(expense, *, reason, actor=None, ip=None):
    """The accountant withdraws an entry made in error, before it is decided."""
    expense = Expense.objects.select_for_update().get(pk=expense.pk)
    if not is_accountant(actor):
        raise ExpenseError('The accountant cancels a wrong entry.')
    if expense.status not in (Expense.PENDING, Expense.QUERIED):
        raise ExpenseError('Only an expense still waiting for approval can be cancelled.')
    reason = (reason or '').strip()
    if not reason:
        raise ExpenseError('Say why it is cancelled.')
    expense.status = Expense.CANCELLED
    expense.cancelled_reason = reason[:300]
    expense.save(update_fields=['status', 'cancelled_reason'])
    _audit('expense.cancel', expense, actor, f'{expense.payee} · {expense.amount} cancelled — {reason}', ip)
    return expense


@transaction.atomic
def set_budget(category, academic_year, amount, *, note='', actor=None, ip=None):
    """The accountant sets or revises a category's budget for the year."""
    if not is_accountant(actor):
        raise ExpenseError('Budgets are set by the accountant.')
    amount = finance.money(amount)
    if amount < ZERO:
        raise ExpenseError('A budget cannot be negative.')
    budget = ExpenseBudget.objects.filter(category=category, academic_year=academic_year).first()
    before = str(budget.amount) if budget else None
    if budget is None:
        budget = ExpenseBudget.objects.create(category=category, academic_year=academic_year,
                                              amount=amount, note=(note or '')[:300], updated_by=actor)
    else:
        budget.amount = amount
        budget.note = (note or '')[:300]
        budget.updated_by = actor
        budget.save(update_fields=['amount', 'note', 'updated_by', 'updated_at'])
    finance.audit('expense.budget', 'ExpenseBudget', actor=actor, entity_id=budget.id,
                  summary=f'{category} {academic_year}: {before or "none"} → {amount}',
                  before={'amount': before}, after={'amount': str(amount)}, ip=ip)
    return budget
