"""What the end of a semester does to a student.

Advancing the semester used to flip two flags and nothing else. Every student
stayed where they were: a level 4 student was still level 4 in the new year, a
student who failed a module after their supplementary looked exactly like one
who passed it, and a student who stopped coming simply vanished from anybody's
attention while the college still had to know what they owed it academically.

This module is the missing decision. It reads results — it never writes one —
and turns them into:

    a review     what this semester's results mean for this student
    a standing   where the student is now, and what they come back to
    repeats      the modules they failed after a supplementary, still unpassed

The rules the college gave, in the order they are applied:

  * A module is settled only when its result is approved. A supplementary sat
    in semester 2 for a semester 1 module is marked weeks into the new
    semester, so an unsettled module makes the semester **pending**: the
    student carries on and is billed as normal, and the decision waits for the
    marks. The examination officer may confirm them as **provisional** to let
    the advance proceed.
  * GPA below 2.0 discontinues the student. They return by readmission, into
    the exact semester they failed, and are billed as a new student.
  * GPA of 2.0 or better with a module failed after its supplementary means the
    student repeats **that module only**, and stays at the same level until it
    is passed. This holds at level 6 too.
  * Failing a semester 1 module that way defers semester 2: no semester 2 for
    that student until semester 1 is passed.
  * A clear level 6 student with nothing outstanding has finished, and waits
    for the accountant and the principal to clear them.

Nothing here deletes anything. A repeat sitting is a new enrollment with its
own result; the failed sitting it replaces stays in the record as the history
of what happened, and the repeat row points at both.
"""

import logging

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from . import finance
from .grading import is_level_six
from .models import (
    AcademicYear, ChargeType, ClassLevel, Module, OutstandingRepeat, Semester,
    SemesterRegistration, SemesterReview, StandingChange, Student,
    StudentStanding,
)

logger = logging.getLogger(__name__)

#: A module whose result is not yet final. The student is neither passed nor
#: failed, and the semester cannot be decided on it.
PENDING_STATUSES = {'INCOMPLETE', 'SUPP', 'WITHHELD', 'NULLIFIED'}
#: A module the student has to sit again. FAIL is a module failed outright;
#: REPEAT is one failed after its supplementary.
FAILED_STATUSES = {'FAIL', 'REPEAT'}
#: The GPA the college requires to stay on a programme.
PASS_GPA = 2.0


class ProgressionError(ValueError):
    """A progression step the college's rules do not allow."""


# ── reading results ───────────────────────────────────────────────────────────

def _result_serializer():
    """The result serializer, used only for the mark arithmetic it already owns.

    `grading.result_outcome` takes a serializer because that is where the
    weighting of CA and end-of-semester marks lives. Importing it here rather
    than at module level keeps serializers.py free to import this module.
    """
    from .serializers import StudentResultSerializer
    return StudentResultSerializer()


def enrollments_for(profile, semester):
    """Every module this person sat in this semester.

    Matched on the profile, and on the registration number for enrollments made
    before enrollments pointed at a person at all.
    """
    return (
        Student.objects
        .filter(module__semester=semester)
        .filter(Q(profile=profile)
                | Q(profile__isnull=True, nactvet_reg_no__iexact=profile.nactvet_reg_no))
        .select_related('module__class_level', 'module__programme', 'result')
        .order_by('module__code')
    )


def enrollments_by_profile(semester):
    """Every enrollment in the semester, grouped by the person.

    One query instead of one per student. At 500 students each sitting six
    modules, the difference between this and asking per student is thousands of
    round trips on a shared server.
    """
    by_profile, by_reg_no = {}, {}
    rows = (Student.objects.filter(module__semester=semester)
            .select_related('module__class_level', 'module__programme', 'result')
            .order_by('module__code'))
    for row in rows:
        if row.profile_id:
            by_profile.setdefault(row.profile_id, []).append(row)
        else:
            by_reg_no.setdefault(row.nactvet_reg_no.upper(), []).append(row)
    return by_profile, by_reg_no


def repeats_by_profile(profiles=None):
    """Open repeats grouped by the person, in one query."""
    rows = OutstandingRepeat.objects.filter(
        status=OutstandingRepeat.OPEN).select_related('class_level', 'origin_semester')
    if profiles is not None:
        rows = rows.filter(profile__in=profiles)
    grouped = {}
    for repeat in rows:
        grouped.setdefault(repeat.profile_id, []).append(repeat)
    return grouped


def standings_by_profile(profiles):
    return {standing.profile_id: standing for standing in
            StudentStanding.objects.filter(profile__in=profiles)
            .select_related('programme', 'class_level', 'return_year')}


def module_row(enrollment, serializer=None):
    """One module's outcome, as the review stores and shows it."""
    from .grading import result_outcome

    module = enrollment.module
    result = getattr(enrollment, 'result', None)
    row = {
        'module_id': module.id,
        'code': module.code,
        'name': module.name,
        'credits': module.credits,
        'class_level': module.class_level.name,
        'attempt': enrollment.attempt,
        'status': 'INCOMPLETE',
        'grade': None,
        'points': None,
        'total': None,
    }
    if result is None:
        row['reason'] = 'no marks entered'
        return row
    if not result.final_approved and not result.authority_grade:
        # Entered but not published. Until the examination officer approves it,
        # it is not a result the college acts on.
        row['reason'] = 'results not yet approved'
        return row

    outcome = result_outcome(result, serializer or _result_serializer())
    row.update({
        'status': outcome['status'],
        'grade': outcome['grade'],
        'points': outcome['grade_point'],
        'total': outcome['official_total'],
    })
    return row


