"""Said 24 October 2026: the college chooses when results reach students.

A semester's results are shown on the portal level by level, only once the
examination office shows them; students chosen for it are withheld, alone or
several at once; the portal reads every year, not just this one, each
semester classed at its own level; a student placed provisionally is told so
at the top of the portal; and the examination officer settles provisional
passes (a failure stays with the Principal and DPARC).
"""

from importlib import import_module

from django.apps import apps
from django.contrib.auth import get_user_model

from .models import (
    ClassLevel, Module, ResultRelease, ResultWithholding, SemesterReview, Student, StudentProfile,
)
from .test_authority_results import AuthorityBase
from .test_finance_desk_only import office

User = get_user_model()


class PortalBase(AuthorityBase):
    def setUp(self):
        super().setUp()
        self.profile, (self.enrollment, _) = self.student('NIT/30', 'Rehema')
        self.enrollment.set_portal_pin('Portal#2026', require_change=False)
        self.enrollment.save()
        self.upload(self.exam, [('NIT/30', 'PST04101', 'A'), ('NIT/30', 'PST04102', 'B')])
        session = self.client.session
        session['student_id'] = self.enrollment.id
        session.save()

    def portal(self):
        return self.client.get('/student-dashboard/').context

    def show(self, semester, level, visible=True, client=None):
        return (client or self.exam).post('/api/result-releases/', {
            'semester_id': semester.id, 'class_level_id': level.id, 'visible': visible}, format='json')

    def summary(self, semester=None):
        return self.exam.get(f'/api/results-summary/?semester_id={(semester or self.sem1).id}'
                             f'&class_level_id={self.level4.id}').data


class ShowToStudentsTests(PortalBase):
    def test_hidden_until_the_exam_office_shows_them(self):
        self.assertEqual(self.portal()['result_statements'], [])
        self.assertFalse(self.summary()['visible_to_students'])

        self.assertEqual(self.show(self.sem1, self.level4).status_code, 200)
        statements = self.portal()['result_statements']
        self.assertEqual([row['grade'] for row in statements[0]['modules']], ['A', 'B'])
        self.assertTrue(self.summary()['visible_to_students'])

        self.show(self.sem1, self.level4, visible=False)
        self.assertEqual(self.portal()['result_statements'], [])
        self.assertEqual(self.portal()['published_result_count'], 0)

    def test_only_the_exam_office_shows_them(self):
        _, principal = office('boss', 'principal')
        self.assertEqual(self.show(self.sem1, self.level4, client=principal).status_code, 403)
        self.assertFalse(ResultRelease.objects.exists())

    def test_a_withheld_student_stays_withheld_when_the_rest_are_shown(self):
        self.exam.post('/api/result-withholdings/', {
            'profile_id': self.profile.id, 'semester_id': self.sem1.id, 'reason': 'Fees'}, format='json')
        self.show(self.sem1, self.level4)
        statement = self.portal()['result_statements'][0]
        self.assertTrue(statement['withheld'])
        self.assertEqual(statement['modules'], [])

    def test_several_withheld_at_once(self):
        other, _ = self.student('NIT/31', 'Juma')
        done = self.exam.post('/api/result-withholdings/', {
            'profile_ids': [self.profile.id, other.id], 'semester_id': self.sem1.id, 'reason': 'Fees'},
            format='json')
        self.assertEqual(done.status_code, 201, done.data)
        self.assertEqual(len(done.data['withheld']), 2)
        self.assertEqual(set(ResultWithholding.objects.filter(released_at__isnull=True)
                             .values_list('profile_id', flat=True)), {self.profile.id, other.id})
        # Asked again, nothing doubles; a student who does not exist refuses the lot.
        self.exam.post('/api/result-withholdings/', {
            'profile_ids': [self.profile.id], 'semester_id': self.sem1.id}, format='json')
        self.assertEqual(ResultWithholding.objects.count(), 2)
        refused = self.exam.post('/api/result-withholdings/', {
            'profile_ids': [self.profile.id, 99999], 'semester_id': self.sem1.id}, format='json')
        self.assertEqual(refused.status_code, 400)


