from django.db import migrations

NOTE_OLD = 'Each semester: 15 weeks of study and 2 weeks of assessment.'
NOTE_NEW = 'Each semester: 20 weeks of study and 2 weeks of assessment (said 23 November 2026).'


def twenty_weeks(apps, schema_editor):
    """The 2015 curriculum (NTA Levels 5 and 6) has 20 weeks of study a
    semester, not 15 (said 23 November 2026). Plans not yet laid out follow
    it; a plan already laid out keeps its classes."""
    Curriculum = apps.get_model('attendance', 'Curriculum')
    for curriculum in Curriculum.objects.filter(code='PST-2015'):
        curriculum.weeks_of_study = 20
        curriculum.note = curriculum.note.replace(NOTE_OLD, NOTE_NEW)
        curriculum.save(update_fields=['weeks_of_study', 'note'])


def fifteen_weeks(apps, schema_editor):
    Curriculum = apps.get_model('attendance', 'Curriculum')
    for curriculum in Curriculum.objects.filter(code='PST-2015'):
        curriculum.weeks_of_study = 15
        curriculum.note = curriculum.note.replace(NOTE_NEW, NOTE_OLD)
        curriculum.save(update_fields=['weeks_of_study', 'note'])


class Migration(migrations.Migration):

    dependencies = [('attendance', '0088_teaching_plan_pdf_for_qa')]

    operations = [migrations.RunPython(twenty_weeks, fifteen_weeks)]