def evaluate(profile, semester, *, registration=None, enrollments=None,
             repeats=None, serializer=None):
    """What this semester's results say about this student.

    Returns the GPA, a row per module, the proposed outcome and the reason for
    it. Proposes only — the examination officer or the Principal confirms, and
    may overrule.

    `enrollments`, `repeats` and `serializer` are for callers working through a
    whole semester at once, which load them once for everybody.
    """
    serializer = serializer or _result_serializer()
    if enrollments is None:
        enrollments = list(enrollments_for(profile, semester))
    rows = [module_row(enrollment, serializer) for enrollment in enrollments]

    graded = [(row['points'], row['credits']) for row in rows if row['points'] is not None]
    gpa = (round(sum(float(points) * credits for points, credits in graded)
                 / sum(credits for _, credits in graded), 2)
           if graded else None)

    class_level = None
    if registration is not None:
        class_level = registration.class_level
    elif enrollments:
        class_level = max((e.module.class_level for e in enrollments), key=lambda lvl: lvl.order)

    pending = [row['code'] for row in rows if row['status'] in PENDING_STATUSES]
    failed = [row['code'] for row in rows if row['status'] in FAILED_STATUSES]
    discontinued = [row['code'] for row in rows if row['status'] == 'DISCONTINUED']

    if not rows:
        proposed, reason = SemesterReview.PENDING, 'No modules sat this semester.'
    elif discontinued:
        proposed = SemesterReview.DISCONTINUED
        reason = f'Discontinued by the authority on {", ".join(discontinued)}.'
    elif pending:
        proposed = SemesterReview.PENDING
        reason = f'Waiting for results: {", ".join(pending)}.'
    elif gpa is None:
        proposed, reason = SemesterReview.PENDING, 'No graded module yet.'
    elif gpa < PASS_GPA:
        proposed = SemesterReview.DISCONTINUED
        reason = f'GPA {gpa:.2f} is below {PASS_GPA:.1f}.'
    elif failed:
        proposed = SemesterReview.REPEAT
        reason = f'GPA {gpa:.2f}; repeats {", ".join(failed)}.'
    else:
        proposed = SemesterReview.CLEAR
        reason = f'Passed every module with GPA {gpa:.2f}.'

    # Finishing is decided at the end of the year, and only when nothing at all
    # is left outstanding — including a module failed in an earlier year.
    if (proposed == SemesterReview.CLEAR and semester.number == Semester.SEM2
            and class_level is not None and is_level_six(class_level)):
        outstanding = open_repeats(profile) if repeats is None else repeats
        left = [repeat.module_code for repeat in outstanding
                if repeat.origin_semester_id != semester.id]
        if left:
            proposed = SemesterReview.REPEAT
            reason = f'Passed this semester, still to pass {", ".join(sorted(left))}.'
        else:
            proposed = SemesterReview.COMPLETED
            reason = f'Completed level 6 with GPA {gpa:.2f}.'

    return {
        'gpa': gpa,
        'modules': rows,
        'proposed': proposed,
        'reason': reason,
        'class_level': class_level,
        'programme': (registration.programme if registration is not None
                      else next((e.module.programme for e in enrollments if e.module.programme), None)),
        'failed': failed,
        'pending': pending,
        'enrollments': enrollments,
    }


# ── the review ────────────────────────────────────────────────────────────────

def students_of(semester):
    """Everyone the semester has to account for: each person registered for it,
    and anyone enrolled in one of its modules without a registration."""
    from .models import StudentProfile

    registered = {
        registration.profile_id: registration
        for registration in SemesterRegistration.objects
        .filter(semester=semester)
        .exclude(status=SemesterRegistration.CANCELLED)
        .select_related('profile', 'programme', 'class_level')
    }
    # Students enrolled in the semester's modules without a registration — the
    # years before registrations were kept. Anyone who stopped studying it, or
    # whose registration was cancelled, is deliberately not here: they are
    # shown by their standing instead, and a semester they did not study is not
    # a semester anybody has to decide on.
    cancelled = set(
        SemesterRegistration.objects.filter(
            semester=semester, status=SemesterRegistration.CANCELLED)
        .values_list('profile_id', flat=True)
    )
    extra = set(
        Student.objects.studying()
        .filter(module__semester=semester, profile__isnull=False)
        .exclude(profile_id__in=registered)
        .exclude(profile_id__in=cancelled)
        .values_list('profile_id', flat=True)
    )
    profiles = {p.id: p for p in StudentProfile.objects.filter(
        id__in=set(registered) | extra)}
    return [(profiles[pid], registered.get(pid)) for pid in profiles]


