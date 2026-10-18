"""The enrolment form's details and the passport photo (said 18 October 2026):

  * address, guarantor / sponsor and the schools before — O-level, A-level,
    college — are taken down by records and read on the application;
  * a year that is not a year is refused, and nothing of that save is kept;
  * the passport photo is set by the student on the portal, or by records or
    the Principal; it is a real JPEG or PNG of at most 512 KB, the old one is
    deleted, and only the student and the record's readers can see it.
"""

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client

from . import admissions
from .models import Application, Student, StudentEducation, StudentProfile
from .test_records_health import RecordsBase

JPEG = b'\xff\xd8\xff\xe0' + b'\x00' * 200
PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 200


def photo(content=JPEG, name='passport.jpg', content_type='image/jpeg'):
    return SimpleUploadedFile(name, content, content_type=content_type)


class EnrolmentFormDetailsTests(RecordsBase):
    def save(self, client=None, **data):
        return (client or self.records).post(f'/api/student-records/{self.profile.id}/', data, format='json')

    def test_records_takes_down_the_address_sponsor_and_schools(self):
        saved = self.save(address='P.O. Box 12, Singida', sponsor_name='Ally Juma', sponsor_phone='0713000111',
                          education=[{'level': 'o_level', 'institution': 'Mwenge Secondary', 'start_year': '2018',
                                      'end_year': '2021', 'index_number': 'S0123/0045/2021'},
                                     {'level': 'a_level', 'institution': ''},
                                     {'level': 'college', 'institution': ''}])
        self.assertEqual(saved.status_code, 200, saved.data)
        self.profile.refresh_from_db()
        self.assertEqual((self.profile.address, self.profile.sponsor_name, self.profile.sponsor_phone),
                         ('P.O. Box 12, Singida', 'Ally Juma', '0713000111'))
        school = StudentEducation.objects.get(profile=self.profile)
        self.assertEqual((school.level, school.institution, school.start_year, school.end_year),
                         ('o_level', 'Mwenge Secondary', 2018, 2021))
        record = self.records.get(f'/api/student-records/{self.profile.id}/').data
        self.assertEqual(record['education'][0]['institution'], 'Mwenge Secondary')

    def test_a_school_left_blank_is_removed(self):
        admissions.record_education(self.profile, level='college', institution='Singida College')
        self.save(education=[{'level': 'college', 'institution': ''}])
        self.assertFalse(StudentEducation.objects.filter(profile=self.profile).exists())

    def test_a_wrong_year_is_refused_and_nothing_of_that_save_is_kept(self):
        refused = self.save(address='Somewhere new',
                            education=[{'level': 'o_level', 'institution': 'Mwenge', 'start_year': '2021',
                                        'end_year': '2018'}])
        self.assertEqual(refused.status_code, 400)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.address, '')
        for year in ('19', 'abcd', '2999'):
            self.assertEqual(self.save(education=[{'level': 'o_level', 'institution': 'Mwenge',
                                                   'start_year': year}]).status_code, 400, year)
        self.assertEqual(self.save(education=[{'level': 'primary', 'institution': 'X'}]).status_code, 400)

    def test_details_left_out_are_left_alone(self):
        self.save(address='P.O. Box 12')
        self.save(phone='0715000000')
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.address, 'P.O. Box 12')

    def test_the_application_carries_them_so_admission_does_not_blank_them(self):
        newcomer = self.applicant('NACTVET/PENDING/077', 'Neema Said')
        application = admissions.open_application(
            profile=newcomer, semester=self.sem1, programme=self.pst,
            class_level=self.level4, kind=Application.NEW, actor=self.officer)
        admissions.record_details(newcomer, address='P.O. Box 12', sponsor_name='Ally Juma',
                                  education=[{'level': 'o_level', 'institution': 'Mwenge Secondary'}])
        details = self.principal.get(f'/api/applications/{application.id}/').data['details']
        self.assertEqual((details['address'], details['sponsor_name'], details['education'][0]['institution']),
                         ('P.O. Box 12', 'Ally Juma', 'Mwenge Secondary'))

    def test_only_records_or_the_principal_write_them(self):
        self.assertEqual(self.save(self.accountant, address='X').status_code, 403)
        self.assertEqual(self.save(self.principal, address='P.O. Box 1').status_code, 200)


