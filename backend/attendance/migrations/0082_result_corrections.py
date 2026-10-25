# One grade corrected — NACTVET's re-issued grade, or NS for a repeat not sat —
# proposed by the examination officer, approved by DPARC or the Principal
# (said 24–25 October 2026). The grade it replaces is kept on the correction.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('attendance', '0081_authority_grade_clearing'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name='notification',
            name='kind',
            field=models.CharField(choices=[('request.submitted', 'A student asked for something'), ('request.forwarded', 'A request needs a decision'), ('request.decided', 'A request was answered'), ('request.document', 'A document was sent'), ('form.published', 'A form was opened'), ('postponement.requested', 'A student asked to postpone'), ('postponement.decided', 'A postponement was decided'), ('expense.submitted', 'An expense needs approval'), ('expense.decided', 'An expense was decided'), ('expense.queried', 'An expense was queried or answered'), ('health.recorded', 'A student health record was added'), ('health.reported', 'A student reported a health problem'), ('health.answered', 'A health report was answered'), ('hostel.applied', 'A student applied for the hostel'), ('hostel.decided', 'A hostel application was decided'), ('provisional.results', 'Results are in for students placed provisionally'), ('provisional.settled', 'A provisional place was confirmed or changed'), ('exit.requested', 'A student asked to finish after a level'), ('exit.decided', 'A request to finish after a level was decided'), ('status.recorded', "A student's status was recorded (deceased, absconded, suspended, terminated, discontinued)"), ('disposal.proposed', 'An asset disposal needs approval'), ('disposal.decided', 'An asset disposal was decided'), ('password.reset', 'Your password was reset by the office'), ('waiver.requested', 'A fee waiver needs DPFPA approval'), ('waiver.decided', 'A fee waiver was decided'), ('correction.proposed', 'A result correction needs approval'), ('correction.decided', 'A result correction was decided')], max_length=32),
        ),
        migrations.CreateModel(
            name='ResultCorrection',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('field', models.CharField(choices=[('grade', 'First sitting'), ('supp_grade', 'Supplementary')], default='grade', max_length=12)),
                ('old_value', models.CharField(blank=True, max_length=20)),
                ('new_value', models.CharField(max_length=20)),
                ('reason', models.CharField(max_length=300)),
                ('reference', models.CharField(blank=True, help_text="NACTVET's letter or ticket number.", max_length=120)),
                ('status', models.CharField(choices=[('pending', 'Waiting for approval'), ('approved', 'Approved'), ('refused', 'Refused')], default='pending', max_length=10)),
                ('proposed_at', models.DateTimeField(auto_now_add=True)),
                ('decided_at', models.DateTimeField(blank=True, null=True)),
                ('decision_note', models.CharField(blank=True, max_length=300)),
                ('decided_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='corrections_decided', to=settings.AUTH_USER_MODEL)),
                ('proposed_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='corrections_proposed', to=settings.AUTH_USER_MODEL)),
                ('result', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='corrections', to='attendance.studentresult')),
            ],
            options={
                'ordering': ['-proposed_at'],
            },
        ),
    ]
