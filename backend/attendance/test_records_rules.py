"""Records rules from the first day of use (said 19 October 2026):

  * a next of kin and who pays the fees are compulsory at every records desk —
    a continuing student is held at records without them, a VETA one too; a
    sponsor who pays must be named;
  * every correction to a student's details is written down: what it was,
    what it became, who changed it and why;
  * the VETA admission form's details are kept;
  * VETA courses have their own items, checked at their admission desk —
    missing or unchecked, the student is not admitted.
"""

from .models import (
    AdmissionRequirement, Application, CourseApplication, NextOfKin, RequirementCheck, StudentDetailChange,
    StudentProfile,
)
from .test_admissions import ItemsTests, complete_record
from .test_veta import VetaBase
from . import admissions, veta


class CompulsoryDetailsTests(ItemsTests):
    def test_a_continuing_student_is_held_at_records_without_kin_or_payer(self):
        bare = StudentProfile.objects.create(nactvet_reg_no='NIT/PST/2025/0600', name='No Kin')
        application = admissions.open_application(
            profile=bare, semester=self.sem1, programme=self.pst, class_level=self.level5,
            kind=Application.CONTINUING, actor=self.officer)
        application.state = Application.RECORDS
        application.save(update_fields=['state'])
        with self.assertRaisesMessage(admissions.AdmissionError, 'next of kin, who pays the fees'):
            admissions.complete_step(application, actor=self.officer)
        complete_record(bare)
        admissions.complete_step(application, actor=self.officer)
        self.assertEqual(application.state, Application.ADMISSION)

    def test_a_sponsor_who_pays_is_named(self):
        with self.assertRaisesMessage(admissions.AdmissionError, 'name the sponsor'):
            admissions.record_details(self.student, fee_payer='sponsor', actor=self.officer)
        admissions.record_details(self.student, fee_payer='sponsor', sponsor_name='TAHA Trust', actor=self.officer)
        self.student.refresh_from_db()
        self.assertEqual(self.student.get_fee_payer_display(), 'A sponsor')

    def test_a_student_who_pays_their_own_fees_needs_no_sponsor(self):
        bare = StudentProfile.objects.create(nactvet_reg_no='NIT/PST/2025/0601', name='Pays Himself')
        NextOfKin.objects.create(profile=bare, position=1, name='Mzazi', phone='07', relationship=NextOfKin.MOTHER)
        admissions.record_details(bare, fee_payer='self', actor=self.officer)
        self.assertEqual(admissions.compulsory_missing(bare), [])
        bare.refresh_from_db()
        self.assertEqual(bare.get_fee_payer_display(), 'The student')

    def test_every_correction_is_written_down(self):
        admissions.record_details(self.student, name='Items Studnet', actor=self.officer)
        admissions.record_details(self.student, name='Items Student', reason='Typed wrongly at intake',
                                  actor=self.officer)
        latest = StudentDetailChange.objects.filter(profile=self.student, field='Name').first()
        self.assertEqual((latest.old_value, latest.new_value, latest.reason),
                         ('Items Studnet', 'Items Student', 'Typed wrongly at intake'))
        self.assertEqual(StudentDetailChange.objects.filter(profile=self.student, field='Name').count(), 2)
        # Saving what is already there writes nothing.
        admissions.record_details(self.student, name='Items Student', actor=self.officer)
        self.assertEqual(StudentDetailChange.objects.filter(profile=self.student, field='Name').count(), 2)

    def test_the_record_shows_its_history(self):
        records = self.principal          # records sees a student only at its own desks
        records.post(f'/api/student-records/{self.student.id}/', {'phone': '0711000000', 'reason': 'New line'},
                     format='json')
        record = records.get(f'/api/student-records/{self.student.id}/').data
        self.assertEqual(record['changes'][0]['field'], 'Phone')
        self.assertEqual(record['changes'][0]['reason'], 'New line')


for _name in [n for n in dir(ItemsTests) if n.startswith('test_')]:
    setattr(CompulsoryDetailsTests, _name, None)