@transaction.atomic
def build_reviews(semester, *, actor=None):
    """Work out where every student in this semester stands.

    Re-running is safe and is how the officer picks up results that arrived
    since: a review nobody has confirmed is recalculated, and a confirmed one is
    left exactly as it was confirmed.
    """
    people = students_of(semester)
    profiles = [profile for profile, _ in people]
    by_profile, by_reg_no = enrollments_by_profile(semester)
    repeats = repeats_by_profile(profiles)
    existing = {review.profile_id: review for review in
                SemesterReview.objects.filter(semester=semester, profile__in=profiles)}
    serializer = _result_serializer()

    made, changed = [], []
    for profile, registration in people:
        outcome = evaluate(
            profile, semester, registration=registration, serializer=serializer,
            enrollments=(by_profile.get(profile.id)
                         or by_reg_no.get(profile.nactvet_reg_no.upper(), [])),
            repeats=repeats.get(profile.id, []),
        )
        review = existing.get(profile.id)
        if review is None:
            made.append(SemesterReview(
                profile=profile, semester=semester,
                programme=outcome['programme'], class_level=outcome['class_level'],
                gpa=outcome['gpa'], modules=outcome['modules'],
                proposed=outcome['proposed'], proposed_reason=outcome['reason'][:300],
            ))
        elif not review.is_confirmed:
            review.programme = outcome['programme']
            review.class_level = outcome['class_level']
            review.gpa = outcome['gpa']
            review.modules = outcome['modules']
            review.proposed = outcome['proposed']
            review.proposed_reason = outcome['reason'][:300]
            changed.append(review)

    if made:
        SemesterReview.objects.bulk_create(made)
    if changed:
        SemesterReview.objects.bulk_update(
            changed, ['programme', 'class_level', 'gpa', 'modules', 'proposed',
                      'proposed_reason', 'updated_at'])

    reviews = list(SemesterReview.objects.filter(semester=semester, profile__in=profiles)
                   .select_related('profile', 'programme', 'class_level'))
    logger.info('Built %d semester review(s) for %s', len(reviews), semester)
    return reviews


@transaction.atomic
def confirm_review(review, outcome, *, actor, reason='', enrollments=None,
                   repeats=None, serializer=None, known_standings=None):
    """Record the officer's decision on one student, and act on it.

    Confirming is what makes a decision real: repeats are raised, a
    discontinued student's standing is set, and a student who failed semester 1
    after their supplementary is taken off semester 2. The semester cannot be
    advanced until every student has been through here.
    """
    valid = {key for key, _ in SemesterReview.OUTCOME_CHOICES}
    if outcome not in valid:
        raise ProgressionError(f'{outcome!r} is not an outcome.')
    if outcome == SemesterReview.PENDING:
        raise ProgressionError(
            'Pending is not a decision. Wait for the results, or confirm the '
            'student as provisional to let them carry on while they come.')

    # Read the results again, here, at the moment somebody decides on them.
    # A decision is often taken weeks after the review was first built — a
    # supplementary marked in the middle of the next semester — and acting on
    # the older snapshot raised no repeat at all, because the snapshot still
    # said the mark was outstanding.
    registration = SemesterRegistration.objects.filter(
        profile=review.profile, semester=review.semester).exclude(
        status=SemesterRegistration.CANCELLED).first()
    fresh = evaluate(review.profile, review.semester, registration=registration,
                     enrollments=enrollments, repeats=repeats, serializer=serializer)
    review.modules = fresh['modules']
    review.gpa = fresh['gpa']
    review.proposed = fresh['proposed']
    review.proposed_reason = fresh['reason'][:300]
    if fresh['class_level'] is not None:
        review.class_level = fresh['class_level']
    if fresh['programme'] is not None:
        review.programme = fresh['programme']

    review.confirmed = outcome
    review.confirmed_reason = (reason or '')[:300]
    review.confirmed_by = actor
    review.confirmed_at = timezone.now()
    review.save(update_fields=['modules', 'gpa', 'proposed', 'proposed_reason',
                               'class_level', 'programme', 'confirmed', 'confirmed_reason',
                               'confirmed_by', 'confirmed_at', 'updated_at'])

    _resolve_repeat_sittings(review, enrollments=enrollments, repeats=repeats)

    if outcome == SemesterReview.REPEAT:
        _raise_repeats(review, actor=actor, enrollments=enrollments, known=known_standings)
    elif outcome == SemesterReview.DISCONTINUED:
        _discontinue(review, actor=actor, known=known_standings)
    elif outcome == SemesterReview.COMPLETED:
        set_standing(review.profile, StudentStanding.COMPLETED, actor=actor,
                     semester=review.semester, class_level=review.class_level,
                     programme=review.programme, known=known_standings,
                     reason=review.confirmed_reason or review.proposed_reason)
    elif outcome in (SemesterReview.CLEAR, SemesterReview.PROVISIONAL):
        standing = standing_for(review.profile, class_level=review.class_level,
                                programme=review.programme, known=known_standings)
        if standing.status not in (StudentStanding.COMPLETED, StudentStanding.ARCHIVED,
                                   StudentStanding.POSTPONED):
            keep_repeating = (bool(repeats) if repeats is not None
                              else open_repeats(review.profile).exists())
            set_standing(
                review.profile,
                StudentStanding.REPEATING if keep_repeating else StudentStanding.ACTIVE,
                actor=actor, semester=review.semester, class_level=review.class_level,
                programme=review.programme, reason=review.confirmed_reason,
                known=known_standings)
    return review


