from decimal import Decimal

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class AcademicYear(models.Model):
    name = models.CharField(max_length=9, unique=True)   # e.g. "2025/2026"
    is_active = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-name']

    def __str__(self):
        return self.name

    @property
    def next_name(self):
        y1, y2 = self.name.split('/')
        return f"{int(y1)+1}/{int(y2)+1}"


class Semester(models.Model):
    SEM1 = 1
    SEM2 = 2
    NUMBER_CHOICES = [(SEM1, 'Semester 1'), (SEM2, 'Semester 2')]

    academic_year = models.ForeignKey(AcademicYear, on_delete=models.CASCADE, related_name='semesters')
    number = models.PositiveSmallIntegerField(choices=NUMBER_CHOICES)
    is_active = models.BooleanField(default=False)

    # Attendance cutoff dates — teachers cannot record attendance after each date
    cat1_cutoff = models.DateField(null=True, blank=True, verbose_name='CAT 1 Attendance Cutoff')
    cat2_cutoff = models.DateField(null=True, blank=True, verbose_name='CAT 2 Attendance Cutoff')
    end_cutoff  = models.DateField(null=True, blank=True, verbose_name='End-of-Semester Attendance Cutoff')

    class Meta:
        unique_together = ('academic_year', 'number')
        ordering = ['academic_year__name', 'number']

    def __str__(self):
        return f"{self.academic_year.name} — Semester {self.number}"

    @property
    def label(self):
        return f"Sem {self.number} · {self.academic_year.name}"


class ClassLevel(models.Model):
    name = models.CharField(max_length=100, unique=True)
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ['order', 'name']

    def __str__(self):
        return self.name


class TeacherProfile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='profile')
    full_name = models.CharField(max_length=200)

    def __str__(self):
        return self.full_name


class AccountantProfile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='accountant_profile')
    full_name = models.CharField(max_length=200)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.full_name


