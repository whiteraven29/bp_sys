from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from attendance.models import AttendanceRecord, Module, Session


# Transcribed from the signed NTA Level 5 PST05208 attendance sheet supplied
# on 14 August 2026. The sheet contains two session 08 rows on 25 April; the
# second is labelled "08 (2)" so both can coexist under the database constraint.
PST05208_SESSIONS = (
    (date(2026, 4, 10), '01'),
    (date(2026, 4, 10), '02'),
    (date(2026, 4, 14), '03'),
    (date(2026, 4, 14), '04'),
    (date(2026, 4, 20), '05'),
    (date(2026, 4, 21), '06'),
    (date(2026, 4, 22), '07'),
    (date(2026, 4, 25), '08'),
    (date(2026, 4, 25), '08 (2)'),
    (date(2026, 4, 27), '09'),
    (date(2026, 4, 28), '09'),
    (date(2026, 5, 2), '10'),
    (date(2026, 5, 2), '11'),
    (date(2026, 5, 3), '12'),
    (date(2026, 5, 5), '13'),
    (date(2026, 5, 23), '14'),
    (date(2026, 5, 18), '15'),
)


class Command(BaseCommand):
    help = (
        'Load the signed NTA Level 5 PST05208 attendance sessions and mark '
        'enrolled students present where an attendance record is missing.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--module-code', default='PST05208')
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
        ).select_related('class_level', 'semester__academic_year')

        if not modules.exists():
            raise CommandError(
                f'Module {code} was not found in academic year {academic_year}.'
            )
        if modules.count() != 1:
            semesters = ', '.join(str(module.semester.number) for module in modules)
            raise CommandError(
                f'Module {code} is ambiguous in {academic_year}; found it in '
                f'semesters {semesters}.'
            )

        module = modules.get()
        if module.class_level.order != 5:
            raise CommandError(
                f'Module {module.code} belongs to {module.class_level}, not NTA Level 5.'
            )

        students = list(module.students.all())
        existing_sessions = {
            (session.date, session.label): session
            for session in module.sessions.filter(
                session_type=Session.THEORY,
                exam_period=Session.GENERAL,
            )
        }
        sessions_to_create = 0
        records_to_create = 0
        for day, number in PST05208_SESSIONS:
            session = existing_sessions.get((day, number))
            if session is None:
                sessions_to_create += 1
                records_to_create += len(students)
            else:
                records_to_create += len(students) - session.records.filter(
                    student__in=students
                ).count()

        self.stdout.write(
            f'{module.code} - {module.name} ({module.class_level}): '
            f'{len(PST05208_SESSIONS)} sheet sessions, {sessions_to_create} new; '
            f'{len(students)} enrolled students, {records_to_create} missing present records.'
        )

        if not options['confirm']:
            self.stdout.write(self.style.WARNING(
                'Dry run only. Re-run with --confirm to apply.'
            ))
            return

        sessions_created = 0
        records_created = 0
        with transaction.atomic():
            for day, number in PST05208_SESSIONS:
                session, created = Session.objects.get_or_create(
                    module=module,
                    session_type=Session.THEORY,
                    exam_period=Session.GENERAL,
                    date=day,
                    label=number,
                )
                sessions_created += int(created)
                recorded_student_ids = set(
                    session.records.values_list('student_id', flat=True)
                )
                missing_records = [
                    AttendanceRecord(
                        session=session,
                        student=student,
                        status=AttendanceRecord.PRESENT,
                    )
                    for student in students
                    if student.id not in recorded_student_ids
                ]
                AttendanceRecord.objects.bulk_create(missing_records)
                records_created += len(missing_records)

        self.stdout.write(self.style.SUCCESS(
            f'Created {sessions_created} sessions and {records_created} present records. '
            'Existing attendance records were not changed.'
        ))
