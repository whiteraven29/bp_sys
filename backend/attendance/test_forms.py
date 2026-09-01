"""Evaluation forms: who may answer, what an answer may be, and what it adds up to.

The college's evaluations were paper, tallied by hand. The point of holding them
as data is that the tally stops being the only thing anyone sees — so these
tests care as much about the summary and the export as about the submission.
"""

from datetime import date, timedelta
from io import BytesIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client, TestCase
from rest_framework.test import APIClient

from . import evaluations
from .serializers import FormResponseSerializer
from .models import (
    AcademicYear, ClassLevel, Form, FormAnswer, FormQuestion, FormResponse,
    FormSection, FormSubmissionReceipt, Module, Semester, Student, TeacherProfile,
)

User = get_user_model()


class FormTestBase(TestCase):
    def setUp(self):
        self.year = AcademicYear.objects.create(name='2026/2027', is_active=True)
        self.sem = Semester.objects.create(academic_year=self.year, number=1, is_active=True)
        self.level = ClassLevel.objects.create(name='NTA Level 4', order=4)
        self.module = Module.objects.create(name='Pharmaceutics', code='PHM101', teacher='T',
                                            class_level=self.level, semester=self.sem)
        self.enrollment = Student.objects.create(
            nactvet_reg_no='BPH/2026/001', name='Asha Juma', module=self.module)
        self.enrollment.set_portal_pin('Portal#2026', require_change=False)
        self.enrollment.save()
        from . import finance
        self.asha = finance.profile_for_student(self.enrollment)

        self.admin = User.objects.create_superuser('admin', 'a@b.c', 'pw')
        self.api = APIClient()
        self.api.force_authenticate(self.admin)

        self.portal = Client()
        self.portal.post('/login/', {'identifier': 'BPH/2026/001', 'secret': 'Portal#2026'})

    def _form(self, **kw):
        defaults = {'title': 'Course Evaluation', 'slug': 'course-eval', 'is_active': True}
        defaults.update(kw)
        form = Form.objects.create(**defaults)
        self.section = FormSection.objects.create(form=form, title='Section A', order=0)
        return form

    def _question(self, **kw):
        defaults = {'section': self.section, 'type': FormQuestion.SINGLE_CHOICE,
                    'text': 'Rate the tutor', 'options': ['Excellent', 'Good', 'Poor']}
        defaults.update(kw)
        return FormQuestion.objects.create(**defaults)


