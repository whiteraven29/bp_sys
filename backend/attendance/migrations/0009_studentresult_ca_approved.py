from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('attendance', '0008_studentresult_final_approved'),
    ]

    operations = [
        migrations.AddField(
            model_name='studentresult',
            name='ca_approved',
            field=models.BooleanField(default=False),
        ),
    ]
