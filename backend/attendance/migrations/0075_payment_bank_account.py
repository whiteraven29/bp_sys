# The account a payment went into, so money paid beyond the bill stays as
# credit on that account and pays only its charges (said 20 October 2026).
# Payments already recorded take their invoice's account; one recorded without
# an invoice stays blank, and its credit may settle any charge.

import django.db.models.deletion
from django.db import migrations, models


def from_invoices(apps, schema_editor):
    Payment = apps.get_model('attendance', 'Payment')
    for payment in Payment.objects.filter(invoice__bank_account__isnull=False).select_related('invoice'):
        Payment.objects.filter(pk=payment.pk).update(bank_account_id=payment.invoice.bank_account_id)


class Migration(migrations.Migration):

    dependencies = [
        ('attendance', '0074_names_nhif_and_new_only_items'),
    ]

    operations = [
        migrations.AddField(
            model_name='payment',
            name='bank_account',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='payments', to='attendance.bankaccount'),
        ),
        migrations.RunPython(from_invoices, migrations.RunPython.noop),
    ]
