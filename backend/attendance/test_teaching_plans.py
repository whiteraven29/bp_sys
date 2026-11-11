"""Teaching plans, the curriculum behind them and the teaching calendar (said
8–9 November 2026). The data are the real ones: the 2022 PST curriculum and
assessment plans (curricula/pst_2022.json) and the 2026/27 almanac — so the
PST04104 plan here is checked against the plan its tutor wrote by hand
(Monday L2+T1, Thursday P4+A1, 120 hours over 15 weeks; written tests 5%
each, practical tests 12.5%, assignments 2.5%; examination 15 + 45)."""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIClient

from . import teaching_plans as tp
from .management.commands.load_curriculum import load as load_curriculum
from .management.commands.load_teaching_calendar import load_semester
from .models import (
    AcademicYear, ClassLevel, CurriculumModule, Department, Form, Module, PlanAssessment, PlanSession,
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
        return tp.start_plan(self.module, self.tutor)

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
        plan = tp.start_plan(module, self.tutor)
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
        plan = tp.start_plan(old, self.tutor)
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
        plan = tp.start_plan(module, self.tutor)
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

    def test_approval_and_moves_after_it(self):
        plan = self.laid_out()
        tp.submit(plan, actor=self.tutor)
        with self.assertRaises(tp.PlanError):
            tp.lay_out(plan, plan.pattern, actor=self.tutor)
        self.assertFalse(tp.may_approve(self.module, self.tutor))
        self.assertTrue(tp.may_approve(self.module, self.hod))
        with self.assertRaises(tp.PlanError):
            tp.decide(plan, False, note='', actor=self.hod)
        tp.decide(plan, True, actor=self.hod)
        session = plan.sessions.order_by('planned_on').first()
        with self.assertRaises(tp.PlanError):
            tp.edit_session(session, {'planned_on': date(2026, 11, 3)}, actor=self.tutor)
        tp.edit_session(session, {'planned_on': date(2026, 11, 3), 'reason': 'Power cut'}, actor=self.tutor)
        session.refresh_from_db()
        self.assertEqual((session.moved_from, session.move_reason), (date(2026, 11, 2), 'Power cut'))

    def test_a_head_of_department_never_approves_their_own(self):
        self.module.teachers.add(self.hod)
        self.assertFalse(tp.may_approve(self.module, self.hod))
        self.assertTrue(tp.may_approve(self.module, self.dparc))

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


class ApiTests(PlanBase):
    def test_only_a_tutor_of_the_module_starts_and_lays_out_its_plan(self):
        self.assertEqual(client_for(self.other).post('/api/teaching-plans/', {'module_id': self.module.id},
                                                     format='json').status_code, 403)
        made = client_for(self.tutor).post('/api/teaching-plans/', {'module_id': self.module.id}, format='json')
        self.assertEqual(made.status_code, 201)
        pk = made.data['id']
        self.assertEqual(client_for(self.qa).post(f'/api/teaching-plans/{pk}/lay-out/',
                                                  {'pattern': made.data['pattern']}, format='json').status_code, 403)
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
                                 format='json').status_code, 403)

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
