"""Item checks belong to a student and a semester, so semester 2 can be
rechecked without an application; the admission desk can hold a student.

Additive: every existing check is given the student and semester of the
application it was made on, and keeps that application.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def fill_from_application(apps, schema_editor):
    RequirementCheck = apps.get_model('attendance', 'RequirementCheck')
    for check in RequirementCheck.objects.select_related('application'):
        check.profile_id = check.application.profile_id
        check.semester_id = check.application.semester_id
        check.save(update_fields=['profile', 'semester'])


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('attendance', '0050_document_certificate_levels'),
    ]

    operations = [
        migrations.AddField(
            model_name='application', name='on_hold',
            field=models.BooleanField(default=False)),
        migrations.AddField(
            model_name='application', name='hold_reason',
            field=models.CharField(blank=True, max_length=300)),
        migrations.AddField(
            model_name='application', name='held_at',
            field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(
            model_name='application', name='held_by',
            field=models.ForeignKey(blank=True, null=True,
                                    on_delete=django.db.models.deletion.SET_NULL,
                                    related_name='applications_held', to=settings.AUTH_USER_MODEL)),

        migrations.RemoveConstraint(
            model_name='requirementcheck', name='one_check_per_requirement_per_application'),
        migrations.AddField(
            model_name='requirementcheck', name='profile',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE,
                                    related_name='requirement_checks', to='attendance.studentprofile')),
        migrations.AddField(
            model_name='requirementcheck', name='semester',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT,
                                    related_name='requirement_checks', to='attendance.semester')),
        migrations.AlterField(
            model_name='requirementcheck', name='application',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE,
                                    related_name='requirement_checks', to='attendance.application')),
        migrations.RunPython(fill_from_application, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='requirementcheck', name='profile',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                    related_name='requirement_checks', to='attendance.studentprofile')),
        migrations.AlterField(
            model_name='requirementcheck', name='semester',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                                    related_name='requirement_checks', to='attendance.semester')),
        migrations.AddConstraint(
            model_name='requirementcheck',
            constraint=models.UniqueConstraint(fields=('profile', 'semester', 'requirement'),
                                               name='one_check_per_requirement_per_semester')),
    ]
