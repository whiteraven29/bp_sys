# A shortfall waiver names an instalment, not a charge (said 22 October 2026):
# the accountant picks tuition fee or direct costs, which instalment is short,
# and what is short; DPFPA approves. Requests already made keep their charge.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('attendance', '0078_waiver_requests'),
    ]

    operations = [
        migrations.AlterField(
            model_name='waiverrequest',
            name='charge',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='waiver_requests', to='attendance.studentcharge'),
        ),
        migrations.AddField(
            model_name='waiverrequest',
            name='academic_year',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='waiver_requests', to='attendance.academicyear'),
        ),
        migrations.AddField(
            model_name='waiverrequest',
            name='family',
            field=models.CharField(blank=True, help_text='Tuition fee or direct costs.', max_length=20),
        ),
        migrations.AddField(
            model_name='waiverrequest',
            name='installment_number',
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='waiverrequest',
            name='amount',
            field=models.DecimalField(decimal_places=2, help_text='An instalment: what is short, to be waived. One charge (the first requests): what is waived on it in all, once approved.', max_digits=12),
        ),
    ]