@transaction.atomic
def confirm_proposed(semester, reviews, *, actor):
    """Confirm a whole year group's worth of decisions in one pass.

    Five hundred students confirmed one at a time is five hundred trips back to
    the database for the same semester's enrollments, repeats and standings.
    They are loaded once here and handed to each decision.

    A student still waiting on a result is left alone: "we do not know yet" is
    not a decision anybody can take in bulk.
    """
    reviews = [review for review in reviews if not review.is_confirmed]
    profiles = [review.profile for review in reviews]
    by_profile, by_reg_no = enrollments_by_profile(semester)
    repeats = repeats_by_profile(profiles)
    standings = standings_by_profile(profiles)
    serializer = _result_serializer()

    confirmed, left = 0, 0
    for review in reviews:
        if review.proposed == SemesterReview.PENDING:
            left += 1
            continue
        profile = review.profile
        confirm_review(
            review, review.proposed, actor=actor,
            enrollments=(by_profile.get(profile.id)
                         or by_reg_no.get(profile.nactvet_reg_no.upper(), [])),
            repeats=repeats.get(profile.id, []),
            serializer=serializer, known_standings=standings,
        )
        confirmed += 1
    logger.info('Confirmed %d review(s) for %s, %d left waiting', confirmed, semester, left)
    return confirmed, left


def _resolve_repeat_sittings(review, *, enrollments=None, repeats=None):
    """Close the repeats this semester's sittings have settled.

    A passed repeat is the result that counts for that module from now on. The
    failed one it replaces is not touched — it stays as the record of what
    happened, and this row is what says it has been superseded.
    """
    by_code = {row['code'].upper(): row for row in review.modules
               if row.get('attempt') == Student.REPEAT}
    if not by_code:
        return
    sittings = {e.module.code.upper(): e for e in
                (enrollments if enrollments is not None
                 else enrollments_for(review.profile, review.semester))}
    for repeat in (repeats if repeats is not None else open_repeats(review.profile)):
        row = by_code.get(repeat.module_code.upper())
        if row is None:
            continue
        sitting = sittings.get(repeat.module_code.upper())
        if sitting is not None:
            repeat.attempt_enrollment = sitting
        if row['status'] == 'PASS':
            repeat.status = OutstandingRepeat.PASSED
            repeat.resolved_at = timezone.now()
            repeat.note = f'Passed on the repeat sitting in {review.semester}.'[:300]
        repeat.save(update_fields=['attempt_enrollment', 'status', 'resolved_at', 'note'])


def _raise_repeats(review, *, actor=None, enrollments=None, known=None):
    """Record every module this student still has to pass, and hold them to it."""
    failed = {row['code'].upper() for row in review.modules if row['status'] in FAILED_STATUSES}
    if not failed:
        return []
    sittings = {e.module.code.upper(): e for e in
                (enrollments if enrollments is not None
                 else enrollments_for(review.profile, review.semester))}
    raised = []
    for code in sorted(failed):
        enrollment = sittings.get(code)
        if enrollment is None:
            continue
        repeat, created = OutstandingRepeat.objects.get_or_create(
            profile=review.profile, module_code=enrollment.module.code,
            origin_semester=review.semester,
            defaults={
                'module_name': enrollment.module.name,
                'class_level': enrollment.module.class_level,
                'semester_number': review.semester.number,
                'origin_enrollment': enrollment,
            },
        )
        if created:
            raised.append(repeat)

    set_standing(review.profile, StudentStanding.REPEATING, actor=actor,
                 semester=review.semester, class_level=review.class_level,
                 programme=review.programme, known=known,
                 return_semester_number=review.semester.number,
                 return_year=_next_year(review.semester.academic_year),
                 reason=review.confirmed_reason or review.proposed_reason)

    # No further semester until the module is passed — whether that is the
    # semester 2 they were about to start or the level they were promoted into
    # before the supplementary was marked.
    review.deferred = [registration.semester.label
                       for registration in defer_later_registrations(review, actor=actor)]
    return raised


def defer_later_registrations(review, *, actor=None):
    """Take a student off the semesters they are not entitled to yet.

    Two cases, one rule. A semester 1 module failed after the supplementary
    means no semester 2 until it is passed. And a supplementary marked after the
    year has turned — which is when they are marked — can fail a student who has
    already been registered into the next level: they do not move up until the
    module is passed either, so that registration comes off too.

    The registration is cancelled with its reason, never deleted, and the
    enrollments and marks made while the student was still expected stay exactly
    as they are. Returns what was cancelled, so the officer is told.
    """
    later = SemesterRegistration.objects.filter(profile=review.profile).exclude(
        status=SemesterRegistration.CANCELLED,
    ).select_related('semester__academic_year').filter(
        Q(semester__academic_year__name__gt=review.semester.academic_year.name)
        | Q(semester__academic_year=review.semester.academic_year,
            semester__number__gt=review.semester.number),
    )
    reason = (f'{review.semester.label}: module(s) failed after the supplementary. '
              f'No further semester until they are passed.')[:300]
    cancelled = []
    for registration in later:
        registration.status = SemesterRegistration.CANCELLED
        registration.cancelled_reason = reason
        registration.save(update_fields=['status', 'cancelled_reason'])
        withdraw_enrollments(review.profile, registration.semester, reason=reason)
        cancelled.append(registration)
        logger.info('Took %s off %s after a late result',
                    review.profile.nactvet_reg_no, registration.semester)
    return cancelled


def withdraw_enrollments(profile, semester, *, reason=''):
    """Take a student off the modules of a semester they are no longer studying.

    Cancelling the registration alone was not enough: class lists, the
    attendance register and mark entry all read the enrollments, so a student
    who had stopped still sat on their tutors' screens and still got marked.

    The enrollments and every mark on them stay exactly where they are — this
    is a date, not a delete — so the student's record still shows the semester
    they started and did not finish.
    """
    stopped = profile.enrollments.filter(
        module__semester=semester, withdrawn_at__isnull=True)
    count = stopped.update(withdrawn_at=timezone.now(), withdrawn_reason=reason[:300])
    if count:
        logger.info('Withdrew %s from %d module(s) in %s',
                    profile.nactvet_reg_no, count, semester)
    return count


