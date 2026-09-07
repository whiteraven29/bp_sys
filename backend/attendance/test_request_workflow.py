"""Two desks, and telling people that something needs them.

A request passes through the secretary, who checks it and puts it in front of
the Principal or the Head of Department; they are the ones who say yes or no. It
comes back to the secretary to be acted on. One office doing both is how a
request gets approved by the person who wanted it approved.

And none of it works if nobody notices: a request used to sit in a queue nobody
had a reason to open.
"""

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from rest_framework.test import APIClient

from . import evaluations, finance, notifications
from .models import (
    AcademicYear, ClassLevel, Form, FormQuestion, FormResponse, FormSection,
    HeadOfDepartmentProfile, Module, Notification, PrincipalProfile,
    SecretaryProfile, Semester, Student,
)

User = get_user_model()


class WorkflowTestBase(TestCase):
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
        self.asha = finance.profile_for_student(self.enrollment)

        self.form = Form.objects.create(title='Official Letter Request', slug='letter',
                                        kind=Form.REQUEST, is_active=True, allow_multiple=True)
        section = FormSection.objects.create(form=self.form, title='A', order=0)
        self.question = FormQuestion.objects.create(
            section=section, text='Addressed to', type=FormQuestion.SHORT_TEXT,
            order=0, required=True)

        self.admin = User.objects.create_superuser('admin', 'a@b.c', 'pw')
        self.api = APIClient(); self.api.force_authenticate(self.admin)

        self.secretary = self._staff('secretary', SecretaryProfile, 'Neema Katabazi')
        self.principal = self._staff('principal', PrincipalProfile, 'Asha Mtei')
        self.hod = self._staff('hod', HeadOfDepartmentProfile, 'John Kimaro')

        self.portal = Client()
        self.portal.post('/login/', {'identifier': 'BPH/2026/001', 'secret': 'Portal#2026'})

    def _staff(self, username, model, name):
        user = User.objects.create_user(username, password='pw')
        model.objects.create(user=user, full_name=name)
        user.refresh_from_db()
        api = APIClient(); api.force_authenticate(user)
        user.api = api
        return user

    def _ask(self):
        self.portal.post('/api/my-forms/letter/submit/',
                         {'answers': {str(self.question.id): 'CRDB Bank'}},
                         content_type='application/json')
        return FormResponse.objects.latest('id')


class TheTwoDesksTests(WorkflowTestBase):
    def test_a_new_request_starts_with_the_secretary(self):
        made = self._ask()
        self.assertEqual(made.status, FormResponse.PENDING)
        self.assertEqual(made.get_status_display(), 'With the secretary')

    def test_the_secretary_passes_it_on_and_does_not_decide(self):
        made = self._ask()

        passed = self.secretary.api.post(f'/api/service-requests/{made.id}/forward/',
                                         {'note': 'Details check out.'}, format='json')
        self.assertEqual(passed.status_code, 200, passed.data)
        made.refresh_from_db()
        self.assertEqual(made.status, FormResponse.FORWARDED)
        self.assertEqual(made.forwarded_by, self.secretary)

        refused = self.secretary.api.post(f'/api/service-requests/{made.id}/decide/',
                                          {'status': 'approved'}, format='json')
        self.assertEqual(refused.status_code, 403)

    def test_the_principal_and_the_head_of_department_decide(self):
        for officer, verdict in ((self.principal, 'approved'), (self.hod, 'declined')):
            made = self._ask()
            self.secretary.api.post(f'/api/service-requests/{made.id}/forward/', {}, format='json')
            answered = officer.api.post(f'/api/service-requests/{made.id}/decide/',
                                        {'status': verdict, 'note': 'Noted.'}, format='json')
            self.assertEqual(answered.status_code, 200, (officer.username, answered.data))
            made.refresh_from_db()
            self.assertEqual(made.status, verdict)
            self.assertEqual(made.decided_by, officer)

    def test_a_decision_maker_does_not_do_the_secretarys_processing(self):
        """Sending the document back is the secretary's act, not theirs."""
        made = self._ask()
        self.assertEqual(
            self.principal.api.post(f'/api/service-requests/{made.id}/forward/', {},
                                    format='json').status_code, 403)

    def test_undoing_a_decision_returns_it_to_the_secretary(self):
        made = self._ask()
        self.secretary.api.post(f'/api/service-requests/{made.id}/forward/', {}, format='json')
        self.principal.api.post(f'/api/service-requests/{made.id}/decide/',
                                {'status': 'approved'}, format='json')
        self.principal.api.post(f'/api/service-requests/{made.id}/decide/',
                                {'status': 'pending'}, format='json')

        made.refresh_from_db()
        self.assertEqual(made.status, FormResponse.PENDING)
        self.assertIsNone(made.decided_by)

    def test_an_answered_request_cannot_be_passed_on_again(self):
        made = self._ask()
        evaluations.decide(made, status=FormResponse.APPROVED, by=self.principal)
        refused = self.secretary.api.post(f'/api/service-requests/{made.id}/forward/',
                                          {}, format='json')
        self.assertEqual(refused.status_code, 400)
        self.assertIn('already been answered', refused.data['detail'])

    def test_the_admin_covers_both_desks(self):
        """Somebody has to be able to act when neither office is here."""
        made = self._ask()
        self.assertEqual(self.api.post(f'/api/service-requests/{made.id}/forward/',
                                       {}, format='json').status_code, 200)
        self.assertEqual(self.api.post(f'/api/service-requests/{made.id}/decide/',
                                       {'status': 'approved'}, format='json').status_code, 200)

    def test_the_queue_can_be_filtered_to_what_is_waiting_on_a_decision(self):
        made = self._ask()
        self.secretary.api.post(f'/api/service-requests/{made.id}/forward/', {}, format='json')
        self._ask()

        waiting = self.principal.api.get('/api/service-requests/?status=forwarded').data
        self.assertEqual([row['id'] for row in waiting], [made.id])
        self.assertEqual(waiting[0]['status_label'],
                         'With the Principal / Head of Department')


