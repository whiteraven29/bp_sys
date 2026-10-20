"""Merge programmes into one (said 20 October 2026).

PST was set up as three programmes — one per NTA level (Basic Technician
Certificate at 4, Technician Certificate at 5, Ordinary Diploma at 6). The
system reads a programme as one course of study across its levels: the year
end moves a student to the next level *of their programme*, so a programme
with Level 4 alone would finish every Level 4 student instead of moving them
up. The three certificates are NACTVET's awards along the way, not programmes.

    python manage.py merge_programmes --into OD --from BTC TC
    python manage.py merge_programmes --into OD --from BTC TC --code PST \\
        --name "Ordinary Diploma in Pharmaceutical Sciences" --apply

Without --apply nothing is written: it says what it would move. With it, in
one transaction, every module, registration, standing, review, application
and fee cell of the --from programmes moves to --into, which takes their
levels; then the --from programmes are deleted. A fee cell both programmes
have for the same charge, level and year: the --into cell is kept, the
charges billed from the other are pointed at it — and when the two amounts
differ the run says so, to be checked before --apply. If any merged
programme was active, the one that stays is active. Students' college ID
numbers are left as they were issued; their results, marks and payments are
not touched.
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from attendance.models import (
    Application, FeeStructure, Module, Programme, SemesterRegistration, SemesterReview, StudentStanding,
)

MOVED = [
    (Module, 'modules'), (SemesterRegistration, 'registrations'), (StudentStanding, 'standings'),
    (SemesterReview, 'semester reviews'), (Application, 'applications'),
]


class Command(BaseCommand):
    help = 'Merge programmes into one (PST set up as one programme per level).'

    def add_arguments(self, parser):
        parser.add_argument('--into', required=True, help='Code of the programme that stays.')
        parser.add_argument('--from', dest='sources', nargs='+', required=True,
                            help='Codes of the programmes merged into it (then deleted).')
        parser.add_argument('--code', help='A new code for the programme that stays, e.g. PST.')
        parser.add_argument('--name', help='A new name for the programme that stays.')
        parser.add_argument('--apply', action='store_true', help='Write the changes. Without it: a dry run.')

    def handle(self, *args, **options):
        target = Programme.objects.filter(code__iexact=options['into']).first()
        if target is None:
            raise CommandError(f"No programme with the code {options['into']}.")
        sources = []
        for code in options['sources']:
            source = Programme.objects.filter(code__iexact=code).first()
            if source is None:
                raise CommandError(f'No programme with the code {code}.')
            if source.pk == target.pk:
                raise CommandError(f'{code} is the programme that stays; leave it out of --from.')
            if source.structure != Programme.SEMESTER or target.structure != Programme.SEMESTER:
                raise CommandError('Only NACTVET (semester) programmes are merged; VETA courses are left alone.')
            sources.append(source)
        new_code = (options.get('code') or '').strip().upper()
        if new_code and Programme.objects.filter(code__iexact=new_code).exclude(
                pk__in=[target.pk] + [s.pk for s in sources]).exists():
            raise CommandError(f'The code {new_code} belongs to another programme.')

        apply = options['apply']
        self.stdout.write(('APPLYING' if apply else 'DRY RUN — nothing is written') + '\n')
        self.stdout.write(f'Stays:  {self._describe(target)}')
        with transaction.atomic():
            for source in sources:
                self.stdout.write(f'Merged: {self._describe(source)}')
                for model, label in MOVED:
                    count = model.objects.filter(programme=source).count()
                    if count:
                        self.stdout.write(f'  {count} {label}')
                    model.objects.filter(programme=source).update(programme=target)
                self._fee_cells(source, target)
                target.levels.add(*source.levels.all())
                source.id_counters.all().delete()
                source.delete()
            if new_code:
                target.code = new_code
            if options.get('name'):
                target.name = options['name'].strip()
            if any(source.is_active for source in sources):
                # Admission picks from the active programmes only.
                target.is_active = True
            target.save()
            self.stdout.write(f'Now:    {self._describe(target)}')
            if not apply:
                transaction.set_rollback(True)
        self.stdout.write('Done.' if apply else 'Dry run only. Add --apply to write it.')

    def _describe(self, programme):
        levels = ', '.join(l.name for l in programme.levels.order_by('order')) or 'none'
        return (f'{programme.code} — {programme.name} (levels {levels}; '
                f'department {programme.department.name}; {"active" if programme.is_active else "NOT active"})')

    def _fee_cells(self, source, target):
        moved = clashed = 0
        for cell in FeeStructure.objects.filter(programme=source):
            twin = FeeStructure.objects.filter(
                programme=target, charge_type=cell.charge_type, class_level=cell.class_level,
                academic_year=cell.academic_year).first()
            if twin is None:
                cell.programme = target
                cell.save(update_fields=['programme'])
                moved += 1
                continue
            # Both programmes priced it: keep the one that stays, and bill from it.
            if twin.amount != cell.amount:
                self.stdout.write(self.style.WARNING(
                    f'  ! {cell.charge_type.name}, {cell.class_level.name if cell.class_level else "all levels"}, '
                    f'{cell.academic_year.name}: {source.code} has {cell.amount:,.0f}, '
                    f'{target.code} has {twin.amount:,.0f} — {target.code}\'s amount is kept'))
            for relation in cell._meta.related_objects:
                if relation.one_to_many and relation.related_model.__name__ != 'FeeInstallment':
                    relation.related_model.objects.filter(**{relation.field.name: cell}).update(
                        **{relation.field.name: twin})
            cell.delete()
            clashed += 1
        if moved or clashed:
            self.stdout.write(f'  {moved} fee cells moved' + (f', {clashed} duplicates folded in' if clashed else ''))
