# The college chooses when a semester's results reach the portal, level by
# level (said 24 October 2026); a provisional student settled while they are
# hidden is told once they are shown. What students can already see stays shown: the
# active year's semesters with results published, and the earlier semesters
# their repeat sittings complete.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.db.models import Q


def keep_what_is_shown(apps, schema_editor):
    AcademicYear = apps.get_model('attendance', 'AcademicYear')
    Student = apps.get_model('attendance', 'Student')
    ResultRelease = apps.get_model('attendance', 'ResultRelease')
    year = AcademicYear.objects.filter(is_active=True).first()
    if year is None:
        return
    published = Q(result__final_approved=True) | ~Q(result__authority_grade='')
    this_year = Student.objects.filter(module__semester__academic_year=year)
    shown = set(this_year.filter(published, result__isnull=False)
                .values_list('module__semester_id', 'module__class_level_id'))
    # The earlier semesters this year's repeats complete, where their results
    # were already published (a semester whose results are only now coming in
    # waits to be shown like any other).
    shown |= set(this_year.filter(repeat_of__isnull=False).filter(
        Q(repeat_of__result__final_approved=True) | ~Q(repeat_of__result__authority_grade=''),
        repeat_of__result__isnull=False)
        .values_list('repeat_of__module__semester_id', 'repeat_of__module__class_level_id'))
    for semester_id, level_id in shown:
        ResultRelease.objects.get_or_create(semester_id=semester_id, class_level_id=level_id,
                                            defaults={'visible': True})


class Migration(migrations.Migration):

    dependencies = [
        ('attendance', '0079_waiver_by_instalment'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ResultRelease',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('visible', models.BooleanField(default=False)),
                ('changed_at', models.DateTimeField(auto_now=True)),
                ('changed_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='result_releases', to=settings.AUTH_USER_MODEL)),
                ('class_level', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='result_releases', to='attendance.classlevel')),
                ('semester', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='result_releases', to='attendance.semester')),
            ],
            options={
                'constraints': [models.UniqueConstraint(fields=('semester', 'class_level'), name='one_release_per_semester_level')],
            },
        ),
        migrations.AddField(
            model_name='semesterreview',
            name='notify_when_shown',
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(keep_what_is_shown, migrations.RunPython.noop),
    ]
