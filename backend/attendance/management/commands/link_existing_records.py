"""Link what existed before departments to its programme.

    python manage.py link_existing_records --programme PST --dry-run
    python manage.py link_existing_records --programme PST

Run once, after the department and programme have been registered on the
Departments screen. It:

  1. links every module whose code starts with the prefix (the programme code
     unless --module-prefix says otherwise) and that is not yet linked to any
     programme — nothing already linked is moved;
  2. records a semester registration for every student enrolled in those
     modules, marked "imported", at the highest level they are enrolled at in
     that semester.

It only adds. Module codes and names, enrollments, attendance and marks are
never written; the one link it may set on an enrollment is the pointer to the
person it belongs to, for enrollments that predate that pointer. Running it
twice changes nothing the second time.
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from attendance import finance
from attendance.models import Module, Programme, SemesterRegistration, Student


class Command(BaseCommand):
    help = 'Link existing modules and enrollments to a programme (adds only; run once).'

    def add_arguments(self, parser):
        parser.add_argument('--programme', required=True, help='Programme code, e.g. PST')
        parser.add_argument('--module-prefix', help='Module code prefix to link (default: the programme code)')
        parser.add_argument('--dry-run', action='store_true', help='Report what would change, change nothing')

    def handle(self, *args, **options):
        programme = Programme.objects.filter(code__iexact=options['programme']).first()
        if programme is None:
            raise CommandError(
                f'No programme with code {options["programme"]}. Register it on the Departments screen first.')
        prefix = (options['module_prefix'] or programme.code).strip()
        taught_at = set(programme.levels.values_list('id', flat=True))

        with transaction.atomic():
            # 1. Modules.
            candidates = Module.objects.filter(programme__isnull=True, code__istartswith=prefix)
            linked = skipped = 0
            for module in candidates.select_related('class_level'):
                if taught_at and module.class_level_id not in taught_at:
                    skipped += 1
                    self.stdout.write(f'  skipped {module.code}: {programme.code} is not taught at {module.class_level}')
                    continue
                Module.objects.filter(pk=module.pk).update(programme=programme)
                linked += 1

            # 2. Registrations, one per person per semester.
            enrollments = (Student.objects.filter(module__programme=programme)
                           .select_related('module__class_level', 'module__semester', 'profile'))
            best = {}          # (profile id, semester id) -> class level
            profiles_linked = enrollments.filter(profile__isnull=True).count()
            for enrollment in enrollments:
                profile = finance.profile_for_student(enrollment)
                key = (profile.id, enrollment.module.semester_id)
                level = enrollment.module.class_level
                if key not in best or level.order > best[key].order:
                    best[key] = level

            created = 0
            for (profile_id, semester_id), level in best.items():
                _, made = SemesterRegistration.objects.get_or_create(
                    profile_id=profile_id, semester_id=semester_id,
                    defaults={'programme': programme, 'class_level': level,
                              'kind': SemesterRegistration.IMPORTED},
                )
                created += made

            verb = 'Would link' if options['dry_run'] else 'Linked'
            self.stdout.write(self.style.SUCCESS(
                f'{verb} {linked} module(s) to {programme.code} ({skipped} skipped); '
                f'{"would record" if options["dry_run"] else "recorded"} {created} semester registration(s); '
                f'{profiles_linked} enrollment(s) pointed at their student record.'
            ))
            if options['dry_run']:
                transaction.set_rollback(True)