def _discontinue(review, *, actor=None, known=None):
    set_standing(review.profile, StudentStanding.DISCONTINUED, actor=actor,
                 semester=review.semester, class_level=review.class_level,
                 programme=review.programme, known=known,
                 return_semester_number=review.semester.number,
                 return_year=_next_year(review.semester.academic_year),
                 reason=review.confirmed_reason or review.proposed_reason)
    review.deferred = [registration.semester.label
                       for registration in defer_later_registrations(review, actor=actor)]


# ── standing ──────────────────────────────────────────────────────────────────

def standing_for(profile, *, class_level=None, programme=None, known=None):
    """This student's standing, created as 'studying' the first time it is asked
    for — everyone already in the college is studying until something says
    otherwise.

    `class_level` and `programme` save the two finance lookups that otherwise
    run for every student a batch touches; `known` is a standings-by-profile
    map for callers that loaded them all in one query.
    """
    if known is not None and profile.id in known:
        return known[profile.id]
    standing, _ = StudentStanding.objects.get_or_create(
        profile=profile,
        defaults={
            'status': StudentStanding.ACTIVE,
            'class_level': class_level or finance.class_level_for(profile),
            'programme': programme or finance.programme_for(profile),
        },
    )
    if known is not None:
        known[profile.id] = standing
    return standing


@transaction.atomic
def set_standing(profile, status, *, actor=None, semester=None, reason='',
                 class_level=None, programme=None,
                 return_year=None, return_semester_number=None, note='', known=None):
    """Move a student's standing and keep the trail.

    Every move is written to StandingChange with its reason, so a student who
    comes back in three years asking why they were discontinued is answered from
    the record rather than from memory.
    """
    standing = standing_for(profile, class_level=class_level, programme=programme, known=known)
    before = standing.status
    standing.status = status
    if class_level is not None:
        standing.class_level = class_level
    if programme is not None:
        standing.programme = programme
    if status in StudentStanding.AWAY or status == StudentStanding.REPEATING:
        standing.return_year = return_year
        standing.return_semester_number = return_semester_number
    else:
        standing.return_year = None
        standing.return_semester_number = None
    if note:
        standing.note = note[:300]
    standing.updated_by = actor
    standing.save()

    if before != status:
        StandingChange.objects.create(
            profile=profile, from_status=before, to_status=status,
            semester=semester, reason=(reason or '')[:300], changed_by=actor)
    return standing


def open_repeats(profile):
    return profile.outstanding_repeats.filter(
        status=OutstandingRepeat.OPEN).select_related('class_level', 'origin_semester')


# ── the modules the new year is taught from ───────────────────────────────────

def source_for(year_name, number):
    """The most recent earlier year that has a module list for this semester.

    The college's module list stays the same year to year unless the authority
    changes it, so a new year is taught from the last list the college used.
    """
    for year in AcademicYear.objects.filter(name__lt=year_name).order_by('-name'):
        candidate = Semester.objects.filter(academic_year=year, number=number).first()
        if candidate is not None and candidate.modules.exists():
            return candidate
    return None


def source_semester(target):
    return source_for(target.academic_year.name, target.number)


def preview_carry(year_name, number, target=None):
    """What carrying the modules forward would create, without creating the
    semester — the new academic year does not exist until the advance makes it."""
    source = source_for(year_name, number)
    have = ({code.upper() for code in target.modules.values_list('code', flat=True)}
            if target is not None else set())
    created, existing = [], []
    if source is not None:
        for code in source.modules.values_list('code', flat=True):
            (existing if code.upper() in have else created).append(code)
    return {'source': source, 'created': created, 'existing': existing}


@transaction.atomic
def carry_modules(target, *, source=None, actor=None, dry_run=False):
    """Copy last year's module list into this semester.

    Modules are one row per semester, so a new academic year starts with none at
    all and nobody can be enrolled in anything. The college teaches the same
    modules every year unless the authority adds or removes one, so they are
    carried forward and the examination officer edits the exceptions.

    Only modules missing from the target are created, by code, so running this
    twice does nothing the second time. Nothing in the source semester is
    touched.
    """
    source = source or source_semester(target)
    if source is None:
        return {'source': None, 'created': [], 'existing': []}

    have = {code.upper() for code in target.modules.values_list('code', flat=True)}
    created, skipped = [], []
    for module in source.modules.select_related('class_level', 'programme').prefetch_related('teachers'):
        if module.code.upper() in have:
            skipped.append(module.code)
            continue
        if dry_run:
            created.append(module.code)
            continue
        copy = Module.objects.create(
            name=module.name, code=module.code, teacher=module.teacher,
            class_level=module.class_level, semester=target, programme=module.programme,
            has_practical=module.has_practical, is_field_module=module.is_field_module,
            credits=module.credits,
        )
        copy.teachers.set(module.teachers.all())
        created.append(copy.code)
    logger.info('Carried %d module(s) from %s to %s', len(created), source, target)
    return {'source': source, 'created': created, 'existing': skipped}


# ── enrolling the registered ──────────────────────────────────────────────────

