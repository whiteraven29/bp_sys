from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('attendance', '0013_studentresult_absent_components')]

    operations = [
        migrations.AddField(
            model_name='student',
            name='must_change_portal_password',
            field=models.BooleanField(default=True, editable=False),
        ),
    ]
