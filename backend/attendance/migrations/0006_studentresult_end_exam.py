from decimal import Decimal
from django.db import migrations, models
import django.core.validators


class Migration(migrations.Migration):

    dependencies = [
        ('attendance', '0005_semester_cutoff_dates'),
    ]

    operations = [
        migrations.AddField(
            model_name='studentresult',
            name='end_theory',
            field=models.DecimalField(
                blank=True, decimal_places=2, max_digits=5, null=True,
                validators=[
                    django.core.validators.MinValueValidator(Decimal('0')),
                    django.core.validators.MaxValueValidator(Decimal('100')),
                ],
                verbose_name='End of Semester – Theory/Written (raw /100)',
            ),
        ),
        migrations.AddField(
            model_name='studentresult',
            name='end_practical',
            field=models.DecimalField(
                blank=True, decimal_places=2, max_digits=5, null=True,
                validators=[
                    django.core.validators.MinValueValidator(Decimal('0')),
                    django.core.validators.MaxValueValidator(Decimal('100')),
                ],
                verbose_name='End of Semester – Practical (raw /100)',
            ),
        ),
    ]
