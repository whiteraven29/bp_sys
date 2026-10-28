"""The consent form for students with a supplementary still to sit (said
28 October 2026).

A student whose results say SUPP continues to the next level while they
prepare for the supplementary examination, on the conditions the college's
form sets out. They fill it in on the portal (optional), print it, sign it
and bring it back; the Principal signs and stamps it, and the admission
office records the signed hard copy and the Principal's answer. Nothing is
held while it is missing: the list says who has not filled it in or brought
it back.

The wording is the college's, changed each year (the amounts, the month of
the examination) on its own screen; the student's copy keeps the wording as
they agreed to it.
"""

from django.db import IntegrityError, transaction
from django.utils import timezone

from . import progression
from .models import (
    Application, Semester, SemesterRegistration, SemesterReview, StudentProfile, SuppConsent,
    SuppConsentForm,
)


class ConsentError(ValueError):
    """Something the consent form does not allow."""


#: The college's form of 2025, its years moved on. Edited each year.
DEFAULT_TITLE = ('FOMU YA KURIDHIA KUENDELEA NA NGAZI INAYOFUATA KWA WANAFUNZI WANAOTAKIWA KUFANYA MITIHANI '
                 'YA MARUDIO MWEZI DISEMBA {first}')
DEFAULT_A = """1. Mwanafunzi anayetakiwa kuendelea katika ngazi ya masomo inayofuata, ni yule tu ambaye amefaulu mitihani yake ya mwisho wa muhula wa pili wa mwaka wa masomo {previous}.
2. Mitihani ya marudio inatakiwa kuwa imefanyika ndani ya wiki sita kutoka tarehe ya kutoka kwa matokeo ya awali ya muhula wa pili."""
DEFAULT_B = """1. Kulipa walau kiasi cha laki sita na elfu tano (605,000/=) kama michango mingine ya ngazi inayofuata kwa mujibu wa malipo ya mwaka wa masomo {year} na shilingi za kitanzania laki nne (400,000) kama awamu ya kwanza ya malipo ya ada ya mwaka wa masomo {year}.
2. Malipo ya michango mingine kwa ajili ya shughuli zinazotegemewa kufanyika katika muhula wa kwanza wa mwaka {year} hayatarudishwa endapo mwanafunzi atashindwa kufaulu mtihani ya marudio na kutakiwa kurudia kusoma somo/masomo hayo upya.
3. Michango itakayolipwa kwa ajili ya shughuli za muhula wa pili kama vile mafunzo kwa vitendo (Field Pharmacy Practice) na malipo ya mitihani ya mwisho, inaweza kutumika kama sehemu ya ada wakati mwanafunzi akiwa anarudia kusoma somo/masomo aliyotakiwa kuyarudia baada ya kufeli mitihani. Ubadilishwaji huu wa matumizi ya fedha utafanyika baada ya mwanafunzi kuwasilisha ombi la kimaandishi kwa ofisi ya Mkuu wa Chuo na kupitishwa.
4. Kiasi chochote alicholipa mwanafunzi hakitarejeshwa wala kutumika wakati wa baadae endapo mwanafunzi atafeli na kuachishwa masomo kutokana na kuwa na GPA chini ya 2.
5. Mwanafunzi atakayefaulu mitihani ya marudio itakayofanyika mwezi Disemba {first} atatakiwa kulipa awamu ya pili ya ada sawa na shilingi za kitanzania laki nne (400,000/=) ndani ya siku tano (5) tokea tarehe ambayo matokeo yatakuwa yametolewa rasmi na ofisi ya Mkuu wa Chuo.
6. Mwanafunzi atatakiwa kuhudhuria kikamilifu mafunzo ya ngazi inayofuata na kusaini karatasi ya Mahudhurio ambayo ndiyo yatatumika kama ushahidi kuwa mwanafunzi husika alisoma ngazi iliyofuata. Mwanafunzi atakayekuwa na mahudhurio yasiyoridhisha (chini ya asilimia 90% ya vipindi ambavyo vitakuwa vimefundishwa katika somo lolote lile hatoruhusiwa kuendelea na ngazi hiyo inayofuata hata kama atafaulu mitihani yake ya marudio).
7. Mwanafunzi ambaye atashindwa kuendelea na masomo ya ngazi inayofuata kwa sababu ya kushindwa kufikisha walau asilimia 90% ya mahudhurio ya vipindi vya darasani, hatorudishiwa kiasi cha pesa ambayo atakuwa amekilipa kwa chuo.
8. Mwanafunzi atatakiwa kuhudhuria kikamilifu kwa mahudhurio ya walau asilimia 90% katika mafunzo ya nyongeza (remedial classes) ambazo yataandaliwa na ofisi ya Makamu Mkuu wa Chuo Taaluma ili kuwafundisha wanafunzi wanaotakiwa kufanya mitihani ya marudio inayotegemea kuanza Disemba {first}. Mafunzo haya yana lengo la kuongeza nafasi ya wanafunzi kufaulu. Mwanafunzi atakayekosa kuhudhuria mafunzo ya nyongeza (remedial classes) hata mara moja pasipo kutoa taarifa ya awali hatoruhusiwa tena kuhudhuria mafunzo hayo."""
DEFAULT_C = ('Mimi {name} mwanafunzi wa Chuo cha afya Blue Pharma niliyesajiliwa kwa namba ya NACTVET {reg_no} '
             'ninakiri kuwa nina uelewa wa miongozo ya mitihani iliyopitiwa mwezi Julai 2022 ambayo pia imedokezwa '
             'katika sehemu A hapo juu. Baada ya kuwasiliana na Mfadhili wangu, ninaridhia kuendelea na ngazi ya '
             'masomo inayofuata ambayo ni {level} na kufuata matakwa ya fomu hii yaliyoorodheshwa katika sehemu B '
             'hapo juu.')
