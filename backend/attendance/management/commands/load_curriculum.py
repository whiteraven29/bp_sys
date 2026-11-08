"""Load a curriculum and its assessment plans from the JSON that
attendance/curricula/extract.py made from the national documents.

    python manage.py load_curriculum                       # the 2022 PST curriculum
    python manage.py load_curriculum path/to/other.json --dry-run

Run again whenever the file changes: modules, outcomes, tasks and the parts of
each assessment scheme are updated in place, found by their codes and letters,
so teaching plans keep their links. Nothing a plan points at is deleted; what
the file no longer has is reported, not removed.
"""

import json
from decimal import Decimal
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from attendance.models import (
    AssessmentComponent, Curriculum, CurriculumModule, LearningOutcome, Programme, RelatedTask,
)

DEFAULT = Path(__file__).resolve().parents[2] / 'curricula' / 'pst_2022.json'


class Command(BaseCommand):
    help = 'Load a curriculum (outcomes, tasks, weekly hours, assessment schemes) from its JSON.'

    def add_arguments(self, parser):
        parser.add_argument('path', nargs='?', default=str(DEFAULT))
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, path, dry_run, **options):
        try:
            data = json.loads(Path(path).read_text())
        except (OSError, ValueError) as exc:
            raise CommandError(f'Cannot read {path}: {exc}')
        with transaction.atomic():
            report = load(data)
            for line in report:
                self.stdout.write(line)
            if dry_run:
                transaction.set_rollback(True)
                self.stdout.write(self.style.WARNING('Dry run: nothing saved.'))
            else:
                self.stdout.write(self.style.SUCCESS('Loaded.'))


def load(data):
    report = []
    curriculum, made = Curriculum.objects.update_or_create(
        code=data['code'], defaults={
            'name': data['name'], 'note': data.get('note', ''),
            'programme': Programme.objects.filter(code=data.get('programme')).first(),
            'weeks_of_study': data.get('weeks_of_study', 15),
            'weeks_of_assessment': data.get('weeks_of_assessment', 2)})
    report.append(f'{"Made" if made else "Updated"} {curriculum.code}: {curriculum.name}')
    enabling = data.get('enabling', {})
    for m in data['modules']:
        hours = m.get('hours_per_week', {})
        module, made = CurriculumModule.objects.update_or_create(
            curriculum=curriculum, code=m['code'], name=m['name'], defaults={
                'level': m['level'], 'semester': m['semester'], 'credits': m.get('credits', 0),
                'lecture_hours': hours.get('L', 0), 'tutorial_hours': hours.get('T', 0),
                'practical_hours': hours.get('P', 0), 'assignment_hours': hours.get('A', 0),
                'checks': m.get('checks', [])})
        tasks_by = {}
        for order, o in enumerate(m.get('outcomes', [])):
            outcome, _ = LearningOutcome.objects.update_or_create(
                module=module, code=o['code'], defaults={
                    'text': o['text'], 'enabling_code': o.get('enabling', ''),
                    'enabling_text': enabling.get(o.get('enabling', ''), ''), 'criteria': o.get('criteria', ''),
                    'order': order})
            for t_order, t in enumerate(o.get('tasks', [])):
                task, _ = RelatedTask.objects.update_or_create(
                    outcome=outcome, letter=t['letter'], defaults={'text': t['text'], 'order': t_order})
                tasks_by[(o['code'], t['letter'])] = task
        have = list(module.components.order_by('order'))
        for order, c in enumerate(m.get('assessment', [])):
            component = next((x for x in have if x.stage == c['stage'] and x.method == c['method']
                              and x.order == order), None)
            fields = {'stage': c['stage'], 'method': c['method'], 'label': c['label'][:120],
                      'tool': c.get('tool', '')[:200], 'min_count': c.get('min_count') or 1,
                      'weight': Decimal(str(c['weight'])), 'order': order}
            if component is None:
                component = AssessmentComponent.objects.create(module=module, **fields)
            else:
                for key, value in fields.items():
                    setattr(component, key, value)
                component.save()
            required, unplaced = [], []
            for item in c.get('covers', []):
                for letter in item.get('tasks', []):
                    task = tasks_by.get((item.get('seo'), letter))
                    if task is not None:
                        required.append(task)
                    else:
                        unplaced.append(f'{item.get("seo") or "SEO ?"} {letter}')
            component.required_tasks.set(required)
            component.unplaced = ', '.join(unplaced)
            component.save(update_fields=['unplaced'])
        count = sum(len(o.get('tasks', [])) for o in m.get('outcomes', []))
        report.append(f'  {"+" if made else "~"} {module.code} {module.name}: {len(m.get("outcomes", []))} SEOs, '
                      f'{count} tasks, {len(m.get("assessment", []))} assessment parts'
                      + (f' — {len(m["checks"])} note(s) on the documents' if m.get('checks') else ''))
    return report
