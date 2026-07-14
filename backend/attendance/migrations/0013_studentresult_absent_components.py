from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('attendance', '0012_exam_declaration_handoff')]

    operations = [
        migrations.AddField(model_name='studentresult', name=name, field=models.BooleanField(default=False))
        for name in (
            'assign1_absent', 'assign2_absent', 'cat1_theory_absent', 'cat2_theory_absent',
            'cat1_practical_absent', 'cat2_practical_absent',
            'end_theory_absent', 'end_practical_absent',
        )
    ]
