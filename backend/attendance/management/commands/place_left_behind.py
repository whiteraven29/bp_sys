"""Place the students the year end left behind (said 25 October 2026).

When 2026/27 opened, PST was still three programmes of one level each (PST04,
PST05, PST06). The year end moves a student to the next level *of their
programme*: a Level 4 or Level 5 student had none, so they were left waiting
("finishing the programme waits for the results") or marked finished, with no
place in the new year. After `merge_programmes`, PST runs 4 to 6.

This runs the year end again for those students alone, exactly as it ran for
everyone else: not yet settled → placed provisionally at the next level, an
application waiting at admissions, settled from Provisional places when their
2025/26 semester 2 results are in; confirmed passed → promoted, to admissions;
confirmed repeat or FAIL → as the review says. Level 6, a student who chose to
finish after Level 5, one finished any other way and one away are left as
they are.

    python manage.py place_left_behind            # what it would do
    python manage.py place_left_behind --apply    # do it

Results, marks and payments are not touched. Safe to run twice: a student
placed is not placed again.
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from attendance import admissions, progression
from attendance.models import Semester, StudentProfile


class Command(BaseCommand):
    help = 'Place the students the year end left waiting or marked finished (single-level programmes).'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='Write the changes. Without it: a dry run.')

    def handle(self, *args, **options):
        target = Semester.objects.filter(is_active=True).select_related('academic_year').first()
        if target is None or target.number != Semester.SEM1:
            raise CommandError('Run it while semester 1 of the new year is the active semester.')
        semester = admissions.closing_semester(target)
        if semester is None:
            raise CommandError(f'No semester 2 of the year before {target.academic_year} in the system.')

        chosen, skipped = progression.left_behind(semester, target)
        plan = progression.plan_advance(semester)
        moves = {move['profile_id']: move for move in plan['moves']}
        held = {row['profile_id']: row['reason'] for row in plan['blocked'] + plan['waiting']}
        names = {p.id: p for p in StudentProfile.objects.filter(id__in=chosen)}

        self.stdout.write(f'{semester.label} → {target.label}: {len(chosen)} student(s) left behind, to place.')
        for profile_id in sorted(chosen, key=lambda pid: names[pid].name):
            profile, move = names[profile_id], moves.get(profile_id)
            what = (f"{move.get('from_level') or '?'} · {move.get('detail', '')}" if move
                    else f'held: {held.get(profile_id, "see Semester Review")}')
            self.stdout.write(f'  {profile.nactvet_reg_no:<20} {profile.name:<32} {what}')
        if skipped:
            self.stdout.write(f'Left as they are ({len(skipped)}):')
            for reg_no, name, why in sorted(skipped, key=lambda row: row[1]):
                self.stdout.write(f'  {reg_no:<20} {name:<32} {why}')

        if not options['apply']:
            self.stdout.write('Dry run: nothing written. Read it, then run again with --apply.')
            return
        if not chosen:
            self.stdout.write('Nobody to place.')
            return
        with transaction.atomic():
            summary = progression.apply_advance(semester, target, only=chosen)
        self.stdout.write(self.style.SUCCESS(
            f"Placed: {summary.get('provisional', 0)} provisionally, {summary['promoted']} promoted, "
            f"{summary['repeating']} repeating, {summary['applications']} application(s) opened at admissions; "
            f"{summary.get('waiting', 0)} still waiting, {summary['discontinued']} FAIL, {summary['completed']} finished."))
        self.stdout.write('Next: they come through the admission desks like everyone else; the examination office '
                          'settles the provisional ones from Semester Review ▸ Provisional places as their results '
                          'are in.')