class Module(models.Model):
    name = models.CharField(max_length=200)
    code = models.CharField(max_length=50)
    teacher = models.CharField(max_length=200)
    class_level = models.ForeignKey(ClassLevel, on_delete=models.PROTECT, related_name='modules')
    semester = models.ForeignKey(Semester, on_delete=models.PROTECT, related_name='modules')
    has_practical = models.BooleanField(
        default=False,
        verbose_name='Has Practical Component',
        help_text='Enable for modules assessed with both theory and practical components.',
    )
    credits = models.PositiveSmallIntegerField(
        default=1,
        validators=[MinValueValidator(1)],
        help_text='Module credits used in the weighted GPA calculation.',
    )
    teachers = models.ManyToManyField(
        settings.AUTH_USER_MODEL, related_name='modules_taught', blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('code', 'semester')
        ordering = ['semester__academic_year__name', 'semester__number', 'class_level__order', 'name']

    def __str__(self):
        return f"{self.code} – {self.name}"


class Student(models.Model):
    nactvet_reg_no = models.CharField(max_length=50, verbose_name='NACTVET Reg. No.')
    name = models.CharField(max_length=200)
    module = models.ForeignKey(Module, on_delete=models.CASCADE, related_name='students')
    portal_pin_hash = models.CharField(max_length=128, blank=True, editable=False)
    must_change_portal_password = models.BooleanField(default=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('nactvet_reg_no', 'module')
        ordering = ['name']

    def __str__(self):
        return f"{self.nactvet_reg_no} – {self.name}"

    @property
    def has_portal_pin(self):
        return bool(self.portal_pin_hash)

    def set_portal_pin(self, raw_pin, *, require_change=True):
        self.portal_pin_hash = make_password(str(raw_pin))
        self.must_change_portal_password = require_change

    def check_portal_pin(self, raw_pin):
        return bool(self.portal_pin_hash) and check_password(str(raw_pin), self.portal_pin_hash)


class PaymentCategory(models.Model):
    SCHOOL_FEES = 'school_fees'
    SPECIAL_EXAM = 'special_exam'
    SUPP_EXAM = 'supp_exam'
    REPEAT_MODULE = 'repeat_module'
    DISCONTINUATION = 'discontinuation'
    OTHER = 'other'
    TYPE_CHOICES = [
        (SCHOOL_FEES, 'School Fees'),
        (SPECIAL_EXAM, 'Special Exam'),
        (SUPP_EXAM, 'Supplementary Exam'),
        (REPEAT_MODULE, 'Repeat Module'),
        (DISCONTINUATION, 'Discontinuation'),
        (OTHER, 'Other Payment'),
    ]

    name = models.CharField(max_length=160)
    category_type = models.CharField(max_length=20, choices=TYPE_CHOICES, default=OTHER)
    semester = models.ForeignKey(Semester, on_delete=models.PROTECT, null=True, blank=True, related_name='payment_categories')
    class_level = models.ForeignKey(ClassLevel, on_delete=models.PROTECT, null=True, blank=True, related_name='payment_categories')
    default_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    installment_count = models.PositiveSmallIntegerField(
        default=1,
        validators=[MinValueValidator(1), MaxValueValidator(5)],
        help_text='Maximum number of installments allowed for this category and level.',
    )
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='payment_categories_created',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['category_type', 'name']
        unique_together = ('name', 'category_type')

    def __str__(self):
        return f"{self.get_category_type_display()} – {self.name}"


class StudentFinanceObligation(models.Model):
    SPECIAL_EXAM = 'special_exam'
    SUPP_EXAM = 'supp_exam'
    REPEAT_MODULE = 'repeat_module'
    DISCONTINUATION = 'discontinuation'
    OBLIGATION_CHOICES = [
        (SPECIAL_EXAM, 'Special Exam'),
        (SUPP_EXAM, 'Supplementary Exam'),
        (REPEAT_MODULE, 'Repeat Module'),
        (DISCONTINUATION, 'Discontinuation'),
    ]

    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='finance_obligations')
    semester = models.ForeignKey(Semester, on_delete=models.PROTECT, related_name='finance_obligations')
    module = models.ForeignKey(Module, on_delete=models.PROTECT, null=True, blank=True, related_name='finance_obligations')
    obligation_type = models.CharField(max_length=20, choices=OBLIGATION_CHOICES)
    category = models.ForeignKey(
        PaymentCategory,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='obligations',
    )
    amount_required = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    note = models.CharField(max_length=300, blank=True)
    declared_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='finance_obligations_declared',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    @property
    def amount_paid(self):
        return sum(payment.amount_paid for payment in self.payments.all())

    @property
    def balance(self):
        return self.amount_required - self.amount_paid

    @property
    def is_finance_cleared(self):
        return self.amount_required == 0 or self.balance <= 0

    def __str__(self):
        return f"{self.student.nactvet_reg_no} – {self.get_obligation_type_display()}"


class StudentPayment(models.Model):
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='payments')
    category = models.ForeignKey(PaymentCategory, on_delete=models.PROTECT, related_name='payments')
    obligation = models.ForeignKey(
        StudentFinanceObligation, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='payments',
    )
    amount_required = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    amount_paid = models.DecimalField(max_digits=12, decimal_places=2)
    installment_number = models.PositiveSmallIntegerField(
        default=1,
        validators=[MinValueValidator(1), MaxValueValidator(5)],
    )
    payment_date = models.DateField()
    reference = models.CharField(max_length=100, blank=True)
    note = models.CharField(max_length=300, blank=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='student_payments_recorded',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-payment_date', '-created_at']

    @property
    def balance(self):
        return self.amount_required - self.amount_paid

    def __str__(self):
        return f"{self.student.nactvet_reg_no} – {self.category.name} – {self.amount_paid}"


class StudentFinanceClearance(models.Model):
    CAT1 = 'cat1'
    CAT2 = 'cat2'
    END = 'end'
    REGISTRATION = 'registration'
    PERIOD_CHOICES = [
        (CAT1, 'CAT 1'),
        (CAT2, 'CAT 2'),
        (END, 'End of Semester'),
        (REGISTRATION, 'Registration / Results'),
    ]

    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='finance_clearances')
    semester = models.ForeignKey(Semester, on_delete=models.PROTECT, related_name='finance_clearances')
    period = models.CharField(max_length=20, choices=PERIOD_CHOICES)
    is_cleared = models.BooleanField(default=False)
    note = models.CharField(max_length=300, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='finance_clearances_approved',
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('student', 'semester', 'period')
        ordering = ['student__name', 'period']

    def __str__(self):
        return f"{self.student.nactvet_reg_no} – {self.get_period_display()} – {self.is_cleared}"


class Session(models.Model):
    THEORY = 'T'
    PRACTICAL = 'P'
    TYPE_CHOICES = [(THEORY, 'Theory'), (PRACTICAL, 'Practical')]

    CAT1 = 'C1'
    CAT2 = 'C2'
    GENERAL = 'GN'
    PERIOD_CHOICES = [
        (CAT1, 'CAT 1'),
        (CAT2, 'CAT 2'),
        (GENERAL, 'General'),
    ]

    module = models.ForeignKey(Module, on_delete=models.CASCADE, related_name='sessions')
    session_type = models.CharField(max_length=1, choices=TYPE_CHOICES, default=THEORY, verbose_name='Session Type')
    exam_period = models.CharField(
        max_length=2, choices=PERIOD_CHOICES, default=GENERAL,
        verbose_name='Exam Period',
        help_text='Tag this session to a specific assessment period for eligibility tracking.',
    )
    date = models.DateField()
    label = models.CharField(max_length=200)
    topic = models.CharField(max_length=300, blank=True, verbose_name='Topic Taught')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date', '-created_at']

    def __str__(self):
        return f"{self.module.code} | {self.get_session_type_display()} | {self.date} – {self.label}"


class AttendanceRecord(models.Model):
    PRESENT = 'P'
    ABSENT = 'A'
    SICK = 'S'
    STATUS_CHOICES = [
        (PRESENT, 'Present'),
        (ABSENT, 'Absent'),
        (SICK, 'Sick (Permitted)'),
    ]

    session = models.ForeignKey(Session, on_delete=models.CASCADE, related_name='records')
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='attendance_records')
    status = models.CharField(max_length=1, choices=STATUS_CHOICES, default=PRESENT)
    sick_note = models.CharField(max_length=300, blank=True, verbose_name='Sick Note / Reason')
    certificate_submitted = models.BooleanField(default=False, verbose_name='Certificate Submitted')

    class Meta:
        unique_together = ('session', 'student')

    def __str__(self):
        return f"{self.student.nactvet_reg_no} @ {self.session} = {self.get_status_display()}"


