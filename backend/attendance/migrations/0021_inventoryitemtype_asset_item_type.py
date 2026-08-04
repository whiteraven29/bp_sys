from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('attendance', '0020_assetmaintenance_quantity_assettransfer_quantity_and_more')]

    operations = [
        migrations.CreateModel(
            name='InventoryItemType',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=200)),
                ('description', models.TextField(blank=True)),
                ('default_tag_prefix', models.CharField(max_length=100, unique=True)),
                ('is_active', models.BooleanField(default=True)),
                ('category', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='item_types', to='attendance.assetcategory')),
            ],
            options={'ordering': ['name']},
        ),
        migrations.AddConstraint(
            model_name='inventoryitemtype',
            constraint=models.UniqueConstraint(fields=('name', 'category'), name='unique_inventory_item_type_category'),
        ),
        migrations.AddField(
            model_name='asset',
            name='item_type',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='assets', to='attendance.inventoryitemtype'),
        ),
    ]
