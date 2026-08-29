"""One invoice per payment per year, expiring when the year does.

An invoice used to cover whichever instalments the student ticked, so paying
tuition in five instalments produced five references and the accountant could
not see the five deposits as one bill being worked off. An invoice now covers
every instalment of one payment and keeps its reference all year.

Any student who already generated more than one invoice for the same payment
keeps the first reference they were given — that is the number written on the
slips they have already taken to the bank — and the later duplicates are
cancelled. Nothing financial moves: payments and their allocations are
untouched, and a cancelled invoice still shows what it was for.
"""

from datetime import date

from django.db import migrations, models


def _closes_on(year):
    """The historical models have no closes_on property, so repeat the rule:
    the day the office set, else 30 June of the closing calendar year."""
    if year.end_date:
        return year.end_date
    try:
        return date(int(year.name.split('/')[1]), 6, 30)
    except (IndexError, ValueError):
        return None


def merge_duplicate_invoices(apps, schema_editor):
    Invoice = apps.get_model('attendance', 'Invoice')
    InvoiceLine = apps.get_model('attendance', 'InvoiceLine')

    seen = {}
    for invoice in Invoice.objects.filter(cancelled=False).order_by('id'):
        key = (invoice.profile_id, invoice.academic_year_id, invoice.invoice_group)
        keeper = seen.get(key)
        if keeper is None:
            seen[key] = invoice
            continue

        # Fold the duplicate's lines onto the reference the student is already
        # quoting, skipping charges it already bills for.
        billed = set(
            InvoiceLine.objects.filter(invoice=keeper).values_list('charge_id', flat=True)
        )
        for line in InvoiceLine.objects.filter(invoice=invoice):
            if line.charge_id in billed:
                continue
            InvoiceLine.objects.create(
                invoice=keeper, charge_id=line.charge_id, amount=line.amount)
            billed.add(line.charge_id)

        invoice.cancelled = True
        invoice.cancelled_reason = (
            f'Merged into {keeper.reference}, which now covers every instalment '
            f'of this payment.'
        )[:300]
        invoice.save(update_fields=['cancelled', 'cancelled_reason'])

    # Every surviving invoice was dated to one instalment. It now covers all of
    # them, so it has to stand until the year ends — otherwise a student opens
    # a live bill that says it expired months ago.
    for invoice in seen.values():
        closes_on = _closes_on(invoice.academic_year)
        latest = max(
            (line.charge.due_date
             for line in InvoiceLine.objects.filter(invoice=invoice).select_related('charge')),
            default=None,
        )
        expiry = max([d for d in (closes_on, latest) if d is not None], default=None)
        if expiry and invoice.due_date != expiry:
            invoice.due_date = expiry
            invoice.save(update_fields=['due_date'])


def unmerge(apps, schema_editor):
    """Irreversible in substance — the split cannot be reconstructed — but the
    constraint comes off cleanly, so leave the data as it stands."""


class Migration(migrations.Migration):

    dependencies = [
        ('attendance', '0028_invoice_grouping'),
    ]

    operations = [
        migrations.AddField(
            model_name='academicyear',
            name='end_date',
            field=models.DateField(
                blank=True, null=True,
                help_text='The day the year closes. Every invoice raised for this year '
                          'expires on it, so a student paying by instalments keeps one '
                          'invoice all year. Left blank, it is taken to be 30 June of '
                          'the closing year.',
            ),
        ),
        migrations.RunPython(merge_duplicate_invoices, unmerge),
        migrations.AddConstraint(
            model_name='invoice',
            constraint=models.UniqueConstraint(
                condition=models.Q(('cancelled', False)),
                fields=('profile', 'academic_year', 'invoice_group'),
                name='one_live_invoice_per_payment_per_year',
            ),
        ),
    ]
