from django.db import migrations


CATALOG = [
    ('Office Chair', 'Furniture', 'BPCH/OCH', 'Chair used by staff in an office.'),
    ('Student Chair', 'Furniture', 'BPCH/SCH', 'Chair used by students in a classroom or common study area.'),
    ('Office Table', 'Furniture', 'BPCH/OTB', 'Table or desk used by office staff.'),
    ('Student Table', 'Furniture', 'BPCH/STB', 'Table used by students.'),
    ('Fixed Desk', 'Furniture', 'BPCH/FD', 'Desk fixed to the floor or building structure.'),
    ('Shelf', 'Furniture', 'BPCH/SH', 'Open shelving unit.'),
    ('Cabinet', 'Furniture', 'BPCH/CAB', 'Storage cabinet.'),
    ('Mouse', 'ICT Equipment', 'BPCH/MSE', 'Computer pointing device.'),
    ('Keyboard', 'ICT Equipment', 'BPCH/KBD', 'Computer keyboard.'),
    ('CPU', 'ICT Equipment', 'BPCH/CPU', 'Desktop computer system unit.'),
    ('Monitor', 'ICT Equipment', 'BPCH/MON', 'Computer display monitor.'),
    ('Printer', 'ICT Equipment', 'BPCH/PRN', 'Office or shared printer.'),
    ('Projector', 'ICT Equipment', 'BPCH/PJR', 'Multimedia projector.'),
    ('Projecting Board', 'Fixtures and Displays', 'BPCH/PB', 'Projection screen or projecting board.'),
    ('Notice Board', 'Fixtures and Displays', 'BPCH/NB', 'Wall or freestanding notice board.'),
    ('Whiteboard', 'Fixtures and Displays', 'BPCH/WB', 'Writing whiteboard.'),
    ('Dustbin', 'Cleaning and General', 'BPCH/DB', 'Waste bin in an office or shared area.'),
    ('Mop', 'Cleaning and General', 'BPCH/MOP', 'Floor-cleaning mop.'),
    ('Fire Extinguisher', 'Safety Equipment', 'BPCH/FE', 'Portable fire extinguisher.'),
    ('Safe Custody Box', 'Security Equipment', 'BPCH/SCB', 'Secure safe or custody box.'),
    ('Extension Cable', 'Electrical Accessories', 'BPCH/EXT', 'Electrical extension lead or power strip.'),
]


def seed_catalog(apps, schema_editor):
    AssetCategory = apps.get_model('attendance', 'AssetCategory')
    InventoryItemType = apps.get_model('attendance', 'InventoryItemType')
    categories = {}
    for _, category_name, _, _ in CATALOG:
        categories[category_name], _ = AssetCategory.objects.get_or_create(name=category_name, defaults={'is_active': True})
    for name, category_name, prefix, description in CATALOG:
        if InventoryItemType.objects.filter(name=name, category=categories[category_name]).exists():
            continue
        if InventoryItemType.objects.filter(default_tag_prefix__iexact=prefix).exists():
            continue
        InventoryItemType.objects.create(
            name=name, category=categories[category_name], default_tag_prefix=prefix,
            description=description, is_active=True,
        )


class Migration(migrations.Migration):
    dependencies = [('attendance', '0021_inventoryitemtype_asset_item_type')]
    operations = [migrations.RunPython(seed_catalog, migrations.RunPython.noop)]