def _mark_field(**kwargs):
    """Raw mark field: 0–100, nullable (not yet entered)."""
    validators = [MinValueValidator(Decimal('0')), MaxValueValidator(Decimal('100'))]
    return models.DecimalField(
        max_digits=5, decimal_places=2,
        null=True, blank=True,
        validators=validators,
        **kwargs,
    )


class StudentResult(models.Model):
    """
    CA + End-of-Semester marks for one student.

    CA weights (40 % total):
        Theory-only : A1→5% A2→5% CAT1→15% CAT2→15%
        Theory+Prac : A1→2% A2→2% CAT1-T→8% CAT2-T→8% P1→10% P2→10%

    End-of-Semester exam weights (60 % total):
        Theory-only : end_theory → 60 %
        Theory+Prac : end_theory → 30 %   end_practical → 30 %

    CA eligibility (50 % of 40):
        Theory-only : total_ca ≥ 20
        Theory+Prac : theory_ca ≥ 10  AND  practical_ca ≥ 10

    Final total = total_ca + end exam weighted  (max 100)
    """

    student        = models.OneToOneField(Student, on_delete=models.CASCADE, related_name='result')
    assign1        = _mark_field(verbose_name='Assignment 1 (raw /100)')
    assign2        = _mark_field(verbose_name='Assignment 2 (raw /100)')
    cat1_theory    = _mark_field(verbose_name='CAT 1 – Theory (raw /100)')
    cat2_theory    = _mark_field(verbose_name='CAT 2 – Theory (raw /100)')
    cat1_practical = _mark_field(verbose_name='Practical Test 1 (raw /100)')
    cat2_practical = _mark_field(verbose_name='Practical Test 2 (raw /100)')
    end_theory     = _mark_field(verbose_name='End of Semester – Theory/Written (raw /100)')
    end_practical  = _mark_field(verbose_name='End of Semester – Practical (raw /100)')
    supplementary_mark = _mark_field(
        verbose_name='Supplementary Examination (raw /100)'
    )
    assign1_absent        = models.BooleanField(default=False)
    assign2_absent        = models.BooleanField(default=False)
    cat1_theory_absent    = models.BooleanField(default=False)
    cat2_theory_absent    = models.BooleanField(default=False)
    cat1_practical_absent = models.BooleanField(default=False)
    cat2_practical_absent = models.BooleanField(default=False)
    end_theory_absent     = models.BooleanField(default=False)
    end_practical_absent  = models.BooleanField(default=False)
    ca_approved    = models.BooleanField(default=False)
    final_approved = models.BooleanField(default=False)
    updated_at     = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['student__name']

    def __str__(self):
        return f'Result: {self.student}'
