"""Central academic grading and supplementary-examination rules."""


def is_level_six(class_level):
    name = (getattr(class_level, 'name', '') or '').lower()
    return getattr(class_level, 'order', 0) == 6 or 'level 6' in name or 'lv6' in name


def grade_for_mark(mark, class_level):
    if mark is None:
        return None, None, 'Incomplete'
    mark = float(mark)
    if is_level_six(class_level):
        bands = (
            (75, 'A', 5, 'Excellent'),
            (70, 'B+', 4, 'Very Good'),
            (65, 'B', 3, 'Good'),
            (50, 'C', 2, 'Average'),
            (40, 'D', 1, 'Poor'),
            (0, 'F', 0, 'Fail'),
        )
    else:
        bands = (
            (80, 'A', 4, 'Excellent'),
            (65, 'B', 3, 'Good'),
            (50, 'C', 2, 'Average'),
            (40, 'D', 1, 'Poor'),
            (0, 'F', 0, 'Failure'),
        )
    for minimum, grade, points, description in bands:
        if mark >= minimum:
            return grade, points, description


def gpa_classification(gpa, class_level):
    if gpa is None:
        return 'Incomplete'
    if gpa < 2:
        return 'Discontinuation'
    if is_level_six(class_level):
        if gpa >= 4.5:
            return 'First Class'
        if gpa >= 3.5:
            return 'Upper Second'
        return 'Lower Second'
    if gpa >= 3.5:
        return 'First Class'
    if gpa >= 3:
        return 'Second Class'
    return 'Pass'


def parse_authority_grade(value, class_level):
    """Translate the NACTE grade-key notation into the system outcome fields."""
    raw = str(value or '').strip().upper()
    if not raw:
        raise ValueError('grade is blank')
    if raw == '*N*':
        return {'raw': raw, 'grade': None, 'status': 'NULLIFIED', 'points': None,
                'description': 'Results nullified by authority', 'failed_components': []}
    if raw == '*W*':
        return {'raw': raw, 'grade': None, 'status': 'WITHHELD', 'points': None,
                'description': 'Results withheld by authority', 'failed_components': []}
    if raw == 'FAIL':
        return {'raw': raw, 'grade': None, 'status': 'DISCONTINUED', 'points': 0,
                'description': 'Discontinued from studies', 'failed_components': []}
    if raw == 'NA':
        return {'raw': raw, 'grade': None, 'status': 'NA', 'points': None,
                'description': 'Not applicable', 'failed_components': []}

    grade = raw.rstrip('*').strip()
    suffix = raw[len(grade):]
    valid_grades = {'A', 'B+', 'B', 'C', 'D', 'F'}
    if grade not in valid_grades or suffix not in {'', '*', '**', '***', '****'}:
        raise ValueError(f'invalid authority grade "{raw}"')
    if grade == 'B+' and not is_level_six(class_level):
        raise ValueError('B+ is only valid for Level 6')

    failed_by_suffix = {
        '': [],
        '*': ['semester_exam'],
        '**': ['end_practical'],
        '***': ['end_theory'],
        '****': ['end_theory', 'end_practical'],
    }
    failed_components = failed_by_suffix[suffix]
    points_by_grade = {
        'A': 5 if is_level_six(class_level) else 4,
        'B+': 4, 'B': 3, 'C': 2, 'D': 1, 'F': 0,
    }
    return {
        'raw': raw, 'grade': raw,
        'status': 'SUPP' if failed_components else 'PASS',
        'points': points_by_grade[grade],
        'description': 'Supplementary required by authority' if failed_components else 'Official authority grade',
        'failed_components': failed_components,
    }


def result_outcome(result, serializer):
    """Return the official module result derived from ordinary and supp exams."""
    if result.authority_grade:
        parsed = parse_authority_grade(result.authority_grade, result.student.module.class_level)
        return {
            'end_exam_mark': None, 'supplementary_required': parsed['status'] == 'SUPP',
            'status': parsed['status'], 'grade': parsed['grade'],
            'grade_point': parsed['points'], 'grade_description': parsed['description'],
            'official_total': None, 'failed_end_components': parsed['failed_components'],
        }
    final_total = serializer.get_final_total(result)
    end_fields = ['end_theory'] + (
        ['end_practical'] if result.student.module.has_practical else []
    )
    if serializer.get_total_ca(result) is None or not serializer._complete(result, end_fields):
        return {
            'end_exam_mark': None, 'supplementary_required': False,
            'status': 'INCOMPLETE', 'grade': None, 'grade_point': None,
            'grade_description': 'Incomplete', 'official_total': None,
        }

    end_marks = [float(serializer._mark(result, field)) for field in end_fields]
    end_exam_mark = round(sum(end_marks) / len(end_marks), 2)
    # Every end-of-semester component must independently reach half marks.
    # A strong theory result cannot compensate for a failed practical (or vice
    # versa), even when the weighted final total would otherwise be a high grade.
    failed_components = [
        field for field, mark in zip(end_fields, end_marks) if mark < 50
    ]
    supplementary_required = bool(failed_components)

    if supplementary_required:
        if result.supplementary_mark is None:
            return {
                'end_exam_mark': end_exam_mark, 'supplementary_required': True,
                'status': 'SUPP', 'grade': None, 'grade_point': None,
                'grade_description': 'Supplementary required',
                'official_total': final_total,
                'failed_end_components': failed_components,
            }
        if float(result.supplementary_mark) < 50:
            return {
                'end_exam_mark': end_exam_mark, 'supplementary_required': True,
                'status': 'REPEAT', 'grade': 'F', 'grade_point': 0,
                'grade_description': 'Failed supplementary examination',
                'official_total': final_total,
                'failed_end_components': failed_components,
            }
        return {
            'end_exam_mark': end_exam_mark, 'supplementary_required': True,
            'status': 'PASS', 'grade': 'C', 'grade_point': 2,
            'grade_description': 'Pass after supplementary (grade capped at C)',
            'official_total': final_total,
            'failed_end_components': failed_components,
        }

    grade, points, description = grade_for_mark(final_total, result.student.module.class_level)
    return {
        'end_exam_mark': end_exam_mark, 'supplementary_required': False,
        'status': 'PASS' if final_total >= 50 else 'FAIL',
        'grade': grade, 'grade_point': points,
        'grade_description': description, 'official_total': final_total,
        'failed_end_components': [],
    }
