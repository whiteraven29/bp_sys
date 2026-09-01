"""Evaluation forms: what a student may answer, and what the answers add up to.

The college's evaluations were collected on paper and tallied by hand, so the
only thing anyone ever saw was the tally — never the distribution, never a
comparison against last year, and never the free-text answers unless somebody
retyped them. Holding the questions as data makes all three fall out for free.

Every rule about who may answer what, and what an answer is allowed to look
like, lives here so the portal, the API and the export cannot disagree.
"""

from collections import Counter
from datetime import date

from django.db import transaction

from .models import (
    Form, FormAnswer, FormQuestion, FormResponse, FormSubmissionReceipt,
)

TEXT_TYPES = {FormQuestion.SHORT_TEXT, FormQuestion.LONG_TEXT}
CHOICE_TYPES = {FormQuestion.SINGLE_CHOICE, FormQuestion.MULTI_CHOICE}

MAX_SHORT_TEXT = 300
MAX_LONG_TEXT = 5000


# ── what a student can see ────────────────────────────────────────────────────

def open_forms(today=None, audience=Form.STUDENT):
    """Every form this audience may answer right now.

    Defaults to the student portal, because a staff-completed form appearing in
    a student's list would invite them to evaluate themselves.
    """
    today = today or date.today()
    qs = Form.objects.filter(is_active=True).select_related('academic_year')
    if audience is not None:
        qs = qs.filter(audience=audience)
    return [form for form in qs if form.is_open(today)]


def answered_form_ids(profile):
    if profile is None:
        return set()
    return set(
        FormSubmissionReceipt.objects.filter(profile=profile).values_list('form_id', flat=True)
    )


def forms_for_student(profile, today=None):
    """The open forms, each marked with whether this student has answered it.

    Answered forms are still listed rather than hidden — a student who has
    filled one in wants to see that it is done, not wonder whether it saved.
    """
    answered = answered_form_ids(profile)
    return [
        {'form': form, 'answered': form.id in answered,
         'can_answer': form.allow_multiple or form.id not in answered}
        for form in open_forms(today)
    ]


# ── validating an answer ──────────────────────────────────────────────────────

def _clean_text(value, limit):
    text = '' if value is None else str(value).strip()
    if len(text) > limit:
        raise ValueError(f'Answer is too long (max {limit} characters).')
    return text


def normalise_answer(question, value):
    """Coerce a submitted answer into the shape its question stores.

    Raises ValueError with a message meant for the student, not the developer.
    Returns None when nothing was answered, so the caller can decide whether
    that is allowed.
    """
    label = question.text[:60]

    if question.type == FormQuestion.SHORT_TEXT:
        return _clean_text(value, MAX_SHORT_TEXT) or None

    if question.type == FormQuestion.LONG_TEXT:
        return _clean_text(value, MAX_LONG_TEXT) or None

    if question.type == FormQuestion.SINGLE_CHOICE:
        choice = _clean_text(value, MAX_SHORT_TEXT)
        if not choice:
            return None
        if choice not in question.options:
            raise ValueError(f'"{choice}" is not one of the choices for "{label}".')
        return choice

    if question.type == FormQuestion.MULTI_CHOICE:
        if value in (None, ''):
            return None
        if not isinstance(value, (list, tuple)):
            raise ValueError(f'"{label}" expects a list of choices.')
        chosen = [_clean_text(item, MAX_SHORT_TEXT) for item in value]
        chosen = [item for item in chosen if item]
        for item in chosen:
            if item not in question.options:
                raise ValueError(f'"{item}" is not one of the choices for "{label}".')
        return chosen or None

    if question.type == FormQuestion.MATRIX:
        if not value:
            return None
        if not isinstance(value, dict):
            raise ValueError(f'"{label}" expects a rating for each row.')
        ratings = {}
        for row, choice in value.items():
            row = _clean_text(row, MAX_SHORT_TEXT)
            choice = _clean_text(choice, MAX_SHORT_TEXT)
            if not choice:
                continue
            if row not in question.rows:
                raise ValueError(f'"{row}" is not a row of "{label}".')
            if choice not in question.options:
                raise ValueError(f'"{choice}" is not a rating for "{label}".')
            ratings[row] = choice
        return ratings or None

    if question.type == FormQuestion.GRID_TEXT:
        if not value:
            return None
        if not isinstance(value, (list, tuple)):
            raise ValueError(f'"{label}" expects a table of rows.')
        width = len(question.columns) or 1
        table = []
        for raw_row in value[:question.max_rows or len(value)]:
            if isinstance(raw_row, dict):
                cells = [raw_row.get(column, '') for column in question.columns]
            elif isinstance(raw_row, (list, tuple)):
                cells = list(raw_row)
            else:
                cells = [raw_row]
            cells = [_clean_text(cell, MAX_SHORT_TEXT) for cell in cells[:width]]
            cells += [''] * (width - len(cells))
            if any(cells):                      # drop the rows nobody filled in
                table.append(cells)
        return table or None

    raise ValueError(f'Unknown question type: {question.type}')


