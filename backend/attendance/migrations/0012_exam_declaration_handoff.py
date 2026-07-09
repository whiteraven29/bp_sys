from decimal import Decimal

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('attendance', '0011_paymentcategory_class_level_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='paymentcategory',
            name='category_type',
            field=models.CharField(
                choices=[
                    ('school_fees', 'School Fees'),
                    ('special_exam', 'Special Exam'),
                    ('supp_exam', 'Supplementary Exam'),
                    ('repeat_module', 'Repeat Module'),
                    ('discontinuation', 'Discontinuation'),
                    ('other', 'Other Payment'),
                ],
                default='other',
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name='studentfinanceobligation',
            name='obligation_type',
            field=models.CharField(
                choices=[
                    ('special_exam', 'Special Exam'),
                    ('supp_exam', 'Supplementary Exam'),
                    ('repeat_module', 'Repeat Module'),
                    ('discontinuation', 'Discontinuation'),
                ],
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name='studentfinanceobligation',
            name='amount_required',
            field=models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=12),
        ),
        migrations.AlterField(
            model_name='studentfinanceobligation',
            name='category',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='obligations',
                to='attendance.paymentcategory',
            ),
        ),
    ]
