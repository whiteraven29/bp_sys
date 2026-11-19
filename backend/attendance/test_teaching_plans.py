"""Teaching plans, the curriculum behind them and the teaching calendar (said
8–9 November 2026). The data are the real ones: the 2022 PST curriculum and
assessment plans (curricula/pst_2022.json) and the 2026/27 almanac — so the
PST04104 plan here is checked against the plan its tutor wrote by hand
(Monday L2+T1, Thursday P4+A1, 120 hours over 15 weeks; written tests 5%
each, practical tests 12.5%, assignments 2.5%; examination 15 + 45)."""

import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from . import teaching_kpis as tk
from . import teaching_plans as tp
from .management.commands.load_curriculum import load as load_curriculum
from .management.commands.load_teaching_calendar import load_semester
from .models import (
    AcademicYear, ClassLevel, CurriculumModule, Department, Form, Module, Notification, PlanAssessment, PlanSession,
    Programme, Semester, Session, TeachingPlan,
)
from .views import set_roles

DATA = Path(__file__).resolve().parent / 'curricula'


def person(username, roles, name=None):
    user = User.objects.create_user(username, password='pw')
    set_roles(user, roles, full_name=name or username.title())
    return user


def client_for(user):
    client = APIClient()
    client.force_authenticate(user)
    return client


def qa_approved(plan, qa=None):
    """As if the tutor's PDF had been uploaded and QA had approved it (said 19
    November 2026: a plan is set up only after that)."""
    TeachingPlan.objects.filter(pk=plan.pk).update(
        document_status=TeachingPlan.DOC_APPROVED, document_decided_by=qa,
        document_decided_at=timezone.now())
    plan.refresh_from_db()
    return plan


def pdf_file(name='plan.pdf', body=b'%PDF-1.4\n%fake but starts right\n'):
    from django.core.files.uploadedfile import SimpleUploadedFile
    return SimpleUploadedFile(name, body, content_type='application/pdf')


class PlanBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        load_curriculum(json.loads((DATA / 'pst_2022.json').read_text()))
        cls.year = AcademicYear.objects.create(name='2026/2027', is_active=True)
        cls.sem1 = Semester.objects.create(academic_year=cls.year, number=1, is_active=True)
        almanac = json.loads((DATA / 'almanac_2026_2027.json').read_text())
        load_semester(cls.sem1, almanac['semesters']['1'])
        cls.level4 = ClassLevel.objects.create(name='NTA Level 4', order=4)
        cls.hod = person('hod', ['hod', 'tutor'], 'Head Of Dept')
        cls.department = Department.objects.create(name='Pharmaceutical Sciences', code='PST', hod=cls.hod)
        cls.programme = Programme.objects.create(department=cls.department, name='Pharmaceutical Sciences', code='PST')
        cls.tutor = person('daniel', ['tutor'], 'Daniel B. Mwangela')
        cls.other = person('other', ['tutor'])
        cls.dparc = person('dparc', ['dparc'])
        cls.qa = person('qa', ['qa'], 'Quality Assurance')
        cls.principal = person('principal_user', ['tutor'])
        cls.module = Module.objects.create(name='Computer Application in Pharmacy Practice', code='PST04104',
                                           teacher='Daniel', class_level=cls.level4, semester=cls.sem1,
                                           programme=cls.programme, has_practical=True, credits=12)
        cls.module.teachers.add(cls.tutor)

    def plan(self):
        return qa_approved(tp.start_plan(self.module, self.tutor), self.qa)

    def laid_out(self):
        plan = self.plan()
        return tp.lay_out(plan, tp.default_pattern(plan.curriculum_module, self.module), actor=self.tutor)


class CurriculumTests(PlanBase):
    def test_the_module_reads_as_the_documents_say(self):
        cm = CurriculumModule.objects.get(code='PST04104', level=4)
        self.assertEqual(cm.name, 'Computer Application in Pharmacy Practice')
        self.assertEqual((cm.lecture_hours, cm.tutorial_hours, cm.practical_hours, cm.assignment_hours), (2, 1, 4, 1))
        self.assertEqual(cm.credits, 12)
        self.assertEqual(cm.outcomes.count(), 12)
        self.assertEqual(sum(o.tasks.count() for o in cm.outcomes.all()), 59)   # "Total number of tasks (59)"
        parts = [(c.stage, c.method, c.min_count, c.weight) for c in cm.components.order_by('order')]
        self.assertEqual(parts, [('ca', 'written_test', 2, Decimal('10')), ('ca', 'assignment', 2, Decimal('5')),
                                 ('ca', 'practical_test', 2, Decimal('25')), ('se', 'written_exam', 1, Decimal('15')),
                                 ('se', 'practical_exam', 1, Decimal('45'))])
        written = cm.components.get(method='written_test')
        self.assertIn('5.1.1 a', [f'{t.outcome.code} {t.letter}' for t in written.required_tasks.all()])

    def test_ca_is_forty_and_se_sixty_everywhere(self):
        """Said 9 November 2026: the curriculum's CA 60 / SE 40 is a typing error."""
        for cm in CurriculumModule.objects.prefetch_related('components'):
            ca = sum(c.weight for c in cm.components.all() if c.stage == 'ca')
            se = sum(c.weight for c in cm.components.all() if c.stage == 'se')
            if ca or se:
                self.assertEqual((ca, se), (40, 60), cm.code)
        laws = CurriculumModule.objects.get(code='PST04106')
        self.assertTrue(any('printed as 40%' in check for check in laws.checks))

    def test_where_the_documents_disagree_is_kept(self):
        communication = CurriculumModule.objects.get(code='PST04207')
        self.assertTrue(any('PST04107' in check for check in communication.checks))

    def test_a_module_of_the_old_curriculum_finds_nothing(self):
        """PST04104 is Pharmaceutical Dosage Forms in the old curriculum."""
        old = Module.objects.create(name='Pharmaceutical Dosage Forms', code='PST04104', teacher='x',
                                    class_level=self.level4,
                                    semester=Semester.objects.create(academic_year=self.year, number=2))
        self.assertIsNone(tp.curriculum_module_for(old))
        self.assertEqual(tp.curriculum_module_for(self.module).name, 'Computer Application in Pharmacy Practice')


