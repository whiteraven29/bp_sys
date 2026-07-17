from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('attendance', '0015_grading_and_supplementary')]

    operations = [
        migrations.AddField(
            model_name='studentresult', name='authority_grade',
            field=models.CharField(blank=True, default='', max_length=20),
        ),
        migrations.AddField(
            model_name='studentresult', name='authority_status',
            field=models.CharField(blank=True, default='', max_length=20),
        ),
    ]
