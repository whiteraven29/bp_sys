"""Load a semester's teaching calendar from the almanac's JSON: when teaching
starts and ends, the holidays and recess, CAT weeks, examinations, deadlines
and CQI supervisions.

    python manage.py load_teaching_calendar                 # 2026/2027
    python manage.py load_teaching_calendar path.json --dry-run

DPARC keeps it afterwards on the Teaching calendar screen. Loading again adds
what is missing and corrects dates of what is there (found by kind and title);
it removes nothing DPARC added. A semester not yet made in the system is
skipped and said so.
"""

import json
from datetime import date
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from attendance.models import AcademicYear, CalendarEvent, Semester, TeachingCalendar

DEFAULT = Path(__file__).resolve().parents[2] / 'curricula' / 'almanac_2026_2027.json'


class Command(BaseCommand):
    help = "Load the almanac's teaching weeks, holidays and assessment windows."

    def add_arguments(self, parser):
        parser.add_argument('path', nargs='?', default=str(DEFAULT))
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, path, dry_run, **options):
        try:
            data = json.loads(Path(path).read_text())
        except (OSError, ValueError) as exc:
            raise CommandError(f'Cannot read {path}: {exc}')
        year = AcademicYear.objects.filter(name=data['academic_year']).first()
        if year is None:
            raise CommandError(f'No academic year {data["academic_year"]} in the system.')
        with transaction.atomic():
            for number, spec in sorted(data['semesters'].items()):
                semester = Semester.objects.filter(academic_year=year, number=int(number)).first()
                if semester is None:
                    self.stdout.write(self.style.WARNING(f'Semester {number} of {year} is not in the system: skipped.'))
                    continue
                self.stdout.write(load_semester(semester, spec))
            if dry_run:
                transaction.set_rollback(True)
                self.stdout.write(self.style.WARNING('Dry run: nothing saved.'))
            else:
                self.stdout.write(self.style.SUCCESS('Loaded.'))


def load_semester(semester, spec):
    TeachingCalendar.objects.update_or_create(semester=semester, defaults={
        'teaching_starts': date.fromisoformat(spec['teaching_starts']),
        'teaching_ends': date.fromisoformat(spec['teaching_ends']), 'note': spec.get('note', '')[:300]})
    added = updated = 0
    for kind, title, starts, ends in spec.get('events', []):
        _, made = CalendarEvent.objects.update_or_create(
            semester=semester, kind=kind, title=title[:200],
            defaults={'starts_on': date.fromisoformat(starts), 'ends_on': date.fromisoformat(ends)})
        added, updated = added + made, updated + (not made)
    return f'{semester}: teaching {spec["teaching_starts"]} to {spec["teaching_ends"]}; {added} dates added, {updated} updated.'