DEFAULT_D = ('Mimi ______________________________ Mkuu wa Chuo cha afya Blue Pharma, ninamruhusu/simruhusu* '
             'mwanafunzi tajwa katika Sehemu C hapo juu kuendelea na masomo ya ngazi ya kimasomo inayofuata huku '
             'akijiandaa na mitihani ya marudio inayotegemea kuanza kufanyika mwezi Disemba {first}.\n'
             '*Futa chochote kisichotakiwa.')

PLACEHOLDERS = ('{name}', '{reg_no}', '{level}')


def active_semester():
    return Semester.objects.filter(is_active=True).select_related('academic_year').first()


def form_for(year=None, *, actor=None):
    """The year's form — made from the college's 2025 wording, closed, the
    first time it is asked for, so the office checks the figures first."""
    if year is None:
        active = active_semester()
        if active is None:
            return None
        year = active.academic_year
    form = SuppConsentForm.objects.filter(academic_year=year).first()
    if form is not None:
        return form
    first = year.name.split('/')[0]
    words = {'year': year.name, 'previous': _previous(year.name), 'first': first}
    try:
        # The office's screen asks for the wording and the list at once: the
        # first visit of the year must not make it twice (found 28 October 2026).
        with transaction.atomic():
            return SuppConsentForm.objects.create(
                academic_year=year, title=DEFAULT_TITLE.format(**words), part_a=DEFAULT_A.format(**words),
                part_b=DEFAULT_B.format(**words), part_c=DEFAULT_C, part_d=DEFAULT_D.format(**words),
                updated_by=actor)
    except IntegrityError:
        return SuppConsentForm.objects.get(academic_year=year)


def _previous(name):
    first, second = name.split('/')
    return f'{int(first) - 1}/{int(second) - 1}'


def closing_semester(active=None):
    """The semester whose results the supplementary belongs to: semester 2 of
    last year in semester 1, semester 1 of this year in semester 2."""
    active = active or active_semester()
    if active is None:
        return None
    if active.number == Semester.SEM1:
        return (Semester.objects.select_related('academic_year')
                .filter(academic_year__name=_previous(active.academic_year.name), number=Semester.SEM2).first())
    return Semester.objects.filter(academic_year=active.academic_year, number=Semester.SEM1).first()


def says_supp(review):
    """The results say a supplementary is still to sit, and nothing has been
    settled since."""
    if review is None or review.proposed != SemesterReview.PENDING:
        return False
    if review.confirmed not in ('', SemesterReview.PROVISIONAL):
        return False
    return progression.decide(review.modules or [])['remark'] == progression.SUPP


def supp_reviews(semester=None):
    semester = semester or closing_semester()
    if semester is None:
        return []
    return [review for review in SemesterReview.objects.filter(
                semester=semester, proposed=SemesterReview.PENDING,
                confirmed__in=['', SemesterReview.PROVISIONAL]).select_related('profile', 'class_level')
            if says_supp(review)]


def supp_review_of(profile):
    semester = closing_semester()
    if semester is None:
        return None
    review = SemesterReview.objects.filter(profile=profile, semester=semester).select_related('class_level').first()
    return review if says_supp(review) else None