class BeingToldTests(WorkflowTestBase):
    def _titles(self, user=None, profile=None):
        return [n.title for n in notifications.recent_for(user=user, profile=profile)]

    def test_the_secretary_hears_when_a_student_asks_for_something(self):
        self._ask()
        self.assertIn('Official Letter Request', self._titles(user=self.secretary))
        self.assertEqual(notifications.unread_for(user=self.secretary).count(), 1)
        # Not the deciders — it is not theirs yet.
        self.assertEqual(self._titles(user=self.principal), [])

    def test_the_deciders_hear_when_it_is_passed_to_them(self):
        made = self._ask()
        self.secretary.api.post(f'/api/service-requests/{made.id}/forward/',
                                {'note': 'Details check out.'}, format='json')

        for officer in (self.principal, self.hod):
            titles = self._titles(user=officer)
            self.assertEqual(titles, ['Decision needed: Official Letter Request'])
            body = notifications.recent_for(user=officer)[0].body
            self.assertIn('Asha Juma', body)
            self.assertIn('Details check out.', body)

    def test_the_answer_comes_back_to_the_secretary_and_to_the_student(self):
        made = self._ask()
        self.secretary.api.post(f'/api/service-requests/{made.id}/forward/', {}, format='json')
        self.principal.api.post(f'/api/service-requests/{made.id}/decide/',
                                {'status': 'approved', 'note': 'Collect on Tuesday.'},
                                format='json')

        told = notifications.recent_for(user=self.secretary)[0]
        self.assertIn('approved', told.title)
        self.assertIn('Prepare the document', told.body)

        student = notifications.recent_for(profile=self.asha)[0]
        self.assertIn('approved', student.title)
        self.assertEqual(student.body, 'Collect on Tuesday.')

    def test_a_declined_request_does_not_ask_for_a_document(self):
        made = self._ask()
        self.principal.api.post(f'/api/service-requests/{made.id}/decide/',
                                {'status': 'declined', 'note': 'No.'}, format='json')
        self.assertIn('Nothing further to prepare',
                      notifications.recent_for(user=self.secretary)[0].body)

    def test_a_notification_is_one_row_per_person(self):
        """A shared row with a read flag would let the first reader clear it
        for everybody."""
        made = self._ask()
        self.secretary.api.post(f'/api/service-requests/{made.id}/forward/', {}, format='json')

        self.assertEqual(Notification.objects.filter(
            kind=Notification.REQUEST_FORWARDED).count(), 2)
        notifications.mark_read(user=self.principal)
        self.assertEqual(notifications.unread_for(user=self.principal).count(), 0)
        self.assertEqual(notifications.unread_for(user=self.hod).count(), 1)

    def test_the_bell_reads_and_clears_over_the_api(self):
        self._ask()
        listed = self.secretary.api.get('/api/notifications/').data
        self.assertEqual(listed['unread'], 1)
        self.assertEqual(listed['items'][0]['link'], 'forms:requests')

        cleared = self.secretary.api.post('/api/notifications/read/', {}, format='json')
        self.assertEqual(cleared.data['unread'], 0)

    def test_the_student_reads_theirs_on_the_portal_session(self):
        made = self._ask()
        evaluations.decide(made, status=FormResponse.APPROVED, note='Ready.',
                           by=self.principal)
        notifications.request_decided(made)

        listed = self.portal.get('/api/my-notifications/').json()
        self.assertEqual(listed['unread'], 1)
        self.assertEqual(listed['items'][0]['link'], 'my-requests')

        self.portal.post('/api/my-notifications/', {}, content_type='application/json')
        self.assertEqual(self.portal.get('/api/my-notifications/').json()['unread'], 0)

    def test_one_student_never_sees_another_students_notifications(self):
        made = self._ask()
        notifications.request_decided(made)

        other = Student.objects.create(nactvet_reg_no='BPH/2026/999', name='Someone Else',
                                       module=self.module)
        other.set_portal_pin('Portal#2026', require_change=False)
        other.save()
        intruder = Client()
        intruder.post('/login/', {'identifier': 'BPH/2026/999', 'secret': 'Portal#2026'})
        self.assertEqual(intruder.get('/api/my-notifications/').json()['unread'], 0)

    def test_nobody_signed_in_reads_nothing(self):
        self._ask()
        self.assertEqual(Client().get('/api/my-notifications/').status_code, 403)

    def test_a_notification_never_breaks_the_thing_it_reports_on(self):
        """Submitting a request must not fail because nobody could be told."""
        Notification.objects.all().delete()
        SecretaryProfile.objects.all().delete()
        made = self._ask()
        self.assertEqual(made.status, FormResponse.PENDING)
