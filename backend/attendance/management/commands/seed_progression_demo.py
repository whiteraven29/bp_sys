"""A class to practise the end of the year on, in a development database.

    python manage.py seed_progression_demo
    python manage.py seed_progression_demo --reset

Nine students in the active semester, one for each thing the year end has to
decide: promoted, repeating, discontinued, waiting on a supplementary, finished
at level 6, and the one whose semester 1 supplementary has not been marked yet
so the failure can be entered by hand and the consequence watched.

Everything it creates is registered under a registration number beginning
`DEMO/PST/`, which is how `--reset` finds it again. It never touches a student,
enrollment, mark or module that was already there: modules it needs and finds
are reused, and modules it made are removed on reset only when nobody else has
been enrolled in them since.

Development only. It creates a login with a password printed on the screen, so
do not run it anywhere the college's real records live.
"""

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from attendance import finance
from attendance.models import (
    AcademicYear, ChargeType, ClassLevel, Department, FeeStructure, Module,
    OutstandingRepeat, Programme, Semester, SemesterRegistration, SemesterReview,
    StandingChange, Student, StudentProfile, StudentResult, StudentStanding,
)

User = get_user_model()

PREFIX = 'DEMO/PST/'
PORTAL_PIN = 'Demo#2026'
OFFICER = ('demo_exam', 'demo12345')
REPEAT_RATE = Decimal('50000')

#: The module list the demo needs, by level and semester number. A code that
#: already exists in that semester is used as it is.
MODULES = {
    (4, 1): [('PST04101', 'Human Anatomy and Physiology'), ('PST04102', 'Pharmaceutical Calculations'),
             ('PST04103', 'Communication Skills')],
    (4, 2): [('PST04201', 'Dispensing Practice'), ('PST04202', 'Pharmacognosy'),
             ('PST04203', 'Pharmaceutical Chemistry')],
    (5, 1): [('PST05101', 'Pharmacology I'), ('PST05102', 'Pharmaceutical Microbiology'),
             ('PST05103', 'Pharmaceutical Technology')],
    (5, 2): [('PST05201', 'Pharmacology II'), ('PST05202', 'Community Pharmacy'),
             ('PST05203', 'Quality Control')],
    (6, 1): [('PST06101', 'Clinical Pharmacy'), ('PST06102', 'Pharmaceutical Management'),
             ('PST06103', 'Pharmacovigilance')],
    (6, 2): [('PST06201', 'Research Project'), ('PST06202', 'Professional Practice'),
             ('PST06203', 'Hospital Pharmacy')],
}


