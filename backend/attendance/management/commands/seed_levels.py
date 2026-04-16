from django.core.management.base import BaseCommand
from attendance.models import ClassLevel, AcademicYear, Semester

LEVELS = [
    {'name': 'NTA Level 4', 'order': 4},
    {'name': 'NTA Level 5', 'order': 5},
    {'name': 'NTA Level 6', 'order': 6},
]


class Command(BaseCommand):
    help = 'Seed NTA class levels and initial academic year/semester'

    def handle(self, *args, **options):
        for lvl in LEVELS:
            obj, created = ClassLevel.objects.get_or_create(
                name=lvl['name'],
                defaults={'order': lvl['order']},
            )
            if created:
                self.stdout.write(self.style.SUCCESS(f'  Created level: {obj.name}'))
            else:
                self.stdout.write(f'  Already exists: {obj.name}')

        year, year_created = AcademicYear.objects.get_or_create(
            name='2025/2026',
            defaults={'is_active': True},
        )
        if year_created:
            self.stdout.write(self.style.SUCCESS(f'  Created academic year: {year.name}'))
        else:
            self.stdout.write(f'  Academic year already exists: {year.name}')

        sem1, s1_created = Semester.objects.get_or_create(
            academic_year=year, number=1, defaults={'is_active': True},
        )
        if s1_created:
            self.stdout.write(self.style.SUCCESS(f'  Created: {sem1}'))
        else:
            self.stdout.write(f'  Already exists: {sem1}')

        sem2, s2_created = Semester.objects.get_or_create(
            academic_year=year, number=2, defaults={'is_active': False},
        )
        if s2_created:
            self.stdout.write(self.style.SUCCESS(f'  Created: {sem2}'))
        else:
            self.stdout.write(f'  Already exists: {sem2}')

        if not year_created and not year.is_active:
            self.stdout.write('  (Year exists but is not active — not modifying active flags)')

        self.stdout.write(self.style.SUCCESS('Done.'))