def next_level(profile, review, year):
    """The level they continue to: this year's registration or application,
    else the one after their review's."""
    registration = (SemesterRegistration.objects.filter(profile=profile, semester__academic_year=year)
                    .exclude(status=SemesterRegistration.CANCELLED).select_related('class_level').first())
    if registration is not None:
        return registration.class_level.name
    application = (Application.objects.filter(profile=profile, semester__academic_year=year)
                   .exclude(state__in=[Application.CANCELLED, Application.REJECTED])
                   .select_related('class_level').first())
    if application is not None:
        return application.class_level.name
    return review.class_level.name if review is not None and review.class_level_id else ''


def filled_in(form, profile, level):
    """The form's parts, Part C naming this student."""
    part_c = form.part_c
    for key, value in (('{name}', profile.name), ('{reg_no}', profile.nactvet_reg_no), ('{level}', level or '____')):
        part_c = part_c.replace(key, value)
    return {'title': form.title, 'part_a': form.part_a, 'part_b': form.part_b, 'part_c': part_c,
            'part_d': form.part_d}


def portal_view(profile):
    """What the student's portal shows: None, or the form to fill in and print."""
    if profile is None:
        return None
    form = form_for()
    if form is None:
        return None
    consent = SuppConsent.objects.filter(form=form, profile=profile).first()
    if consent is not None:
        return {'consent': consent, 'wording': consent.wording, 'level': consent.next_level}
    review = supp_review_of(profile)
    if review is None or not form.is_open:
        return None
    level = next_level(profile, review, form.academic_year)
    return {'consent': None, 'wording': filled_in(form, profile, level), 'level': level}


@transaction.atomic
def agree(profile):
    """The student fills it in on the portal: the soft copy."""
    form = form_for()
    if form is None or not form.is_open:
        raise ConsentError('The consent form is not open.')
    existing = SuppConsent.objects.filter(form=form, profile=profile).first()
    if existing is not None:
        return existing
    review = supp_review_of(profile)
    if review is None:
        raise ConsentError('Your results do not ask for a supplementary: there is nothing to consent to.')
    level = next_level(profile, review, form.academic_year)
    return SuppConsent.objects.create(form=form, profile=profile, review=review, next_level=level,
                                      wording=filled_in(form, profile, level))


@transaction.atomic
def record_paper(consent, *, decision, note='', actor=None):
    """The signed hard copy is back: the Principal's answer on it."""
    if decision not in dict(SuppConsent.DECISION_CHOICES):
        raise ConsentError('Say what the Principal decided on the paper: allowed or not allowed.')
    consent.principal_decision = decision
    consent.paper_note = (note or '').strip()[:300]
    consent.paper_received_at = consent.paper_received_at or timezone.now()
    consent.paper_received_by = actor
    consent.save(update_fields=['principal_decision', 'paper_note', 'paper_received_at', 'paper_received_by'])
    return consent


def office_list(query='', state=''):
    """Every supplementary student this year, and every consent filled in:
    who has not filled it in, who has and owes the signed copy, who brought it."""
    form = form_for()
    if form is None:
        return []
    consents = {c.profile_id: c for c in SuppConsent.objects.filter(form=form).select_related(
        'profile', 'paper_received_by')}
    reviews = {review.profile_id: review for review in supp_reviews()}
    profiles = {p.id: p for p in StudentProfile.objects.filter(id__in=set(consents) | set(reviews))}
    rows = []
    for profile_id, profile in profiles.items():
        consent, review = consents.get(profile_id), reviews.get(profile_id)
        row_state = ('paper' if consent and consent.paper_received_at
                     else 'online' if consent else 'not_filled')
        rows.append({
            'profile_id': profile_id, 'reg_no': profile.nactvet_reg_no, 'name': profile.name,
            'level': consent.next_level if consent else next_level(profile, review, form.academic_year),
            'state': row_state, 'consent_id': consent.id if consent else None,
            'agreed_at': consent.agreed_at if consent else None,
            'paper_received_at': consent.paper_received_at if consent else None,
            'decision': consent.get_principal_decision_display() if consent and consent.principal_decision else '',
            'paper_note': consent.paper_note if consent else '',
            'still_supp': review is not None,
        })
    query = (query or '').strip().lower()
    if query:
        rows = [row for row in rows if query in row['name'].lower() or query in row['reg_no'].lower()]
    if state:
        rows = [row for row in rows if row['state'] == state]
    return sorted(rows, key=lambda row: row['name'])