class EveryYearTests(PortalBase):
    """The student sees the year they cleared, under that year."""

    def setUp(self):
        super().setUp()
        self.level6 = ClassLevel.objects.create(name='NTA Level 6', order=6)
        # Last year, at level 4: A and B — 3.5, First Class at level 4 (Upper
        # Second by level 6's bands).
        self.old = [Module.objects.create(name=code, code=code, teacher='T', class_level=self.level4,
                                          semester=self.old_sem1, programme=self.pst, credits=3)
                    for code in ('PST04001', 'PST04002')]
        for module in self.old:
            Student.objects.create(nactvet_reg_no='NIT/30', name='Rehema', profile=self.profile, module=module)
        self.upload(self.exam, [('NIT/30', 'PST04001', 'A'), ('NIT/30', 'PST04002', 'B')], semester=self.old_sem1)
        # This year the portal signs in through a level 6 module.
        now = Module.objects.create(name='Ops', code='PST06101', teacher='T', class_level=self.level6,
                                    semester=self.sem1, programme=self.pst, credits=3)
        self.enrollment = Student.objects.create(nactvet_reg_no='NIT/30', name='Rehema', profile=self.profile,
                                                 module=now)
        self.enrollment.set_portal_pin('Portal#2026', require_change=False)
        self.enrollment.save()
        session = self.client.session
        session['student_id'] = self.enrollment.id
        session.save()

    def test_last_year_shows_once_shown_in_order_at_its_own_level(self):
        self.show(self.sem1, self.level4)
        self.assertEqual([s['label'] for s in self.portal()['result_statements']], [self.sem1.label])

        self.show(self.old_sem1, self.level4)
        statements = self.portal()['result_statements']
        self.assertEqual([s['label'] for s in statements], [self.old_sem1.label, self.sem1.label])
        self.assertEqual((statements[0]['gpa'], statements[0]['remark']), (3.5, 'First Class'))

    def test_last_year_counts_nowhere_else(self):
        self.show(self.old_sem1, self.level4)
        context = self.portal()
        self.assertNotIn('PST04001', [module['module_code'] for module in context['modules']])


class ProvisionalNoticeTests(PortalBase):
    def setUp(self):
        super().setUp()
        self.review = SemesterReview.objects.create(
            profile=self.profile, semester=self.old_sem1, class_level=self.level4,
            proposed=SemesterReview.PENDING, confirmed=SemesterReview.PROVISIONAL,
            modules=[{'code': 'PST04001', 'status': 'PASS'}, {'code': 'PST04002', 'status': 'SUPP'}])

    def test_told_at_the_top_and_nothing_more_until_results_are_shown(self):
        notice = self.portal()['provisional_notice']
        self.assertIn('provisional', notice['title'])
        self.assertNotIn('PST04002', notice['body'])
        self.assertContains(self.client.get('/student-dashboard/'), 'id="provisional-notice"')

        self.show(self.old_sem1, self.level4)
        self.assertIn('supplementary in PST04002', self.portal()['provisional_notice']['body'])

    def test_no_notice_once_confirmed(self):
        self.review.confirmed = SemesterReview.CLEAR
        self.review.save(update_fields=['confirmed'])
        self.assertIsNone(self.portal()['provisional_notice'])


class WhatStudentsSeeTodayTests(PortalBase):
    """Migration 0080 keeps shown what students could already see: this
    year's published results — not last year's, which the portal never showed."""

    def test_this_year_stays_shown_and_earlier_years_wait(self):
        old = Module.objects.create(name='Old', code='PST04001', teacher='T', class_level=self.level4,
                                    semester=self.old_sem1, programme=self.pst, credits=3)
        Student.objects.create(nactvet_reg_no='NIT/30', name='Rehema', profile=self.profile, module=old)
        self.upload(self.exam, [('NIT/30', 'PST04001', 'A')], semester=self.old_sem1)

        import_module('attendance.migrations.0080_result_release').keep_what_is_shown(apps, None)
        self.assertEqual(set(ResultRelease.objects.filter(visible=True).values_list('semester_id', 'class_level_id')),
                         {(self.sem1.id, self.level4.id)})
