from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from attendance.models import AttendanceRecord, Module, Session


# Transcribed from the signed PST05209 attendance sheet supplied on 13 August
# 2026. 20 May 2026 (sessions 07 and 08) is intentionally excluded because it
# has already been entered in HIMS.
HIMS_SESSIONS = (
    (date(2026, 4, 7), '01'),
    (date(2026, 4, 8), '01'),
    (date(2026, 4, 9), '01'),
    (date(2026, 4, 10), '02'),
    (date(2026, 4, 13), '09'),
    (date(2026, 4, 14), '10'),
    (date(2026, 4, 16), '03'),
    (date(2026, 4, 16), '04'),
    (date(2026, 4, 20), '05'),
    (date(2026, 4, 22), '04'),
    (date(2026, 4, 23), '04'),
    (date(2026, 4, 23), '05'),
    (date(2026, 4, 23), '06'),
    (date(2026, 5, 21), '11'),
    (date(2026, 5, 21), '12'),
    (date(2026, 5, 21), '13'),
    (date(2026, 5, 25), '14'),
)


class Command(BaseCommand):
    help = (
        'Create the PST05209 sessions transcribed from the signed HIMS sheet '
        'and mark enrolled students present in newly created sessions.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--module-code', default='PST05209')
        parser.add_argument('--academic-year', default='2025/2026')
        parser.add_argument(
            '--confirm', action='store_true',
            help='Apply changes. Without this option the command is a dry run.',
        )

    def handle(self, *args, **options):
        code = options['module_code'].strip()
        academic_year = options['academic_year'].strip()
        modules = Module.objects.filter(
            code__iexact=code,
            semester__academic_year__name=academic_year,
        ).select_related('semester__academic_year')

        if not modules.exists():
            raise CommandError(
                f'Module {code} was not found in academic year {academic_year}.'
            )
        if modules.count() != 1:
            semesters = ', '.join(str(m.semester.number) for m in modules)
            raise CommandError(
                f'Module {code} is ambiguous in {academic_year}; found it in '
                f'semesters {semesters}.'
            )

        module = modules.get()
        student_count = module.students.count()
        existing = {
            (session.date, session.label.strip())
            for session in module.sessions.filter(
                session_type=Session.THEORY,
                exam_period=Session.GENERAL,
            )
        }
        pending = [(day, number) for day, number in HIMS_SESSIONS if (day, number) not in existing]

        self.stdout.write(
            f'{module.code} - {module.name}: {len(HIMS_SESSIONS)} sheet sessions, '
            f'{len(pending)} new, {len(HIMS_SESSIONS) - len(pending)} already present; '
            f'{student_count} enrolled students.'
        )
        for day, number in pending:
            self.stdout.write(f'  {day:%d/%m/%Y} session {number}: {student_count} present records')

        if not options['confirm']:
            self.stdout.write(self.style.WARNING('Dry run only. Re-run with --confirm to apply.'))
            return

        sessions_created = 0
        records_created = 0
        students = list(module.students.all())
        with transaction.atomic():
            for day, number in pending:
                session = Session.objects.create(
                    module=module,
                    session_type=Session.THEORY,
                    exam_period=Session.GENERAL,
                    date=day,
                    label=number,
                )
                AttendanceRecord.objects.bulk_create([
                    AttendanceRecord(
                        session=session,
                        student=student,
                        status=AttendanceRecord.PRESENT,
                    )
                    for student in students
                ])
                sessions_created += 1
                records_created += len(students)

        self.stdout.write(self.style.SUCCESS(
            f'Created {sessions_created} sessions and {records_created} present records. '
            'Existing sessions and records were not changed.'
        ))
