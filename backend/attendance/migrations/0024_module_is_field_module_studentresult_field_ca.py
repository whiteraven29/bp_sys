from django.db import migrations, models
import django.core.validators


class Migration(migrations.Migration):
    dependencies = [('attendance', '0023_inventorylocation_location_type')]

    operations = [
        migrations.AddField(
            model_name='module',
            name='is_field_module',
            field=models.BooleanField(default=False, help_text='Use one CA mark weighted to 40% and one final mark weighted to 60%.', verbose_name='Field Results Module'),
        ),
        migrations.AddField(
            model_name='studentresult',
            name='field_ca',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=5, null=True, validators=[django.core.validators.MinValueValidator(0), django.core.validators.MaxValueValidator(100)], verbose_name='Field CA (raw /100)'),
        ),
    ]