def _portal_credentials(profile):
    """The password the student already uses, so a new year's enrollments do not
    lock them out. Login checks the PIN on the enrollment rows themselves."""
    existing = (profile.enrollments.exclude(portal_pin_hash='')
                .order_by('-created_at').first())
    if existing is None:
        return '', True
    return existing.portal_pin_hash, existing.must_change_portal_password


@transaction.atomic
def enroll_registration(registration, *, actor=None, repeats=None, modules_by_semester=None):
    """Put a registered student in front of the modules they are to sit.

    A continuing student gets every module of their programme, level and
    semester. A repeating student gets only the modules they failed, marked as
    repeat sittings and billed at the accountant's per-module repeat rate rather
    than a fresh set of programme fees.
    """
    if registration.status == SemesterRegistration.CANCELLED:
        return []

    profile = registration.profile
    semester = registration.semester
    pin_hash, must_change = _portal_credentials(profile)
    made = []

    # Whatever else they are studying, a module still to be passed is sat when
    # its semester comes round again — including by a student whose other
    # modules are at a different level.
    owed = (repeats if repeats is not None
            else list(open_repeats(profile)))
    wanted = {repeat.module_code.upper(): repeat for repeat in owed
              if repeat.semester_number == semester.number}
    # The semester's modules, loaded once when a caller is working through a
    # whole year group rather than one student.
    if modules_by_semester is None:
        modules_by_semester = list(
            Module.objects.filter(semester=semester).select_related('class_level'))
    resitting = [module for module in modules_by_semester
                 if module.code.upper() in wanted
                 and module.programme_id in (registration.programme_id, None)]
    if registration.kind == SemesterRegistration.REPEATING:
        modules = resitting
    else:
        # The repeat row wins over the level's own copy of the same module, so
        # a re-sitting is billed as a repeat and not as a first attempt.
        by_id = {module.id: module for module in modules_by_semester
                 if module.class_level_id == registration.class_level_id
                 and module.programme_id == registration.programme_id}
        by_id.update({module.id: module for module in resitting})
        modules = list(by_id.values())

    already = set(Student.objects.filter(
        nactvet_reg_no=profile.nactvet_reg_no,
        module__in=[module.id for module in modules]).values_list('module_id', flat=True))

    for module in modules:
        repeat = wanted.get(module.code.upper())
        if module.id in already:
            continue
        enrollment = Student(
            nactvet_reg_no=profile.nactvet_reg_no, module=module,
            name=profile.name, profile=profile,
            portal_pin_hash=pin_hash, must_change_portal_password=must_change,
            attempt=Student.REPEAT if repeat else Student.NORMAL,
            repeat_of=repeat.origin_enrollment if repeat else None,
        )
        # Billing is raised once for the registration below, not once for each
        # module: enrolling a class of 500 into six modules each ran the fee
        # generator three thousand times for five hundred students' worth of
        # charges. A student sitting a module again pays the accountant's rate
        # for that module and no programme fees at all.
        enrollment.skip_auto_billing = True
        enrollment.save()
        made.append(enrollment)
        if repeat is not None:
            repeat.attempt_enrollment = enrollment
            repeat.save(update_fields=['attempt_enrollment'])
            try:
                finance.declare_exam_charge(
                    enrollment, ChargeType.REPEAT_MODULE, actor=actor,
                    reason=f'Repeat of {module.code}')
            except finance.DeclarationError as exc:
                # No rate set yet. The student is enrolled and the accountant
                # raises the charge from the declarations screen; failing the
                # whole advance over a missing fee row would be worse.
                logger.warning('No repeat charge for %s on %s: %s',
                               profile.nactvet_reg_no, module.code, exc)

    if made and registration.kind != SemesterRegistration.REPEATING:
        try:
            finance.generate_charges(profile, semester.academic_year, actor=actor)
        except ValueError as exc:
            # The fee structure has no due dates yet. Registration must not
            # fail over that; the accountant's bulk run catches them up.
            logger.warning('Could not bill %s for %s: %s',
                           profile.nactvet_reg_no, semester.academic_year, exc)
    return made


def enroll_module(module, *, actor=None):
    """Enroll everyone already registered for the semester into a module created
    after their registrations were made.

    Registration and the module list are built in either order — the office may
    register a class before the examination officer has finished carrying
    forward the modules — so both directions have to end in the same place.
    """
    made = []
    registrations = SemesterRegistration.objects.filter(
        semester=module.semester, class_level=module.class_level,
    ).exclude(status=SemesterRegistration.CANCELLED).select_related(
        'profile', 'programme', 'class_level', 'semester')
    if module.programme_id:
        registrations = registrations.filter(programme=module.programme)
    for registration in registrations:
        made.extend(enroll_registration(registration, actor=actor))
    return made


def enroll_semester(semester, *, actor=None):
    """Enroll everyone registered for this semester. Safe to repeat."""
    made = []
    for registration in SemesterRegistration.objects.filter(semester=semester).exclude(
            status=SemesterRegistration.CANCELLED).select_related(
            'profile', 'programme', 'class_level', 'semester'):
        made.extend(enroll_registration(registration, actor=actor))
    return made


# ── the advance itself ────────────────────────────────────────────────────────

def _next_year(year):
    return AcademicYear.objects.filter(name=year.next_name).first()


