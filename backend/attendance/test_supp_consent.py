"""Said 28 October 2026: the consent form for students with a supplementary
still to sit — filled in on the portal (optional), printed, signed, brought
back; the Principal signs and stamps it and the admission office records the
signed hard copy. The wording is the college's, changed each year; nothing is
held while the paper is missing.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from rest_framework.test import APIClient

from . import consent
from .models import (
    AcademicYear, ClassLevel, Department, Module, Programme, Semester, SemesterRegistration, SemesterReview,
    Student, StudentProfile, SuppConsent, SuppConsentForm,
)
from .views import set_roles

User = get_user_model()

SUPP_ROWS = [{'code': 'PST04201', 'name': 'Dispensing', 'status': 'PASS', 'grade': 'B', 'points': 3, 'credits': 10,
              'attempt': 'normal'},
             {'code': 'PST04202', 'name': 'Law', 'status': 'SUPP', 'grade': 'C*', 'points': 1, 'credits': 10,
              'attempt': 'normal'},
             {'code': 'PST04203', 'name': 'Anatomy', 'status': 'PASS', 'grade': 'A', 'points': 4, 'credits': 10,
              'attempt': 'normal'}]
PASS_ROWS = [dict(row, status='PASS', grade='B', points=3) for row in SUPP_ROWS]


class SuppConsentTests(TestCase):
    def office(self, username, *roles):
        user = User.objects.create_user(username, password='pw')
        set_roles(user, list(roles), full_name=username.title())
        client = APIClient()
        client.force_authenticate(user)
        return user, client

    def setUp(self):
        last = AcademicYear.objects.create(name='2025/2026')
        self.closing = Semester.objects.create(academic_year=last, number=2)
        self.year = AcademicYear.objects.create(name='2026/2027', is_active=True)
        Semester.objects.create(academic_year=self.year, number=1, is_active=True)
        self.level4 = ClassLevel.objects.create(name='NTA Level 4', order=4)
        self.level5 = ClassLevel.objects.create(name='NTA Level 5', order=5)
        pst = Programme.objects.create(department=Department.objects.create(name='Pharmacy', code='PST'),
                                       name='Pharmaceutical Sciences', code='PST')
        self.admission_user, self.admission = self.office('adm', 'admission_officer')
        _, self.principal = self.office('boss', 'principal')
        _, self.dparc = self.office('dparc', 'dparc')
        _, self.accountant = self.office('acc', 'accountant')

        self.profile = StudentProfile.objects.create(nactvet_reg_no='NS0700/0001/2025', name='Zawadi Supp')
        SemesterReview.objects.create(profile=self.profile, semester=self.closing, class_level=self.level4,
                                      programme=pst, gpa=Decimal('2.6'), proposed=SemesterReview.PENDING,
                                      confirmed=SemesterReview.PROVISIONAL, modules=SUPP_ROWS)
        SemesterRegistration.objects.create(profile=self.profile, semester=self.year.semesters.get(number=1),
                                            programme=pst, class_level=self.level5)
        module = Module.objects.create(name='Law', code='PST04202', teacher='T', class_level=self.level4,
                                       semester=self.closing, programme=pst, credits=10)
        enrollment = Student.objects.create(nactvet_reg_no=self.profile.nactvet_reg_no, name=self.profile.name,
                                            profile=self.profile, module=module)
        enrollment.set_portal_pin('Portal#2026', require_change=False)
        enrollment.save()
        self.portal = Client()
        session = self.portal.session
        session['student_id'] = enrollment.id
        session.save()

        # One who passed: never asked.
        self.passed = StudentProfile.objects.create(nactvet_reg_no='NS0701/0001/2025', name='Amani Pass')
        SemesterReview.objects.create(profile=self.passed, semester=self.closing, class_level=self.level4,
                                      programme=pst, proposed=SemesterReview.CLEAR, modules=PASS_ROWS)

    def open_form(self):
        return self.admission.put('/api/supp-consent/form/', {'is_open': True}, format='json')

    def mine(self):
        return self.portal.get('/api/my-supp-consent/')

    def test_the_year_s_wording_starts_closed_from_the_college_s_form(self):
        form = self.admission.get('/api/supp-consent/form/').data
        self.assertFalse(form['is_open'])
        self.assertIn('DISEMBA 2026', form['title'])
        self.assertIn('2025/2026', form['part_a'])
        self.assertIn('{name}', form['part_c'])
        self.assertEqual(self.mine().json(), {'shown': False})                       # closed: the student sees nothing yet

    def test_who_keeps_the_wording(self):
        self.assertEqual(self.dparc.put('/api/supp-consent/form/', {'is_open': True}, format='json').status_code, 403)
        self.assertEqual(self.accountant.get('/api/supp-consent/form/').status_code, 403)
        changed = self.principal.put('/api/supp-consent/form/', {'part_b': '1. Kulipa 700,000/='}, format='json')
        self.assertEqual(changed.data['part_b'], '1. Kulipa 700,000/=')

    def test_a_supp_student_fills_it_in_prints_it_and_the_wording_is_kept(self):
        self.open_form()
        shown = self.mine().json()
        self.assertFalse(shown['filled_in'])
        self.assertIn('Zawadi Supp', shown['wording']['part_c'])
        self.assertIn('NTA Level 5', shown['wording']['part_c'])
        sent = self.portal.post('/api/my-supp-consent/', content_type='application/json')
        self.assertEqual(sent.status_code, 201, sent.content)
        record = SuppConsent.objects.get(profile=self.profile)
        self.assertEqual(record.next_level, 'NTA Level 5')
        # The college changes the wording later: the student's copy keeps what they agreed to.
        self.admission.put('/api/supp-consent/form/', {'part_b': 'new wording'}, format='json')
        record.refresh_from_db()
        self.assertNotEqual(record.wording['part_b'], 'new wording')
        page = self.portal.get(f'/supp-consent/{record.id}/print/')
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Sehemu D')
        self.assertContains(page, 'Muhuri wa ofisi')
        self.assertContains(page, 'NS0700/0001/2025')

    def test_only_a_supplementary_student_is_asked(self):
        self.open_form()
        other = Client()
        enrollment = Student.objects.create(nactvet_reg_no=self.passed.nactvet_reg_no, name=self.passed.name,
                                            profile=self.passed, module=Module.objects.get(code='PST04202'))
        enrollment.set_portal_pin('Portal#2026', require_change=False)
        enrollment.save()
        session = other.session
        session['student_id'] = enrollment.id
        session.save()
        self.assertEqual(other.get('/api/my-supp-consent/').json(), {'shown': False})
        self.assertEqual(other.post('/api/my-supp-consent/', content_type='application/json').status_code, 400)
        record = SuppConsent.objects.create(form=consent.form_for(), profile=self.profile, wording={})
        self.assertEqual(other.get(f'/supp-consent/{record.id}/print/').status_code, 404)   # not theirs

    def test_the_office_list_and_the_signed_copy(self):
        self.open_form()
        rows = self.admission.get('/api/supp-consent/').data
        self.assertEqual([(row['reg_no'], row['state']) for row in rows], [('NS0700/0001/2025', 'not_filled')])
        self.portal.post('/api/my-supp-consent/', content_type='application/json')
        row = self.admission.get('/api/supp-consent/?state=online').data[0]
        self.assertEqual(self.dparc.post(f"/api/supp-consent/{row['consent_id']}/paper/",
                                         {'decision': 'allowed'}, format='json').status_code, 403)
        self.assertEqual(self.admission.post(f"/api/supp-consent/{row['consent_id']}/paper/",
                                             {'decision': 'maybe'}, format='json').status_code, 400)
        done = self.admission.post(f"/api/supp-consent/{row['consent_id']}/paper/",
                                   {'decision': 'allowed', 'note': 'Stamped 30 Oct'}, format='json')
        self.assertEqual(done.status_code, 200)
        back = self.dparc.get('/api/supp-consent/?state=paper').data[0]
        self.assertEqual((back['decision'], back['paper_note']), ('Allowed to continue', 'Stamped 30 Oct'))
        self.assertEqual(self.mine().json()['decision'], 'Allowed to continue')
        self.assertEqual(self.admission.get(f"/supp-consent/{row['consent_id']}/print/").status_code, 302)  # staff via login
        staff = Client()
        staff.force_login(self.admission_user)
        self.assertEqual(staff.get(f"/supp-consent/{row['consent_id']}/print/").status_code, 200)

    def test_nothing_is_held_while_the_paper_is_missing(self):
        self.open_form()
        self.portal.post('/api/my-supp-consent/', content_type='application/json')
        self.assertTrue(SemesterRegistration.objects.filter(profile=self.profile).exists())
        self.assertEqual(SuppConsentForm.objects.count(), 1)
        self.assertEqual(self.portal.get('/student-dashboard/').status_code, 200)

    def test_made_once_when_two_screens_ask_at_the_same_moment(self):
        from unittest import mock
        made = consent.form_for()
        SuppConsentForm.objects.all().delete()
        real_filter = SuppConsentForm.objects.filter

        def seen_nothing_yet(*args, **kwargs):     # the other request made it in between
            class Nothing:
                def first(self):
                    return None
            if not SuppConsentForm.objects.exists():
                SuppConsentForm.objects.create(academic_year=made.academic_year, title='made by the other request')
            return Nothing()

        with mock.patch.object(SuppConsentForm.objects, 'filter', side_effect=seen_nothing_yet):
            again = consent.form_for()
        self.assertEqual(again.title, 'made by the other request')
        self.assertEqual(real_filter(academic_year=made.academic_year).count(), 1)
