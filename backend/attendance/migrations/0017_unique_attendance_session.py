from django.db import migrations, models


def preserve_existing_duplicates(apps, schema_editor):
    """Give pre-existing exact duplicates distinct, visible labels before constraining."""
    Session = apps.get_model('attendance', 'Session')
    seen = set()
    for session in Session.objects.order_by('id').iterator():
        key = (
            session.module_id, session.session_type, session.exam_period,
            session.date, session.label,
        )
        if key not in seen:
            seen.add(key)
            continue

        base_label = session.label
        copy_number = 2
        while True:
            candidate = f'{base_label} (Duplicate {copy_number})'
            candidate_key = (
                session.module_id, session.session_type, session.exam_period,
                session.date, candidate,
            )
            if candidate_key not in seen and not Session.objects.filter(
                module_id=session.module_id,
                session_type=session.session_type,
                exam_period=session.exam_period,
                date=session.date,
                label=candidate,
            ).exists():
                session.label = candidate
                session.save(update_fields=['label'])
                seen.add(candidate_key)
                break
            copy_number += 1


class Migration(migrations.Migration):
    dependencies = [
        ('attendance', '0016_studentresult_authority_grade'),
    ]

    operations = [
        migrations.RunPython(preserve_existing_duplicates, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name='session',
            constraint=models.UniqueConstraint(
                fields=('module', 'session_type', 'exam_period', 'date', 'label'),
                name='unique_attendance_session',
            ),
        ),
    ]
