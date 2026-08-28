from decimal import Decimal

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.core.validators import FileExtensionValidator, MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q


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


class EstateOfficerProfile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='estate_officer_profile')
    full_name = models.CharField(max_length=200)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.full_name


class InventoryLocation(models.Model):
    LOCATION_TYPE_CHOICES = [
        ('office', 'College Offices'), ('classroom', 'Classrooms'),
        ('lab', 'Laboratories'), ('other', 'Other Areas'),
    ]
    name = models.CharField(max_length=160, unique=True)
    location_type = models.CharField(max_length=20, choices=LOCATION_TYPE_CHOICES, default='other')
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class AssetCategory(models.Model):
    name = models.CharField(max_length=160, unique=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']
        verbose_name_plural = 'asset categories'

    def __str__(self):
        return self.name


class InventoryItemType(models.Model):
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    category = models.ForeignKey(AssetCategory, on_delete=models.PROTECT, related_name='item_types')
    default_tag_prefix = models.CharField(max_length=100, unique=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']
        constraints = [models.UniqueConstraint(fields=['name', 'category'], name='unique_inventory_item_type_category')]

    def __str__(self):
        return self.name


class Asset(models.Model):
    NEW = 'new'
    GOOD = 'good'
    FAIR = 'fair'
    POOR = 'poor'
    UNSERVICEABLE = 'unserviceable'
    CONDITION_CHOICES = [
        (NEW, 'New'), (GOOD, 'Good'), (FAIR, 'Fair'),
        (POOR, 'Poor'), (UNSERVICEABLE, 'Unserviceable'),
    ]

    asset_tag = models.CharField(max_length=100, unique=True, verbose_name='Asset number/tag')
    name = models.CharField(max_length=200, verbose_name='Asset name')
    description = models.TextField(blank=True)
    category = models.ForeignKey(AssetCategory, on_delete=models.PROTECT, related_name='assets')
    item_type = models.ForeignKey(InventoryItemType, on_delete=models.PROTECT, null=True, blank=True, related_name='assets')
    location = models.ForeignKey(InventoryLocation, on_delete=models.PROTECT, related_name='assets')
    responsible_office = models.CharField(max_length=200, verbose_name='Person/office responsible')
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    condition = models.CharField(max_length=20, choices=CONDITION_CHOICES)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='assets_created')
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='assets_updated')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['asset_tag']

    def __str__(self):
        return f'{self.asset_tag} – {self.name}'


class AssetImport(models.Model):
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='asset_imports')
    file_name = models.CharField(max_length=255)
    imported_rows = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


class AssetTransfer(models.Model):
    asset = models.ForeignKey(Asset, on_delete=models.PROTECT, related_name='transfers')
    from_location = models.ForeignKey(InventoryLocation, on_delete=models.PROTECT, related_name='transfers_from')
    to_location = models.ForeignKey(InventoryLocation, on_delete=models.PROTECT, related_name='transfers_to')
    quantity = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])
    resulting_asset = models.ForeignKey(Asset, on_delete=models.PROTECT, null=True, blank=True, related_name='split_from_transfers')
    new_responsible_office = models.CharField(max_length=200)
    reason = models.TextField()
    transferred_at = models.DateField()
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='asset_transfers_recorded')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-transferred_at', '-created_at']


class AssetMaintenance(models.Model):
    REPORTED = 'reported'
    IN_PROGRESS = 'in_progress'
    COMPLETED = 'completed'
    STATUS_CHOICES = [(REPORTED, 'Reported'), (IN_PROGRESS, 'In progress'), (COMPLETED, 'Completed')]
    asset = models.ForeignKey(Asset, on_delete=models.PROTECT, related_name='maintenance_records')
    quantity = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])
    issue = models.TextField()
    action_taken = models.TextField(blank=True)
    provider = models.CharField(max_length=200, blank=True)
    cost = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=REPORTED)
    reported_date = models.DateField()
    completed_date = models.DateField(null=True, blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='asset_maintenance_recorded')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-reported_date', '-created_at']