def next_level(programme, class_level):
    """The level a student moves up to, or None when they have finished.

    Taken from the levels the programme is taught at, so a programme that runs
    5 to 6 does not promote anybody to a level it does not teach.
    """
    if class_level is None:
        return None
    levels = list(programme.levels.order_by('order')) if programme else list(
        ClassLevel.objects.order_by('order'))
    higher = [level for level in levels if level.order > class_level.order]
    return higher[0] if higher else None


def _next_level_cached(programme, class_level, cache):
    """`next_level`, asked once per programme rather than once per student."""
    key = programme.id if programme else None
    if key not in cache:
        cache[key] = list(programme.levels.order_by('order')) if programme else list(
            ClassLevel.objects.order_by('order'))
    higher = [level for level in cache[key] if class_level and level.order > class_level.order]
    return higher[0] if higher else None


def plan_advance(semester):
    """What advancing out of this semester would do to each student.

    Read-only: this is the list the officer sees before pressing the button, and
    the same list the advance then applies.
    """
    target_number = Semester.SEM2 if semester.number == Semester.SEM1 else Semester.SEM1
    moves, blocked = [], []

    people = students_of(semester)
    profiles = [profile for profile, _ in people]
    reviews = {review.profile_id: review for review in
               SemesterReview.objects.filter(semester=semester, profile__in=profiles)
               .select_related('class_level', 'programme')}
    standings = standings_by_profile(profiles)
    repeats = repeats_by_profile(profiles)
    levels_of = {}

    for profile, registration in people:
        review = reviews.get(profile.id)
        standing = standing_for(profile, known=standings)
        if review is None or not review.is_confirmed:
            blocked.append({
                'profile_id': profile.id, 'reg_no': profile.nactvet_reg_no,
                'name': profile.name,
                'reason': 'no confirmed review' if review is None
                          else f'review still {review.get_proposed_display().lower()}',
            })
            continue

        outcome = review.confirmed
        level = review.class_level or standing.class_level
        programme = review.programme or standing.programme
        move = {
            'profile_id': profile.id, 'reg_no': profile.nactvet_reg_no, 'name': profile.name,
            'outcome': outcome, 'from_level': level.name if level else None,
            'programme': programme.code if programme else None,
        }

        if standing.status == StudentStanding.POSTPONED:
            move.update(action='away', detail='Postponed', registers=False)
        elif outcome == SemesterReview.DISCONTINUED:
            move.update(action='discontinued', registers=False,
                        detail=f'Returns on readmission to semester {semester.number}')
        elif outcome == SemesterReview.COMPLETED:
            move.update(action='completed', registers=False,
                        detail='Finished — awaiting clearance')
        elif semester.number == Semester.SEM1:
            if outcome == SemesterReview.REPEAT:
                move.update(action='deferred', registers=False,
                            detail='No semester 2 until the failed module is passed')
            else:
                move.update(action='continues', registers=True, to_level=level.name if level else None,
                            to_semester=target_number, kind=SemesterRegistration.CONTINUING,
                            detail='Continues into semester 2')
        else:
            owed = repeats.get(profile.id, [])
            if owed:
                sem1_repeats = [r.module_code for r in owed
                                if r.semester_number == Semester.SEM1]
                move.update(
                    action='repeats',
                    registers=bool(sem1_repeats),
                    to_level=level.name if level else None,
                    to_semester=Semester.SEM1 if sem1_repeats else Semester.SEM2,
                    kind=SemesterRegistration.REPEATING,
                    detail=('Repeats ' + ', '.join(sorted(r.module_code for r in owed))
                            + ('' if sem1_repeats else ' — registers in semester 2')))
            else:
                up = _next_level_cached(programme, level, levels_of)
                if up is None:
                    move.update(action='completed', registers=False,
                                detail='Finished — awaiting clearance')
                else:
                    move.update(action='promoted', registers=True, to_level=up.name,
                                to_semester=Semester.SEM1, kind=SemesterRegistration.CONTINUING,
                                detail=f'{level.name} → {up.name}')
        moves.append(move)

    return {'moves': moves, 'blocked': blocked}


