from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('attendance', '0002_remove_module_module_type_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='session',
            name='exam_period',
            field=models.CharField(
                choices=[('C1', 'CAT 1'), ('C2', 'CAT 2'), ('GN', 'General')],
                default='GN',
                help_text='Tag this session to a specific assessment period for eligibility tracking.',
                max_length=2,
                verbose_name='Exam Period',
            ),
        ),
        migrations.AddField(
            model_name='attendancerecord',
            name='sick_note',
            field=models.CharField(blank=True, max_length=300, verbose_name='Sick Note / Reason'),
        ),
        migrations.AddField(
            model_name='attendancerecord',
            name='certificate_submitted',
            field=models.BooleanField(default=False, verbose_name='Certificate Submitted'),
        ),
    ]
