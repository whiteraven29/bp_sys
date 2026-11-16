"""A class added after its plan was sent is an extra class, and a class
missed or partly taught can be finished in a later one (16 November 2026).

Classes already added after their plan was sent are found from the plan's
own history ("added a class: Lecture on 05 Dec", logged after submitted_at)
and marked extra, so they stop counting as classes the plan is measured
against. Nothing else changes: no mark, date or register is touched."""

import re

from django.db import migrations, models
import django.db.models.deletion


def mark_extras(apps, schema_editor):
    PlanEvent = apps.get_model('attendance', 'PlanEvent')
    PlanSession = apps.get_model('attendance', 'PlanSession')
    kinds = {label: value for value, label in [
        ('lecture', 'Lecture'), ('tutorial', 'Tutorial'), ('practical', 'Practical'),
        ('assignment', 'Assignment'), ('revision', 'Revision'), ('field', 'Field practice')]}
    events = PlanEvent.objects.filter(action='added a class', plan__submitted_at__isnull=False).select_related('plan')
    for event in events:
        plan = event.plan
        if event.at < plan.submitted_at:
            continue
        found = re.match(r'^(.+) on (\d{2}) (\w{3})$', event.detail or '')
        if not found or found.group(1) not in kinds:
            continue
        kind, day, month = kinds[found.group(1)], int(found.group(2)), found.group(3)
        # The day it was added for: its first date if it has been moved since.
        for session in PlanSession.objects.filter(plan=plan, kind=kind, extra=False).order_by('-id'):
            first = session.moved_from or session.planned_on
            if first.day == day and first.strftime('%b') == month:
                session.extra = True
                session.save(update_fields=['extra'])
                break


class Migration(migrations.Migration):

    dependencies = [
        ('attendance', '0086_teaching_plans'),
    ]

    operations = [
        migrations.AddField(
            model_name='plansession',
            name='extra',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='plansession',
            name='finished_in',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                    related_name='finishes', to='attendance.plansession'),
        ),
        migrations.RunPython(mark_extras, migrations.RunPython.noop),
    ]
