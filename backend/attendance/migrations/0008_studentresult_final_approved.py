from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('attendance', '0007_student_portal_pin_hash'),
    ]

    operations = [
        migrations.AddField(
            model_name='studentresult',
            name='final_approved',
            field=models.BooleanField(default=False),
        ),
    ]