# ── submitting ────────────────────────────────────────────────────────────────

@transaction.atomic
def submit(form, answers, *, profile=None, class_level=None, academic_year=None,
           submitted_by=None, today=None):
    """Record one filled-in form.

    `answers` is {question_id: value}. Everything is validated before anything
    is written, so a form is never half-saved.

    On an anonymous form the response is stored with no profile at all, and the
    receipt that stops a second submission is written separately — there is no
    join that would put the two back together.
    """
    if not form.is_open(today):
        raise ValueError('That form is not open for responses.')

    if profile is not None and not form.allow_multiple:
        if FormSubmissionReceipt.objects.filter(form=form, profile=profile).exists():
            raise ValueError('You have already answered this form.')

    questions = list(
        FormQuestion.objects.filter(section__form=form).select_related('section')
    )
    by_id = {question.id: question for question in questions}

    # Keys arrive as JSON object keys, so they are strings. A key that is not a
    # number at all used to raise int()'s own message, which was then shown to
    # the student as "invalid literal for int() with base 10".
    try:
        submitted = {int(key) for key in answers}
    except (TypeError, ValueError):
        raise ValueError('That answer does not belong to this form.')
    if submitted - set(by_id):
        raise ValueError('That answer does not belong to this form.')

    cleaned = {}
    for question in questions:
        raw = answers.get(question.id, answers.get(str(question.id)))
        value = normalise_answer(question, raw)
        if value is None:
            if question.required:
                raise ValueError(f'"{question.text[:80]}" must be answered.')
            continue
        cleaned[question.id] = value

    response = FormResponse.objects.create(
        form=form,
        profile=None if form.is_anonymous else profile,
        class_level=class_level,
        academic_year=academic_year or form.academic_year,
        # Never on an anonymous form: recording the member of staff who filled
        # one in would identify the respondent just as surely as a name.
        submitted_by=None if form.is_anonymous else submitted_by,
    )
    FormAnswer.objects.bulk_create([
        FormAnswer(response=response, question_id=question_id, value=value)
        for question_id, value in cleaned.items()
    ])
    if profile is not None:
        FormSubmissionReceipt.objects.get_or_create(form=form, profile=profile)
    return response


# ── what the answers add up to ────────────────────────────────────────────────

def _mean(pairs):
    """Weighted mean of [(numeric value, count)], to one decimal."""
    total = sum(count for _value, count in pairs)
    if not total:
        return None
    return round(sum(value * count for value, count in pairs) / total, 1)


def _choice_summary(question, values):
    counts = Counter(values)
    numeric = question.numeric_options
    scale = dict(zip(question.options, numeric)) if numeric else {}
    return {
        'counts': [{'option': option, 'count': counts.get(option, 0)}
                   for option in question.options],
        'answered': sum(counts.values()),
        'mean': _mean([(scale[option], counts.get(option, 0)) for option in question.options])
        if scale else None,
    }


def summarise_question(question, answers):
    """One question's results, in the shape a chart and a table both want."""
    values = [answer.value for answer in answers]
    base = {
        'id': question.id,
        'text': question.text,
        'type': question.type,
        'section': question.section.title,
        'answered': len(values),
    }

    if question.type in TEXT_TYPES:
        return {**base, 'kind': 'text',
                'responses': [value for value in values if value]}

    if question.type == FormQuestion.SINGLE_CHOICE:
        return {**base, 'kind': 'choice', **_choice_summary(question, values)}

    if question.type == FormQuestion.MULTI_CHOICE:
        flat = [item for value in values if isinstance(value, list) for item in value]
        return {**base, 'kind': 'choice', **_choice_summary(question, flat)}

    if question.type == FormQuestion.MATRIX:
        numeric = question.numeric_options
        scale = dict(zip(question.options, numeric)) if numeric else {}
        rows = []
        for row in question.rows:
            counts = Counter(
                value[row] for value in values
                if isinstance(value, dict) and value.get(row)
            )
            rows.append({
                'row': row,
                'counts': [{'option': option, 'count': counts.get(option, 0)}
                           for option in question.options],
                'answered': sum(counts.values()),
                'mean': _mean([(scale[option], counts.get(option, 0))
                               for option in question.options]) if scale else None,
            })
        overall = [row['mean'] for row in rows if row['mean'] is not None]
        return {**base, 'kind': 'matrix', 'options': question.options, 'rows': rows,
                'mean': round(sum(overall) / len(overall), 1) if overall else None}

    # grid_text — a table of free text; there is nothing to average, so the
    # rows people actually wrote are the answer.
    table = [row for value in values if isinstance(value, list) for row in value]
    return {**base, 'kind': 'table', 'columns': question.columns, 'rows': table}


