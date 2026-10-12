# The college's new name (12 October 2026): Blue Pharma College of Health
# (BPHACOH) is now Blue Pharma Institute of Science and Technology (BPHIST).
#
# The college profile — the name on every invoice and printout — is renamed
# only while it still holds the old name: one the accountant has since set by
# hand is theirs. Forms follow the seed's own rule: titles, introductions and
# print notes always; sections and questions only on a form nobody has
# answered yet, so a response is never shown beside wording it was not given.
# The CAT exam numbers' college initials (BLPCH, from the old name) become
# BPHIST for a year that has issued no CAT number yet; a year that has keeps
# its initials, so one sitting's numbers never carry two. Bank account names
# are left alone: they are what the bank has registered.

from django.db import migrations, models

OLD, NEW = 'Blue Pharma College of Health', 'Blue Pharma Institute of Science and Technology'
OLD_SHORT, NEW_SHORT = 'BPHACOH', 'BPHIST'
OLD_CAT, NEW_CAT = 'BLPCH', 'BPHIST'


def _renamed(text, old=OLD, new=NEW):
    if not text:
        return text
    return text.replace(old, new).replace(old.upper(), new.upper())


def _renamed_list(items, old=OLD, new=NEW):
    return [_renamed(item, old, new) if isinstance(item, str) else item for item in (items or [])]


def rename(apps, schema_editor, old=OLD, new=NEW, old_short=OLD_SHORT, new_short=NEW_SHORT):
    CollegeProfile = apps.get_model('attendance', 'CollegeProfile')
    for profile in CollegeProfile.objects.all():
        changed = []
        if profile.name == old:
            profile.name = new
            changed.append('name')
        if profile.short_name == old_short:
            profile.short_name = new_short
            changed.append('short_name')
        terms = _renamed(profile.invoice_terms, old, new).replace(f'{old_short} ', f'{new_short} ')
        if terms != profile.invoice_terms:
            profile.invoice_terms = terms
            changed.append('invoice_terms')
        if changed:
            profile.save(update_fields=changed)

    Form = apps.get_model('attendance', 'Form')
    FormSection = apps.get_model('attendance', 'FormSection')
    FormQuestion = apps.get_model('attendance', 'FormQuestion')
    FormResponse = apps.get_model('attendance', 'FormResponse')
    # By id throughout: model instances from a migration's state do not always
    # match the related models they are compared with.
    for form in Form.objects.all():
        changed = [field for field in ('title', 'intro', 'print_note')
                   if _renamed(getattr(form, field), old, new) != getattr(form, field)]
        for field in changed:
            setattr(form, field, _renamed(getattr(form, field), old, new))
        if changed:
            form.save(update_fields=changed)
        if FormResponse.objects.filter(form_id=form.id).exists():
            continue
        for section in FormSection.objects.filter(form_id=form.id):
            fields = [f for f in ('title', 'description') if _renamed(getattr(section, f), old, new) != getattr(section, f)]
            for field in fields:
                setattr(section, field, _renamed(getattr(section, field), old, new))
            if fields:
                section.save(update_fields=fields)
        for question in FormQuestion.objects.filter(section__form_id=form.id):
            fields = []
            for field in ('text', 'help_text'):
                if _renamed(getattr(question, field), old, new) != getattr(question, field):
                    setattr(question, field, _renamed(getattr(question, field), old, new))
                    fields.append(field)
            for field in ('options', 'rows', 'columns'):
                renamed = _renamed_list(getattr(question, field), old, new)
                if renamed != getattr(question, field):
                    setattr(question, field, renamed)
                    fields.append(field)
            if fields:
                question.save(update_fields=fields)


def cat_initials(apps, schema_editor, old=OLD_CAT, new=NEW_CAT):
    CatNumberFormat = apps.get_model('attendance', 'CatNumberFormat')
    CatExamNumber = apps.get_model('attendance', 'CatExamNumber')
    for fmt in CatNumberFormat.objects.filter(college_code=old):
        if not CatExamNumber.objects.filter(semester__academic_year=fmt.academic_year_id).exists():
            fmt.college_code = new
            fmt.save(update_fields=['college_code'])


def rename_back(apps, schema_editor):
    rename(apps, schema_editor, old=NEW, new=OLD, old_short=NEW_SHORT, new_short=OLD_SHORT)
    cat_initials(apps, schema_editor, old=NEW_CAT, new=OLD_CAT)


def rename_all(apps, schema_editor):
    rename(apps, schema_editor)
    cat_initials(apps, schema_editor)


class Migration(migrations.Migration):

    dependencies = [
        ('attendance', '0062_module_register_sessions'),
    ]

    operations = [
        migrations.AlterField(
            model_name='collegeprofile',
            name='name',
            field=models.CharField(default='Blue Pharma Institute of Science and Technology', max_length=200),
        ),
        migrations.AlterField(
            model_name='collegeprofile',
            name='short_name',
            field=models.CharField(default='BPHIST', max_length=60),
        ),
        migrations.AlterField(
            model_name='catnumberformat',
            name='college_code',
            field=models.CharField(default='BPHIST', max_length=20),
        ),
        migrations.RunPython(rename_all, rename_back),
    ]