class OldCurriculumTests(PlanBase):
    """The old curriculum for Levels 5 and 6 (said 11 November 2026), read from
    its assessment plans of March 2019 (curricula/pst_2015.json), beside the
    2022 one. Its plans keep the weights results use; it adds the outcomes."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        load_curriculum(json.loads((DATA / 'pst_2015.json').read_text()))
        cls.level5 = ClassLevel.objects.create(name='NTA Level 5', order=5)

    def level5_module(self, code, name, semester=None, **extra):
        module = Module.objects.create(name=name, code=code, teacher='x', class_level=self.level5,
                                       semester=semester or self.sem1, programme=self.programme, **extra)
        module.teachers.add(self.tutor)
        return module

    def test_the_old_module_reads_as_its_assessment_plan_says(self):
        cm = CurriculumModule.objects.get(curriculum__code='PST-2015', code='PST05101')
        self.assertEqual(cm.name, 'Medicines and Medical Supplies Management')
        self.assertEqual([o.code for o in cm.outcomes.all()], ['1.1.1', '1.1.2', '1.1.3', '1.2.3', '5.3.1'])
        self.assertEqual(sum(o.tasks.count() for o in cm.outcomes.all()), 33)
        self.assertEqual([(c.stage, c.method, c.min_count, c.weight) for c in cm.components.order_by('order')],
                         [('ca', 'written_test', 2, Decimal('30')), ('ca', 'assignment', 2, Decimal('10')),
                          ('se', 'written_exam', 1, Decimal('60'))])
        self.assertEqual((cm.credits, cm.hours_per_week), (0, 0))      # the plans give neither
        # The plan heads Pharmacology and Therapeutics PST05204; it is PST05104.
        fixed = CurriculumModule.objects.get(curriculum__code='PST-2015', code='PST05104')
        self.assertTrue(any('PST05204' in check for check in fixed.checks))

    def test_each_level_5_module_finds_its_own_curriculum(self):
        old = self.level5_module('PST05101', 'Medicines and Medical Supplies Management')
        next_year = AcademicYear.objects.create(name='2027/2028')     # the new curriculum reaches Level 5
        new = self.level5_module('PST05101', 'Purchasing and Selling of Health Commodities',
                                 semester=Semester.objects.create(academic_year=next_year, number=1))
        self.assertEqual(tp.curriculum_module_for(old).curriculum.code, 'PST-2015')
        self.assertEqual(tp.curriculum_module_for(new).curriculum.code, 'PST-2022')

    def test_its_plan_keeps_the_weights_results_use_and_gains_the_outcomes(self):
        # The plan says written tests 18% and practical tests 18%; results use 16 and 20.
        module = self.level5_module('PST05209', 'Health Information Management', has_practical=True)
        plan = qa_approved(tp.start_plan(module, self.tutor))
        self.assertEqual(plan.curriculum_module.curriculum.code, 'PST-2015')
        self.assertTrue(plan.pattern)                                   # no hours given: the usual guess
        tp.lay_out(plan, plan.pattern, actor=self.tutor)
        weights = sorted((a.method, a.weight) for a in plan.assessments.all())
        self.assertIn(('written_test', Decimal('8.00')), weights)
        self.assertIn(('practical_test', Decimal('10.00')), weights)
        self.assertIn(('written_exam', Decimal('30.00')), weights)
        self.assertTrue(plan.sessions.exclude(outcome=None).exists())
        # Each CAT is tied to the plan's written tests, so what they must cover is followed.
        test = plan.assessments.get(method='written_test', number=1)
        self.assertEqual(test.component.module_id, plan.curriculum_module_id)
        gaps = {g['component']: g for g in tp.coverage(plan)['components']}
        self.assertEqual(gaps['Written tests']['required'], 11)    # test one 6 tasks, test two 5
        detail = client_for(self.tutor).get(f'/api/teaching-plans/{plan.id}/').json()
        self.assertTrue(detail['scheme_source'].startswith('results today'))


class LayOutTests(PlanBase):
    def test_the_pattern_comes_from_the_scheme_of_study(self):
        plan = self.plan()
        self.assertEqual(plan.pattern, [{'weekday': 0, 'kind': 'lecture', 'hours': 2},
                                        {'weekday': 0, 'kind': 'tutorial', 'hours': 1},
                                        {'weekday': 3, 'kind': 'practical', 'hours': 4},
                                        {'weekday': 3, 'kind': 'assignment', 'hours': 1}])

    def test_fifteen_weeks_around_the_recess_and_120_hours(self):
        plan = self.laid_out()
        sessions = list(plan.sessions.order_by('planned_on'))
        self.assertEqual(len(sessions), 60)
        self.assertEqual(sum(s.hours for s in sessions), 120)
        self.assertEqual(sessions[0].planned_on, date(2026, 11, 2))
        self.assertEqual(sessions[-1].planned_on, date(2027, 2, 25))
        self.assertFalse([s for s in sessions if date(2026, 12, 21) <= s.planned_on <= date(2027, 1, 3)])
        self.assertEqual(sessions[0].outcome.code, '5.1.1')
        self.assertEqual(sessions[-1].outcome.code, '5.3.6')

    def test_a_class_on_a_holiday_moves_within_its_week(self):
        plan = self.plan()
        tp.lay_out(plan, [{'weekday': 1, 'kind': 'lecture', 'hours': 2}], actor=self.tutor)
        days = set(plan.sessions.values_list('planned_on', flat=True))
        self.assertNotIn(date(2027, 1, 12), days)            # Zanzibar Revolution Day
        moved = plan.sessions.get(planned_on=date(2027, 1, 13))
        self.assertIn('Zanzibar', moved.note)

    def test_assessments_follow_the_assessment_plan_and_the_almanac(self):
        plan = self.laid_out()
        rows = {(a.method, a.number): a for a in plan.assessments.all()}
        self.assertEqual(rows[('written_test', 1)].weight, Decimal('5.00'))
        self.assertEqual(rows[('written_test', 1)].planned_on, date(2027, 1, 18))      # CAT 1
        self.assertEqual(rows[('practical_test', 2)].planned_on, date(2027, 2, 15))    # CAT 2
        self.assertEqual(rows[('practical_test', 2)].weight, Decimal('12.50'))
        self.assertEqual(rows[('assignment', 2)].weight, Decimal('2.50'))
        self.assertLess(rows[('assignment', 2)].planned_on, date(2027, 2, 26))         # results due
        self.assertEqual((rows[('written_exam', 1)].weight, rows[('practical_exam', 1)].weight),
                         (Decimal('15.00'), Decimal('45.00')))
        self.assertEqual(rows[('written_exam', 1)].planned_on, date(2027, 3, 30))

    def test_a_module_the_curriculum_does_not_describe_uses_the_weights_results_use(self):
        old = Module.objects.create(name='Pharmaceutical Dosage Forms', code='PST04199', teacher='x',
                                    class_level=self.level4, semester=self.sem1, has_practical=True)
        old.teachers.add(self.tutor)
        plan = qa_approved(tp.start_plan(old, self.tutor))
        tp.lay_out(plan, plan.pattern, actor=self.tutor)
        weights = sorted((a.method, a.weight) for a in plan.assessments.all())
        self.assertIn(('assignment', Decimal('2.00')), weights)
        self.assertIn(('written_test', Decimal('8.00')), weights)
        self.assertIn(('practical_test', Decimal('10.00')), weights)
        self.assertIn(('written_exam', Decimal('30.00')), weights)
        self.assertFalse(plan.sessions.exclude(outcome=None).exists())

    def test_no_calendar_no_layout(self):
        other_sem = Semester.objects.create(academic_year=self.year, number=2)
        module = Module.objects.create(name='Basic Pharmacology', code='PST04208', teacher='x',
                                       class_level=self.level4, semester=other_sem)
        module.teachers.add(self.tutor)
        plan = qa_approved(tp.start_plan(module, self.tutor))
        with self.assertRaises(tp.PlanError):
            tp.lay_out(plan, plan.pattern, actor=self.tutor)


class FollowingTests(PlanBase):
    def test_progress_is_against_the_plan_and_the_register_is_the_evidence(self):
        plan = self.laid_out()
        Session.objects.create(module=self.module, date=date(2026, 11, 2), label='L1')
        due = list(plan.sessions.filter(planned_on__lte=date(2026, 11, 12)).order_by('planned_on'))
        self.assertEqual(len(due), 8)
        for s in due[:6]:
            tp.mark(s, PlanSession.DONE, delivered_on=s.planned_on, actor=self.tutor, today=date(2026, 11, 12))
        reading = tp.progress(plan, date(2026, 11, 12))
        self.assertEqual(reading['on_schedule'], 75)
        self.assertEqual(reading['band'], 'behind')
        self.assertEqual(reading['without_register'], 4)          # only 2 Nov had a register
        self.assertEqual(reading['weeks_behind'], 0)
        self.assertEqual(tp.progress(plan, date(2026, 11, 3))['band'], 'on_track')   # as at a checkpoint

    def test_a_class_is_not_marked_before_it_is_taught(self):
        plan = self.laid_out()
        with self.assertRaises(tp.PlanError):
            tp.mark(plan.sessions.last(), PlanSession.DONE, delivered_on=date(2027, 2, 25), actor=self.tutor,
                    today=date(2026, 11, 12))

    def test_set_up_it_is_approved_with_the_pdf_and_classes_move_one_at_a_time(self):
        plan = self.laid_out()
        tp.submit(plan, actor=self.tutor)
        plan.refresh_from_db()
        self.assertEqual((plan.status, plan.decided_by, plan.decision_note),
                         (TeachingPlan.APPROVED, self.qa, 'Set up after QA approved the PDF.'))
        with self.assertRaises(tp.PlanError):
            tp.lay_out(plan, plan.pattern, actor=self.tutor)
        self.assertFalse(tp.may_approve_plan(self.module, self.tutor))
        self.assertFalse(tp.may_approve_plan(self.module, self.hod))        # the HOD moderates tests only
        self.assertTrue(tp.may_approve_plan(self.module, self.qa))
        self.assertTrue(tp.may_moderate(self.module, self.hod))
        session = plan.sessions.order_by('planned_on').first()
        with self.assertRaises(tp.PlanError):
            tp.edit_session(session, {'planned_on': date(2026, 11, 3)}, actor=self.tutor)
        tp.edit_session(session, {'planned_on': date(2026, 11, 3), 'reason': 'Power cut'}, actor=self.tutor)
        session.refresh_from_db()
        self.assertEqual((session.moved_from, session.move_reason), (date(2026, 11, 2), 'Power cut'))

    def test_a_class_taught_early_moves_to_its_day_and_counts_there(self):
        plan = self.laid_out()
        tp.submit(plan, actor=self.tutor)
        register = Session.objects.create(module=self.module, date=date(2026, 11, 30), label='L5')
        later = plan.sessions.filter(planned_on__gte=date(2026, 12, 7)).order_by('planned_on').first()
        first_day = later.planned_on
        monday_week = plan.sessions.filter(planned_on=date(2026, 11, 30)).first().week
        today = date(2026, 11, 30)
        with self.assertRaises(tp.PlanError):            # not yet taught
            tp.taught_early(later, date(2026, 12, 1), actor=self.tutor, today=today)
        with self.assertRaises(tp.PlanError):            # not before its day
            tp.taught_early(later, first_day, actor=self.tutor, today=first_day)
        tp.taught_early(later, date(2026, 11, 30), actor=self.tutor, today=today)
        later.refresh_from_db()
        self.assertEqual((later.planned_on, later.moved_from, later.move_reason, later.status),
                         (date(2026, 11, 30), first_day, 'Taught early', PlanSession.DONE))
        self.assertEqual(later.register, register)
        self.assertEqual(later.week, monday_week)        # drawn in the week it was taught
        self.assertIn(later.id, [s.id for s in plan.sessions.filter(planned_on__lte=today)])
        self.assertEqual(tp.progress(plan, today)['done_due'], 1)
        self.assertTrue(plan.history.filter(action='taught a class early').exists())
        with self.assertRaises(tp.PlanError):            # already marked
            tp.taught_early(later, date(2026, 11, 29), actor=self.tutor, today=today)

    def test_what_a_class_covers_goes_into_the_history_once_the_plan_is_sent(self):
        plan = self.laid_out()
        first, second = plan.sessions.exclude(outcome=None).order_by('planned_on')[:2]
        other = plan.curriculum_module.outcomes.exclude(pk=first.outcome_id).first()
        was, heading = first.outcome.code, f'{first.get_kind_display()} of {first.planned_on:%a %d %b}'
        tp.edit_session(first, {'topic': 'Draft wording'}, actor=self.tutor)
        self.assertFalse(plan.history.filter(action='changed a class').exists())     # still a draft
        tp.submit(plan, actor=self.tutor)
        tp.edit_session(first, {'outcome_id': other.id, 'topic': 'Spreadsheets'}, actor=self.tutor)
        tp.edit_session(second, {'topic': second.topic}, actor=self.tutor)           # no change, no entry
        entries = list(plan.history.filter(action='changed a class').values_list('detail', flat=True))
        self.assertEqual(len(entries), 1)
        self.assertIn(f'SEO {was} → {other.code}', entries[0])
        self.assertIn('topic "Draft wording" → "Spreadsheets"', entries[0])
        self.assertTrue(entries[0].startswith(heading))

    def test_a_head_of_department_never_approves_their_own(self):
        self.module.teachers.add(self.hod)
        self.assertFalse(tp.may_moderate(self.module, self.hod))
        self.assertTrue(tp.may_moderate(self.module, self.dparc))

    def test_moderation_and_coverage(self):
        plan = self.laid_out()
        test1 = plan.assessments.get(method='written_test', number=1)
        with self.assertRaises(tp.PlanError):
            tp.moderate(test1, 'submit', actor=self.tutor)            # no setting plan yet
        before = next(c for c in tp.coverage(plan)['components'] if c['component'] == 'Written Tests')
        cm = plan.curriculum_module
        early_task = cm.outcomes.get(code='5.1.1').tasks.get(letter='a')
        late_task = cm.outcomes.get(code='5.3.6').tasks.get(letter='a')    # taught in February
        tp.edit_assessment(test1, {'task_ids': [early_task.id, late_task.id]}, actor=self.tutor)
        after = next(c for c in tp.coverage(plan)['components'] if c['component'] == 'Written Tests')
        self.assertEqual(after['covered'], before['covered'] + 2)
        early = tp.coverage(plan)['tested_before_taught']
        self.assertEqual([e['task'] for e in early], ['5.3.6 a'])
        tp.moderate(test1, 'submit', actor=self.tutor)
        with self.assertRaises(tp.PlanError):
            tp.moderate(test1, 'return', note='', actor=self.hod)
        tp.moderate(test1, 'approve', actor=self.hod)
        self.assertEqual(PlanAssessment.objects.get(pk=test1.pk).moderation, PlanAssessment.APPROVED)


class FollowUpTests(PlanBase):
    """Classes partly taught, missed, made up, finished in the next class,
    and taught off the plan (said 16 November 2026)."""

    def sent(self):
        plan = self.laid_out()
        tp.submit(plan, actor=self.tutor)
        return plan

    def day(self, plan, on, nth=0):
        return list(plan.sessions.filter(planned_on=on, extra=False).order_by('order'))[nth]

    def test_a_partly_taught_class_finished_in_the_next_class_counts_as_done_from_then(self):
        plan = self.sent()
        today = date(2026, 11, 12)
        monday = self.day(plan, date(2026, 11, 2))
        thursday = self.day(plan, date(2026, 11, 5))
        tp.mark(monday, PlanSession.PARTIAL, delivered_on=monday.planned_on, actor=self.tutor, today=today)
        tp.mark(thursday, PlanSession.DONE, delivered_on=thursday.planned_on, actor=self.tutor, today=today)
        with self.assertRaises(tp.PlanError):          # a class taught in full is not finished later
            tp.finish_later(thursday, monday, actor=self.tutor)
        earlier = PlanSession.objects.create(plan=plan, week=1, planned_on=date(2026, 11, 1), kind='lecture',
                                             hours=1, extra=True)
        with self.assertRaises(tp.PlanError):          # nor in an earlier class
            tp.finish_later(monday, earlier, actor=self.tutor)
        earlier.delete()
        tp.finish_later(monday, thursday, actor=self.tutor)
        monday.refresh_from_db()
        sessions = list(plan.sessions.all())
        by_id = {s.id: s for s in sessions}
        self.assertEqual(tp._credit(by_id[monday.id], date(2026, 11, 4), by_id), 0.5)   # before Thursday
        self.assertEqual(tp._credit(by_id[monday.id], date(2026, 11, 5), by_id), 1)     # from Thursday
        reading = tp.progress(plan, date(2026, 11, 5), sessions)
        self.assertEqual(reading['made_up'], 1)
        self.assertEqual(PlanSession.objects.get(pk=thursday.pk).status, PlanSession.DONE)   # its own mark kept
        detail = tp.plan_detail(plan, self.tutor, today)
        row = next(r for r in detail['sessions'] if r['id'] == thursday.id)
        self.assertEqual(row['finishes'], [tp._label(monday)])
        self.assertTrue(plan.history.filter(action='finished a class later').exists())
        tp.finish_later(monday, None, actor=self.tutor)                  # taken back
        monday.refresh_from_db()
        self.assertIsNone(monday.finished_in)
        tp.finish_later(monday, thursday, actor=self.tutor)
        tp.mark(monday, PlanSession.DONE, delivered_on=monday.planned_on, actor=self.tutor, today=today)
        monday.refresh_from_db()
        self.assertIsNone(monday.finished_in)                            # taught in full itself

    def test_a_make_up_class_is_extra_and_makes_up_a_missed_one(self):
        plan = self.sent()
        today = date(2026, 11, 12)
        missed = self.day(plan, date(2026, 11, 2))
        tp.mark(missed, PlanSession.MISSED, note='Power cut', actor=self.tutor, today=today)
        before = tp.progress(plan, today)
        self.assertEqual(before['missed'], 1)
        makeup = tp.add_session(plan, {'planned_on': date(2026, 11, 7), 'kind': 'lecture', 'hours': 2,
                                       'finishes_id': missed.id}, actor=self.tutor)
        self.assertTrue(makeup.extra)
        self.assertEqual(tp.progress(plan, today)['due'], before['due'])      # not one of the plan's classes
        tp.mark(makeup, PlanSession.DONE, delivered_on=makeup.planned_on, actor=self.tutor, today=today)
        after = tp.progress(plan, today)
        self.assertEqual((after['missed'], after['made_up'], after['extra']), (0, 1, 1))
        self.assertEqual(after['done_due'], before['done_due'] + 1)
        self.assertEqual(after['hours_done'], before['hours_done'] + 2)
        # An extra class that finishes nothing: hours taught rise, the schedule does not move.
        revision = tp.add_session(plan, {'planned_on': date(2026, 11, 10), 'kind': 'revision', 'hours': 1},
                                  actor=self.tutor)
        tp.mark(revision, PlanSession.DONE, delivered_on=revision.planned_on, actor=self.tutor, today=today)
        again = tp.progress(plan, today)
        self.assertEqual((again['on_schedule'], again['hours_done']), (after['on_schedule'], after['hours_done'] + 1))

    def test_a_class_added_while_preparing_is_one_of_the_plans(self):
        plan = self.laid_out()
        added = tp.add_session(plan, {'planned_on': date(2026, 11, 7), 'kind': 'practical', 'hours': 2},
                               actor=self.tutor)
        self.assertFalse(added.extra)
        self.assertEqual(added.week, self.day(plan, date(2026, 11, 2)).week)

    def test_registers_not_in_the_plan_and_entering_one(self):
        plan = self.sent()
        today = date(2026, 11, 12)
        monday = self.day(plan, date(2026, 11, 2))
        Session.objects.create(module=self.module, date=date(2026, 11, 2), label='L1', topic='Excel')
        saturday = Session.objects.create(module=self.module, date=date(2026, 11, 7), label='P1',
                                          session_type=Session.PRACTICAL, topic='Extra practical')
        outside = tp.registers_outside(plan, today)
        self.assertEqual([r['date'] for r in outside], [date(2026, 11, 2), date(2026, 11, 7)])
        self.assertIn(monday.id, [c['id'] for c in outside[0]['planned']])   # the class to mark that day
        self.assertEqual(outside[1]['planned'], [])
        tp.mark(monday, PlanSession.DONE, delivered_on=monday.planned_on, actor=self.tutor, today=today)
        self.assertEqual([r['date'] for r in tp.registers_outside(plan, today)], [date(2026, 11, 7)])
        registers = Session.objects.count()
        made = tp.class_from_register(plan, saturday, {'hours': 3}, actor=self.tutor, today=today)
        self.assertEqual((made.extra, made.kind, made.status, made.register_id, made.topic),
                         (True, 'practical', PlanSession.DONE, saturday.id, 'Extra practical'))
        self.assertEqual(tp.registers_outside(plan, today), [])
        self.assertEqual(Session.objects.count(), registers)                 # the registers are untouched
        draft = tp.start_plan(Module.objects.create(
            name='Other', code='PST04199', teacher='x', class_level=self.level4, semester=self.sem1,
            programme=self.programme), self.tutor)
        with self.assertRaises(tp.PlanError):
            tp.class_from_register(draft, saturday, {}, actor=self.tutor)


class ExtraBackfillTests(PlanBase):
    def test_classes_added_after_sending_are_marked_extra_from_the_history(self):
        import importlib
        from django.apps import apps
        migration = importlib.import_module('attendance.migrations.0087_plan_session_follow_up')
        plan = self.laid_out()
        before = tp.add_session(plan, {'planned_on': date(2026, 11, 7), 'kind': 'practical'}, actor=self.tutor)
        tp.submit(plan, actor=self.tutor)
        after = tp.add_session(plan, {'planned_on': date(2026, 11, 14), 'kind': 'lecture'}, actor=self.tutor)
        tp.edit_session(after, {'planned_on': date(2026, 11, 13), 'reason': 'Moved'}, actor=self.tutor)
        PlanSession.objects.filter(plan=plan).update(extra=False)          # as before migration 0087
        migration.mark_extras(apps, None)
        self.assertEqual(list(PlanSession.objects.filter(extra=True).values_list('id', flat=True)), [after.id])
        self.assertFalse(PlanSession.objects.get(pk=before.pk).extra)


class KpiTests(PlanBase):
    def test_bands(self):
        self.assertEqual([tk.band('on_schedule', v) for v in (95, 90, 80, 69, None)],
                         ['met', 'met', 'near', 'below', 'not_measured'])
        self.assertEqual([tk.band('missed_open', v) for v in (0, 2, 3)], ['met', 'near', 'below'])
        self.assertEqual([tk.band('tested_early', v) for v in (0, 1)], ['met', 'below'])
        self.assertEqual(len(tk.KPIS), 10)

    def test_a_tutors_kpis_come_from_the_plans(self):
        plan = self.laid_out()
        tp.submit(plan, actor=self.tutor)
        day = date(2026, 11, 12)
        due = list(plan.sessions.filter(planned_on__lte=day).order_by('planned_on'))
        Session.objects.create(module=self.module, date=due[0].planned_on, label='L1')
        Session.objects.create(module=self.module, date=date(2026, 11, 7), label='Sat')     # not in the plan
        for s in due[:6]:
            tp.mark(s, PlanSession.DONE, delivered_on=s.planned_on, actor=self.tutor, today=day)
        tp.mark(due[6], PlanSession.MISSED, actor=self.tutor, today=day)
        test1 = plan.assessments.get(method='written_test', number=1)
        moderated = timezone.make_aware(datetime(2026, 11, 10, 9, 0))
        PlanAssessment.objects.filter(pk=test1.pk).update(
            planned_on=date(2026, 11, 11), held_on=date(2026, 11, 11), moderation=PlanAssessment.APPROVED,
            moderated_at=moderated)
        data = tk.tutor_kpis(self.qa, self.sem1, day)
        row = next(r for r in data['tutors'] if r['name'] == 'Daniel B. Mwangela')
        v, b = row['values'], row['bands']
        self.assertEqual(v['plans_approved'], 100)
        self.assertEqual(v['on_schedule'], tp.progress(plan, day)['on_schedule'])     # 6 of 8: 75
        self.assertEqual((v['on_schedule'], b['on_schedule']), (75, 'near'))
        self.assertEqual(v['missed_open'], 1)
        self.assertEqual(v['registers_outside'], 1)
        self.assertEqual(v['evidence'], round(200 / 6))        # 2 Nov's lecture and tutorial share its register
        self.assertEqual((v['held'], v['moderated']), (100, 100))
        self.assertEqual(row['modules'][0]['code'], 'PST04104')
        self.assertEqual(len(data['trend']['weeks']), 2)                               # 2–8 and 9–12 Nov
        self.assertEqual(data['trend']['all'][-1], 75)
        self.assertNotIn('Other', [r['name'] for r in data['tutors']])                 # teaches nothing
        # Who may read them.
        self.assertEqual(client_for(self.qa).get('/api/teaching-plans/kpis/').status_code, 200)
        self.assertEqual(client_for(self.hod).get('/api/teaching-plans/kpis/').status_code, 200)
        self.assertEqual(client_for(self.tutor).get('/api/teaching-plans/kpis/').status_code, 403)


class ApiTests(PlanBase):
    def test_only_a_tutor_of_the_module_starts_and_lays_out_its_plan(self):
        self.assertEqual(client_for(self.other).post('/api/teaching-plans/', {'module_id': self.module.id},
                                                     format='json').status_code, 403)
        made = client_for(self.tutor).post('/api/teaching-plans/', {'module_id': self.module.id}, format='json')
        self.assertEqual(made.status_code, 201)
        pk = made.data['id']
        self.assertEqual(client_for(self.qa).post(f'/api/teaching-plans/{pk}/lay-out/',
                                                  {'pattern': made.data['pattern']}, format='json').status_code, 403)
        refused = client_for(self.tutor).post(f'/api/teaching-plans/{pk}/lay-out/', {'pattern': made.data['pattern']},
                                              format='json')
        self.assertEqual(refused.status_code, 400)                          # QA has not approved the PDF
        self.assertIn('QA approves your plan first', refused.data['detail'])
        qa_approved(TeachingPlan.objects.get(pk=pk), self.qa)
        laid = client_for(self.tutor).post(f'/api/teaching-plans/{pk}/lay-out/', {'pattern': made.data['pattern']},
                                           format='json')
        self.assertEqual(laid.status_code, 200)
        self.assertEqual(laid.data['progress']['sessions'], 60)

    def test_who_follows_which_plans(self):
        self.laid_out()
        for user, sees in ((self.qa, True), (self.dparc, True), (self.hod, True), (self.tutor, True),
                           (self.other, False)):
            rows = client_for(user).get('/api/teaching-plans/?scope=all').data['rows']
            self.assertEqual(bool([r for r in rows if r['code'] == 'PST04104']), sees, user.username)
        # A tutor given no module yet sees an empty list, which the screen
        # explains ("Your Head of Department gives you your modules"), and
        # the calendar and curricula; nobody else gets in.
        self.assertEqual(client_for(self.other).get('/api/teaching-plans/?scope=mine').data['rows'], [])
        self.assertEqual(client_for(self.other).get('/api/curricula/').status_code, 200)
        accountant = person('accountant_user', ['accountant'])
        self.assertEqual(client_for(accountant).get('/api/teaching-plans/?scope=mine').status_code, 403)
        self.assertEqual(client_for(accountant).get('/api/curricula/').status_code, 403)
        waiting = client_for(self.hod).get('/api/teaching-plans/?scope=approve').data['rows']
        self.assertEqual([r['code'] for r in waiting], ['PST04104'])
        self.assertEqual(client_for(self.dparc).get('/api/teaching-plans/?scope=approve').data['rows'][0]['code'],
                         'PST04104')

    def test_qa_reads_and_notes_but_changes_nothing(self):
        plan = self.laid_out()
        qa = client_for(self.qa)
        self.assertEqual(qa.get(f'/api/teaching-plans/{plan.id}/').status_code, 200)
        noted = qa.post(f'/api/teaching-plans/{plan.id}/notes/', {'text': 'Week 3 behind', 'checkpoint': 'CQI 1'},
                        format='json')
        self.assertEqual(noted.data['notes'][0]['text'], 'Week 3 behind')
        session = plan.sessions.first()
        self.assertEqual(qa.post(f'/api/teaching-plans/{plan.id}/sessions/{session.id}/mark/',
                                 {'status': 'done'}, format='json').status_code, 403)
        self.assertEqual(qa.post(f'/api/teaching-plans/{plan.id}/decide/', {'approve': True},
                                 format='json').status_code, 400)                  # nothing waits for it
        self.assertEqual(qa.post(f'/api/teaching-plans/{plan.id}/lay-out/', {'pattern': plan.pattern},
                                 format='json').status_code, 403)

    def test_only_a_tutor_marks_a_class_taught_early(self):
        plan = self.laid_out()
        later = plan.sessions.order_by('-planned_on').first()
        if later.planned_on <= date.today():
            self.skipTest('The plan of 2026/27 has no class left to come.')
        url = f'/api/teaching-plans/{plan.id}/sessions/{later.id}/taught-early/'
        payload = {'delivered_on': date.today().isoformat(), 'status': 'partial', 'reason': 'Ahead of schedule'}
        self.assertEqual(client_for(self.qa).post(url, payload, format='json').status_code, 403)
        done = client_for(self.tutor).post(url, payload, format='json')
        self.assertEqual(done.status_code, 200)
        row = next(s for s in done.data['sessions'] if s['id'] == later.id)
        self.assertEqual((row['status'], row['move_reason']), ('partial', 'Ahead of schedule'))
        self.assertEqual(client_for(self.tutor).post(url, payload, format='json').status_code, 400)

    def test_the_tutors_own_document_is_kept(self):
        plan = self.plan()
        tutor = client_for(self.tutor)
        fake = SimpleUploadedFile('plan.pdf', b'not a pdf at all', content_type='application/pdf')
        self.assertEqual(tutor.post(f'/api/teaching-plans/{plan.id}/document/', {'file': fake},
                                    format='multipart').status_code, 400)
        real = SimpleUploadedFile('PST04104 plan.pdf', b'%PDF-1.4 a plan', content_type='application/pdf')
        self.assertEqual(tutor.post(f'/api/teaching-plans/{plan.id}/document/', {'file': real},
                                    format='multipart').status_code, 200)
        got = client_for(self.qa).get(f'/api/teaching-plans/{plan.id}/document/')
        self.assertEqual(got.status_code, 200)
        self.assertEqual(b''.join(got.streaming_content), b'%PDF-1.4 a plan')
        TeachingPlan.objects.get(pk=plan.pk).document.delete()

    def test_dparc_keeps_the_calendar(self):
        payload = {'semester_id': self.sem1.id, 'teaching_starts': '2026-11-02', 'teaching_ends': '2027-03-26'}
        self.assertEqual(client_for(self.tutor).post('/api/teaching-calendar/', payload, format='json').status_code, 403)
        self.assertEqual(client_for(self.dparc).post('/api/teaching-calendar/', payload, format='json').status_code, 200)
        added = client_for(self.dparc).post('/api/teaching-calendar/events/', {
            'semester_id': self.sem1.id, 'kind': 'holiday', 'title': 'Graduation', 'starts_on': '2027-01-15'},
            format='json')
        self.assertIn('Graduation', [e['title'] for e in added.data['events']])
        seen = client_for(self.tutor).get(f'/api/teaching-calendar/?semester_id={self.sem1.id}').data
        self.assertFalse(seen['can_edit'])
        self.assertEqual(seen['calendar']['teaching_starts'], date(2026, 11, 2))


class QaEvaluationFormTests(PlanBase):
    """Said 9 November 2026: QA runs every student and staff evaluation form;
    service requests stay with the departments."""

    def test_qa_makes_evaluation_forms_not_service_requests(self):
        qa = client_for(self.qa)
        made = qa.post('/api/forms/', {'title': 'Module evaluation', 'kind': 'evaluation'}, format='json')
        self.assertEqual(made.status_code, 201, made.data)
        section = qa.post('/api/form-sections/', {'form': made.data['id'], 'title': 'Teaching'}, format='json')
        self.assertEqual(section.status_code, 201, section.data)
        self.assertEqual(qa.patch(f'/api/forms/{made.data["id"]}/', {'title': 'Module evaluation 2026/27'},
                                  format='json').status_code, 200)
        self.assertEqual(qa.post('/api/forms/', {'title': 'Transcript request', 'kind': 'request'},
                                 format='json').status_code, 403)
        request_form = Form.objects.create(title='Leave', slug='leave', kind=Form.REQUEST)
        self.assertEqual(qa.patch(f'/api/forms/{request_form.id}/', {'title': 'x'}, format='json').status_code, 403)
        self.assertEqual(client_for(self.tutor).post('/api/forms/', {'title': 'x', 'kind': 'evaluation'},
                                                     format='json').status_code, 403)


class CostTests(PlanBase):
    def test_a_whole_plan_is_read_in_a_bounded_number_of_queries(self):
        """Sixty classes once asked for each tutor's name sixty times over."""
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        plan = self.laid_out()
        for s in plan.sessions.order_by('planned_on')[:10]:
            tp.mark(s, PlanSession.DONE, delivered_on=s.planned_on, actor=self.tutor, today=date(2027, 1, 1))
        with CaptureQueriesContext(connection) as queries:
            tp.plan_detail(TeachingPlan.objects.get(pk=plan.pk), self.qa, date(2027, 1, 1))
        self.assertLess(len(queries), 80)
        with CaptureQueriesContext(connection) as queries:
            tp.overview(self.qa, self.sem1, date(2027, 1, 1))
        self.assertLess(len(queries), 60)


