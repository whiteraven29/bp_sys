"""Take a settled REPEAT off the desks of a semester they do not repeat in
(said 26 October 2026).

A repeater placed provisionally when 2026/27 opened, whose repeat sitting
then read NS (not sat), was settled REPEAT — and kept the semester 1 place
and finance application a REPEAT takes away: a repeat not sat raised
nothing. Fixed for every settlement from now on; this puts right the ones
settled before.

For each student whose last confirmed review is REPEAT and who still holds a
place in a semester they owe no module in, it does what confirming the
REPEAT does: what they owe stays owed, their standing brings them back in the
semester they repeat (semester 2 for a semester 2 module), and the place
after it is called off — the application cancelled, its fees reversed, what
was paid kept as credit. A repeater who does owe a module in the semester they
are placed in is left where they are.

    python manage.py call_off_repeat_places            # what it would do
    python manage.py call_off_repeat_places --apply    # do it

Results and marks are not touched. Safe to run twice.
"""

from django.core.management.base import BaseCommand

from attendance import progression


def describe(row):
    review = row['review']
    owed = ', '.join(f'{code} (sem {number}, {label})' for code, number, label in row['will_owe']) or 'nothing'
    places = ', '.join(sorted({place.label for place in row['places']}))
    return (f'  {review.profile.nactvet_reg_no:<20} {review.profile.name:<32} REPEAT in {review.semester.label}; '
            f'will owe {owed}; placed in {places}')


class Command(BaseCommand):
    help = 'Call off the places settled REPEAT students still hold in a semester they do not repeat in.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='Write the changes. Without it: a dry run.')

    def handle(self, *args, **options):
        found, kept = progression.repeat_places_kept()
        # Nothing to owe means nothing would bring them back: never call off a
        # place that leaves a student with no way back.
        to_call_off = [row for row in found if row['will_owe']]
        unknown = [row for row in found if not row['will_owe']]
        self.stdout.write(f'{len(to_call_off)} REPEAT student(s) placed in a semester they owe nothing in:')
        for row in to_call_off:
            self.stdout.write(describe(row))
        if unknown:
            self.stdout.write(f'Left where they are — what they owe cannot be read from their results '
                              f'(check their repeat sheet; {len(unknown)}):')
            for row in unknown:
                self.stdout.write(describe(row))
        if kept:
            self.stdout.write(f'Left where they are — they repeat a module of that semester ({len(kept)}):')
            for row in kept:
                self.stdout.write(describe(row))

        if not options['apply']:
            self.stdout.write('Dry run: nothing written. Read it, then run again with --apply.')
            return
        for row in to_call_off:
            progression.call_off_repeat_place(row['review'])
        self.stdout.write(self.style.SUCCESS(
            f'{len(to_call_off)} place(s) called off. When the semester they repeat opens, the advance '
            'hands them to admissions for the module(s) they owe.'))