@transaction.atomic
def apply_advance(semester, target, *, actor=None):
    """Move every reviewed student into the semester the college is opening.

    Registrations are created, standings updated and enrollments made. Students
    who are not coming — discontinued, postponed, deferred, finished — are left
    with the standing that says where they are and what they come back to.
    """
    summary = {'registered': 0, 'promoted': 0, 'repeating': 0, 'discontinued': 0,
               'completed': 0, 'deferred': 0, 'away': 0, 'returned': 0, 'enrolled': 0,
               'students': []}

    people = students_of(semester)
    profiles = [profile for profile, _ in people]
    reviews = {review.profile_id: review for review in
               SemesterReview.objects.filter(semester=semester, profile__in=profiles)
               .select_related('class_level', 'programme')}
    standings = standings_by_profile(profiles)
    repeats_open = repeats_by_profile(profiles)
    target_modules = list(Module.objects.filter(semester=target).select_related('class_level'))
    registered_already = set(SemesterRegistration.objects.filter(
        semester=target, profile__in=profiles).values_list('profile_id', flat=True))
    levels_of = {}

    for profile, _registration in people:
        review = reviews.get(profile.id)
        if review is None or not review.is_confirmed:
            raise ProgressionError(
                f'{profile.nactvet_reg_no} has no confirmed review for {semester}.')
        standing = standing_for(profile, known=standings)
        outcome = review.confirmed
        level = review.class_level or standing.class_level
        programme = review.programme or standing.programme

        if standing.status == StudentStanding.POSTPONED:
            summary['away'] += 1
            continue
        if outcome == SemesterReview.DISCONTINUED:
            summary['discontinued'] += 1
            continue
        if outcome == SemesterReview.COMPLETED:
            summary['completed'] += 1
            continue

        if semester.number == Semester.SEM1:
            if outcome == SemesterReview.REPEAT:
                summary['deferred'] += 1
                continue
            kind, to_level = SemesterRegistration.CONTINUING, level
        else:
            owed = repeats_open.get(profile.id, [])
            if owed:
                if not any(repeat.semester_number == Semester.SEM1 for repeat in owed):
                    # Everything they owe is a semester 2 module; they register
                    # when semester 2 opens, not now.
                    set_standing(profile, StudentStanding.REPEATING, actor=actor,
                                 semester=semester, class_level=level, programme=programme,
                                 return_year=target.academic_year,
                                 return_semester_number=Semester.SEM2, known=standings,
                                 reason='Repeats semester 2 module(s).')
                    summary['repeating'] += 1
                    continue
                kind, to_level = SemesterRegistration.REPEATING, level
                summary['repeating'] += 1
            else:
                up = _next_level_cached(programme, level, levels_of)
                if up is None:
                    set_standing(profile, StudentStanding.COMPLETED, actor=actor,
                                 semester=semester, class_level=level, programme=programme,
                                 known=standings, reason='Finished the programme.')
                    summary['completed'] += 1
                    continue
                kind, to_level = SemesterRegistration.CONTINUING, up
                summary['promoted'] += 1

        if programme is None or to_level is None:
            raise ProgressionError(
                f'{profile.nactvet_reg_no} has no programme or level recorded; '
                'set it on the review before advancing.')

        if profile.id in registered_already:
            registration = SemesterRegistration.objects.get(profile=profile, semester=target)
        else:
            registration = SemesterRegistration.objects.create(
                profile=profile, semester=target, programme=programme,
                class_level=to_level, kind=kind, created_by=actor)
            registered_already.add(profile.id)
            summary['registered'] += 1
        set_standing(profile,
                     StudentStanding.REPEATING if kind == SemesterRegistration.REPEATING
                     else StudentStanding.ACTIVE,
                     actor=actor, semester=semester, class_level=to_level, programme=programme,
                     return_year=target.academic_year if kind == SemesterRegistration.REPEATING else None,
                     return_semester_number=target.number if kind == SemesterRegistration.REPEATING else None,
                     reason=f'Advanced into {target}.', known=standings)
        made = enroll_registration(registration, actor=actor,
                                   repeats=repeats_open.get(profile.id, []),
                                   modules_by_semester=target_modules)
        summary['enrolled'] += len(made)
        summary['students'].append({
            'reg_no': profile.nactvet_reg_no, 'name': profile.name,
            'kind': kind, 'level': to_level.name, 'modules': len(made),
        })

    summary['returned'] = len(register_returning_repeats(target, actor=actor, summary=summary))

    logger.info('Advanced %s → %s: %s', semester, target,
                {key: value for key, value in summary.items() if key != 'students'})
    return summary


def returning_repeats(target):
    """The students who owe a module that this semester teaches.

    They are not in the semester that is closing — a student who failed a
    supplementary stopped there, and one who owes a semester 2 module sat out
    semester 1 — so nothing in the advance would otherwise look for them. This
    is the college's rule read forwards: they wait for the module to come round
    again, and it has.
    """
    waiting = []
    standings = StudentStanding.objects.filter(
        status=StudentStanding.REPEATING).select_related('profile', 'programme', 'class_level')
    for standing in standings:
        repeats = [repeat for repeat in open_repeats(standing.profile)
                   if repeat.semester_number == target.number]
        if not repeats:
            continue
        if SemesterRegistration.objects.filter(
                profile=standing.profile, semester=target).exclude(
                status=SemesterRegistration.CANCELLED).exists():
            continue
        waiting.append((standing, repeats))
    return waiting


@transaction.atomic
def register_returning_repeats(target, *, actor=None, summary=None):
    """Bring those students back for the module they owe, and bill it."""
    brought_back = []
    for standing, repeats in returning_repeats(target):
        programme = standing.programme or finance.programme_for(standing.profile)
        level = standing.class_level or repeats[0].class_level
        if programme is None or level is None:
            logger.warning('Cannot register %s for their repeat: no programme or level',
                           standing.profile.nactvet_reg_no)
            continue
        registration = SemesterRegistration.objects.create(
            profile=standing.profile, semester=target, programme=programme,
            class_level=level, kind=SemesterRegistration.REPEATING, created_by=actor)
        made = enroll_registration(registration, actor=actor)
        set_standing(standing.profile, StudentStanding.REPEATING, actor=actor, semester=target,
                     class_level=level, programme=programme,
                     return_year=target.academic_year, return_semester_number=target.number,
                     reason=f'Back for {", ".join(repeat.module_code for repeat in repeats)}.')
        brought_back.append(registration)
        if summary is not None:
            summary['enrolled'] = summary.get('enrolled', 0) + len(made)
            summary['students'].append({
                'reg_no': standing.profile.nactvet_reg_no, 'name': standing.profile.name,
                'kind': SemesterRegistration.REPEATING, 'level': level.name,
                'modules': len(made),
            })
        logger.info('Brought %s back into %s for %d repeat module(s)',
                    standing.profile.nactvet_reg_no, target, len(made))
    return brought_back
