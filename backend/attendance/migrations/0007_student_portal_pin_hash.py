from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('attendance', '0006_studentresult_end_exam'),
    ]

    operations = [
        migrations.AddField(
            model_name='student',
            name='portal_pin_hash',
            field=models.CharField(blank=True, editable=False, max_length=128),
        ),
    ]