class PassportTests(RecordsBase):
    def setUp(self):
        super().setUp()
        enrollment = Student.objects.get(profile=self.profile)
        enrollment.set_portal_pin('Portal#2026', require_change=False)
        enrollment.save()
        self.portal = Client()
        session = self.portal.session
        session['student_id'] = enrollment.id
        session.save()

    def upload(self, client, upload):
        if isinstance(client, Client) and client is self.portal:
            return client.post('/api/my-passport/', {'file': upload})
        return client.post(f'/api/student-records/{self.profile.id}/passport/', {'file': upload}, format='multipart')

    def test_the_student_sets_it_on_the_portal_and_sees_it(self):
        sent = self.upload(self.portal, photo())
        self.assertEqual(sent.status_code, 201, sent.content)
        self.assertTrue(sent.json()['passport_url'].startswith('/api/my-passport/?v='))
        shown = self.portal.get('/api/my-passport/')
        self.assertEqual(shown.status_code, 200)
        self.assertEqual(shown['Content-Type'], 'image/jpeg')
        self.assertEqual(shown['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(b''.join(shown.streaming_content), JPEG)
        page = self.portal.get('/student-dashboard/')
        self.assertContains(page, '/api/my-passport/?v=')

    def test_records_sets_it_and_the_old_one_is_deleted(self):
        self.upload(self.portal, photo())
        self.profile.refresh_from_db()
        old = self.profile.passport.name
        made = self.upload(self.records, photo(PNG, 'p.png', 'image/png'))
        self.assertEqual(made.status_code, 200, made.data)
        self.assertTrue(made.data['passport_url'].startswith(f'/api/student-records/{self.profile.id}/passport/'))
        self.profile.refresh_from_db()
        self.assertNotEqual(self.profile.passport.name, old)
        self.assertFalse(self.profile.passport.storage.exists(old))
        self.assertEqual(self.records.get(f'/api/student-records/{self.profile.id}/passport/')['Content-Type'],
                         'image/png')

    def test_only_a_real_small_picture_is_taken(self):
        disguised = photo(b'<svg onload="alert(1)"></svg>', 'p.jpg')
        self.assertEqual(self.upload(self.portal, disguised).status_code, 400)
        self.assertEqual(self.upload(self.records, photo(b'%PDF-1.4 x', 'p.png', 'image/png')).status_code, 400)
        big = photo(JPEG + b'\x00' * admissions.MAX_PASSPORT_BYTES)
        self.assertEqual(self.upload(self.portal, big).status_code, 400)
        self.assertEqual(self.portal.post('/api/my-passport/', {}).status_code, 400)
        self.profile.refresh_from_db()
        self.assertFalse(self.profile.passport)

    def test_who_may_set_it_and_who_may_see_it(self):
        self.assertEqual(self.upload(self.accountant, photo()).status_code, 403)
        self.assertEqual(self.upload(self.tutor, photo()).status_code, 403)
        self.assertEqual(self.upload(self.principal, photo()).status_code, 200)
        url = f'/api/student-records/{self.profile.id}/passport/'
        self.assertEqual(self.tutor.get(url).status_code, 403)
        self.assertEqual(self.records.get(url).status_code, 200)
        self.assertIn(Client().get(url).status_code, (302, 401, 403))
        self.assertIn(Client().get('/api/my-passport/').status_code, (302, 401, 403))
        self.assertIn(Client().post('/api/my-passport/', {'file': photo()}).status_code, (302, 401, 403))

    def test_another_student_cannot_see_it(self):
        self.upload(self.records, photo())
        other = self.student('NIT/PST/2026/0301', 'Neema Said')
        enrollment = Student.objects.get(profile=other)
        enrollment.set_portal_pin('Portal#2026', require_change=False)
        enrollment.save()
        theirs = Client()
        session = theirs.session
        session['student_id'] = enrollment.id
        session.save()
        self.assertEqual(theirs.get('/api/my-passport/').status_code, 404)
        self.assertIn(theirs.get(f'/api/student-records/{self.profile.id}/passport/').status_code, (302, 401, 403))
        self.assertFalse(StudentProfile.objects.get(pk=other.pk).passport)