class InventoryInspection(models.Model):
    OPEN = 'open'
    CLOSED = 'closed'
    STATUS_CHOICES = [(OPEN, 'Open'), (CLOSED, 'Closed')]
    location = models.ForeignKey(InventoryLocation, on_delete=models.PROTECT, related_name='inspections')
    inspection_date = models.DateField()
    inspector_name = models.CharField(max_length=200)
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=OPEN)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='inventory_inspections_recorded')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-inspection_date', '-created_at']


class InventoryInspectionItem(models.Model):
    FOUND = 'found'
    MISSING = 'missing'
    DAMAGED = 'damaged'
    RELOCATED = 'relocated'
    RESULT_CHOICES = [(FOUND, 'Found'), (MISSING, 'Missing'), (DAMAGED, 'Damaged'), (RELOCATED, 'Relocated')]
    inspection = models.ForeignKey(InventoryInspection, on_delete=models.CASCADE, related_name='items')
    asset = models.ForeignKey(Asset, on_delete=models.PROTECT, related_name='inspection_items')
    result = models.CharField(max_length=20, choices=RESULT_CHOICES)
    note = models.TextField(blank=True)

    class Meta:
        unique_together = ('inspection', 'asset')
        ordering = ['asset__asset_tag']


class AssetDisposal(models.Model):
    PROPOSED = 'proposed'
    DISPOSED = 'disposed'
    STATUS_CHOICES = [(PROPOSED, 'Proposed'), (DISPOSED, 'Disposed')]
    asset = models.OneToOneField(Asset, on_delete=models.PROTECT, related_name='disposal')
    reason = models.TextField()
    method = models.CharField(max_length=160, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=PROPOSED)
    proposed_date = models.DateField()
    disposal_date = models.DateField(null=True, blank=True)
    reference = models.CharField(max_length=160, blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='asset_disposals_recorded')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-proposed_date', '-created_at']


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
    is_field_module = models.BooleanField(
        default=False,
        verbose_name='Field Results Module',
        help_text='Use one CA mark weighted to 40% and one final mark weighted to 60%.',
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
    """One enrollment: this person, in this module. Attendance and results hang
    off it, and always have.

    Money does not. Fees, invoices and clearance attach to `profile` — the
    person — because a student taking eight modules is eight rows here and
    owes one balance, not eight. See StudentProfile.
    """
    nactvet_reg_no = models.CharField(max_length=50, verbose_name='NACTVET Reg. No.')
    name = models.CharField(max_length=200)
    profile = models.ForeignKey(
        'StudentProfile', on_delete=models.CASCADE, null=True, blank=True, related_name='enrollments',
    )
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
        constraints = [
            models.UniqueConstraint(
                fields=['module', 'session_type', 'exam_period', 'date', 'label'],
                name='unique_attendance_session',
            ),
        ]

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
    field_ca       = _mark_field(verbose_name='Field CA (raw /100)')
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
    authority_grade = models.CharField(max_length=20, blank=True, default='')
    authority_status = models.CharField(max_length=20, blank=True, default='')
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


# ── FEES LEDGER ───────────────────────────────────────────────────────────────
#
# The models above (PaymentCategory, StudentPayment, StudentFinanceObligation,
# StudentFinanceClearance) hang finance off `Student`, which is an *enrollment*
# — one row per module — so a student taking eight modules carried eight
# separate balances that never added up. Everything below replaces them.
#
# The shape is a receivables ledger:
#
#     ChargeType    what the college can charge for       (catalogue)
#     FeeStructure  how much, and in how many installments, per NTA level
#     StudentCharge a debt owed by one person             (debit)
#     Payment       money received, verified at the counter (credit)
#     balance       = charges − waivers − payments        (never stored)
#
# Money attaches to StudentProfile — the person — not to an enrollment.


class StudentProfile(models.Model):
    """One row per human being, keyed on the registration number.

    `Student` remains the per-module enrollment it has always been, and keeps
    carrying attendance and results. It gains a pointer here so that fees,
    invoices and clearance can attach to the person instead of being
    duplicated across every module they take.
    """
    nactvet_reg_no = models.CharField(max_length=50, unique=True, verbose_name='NACTVET Reg. No.')
    name = models.CharField(max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f'{self.nactvet_reg_no} – {self.name}'


class ChargeType(models.Model):
    """WHAT the college can charge for. One row per item, college-wide.

    Amounts and installment counts live on FeeStructure instead, because both
    differ by NTA level — Level 4 pays fees over 5 installments where Levels 5
    and 6 pay over 4.
    """
    FEE = 'fee'
    DIRECT_COST = 'direct_cost'
    OTHER = 'other'
    FAMILY_CHOICES = [
        (FEE, 'School Fees'),
        (DIRECT_COST, 'Direct Costs'),
        (OTHER, 'Other Payments'),
    ]

    AUTOMATIC = 'automatic'
    OPTIONAL = 'optional'
    ON_REQUEST = 'on_request'
    APPLIES_CHOICES = [
        (AUTOMATIC, 'Every student in the level'),
        (OPTIONAL, 'Only students assigned it (hostel, field trips)'),
        (ON_REQUEST, 'Only when the college declares it (supplementary, repeat)'),
    ]

    name = models.CharField(max_length=160, unique=True)
    family = models.CharField(max_length=20, choices=FAMILY_CHOICES, default=FEE)
    applies = models.CharField(max_length=20, choices=APPLIES_CHOICES, default=AUTOMATIC)

    # What non-payment prevents. Set by the accountant — this is the college's
    # exam-eligibility policy, and it is deliberately not hardcoded: tuition
    # should block an exam, a graduation gown should not.
    blocks_registration = models.BooleanField(default=False, verbose_name='Blocks registration')
    blocks_cat1 = models.BooleanField(default=False, verbose_name='Blocks CAT 1')
    blocks_cat2 = models.BooleanField(default=False, verbose_name='Blocks CAT 2')
    blocks_final = models.BooleanField(default=False, verbose_name='Blocks end-of-semester exam')
    blocks_results = models.BooleanField(default=False, verbose_name='Blocks results release')

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    # Period keys used by the clearance service, mapped to the flags above.
    REGISTRATION = 'registration'
    CAT1 = 'cat1'
    CAT2 = 'cat2'
    FINAL = 'final'
    RESULTS = 'results'
    PERIOD_FIELDS = {
        REGISTRATION: 'blocks_registration',
        CAT1: 'blocks_cat1',
        CAT2: 'blocks_cat2',
        FINAL: 'blocks_final',
        RESULTS: 'blocks_results',
    }

    class Meta:
        ordering = ['family', 'name']

    def __str__(self):
        return self.name

    def blocks_period(self, period):
        field = self.PERIOD_FIELDS.get(period)
        return bool(field and getattr(self, field))


class FeeStructure(models.Model):
    """HOW MUCH, and in how many installments, for one charge type at one NTA
    level in one academic year. This is the grid the accountant fills in."""
    ACADEMIC_YEAR = 'academic_year'
    SEMESTER = 'semester'
    PERIOD_CHOICES = [
        (ACADEMIC_YEAR, 'Once per academic year'),
        (SEMESTER, 'Once per semester'),
    ]

    charge_type = models.ForeignKey(ChargeType, on_delete=models.PROTECT, related_name='fee_structures')
    class_level = models.ForeignKey(ClassLevel, on_delete=models.PROTECT, related_name='fee_structures')
    academic_year = models.ForeignKey(AcademicYear, on_delete=models.PROTECT, related_name='fee_structures')
    amount = models.DecimalField(
        max_digits=12, decimal_places=2,
        validators=[MinValueValidator(Decimal('0.00'))],
    )
    billing_period = models.CharField(max_length=20, choices=PERIOD_CHOICES, default=ACADEMIC_YEAR)
    installments = models.PositiveSmallIntegerField(
        default=1,
        validators=[MinValueValidator(1), MaxValueValidator(12)],
        help_text='Level 4 fees are paid over 5; Levels 5 and 6 over 4.',
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['academic_year__name', 'class_level__order', 'charge_type__family', 'charge_type__name']
        constraints = [
            models.UniqueConstraint(
                fields=['charge_type', 'class_level', 'academic_year'],
                name='unique_fee_structure_cell',
            ),
        ]

    def __str__(self):
        return f'{self.charge_type} · {self.class_level} · {self.academic_year} = {self.amount}'


class FeeInstallment(models.Model):
    """One installment of a FeeStructure, with the date it falls due.

    Due dates are what exam clearance is measured against — a student is
    cleared when everything *due by the exam* is settled, not when the whole
    year's bill is settled.
    """
    fee_structure = models.ForeignKey(FeeStructure, on_delete=models.CASCADE, related_name='installment_schedule')
    number = models.PositiveSmallIntegerField(validators=[MinValueValidator(1)])
    amount = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal('0.00'))])
    due_date = models.DateField()

    class Meta:
        ordering = ['fee_structure', 'number']
        constraints = [
            models.UniqueConstraint(
                fields=['fee_structure', 'number'],
                name='unique_fee_installment_number',
            ),
        ]

    def __str__(self):
        return f'{self.fee_structure.charge_type} inst. {self.number} due {self.due_date}'


