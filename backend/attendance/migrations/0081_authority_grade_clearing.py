# Uploaded NACTVET grades can be cleared to upload a corrected sheet (said 24
# October 2026); what was cleared is kept, with who and why.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('attendance', '0080_result_release'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='AuthorityGradeClearing',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('sheet', models.CharField(max_length=12)),
                ('sitting', models.CharField(max_length=6)),
                ('reason', models.CharField(max_length=300)),
                ('grades', models.JSONField(default=list)),
                ('cleared_at', models.DateTimeField(auto_now_add=True)),
                ('class_level', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='grade_clearings', to='attendance.classlevel')),
                ('cleared_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='grade_clearings', to=settings.AUTH_USER_MODEL)),
                ('semester', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='grade_clearings', to='attendance.semester')),
            ],
            options={
                'ordering': ['-cleared_at'],
            },
        ),
    ]