class QaApprovesThePdfTests(PlanBase):
    """Said 19 November 2026: the tutor uploads the teaching plan as a PDF, QA
    reads and approves it, and only then is the plan set up in the system —
    approved with the PDF, no second approval. The HOD reads and moderates."""

    def upload(self, client, plan, upload):
        return client.post(f'/api/teaching-plans/{plan.id}/document/', {'file': upload}, format='multipart')

    def test_upload_qa_approves_then_set_up(self):
        plan = tp.start_plan(self.module, self.tutor)
        tutor, qa, hod = client_for(self.tutor), client_for(self.qa), client_for(self.hod)
        self.assertEqual(self.upload(tutor, plan, pdf_file('plan.docx', b'PK\x03\x04')).status_code, 400)
        self.assertEqual(self.upload(tutor, plan, pdf_file('plan.pdf', b'not a pdf')).status_code, 400)
        self.assertEqual(self.upload(qa, plan, pdf_file()).status_code, 403)          # a tutor of the module
        sent = self.upload(tutor, plan, pdf_file('PST04104 plan.pdf'))
        self.assertEqual(sent.status_code, 200, sent.data)
        self.assertEqual(sent.data['document_review']['status'], 'pending')
        self.assertFalse(sent.data['can']['set_up'])
        self.assertTrue(Notification.objects.filter(user=self.qa, kind=Notification.PLAN_SENT).exists())
        # QA sees it waiting, reads it in the browser, returns it with a reason, then approves the next one.
        rows = qa.get('/api/teaching-plans/?scope=qa').data['rows']
        self.assertEqual([(r['code'], r['document_status']) for r in rows], [('PST04104', 'pending')])
        opened = qa.get(f'/api/teaching-plans/{plan.id}/document/?view=1')
        self.assertEqual((opened.status_code, opened['Content-Type']), (200, 'application/pdf'))
        self.assertNotIn('attachment', opened['Content-Disposition'])
        url = f'/api/teaching-plans/{plan.id}/document/decide/'
        self.assertEqual(hod.post(url, {'approve': True}, format='json').status_code, 403)
        self.assertEqual(qa.post(url, {'approve': False}, format='json').status_code, 400)   # say why
        back = qa.post(url, {'approve': False, 'note': 'Add the practical weeks'}, format='json')
        self.assertEqual(back.data['document_review']['status'], 'returned')
        self.assertTrue(Notification.objects.filter(user=self.tutor, kind=Notification.PLAN_DECIDED,
                                                    body__contains='Add the practical weeks').exists())
        self.upload(tutor, plan, pdf_file('PST04104 plan v2.pdf'))
        done = qa.post(url, {'approve': True}, format='json')
        self.assertEqual(done.data['document_review']['status'], 'approved')
        self.assertEqual(qa.post(url, {'approve': True}, format='json').status_code, 400)     # nothing waits
        # Now the tutor sets it up; set up, it is approved — no second approval.
        laid = tutor.post(f'/api/teaching-plans/{plan.id}/lay-out/', {'pattern': plan.pattern}, format='json')
        self.assertEqual(laid.status_code, 200, laid.data)
        self.assertTrue(laid.data['can']['set_up'])
        ready = tutor.post(f'/api/teaching-plans/{plan.id}/submit/', {}, format='json')
        self.assertEqual((ready.data['status'], ready.data['decision']['by']), ('approved', 'Quality Assurance'))
        # A new version goes back to QA; the plan already set up goes on.
        again = self.upload(tutor, plan, pdf_file('PST04104 plan v3.pdf'))
        self.assertEqual((again.data['status'], again.data['document_review']['status']), ('approved', 'pending'))

    def test_qa_never_approves_a_plan_of_a_module_they_teach(self):
        self.module.teachers.add(self.qa)
        self.assertFalse(tp.may_approve_plan(self.module, self.qa))

    def test_a_plan_started_before_keeps_its_classes_and_asks_for_the_pdf(self):
        plan = tp.start_plan(self.module, self.tutor)
        qa_approved(plan)
        tp.lay_out(plan, plan.pattern, actor=self.tutor)
        TeachingPlan.objects.filter(pk=plan.pk).update(document_status='')        # as before 19 November
        plan.refresh_from_db()
        tp.lay_out(plan, plan.pattern, actor=self.tutor)                          # its classes may be laid again
        with self.assertRaises(tp.PlanError):
            tp.submit(plan, actor=self.tutor)                                     # but it is set up after QA
        first = plan.sessions.order_by('planned_on').first()
        tp.mark(first, PlanSession.DONE, delivered_on=first.planned_on, actor=self.tutor,
                today=first.planned_on)                                           # and marked meanwhile

    def test_a_pdf_attached_before_waits_for_qa(self):
        import importlib
        from django.apps import apps
        migration = importlib.import_module('attendance.migrations.0088_teaching_plan_pdf_for_qa')
        plan = tp.start_plan(self.module, self.tutor)
        plan.document.save('old.pdf', pdf_file().file, save=True)
        word = tp.start_plan(Module.objects.create(name='Other', code='PST04198', teacher='x',
                                                   class_level=self.level4, semester=self.sem1), self.tutor)
        word.document.save('old.docx', pdf_file('old.docx', b'PK').file, save=True)
        migration.pdfs_wait_for_qa(apps, None)
        self.assertEqual(TeachingPlan.objects.get(pk=plan.pk).document_status, 'pending')
        self.assertEqual(TeachingPlan.objects.get(pk=word.pk).document_status, '')

