"""Load BPHACOH's evaluation and request forms.

Transcribed from the college's own documents — the End of Academic Year Course
Evaluation Form, the Tutor's Evaluation Form, the Hostel Facilities and
Services Evaluation Form, the Tracer Study Survey Form, the mentorship and
performance evaluations, and the two introduction letter request forms. Nobody
should have to retype twenty-seven questions into a form builder to start
using this.

Safe to re-run: forms are matched on their slug and updated in place, and the
questions of a form that already has responses are left alone rather than
rebuilt underneath them.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from attendance.models import (
    AcademicYear, ClassLevel, Form, FormQuestion, FormSection,
)

STUDENT = Form.STUDENT
STAFF = Form.STAFF
EVALUATION = Form.EVALUATION
REQUEST = Form.REQUEST

SHORT = FormQuestion.SHORT_TEXT
LONG = FormQuestion.LONG_TEXT
ONE = FormQuestion.SINGLE_CHOICE
MATRIX = FormQuestion.MATRIX
GRID = FormQuestion.GRID_TEXT

EXCELLENT = ['Excellent', 'Good', 'Average', 'Poor', 'Very Poor']
LIKERT = ['Strongly disagree', 'Disagree', 'Neutral', 'Agree', 'Strongly agree']
SATISFACTION = ['Very satisfied', 'Satisfied', 'Neutral', 'Dissatisfied', 'Very Dissatisfied']
NO_YES = ['No', 'Yes']
RECOMMEND = ['Yes', 'No', 'May be']
RATING_5 = ['1', '2', '3', '4', '5']


def q(text, type=ONE, **kw):
    return {'text': text, 'type': type, **kw}


OFFICES = [
    "Principal's office", 'Head of Department office', 'Deputy Principal Academic Office',
    'Deputy Principal Finance, planning and Administration office', 'Accountant office',
    "Dean of students' office",
    "Tutor's self-respect, integrity, and sensitivity to students' issues",
    'Library office', 'Secretary office', 'Quality Assurance Manager office',
    "Estate Manager's office", 'ICT office', 'Examination office', 'Admission office',
    "Gender desk coordinator's office",
]

COLLEGE_SERVICES = [
    'Presence of Well-maintained and adequate classrooms', 'Presence of recreation facilities',
    'Conduciveness of the studying environment', 'Academic advising services',
    'Personal counselling services',
    'Relevance of number of toilets to number of present students', 'Water supply',
    'Health services', 'Campus Safety and Security',
]

TEACHING_EFFECTIVENESS = [
    "Tutor's preparedness on the subject matter",
    "Tutor's attendance in the class and management of time",
    "Tutor's possession of up-to-date skills and knowledge in the subject matter",
    "Tutor's mode of delivery of the subject matter",
    "Tutor's availability for consultations",
    'Organization of materials and lectures',
    'Provision of a class assignment and students responsibilities',
    'Sufficiency of tests and assignments as well as timely feedback',
    'Fairness of grading of assignments and tests',
    'Setting examination/test that captures all aspect of the course unit',
    "Tutor's self-respect, integrity, and sensitivity to students' issues",
    "Tutor's mode of delivery of the subject (demo, techniques, and styles)",
    "Tutor's help to understand career options",
    'A tutor provides you with skills and abilities for your future life outside work',
    'Generally, how do you evaluate the competence of the instructor to meet your '
    'learning satisfaction',
]

HOSTEL_SERVICES = [
    'Environment', 'Electricity facility', 'Recreation facilities', 'Studying environment',
    'Furniture availability', 'Safety and Security', 'Water facility', 'First Aid Kit',
    'Location', 'Food/ Canteen services', 'Cleanness services', 'Patron/Matron services',
]

RATING_HELP = '1 = Poor, 2 = Satisfactory, 3 = Good, 4 = Very Good, 5 = Excellent'

# Mentorship programme
EXCELLENT_POOR = ['Excellent', 'Good', 'Fair', 'Poor']
YES_NO = ['Yes', 'No']
YES_NO_PARTIAL = ['Yes', 'No', 'Partially']
AGREEMENT_4 = ['Strongly Disagree', 'Disagree', 'Agree', 'Strongly Agree']
MENTORSHIP_OUTCOMES = [
    'The mentorship program has contributed to the improvement of my academic performance.',
    'My confidence has improved through the mentorship program.',
    'Networking and communication skills have improved as a result of the mentorship program',
    'Relationship between me and my mentor/mentee has greatly improved with time.',
]

# Students' performance evaluation — filled in by a mentor about a student.
FREQUENCY_5 = ['Always', 'Sometimes', 'Seldom', 'Never', 'N/A']
LEARNING_MOTIVATION = [
    'Able to focus on a topic for a long period of time',
    'Able to learn autonomously and independently',
    'Sustained interest in learning new knowledge',
    'Persistent and refuse to give up when facing difficulties or failure',
]
LEARNING_CHARACTERISTICS = [
    'Seek the "how\'s" and "whys" rather than taking them for granted',
    'Able to understand diagrams by intuition',
    'Exhibit willingness to learn new skills and accept new responsibilities',
    'Carries out assigned duties timely',
    'Demonstrate efficient use of time',
    'Ability to work independently with little or no supervision',
    'Adhere to work schedule (time and attendance)',
    'Adheres to departmental policies and protocols',
]
CREATIVITY = [
    'Demonstrate a keen sense of humor',
    'Willing to attempt, to make assumption and improve',
    'Like to think in different angles',
    'Able to suggest ideas and solutions to various problems',
]
BEHAVIOUR = [
    'Study or participate in activities in accordance with institution',
    'Show courage to ask questions',
    'Able to concentrate on his or her studies',
    'Able to cooperate with classmates',
    'Able to express his or her emotions effectively',
    'Able to listen to others efficiently',
]
LEADERSHIP = [
    'With a strong sense of responsibilities and can be entrusted with tasks',
    'Like to participate in group activities',
    'Able to cooperate with others',
    'Able to communicate with others and express himself/herself clearly',
    'Able to understand other people\'s feeling and needs',
    'Show leadership in various activities',
]

FORMS = [
    {
        'slug': 'course-evaluation',
        'title': 'End of Academic Year Course Evaluation Form',
        'is_anonymous': False,
        'intro': 'Please take a few minutes to complete this evaluation form. Your feedback '
                 'is valuable and will help the college to improve on weak areas in the next '
                 'academic years.',
        'sections': [
            ('Section A: Participant information', '', [
                q('Full Name', SHORT, required=True),
                q('Program name (e.g. PST)', SHORT),
                q('Course Name (NTA level)', SHORT),
                q('Academic year', SHORT),
                q('Email', SHORT),
                q('Phone number', SHORT),
            ]),
            ('Section B: Evaluation questions', '', [
                q('Rate the extent to which the course learning outcomes and related tasks '
                  'were made clear to you by the facilitating tutor.',
                  options=EXCELLENT, required=True),
                q('Were the course materials (notes, books, online resources etc.) provided '
                  'by tutors relevant and helpful towards answering Continuous assessments '
                  '(CAs) and semester examination (SE).', options=NO_YES, required=True),
                q('If the answer to the question above is "No", please mention Modules whose '
                  'course materials were not relevant and helpful towards answering '
                  'Continuous assessments and semester examination.', GRID,
                  columns=['Modules name',
                           'Materials that were irrelevant and not helpful towards '
                           'answering CAs & SE'],
                  max_rows=4),
                q('How do you rate the quality of field practice training provided during '
                  'the academic year', options=EXCELLENT, required=True),
                q('If question 4 above is rated with average to very poor, please mention '
                  'areas that the college needs to modify and if possible, with '
                  'recommendations of such modifications', GRID,
                  columns=['Areas with weakness', 'Recommended Modifications'], max_rows=4),
                q('Were the provided continuous assessments (CATs and Assignment) fair and '
                  'appropriate', options=NO_YES, required=True,
                  help_text='Fairness is defined as an assessment having a mixture of simple '
                            'to complex questions and objectively marked'),
                q('Were the course modules’ contents well organized and easy to follow?',
                  options=NO_YES, required=True),
                q('If the answer to question 7 above is "No", state the module(s) that you '
                  'think its contents were not well organized and not easy to follow, and if '
                  'possible, provide with recommendations.', GRID,
                  columns=['Modules’ code', 'Recommended organization'], max_rows=7),
                q('Rate the ability of facilitators to communicate the course module’s '
                  'contents clearly?', options=EXCELLENT, required=True),
                q('Generally, the facilitators encouraged participation and discussions '
                  'during training', options=LIKERT, required=True),
                q('Generally, the facilitators were available for consultation and support '
                  '(during working hours)', options=LIKERT, required=True),
                q('Generally, the facilitators provided helpful feedback on Continuous '
                  'assessments (Assignments and Continuous Assessment tests)',
                  options=LIKERT, required=True),
                q('You felt engaged and motivated during the whole time of the course '
                  'training', options=LIKERT, required=True),
                q('The course workload was manageable', options=LIKERT, required=True),
                q('You achieved the learning outcomes set for the course',
                  options=LIKERT, required=True),
                q('Rate the effectiveness of the academic advising services?',
                  options=EXCELLENT, required=True),
                q('How do you rate the quality of college library facilities and relevance '
                  'of the reference materials to the course studied?',
                  options=EXCELLENT, required=True),
                q('How do you rate the quality of college’s computer laboratory '
                  'facilities?', options=EXCELLENT, required=True),
                q('How do you rate the quality of college’s compounding laboratory '
                  'facilities?', options=EXCELLENT, required=True),
                q('How are you satisfied with the extracurricular activities conducted '
                  'during the academic year?', options=SATISFACTION, required=True),
                q('How do you rate the social environment and community spirit on campus?',
                  options=EXCELLENT, required=True),
                q('How are you satisfied with your overall college experience?',
                  options=SATISFACTION, required=True),
                q('How are you satisfied with college cleanliness?',
                  options=SATISFACTION, required=True),
                q('How are you satisfied with the services provided by the following college '
                  'offices during undertaking of the course?', MATRIX,
                  help_text='1 = unsatisfactory services, 2 = satisfactory services, '
                            '3 = good services, 4 = better services, 5 = excellent services',
                  options=RATING_5, rows=OFFICES),
                q('Please rate the services provided by the College',
                  MATRIX,
                  help_text='1 = very dissatisfied, 2 = Dissatisfied, 3 = Neutral, '
                            '4 = satisfied, 5 = very satisfied',
                  options=RATING_5, rows=COLLEGE_SERVICES),
                q('Would you recommend Blue Pharma College of Health to others?',
                  options=RECOMMEND, required=True),
                q('Please provide additional comments about the course that you have '
                  'recently completed', LONG),
            ]),
        ],
    },
    {
        'slug': 'tutor-evaluation',
        # Students will not say a tutor was unprepared with their name attached.
        'is_anonymous': True,
        'allow_multiple': True,          # one response per tutor, per module
        'title': "Tutor's Evaluation Form",
        'intro': 'Blue Pharma College of Health aims at providing competency-based education '
                 'that prepares you to be skills-oriented personnel after your graduation. '
                 'Your evaluation, comments and recommendations shall be used to improve '
                 'training only and not any otherwise. Fill this in once for each tutor.',
        'sections': [
            ('Part One: Tutor’s particulars', '', [
                q('Tutor’s Name', SHORT, required=True),
                q('Module Name', SHORT, required=True),
                q('Module Code', SHORT),
                q('Year of study', SHORT),
            ]),
            ('Part Two: Teaching effectiveness', RATING_HELP, [
                q('Teaching Effectiveness', MATRIX, help_text=RATING_HELP,
                  options=RATING_5, rows=TEACHING_EFFECTIVENESS, required=True),
            ]),
            ('Part Three', '', [
                q('Any additional comments (if any)', LONG),
            ]),
        ],
    },
    {
        'slug': 'hostel-evaluation',
        'is_anonymous': True,
        'title': 'Hostel Facilities and Services Evaluation Form',
        'intro': 'In the process of improving services provided, your evaluation, comments '
                 'and recommendations are highly regarded and shall be used to improve '
                 'services and the hostel environment.',
        'sections': [
            ('Part One: Hostel facilities and services', RATING_HELP, [
                q('Hostel facilities and Services', MATRIX, help_text=RATING_HELP,
                  options=RATING_5, rows=HOSTEL_SERVICES, required=True),
            ]),
            ('Part Two', '', [
                q('Any additional comments (if any)', LONG),
            ]),
        ],
    },
    {
        'slug': 'tracer-study',
        'is_anonymous': False,
        'title': 'Tracer Study Survey Form',
        'intro': 'This form gathers feedback from graduates on the academic programme. Your '
                 'genuine and unbiased responses help the college improve the quality of the '
                 'programme. (Section E of the paper form is completed by employers and is '
                 'not part of this portal version.)',
        'sections': [
            ('Section A: Participant information', '', [
                q('Full Name', SHORT, required=True),
                q('Program Name', SHORT),
                q('Year of study/completion', SHORT),
                q('Email', SHORT),
                q('Phone number', SHORT),
            ]),
            ('Section B: Employment status', 'To be filled by college graduates', [
                q('How did the curriculum prepare you for your career or further education?',
                  options=['Very well', 'Well', 'Adequately', 'Poorly', 'Very poorly']),
                q('Are you currently employed?',
                  options=['Yes, full time', 'Yes, part time',
                           'No, but actively seeking for employment',
                           'No, not seeking for employment', 'Self employed']),
                q('Job title', SHORT), q('Name of employer', SHORT),
                q('Industry or Field', SHORT), q('Location', SHORT),
                q('Date of employment', SHORT),
                q('Monthly salary range (Tshs)',
                  options=['100,000 – 200,000', '300,000 – 400,000', '400,000 – 500,000',
                           '500,000 – 600,000', '600,000 – 700,000', '700,000 – 800,000',
                           '800,000 – 900,000', '900,000 and above']),
                q('Is your current job related to your program of study?',
                  options=['Yes', 'No', 'Somehow']),
                q('How long did it take for you to secure your first job after graduation?',
                  options=['Less than three (3) months', '3 – 6 Months', '7 – 12 Months',
                           'Over 12 Months']),
            ]),
            ('Section C: Further studies', '', [
                q('Have you pursued further education since graduating from Blue Pharma '
                  'College of Health?', options=['Yes', 'No']),
                q('Degree/certificate pursued', SHORT),
                q('Institutional Name', SHORT),
                q('Field of Study', SHORT),
                q('Current status', options=['Completed', 'Ongoing']),
                q('Skills application and relevance',
                  options=['Highly relevant', 'Moderately relevant', 'Slightly relevant',
                           'Not relevant']),
                q('Which specific skills learned during your program have you applied in '
                  'your current job?', MATRIX, options=['Yes', 'Not Applicable'],
                  rows=['Technical skills (Dispensing, Compounding, Pharmacology and '
                        'Therapeutics, Medical store keeping etc.)',
                        'Soft skills (communication, team work, leadership etc.)',
                        'Problem solving']),
                q('Others, please specify', SHORT),
            ]),
            ('Section D: Satisfaction and feedback', '', [
                q('Overall, how satisfied are you with the education and training that you '
                  'received at Blue Pharma College of Health?',
                  options=['Very satisfied', 'Satisfied', 'Neutral', 'Dissatisfied',
                           'Very dissatisfied']),
                q('Would you recommend Blue Pharma College of Health to others?',
                  options=RECOMMEND),
                q('Please provide additional comments or suggestions on how the college can '
                  'improve the program?', LONG),
            ]),
        ],
    },
    {
        'slug': 'mentorship-evaluation',
        'title': "Students' Mentorship Program — Feedback and Evaluation Form",
        'audience': STUDENT,
        # The paper form marks the name Optional, which is the college saying
        # people should be able to speak freely. Honour that properly.
        'is_anonymous': True,
        'intro': 'Thank you for participating in the students’ mentorship program for the '
                 'whole academic year. Your feedback helps the office of the Dean of Students '
                 'improve the program.',
        'sections': [
            ('Section A: General Information', '', [
                q('Name (Optional)', SHORT),
                q('Role', options=['Mentor', 'Mentee'], required=True),
                q('Duration of Participation', SHORT),
                q('Date of evaluation', SHORT, help_text='dd/mm/yyyy'),
            ]),
            ('Section B: Program Structure', '', [
                q('How would you rate the overall structure of the mentorship program',
                  options=EXCELLENT_POOR, required=True),
                q('Were the goals and expectations of the mentorship program clearly '
                  'communicated?', options=YES_NO, required=True),
            ]),
            ('Section C: Mentor / Mentee experience', '', [
                q('How would you rate your relationship with your mentor/mentees',
                  options=EXCELLENT_POOR, required=True),
                q('Did you feel supported and heard by your mentor/mentees?',
                  options=YES_NO, required=True),
                q('How often did you meet with your mentor/mentees?',
                  options=['Weekly', 'Bi-weekly', 'Monthly', 'Quarterly']),
                q('Others (Mention)', SHORT),
                q('Were the agenda/topics discussed during your meetings relevant to the '
                  'program and beneficial?', options=YES_NO_PARTIAL, required=True),
            ]),
            ('Section D: Outcomes and Impact', '', [
                q('Did the program help you achieve your professional or personal '
                  'development goals?', options=YES_NO_PARTIAL, required=True),
                q('Please rate the following', MATRIX,
                  options=AGREEMENT_4, rows=MENTORSHIP_OUTCOMES, required=True),
                q('What challenges (if any) did you experience during the program', LONG),
            ]),
            ('Section E: Suggestion for Improvement', '', [
                q('How can the mentorship program be improved?', LONG),
            ]),
        ],
    },
    {
        'slug': 'student-performance-evaluation',
        'title': "Students' Performance Evaluation Form",
        # Filled in by a mentor about a student, so it must never appear in a
        # student's own list — they would be grading themselves.
        'audience': STAFF,
        'is_anonymous': False,
        'allow_multiple': True,          # one per student the mentor assesses
        'intro': 'Please tick the appropriate column for each characteristic. '
                 'N/A means Not Applicable.',
        'sections': [
            ('Student and mentor', '', [
                q('Students Name', SHORT, required=True),
                q('NTA level', SHORT, required=True),
                q('Mentor’s Name', SHORT, required=True),
                q('Evaluation Date', SHORT, help_text='dd/mm/yyyy'),
            ]),
            ('Learning motivation / Attitude', '', [
                q('Learning motivation / Attitude', MATRIX,
                  options=FREQUENCY_5, rows=LEARNING_MOTIVATION, required=True),
            ]),
            ('Learning characteristics', '', [
                q('Learning characteristics', MATRIX,
                  options=FREQUENCY_5, rows=LEARNING_CHARACTERISTICS, required=True),
            ]),
            ('Creativity', '', [
                q('Creativity', MATRIX, options=FREQUENCY_5, rows=CREATIVITY, required=True),
            ]),
            ('Behavioral performance in class', '', [
                q('Behavioral performance in class', MATRIX,
                  options=FREQUENCY_5, rows=BEHAVIOUR, required=True),
            ]),
            ('Leadership', '', [
                q('Leadership', MATRIX, options=FREQUENCY_5, rows=LEADERSHIP, required=True),
            ]),
            ('Assessor', '', [
                q('Other characteristics', LONG),
                q('Solutions for improvement', LONG),
                q('Assessor’s Name', SHORT, required=True),
                q('Date', SHORT, help_text='dd/mm/yyyy'),
            ]),
        ],
    },
    {
        'slug': 'practicum-letter-request',
        'title': 'Introduction Letter to Practicum Facilities Request Form',
        'audience': STUDENT,
        'kind': REQUEST,
        'levels': [4, 5],                # level 6 has its own, longer version below
        'is_anonymous': False,
        'allow_multiple': True,          # a student may request more than once
        'intro': 'Use this form to request an introduction letter from the office of the '
                 'College Principal once you have secured a practicum facility for the '
                 'annual holidays after the semester II examinations. Letters are provided '
                 'to students who have completed at least semester II of NTA level 4. Parts '
                 'D and E of the paper form (HoD recommendation and DPARC approval) are '
                 'completed by the college after you submit.',
        'sections': [
            ('Part A: Student’s Details', '', [
                q('Name', SHORT, required=True),
                q('NACTVET Registration number', SHORT, required=True),
                q('NTA level', SHORT, required=True),
                q('Phone number', SHORT, required=True),
                q('Email', SHORT),
            ]),
            ('Part B: Details of the practicum facility you intend to practice at', '', [
                q('Name of the Facility/Institution', SHORT, required=True),
                q('Registration number', SHORT),
                q('Physical Address', SHORT, required=True),
                q('Postal Address', SHORT),
                q('Official contact — Mobile number', SHORT, required=True),
                q('Official contact — Email', SHORT),
            ]),
            ('Part C: Student’s Declaration', '', [
                q('I declare that the information provided in Part A and Part B above is '
                  'correct to the best of my knowledge, and I understand that the '
                  'introduction letter I am requesting is not a licence to practise as '
                  'Pharmaceutical Personnel.', options=['I agree'], required=True),
                q('Date of declaration', SHORT, help_text='dd/mm/yyyy'),
            ]),
        ],
    },
    {
        'slug': 'practicum-letter-request-level-6',
        'title': 'Introduction Letter Request Form — NTA Level 6',
        'audience': STUDENT,
        'kind': REQUEST,
        'levels': [6],
        'is_anonymous': False,
        'allow_multiple': True,
        'intro': 'This form gives registered students the chance to request a letter of '
                 'introduction to a practicum site for further practical training during the '
                 'long holiday (August–September, after the semester II examinations). It '
                 'collects your details, the practicum facility’s details and those of the '
                 'supervisor at that facility. The college verifies what you submit, and '
                 'issues the introduction letter by email only if your personal information '
                 'is correct, the facility is legally recognised by the relevant authorities, '
                 'and a supervisor with the relevant qualification is present. '
                 'Very important: make sure the facility you intend to attach to is '
                 'registered by the relevant authority, and that there is a qualified '
                 'pharmaceutical personnel there — registered and licensed by the Pharmacy '
                 'Council, and at least one level above your current level of study.',
        'sections': [
            ('Student’s Personal Information',
             'Fill in your personal information correctly. Make sure the email you give here '
             'is valid and actively working — the introduction letter is sent to it.', [
                q('Student’s Name', SHORT, required=True),
                q('Student’s NACTVET Registration Number', SHORT, required=True,
                  help_text='To be filled correctly.'),
                q('Phone number', SHORT, required=True, help_text='e.g. 0682…'),
                q('Email', SHORT, required=True,
                  help_text='e.g. mwanafunzi@gmail.com — the introduction letter is shared '
                            'with you at this address once the information is approved.'),
                q('Academic Program', options=['Pharmaceutical Sciences'], required=True),
                q('Course whose semester II examination you have recently completed',
                  options=['Basic Technician Certificate', 'Technician Certificate',
                           'Ordinary Diploma'], required=True),
                q('Academic year', SHORT, required=True, help_text='e.g. 2025/2026'),
                q('Date of filling this form', SHORT, required=True, help_text='dd/mm/yyyy'),
            ]),
            ('Information of the practicum site you intend to attach to',
             'Fill in correctly the details of the facility you intend to be introduced to '
             'for further practical training during the holiday.', [
                q('Nature of the practicum facility',
                  options=['Pharmacy or ADDO Shop', 'Dispensary', 'Health Centre',
                           'District/Municipal Hospital', 'Regional Referral Hospital',
                           'Zonal/National/Special Hospital',
                           'Tanzania Medicines and Medical Devices Authority (TMDA)',
                           'Medical Store Department', 'Pharmacy Council',
                           'Pharmaceutical Industry'],
                  required=True),
            ]),
            ('Information about the Pharmacy or ADDO Shop',
             'Answer this section only if you chose "Pharmacy or ADDO Shop" above.', [
                q('Where do you intend to be attached?',
                  options=['Community/wholesale Pharmacy', 'ADDO Shop']),
                q('Facility Identification Number (FIN) issued by the Pharmacy Council',
                  SHORT, help_text='For the facility you chose in the question above.'),
            ]),
            ('Information about the Dispensary, Health Centre or Hospital',
             'Answer this section only if you chose a dispensary, health centre or hospital '
             'above.', [
                q('Facility ownership',
                  options=['Public (owned by the Government)',
                           'FBO (owned by a Faith Based Organization)', 'Private']),
                q('Name of the facility', SHORT),
                q('Facility registration number issued by the Ministry of Health', SHORT),
            ]),
            ('Details of the pharmaceutical personnel who will supervise you',
             'Give the pharmaceutical personnel who will physically be at the facility to '
             'supervise your activities as a trainee.', [
                q('Type of pharmaceutical personnel present physically at the facility',
                  options=['Pharmacist', 'Pharmaceutical Technician',
                           'Pharmaceutical Assistant'], required=True),
                q('Their Personal Identification Number (PIN) issued by the Pharmacy Council',
                  SHORT, required=True),
                q('Their mobile number', SHORT, required=True),
            ]),
        ],
    },
    {
        # Transcribed from the college's own Student's Sicksheet Form. Parts A
        # (requesting officer), B (the health facility) and C (Head of
        # Department, Dean of Students) are signed on paper, so they are marked
        # for_office: never shown in the portal, printed blank on the approved
        # document for the people who sign and stamp it.
        'slug': 'sick-sheet-request',
        'title': "Student's Sicksheet Form",
        'audience': STUDENT,
        'kind': REQUEST,
        'allow_multiple': True,          # illness is not a once-a-year event
        'intro': 'Ask the College for a sick sheet so you can consult a health facility and, '
                 'if the medical officer excuses you, have your absence recognised. Say which '
                 'facility you intend to consult. Once the College approves the request you '
                 'download and print the form, take it to the facility for Part B, and bring '
                 'it back for the Head of Department and Dean of Students to approve.',
        'print_note': 'This form must be signed by the consulted Medical Officer and stamped '
                      'with the official health facility stamp.\n'
                      'The days a student is excused from training (if so) may affect their '
                      'attendance percentage (90%) and subsequently lead them to repeat the '
                      'semester.',
        'sections': [
            ('A. REQUEST TO HEALTH FACILITY',
             'To the Medical Officer in charge of the health facility named below. The Blue '
             'Pharma College of Health student named on this form requires to consult your '
             'facility for their health issues.', [
                q('Name of the health facility you intend to consult', SHORT, required=True),
                q('Where the facility is', SHORT,
                  help_text='Town, ward or district — enough for the office to identify it.'),
                q('Date you intend to consult', SHORT, help_text='dd/mm/yyyy'),
            ]),
            ('A. REQUEST TO HEALTH FACILITY — Requested by', '', [
                q('Name of the requesting officer', SHORT),
                q('Signature', SHORT),
                q('Date', SHORT),
            ], True),
            ('B. HEALTH FACILITY DECLARATION — 1. To be filled in case a student is to be '
             'excused from training',
             'I certify that the student named on this form is under treatment and unable to '
             'attend training. I recommend this student to be excused from training for the '
             'days stated below.', [
                q('Number of days excused', SHORT),
                q('From date', SHORT),
                q('To date', SHORT),
                q('Name of Medical Officer', SHORT),
                q('Signature', SHORT),
                q('Date', SHORT),
            ], True),
            ('B. HEALTH FACILITY DECLARATION — 2. To be filled in case a student is not to be '
             'excused from training',
             'I certify that the student named on this form is under treatment and able to '
             'attend training.', [
                q('Name of Medical Officer', SHORT),
                q('Signature', SHORT),
                q('Date', SHORT),
            ], True),
            ('C. APPROVED BY COLLEGE ADMINISTRATION — 1. Head of Department', '', [
                q('Name of Head of Department', SHORT),
                q('Signature', SHORT),
                q('Date', SHORT),
            ], True),
            ('C. APPROVED BY COLLEGE ADMINISTRATION — 2. Dean of Students', '', [
                q('Name of Dean of Students', SHORT),
                q('Signature', SHORT),
                q('Date', SHORT),
            ], True),
        ],
    },
    {
        # Fomu ya Ruhusa ya Wanafunzi, kept in Swahili because that is the
        # language of the college's own form and of the students filling it in.
        'slug': 'student-permission-request',
        'title': 'Fomu ya Ruhusa ya Wanafunzi',
        'audience': STUDENT,
        'kind': REQUEST,
        'allow_multiple': True,
        'intro': 'Omba ruhusa ya kutokuwepo Chuoni kwa muda. Jaza taarifa zako, sababu na '
                 'tarehe unazoomba, kisha tuma maombi. Ruhusa ikikubaliwa, pakua na chapisha '
                 'fomu hii kisha ipeleke kwa Mkuu wa Idara na Muadili wa wanafunzi ili '
                 'isainiwe na kupigwa mhuri.',
        'sections': [
            ('A. TAARIFA ZA MWANAFUNZI', '', [
                q('Kozi unayosoma', SHORT, required=True),
                q('Ngazi', SHORT, required=True, help_text='Mfano: NTA Level 4'),
                q('Muhula', options=['Muhula wa Kwanza', 'Muhula wa Pili'], required=True),
                q('Mwaka wa masomo', SHORT, required=True, help_text='Mfano: 2026/2027'),
                q('Idadi ya siku unazoomba kutokuwepo Chuoni', SHORT, required=True),
                q('Kuanzia tarehe', SHORT, required=True, help_text='siku/mwezi/mwaka'),
                q('Hadi tarehe', SHORT, required=True, help_text='siku/mwezi/mwaka'),
                q('Sababu ya kutokuwepo Chuoni kwa muda huo', LONG, required=True),
            ]),
            ('B. TAMKO',
             'Natamka kuwa nitawajibika kikamilifu kusoma yale ambayo nitakuwa nimeyakosa '
             'katika siku ambazo sitokuwepo chuoni, na kwamba nitatakiwa kurudia kusoma '
             'somo/masomo husika endapo siku hizi ambazo sitohudhuria darasani zitasababisha '
             'kutofikisha asilimia 90% za mahudhurio ya vipindi vya darasani.', [
                q('Natamka na ninakubali tamko hili', options=['Ndiyo, nakubali'],
                  required=True),
            ]),
            ('B. TAMKO — Saini ya mwanafunzi', '', [
                q('Saini', SHORT),
                q('Tarehe', SHORT),
            ], True),
            ('C. IMEPITISHWA NA — i. Mkuu wa Idara', '', [
                q('Maoni', LONG),
                q('Jina', SHORT),
                q('Saini', SHORT),
                q('Tarehe', SHORT),
            ], True),
            ('C. IMEPITISHWA NA — ii. Muadili wa wanafunzi', '', [
                q('Maoni', LONG),
                q('Jina', SHORT),
                q('Saini', SHORT),
                q('Tarehe', SHORT),
            ], True),
        ],
    },
    {
        'slug': 'official-letter-request',
        'title': 'Official Letter Request',
        'audience': STUDENT,
        'kind': REQUEST,
        'allow_multiple': True,          # a student may need several over a year
        'intro': 'Ask the College for a letter addressed to someone outside it — a bank, a '
                 'loans board, an embassy, an employer. Say who the letter is for and what '
                 'it has to confirm; the office prepares it and tells you here when it is '
                 'ready to collect.',
        'sections': [
            ('Your details', '', [
                q('Name', SHORT, required=True),
                q('NACTVET Registration number', SHORT, required=True),
                q('Phone number', SHORT, required=True),
                q('Email', SHORT),
            ]),
            ('The letter', '', [
                q('What letter do you need?',
                  options=['Confirmation that I am a registered student',
                           'Introduction to a bank', 'Letter to the loans board (HESLB)',
                           'Testimonial / letter of good conduct',
                           'Confirmation of results or transcript',
                           'Letter to an employer or attachment site', 'Something else'],
                  required=True),
                q('If something else, what', SHORT),
                q('Who should the letter be addressed to?', SHORT, required=True,
                  help_text='The name of the bank, office or person, as it should be printed.'),
                q('What must the letter confirm?', LONG, required=True),
                q('When do you need it by?', SHORT, help_text='dd/mm/yyyy'),
                q('How will you collect it?',
                  options=['I will collect it from the office', 'Send it to my email']),
            ]),
        ],
    },
    {
        'slug': 'hostel-accommodation-request',
        'title': 'Hostel Accommodation Request',
        'audience': STUDENT,
        'kind': REQUEST,
        'allow_multiple': False,         # one bed, one request, one academic year
        'intro': 'Apply for a bed in the College hostel for this academic year. Applying is '
                 'not the same as being allocated: beds are limited, and the accommodation '
                 'charge is added to your fees only once your place is confirmed.',
        'sections': [
            ('Your details', '', [
                q('Name', SHORT, required=True),
                q('NACTVET Registration number', SHORT, required=True),
                q('Phone number', SHORT, required=True),
                q('Sex', options=['Female', 'Male'], required=True),
            ]),
            ('Your request', '', [
                q('Which part of the year do you need a bed for?',
                  options=['The whole academic year', 'Semester I only', 'Semester II only'],
                  required=True),
                q('Date you would move in', SHORT, help_text='dd/mm/yyyy'),
                q('Have you stayed in the College hostel before?', options=NO_YES,
                  required=True),
                q('Any health or accessibility need we should know about', LONG,
                  help_text='For example a ground-floor room. Leave blank if none.'),
            ]),
            ('Next of kin', 'Who the College should contact in an emergency.', [
                q('Name', SHORT, required=True),
                q('Relationship to you', SHORT, required=True),
                q('Phone number', SHORT, required=True),
            ]),
        ],
    },
]


class Command(BaseCommand):
    help = "Load BPHACOH's evaluation and letter request forms, aimed at the right NTA levels."

    def add_arguments(self, parser):
        parser.add_argument('--year', default=None,
                            help='Attach the forms to this academic year, e.g. 2026/2027.')
        parser.add_argument('--activate', action='store_true',
                            help='Publish them to students straight away. Off by default so '
                                 'the admin can check them over first.')

    @transaction.atomic
    def handle(self, *args, **options):
        year = None
        if options['year']:
            year = AcademicYear.objects.filter(name=options['year']).first()
            if year is None:
                self.stdout.write(self.style.WARNING(
                    f"Academic year {options['year']} does not exist — leaving the forms "
                    f"unattached to a year."))

        for spec in FORMS:
            form, created = Form.objects.update_or_create(
                slug=spec['slug'],
                defaults={
                    'title': spec['title'],
                    'intro': spec['intro'],
                    'academic_year': year,
                    'audience': spec.get('audience', STUDENT),
                    'kind': spec.get('kind', EVALUATION),
                    'print_note': spec.get('print_note', ''),
                    'is_anonymous': spec.get('is_anonymous', False),
                    'allow_multiple': spec.get('allow_multiple', False),
                    'is_active': options['activate'],
                },
            )
            self._set_levels(form, spec.get('levels'))

            if form.responses.exists():
                self.stdout.write(self.style.WARNING(
                    f'  {form.title} — already has responses, questions left untouched.'))
                continue

            # Nobody has answered, so rebuilding is safe and keeps the form
            # exactly in step with the paper document.
            form.sections.all().delete()
            questions = 0
            for order, block in enumerate(spec['sections']):
                # A fourth element marks a part of the paper form that an office
                # fills in — never shown in the portal, printed blank to sign.
                title, description, items = block[:3]
                section = FormSection.objects.create(
                    form=form, title=title, description=description, order=order,
                    for_office=block[3] if len(block) > 3 else False)
                for position, item in enumerate(items):
                    FormQuestion.objects.create(
                        section=section, order=position,
                        text=item['text'], type=item.get('type', ONE),
                        help_text=item.get('help_text', ''),
                        required=item.get('required', False),
                        options=item.get('options', []),
                        rows=item.get('rows', []),
                        columns=item.get('columns', []),
                        max_rows=item.get('max_rows', 4),
                    )
                    questions += 1
            self.stdout.write(
                f'  {"created" if created else "updated"}: {form.title} '
                f'({len(spec["sections"])} sections, {questions} questions)')

        state = 'active' if options['activate'] else 'inactive — publish them when ready'
        services = sum(1 for spec in FORMS if spec.get('kind') == REQUEST)
        self.stdout.write(self.style.SUCCESS(
            f'{len(FORMS)} forms loaded ({len(FORMS) - services} evaluations, '
            f'{services} services students can request), {state}.'))

    def _set_levels(self, form, numbers):
        """Aim a form at the NTA levels that fill it in — all of them if unnamed.

        A missing class level is said out loud rather than passed over: an
        unaimed form goes to every student, which for the level 6 letter request
        is exactly the mistake this field exists to prevent.
        """
        if not numbers:
            form.levels.clear()
            return
        levels = list(ClassLevel.objects.filter(order__in=numbers))
        if len(levels) != len(numbers):
            levels = list(ClassLevel.objects.filter(
                name__in=[f'NTA Level {number}' for number in numbers]))
        if len(levels) != len(numbers):
            self.stdout.write(self.style.WARNING(
                f'  {form.title} — is for NTA level(s) '
                f'{", ".join(str(n) for n in numbers)}, but only '
                f'{len(levels)} of them exist. Set the missing class level up, then '
                f'aim the form on the Forms page.'))
        form.levels.set(levels)
