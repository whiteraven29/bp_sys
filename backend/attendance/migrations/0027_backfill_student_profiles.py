from django.db import migrations


def create_profiles(apps, schema_editor):
    """Give every existing enrollment a StudentProfile, grouped on the
    registration number.

    A student taking eight modules has eight Student rows; they are one person
    and share one profile. The name is taken from the most recently created
    enrollment, which is the one most likely to carry a corrected spelling.
    """
    Student = apps.get_model('attendance', 'Student')
    StudentProfile = apps.get_model('attendance', 'StudentProfile')

    by_reg_no = {}
    for enrollment in Student.objects.order_by('created_at', 'id'):
        by_reg_no.setdefault(enrollment.nactvet_reg_no.strip().upper(), []).append(enrollment)

    for reg_no, enrollments in by_reg_no.items():
        profile, _ = StudentProfile.objects.get_or_create(
            nactvet_reg_no=reg_no,
            defaults={'name': enrollments[-1].name},
        )
        Student.objects.filter(id__in=[e.id for e in enrollments]).update(profile=profile)


def drop_profiles(apps, schema_editor):
    Student = apps.get_model('attendance', 'Student')
    StudentProfile = apps.get_model('attendance', 'StudentProfile')
    Student.objects.update(profile=None)
    StudentProfile.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('attendance', '0026_fees_ledger'),
    ]

    operations = [
        migrations.RunPython(create_profiles, drop_profiles),
    ]
