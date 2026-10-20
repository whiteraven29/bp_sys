"""Enroll this year's registered students in the modules they are missing
(said 20 October 2026).

Admission enrolls a student in the modules of their programme and level for
the semester. A student admitted before those modules were there — or while
they had no programme, before admission read such a module as the level's —
was enrolled in nothing, and a student in no module cannot be given a portal
password. Modules added later enroll the class by themselves; this catches up
those admitted before.

    python manage.py enroll_registered            # dry run: who would be enrolled
    python manage.py enroll_registered --apply

Safe to repeat: a module a student is already in is skipped. Charges the
enrolment raises are counted; the year's own are raised once, never twice.
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from attendance import progression
from attendance.models import AcademicYear, SemesterRegistration, StudentCharge


class Command(BaseCommand):
    help = "Enroll this year's registered students in the modules they are missing."

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='Write it. Without it: a dry run.')

    def handle(self, *args, **options):
        year = AcademicYear.objects.filter(is_active=True).first()
        if year is None:
            raise CommandError('No active academic year.')
        apply = options['apply']
        self.stdout.write(('APPLYING' if apply else 'DRY RUN — nothing is written') + f' — {year}')
        with transaction.atomic():
            registrations = (SemesterRegistration.objects.filter(semester__academic_year=year)
                             .exclude(status=SemesterRegistration.CANCELLED)
                             .select_related('profile', 'programme', 'class_level', 'semester')
                             .order_by('profile__name'))
            profiles = [r.profile_id for r in registrations]
            charges_before = StudentCharge.objects.filter(profile_id__in=profiles).count()
            enrolled = 0
            for registration in registrations:
                made = progression.enroll_registration(registration)
                if made:
                    enrolled += 1
                    self.stdout.write(f'  {registration.profile.name} ({registration.profile.college_id or registration.profile.nactvet_reg_no}, '
                                      f'{registration.programme.code} {registration.class_level.name}): '
                                      + ', '.join(e.module.code for e in made))
            if not enrolled:
                self.stdout.write('  Nobody was missing a module that exists.')
            added = StudentCharge.objects.filter(profile_id__in=profiles).count() - charges_before
            if added:
                self.stdout.write(self.style.WARNING(f'  ! {added} new charge(s) raised — check them in Finance'))
            if not apply:
                transaction.set_rollback(True)
        self.stdout.write('Done. Now the password desk lists them.' if apply
                          else 'Dry run only. Add --apply to write it.')
