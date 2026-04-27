import decimal

import django.core.validators
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('attendance', '0003_session_exam_period_attendancerecord_sick_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='module',
            name='has_practical',
            field=models.BooleanField(
                default=False,
                help_text='Enable for modules assessed with both theory and practical components.',
                verbose_name='Has Practical Component',
            ),
        ),
        migrations.CreateModel(
            name='StudentResult',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('assign1',        models.DecimalField(blank=True, decimal_places=2, max_digits=5, null=True, validators=[django.core.validators.MinValueValidator(decimal.Decimal('0')), django.core.validators.MaxValueValidator(decimal.Decimal('100'))], verbose_name='Assignment 1 (raw /100)')),
                ('assign2',        models.DecimalField(blank=True, decimal_places=2, max_digits=5, null=True, validators=[django.core.validators.MinValueValidator(decimal.Decimal('0')), django.core.validators.MaxValueValidator(decimal.Decimal('100'))], verbose_name='Assignment 2 (raw /100)')),
                ('cat1_theory',    models.DecimalField(blank=True, decimal_places=2, max_digits=5, null=True, validators=[django.core.validators.MinValueValidator(decimal.Decimal('0')), django.core.validators.MaxValueValidator(decimal.Decimal('100'))], verbose_name='CAT 1 – Theory (raw /100)')),
                ('cat2_theory',    models.DecimalField(blank=True, decimal_places=2, max_digits=5, null=True, validators=[django.core.validators.MinValueValidator(decimal.Decimal('0')), django.core.validators.MaxValueValidator(decimal.Decimal('100'))], verbose_name='CAT 2 – Theory (raw /100)')),
                ('cat1_practical', models.DecimalField(blank=True, decimal_places=2, max_digits=5, null=True, validators=[django.core.validators.MinValueValidator(decimal.Decimal('0')), django.core.validators.MaxValueValidator(decimal.Decimal('100'))], verbose_name='Practical Test 1 (raw /100)')),
                ('cat2_practical', models.DecimalField(blank=True, decimal_places=2, max_digits=5, null=True, validators=[django.core.validators.MinValueValidator(decimal.Decimal('0')), django.core.validators.MaxValueValidator(decimal.Decimal('100'))], verbose_name='Practical Test 2 (raw /100)')),
                ('updated_at',     models.DateTimeField(auto_now=True)),
                ('student',        models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='result', to='attendance.student')),
            ],
            options={
                'ordering': ['student__name'],
            },
        ),
    ]
