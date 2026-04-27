from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('attendance', '0004_module_has_practical_studentresult'),
    ]

    operations = [
        migrations.AddField(
            model_name='semester',
            name='cat1_cutoff',
            field=models.DateField(blank=True, null=True, verbose_name='CAT 1 Attendance Cutoff'),
        ),
        migrations.AddField(
            model_name='semester',
            name='cat2_cutoff',
            field=models.DateField(blank=True, null=True, verbose_name='CAT 2 Attendance Cutoff'),
        ),
        migrations.AddField(
            model_name='semester',
            name='end_cutoff',
            field=models.DateField(blank=True, null=True, verbose_name='End-of-Semester Attendance Cutoff'),
        ),
    ]