class StudentCharge(models.Model):
    """A debt owed by one student. The entity the old model was missing.

    `amount_required` used to be stamped onto every payment row and summed,
    which reported a fully-paid student as owing three times the fee. A charge
    is recorded once; payments allocate against it.
    """
    STRUCTURE = 'structure'
    ON_REQUEST = 'on_request'
    CARRY_FORWARD = 'carry_forward'
    SOURCE_CHOICES = [
        (STRUCTURE, 'Generated from the fee structure'),
        (ON_REQUEST, 'Raised on request'),
        (CARRY_FORWARD, 'Brought forward from a previous year'),
    ]

    profile = models.ForeignKey(StudentProfile, on_delete=models.CASCADE, related_name='charges')
    charge_type = models.ForeignKey(ChargeType, on_delete=models.PROTECT, related_name='charges')
    academic_year = models.ForeignKey(AcademicYear, on_delete=models.PROTECT, related_name='charges')
    # Null for charges billed once per academic year rather than per semester.
    semester = models.ForeignKey(Semester, on_delete=models.PROTECT, null=True, blank=True, related_name='charges')
    fee_structure = models.ForeignKey(
        FeeStructure, on_delete=models.SET_NULL, null=True, blank=True, related_name='charges',
    )
    installment_number = models.PositiveSmallIntegerField(default=1, validators=[MinValueValidator(1)])
    amount = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal('0.00'))])
    due_date = models.DateField()
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default=STRUCTURE)

    # A waiver reduces what is owed without pretending money arrived, so a
    # bursary never looks like a payment in the collections report.
    waived_amount = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
    )
    waived_reason = models.CharField(max_length=300, blank=True)
    waived_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='charges_waived',
    )

    note = models.CharField(max_length=300, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='charges_created',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['due_date', 'charge_type__family', 'charge_type__name', 'installment_number']
        indexes = [
            models.Index(fields=['profile', 'academic_year']),
            models.Index(fields=['due_date']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['profile', 'charge_type', 'academic_year', 'semester', 'installment_number'],
                name='unique_student_charge_installment',
            ),
        ]

    def __str__(self):
        return f'{self.profile.nactvet_reg_no} – {self.charge_type} inst. {self.installment_number}'

    @property
    def payable(self):
        """What is actually owed once any waiver is taken off."""
        return self.amount - self.waived_amount


class Invoice(models.Model):
    """A payment instruction the student takes to the bank.

    The reference is the whole trick: the student writes it on the CRDB slip,
    so when they return to the counter the paper itself says who paid and what
    for. No bank integration is involved — it is a college-side convention read
    by the accountant, not by CRDB.
    """
    STUDENT = 'student'
    OFFICE = 'office'
    SOURCE_CHOICES = [(STUDENT, 'Generated by the student'), (OFFICE, 'Raised at the office')]

    profile = models.ForeignKey(StudentProfile, on_delete=models.CASCADE, related_name='invoices')
    reference = models.CharField(max_length=32, unique=True, editable=False)
    academic_year = models.ForeignKey(AcademicYear, on_delete=models.PROTECT, related_name='invoices')
    issued_on = models.DateField(auto_now_add=True)
    due_date = models.DateField(null=True, blank=True)
    source = models.CharField(max_length=10, choices=SOURCE_CHOICES, default=STUDENT)
    cancelled = models.BooleanField(default=False)
    cancelled_reason = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['profile', '-created_at'])]

    def __str__(self):
        return self.reference

    @property
    def total(self):
        return sum((line.amount for line in self.lines.all()), Decimal('0.00'))


