from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('attendance', '0014_student_must_change_portal_password')]

    operations = [
        migrations.AddField(
            model_name='module',
            name='credits',
            field=models.PositiveSmallIntegerField(
                default=1,
                help_text='Module credits used in the weighted GPA calculation.',
                validators=[MinValueValidator(1)],
            ),
        ),
        migrations.AddField(
            model_name='studentresult',
            name='supplementary_mark',
            field=models.DecimalField(
                blank=True, decimal_places=2, max_digits=5, null=True,
                validators=[
                    MinValueValidator(Decimal('0')),
                    MaxValueValidator(Decimal('100')),
                ],
                verbose_name='Supplementary Examination (raw /100)',
            ),
        ),
    ]
