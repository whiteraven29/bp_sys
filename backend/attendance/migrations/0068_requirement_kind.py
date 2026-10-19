from django.db import migrations, models


def check_only_where_nothing_is_billed(apps, schema_editor):
    """An item saved with nothing to charge could only ever be checked: it
    becomes a check-only item, which now holds a student who lacks it."""
    AdmissionRequirement = apps.get_model('attendance', 'AdmissionRequirement')
    AdmissionRequirement.objects.filter(charge_type__isnull=True).update(kind='check')


class Migration(migrations.Migration):

    dependencies = [
        ('attendance', '0067_enrolment_form_details_and_passport'),
    ]

    operations = [
        migrations.AddField(
            model_name='admissionrequirement',
            name='kind',
            field=models.CharField(choices=[('billed', 'Billed when missing — never a hold'), ('check', 'Checked only — missing holds the student')], default='billed', max_length=10),
        ),
        migrations.AlterField(
            model_name='admissionrequirement',
            name='charge_type',
            field=models.ForeignKey(blank=True, help_text='What the college charges when the student does not have it (billed items only).', null=True, on_delete=models.deletion.PROTECT, related_name='requirements', to='attendance.chargetype'),
        ),
        migrations.RunPython(check_only_where_nothing_is_billed, migrations.RunPython.noop),
    ]
