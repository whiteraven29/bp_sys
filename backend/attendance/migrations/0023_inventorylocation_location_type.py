from django.db import migrations, models


def classify_existing_locations(apps, schema_editor):
    Location = apps.get_model('attendance', 'InventoryLocation')
    for location in Location.objects.all():
        name = location.name.casefold()
        if 'class' in name:
            location.location_type = 'classroom'
        elif 'lab' in name or 'laboratory' in name:
            location.location_type = 'lab'
        elif 'office' in name:
            location.location_type = 'office'
        else:
            location.location_type = 'other'
        location.save(update_fields=['location_type'])


class Migration(migrations.Migration):
    dependencies = [('attendance', '0022_seed_inventory_item_catalog')]
    operations = [
        migrations.AddField(
            model_name='inventorylocation', name='location_type',
            field=models.CharField(choices=[('office', 'College Offices'), ('classroom', 'Classrooms'), ('lab', 'Laboratories'), ('other', 'Other Areas')], default='other', max_length=20),
        ),
        migrations.RunPython(classify_existing_locations, migrations.RunPython.noop),
    ]