class Command(BaseCommand):
    help = 'Seed a class of demo students so the end of the year can be practised (development only).'

    def add_arguments(self, parser):
        parser.add_argument('--reset', action='store_true',
                            help='Remove everything a previous run created, and stop.')
        parser.add_argument('--pin', default=PORTAL_PIN,
                            help=f'Portal password for the demo students (default {PORTAL_PIN}).')

    # ── entry ─────────────────────────────────────────────────────────────────

    def handle(self, *args, **options):
        self.pin = options['pin']
        if options['reset']:
            return self.reset()

        semester = Semester.objects.filter(is_active=True).select_related('academic_year').first()
        if semester is None:
            raise CommandError('No semester is active. Set one before seeding.')
        if semester.number != Semester.SEM2:
            self.stdout.write(self.style.WARNING(
                f'The active semester is {semester}. The demo is written for the end of the year, '
                f'so the interesting part — promotion — only happens when semester 2 is active.'))
        year = semester.academic_year
        first = Semester.objects.filter(academic_year=year, number=Semester.SEM1).first()
        if first is None:
            raise CommandError(f'{year} has no semester 1; the demo needs both semesters.')

        with transaction.atomic():
            programme = self.programme()
            levels = self.levels()
            modules = self.modules(programme, levels, first, semester)
            self.repeat_fee(programme, levels, year)
            officer = self.officer()
            made = self.students(programme, levels, modules, first, semester)

        self.report(year, semester, made, officer)

    # ── the world the students live in ────────────────────────────────────────

    def programme(self):
        department, _ = Department.objects.get_or_create(
            code='PST', defaults={'name': 'Pharmaceutical Sciences'})
        programme, _ = Programme.objects.get_or_create(
            code='PST', defaults={'department': department, 'name': 'Pharmaceutical Sciences'})
        return programme

    def levels(self):
        levels = {}
        for order in (4, 5, 6):
            level, _ = ClassLevel.objects.get_or_create(
                order=order, defaults={'name': f'NTA Level {order}'})
            levels[order] = level
        programme = Programme.objects.get(code='PST')
        programme.levels.add(*levels.values())
        return levels

    def modules(self, programme, levels, first, second):
        """The modules the demo teaches. Anything already in the semester is used
        rather than duplicated, and what is created is remembered for reset."""
        made, reused = {}, []
        for (order, number), entries in MODULES.items():
            semester = first if number == 1 else second
            for code, name in entries:
                existing = Module.objects.filter(code=code, semester=semester).first()
                if existing is not None:
                    reused.append(code)
                    made[code] = existing
                    continue
                made[code] = Module.objects.create(
                    code=code, name=name, teacher='Demo Tutor', class_level=levels[order],
                    semester=semester, programme=programme, credits=3)
        self.reused_modules = reused
        return made

    def repeat_fee(self, programme, levels, year):
        """The accountant's per-module repeat rate, for the year the repeats will
        be sat in — without it a repeat sitting is enrolled but not billed."""
        charge_type, _ = ChargeType.objects.get_or_create(
            declaration=ChargeType.REPEAT_MODULE,
            defaults={'name': 'Repeat Module Fee', 'family': ChargeType.FEE,
                      'applies': ChargeType.ON_REQUEST},
        )
        next_year, _ = AcademicYear.objects.get_or_create(name=year.next_name)
        for level in levels.values():
            structure, created = FeeStructure.objects.get_or_create(
                charge_type=charge_type, programme=programme, class_level=level,
                academic_year=next_year,
                defaults={'amount': REPEAT_RATE, 'billing_period': FeeStructure.ONCE,
                          'installments': 1},
            )
            if created:
                finance.set_installment_schedule(structure, [date(int(next_year.name[:4]), 11, 30)])
        self.next_year = next_year
        return charge_type

    def officer(self):
        """An examination officer, because the semester review and the advance
        are both theirs, with the Principal. A records officer cannot do either
        — their work is the student record itself."""
        username, password = OFFICER
        user = User.objects.filter(username=username).first()
        if user is None:
            user = User.objects.create_user(username=username, password=password, is_staff=True)
            return user, password
        return user, None

    # ── the students ──────────────────────────────────────────────────────────

    def person(self, reg_no, name, level, programme, first, second, *, register=(1, 2)):
        profile, _ = StudentProfile.objects.get_or_create(
            nactvet_reg_no=reg_no,
            defaults={'name': name, 'college_id': reg_no.replace('DEMO/PST/', 'BPH/PST/'),
                      'phone': '0712000000', 'gender': StudentProfile.FEMALE},
        )
        for number in register:
            SemesterRegistration.objects.get_or_create(
                profile=profile, semester=first if number == 1 else second,
                defaults={'programme': programme, 'class_level': level,
                          'kind': SemesterRegistration.CONTINUING},
            )
        return profile

    def sit(self, profile, module, *, ca=None, end=None, supp=None, published=True):
        """Enroll and record a result. `ca` and `end` are raw marks out of 100;
        the four CA components carry 40 between them and the paper carries 60,
        so equal marks give that mark as the final total."""
        enrollment, created = Student.objects.get_or_create(
            nactvet_reg_no=profile.nactvet_reg_no, module=module,
            defaults={'name': profile.name, 'profile': profile},
        )
        if created:
            enrollment.set_portal_pin(self.pin, require_change=False)
            enrollment.save(update_fields=['portal_pin_hash', 'must_change_portal_password'])
        if ca is None and end is None:
            return enrollment
        StudentResult.objects.get_or_create(
            student=enrollment,
            defaults={'assign1': ca, 'assign2': ca, 'cat1_theory': ca, 'cat2_theory': ca,
                      'end_theory': end, 'supplementary_mark': supp,
                      'ca_approved': True, 'final_approved': published},
        )
        return enrollment

    def students(self, programme, levels, modules, first, second):
        """One student per decision the year end has to make."""
        l4, l5, l6 = levels[4], levels[5], levels[6]
        made = []

        def note(reg_no, name, expected):
            made.append((reg_no, name, expected))

        # 1. Level 4, everything passed → promoted to level 5.
        asha = self.person(PREFIX + '2025/001', 'Asha Mwinyi', l4, programme, first, second)
        self.sit(asha, modules['PST04101'], ca=78, end=78)
        self.sit(asha, modules['PST04102'], ca=82, end=80)
        self.sit(asha, modules['PST04103'], ca=74, end=70)
        self.sit(asha, modules['PST04201'], ca=75, end=72)
        self.sit(asha, modules['PST04202'], ca=70, end=68)
        self.sit(asha, modules['PST04203'], ca=84, end=82)
        note(asha.nactvet_reg_no, asha.name, 'passed everything → level 5')

        # 2. Level 4, one semester 2 module failed after the supplementary, GPA
        #    still sound → repeats that module, stays level 4, back for
        #    semester 2.
        baraka = self.person(PREFIX + '2025/002', 'Baraka Mnyika', l4, programme, first, second)
        self.sit(baraka, modules['PST04101'], ca=70, end=66)
        self.sit(baraka, modules['PST04102'], ca=65, end=62)
        self.sit(baraka, modules['PST04103'], ca=72, end=70)
        self.sit(baraka, modules['PST04201'], ca=88, end=86)
        self.sit(baraka, modules['PST04202'], ca=70, end=68)
        self.sit(baraka, modules['PST04203'], ca=60, end=42, supp=38)
        note(baraka.nactvet_reg_no, baraka.name,
             'failed the supplementary → repeats PST04203, still level 4')

        # 3. Level 4, GPA below 2.0 → discontinued, back by readmission into
        #    semester 2.
        chausiku = self.person(PREFIX + '2025/003', 'Chausiku Lema', l4, programme, first, second)
        self.sit(chausiku, modules['PST04101'], ca=45, end=52)
        self.sit(chausiku, modules['PST04102'], ca=40, end=50)
        self.sit(chausiku, modules['PST04103'], ca=42, end=50)
        self.sit(chausiku, modules['PST04201'], ca=25, end=50)
        self.sit(chausiku, modules['PST04202'], ca=30, end=44, supp=35)
        self.sit(chausiku, modules['PST04203'], ca=25, end=50)
        note(chausiku.nactvet_reg_no, chausiku.name,
             'GPA below 2.0 → discontinued, back for semester 2')

        # 4. Level 4, semester 2 supplementary not marked → the semester waits,
        #    and the officer decides whether to carry them provisionally.
        daudi = self.person(PREFIX + '2025/004', 'Daudi Komba', l4, programme, first, second)
        self.sit(daudi, modules['PST04101'], ca=74, end=70)
        self.sit(daudi, modules['PST04102'], ca=68, end=66)
        self.sit(daudi, modules['PST04103'], ca=70, end=72)
        self.sit(daudi, modules['PST04201'], ca=80, end=76)
        self.sit(daudi, modules['PST04202'], ca=72, end=70)
        self.sit(daudi, modules['PST04203'], ca=66, end=44)
        note(daudi.nactvet_reg_no, daudi.name,
             'supplementary unmarked → waiting; confirm as provisional')

        # 5. Level 4, semester 1 supplementary sat and not yet marked, already
        #    weeks into semester 2 with coursework recorded. Enter a failing
        #    supplementary mark and confirm her semester 1 review to watch the
        #    authority's rule: she stops there and leaves semester 2.
        # Her other two semester 1 modules are strong enough that the failure is
        # a repeat and not a discontinuation — the rule being demonstrated is
        # what a failed supplementary does, not what a weak year does.
        edina = self.person(PREFIX + '2025/005', 'Edina Mtui', l4, programme, first, second)
        self.sit(edina, modules['PST04101'], ca=86, end=84)
        self.sit(edina, modules['PST04103'], ca=72, end=70)
        self.sit(edina, modules['PST04102'], ca=64, end=43)
        self.sit(edina, modules['PST04201'], ca=68, end=None, published=False)
        self.sit(edina, modules['PST04202'], ca=70, end=None, published=False)
        self.sit(edina, modules['PST04203'], ca=66, end=None, published=False)
        note(edina.nactvet_reg_no, edina.name,
             'semester 1 supplementary unmarked — mark PST04102 failed to see her stop there')

        # 6. Level 5, clear → level 6.
        frida = self.person(PREFIX + '2024/006', 'Frida Massawe', l5, programme, first, second)
        self.sit(frida, modules['PST05101'], ca=76, end=74)
        self.sit(frida, modules['PST05102'], ca=70, end=72)
        self.sit(frida, modules['PST05103'], ca=68, end=70)
        self.sit(frida, modules['PST05201'], ca=68, end=66)
        self.sit(frida, modules['PST05202'], ca=80, end=78)
        self.sit(frida, modules['PST05203'], ca=72, end=70)
        note(frida.nactvet_reg_no, frida.name, 'passed everything → level 6')

        # 7. Level 5, failed a semester 1 module after the supplementary. He has
        #    stopped there, so he is registered for semester 1 only and sat no
        #    semester 2 — the advance has to find him by the module he owes.
        gerald = self.person(PREFIX + '2024/007', 'Gerald Nyoni', l5, programme,
                             first, second, register=(1,))
        self.sit(gerald, modules['PST05101'], ca=62, end=40, supp=36)
        self.sit(gerald, modules['PST05102'], ca=70, end=68)
        self.sit(gerald, modules['PST05103'], ca=74, end=72)
        note(gerald.nactvet_reg_no, gerald.name,
             'failed a semester 1 supplementary → repeats PST05101 when it runs again')

        # 8. Level 6, clear → finished, waiting for clearance.
        hawa = self.person(PREFIX + '2023/008', 'Hawa Selemani', l6, programme, first, second)
        self.sit(hawa, modules['PST06101'], ca=80, end=78)
        self.sit(hawa, modules['PST06102'], ca=76, end=74)
        self.sit(hawa, modules['PST06103'], ca=78, end=76)
        self.sit(hawa, modules['PST06201'], ca=82, end=80)
        self.sit(hawa, modules['PST06202'], ca=78, end=76)
        self.sit(hawa, modules['PST06203'], ca=80, end=78)
        note(hawa.nactvet_reg_no, hawa.name, 'finished level 6 → awaiting clearance')

        # 9. Level 6 with a module still to pass — level 6 is not finished until
        #    everything is cleared.
        iddi = self.person(PREFIX + '2023/009', 'Iddi Mwakalinga', l6, programme, first, second)
        self.sit(iddi, modules['PST06101'], ca=74, end=70)
        self.sit(iddi, modules['PST06102'], ca=68, end=66)
        self.sit(iddi, modules['PST06103'], ca=72, end=70)
        self.sit(iddi, modules['PST06201'], ca=72, end=70)
        self.sit(iddi, modules['PST06202'], ca=64, end=42, supp=40)
        self.sit(iddi, modules['PST06203'], ca=76, end=72)
        note(iddi.nactvet_reg_no, iddi.name,
             'level 6 with a failed supplementary → stays until it is passed')

        return made

    # ── reporting and reset ───────────────────────────────────────────────────

    def report(self, year, semester, made, officer):
        from attendance import progression

        user, password = officer
        self.stdout.write(self.style.SUCCESS(f'\nSeeded {len(made)} demo student(s) into {semester}.'))
        if self.reused_modules:
            self.stdout.write(f'Modules already there and reused: {", ".join(sorted(set(self.reused_modules)))}')

        self.stdout.write('\nWhat the results say right now, per student:')
        for reg_no, name, expected in made:
            profile = StudentProfile.objects.get(nactvet_reg_no=reg_no)
            outcome = progression.evaluate(profile, semester)
            gpa = '—' if outcome['gpa'] is None else f'{outcome["gpa"]:.2f}'
            self.stdout.write(f'  {reg_no:20} {name:18} GPA {gpa:>5}  '
                              f'{outcome["proposed"]:12} — {expected}')

        self.stdout.write('\nStudent portal: any of the registration numbers above, password '
                          f'{self.pin!r}')
        if password:
            self.stdout.write(f'Examination officer login: {user.username} / {password}')
        else:
            self.stdout.write(
                f'Examination officer login: {user.username} (already existed, password unchanged)')

        self.stdout.write(self.style.MIGRATE_HEADING('\nTo drive the year end:'))
        self.stdout.write(
            '  1. Sign in as the examination officer → Semester Review.\n'
            f'  2. Pick {semester.label}, press "Read the results again", then\n'
            '     "Confirm everyone not in doubt". Daudi is left for you — decide him\n'
            '     as "continues while results are awaited".\n'
            '  3. For Edina: switch to semester 1, enter 30 as her PST04102 supplementary\n'
            '     mark (Results), then confirm her semester 1 review and\n'
            '     watch her leave the semester 2 class list.\n'
            '  4. Dashboard → "Advance Semester" for the preview, then advance.\n'
            '  5. Undo it all with:  python manage.py seed_progression_demo --reset')

    def reset(self):
        """Remove what a previous run created, and nothing else."""
        profiles = StudentProfile.objects.filter(nactvet_reg_no__startswith=PREFIX)
        enrollments = Student.objects.filter(nactvet_reg_no__startswith=PREFIX)
        codes = [code for entries in MODULES.values() for code, _ in entries]

        counts = {
            'students': profiles.count(),
            'enrollments': enrollments.count(),
            'results': StudentResult.objects.filter(
                student__nactvet_reg_no__startswith=PREFIX).count(),
            'registrations': SemesterRegistration.objects.filter(profile__in=profiles).count(),
            'reviews': SemesterReview.objects.filter(profile__in=profiles).count(),
            'repeats': OutstandingRepeat.objects.filter(profile__in=profiles).count(),
            'standings': StudentStanding.objects.filter(profile__in=profiles).count(),
        }

        with transaction.atomic():
            StandingChange.objects.filter(profile__in=profiles).delete()
            enrollments.delete()
            profiles.delete()

            # A module the demo made is only removed when nobody else has been
            # enrolled in it since. One somebody is teaching is left alone.
            removed, kept = [], []
            for module in Module.objects.filter(code__in=codes, teacher='Demo Tutor'):
                if module.students.exists():
                    kept.append(module.code)
                else:
                    removed.append(module.code)
                    module.delete()

            User.objects.filter(username=OFFICER[0]).delete()

        self.stdout.write(self.style.SUCCESS(
            'Removed: ' + ', '.join(f'{value} {key}' for key, value in counts.items() if value)))
        if removed:
            self.stdout.write(f'Modules removed: {", ".join(sorted(removed))}')
        if kept:
            self.stdout.write(f'Modules kept (other students are enrolled): {", ".join(sorted(kept))}')
        self.stdout.write('Left alone: the repeat fee, the next academic year, and every student '
                          'that was not part of the demo.')