class WhatStudentsCanSeeTests(FormTestBase):
    """Only active forms, and only inside their window."""

    def test_a_student_sees_only_active_forms(self):
        self._form(title='Live one', slug='live', is_active=True)
        self._form(title='Draft one', slug='draft', is_active=False)

        listed = self.portal.get('/api/my-forms/').json()
        self.assertEqual([f['slug'] for f in listed], ['live'])

    def test_a_form_is_hidden_before_it_opens_and_after_it_closes(self):
        today = date(2026, 10, 15)
        early = self._form(slug='early', opens_on=today + timedelta(days=3))
        late = self._form(slug='late', closes_on=today - timedelta(days=1))
        now = self._form(slug='now', opens_on=today - timedelta(days=1),
                         closes_on=today + timedelta(days=1))

        self.assertEqual(early.status(today), Form.DRAFT)
        self.assertEqual(late.status(today), Form.CLOSED)
        self.assertEqual(now.status(today), Form.OPEN)
        self.assertEqual([f.slug for f in evaluations.open_forms(today)], ['now'])

    def test_an_inactive_form_cannot_be_opened_or_answered_directly(self):
        form = self._form(slug='draft', is_active=False)
        self._question()
        self.assertEqual(self.portal.get(f'/api/my-forms/{form.slug}/').status_code, 404)

        response = self.portal.post(f'/api/my-forms/{form.slug}/submit/',
                                    {'answers': {}}, content_type='application/json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('not open', response.json()['detail'])

    def test_a_signed_out_visitor_gets_nothing(self):
        self._form()
        self.assertEqual(Client().get('/api/my-forms/').status_code, 403)


class AnsweringTests(FormTestBase):
    def test_answers_are_recorded_against_the_form(self):
        form = self._form()
        rating = self._question(required=True)
        comment = self._question(type=FormQuestion.LONG_TEXT, text='Comments', options=[])

        response = self.portal.post(
            f'/api/my-forms/{form.slug}/submit/',
            {'answers': {str(rating.id): 'Good', str(comment.id): 'The labs were excellent.'}},
            content_type='application/json')
        self.assertEqual(response.status_code, 201, response.content)

        saved = FormResponse.objects.get(form=form)
        self.assertEqual(saved.profile, self.asha)
        self.assertEqual(saved.class_level, self.level)
        self.assertEqual(saved.academic_year, self.year)
        self.assertEqual(
            {a.question_id: a.value for a in saved.answers.all()},
            {rating.id: 'Good', comment.id: 'The labs were excellent.'})

    def test_a_required_question_must_be_answered(self):
        form = self._form()
        self._question(required=True, text='Rate the field practice')

        response = self.portal.post(f'/api/my-forms/{form.slug}/submit/',
                                    {'answers': {}}, content_type='application/json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('must be answered', response.json()['detail'])
        self.assertFalse(FormResponse.objects.exists())

    def test_an_answer_outside_the_choices_is_refused(self):
        form = self._form()
        rating = self._question()

        response = self.portal.post(
            f'/api/my-forms/{form.slug}/submit/',
            {'answers': {str(rating.id): 'Superb'}}, content_type='application/json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('not one of the choices', response.json()['detail'])

    def test_a_malformed_answer_key_is_refused_cleanly(self):
        """A junk key used to surface int()'s own message to the student."""
        form = self._form()
        self._question()
        response = self.portal.post(
            f'/api/my-forms/{form.slug}/submit/', {'answers': {'not-a-number': 'Good'}},
            content_type='application/json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['detail'],
                         'That answer does not belong to this form.')
        self.assertFalse(FormResponse.objects.exists())

    def test_nothing_is_saved_when_one_answer_is_bad(self):
        """A form is never half-recorded."""
        form = self._form()
        good = self._question(text='Rate the tutor')
        bad = self._question(text='Rate the labs')

        self.portal.post(
            f'/api/my-forms/{form.slug}/submit/',
            {'answers': {str(good.id): 'Good', str(bad.id): 'Nonsense'}},
            content_type='application/json')
        self.assertFalse(FormResponse.objects.exists())
        self.assertFalse(FormAnswer.objects.exists())

    def test_a_student_answers_once(self):
        form = self._form()
        rating = self._question()
        payload = {'answers': {str(rating.id): 'Good'}}

        first = self.portal.post(f'/api/my-forms/{form.slug}/submit/', payload,
                                 content_type='application/json')
        self.assertEqual(first.status_code, 201)
        second = self.portal.post(f'/api/my-forms/{form.slug}/submit/', payload,
                                  content_type='application/json')
        self.assertEqual(second.status_code, 400)
        self.assertIn('already answered', second.json()['detail'])
        self.assertEqual(FormResponse.objects.count(), 1)

    def test_a_form_can_allow_more_than_one_response(self):
        """One response per tutor, so the tutor evaluation must repeat."""
        form = self._form(allow_multiple=True)
        rating = self._question()
        payload = {'answers': {str(rating.id): 'Good'}}

        for _ in range(3):
            self.assertEqual(
                self.portal.post(f'/api/my-forms/{form.slug}/submit/', payload,
                                 content_type='application/json').status_code, 201)
        self.assertEqual(FormResponse.objects.count(), 3)

    def test_the_form_lists_what_i_have_already_answered(self):
        form = self._form()
        rating = self._question()
        listed = self.portal.get('/api/my-forms/').json()[0]
        self.assertFalse(listed['answered'])
        self.assertTrue(listed['can_answer'])

        self.portal.post(f'/api/my-forms/{form.slug}/submit/',
                         {'answers': {str(rating.id): 'Good'}},
                         content_type='application/json')
        listed = self.portal.get('/api/my-forms/').json()[0]
        self.assertTrue(listed['answered'])
        self.assertFalse(listed['can_answer'])


class AnonymityTests(FormTestBase):
    """A student will not say a tutor was unprepared with their name on it."""

    def test_an_anonymous_response_carries_no_link_to_the_student(self):
        form = self._form(is_anonymous=True)
        rating = self._question()

        self.portal.post(f'/api/my-forms/{form.slug}/submit/',
                         {'answers': {str(rating.id): 'Poor'}},
                         content_type='application/json')

        saved = FormResponse.objects.get(form=form)
        self.assertIsNone(saved.profile)
        # ...and nothing the admin can read off it leads back to her either.
        self.assertNotIn(self.asha.nactvet_reg_no,
                         str(FormResponseSerializer(saved).data))

    def test_an_anonymous_form_still_knows_who_has_answered(self):
        form = self._form(is_anonymous=True)
        rating = self._question()
        self.portal.post(f'/api/my-forms/{form.slug}/submit/',
                         {'answers': {str(rating.id): 'Poor'}},
                         content_type='application/json')

        receipt = FormSubmissionReceipt.objects.get(form=form)
        self.assertEqual(receipt.profile, self.asha)
        # Which is how a second attempt is refused without de-anonymising anyone.
        again = self.portal.post(f'/api/my-forms/{form.slug}/submit/',
                                 {'answers': {str(rating.id): 'Good'}},
                                 content_type='application/json')
        self.assertEqual(again.status_code, 400)

    def test_the_admin_can_chase_non_responders_without_reading_names_off_answers(self):
        form = self._form(is_anonymous=True)
        rating = self._question()
        self.portal.post(f'/api/my-forms/{form.slug}/submit/',
                         {'answers': {str(rating.id): 'Poor'}},
                         content_type='application/json')

        listed = self.api.get(f'/api/forms/{form.id}/who-responded/').data
        self.assertEqual(listed['count'], 1)
        self.assertEqual(listed['answered'][0]['reg_no'], 'BPH/2026/001')

        # The responses themselves name nobody.
        responses = self.api.get(f'/api/forms/{form.id}/responses/').data
        self.assertEqual(responses[0]['student_name'], 'Anonymous')
        self.assertEqual(responses[0]['student_reg_no'], '')


class RatingTableTests(FormTestBase):
    ROWS = ['Water facility', 'Electricity facility', 'Safety and Security']

    def _matrix_form(self):
        form = self._form(slug='hostel')
        question = self._question(
            type=FormQuestion.MATRIX, text='Hostel facilities',
            options=['1', '2', '3', '4', '5'], rows=self.ROWS)
        return form, question

    def test_a_rating_table_is_stored_row_by_row(self):
        form, question = self._matrix_form()
        self.portal.post(
            f'/api/my-forms/{form.slug}/submit/',
            {'answers': {str(question.id): {'Water facility': '2',
                                            'Electricity facility': '4'}}},
            content_type='application/json')

        answer = FormAnswer.objects.get(question=question)
        self.assertEqual(answer.value, {'Water facility': '2', 'Electricity facility': '4'})

    def test_a_rating_outside_the_scale_or_an_unknown_row_is_refused(self):
        form, question = self._matrix_form()
        for bad in ({'Water facility': '9'}, {'Swimming pool': '3'}):
            response = self.portal.post(
                f'/api/my-forms/{form.slug}/submit/', {'answers': {str(question.id): bad}},
                content_type='application/json')
            self.assertEqual(response.status_code, 400, bad)
        self.assertFalse(FormResponse.objects.exists())

    def test_a_table_of_text_drops_the_rows_nobody_filled_in(self):
        form = self._form(slug='grid')
        question = self._question(
            type=FormQuestion.GRID_TEXT, text='Modules with poor materials',
            options=[], columns=['Module name', 'What was wrong'], max_rows=4)

        self.portal.post(
            f'/api/my-forms/{form.slug}/submit/',
            {'answers': {str(question.id): [['PHM101', 'No notes'], ['', ''], ['ANA101', '']]}},
            content_type='application/json')

        answer = FormAnswer.objects.get(question=question)
        self.assertEqual(answer.value, [['PHM101', 'No notes'], ['ANA101', '']])


class SummaryTests(FormTestBase):
    def _answer(self, reg_no, question, value):
        enrollment = Student.objects.create(
            nactvet_reg_no=reg_no, name=f'Student {reg_no}', module=self.module)
        enrollment.set_portal_pin('Portal#2026', require_change=False)
        enrollment.save()
        from . import finance
        profile = finance.profile_for_student(enrollment)
        evaluations.submit(question.section.form, {question.id: value}, profile=profile,
                           class_level=self.level, academic_year=self.year)

    def test_a_choice_question_is_counted_per_option(self):
        form = self._form()
        question = self._question(options=['Excellent', 'Good', 'Poor'])
        for index, value in enumerate(['Good', 'Good', 'Poor']):
            self._answer(f'BPH/S/{index}', question, value)

        summary = evaluations.summarise(form)
        self.assertEqual(summary['responses'], 3)
        counts = summary['questions'][0]['counts']
        self.assertEqual(counts, [{'option': 'Excellent', 'count': 0},
                                  {'option': 'Good', 'count': 2},
                                  {'option': 'Poor', 'count': 1}])
        # Words do not average — only a numeric scale does.
        self.assertIsNone(summary['questions'][0]['mean'])

    def test_a_numeric_scale_averages(self):
        form = self._form()
        question = self._question(options=['1', '2', '3', '4', '5'])
        for index, value in enumerate(['5', '4', '3']):
            self._answer(f'BPH/N/{index}', question, value)

        self.assertEqual(evaluations.summarise(form)['questions'][0]['mean'], 4.0)

    def test_a_rating_table_averages_each_row_and_overall(self):
        form = self._form(slug='hostel')
        question = self._question(type=FormQuestion.MATRIX, text='Hostel facilities',
                                  options=['1', '2', '3', '4', '5'],
                                  rows=['Water facility', 'Electricity facility'])
        self._answer('BPH/M/1', question, {'Water facility': '2', 'Electricity facility': '4'})
        self._answer('BPH/M/2', question, {'Water facility': '4', 'Electricity facility': '4'})

        summary = evaluations.summarise(form)['questions'][0]
        self.assertEqual(summary['kind'], 'matrix')
        rows = {row['row']: row for row in summary['rows']}
        self.assertEqual(rows['Water facility']['mean'], 3.0)
        self.assertEqual(rows['Electricity facility']['mean'], 4.0)
        self.assertEqual(summary['mean'], 3.5)

    def test_free_text_answers_are_listed_rather_than_counted(self):
        form = self._form()
        question = self._question(type=FormQuestion.LONG_TEXT, text='Comments', options=[])
        self._answer('BPH/T/1', question, 'More practicals please.')

        summary = evaluations.summarise(form)['questions'][0]
        self.assertEqual(summary['kind'], 'text')
        self.assertEqual(summary['responses'], ['More practicals please.'])

    def test_the_summary_endpoint_serves_it(self):
        form = self._form()
        question = self._question()
        self._answer('BPH/E/1', question, 'Good')

        data = self.api.get(f'/api/forms/{form.id}/summary/').data
        self.assertEqual(data['responses'], 1)
        self.assertEqual(data['questions'][0]['text'], question.text)


class ExportTests(FormTestBase):
    def test_the_workbook_has_the_answers_and_the_tally(self):
        form = self._form()
        rating = self._question(text='Rate the tutor')
        table = self._question(type=FormQuestion.MATRIX, text='Hostel facilities',
                               options=['1', '2', '3', '4', '5'],
                               rows=['Water facility', 'Electricity facility'])
        evaluations.submit(form, {rating.id: 'Good',
                                  table.id: {'Water facility': '2'}},
                           profile=self.asha, class_level=self.level, academic_year=self.year)

        workbook = evaluations.export_workbook(form)
        self.assertEqual(workbook.sheetnames, ['Responses', 'Summary'])

        sheet = workbook['Responses']
        header = [cell.value for cell in sheet[1]]
        self.assertIn('Student', header)
        self.assertIn('Rate the tutor', header)
        # A rating table takes one column per row rated.
        self.assertIn('Hostel facilities — Water facility', header)
        self.assertIn('Hostel facilities — Electricity facility', header)

        row = [cell.value for cell in sheet[2]]
        self.assertIn('Asha Juma', row)
        self.assertIn('Good', row)
        self.assertIn('2', row)

    def test_an_anonymous_export_has_no_name_columns(self):
        form = self._form(is_anonymous=True)
        rating = self._question()
        evaluations.submit(form, {rating.id: 'Poor'}, profile=self.asha)

        sheet = evaluations.export_workbook(form)['Responses']
        header = [cell.value for cell in sheet[1]]
        self.assertNotIn('Student', header)
        self.assertNotIn('Registration no.', header)
        self.assertNotIn('Asha Juma', [cell.value for cell in sheet[2]])

    def test_the_download_endpoint_serves_a_spreadsheet(self):
        form = self._form()
        self._question()
        response = self.api.get(f'/api/forms/{form.id}/export/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('spreadsheetml', response['Content-Type'])
        self.assertIn(f'{form.slug}_responses.xlsx', response['Content-Disposition'])

        from openpyxl import load_workbook
        workbook = load_workbook(BytesIO(response.content))
        self.assertEqual(workbook.sheetnames, ['Responses', 'Summary'])


class AdminAccessTests(FormTestBase):
    def test_only_the_admin_can_publish_a_form(self):
        tutor = User.objects.create_user('tutor', password='pw')
        TeacherProfile.objects.create(user=tutor, full_name='Tutor')
        api = APIClient()
        api.force_authenticate(tutor)

        self.assertEqual(api.post('/api/forms/', {'title': 'Mine'}, format='json').status_code, 403)
        # A tutor may still read them.
        self.assertEqual(api.get('/api/forms/').status_code, 200)

    def test_a_form_is_created_with_a_slug_of_its_own(self):
        first = self.api.post('/api/forms/', {'title': 'Course Evaluation'}, format='json')
        second = self.api.post('/api/forms/', {'title': 'Course Evaluation'}, format='json')
        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(first.data['slug'], 'course-evaluation')
        self.assertEqual(second.data['slug'], 'course-evaluation-2')

    def test_a_closing_date_before_the_opening_date_is_refused(self):
        response = self.api.post('/api/forms/', {
            'title': 'Backwards', 'opens_on': '2026-10-10', 'closes_on': '2026-10-01',
        }, format='json')
        self.assertEqual(response.status_code, 400)

    def test_a_question_that_cannot_be_answered_is_refused(self):
        self._form()
        for payload in (
            {'section': self.section.id, 'text': 'Pick one', 'type': FormQuestion.SINGLE_CHOICE},
            {'section': self.section.id, 'text': 'Rate', 'type': FormQuestion.MATRIX,
             'options': ['1', '2']},
        ):
            response = self.api.post('/api/form-questions/', payload, format='json')
            self.assertEqual(response.status_code, 400, payload)

    def test_a_question_with_answers_cannot_be_deleted(self):
        form = self._form()
        rating = self._question()
        evaluations.submit(form, {rating.id: 'Good'}, profile=self.asha)

        response = self.api.delete(f'/api/form-questions/{rating.id}/')
        self.assertEqual(response.status_code, 403)


class SeededFormsTests(TestCase):
    """The college's four real forms, loaded from its own documents."""

    def test_the_colleges_forms_load_and_match_the_paper(self):
        call_command('seed_evaluation_forms', verbosity=0)
        by_slug = {form.slug: form for form in Form.objects.all()}
        self.assertEqual(sorted(by_slug), [
            'course-evaluation', 'hostel-evaluation', 'mentorship-evaluation',
            'practicum-letter-request', 'student-performance-evaluation',
            'tracer-study', 'tutor-evaluation'])

        # Anything evaluating a member of staff is anonymous.
        self.assertTrue(by_slug['tutor-evaluation'].is_anonymous)
        self.assertTrue(by_slug['hostel-evaluation'].is_anonymous)
        self.assertFalse(by_slug['course-evaluation'].is_anonymous)

        # They arrive unpublished, so the admin checks them over first.
        self.assertFalse(any(form.is_active for form in by_slug.values()))

        hostel = FormQuestion.objects.get(section__form=by_slug['hostel-evaluation'],
                                          type=FormQuestion.MATRIX)
        self.assertEqual(hostel.options, ['1', '2', '3', '4', '5'])
        self.assertEqual(len(hostel.rows), 12)
        self.assertIn('Patron/Matron services', hostel.rows)

        tutor = FormQuestion.objects.get(section__form=by_slug['tutor-evaluation'],
                                         type=FormQuestion.MATRIX)
        self.assertEqual(len(tutor.rows), 15)

        offices = FormQuestion.objects.filter(
            section__form=by_slug['course-evaluation'], type=FormQuestion.MATRIX).first()
        self.assertEqual(len(offices.rows), 15)

    def test_the_mentorship_form_matches_its_document(self):
        call_command('seed_evaluation_forms', verbosity=0)
        form = Form.objects.get(slug='mentorship-evaluation')
        # The paper marks the name Optional — the college asking people to
        # speak freely — so the form is anonymous rather than nearly so.
        self.assertTrue(form.is_anonymous)
        self.assertEqual(form.audience, Form.STUDENT)
        self.assertEqual(form.sections.count(), 5)

        outcomes = FormQuestion.objects.get(section__form=form, type=FormQuestion.MATRIX)
        self.assertEqual(outcomes.options,
                         ['Strongly Disagree', 'Disagree', 'Agree', 'Strongly Agree'])
        self.assertEqual(len(outcomes.rows), 4)

        role = FormQuestion.objects.get(section__form=form, text='Role')
        self.assertEqual(role.options, ['Mentor', 'Mentee'])

    def test_the_performance_evaluation_is_filled_in_by_staff_not_students(self):
        """A mentor assesses a student with it. If it reached the student's own
        list they would be grading themselves."""
        call_command('seed_evaluation_forms', activate=True, verbosity=0)
        form = Form.objects.get(slug='student-performance-evaluation')
        self.assertEqual(form.audience, Form.STAFF)
        self.assertTrue(form.allow_multiple)     # one per student assessed

        self.assertNotIn(form, evaluations.open_forms())
        self.assertIn(form, evaluations.open_forms(audience=Form.STAFF))

        # Its five rating tables use the paper's Always → N/A scale.
        tables = FormQuestion.objects.filter(section__form=form, type=FormQuestion.MATRIX)
        self.assertEqual(tables.count(), 5)
        for table in tables:
            self.assertEqual(table.options,
                             ['Always', 'Sometimes', 'Seldom', 'Never', 'N/A'])
        self.assertEqual(sum(len(t.rows) for t in tables), 28)

    def test_the_practicum_request_captures_the_students_half_of_the_form(self):
        call_command('seed_evaluation_forms', verbosity=0)
        form = Form.objects.get(slug='practicum-letter-request')
        self.assertEqual(form.audience, Form.STUDENT)
        self.assertFalse(form.is_anonymous)      # the office must know who asked
        self.assertTrue(form.allow_multiple)

        # Parts A to C only — D and E are the college's approval chain.
        self.assertEqual([s.title.split(':')[0] for s in form.sections.all()],
                         ['Part A', 'Part B', 'Part C'])
        self.assertIn('completed by the college after you submit', form.intro)

        declaration = FormQuestion.objects.get(section__form=form, options=['I agree'])
        self.assertTrue(declaration.required)

    def test_re_running_it_does_not_disturb_answered_forms(self):
        call_command('seed_evaluation_forms', verbosity=0)
        form = Form.objects.get(slug='hostel-evaluation')
        question = FormQuestion.objects.filter(section__form=form).first()
        FormAnswer.objects.create(
            response=FormResponse.objects.create(form=form), question=question, value={})

        call_command('seed_evaluation_forms', verbosity=0)
        self.assertTrue(FormQuestion.objects.filter(id=question.id).exists())

    def test_activating_publishes_them(self):
        call_command('seed_evaluation_forms', activate=True, verbosity=0)
        self.assertTrue(all(form.is_active for form in Form.objects.all()))


class AudienceTests(FormTestBase):
    """A staff-completed form must never reach a student."""

    def test_a_staff_form_is_absent_from_the_students_list(self):
        self._form(title='Mentor assessment', slug='mentor-assess', audience=Form.STAFF)
        self._question()
        self.assertEqual(self.portal.get('/api/my-forms/').json(), [])

    def test_a_student_cannot_open_or_submit_a_staff_form_by_its_slug(self):
        form = self._form(slug='mentor-assess', audience=Form.STAFF)
        rating = self._question()

        self.assertEqual(self.portal.get(f'/api/my-forms/{form.slug}/').status_code, 404)
        blocked = self.portal.post(f'/api/my-forms/{form.slug}/submit/',
                                   {'answers': {str(rating.id): 'Good'}},
                                   content_type='application/json')
        self.assertEqual(blocked.status_code, 404)
        self.assertFalse(FormResponse.objects.exists())

    def test_the_admin_still_sees_and_exports_staff_forms(self):
        form = self._form(slug='mentor-assess', audience=Form.STAFF)
        self._question()
        listed = self.api.get('/api/forms/').data
        self.assertIn(form.id, [f['id'] for f in listed])
        self.assertEqual(self.api.get(f'/api/forms/{form.id}/export/').status_code, 200)

    def test_forms_default_to_the_student_portal(self):
        created = self.api.post('/api/forms/', {'title': 'New one'}, format='json')
        self.assertEqual(created.data['audience'], Form.STUDENT)


class DeletingAFormTests(FormTestBase):
    """A form the college has stopped running must be removable — but not by
    accident, because deleting one takes its responses with it."""

    def test_an_unanswered_form_deletes_straight_away(self):
        form = self._form()
        self._question()
        self.assertEqual(self.api.delete(f'/api/forms/{form.id}/').status_code, 204)
        self.assertFalse(Form.objects.filter(id=form.id).exists())
        self.assertFalse(FormQuestion.objects.exists())

    def test_an_answered_form_asks_before_destroying_the_answers(self):
        form = self._form()
        rating = self._question()
        evaluations.submit(form, {rating.id: 'Good'}, profile=self.asha)

        refused = self.api.delete(f'/api/forms/{form.id}/')
        self.assertEqual(refused.status_code, 409)
        self.assertTrue(refused.data['requires_confirmation'])
        self.assertEqual(refused.data['responses'], 1)
        self.assertIn('Download the Excel first', refused.data['detail'])
        self.assertTrue(Form.objects.filter(id=form.id).exists())

    def test_confirming_deletes_the_form_and_its_answers(self):
        form = self._form()
        rating = self._question()
        evaluations.submit(form, {rating.id: 'Good'}, profile=self.asha)

        self.assertEqual(
            self.api.delete(f'/api/forms/{form.id}/?confirm=yes').status_code, 204)
        self.assertFalse(Form.objects.filter(id=form.id).exists())
        self.assertFalse(FormResponse.objects.exists())
        self.assertFalse(FormAnswer.objects.exists())

    def test_the_deletion_is_audited(self):
        form = self._form(title='Old evaluation')
        rating = self._question()
        evaluations.submit(form, {rating.id: 'Good'}, profile=self.asha)
        self.api.delete(f'/api/forms/{form.id}/?confirm=yes')

        from .models import FinanceAuditLog
        entry = FinanceAuditLog.objects.filter(action='form.delete').first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.actor, self.admin)
        self.assertIn('Old evaluation', entry.summary)
        self.assertEqual(entry.before['responses'], 1)

    def test_a_tutor_cannot_delete_a_form(self):
        form = self._form()
        tutor = User.objects.create_user('tutor2', password='pw')
        TeacherProfile.objects.create(user=tutor, full_name='Tutor')
        api = APIClient()
        api.force_authenticate(tutor)
        self.assertEqual(api.delete(f'/api/forms/{form.id}/').status_code, 403)
        self.assertTrue(Form.objects.filter(id=form.id).exists())

    def test_a_section_with_answers_is_protected(self):
        form = self._form()
        rating = self._question()
        evaluations.submit(form, {rating.id: 'Good'}, profile=self.asha)

        refused = self.api.delete(f'/api/form-sections/{self.section.id}/')
        self.assertEqual(refused.status_code, 403)
        self.assertIn('delete the whole form', refused.data['detail'])

    def test_an_unanswered_section_deletes_with_its_questions(self):
        self._form()
        self._question()
        self.assertEqual(
            self.api.delete(f'/api/form-sections/{self.section.id}/').status_code, 204)
        self.assertFalse(FormQuestion.objects.exists())


class StaffFilledFormTests(FormTestBase):
    """The Students' Performance Evaluation is a mentor assessing a student, so
    it needs somewhere to be filled in that is not the student's own portal."""

    def _staff_form(self):
        form = self._form(slug='performance', audience=Form.STAFF, allow_multiple=True)
        question = self._question(type=FormQuestion.MATRIX, text='Leadership',
                                  options=['Always', 'Sometimes', 'Never'],
                                  rows=['Able to cooperate with others'])
        name = self._question(type=FormQuestion.SHORT_TEXT, text='Students Name',
                              options=[], required=True)
        return form, question, name

    def test_the_admin_can_fill_a_staff_form_in(self):
        form, matrix, name = self._staff_form()
        response = self.api.post(f'/api/forms/{form.id}/submit/', {'answers': {
            str(name.id): 'Asha Juma',
            str(matrix.id): {'Able to cooperate with others': 'Always'},
        }}, format='json')
        self.assertEqual(response.status_code, 201, response.data)

        saved = FormResponse.objects.get(form=form)
        self.assertEqual(saved.submitted_by, self.admin)
        self.assertIsNone(saved.profile)
        self.assertEqual(saved.answers.count(), 2)

    def test_a_mentor_can_assess_more_than_one_student(self):
        form, matrix, name = self._staff_form()
        for student in ('Asha Juma', 'Baraka Simon'):
            self.assertEqual(self.api.post(f'/api/forms/{form.id}/submit/', {'answers': {
                str(name.id): student,
                str(matrix.id): {'Able to cooperate with others': 'Always'},
            }}, format='json').status_code, 201)
        self.assertEqual(FormResponse.objects.count(), 2)

    def test_a_required_question_is_enforced_here_too(self):
        form, matrix, _name = self._staff_form()
        refused = self.api.post(f'/api/forms/{form.id}/submit/', {'answers': {
            str(matrix.id): {'Able to cooperate with others': 'Always'},
        }}, format='json')
        self.assertEqual(refused.status_code, 400)
        self.assertIn('must be answered', refused.data['detail'])

    def test_a_student_form_cannot_be_filled_in_from_the_panel(self):
        form = self._form(slug='course', audience=Form.STUDENT)
        rating = self._question()
        refused = self.api.post(f'/api/forms/{form.id}/submit/',
                                {'answers': {str(rating.id): 'Good'}}, format='json')
        self.assertEqual(refused.status_code, 400)
        self.assertIn('answered by students on their portal', refused.data['detail'])

    def test_a_tutor_cannot_fill_a_staff_form_in(self):
        form, matrix, name = self._staff_form()
        tutor = User.objects.create_user('tutor3', password='pw')
        TeacherProfile.objects.create(user=tutor, full_name='Tutor')
        api = APIClient()
        api.force_authenticate(tutor)
        self.assertEqual(api.post(f'/api/forms/{form.id}/submit/',
                                  {'answers': {}}, format='json').status_code, 403)

    def test_the_export_names_who_filled_it_in(self):
        form, matrix, name = self._staff_form()
        self.api.post(f'/api/forms/{form.id}/submit/', {'answers': {
            str(name.id): 'Asha Juma',
            str(matrix.id): {'Able to cooperate with others': 'Always'},
        }}, format='json')

        sheet = evaluations.export_workbook(form)['Responses']
        header = [cell.value for cell in sheet[1]]
        self.assertIn('Filled in by', header)
        self.assertNotIn('Registration no.', header)
        self.assertIn('Asha Juma', [cell.value for cell in sheet[2]])

    def test_an_anonymous_form_never_records_the_member_of_staff(self):
        """Recording who filled an anonymous form in would identify the
        respondent just as surely as a name."""
        form = self._form(slug='anon-staff', audience=Form.STAFF, is_anonymous=True)
        rating = self._question()
        self.api.post(f'/api/forms/{form.id}/submit/',
                      {'answers': {str(rating.id): 'Good'}}, format='json')
        saved = FormResponse.objects.get(form=form)
        self.assertIsNone(saved.submitted_by)
        self.assertIsNone(saved.profile)


class EditingAQuestionTests(FormTestBase):
    def test_a_question_can_be_corrected_after_it_is_answered(self):
        """A typo used to mean deleting and retyping, which is refused once
        anyone has answered."""
        form = self._form()
        rating = self._question(text='Rate the tutar')
        evaluations.submit(form, {rating.id: 'Good'}, profile=self.asha)

        response = self.api.patch(f'/api/form-questions/{rating.id}/', {
            'text': 'Rate the tutor',
            'options': ['Excellent', 'Good', 'Poor'],
            'help_text': 'Think about the whole year',
        }, format='json')
        self.assertEqual(response.status_code, 200, response.data)

        rating.refresh_from_db()
        self.assertEqual(rating.text, 'Rate the tutor')
        self.assertEqual(rating.help_text, 'Think about the whole year')
        # The answer already given is untouched.
        self.assertEqual(FormAnswer.objects.get(question=rating).value, 'Good')

    def test_an_edit_that_would_orphan_an_answer_is_still_checked(self):
        form = self._form()
        rating = self._question()
        refused = self.api.patch(f'/api/form-questions/{rating.id}/',
                                 {'options': []}, format='json')
        self.assertEqual(refused.status_code, 400)


class StudentResultsNavigationTests(TestCase):
    """The results area is two screens: CA for the semester in progress, and
    every published end-of-semester result.

    CA is a running figure, not a result. Once the college advances the
    semester the marks are superseded by the end-of-semester result, so they
    stop being shown — but nothing published is ever lost, because the
    end-of-semester screen keeps it.
    """

    def setUp(self):
        from .models import StudentResult
        self.year = AcademicYear.objects.create(name='2026/2027', is_active=True)
        self.sem1 = Semester.objects.create(academic_year=self.year, number=1, is_active=True)
        self.sem2 = Semester.objects.create(academic_year=self.year, number=2, is_active=False)
        self.level = ClassLevel.objects.create(name='NTA Level 4', order=4)
        self.mod1 = Module.objects.create(name='Pharmaceutics', code='PHM101', teacher='T',
                                          class_level=self.level, semester=self.sem1)
        self.mod2 = Module.objects.create(name='Anatomy', code='ANA101', teacher='T',
                                          class_level=self.level, semester=self.sem2)
        for module in (self.mod1, self.mod2):
            enrollment = Student.objects.create(
                nactvet_reg_no='BPH/2026/001', name='Asha Juma', module=module)
            enrollment.set_portal_pin('Portal#2026', require_change=False)
            enrollment.save()
            StudentResult.objects.create(
                student=enrollment, assign1=30, assign2=30, cat1_theory=60, cat2_theory=60,
                ca_approved=True)
        self.portal = Client()
        self.portal.post('/login/', {'identifier': 'BPH/2026/001', 'secret': 'Portal#2026'})

    def _context(self):
        return self.portal.get('/student-dashboard/').context

    def test_the_results_menu_offers_exactly_two_screens(self):
        body = self.portal.get('/student-dashboard/').content.decode()
        self.assertIn('data-view="ca-results"', body)
        self.assertIn('data-view="final-results"', body)
        # The per-semester CA screens are gone, and What I Owe has moved to
        # Payments where the rest of the money lives.
        self.assertNotIn('data-view="ca-sem1"', body)
        self.assertNotIn('data-view="ca-sem2"', body)
        self.assertIn('id="payments-menu"', body)

    def test_ca_shows_only_the_semester_in_progress(self):
        context = self._context()
        codes = [m['module_code'] for m in context['ca_theory_modules']]
        self.assertEqual(codes, ['PHM101'])          # semester 1 is active
        self.assertTrue(context['has_ca_results'])

    def test_advancing_the_semester_takes_the_old_ca_away(self):
        """Exactly what the college sees after the admin advances."""
        self.assertEqual(len(self._context()['ca_theory_modules']), 1)

        self.sem1.is_active = False
        self.sem1.save(update_fields=['is_active'])
        self.sem2.is_active = True
        self.sem2.save(update_fields=['is_active'])

        context = self._context()
        codes = [m['module_code'] for m in context['ca_theory_modules']]
        self.assertEqual(codes, ['ANA101'])          # semester 2 now
        self.assertNotIn('PHM101', codes)

    def test_ca_empties_when_the_new_semester_has_no_marks_yet(self):
        from .models import StudentResult
        StudentResult.objects.filter(student__module=self.mod2).update(ca_approved=False)
        self.sem1.is_active = False
        self.sem1.save(update_fields=['is_active'])
        self.sem2.is_active = True
        self.sem2.save(update_fields=['is_active'])

        context = self._context()
        self.assertFalse(context['has_ca_results'])
        self.assertEqual(context['ca_theory_modules'], [])
        self.assertContains(self.portal.get('/student-dashboard/'),
                            'No CA results have been published for')

    def test_end_of_semester_keeps_every_published_semester(self):
        from .models import StudentResult
        # Publish both semesters, then advance past semester 1.
        StudentResult.objects.all().update(
            end_theory=70, final_approved=True, ca_approved=True)
        self.sem1.is_active = False
        self.sem1.save(update_fields=['is_active'])
        self.sem2.is_active = True
        self.sem2.save(update_fields=['is_active'])

        context = self._context()
        published = [m['module_code'] for m in context['modules'] if m['has_final_result']]
        self.assertCountEqual(published, ['PHM101', 'ANA101'])
        self.assertEqual(context['published_result_count'], 2)
        # Semester 1's CA is gone, but its result is not.
        self.assertNotIn('PHM101', [m['module_code'] for m in context['ca_theory_modules']])