class VetaDetailsAndItemsTests(VetaBase):
    def setUp(self):
        super().setUp()
        self.course, self.intake = self.course_with_fees()
        self.form = AdmissionRequirement.objects.create(
            name='Signed declaration form', kind=AdmissionRequirement.CHECK_ONLY,
            applies_to=AdmissionRequirement.FOR_VETA, frequency=AdmissionRequirement.ONCE)
        AdmissionRequirement.objects.create(name='O-level certificate', kind=AdmissionRequirement.CHECK_ONLY,
                                            applies_to=AdmissionRequirement.FOR_PST)

    def test_the_admission_form_details_are_kept(self):
        app = self.apply(self.intake)
        self.admission.post(f'/api/course-applications/{app}/clear/', {}, format='json')   # → records
        saved = self.records.post(f'/api/course-applications/{app}/details/', {
            'gender': 'F', 'phone': '0684233559', 'fee_payer': 'family', 'birth_year': 1999,
            'birth_place': 'Singida', 'religion': 'Mkristo', 'address': 'Unyankindy',
            'education_level': 'Darasa la 7', 'has_chronic_condition': 'no',
            'next_of_kin': [{'name': 'Neema Salmu', 'phone': '0684233559', 'relationship': 'mother',
                             'address': 'Unyankindy'}]}, format='json')
        self.assertEqual(saved.status_code, 200, saved.data)
        profile = CourseApplication.objects.get(pk=app).profile
        self.assertEqual((profile.birth_year, profile.birth_place, profile.religion, profile.education_level,
                          profile.has_chronic_condition), (1999, 'Singida', 'Mkristo', 'Darasa la 7', False))
        self.assertEqual(NextOfKin.objects.get(profile=profile).address, 'Unyankindy')
        self.assertEqual(saved.data['birth_place'], 'Singida')

    def test_a_veta_student_who_pays_themselves_passes_records(self):
        app = self.apply(self.intake)
        self.admission.post(f'/api/course-applications/{app}/clear/', {}, format='json')   # → records
        saved = self.records.post(f'/api/course-applications/{app}/details/', {
            'gender': 'F', 'fee_payer': 'self',
            'next_of_kin': [{'name': 'Baba', 'phone': '07', 'relationship': 'mother'}]}, format='json')
        self.assertEqual(saved.status_code, 200, saved.data)
        self.assertEqual(saved.data['blockers'], [])
        self.assertEqual(saved.data['fee_payer_display'], 'The student')

    def test_veta_items_are_checked_before_admitting(self):
        app_id = self.apply(self.intake)
        app = CourseApplication.objects.get(pk=app_id)
        names = [row['name'] for row in veta.items_for(app)]
        self.assertEqual(names, ['Signed declaration form'])           # the PST item is not asked
        app.state = CourseApplication.ADMISSION
        app.save(update_fields=['state'])
        self.assertIn('Check first: Signed declaration form.', veta.desk_blockers(app))
        marked = self.admission.post(f'/api/course-applications/{app_id}/item/',
                                     {'requirement_id': self.form.id, 'status': 'missing'}, format='json')
        self.assertEqual(marked.status_code, 200, marked.data)
        self.assertIn('On hold — missing: Signed declaration form.', veta.desk_blockers(app))
        self.admission.post(f'/api/course-applications/{app_id}/item/',
                            {'requirement_id': self.form.id, 'status': 'has_it'}, format='json')
        self.assertFalse([b for b in veta.desk_blockers(app) if 'declaration' in b])
        self.assertEqual(RequirementCheck.objects.get(course_application=app).status, 'has_it')

    def test_items_are_not_checked_at_another_desk(self):
        app_id = self.apply(self.intake)
        self.admission.post(f'/api/course-applications/{app_id}/clear/', {}, format='json')   # → records
        refused = self.records.post(f'/api/course-applications/{app_id}/item/',
                                    {'requirement_id': self.form.id, 'status': 'has_it'}, format='json')
        self.assertEqual(refused.status_code, 403)


for _name in [n for n in dir(VetaBase) if n.startswith('test_')]:
    setattr(VetaDetailsAndItemsTests, _name, None)


class RelationshipTests(ItemsTests):
    """Said 21 October 2026: mother or father, sister or brother, husband or
    wife — "parent", "sibling" and "spouse" stay readable on records already
    taken, are not offered again, and are asked of records to correct."""

    def setUp(self):
        super().setUp()
        from .models import NextOfKin, StudentProfile
        self.NextOfKin = NextOfKin
        self.profile = complete_record(StudentProfile.objects.create(nactvet_reg_no='NS0700/0001/2025', name='Old Record'))
        self.profile.next_of_kin.all().delete()
        NextOfKin.objects.create(profile=self.profile, position=1, name='Mama', phone='0713', relationship=NextOfKin.PARENT)
        NextOfKin.objects.create(profile=self.profile, position=2, name='Kaka', phone='0714', relationship=NextOfKin.SIBLING)

    def kin(self, first, second):
        return [{'name': 'Mama', 'phone': '0713', 'relationship': first},
                {'name': 'Kaka', 'phone': '0714', 'relationship': second}]

    def test_the_new_ones_are_offered_and_the_old_are_not(self):
        offered = dict(self.NextOfKin.OFFERED)
        for value in ('mother', 'father', 'sister', 'brother', 'husband', 'wife', 'guardian'):
            self.assertIn(value, offered)
        for value in ('parent', 'sibling', 'spouse'):
            self.assertNotIn(value, offered)

    def test_an_old_record_still_reads_and_records_is_asked_to_correct_it(self):
        self.assertEqual(self.profile.next_of_kin.get(position=1).get_relationship_display(), 'Parent')
        missing = admissions.missing_details(self.profile)
        self.assertIn('next of kin 1: Parent → Mother or Father', missing)
        self.assertIn('next of kin 2: Sibling → Sister or Brother', missing)
        # Saving other details leaves an old value as it is…
        admissions.record_details(self.profile, phone='0712999999', next_of_kin=self.kin('parent', 'sibling'),
                                  actor=self.officer)
        # …and correcting it clears the flag.
        admissions.record_details(self.profile, next_of_kin=self.kin('mother', 'brother'), actor=self.officer)
        self.assertFalse([m for m in admissions.missing_details(self.profile) if 'next of kin' in m])

    def test_an_old_value_is_never_written_anew(self):
        admissions.record_details(self.profile, next_of_kin=self.kin('mother', 'brother'), actor=self.officer)
        with self.assertRaises(admissions.AdmissionError) as refused:
            admissions.record_details(self.profile, next_of_kin=self.kin('mother', 'spouse'), actor=self.officer)
        self.assertIn('Husband or Wife', str(refused.exception))