def summarise(form):
    """Every question's results, ready for the charts and the summary sheet."""
    questions = list(
        FormQuestion.objects.filter(section__form=form)
        .select_related('section')
        .order_by('section__order', 'order', 'id')
    )
    answers = {}
    for answer in FormAnswer.objects.filter(response__form=form).select_related('question'):
        answers.setdefault(answer.question_id, []).append(answer)

    return {
        'form': {'id': form.id, 'title': form.title, 'slug': form.slug,
                 'status': form.status(), 'is_anonymous': form.is_anonymous},
        'responses': FormResponse.objects.filter(form=form).count(),
        'questions': [summarise_question(q, answers.get(q.id, [])) for q in questions],
    }


# ── the spreadsheet ───────────────────────────────────────────────────────────

def _flatten(question, value):
    """One answer as the cells it occupies in the response sheet."""
    if value is None:
        return [''] * len(export_columns(question))
    if question.type == FormQuestion.MATRIX:
        return [value.get(row, '') if isinstance(value, dict) else '' for row in question.rows]
    if question.type == FormQuestion.MULTI_CHOICE:
        return ['; '.join(value) if isinstance(value, list) else str(value)]
    if question.type == FormQuestion.GRID_TEXT:
        rows = [' | '.join(str(cell) for cell in row) for row in value] \
            if isinstance(value, list) else [str(value)]
        return ['\n'.join(rows)]
    return [str(value)]


def export_columns(question):
    """A rating table needs one column per row rated; everything else needs one."""
    if question.type == FormQuestion.MATRIX:
        return [f'{question.text[:60]} — {row}' for row in question.rows]
    return [question.text[:120]]


def export_workbook(form):
    """The form's responses as a workbook: every answer, then the tallies.

    Two sheets rather than one because they answer different questions —
    "what did people say" and "what does it add up to" — and the college
    reports from the second while checking against the first.
    """
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    header_fill = PatternFill('solid', fgColor='1E2D78')
    header_font = Font(bold=True, color='FFFFFF', size=10)
    wrap = Alignment(horizontal='center', vertical='center', wrap_text=True)

    questions = list(
        FormQuestion.objects.filter(section__form=form)
        .select_related('section').order_by('section__order', 'order', 'id')
    )
    responses = list(
        FormResponse.objects.filter(form=form)
        .select_related('profile', 'class_level', 'academic_year', 'submitted_by')
        .prefetch_related('answers')
        .order_by('submitted_at')
    )

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = 'Responses'

    identity = ['#', 'Submitted']
    if not form.is_anonymous:
        identity += (['Filled in by'] if form.audience == form.STAFF
                     else ['Student', 'Registration no.'])
    identity += ['Level', 'Academic year']

    header = list(identity)
    for question in questions:
        header += export_columns(question)
    sheet.append(header)
    for cell in sheet[1]:
        cell.fill, cell.font, cell.alignment = header_fill, header_font, wrap

    for index, response in enumerate(responses, start=1):
        answers = {answer.question_id: answer.value for answer in response.answers.all()}
        row = [index, response.submitted_at.strftime('%d %b %Y %H:%M')]
        if not form.is_anonymous:
            if form.audience == form.STAFF:
                staff = response.submitted_by
                row += [(staff.get_full_name() or staff.username) if staff else '—']
            else:
                row += [response.profile.name if response.profile else '—',
                        response.profile.nactvet_reg_no if response.profile else '—']
        row += [response.class_level.name if response.class_level else '—',
                response.academic_year.name if response.academic_year else '—']
        for question in questions:
            row += _flatten(question, answers.get(question.id))
        sheet.append(row)

    sheet.freeze_panes = 'A2'
    for column in range(1, len(header) + 1):
        sheet.column_dimensions[get_column_letter(column)].width = 22 if column > len(identity) else 14

    # ── the tally ──
    tally = workbook.create_sheet('Summary')
    tally.append(['Question', 'Row', 'Answer', 'Count', 'Average'])
    for cell in tally[1]:
        cell.fill, cell.font, cell.alignment = header_fill, header_font, wrap

    data = summarise(form)
    for question in data['questions']:
        if question['kind'] == 'choice':
            for entry in question['counts']:
                tally.append([question['text'], '', entry['option'], entry['count'],
                              question['mean'] if question['mean'] is not None else ''])
        elif question['kind'] == 'matrix':
            for row in question['rows']:
                for entry in row['counts']:
                    tally.append([question['text'], row['row'], entry['option'],
                                  entry['count'],
                                  row['mean'] if row['mean'] is not None else ''])
        else:
            tally.append([question['text'], '', 'Free text', question['answered'], ''])

    for column, width in zip('ABCDE', (54, 34, 24, 10, 10)):
        tally.column_dimensions[column].width = width
    tally.freeze_panes = 'A2'
    return workbook
