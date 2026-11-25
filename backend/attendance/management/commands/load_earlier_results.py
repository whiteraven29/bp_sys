"""Load an earlier year's NACTVET sheets from a folder of upload files, once
(said 25 November 2026: 2024/25 semester 2 held only the students on the
repeat sheet, so comparing the years was false).

The same import as Results ▸ Earlier Results, file by file: each workbook's
"Read me" names the year, the semester, the level and the sitting; its
"Grades" sheet has a row per student, its "Modules" sheet the codes, names and
credits. First sittings are loaded before supplementaries.

    python manage.py load_earlier_results /path/to/folder            # what it would do
    python manage.py load_earlier_results /path/to/folder --apply    # do it

A grade already there is never replaced by a different one, so a student
loaded before keeps theirs; safe to run twice. Students the system never held
get a plain record (no password, no bill, no class list), as the page does.
The files hold students' names and grades: keep them out of git.
"""

import re
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from openpyxl import load_workbook

from attendance import authority, progression
from attendance.models import ClassLevel, Semester

READ_ME = re.compile(r'year (?P<year>\d{4}/\d{4}), semester (?P<semester>[12]), NTA Level (?P<level>\d), '
                     r'sitting: (?P<sitting>First sitting|Supplementary)', re.I)


class _DryRun(Exception):
    """Raised to undo everything a dry run did."""


def read(path):
    book = load_workbook(path, read_only=True, data_only=True)
    if not {'Grades', 'Modules', 'Read me'} <= set(book.sheetnames):
        raise CommandError(f'{path.name}: needs the sheets Grades, Modules and Read me.')
    title = next((str(row[0]) for row in book['Read me'].iter_rows(values_only=True) if row and row[0]), '')
    found = READ_ME.search(title)
    if found is None:
        raise CommandError(f'{path.name}: its Read me does not say the year, semester, level and sitting.')
    grades = list(book['Grades'].iter_rows(values_only=True))
    codes = [str(code).strip() for code in grades[0][2:] if code]
    rows = [{'reg_no': str(row[0]).strip(), 'name': str(row[1] or '').strip(), 'module_code': code,
             'grade': str(grade or '').strip()}
            for row in grades[1:] if row and row[0] for code, grade in zip(codes, row[2:])]
    modules = [{'code': row[0], 'name': row[1], 'credits': row[2]}
               for row in list(book['Modules'].iter_rows(values_only=True))[1:] if row and row[0]]
    return {'path': path, 'year': found['year'], 'semester': int(found['semester']), 'level': int(found['level']),
            'sitting': 'first' if found['sitting'].lower().startswith('first') else 'supp',
            'rows': rows, 'modules': modules, 'students': len({row['reg_no'] for row in rows})}


class Command(BaseCommand):
    help = "Load an earlier year's NACTVET sheets (Earlier Results files) from a folder, once."

    def add_arguments(self, parser):
        parser.add_argument('folder', help='The folder of .xlsx files (first sitting and supplementary per level).')
        parser.add_argument('--apply', action='store_true', help='Write the grades. Without it: a dry run.')

    def handle(self, *args, folder, apply=False, **options):
        files = sorted(Path(folder).expanduser().glob('*.xlsx'))
        if not files:
            raise CommandError(f'No .xlsx files in {folder}.')
        sheets = sorted((read(path) for path in files),
                        key=lambda s: (s['year'], s['semester'], s['level'], s['sitting'] != 'first'))
        levels = {}
        for sheet in sheets:
            level = ClassLevel.objects.filter(order=sheet['level']).first()
            if level is None:
                raise CommandError(f'{sheet["path"].name}: no NTA Level {sheet["level"]} in the system.')
            levels[sheet['level']] = level

        self.stdout.write(('Loading' if apply else 'Dry run — nothing is written') + f': {len(sheets)} file(s).')
        try:
            with transaction.atomic():
                for sheet in sheets:
                    level = levels[sheet['level']]
                    if sheet['sitting'] == 'first':
                        done = authority.import_first_sitting(sheet['year'], sheet['semester'], level,
                                                              sheet['modules'], sheet['rows'])
                    else:
                        done = authority.import_supplementary(sheet['year'], sheet['semester'], level, sheet['rows'])
                    self.stdout.write(
                        f'  {sheet["path"].name}\n'
                        f'    {sheet["students"]} students · {done.get("saved", 0)} grades saved, '
                        f'{done.get("unchanged", 0)} already there'
                        + (f', {done["students_recorded"]} new student records, {done["sittings_added"]} sittings added'
                           if sheet['sitting'] == 'first' else '')
                        + (f' · {len(done["errors"])} not taken: {done["errors"][:5]}' if done.get('errors') else ''))
                # What Results Summary now says, level by level.
                for key in sorted({(s['year'], s['semester'], s['level']) for s in sheets}):
                    semester = Semester.objects.get(academic_year__name=key[0], number=key[1])
                    counts = progression.results_summary(semester, levels[key[2]])['counts']
                    self.stdout.write(f'  {key[0]} semester {key[1]} · NTA Level {key[2]}: {counts["total"]} students — '
                                      f'PASS {counts["PASS"]}, SUPP {counts["SUPP"]}, REPEAT {counts["REPEAT"]}, '
                                      f'FAIL {counts["DISCO"]}, INCOMPLETE {counts["INCOMPLETE"]}')
                if not apply:
                    raise _DryRun
        except _DryRun:
            self.stdout.write(self.style.WARNING('Dry run: undone. Add --apply to load them.'))
            return
        self.stdout.write(self.style.SUCCESS('Loaded.'))