class InvoiceLine(models.Model):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name='lines')
    charge = models.ForeignKey(StudentCharge, on_delete=models.PROTECT, related_name='invoice_lines')
    amount = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal('0.01'))])

    class Meta:
        ordering = ['charge__due_date']
        constraints = [
            models.UniqueConstraint(fields=['invoice', 'charge'], name='unique_invoice_line_charge'),
        ]


class Payment(models.Model):
    """Money received, recorded at the counter against physical proof.

    Append-only. Nothing here is ever edited or deleted — a mistake is
    corrected by a reversal row that points back at the original, so the trail
    survives the correction instead of being erased by it. `amount` is signed:
    a payment is positive, a reversal negative, so sums work everywhere.
    """
    CRDB = 'crdb'
    MOBILE = 'mobile'
    CASH = 'cash'
    CHANNEL_CHOICES = [
        (CRDB, 'CRDB bank deposit'),
        (MOBILE, 'Mobile money'),
        (CASH, 'Cash at the office'),
    ]

    SELF = 'self'
    PARENT = 'parent'
    GUARDIAN = 'guardian'
    SPONSOR = 'sponsor'
    EMPLOYER = 'employer'
    PAYER_CHOICES = [
        (SELF, 'The student'), (PARENT, 'Parent'), (GUARDIAN, 'Guardian'),
        (SPONSOR, 'Sponsor'), (EMPLOYER, 'Employer'),
    ]

    profile = models.ForeignKey(StudentProfile, on_delete=models.PROTECT, related_name='ledger_payments')
    invoice = models.ForeignKey(
        Invoice, on_delete=models.PROTECT, null=True, blank=True, related_name='payments',
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    # The date on the bank slip, NOT the day it was keyed in — a student who
    # paid before a deadline stays cleared even if the office recorded it late.
    payment_date = models.DateField(verbose_name='Date on the slip')
    channel = models.CharField(max_length=20, choices=CHANNEL_CHOICES, default=CRDB)

    bank_reference = models.CharField(
        max_length=100, blank=True,
        help_text="The bank's own transaction number from the slip.",
    )
    efd_receipt_no = models.CharField(
        max_length=100, blank=True, verbose_name='EFD receipt no.',
        help_text='From the EFD machine. Links this record to the fiscal receipt.',
    )
    # Often not the student. Recorded so "who paid this" has an answer later.
    payer_name = models.CharField(max_length=200, blank=True)
    payer_relation = models.CharField(max_length=20, choices=PAYER_CHOICES, default=SELF)
    proof = models.FileField(
        upload_to='payment-proof/%Y/%m/', null=True, blank=True,
        validators=[FileExtensionValidator(['pdf', 'jpg', 'jpeg', 'png'])],
    )

    note = models.CharField(max_length=300, blank=True)
    reverses = models.OneToOneField(
        'self', on_delete=models.PROTECT, null=True, blank=True, related_name='reversal',
    )
    reversal_reason = models.CharField(max_length=300, blank=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='ledger_payments_recorded',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-payment_date', '-created_at']
        indexes = [models.Index(fields=['profile', '-payment_date'])]
        constraints = [
            # A payment credits, a reversal debits. Nothing may be zero, and a
            # negative row must say which payment it undoes.
            models.CheckConstraint(
                check=Q(reverses__isnull=True, amount__gt=0) | Q(reverses__isnull=False, amount__lt=0),
                name='payment_sign_matches_reversal',
            ),
        ]

    def __str__(self):
        return f'{self.profile.nactvet_reg_no} – {self.amount} on {self.payment_date}'

    @property
    def is_reversal(self):
        return self.reverses_id is not None


class PaymentAllocation(models.Model):
    """Which charge a payment settled, and by how much.

    Separate from Payment because one deposit routinely covers several charges
    — a parent paying tuition and the exam fee in a single CRDB transaction.
    """
    payment = models.ForeignKey(Payment, on_delete=models.CASCADE, related_name='allocations')
    charge = models.ForeignKey(StudentCharge, on_delete=models.PROTECT, related_name='allocations')
    amount = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        ordering = ['charge__due_date']
        constraints = [
            models.UniqueConstraint(fields=['payment', 'charge'], name='unique_payment_allocation'),
        ]

    def __str__(self):
        return f'{self.amount} → {self.charge}'


class FinanceOverride(models.Model):
    """A human decision that beats the arithmetic — bursary, sponsor delay,
    hardship, or a hold placed for a reason outside the ledger.

    This is what the old StudentFinanceClearance becomes. The difference is
    that an override now carries a reason, an approver and an expiry, so an
    exception looks like an exception instead of being indistinguishable from
    a student who simply paid.
    """
    CLEARED = 'cleared'
    BLOCKED = 'blocked'
    STATUS_CHOICES = [(CLEARED, 'Cleared'), (BLOCKED, 'Blocked')]

    PERIOD_CHOICES = [
        (ChargeType.REGISTRATION, 'Registration'),
        (ChargeType.CAT1, 'CAT 1'),
        (ChargeType.CAT2, 'CAT 2'),
        (ChargeType.FINAL, 'End-of-semester exam'),
        (ChargeType.RESULTS, 'Results release'),
    ]

    profile = models.ForeignKey(StudentProfile, on_delete=models.CASCADE, related_name='finance_overrides')
    academic_year = models.ForeignKey(AcademicYear, on_delete=models.PROTECT, related_name='finance_overrides')
    semester = models.ForeignKey(
        Semester, on_delete=models.PROTECT, null=True, blank=True, related_name='finance_overrides',
    )
    period = models.CharField(max_length=20, choices=PERIOD_CHOICES)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=CLEARED)
    reason = models.CharField(max_length=300)
    expires_on = models.DateField(
        null=True, blank=True,
        help_text='After this date the override lapses and the ledger decides again.',
    )
    is_active = models.BooleanField(default=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='finance_overrides_approved',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['profile', 'academic_year', 'period'])]

    def __str__(self):
        return f'{self.profile.nactvet_reg_no} – {self.get_period_display()} – {self.status}'


class FinanceAuditLog(models.Model):
    """Every money-touching action, kept forever.

    Students can read their own statements and will occasionally dispute them.
    When one says "I paid", the answer needs to be a record rather than
    somebody's memory of last term.
    """
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='finance_audit_entries',
    )
    action = models.CharField(max_length=60)          # payment.record, payment.reverse, charge.waive …
    entity = models.CharField(max_length=60)          # Payment, StudentCharge, FeeStructure …
    entity_id = models.PositiveIntegerField(null=True, blank=True)
    profile = models.ForeignKey(
        StudentProfile, on_delete=models.SET_NULL, null=True, blank=True, related_name='audit_entries',
    )
    summary = models.CharField(max_length=300)
    before = models.JSONField(null=True, blank=True)
    after = models.JSONField(null=True, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-at']
        indexes = [
            models.Index(fields=['-at']),
            models.Index(fields=['entity', 'entity_id']),
        ]

    def __str__(self):
        return f'{self.at:%Y-%m-%d %H:%M} {self.action} – {self.summary}'


class Announcement(models.Model):
    """A PDF notice the admin broadcasts to every student and staff member.

    Downloads are gated (see attendance.views.announcement_download) rather
    than served from a public /media/ URL, so `file` is never exposed as a
    raw path/URL through the API — see AnnouncementSerializer.
    """
    title = models.CharField(max_length=200)
    note = models.CharField(max_length=300, blank=True)
    file = models.FileField(
        upload_to='announcements/%Y/%m/',
        validators=[FileExtensionValidator(['pdf'])],
    )
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='announcements',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.title
