from datetime import date as _date_type
from decimal import Decimal

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.core.validators import FileExtensionValidator, MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone


class AcademicYear(models.Model):
    name = models.CharField(max_length=9, unique=True)   # e.g. "2025/2026"
    is_active = models.BooleanField(default=False)
    end_date = models.DateField(
        null=True, blank=True,
        help_text='The day the year closes. Every invoice raised for this year expires on '
                  'it, so a student paying by instalments keeps one invoice all year. Left '
                  'blank, it is taken to be 30 June of the closing year.',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-name']

    def __str__(self):
        return self.name

    @property
    def next_name(self):
        y1, y2 = self.name.split('/')
        return f"{int(y1)+1}/{int(y2)+1}"

    @property
    def closes_on(self):
        """The last day of the year — the expiry date printed on every invoice.

        One invoice covers every instalment of a payment, so it cannot expire
        when the first instalment falls due; it stands until the year itself
        ends. The office sets `end_date` when it wants an exact day. Failing
        that we take the latest semester cutoff, and failing that the 30th of
        June in the closing calendar year, which is when this college's year
        runs out.
        """
        if self.end_date:
            return self.end_date
        try:
            return _date_type(int(self.name.split('/')[1]), 6, 30)
        except (IndexError, ValueError):
            pass
        # Last resort only. A semester's end_cutoff is when attendance stops
        # being recorded, which is well before the year is over — an invoice
        # dated from it would expire while instalments were still to come.
        cutoffs = [s.end_cutoff for s in self.semesters.all() if s.end_cutoff]
        return max(cutoffs) if cutoffs else None


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


class ResultEntryWindow(models.Model):
    """When marks may be entered, declared by the examination officer.

    Marks used to be enterable whenever anyone happened to open the page, so a
    tutor could quietly revise a CA mark weeks after the results were published
    and nobody would know. The examination officer now says when the books are
    open; outside that, only they can write.

    One window per kind per semester — continuous assessment and the end of
    semester examination close at different times, and neither is the other.
    """

    #: Continuous assessment is the window that governs tutors — the CATs and
    #: assignments they set and marked. The end of semester window governs
    #: nobody's write access, because that paper is entered by the examination
    #: office alone; it is the deadline the college publishes to itself, and it
    #: shows on the same banner.
    CA = 'ca'
    END = 'end'
    KIND_CHOICES = [
        (CA, 'Continuous assessment marks — tutors enter these'),
        (END, 'End of semester examination — examination office deadline'),
    ]

    semester = models.ForeignKey(Semester, on_delete=models.CASCADE, related_name='result_windows')
    kind = models.CharField(max_length=10, choices=KIND_CHOICES)
    opens_on = models.DateField()
    closes_on = models.DateField()
    # The switch the examination officer flips to shut the books early, or to
    # prepare next semester's window without opening it yet.
    is_active = models.BooleanField(
        default=True,
        help_text='Turn off to close entry immediately, whatever the dates say.',
    )
    note = models.CharField(
        max_length=200, blank=True,
        help_text='Shown to tutors alongside the dates — "submit by Friday", say.',
    )
    declared_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='result_windows_declared',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-semester__academic_year__name', '-semester__number', 'kind']
        constraints = [
            models.UniqueConstraint(fields=['semester', 'kind'],
                                    name='one_result_window_per_kind_per_semester'),
        ]

    def __str__(self):
        return f'{self.semester} · {self.get_kind_display()}'

    def status(self, today=None):
        """Closed, open, or not yet — what the tutor's banner says."""
        from datetime import date as _date
        today = today or _date.today()
        if not self.is_active:
            return 'closed'
        if today < self.opens_on:
            return 'upcoming'
        if today > self.closes_on:
            return 'closed'
        return 'open'

    def is_open(self, today=None):
        return self.status(today) == 'open'

    @classmethod
    def for_semester(cls, semester, kind):
        if semester is None:
            return None
        return cls.objects.filter(semester=semester, kind=kind).first()

    @classmethod
    def entry_is_open(cls, semester, kind, today=None):
        """No window declared means closed, not open.

        The examination officer saying nothing is not the same as them saying
        yes — the point of the window is that somebody decided.
        """
        window = cls.for_semester(semester, kind)
        return bool(window and window.is_open(today))


class ClassLevel(models.Model):
    name = models.CharField(max_length=100, unique=True)
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ['order', 'name']

    def __str__(self):
        return self.name


class Department(models.Model):
    """An academic department: the programmes it runs, its head and its staff.

    Fees are not kept here. They differ by programme and they are the
    accountant's to set (FeeStructure.programme); the department screen only
    shows them.
    """
    name = models.CharField(max_length=160, unique=True)
    code = models.CharField(max_length=20, unique=True)
    # Chosen from accounts that already hold the Head of Department role. The
    # department screen does not hand out roles: that stays with the role
    # editor, which is where "nobody changes their own role" is enforced.
    hod = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='departments_headed', verbose_name='Head of department',
    )
    staff = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name='departments')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class Programme(models.Model):
    """A course of study run by one department, at one or more NTA levels.

    `code` is the course code printed in college ID numbers, which is why it is
    unique across the whole college rather than within its department.
    """
    department = models.ForeignKey(Department, on_delete=models.PROTECT, related_name='programmes')
    name = models.CharField(max_length=160)
    code = models.CharField(max_length=20, unique=True)
    levels = models.ManyToManyField(
        ClassLevel, blank=True, related_name='programmes',
        help_text='The NTA levels this programme is taught at.',
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['department__name', 'name']
        constraints = [
            models.UniqueConstraint(fields=['department', 'name'], name='unique_programme_name_per_department'),
        ]

    def __str__(self):
        return f'{self.code} – {self.name}'


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


class PrincipalProfile(models.Model):
    """The Principal.

    Carries admin rights over everything academic, and deliberately none over
    money or college property: those belong to the accountant and the estate
    officer, and an account that can do everything is an account nobody can
    audit.
    """
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='principal_profile')
    full_name = models.CharField(max_length=200)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.full_name


class HeadOfDepartmentProfile(models.Model):
    """The Head of Department.

    Runs the department — modules, students, attendance, staff and the forms —
    but not examinations: results, eligibility and exam declarations stay with
    the examination officer. Nor money, nor property. The person who runs the
    department is not the person who decides who sits an exam.
    """
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='hod_profile')
    full_name = models.CharField(max_length=200)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.full_name


class SecretaryProfile(models.Model):
    """The college secretary.

    Distinct from the accountant and the examination officer because the work
    is: the secretary is the office a student's request for a sick sheet or
    leave of absence goes to, and the person who releases the printed document
    for signature and stamping. They have no business in the ledger or the
    academic register.
    """
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='secretary_profile')
    full_name = models.CharField(max_length=200)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.full_name


class RecordsOfficerProfile(models.Model):
    """The records officer: keeps the student record.

    Captures and verifies a student's personal details at admission, and works
    the admission alongside the accountant and the admission officer. Holds no
    administrator rights, like the accountant and the secretary.
    """
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='records_officer_profile',
    )
    full_name = models.CharField(max_length=200)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.full_name


class AdmissionOfficerProfile(models.Model):
    """The admission officer: admits students.

    Checks the admission requirements, issues the college ID number and
    finalises the admission. Holds no administrator rights.
    """
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='admission_officer_profile',
    )
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
    # Optional so the modules that existed before departments did keep working
    # untouched; they are linked to their programme afterwards, one link at a time.
    programme = models.ForeignKey(
        'Programme', on_delete=models.PROTECT, null=True, blank=True, related_name='modules',
    )
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


class StudentQuerySet(models.QuerySet):
    def studying(self):
        """The class as it stands: everyone still sitting these modules.

        A student stopped mid-semester — a supplementary they failed, a
        postponement — keeps every row and every mark they had, and comes off
        the class lists, the attendance register, mark entry and the eligibility
        lists. `Student.objects` stays unfiltered, because their own record and
        the college's history must still show what happened.
        """
        return self.filter(withdrawn_at__isnull=True)


class Student(models.Model):
    """One enrollment: this person, in this module. Attendance and results hang
    off it, and always have.

    Money does not. Fees, invoices and clearance attach to `profile` — the
    person — because a student taking eight modules is eight rows here and
    owes one balance, not eight. See StudentProfile.
    """
    NORMAL = 'normal'
    REPEAT = 'repeat'
    ATTEMPT_CHOICES = [(NORMAL, 'First sitting'), (REPEAT, 'Repeat sitting')]

    nactvet_reg_no = models.CharField(max_length=50, verbose_name='NACTVET Reg. No.')
    name = models.CharField(max_length=200)
    profile = models.ForeignKey(
        'StudentProfile', on_delete=models.CASCADE, null=True, blank=True, related_name='enrollments',
    )
    module = models.ForeignKey(Module, on_delete=models.CASCADE, related_name='students')
    # A repeat sitting is billed at the accountant's per-module repeat rate
    # rather than the programme's fees, and its result is the one that counts
    # for the module — the failed sitting it points back at stays in the record.
    attempt = models.CharField(max_length=10, choices=ATTEMPT_CHOICES, default=NORMAL)
    repeat_of = models.ForeignKey(
        'self', on_delete=models.SET_NULL, null=True, blank=True, related_name='repeat_attempts',
    )
    # Set when the student stops studying this module part-way through: they
    # failed a supplementary for the semester before it, or they postponed.
    # The authority's rule is that they stop there, so they come off the class
    # lists — and nothing they had already done is removed.
    withdrawn_at = models.DateTimeField(null=True, blank=True)
    withdrawn_reason = models.CharField(max_length=300, blank=True)
    # When the portal password was last set. A password has a life; this is
    # what it is measured from.
    portal_pin_set_at = models.DateTimeField(null=True, blank=True, editable=False)

    objects = StudentQuerySet.as_manager()
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
        from django.utils import timezone as _tz

        self.portal_pin_hash = make_password(str(raw_pin))
        self.must_change_portal_password = require_change
        # Stamped here rather than by each caller, so no path can set a
        # password and leave the college unable to tell how old it is.
        self.portal_pin_set_at = _tz.now()

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
    MALE = 'M'
    FEMALE = 'F'
    GENDER_CHOICES = [(MALE, 'Male'), (FEMALE, 'Female')]

    nactvet_reg_no = models.CharField(max_length=50, unique=True, verbose_name='NACTVET Reg. No.')
    name = models.CharField(max_length=200)
    # The college's own number, as printed on the student ID card. Issued once
    # by the admission office and kept until the student finishes their studies
    # — a readmitted student keeps theirs. Null rather than blank while unset,
    # so any number of students can be waiting for one without colliding.
    college_id = models.CharField(
        max_length=40, unique=True, null=True, blank=True, verbose_name='College ID No.',
    )
    phone = models.CharField(max_length=30, blank=True)
    gender = models.CharField(max_length=1, choices=GENDER_CHOICES, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f'{self.nactvet_reg_no} – {self.name}'


class NextOfKin(models.Model):
    """Someone the college contacts about a student. The admission form takes two."""
    PARENT = 'parent'
    GUARDIAN = 'guardian'
    SPOUSE = 'spouse'
    SIBLING = 'sibling'
    RELATIVE = 'relative'
    OTHER = 'other'
    RELATIONSHIP_CHOICES = [
        (PARENT, 'Parent'), (GUARDIAN, 'Guardian'), (SPOUSE, 'Spouse'),
        (SIBLING, 'Sibling'), (RELATIVE, 'Other relative'), (OTHER, 'Other'),
    ]

    profile = models.ForeignKey(StudentProfile, on_delete=models.CASCADE, related_name='next_of_kin')
    position = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(2)],
        help_text='First or second next of kin.',
    )
    name = models.CharField(max_length=200)
    phone = models.CharField(max_length=30)
    relationship = models.CharField(max_length=20, choices=RELATIONSHIP_CHOICES)

    class Meta:
        ordering = ['profile', 'position']
        constraints = [
            models.UniqueConstraint(fields=['profile', 'position'], name='one_next_of_kin_per_position'),
            models.CheckConstraint(check=Q(position__in=[1, 2]), name='next_of_kin_position_one_or_two'),
        ]

    def __str__(self):
        return f'{self.profile.nactvet_reg_no} – next of kin {self.position}: {self.name}'


class SemesterRegistration(models.Model):
    """Where one student studies in one semester: which programme, at which NTA
    level, and on what footing.

    The level used to be read off whichever module enrollment happened to come
    first. For a level 5 student repeating a level 4 module that could be the
    wrong one, and the level decides the fees. It is recorded here instead.

    Registrations only ever add to the record. The module enrollments, marks
    and results they sit beside are never rewritten through them.
    """
    NEW = 'new'
    CONTINUING = 'continuing'
    REPEATING = 'repeating'
    READMISSION = 'readmission'
    IMPORTED = 'imported'
    KIND_CHOICES = [
        (NEW, 'New to the college'),
        (CONTINUING, 'Continuing'),
        (REPEATING, 'Repeating failed modules'),
        (READMISSION, 'Readmitted'),
        (IMPORTED, 'Recorded from enrollments made before registrations were kept'),
    ]

    ACTIVE = 'active'
    CANCELLED = 'cancelled'
    STATUS_CHOICES = [(ACTIVE, 'Studying'), (CANCELLED, 'Cancelled')]

    profile = models.ForeignKey(StudentProfile, on_delete=models.CASCADE, related_name='registrations')
    semester = models.ForeignKey(Semester, on_delete=models.PROTECT, related_name='registrations')
    programme = models.ForeignKey(Programme, on_delete=models.PROTECT, related_name='registrations')
    class_level = models.ForeignKey(ClassLevel, on_delete=models.PROTECT, related_name='registrations')
    kind = models.CharField(max_length=20, choices=KIND_CHOICES)
    # A registration is cancelled when the student turns out not to be studying
    # that semester after all — they postponed, or a supplementary result that
    # arrived mid-semester sent them back to repeat the semester before it. The
    # row stays, with the reason, and the enrollments and marks made while they
    # were still expected stay exactly as they were.
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=ACTIVE)
    cancelled_reason = models.CharField(max_length=300, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='registrations_created',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-semester__academic_year__name', '-semester__number', 'profile__name']
        constraints = [
            models.UniqueConstraint(fields=['profile', 'semester'], name='one_registration_per_semester'),
        ]

    def __str__(self):
        return f'{self.profile.nactvet_reg_no} · {self.semester} · {self.programme.code} {self.class_level}'


# ── PROGRESSION ───────────────────────────────────────────────────────────────
#
# Advancing the semester used to do one thing: flip the active flags. Students
# stayed exactly where they were, so a level 4 student was still a level 4
# student in the new year, a student who failed a module after their
# supplementary was indistinguishable from one who passed, and a student who
# stopped coming left no trace of what they still owed the college
# academically.
#
# The four models below are that missing memory:
#
#     SemesterReview     what each semester's results mean for one student
#     StudentStanding    where the student is now, and what they come back to
#     StandingChange     every move of that standing, and who decided it
#     OutstandingRepeat  a module failed after its supplementary, still unpassed
#
# None of them touches a mark. Results are read, never written; a repeat sitting
# is a new enrollment with its own result, and the failed one it replaces stays
# in the record as history.


class StudentStanding(models.Model):
    """Where one student stands with the college today.

    `status` decides what the rest of the system may do for them: who is
    registered for the coming semester, who appears on the due-back list, and
    who may still sign in to the portal.
    """
    ACTIVE = 'active'
    REPEATING = 'repeating'
    POSTPONED = 'postponed'
    DISCONTINUED = 'discontinued'
    COMPLETED = 'completed'
    ARCHIVED = 'archived'
    STATUS_CHOICES = [
        (ACTIVE, 'Studying'),
        (REPEATING, 'Repeating failed modules'),
        (POSTPONED, 'Postponed'),
        (DISCONTINUED, 'Discontinued — may return on readmission'),
        (COMPLETED, 'Finished studies, awaiting clearance'),
        (ARCHIVED, 'Cleared and archived'),
    ]

    #: Statuses whose students are expected back on a stated semester.
    AWAY = {POSTPONED, DISCONTINUED}
    #: Only an archived student is refused the portal. A discontinued or
    #: postponed one still needs to see their results, their balance, and the
    #: services they use to ask for readmission.
    PORTAL_BLOCKED = {ARCHIVED}

    profile = models.OneToOneField(StudentProfile, on_delete=models.CASCADE, related_name='standing')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=ACTIVE)
    programme = models.ForeignKey(
        Programme, on_delete=models.PROTECT, null=True, blank=True, related_name='standings')
    class_level = models.ForeignKey(
        ClassLevel, on_delete=models.PROTECT, null=True, blank=True, related_name='standings',
        help_text='The level the student is at now, or was at when they left.')
    return_year = models.ForeignKey(
        AcademicYear, on_delete=models.SET_NULL, null=True, blank=True, related_name='returning_students',
        help_text='The year a postponed or discontinued student is expected back.')
    return_semester_number = models.PositiveSmallIntegerField(
        null=True, blank=True, choices=Semester.NUMBER_CHOICES,
        help_text='The semester they return to — a student who failed semester 2 '
                  'comes back to semester 2, not to the start of the year.')
    note = models.CharField(max_length=300, blank=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='standings_updated')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['profile__name']

    def __str__(self):
        return f'{self.profile.nactvet_reg_no} · {self.get_status_display()}'

    @property
    def blocks_portal(self):
        return self.status in self.PORTAL_BLOCKED


class StandingChange(models.Model):
    """One move of a standing, kept forever.

    A student who comes back in 2028 asking why they were discontinued is
    answered from here: the semester it was decided in, the reason, and the
    officer who confirmed it.
    """
    profile = models.ForeignKey(StudentProfile, on_delete=models.CASCADE, related_name='standing_changes')
    from_status = models.CharField(max_length=20, blank=True)
    to_status = models.CharField(max_length=20)
    semester = models.ForeignKey(
        Semester, on_delete=models.SET_NULL, null=True, blank=True, related_name='standing_changes',
        help_text='The semester whose results or decision caused the move.')
    reason = models.CharField(max_length=300, blank=True)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='standing_changes_made')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', 'profile__name']

    def __str__(self):
        return f'{self.profile.nactvet_reg_no}: {self.from_status or "—"} → {self.to_status}'


class SemesterReview(models.Model):
    """What one semester's results mean for one student.

    The system proposes; the examination officer or the Principal confirms.
    Both are kept, so an override always shows what the results said before
    somebody overruled them.
    Nothing here is read until a human has confirmed it — the semester cannot be
    advanced while any student is still unconfirmed.
    """
    CLEAR = 'clear'
    REPEAT = 'repeat'
    DISCONTINUED = 'discontinued'
    COMPLETED = 'completed'
    PENDING = 'pending'
    PROVISIONAL = 'provisional'
    POSTPONED = 'postponed'
    OUTCOME_CHOICES = [
        (CLEAR, 'Passed everything'),
        (REPEAT, 'Repeats the failed module(s)'),
        (DISCONTINUED, 'Discontinued — GPA below 2.0'),
        (COMPLETED, 'Finished level 6'),
        (PENDING, 'Waiting for results'),
        (PROVISIONAL, 'Continues while supplementary results are awaited'),
        (POSTPONED, 'Postponed'),
    ]
    #: Outcomes that carry the student into the next semester.
    CONTINUES = {CLEAR, REPEAT, PROVISIONAL}

    profile = models.ForeignKey(StudentProfile, on_delete=models.CASCADE, related_name='semester_reviews')
    semester = models.ForeignKey(Semester, on_delete=models.PROTECT, related_name='reviews')
    programme = models.ForeignKey(
        Programme, on_delete=models.PROTECT, null=True, blank=True, related_name='reviews')
    class_level = models.ForeignKey(
        ClassLevel, on_delete=models.PROTECT, null=True, blank=True, related_name='reviews')
    gpa = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True)
    # One entry per module sat: code, name, credits, status, grade, points.
    # Stored so the list the officer confirmed can be shown again unchanged,
    # even after a repeat sitting has replaced one of those results.
    modules = models.JSONField(default=list, blank=True)
    proposed = models.CharField(max_length=20, choices=OUTCOME_CHOICES)
    proposed_reason = models.CharField(max_length=300, blank=True)
    confirmed = models.CharField(max_length=20, choices=OUTCOME_CHOICES, blank=True, default='')
    confirmed_reason = models.CharField(max_length=300, blank=True)
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='reviews_confirmed')
    confirmed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-semester__academic_year__name', '-semester__number', 'profile__name']
        constraints = [
            models.UniqueConstraint(fields=['profile', 'semester'], name='one_review_per_semester'),
        ]

    def __str__(self):
        return f'{self.profile.nactvet_reg_no} · {self.semester} · {self.outcome}'

    @property
    def outcome(self):
        """What the college is going by: the confirmed decision, or the proposal
        while nobody has confirmed one."""
        return self.confirmed or self.proposed

    @property
    def is_confirmed(self):
        return bool(self.confirmed)


class OutstandingRepeat(models.Model):
    """A module failed after its supplementary examination, and not yet passed.

    This is what keeps a repeating student traceable. The row names the module
    and the semester it was failed in, and it stays open — across academic years
    — until a repeat sitting passes it. The failed result it refers to is never
    altered: passing is recorded on the new sitting, and this row points at both.
    """
    OPEN = 'open'
    PASSED = 'passed'
    CANCELLED = 'cancelled'
    STATUS_CHOICES = [
        (OPEN, 'Still to be passed'),
        (PASSED, 'Passed on a repeat sitting'),
        (CANCELLED, 'Cancelled by the office'),
    ]

    profile = models.ForeignKey(StudentProfile, on_delete=models.CASCADE, related_name='outstanding_repeats')
    module_code = models.CharField(max_length=50)
    module_name = models.CharField(max_length=200, blank=True)
    class_level = models.ForeignKey(
        ClassLevel, on_delete=models.PROTECT, related_name='outstanding_repeats')
    semester_number = models.PositiveSmallIntegerField(choices=Semester.NUMBER_CHOICES)
    origin_semester = models.ForeignKey(
        Semester, on_delete=models.PROTECT, related_name='repeats_raised',
        help_text='The semester the module was failed in — the year the student is held to.')
    origin_enrollment = models.ForeignKey(
        Student, on_delete=models.SET_NULL, null=True, blank=True, related_name='repeats_raised',
        help_text='The failed enrollment, kept as the evidence. Never rewritten.')
    attempt_enrollment = models.ForeignKey(
        Student, on_delete=models.SET_NULL, null=True, blank=True, related_name='repeats_attempted',
        help_text='The latest repeat sitting of this module.')
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=OPEN)
    note = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['profile__name', 'origin_semester__academic_year__name', 'module_code']
        constraints = [
            models.UniqueConstraint(fields=['profile', 'module_code', 'origin_semester'],
                                    name='one_repeat_per_module_per_semester'),
        ]

    def __str__(self):
        return f'{self.profile.nactvet_reg_no} · {self.module_code} · {self.get_status_display()}'


# ── ADMISSION ─────────────────────────────────────────────────────────────────
#
# Admitting a student is three desks in order, not one form: finance says what
# they owe and whether it has been settled, records says the person and their
# papers are who and what they claim, and the admission office says yes. The
# models below are that queue, and the trail of who did which part.
#
#     AdmissionWindow     when the college is admitting, and for which semester
#     CollegeIdFormat     the number the admission officer issues, and its counter
#     Application         one student's admission, moving desk to desk
#     ApplicationStep     who cleared which desk, when, and what they said
#     AdmissionRequirement / RequirementCheck
#                         the TPH book, insurance, calculator and rim paper —
#                         checked every semester, charged when missing


class AdmissionWindow(models.Model):
    """When the college is admitting, for one semester.

    Continuing students verify their details inside this window; outside it the
    office is not taking applications, and the screens say so rather than
    quietly accepting one.
    """
    semester = models.OneToOneField(Semester, on_delete=models.PROTECT, related_name='admission_window')
    opens_on = models.DateField()
    closes_on = models.DateField()
    is_active = models.BooleanField(
        default=True, help_text='Turn off to close admissions immediately, whatever the dates say.')
    note = models.CharField(max_length=300, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='admission_windows_opened')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-semester__academic_year__name', '-semester__number']

    def __str__(self):
        return f'Admissions · {self.semester}'

    def is_open(self, today=None):
        from datetime import date as _d
        today = today or _d.today()
        return bool(self.is_active and self.opens_on <= today <= self.closes_on)


class CollegeIdFormat(models.Model):
    """The college's own student number: how it is written, and where it starts.

    The college issues these itself, so the admission officer says what they
    look like and which number the year begins at. The pattern is written with
    the pieces the office already uses:

        {COLLEGE}  the college's short code, e.g. BPH
        {PROG}     the programme code, e.g. PST
        {YEAR}     the opening year in full, e.g. 2026
        {YY}       the opening year in two digits, e.g. 26
        {SEQ}      the running number, padded to `sequence_width`

    One counter for the whole college per academic year, as the office keeps it.
    """
    academic_year = models.OneToOneField(
        AcademicYear, on_delete=models.CASCADE, related_name='college_id_format')
    pattern = models.CharField(
        max_length=120, default='{COLLEGE}/{PROG}/{YEAR}/{SEQ}',
        help_text='Where each piece goes. {COLLEGE} {PROG} {YEAR} {YY} {SEQ}')
    college_code = models.CharField(max_length=20, default='BPH')
    starts_at = models.PositiveIntegerField(
        default=1, help_text='The first number of the year.')
    next_number = models.PositiveIntegerField(default=1)
    sequence_width = models.PositiveSmallIntegerField(
        default=3, help_text='How many digits the running number is padded to.')
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='college_id_formats_set')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-academic_year__name']

    def __str__(self):
        return f'College ID · {self.academic_year} · {self.pattern}'

    def render(self, number, programme=None):
        """One number, written out. Nothing here consumes the counter."""
        opening = self.academic_year.name.split('/')[0]
        return (self.pattern
                .replace('{COLLEGE}', self.college_code)
                .replace('{PROG}', programme.code if programme else '')
                .replace('{YEAR}', opening)
                .replace('{YY}', opening[-2:])
                .replace('{SEQ}', str(number).zfill(self.sequence_width)))

    @property
    def example(self):
        """What the next number will look like, written out with a real
        programme code so the office can see the whole thing rather than a gap
        where the programme goes."""
        from django.apps import apps
        programme = apps.get_model('attendance', 'Programme').objects.filter(
            is_active=True).order_by('code').first()
        return self.render(self.next_number, programme)


class Application(models.Model):
    """One student's admission, on its way through the desks.

    A first year is captured first — the college has no record of them yet —
    and then goes the same way as everybody else: finance, records, admission.
    A continuing or readmitted student is already a person the college knows,
    so their application starts at finance.

    Nothing here registers or enrolls anybody. That happens once, at the end,
    when the admission officer admits them.
    """
    NEW = 'new'
    CONTINUING = 'continuing'
    READMISSION = 'readmission'
    KIND_CHOICES = [
        (NEW, 'First year'),
        (CONTINUING, 'Continuing student'),
        (READMISSION, 'Readmission'),
    ]

    #: The desks, in the order they are worked.
    INTAKE = 'intake'
    INFORMATION = 'information'
    FINANCE = 'finance'
    RECORDS = 'records'
    ADMISSION = 'admission'
    ADMITTED = 'admitted'
    REJECTED = 'rejected'
    CANCELLED = 'cancelled'
    STATE_CHOICES = [
        (INTAKE, 'Intake — the admission office takes them on'),
        (INFORMATION, 'Records — details and documents'),
        (FINANCE, 'With finance'),
        (RECORDS, 'With records'),
        (ADMISSION, 'With the admission officer'),
        (ADMITTED, 'Admitted'),
        (REJECTED, 'Refused'),
        (CANCELLED, 'Withdrawn'),
    ]
    #: Which desks each kind of application goes through.
    #:
    #: A first year begins and ends at the admission office: intake is where
    #: the college takes them on and their NACTVET number is obtained, and the
    #: last desk is where they are admitted. Records sits between the two and
    #: keeps their details and their documents — no money passes there; that is
    #: finance's desk, and the only one that bills.
    ROUTES = {
        NEW: [INTAKE, INFORMATION, FINANCE, ADMISSION],
        CONTINUING: [FINANCE, RECORDS, ADMISSION],
        READMISSION: [FINANCE, RECORDS, ADMISSION],
    }
    OPEN_STATES = {INTAKE, INFORMATION, FINANCE, RECORDS, ADMISSION}

    kind = models.CharField(max_length=20, choices=KIND_CHOICES)
    state = models.CharField(max_length=20, choices=STATE_CHOICES)
    # The person, once there is one. A first year has a profile from the moment
    # their details are captured — a person the college has written down is not
    # yet a student, and becomes one only when they are admitted.
    profile = models.ForeignKey(
        StudentProfile, on_delete=models.CASCADE, related_name='applications')
    semester = models.ForeignKey(Semester, on_delete=models.PROTECT, related_name='applications')
    programme = models.ForeignKey(Programme, on_delete=models.PROTECT, related_name='applications')
    class_level = models.ForeignKey(ClassLevel, on_delete=models.PROTECT, related_name='applications')
    # What the student is returning to, for a readmission: the semester they
    # were discontinued in is the semester they come back to.
    returning_to = models.ForeignKey(
        Semester, on_delete=models.SET_NULL, null=True, blank=True, related_name='readmissions')
    registration = models.OneToOneField(
        SemesterRegistration, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='application',
        help_text='Created when the admission officer admits them.')
    note = models.CharField(max_length=300, blank=True)
    decided_reason = models.CharField(max_length=300, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='applications_opened')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(fields=['profile', 'semester'],
                                    name='one_application_per_semester'),
        ]

    def __str__(self):
        return f'{self.profile.nactvet_reg_no} · {self.get_kind_display()} · {self.get_state_display()}'

    @property
    def route(self):
        return self.ROUTES[self.kind]

    @property
    def is_open(self):
        return self.state in self.OPEN_STATES

    @property
    def next_state(self):
        """The desk after this one, or 'admitted' at the end of the route."""
        route = self.route
        if self.state not in route:
            return None
        position = route.index(self.state)
        return route[position + 1] if position + 1 < len(route) else self.ADMITTED


class ApplicationStep(models.Model):
    """One desk's part of an admission: who cleared it, when, and what they said."""
    application = models.ForeignKey(Application, on_delete=models.CASCADE, related_name='steps')
    step = models.CharField(max_length=20, choices=Application.STATE_CHOICES)
    done_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='application_steps')
    done_at = models.DateTimeField(auto_now_add=True)
    note = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ['done_at']

    def __str__(self):
        return f'{self.application_id} · {self.step}'


class AdmissionRequirement(models.Model):
    """Something a student must have to study: the TPH book, insurance, a
    calculator, rim paper.

    Checked at admission and again the next semester, because a student who had
    a book in October may not have one in February. What is missing is charged
    at the accountant's rate for it, which is why a requirement points at a
    charge type rather than carrying an amount of its own.
    """
    EVERY_SEMESTER = 'each_semester'
    EVERY_YEAR = 'each_year'
    ONCE = 'once'
    FREQUENCY_CHOICES = [
        (EVERY_SEMESTER, 'Checked every semester'),
        (EVERY_YEAR, 'Checked once a year'),
        (ONCE, 'Checked once, when they join'),
    ]

    name = models.CharField(max_length=120, unique=True)
    description = models.CharField(max_length=300, blank=True)
    charge_type = models.ForeignKey(
        'ChargeType', on_delete=models.PROTECT, null=True, blank=True,
        related_name='requirements',
        help_text='What the college charges when the student does not have it.')
    frequency = models.CharField(max_length=20, choices=FREQUENCY_CHOICES, default=EVERY_SEMESTER)
    applies_to_levels = models.ManyToManyField(
        ClassLevel, blank=True, related_name='requirements',
        help_text='Leave empty for every level.')
    mandatory = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class RequirementCheck(models.Model):
    """Whether one student had one requirement, on one application."""
    HAS_IT = 'has_it'
    MISSING = 'missing'
    WAIVED = 'waived'
    STATUS_CHOICES = [
        (HAS_IT, 'Has it'),
        (MISSING, 'Missing — charged'),
        (WAIVED, 'Waived by the college'),
    ]

    application = models.ForeignKey(Application, on_delete=models.CASCADE, related_name='requirement_checks')
    requirement = models.ForeignKey(AdmissionRequirement, on_delete=models.PROTECT, related_name='checks')
    status = models.CharField(max_length=12, choices=STATUS_CHOICES)
    charge = models.ForeignKey(
        'StudentCharge', on_delete=models.SET_NULL, null=True, blank=True, related_name='requirement_checks')
    note = models.CharField(max_length=300, blank=True)
    checked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='requirement_checks')
    checked_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['requirement__name']
        constraints = [
            models.UniqueConstraint(fields=['application', 'requirement'],
                                    name='one_check_per_requirement_per_application'),
        ]

    def __str__(self):
        return f'{self.application_id} · {self.requirement} · {self.status}'


class StudentDocument(models.Model):
    """A paper the student gave the college, kept as a file.

    Certificates, result slips, a birth certificate, a medical form. The
    records desk scans what comes in over the counter, and a student can upload
    their own from the portal — most of these are documents they already have
    on their phone, and a scan that arrives before they do is one fewer queue.

    Downloads are gated the way a request's attachment is: the file carries the
    student's name and their results, so it belongs to them and to the offices
    that admit and keep the record.
    """
    CERTIFICATE = 'certificate'
    RESULT_SLIP = 'result_slip'
    BIRTH = 'birth_certificate'
    IDENTITY = 'identity'
    MEDICAL = 'medical'
    OTHER = 'other'
    KIND_CHOICES = [
        (CERTIFICATE, 'Certificate'),
        (RESULT_SLIP, 'Result slip'),
        (BIRTH, 'Birth certificate'),
        (IDENTITY, 'Identification'),
        (MEDICAL, 'Medical form'),
        (OTHER, 'Other'),
    ]

    profile = models.ForeignKey(StudentProfile, on_delete=models.CASCADE, related_name='documents')
    kind = models.CharField(max_length=20, choices=KIND_CHOICES, default=OTHER)
    file = models.FileField(
        upload_to='student_documents/%Y/%m/',
        validators=[FileExtensionValidator(['pdf', 'jpg', 'jpeg', 'png'])],
        help_text='A scan or a photograph — PDF, JPG or PNG.',
    )
    original_name = models.CharField(max_length=255, blank=True)
    note = models.CharField(max_length=200, blank=True)
    # Who put it there. A student uploading their own certificate is the
    # ordinary case, and the records desk verifies it afterwards.
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='student_documents_uploaded')
    uploaded_by_student = models.BooleanField(default=False)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='student_documents_verified')
    verified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-uploaded_at']

    def __str__(self):
        return f'{self.profile.nactvet_reg_no} · {self.get_kind_display()}'

    @property
    def display_name(self):
        return self.original_name or self.file.name.rsplit('/', 1)[-1]

    @property
    def size(self):
        try:
            return self.file.size
        except (OSError, ValueError):
            return 0

    @property
    def is_verified(self):
        return self.verified_at is not None


class PasswordStatus(models.Model):
    """How old a staff account's password is, and whether it still opens the door.

    Django keeps the password itself but not the day it was set, and a college
    that cannot say how old a password is cannot have a policy about it. One
    row per account, stamped whenever the password changes.
    """
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='password_status')
    changed_at = models.DateTimeField(default=timezone.now)
    #: Set when the office resets a password: whatever they were given is
    #: temporary, and the holder picks their own at the next sign-in.
    must_change = models.BooleanField(default=False)
    reset_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='passwords_reset')
    reset_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['user__username']
        verbose_name_plural = 'password statuses'

    def __str__(self):
        return f'{self.user.username} · set {self.changed_at:%Y-%m-%d}'


class CollegeProfile(models.Model):
    """The college's own details, as they appear on an invoice.

    A single row, edited by the accountant. Kept in the database rather than
    settings so the office can correct a phone number without a deploy.
    """
    name = models.CharField(max_length=200, default='Blue Pharma College of Health')
    short_name = models.CharField(max_length=60, default='BPHACOH')
    po_box = models.CharField(max_length=60, blank=True)
    town = models.CharField(max_length=80, blank=True)
    country = models.CharField(max_length=80, default='Tanzania')
    phone = models.CharField(max_length=120, blank=True)
    email = models.EmailField(blank=True)
    website = models.CharField(max_length=160, blank=True)
    logo = models.FileField(
        upload_to='college/', null=True, blank=True,
        validators=[FileExtensionValidator(['png', 'jpg', 'jpeg', 'svg'])],
    )
    invoice_terms = models.TextField(
        blank=True,
        help_text='Shown under Terms & Conditions on every invoice, one rule per line.',
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'college profile'

    def __str__(self):
        return self.name

    @classmethod
    def get(cls):
        return cls.objects.first() or cls()


class BankAccount(models.Model):
    """A college bank account a student can deposit into.

    The college runs more than one — tuition and other charges go to different
    CRDB accounts — so an invoice may only ever name a single account. Mixing
    them would produce a bill the student cannot pay in one deposit.
    """
    bank_name = models.CharField(max_length=120, default='CRDB')
    account_name = models.CharField(max_length=200)
    account_number = models.CharField(max_length=64, unique=True)
    purpose = models.CharField(
        max_length=160,
        help_text='What this account collects, e.g. "Tuition fee" or "Other charges & accommodation".',
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['purpose', 'account_number']

    def __str__(self):
        return f'{self.bank_name} {self.account_number} – {self.purpose}'


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

    ONCE = 'once'
    EACH_YEAR = 'each_year'
    EACH_SEMESTER = 'each_semester'
    FREQUENCY_CHOICES = [
        (ONCE, 'Once — never repeated'),
        (EACH_YEAR, 'Each year'),
        (EACH_SEMESTER, 'Each semester'),
    ]

    name = models.CharField(max_length=160, unique=True)
    # The item code the accountant quotes for this charge on an invoice. Set by
    # the office to match whatever the college's books already use, so a bill
    # can be reconciled against them line by line.
    code = models.CharField(max_length=40, blank=True, verbose_name='Item code')
    # Where this item sits in the college's published table. An invoice lists
    # its components in this order, so a student can check the bill against the
    # Other Charges table in the admission form row by row.
    sort_order = models.PositiveSmallIntegerField(
        default=0, verbose_name='Row order',
        help_text='Position in the published fee table. Items with the same order fall back '
                  'to alphabetical.',
    )
    family = models.CharField(max_length=20, choices=FAMILY_CHOICES, default=FEE)
    applies = models.CharField(max_length=20, choices=APPLIES_CHOICES, default=AUTOMATIC)

    # How often it is billed. `once` is what separates a first-year's bill from
    # a continuing student's: caution money, admission, ID card, uniforms and
    # the like are charged one time for the whole programme and never again.
    frequency = models.CharField(max_length=20, choices=FREQUENCY_CHOICES, default=EACH_YEAR)

    # Which account the money goes into, and which invoice it belongs on. Both
    # matter because the college banks tuition and other charges separately —
    # a student paying both makes two deposits and needs two invoices.
    bank_account = models.ForeignKey(
        BankAccount, on_delete=models.PROTECT, null=True, blank=True, related_name='charge_types',
    )
    invoice_group = models.CharField(
        max_length=80, blank=True,
        help_text='Charges sharing a group are invoiced together, e.g. "Tuition Fee", '
                  '"Direct Costs", "Accommodation". Defaults to the group name.',
    )

    # What non-payment prevents. Set by the accountant — this is the college's
    # exam-eligibility policy, and it is deliberately not hardcoded: tuition
    # should block an exam, a graduation gown should not.
    blocks_registration = models.BooleanField(default=False, verbose_name='Blocks registration')
    blocks_cat1 = models.BooleanField(default=False, verbose_name='Blocks CAT 1')
    blocks_cat2 = models.BooleanField(default=False, verbose_name='Blocks CAT 2')
    blocks_final = models.BooleanField(default=False, verbose_name='Blocks end-of-semester exam')
    blocks_results = models.BooleanField(default=False, verbose_name='Blocks results release')

    # Which exam declaration raises this charge. A declared charge is priced
    # per module: the fee structure holds the rate, and a student declared for
    # two modules is charged twice.
    SPECIAL_EXAM = 'special_exam'
    SUPP_EXAM = 'supp_exam'
    REPEAT_MODULE = 'repeat_module'
    DECLARATION_CHOICES = [
        (SPECIAL_EXAM, 'Special exam'),
        (SUPP_EXAM, 'Supplementary exam'),
        (REPEAT_MODULE, 'Repeat module'),
    ]
    declaration = models.CharField(
        max_length=20, choices=DECLARATION_CHOICES, blank=True,
        help_text='Raised per module when the examination office declares a student for this.',
    )
    # A readmitted student starts afresh, so a charge billed once for the whole
    # programme (admission fee, caution money, uniforms) is billed again.
    charged_again_on_readmission = models.BooleanField(
        default=True,
        help_text='For a charge billed once, bill it again when a discontinued student is readmitted.',
    )

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
        ordering = ['family', 'sort_order', 'name']
        constraints = [
            # One charge per kind of declaration, or a declared student could be
            # billed twice for the same supplementary exam.
            models.UniqueConstraint(
                fields=['declaration'], condition=~Q(declaration=''),
                name='one_charge_type_per_declaration',
            ),
        ]

    def __str__(self):
        return self.name

    @property
    def frequency_label(self):
        """How often it is charged, as the published table words it: "Each
        year" or "Once"."""
        return {self.ONCE: 'Once', self.EACH_YEAR: 'Each year',
                self.EACH_SEMESTER: 'Each semester'}.get(self.frequency, '')

    def blocks_period(self, period):
        field = self.PERIOD_FIELDS.get(period)
        return bool(field and getattr(self, field))

    @property
    def group_label(self):
        return self.invoice_group.strip() or self.get_family_display()


class FeeStructure(models.Model):
    """HOW MUCH, and in how many installments, for one charge type at one NTA
    level in one academic year. This is the grid the accountant fills in."""
    ACADEMIC_YEAR = 'academic_year'
    SEMESTER = 'semester'
    ONCE = 'once'
    PERIOD_CHOICES = [
        (ACADEMIC_YEAR, 'Once per academic year'),
        (SEMESTER, 'Once per semester'),
        (ONCE, 'Once for the whole programme'),
    ]

    charge_type = models.ForeignKey(ChargeType, on_delete=models.PROTECT, related_name='fee_structures')
    # Fees differ by programme. Blank means the same amount for every programme;
    # where a programme has its own row, that row wins for its students.
    programme = models.ForeignKey(
        Programme, on_delete=models.PROTECT, null=True, blank=True, related_name='fee_structures',
        help_text='Leave blank for an amount every programme pays.',
    )
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
            # Two constraints rather than one: a unique index treats NULLs as
            # distinct, so a single one over programme would let a college-wide
            # cell be entered twice.
            models.UniqueConstraint(
                fields=['charge_type', 'class_level', 'academic_year'],
                condition=Q(programme__isnull=True),
                name='unique_fee_structure_cell',
            ),
            models.UniqueConstraint(
                fields=['charge_type', 'programme', 'class_level', 'academic_year'],
                condition=Q(programme__isnull=False),
                name='unique_programme_fee_structure_cell',
            ),
        ]

    def __str__(self):
        scope = self.programme.code if self.programme_id else 'all programmes'
        return f'{self.charge_type} · {scope} · {self.class_level} · {self.academic_year} = {self.amount}'


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
    # The module a declared charge is for — one supplementary exam, special exam
    # or repeat per module. Also what stops the same declaration billing twice.
    module = models.ForeignKey(
        'Module', on_delete=models.PROTECT, null=True, blank=True, related_name='charges',
    )

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

    @property
    def balance(self):
        """What is still outstanding.

        Uses the `allocated` annotation from finance.with_balances() when the
        queryset supplied one, so rendering a list of charges costs one query
        rather than one per row.
        """
        paid = getattr(self, 'allocated', None)
        if paid is None:
            paid = self.allocations.aggregate(total=models.Sum('amount'))['total'] or Decimal('0.00')
        return self.payable - paid

    @property
    def is_overdue(self):
        from datetime import date as _date
        return self.due_date < _date.today() and self.balance > Decimal('0.00')

    @property
    def is_due_soon(self):
        """Due already, or within the month.

        Used to pre-tick the instalments a student is actually about to pay —
        invoicing the whole year at once is not what anyone walks into the bank
        with.
        """
        from datetime import date as _date, timedelta as _td
        return self.due_date <= _date.today() + _td(days=30) and self.balance > Decimal('0.00')

    @property
    def installments_total(self):
        """How many instalments this charge is one of, so an invoice can say
        "instalment 2 of 5" rather than a bare number."""
        return self.fee_structure.installments if self.fee_structure_id else 1


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
    # What this invoice covers and where it is paid. One invoice never spans
    # two accounts — the student would have to split the deposit.
    invoice_group = models.CharField(max_length=80, blank=True)
    bank_account = models.ForeignKey(
        BankAccount, on_delete=models.PROTECT, null=True, blank=True, related_name='invoices',
    )
    issued_on = models.DateField(auto_now_add=True)
    due_date = models.DateField(null=True, blank=True)
    source = models.CharField(max_length=10, choices=SOURCE_CHOICES, default=STUDENT)
    cancelled = models.BooleanField(default=False)
    cancelled_reason = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['profile', '-created_at'])]
        constraints = [
            # One reference per payment per year. A student paying tuition in
            # five instalments quotes the same number on all five slips, so the
            # constraint is what makes the reference stable rather than a new
            # one appearing every time they open the page.
            models.UniqueConstraint(
                fields=['profile', 'academic_year', 'invoice_group'],
                condition=Q(cancelled=False),
                name='one_live_invoice_per_payment_per_year',
            ),
        ]

    def __str__(self):
        return self.reference

    @property
    def total(self):
        return sum((line.amount for line in self.lines.all()), Decimal('0.00'))

    @property
    def expires_on(self):
        """The last day this invoice can be paid against — the end of its
        academic year, because it covers that whole year's instalments."""
        return self.due_date or self.academic_year.closes_on


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


# ── EVALUATION FORMS ──────────────────────────────────────────────────────────
#
# The college runs several paper evaluations every year — course evaluation,
# tutor evaluation, hostel facilities, the tracer study. They are collected on
# paper, tallied by hand, and the tally is the only thing anybody ever sees.
#
# These models hold the same forms as structured questions so the answers can be
# counted, exported and charted. A form is not an uploaded document: a PDF
# cannot be filled in the portal, aggregated into a spreadsheet or graphed.


class Form(models.Model):
    """One evaluation form the college publishes to students."""

    DRAFT = 'draft'
    OPEN = 'open'
    CLOSED = 'closed'

    # Not every form on the college's shelf is filled in by the student. The
    # Students' Performance Evaluation is filled in by a mentor *about* a
    # student, and showing it in the student's own Forms list would invite them
    # to grade themselves.
    STUDENT = 'student'
    STAFF = 'staff'
    AUDIENCE_CHOICES = [
        (STUDENT, 'Students — filled in on the student portal'),
        (STAFF, 'Staff — filled in by a tutor, mentor or officer'),
    ]

    # Two different things wear the same clothes. An evaluation is feedback the
    # college tallies and reports on; a service request is one student asking
    # for one thing and waiting on an answer. Same questions, same builder —
    # but a request is owed a decision, and an evaluation is not.
    EVALUATION = 'evaluation'
    REQUEST = 'request'
    KIND_CHOICES = [
        (EVALUATION, 'Evaluation — feedback the college tallies and reports on'),
        (REQUEST, 'Service request — one student asking for something, owed an answer'),
    ]

    title = models.CharField(max_length=200)
    kind = models.CharField(max_length=20, choices=KIND_CHOICES, default=EVALUATION)
    audience = models.CharField(max_length=20, choices=AUDIENCE_CHOICES, default=STUDENT)
    slug = models.SlugField(max_length=220, unique=True)
    intro = models.TextField(
        blank=True,
        help_text='Shown above the questions — the instructions paragraph from the form.',
    )
    academic_year = models.ForeignKey(
        AcademicYear, on_delete=models.PROTECT, null=True, blank=True, related_name='forms',
        help_text='Leave blank for a form that is not tied to one year.',
    )
    # The college does not ask every level the same things. The introduction
    # letter request exists in two versions — one for levels 4 and 5, another
    # for level 6 — and showing a student the wrong one gets the wrong answers
    # back. Empty means every level, which is what most forms want and what
    # every form that predates this field already meant.
    levels = models.ManyToManyField(
        ClassLevel, blank=True, related_name='forms', verbose_name='NTA levels',
        help_text='Which levels are asked to fill this in. Leave empty for all of them.',
    )

    # Whether students can see it now. `is_active` is the switch the admin
    # flips; the two dates let them schedule a window and stop having to
    # remember to close it.
    is_active = models.BooleanField(
        default=False,
        help_text='Only active forms appear to students.',
    )
    opens_on = models.DateField(null=True, blank=True)
    closes_on = models.DateField(null=True, blank=True)

    # Students will not say a tutor was unprepared with their name on it. An
    # anonymous form still records *that* a student responded — so nobody is
    # asked twice — but never which response was theirs.
    is_anonymous = models.BooleanField(
        default=False,
        help_text='Record the answers with no link to who gave them. Use for anything '
                  'evaluating a member of staff.',
    )
    allow_multiple = models.BooleanField(
        default=False,
        help_text='Let one student submit more than once. Off means one response each.',
    )
    # A census rather than an invitation. Response rates on a form students can
    # ignore are what make an evaluation useless to report on, so the college
    # can require one: the portal stops at it until it is filled in.
    is_mandatory = models.BooleanField(
        default=False,
        verbose_name='Every student must fill this in',
        help_text='Students see this form on sign-in and cannot use the portal until they '
                  'have answered it. Student forms only.',
    )

    # The small print at the foot of the college's paper form — the sick sheet's
    # warning that excused days still count against the 90% attendance rule, for
    # instance. Printed on the document, never shown in the portal: it is a note
    # to whoever signs and stamps the paper.
    print_note = models.TextField(
        blank=True,
        help_text='Notes printed at the foot of the approved document, one per line.',
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='forms_created',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.title

    def status(self, today=None):
        """Draft, open or closed — what the admin list shows at a glance."""
        from datetime import date as _date
        today = today or _date.today()
        if not self.is_active:
            return self.DRAFT
        if self.opens_on and today < self.opens_on:
            return self.DRAFT
        if self.closes_on and today > self.closes_on:
            return self.CLOSED
        return self.OPEN

    def is_open(self, today=None):
        return self.status(today) == self.OPEN

    def applies_to(self, class_level):
        """Whether a student at this NTA level is asked for this form.

        A form with no levels chosen is for everybody. A form that names its
        levels is for nobody else — including a student whose level we cannot
        work out, because guessing would put the level 6 letter request in
        front of a level 4 student.
        """
        targeted = self.levels.all()
        if not targeted:
            return True
        if class_level is None:
            return False
        return any(level.pk == class_level.pk for level in targeted)

    @property
    def level_names(self):
        """"NTA Level 4, NTA Level 5", or "All levels" when it is not restricted."""
        names = [level.name for level in self.levels.all()]
        return ', '.join(names) if names else 'All levels'


class FormSection(models.Model):
    """A titled group of questions — "Section A: Participant information"."""
    form = models.ForeignKey(Form, on_delete=models.CASCADE, related_name='sections')
    title = models.CharField(max_length=200, blank=True)
    description = models.TextField(blank=True)
    order = models.PositiveSmallIntegerField(default=0)
    # Half of the college's paper forms are not filled in by the student at all.
    # The sick sheet's Part B belongs to the medical officer and Part C to the
    # Head of Department and Dean of Students. Those parts are never shown in
    # the portal — they are printed blank, on the approved document, for the
    # people who sign and stamp it.
    for_office = models.BooleanField(
        default=False,
        verbose_name='Filled in on paper by an office, not by the student',
        help_text='Hidden in the portal. Printed as blank lines on the approved document '
                  'for a health facility, a Head of Department or the Dean of Students.',
    )

    class Meta:
        ordering = ['order', 'id']

    def __str__(self):
        return f'{self.form} · {self.title or "Section"}'


class FormQuestion(models.Model):
    """One question. The type decides what `options`, `rows` and `columns` mean.

    The five types between them express every question on the college's paper
    forms: plain fields, the Excellent-to-Very-Poor and Likert lists, the 1–5
    rating tables, and the two-column "name the module / say what was wrong"
    tables.
    """
    SHORT_TEXT = 'short_text'
    LONG_TEXT = 'long_text'
    SINGLE_CHOICE = 'single_choice'
    MULTI_CHOICE = 'multi_choice'
    MATRIX = 'matrix'
    GRID_TEXT = 'grid_text'
    TYPE_CHOICES = [
        (SHORT_TEXT, 'Short text'),
        (LONG_TEXT, 'Paragraph'),
        (SINGLE_CHOICE, 'Choose one'),
        (MULTI_CHOICE, 'Choose any'),
        (MATRIX, 'Rating table'),
        (GRID_TEXT, 'Table of text'),
    ]

    section = models.ForeignKey(FormSection, on_delete=models.CASCADE, related_name='questions')
    text = models.TextField()
    help_text = models.CharField(max_length=400, blank=True)
    type = models.CharField(max_length=20, choices=TYPE_CHOICES, default=SINGLE_CHOICE)
    required = models.BooleanField(default=False)
    order = models.PositiveSmallIntegerField(default=0)

    # Answer choices, and the column headings of a rating table.
    options = models.JSONField(default=list, blank=True)
    # The things being rated, down the left of a rating table.
    rows = models.JSONField(default=list, blank=True)
    # The column headings of a table of text.
    columns = models.JSONField(default=list, blank=True)
    max_rows = models.PositiveSmallIntegerField(default=4)

    class Meta:
        ordering = ['order', 'id']

    def __str__(self):
        return self.text[:80]

    @property
    def blank_rows(self):
        """Empty rows for a table of text. A Django template cannot count, so
        the row count has to arrive as something iterable."""
        return range(self.max_rows or 4)

    @property
    def numeric_options(self):
        """The options as numbers, when every one of them is a number.

        A 1–5 rating averages meaningfully; Excellent-to-Very-Poor does not,
        and must be charted as a distribution instead of a mean.
        """
        values = []
        for option in self.options:
            try:
                values.append(float(str(option).strip()))
            except (TypeError, ValueError):
                return None
        return values or None


class FormResponse(models.Model):
    """One filled-in form.

    `profile` is null on an anonymous form — deliberately, not incidentally.
    Who responded is recorded separately in FormSubmissionReceipt, which has no
    route back to the answers.
    """
    form = models.ForeignKey(Form, on_delete=models.CASCADE, related_name='responses')
    profile = models.ForeignKey(
        StudentProfile, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='form_responses',
    )
    # Kept alongside the answers so a response still reports the respondent's
    # level and year after they graduate or move up.
    class_level = models.ForeignKey(
        ClassLevel, on_delete=models.SET_NULL, null=True, blank=True, related_name='form_responses')
    academic_year = models.ForeignKey(
        AcademicYear, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='form_responses')
    # Set when a member of staff fills the form in — a mentor assessing a
    # student, say. Students submit through the portal and are recorded on
    # `profile` instead (or nowhere at all, on an anonymous form).
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='form_responses_submitted',
    )
    submitted_at = models.DateTimeField(auto_now_add=True)

    # A service request is owed an answer. An evaluation is not, and stays
    # PENDING for ever without meaning anything — the student's Services page
    # only reads these on a request-kind form.
    #: A request passes through two desks. The secretary receives it, checks it
    #: and puts it in front of the Principal or the Head of Department; they are
    #: the ones who say yes or no. It comes back to the secretary to be acted on
    #: — the letter typed, signed and sent. Deciding and processing are separate
    #: jobs, and one office doing both is how a request gets approved by the
    #: person who wanted it approved.
    PENDING = 'pending'
    FORWARDED = 'forwarded'
    APPROVED = 'approved'
    DECLINED = 'declined'
    STATUS_CHOICES = [
        (PENDING, 'With the secretary'),
        (FORWARDED, 'With the Principal / Head of Department'),
        (APPROVED, 'Approved'),
        (DECLINED, 'Declined'),
    ]
    #: The statuses a decision-maker has answered.
    ANSWERED_STATUSES = (APPROVED, DECLINED)

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=PENDING)
    forwarded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='form_forwards',
    )
    forwarded_at = models.DateTimeField(null=True, blank=True)
    forward_note = models.CharField(
        max_length=300, blank=True,
        help_text='What the secretary wants the Principal or HoD to know. Not shown '
                  'to the student.',
    )
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='form_decisions',
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    # Shown to the student, so it says where to collect the letter or why the
    # request was turned down — not an internal note.
    decision_note = models.TextField(
        blank=True,
        help_text='Shown to the student with the decision — where to collect it, '
                  'or why it was declined.',
    )

    class Meta:
        ordering = ['-submitted_at']
        indexes = [models.Index(fields=['form', '-submitted_at'])]

    def __str__(self):
        return f'{self.form} · {self.submitted_at:%d %b %Y}'

    @property
    def reference(self):
        """What the printed document is called when somebody has to find it again."""
        return f'REQ-{self.id:05d}'


class Notification(models.Model):
    """Something that has happened and somebody needs to know about.

    A request used to sit in a queue nobody had a reason to open. The secretary
    found out a student had asked for a letter by going and looking; the
    Principal found out one was waiting for a decision the same way. Work that
    depends on somebody noticing it is work that waits.

    One row per recipient — a notification is read by one person, so a shared
    row with a read flag would mean the first reader clears it for everybody.
    """

    REQUEST_SUBMITTED = 'request.submitted'
    REQUEST_FORWARDED = 'request.forwarded'
    REQUEST_DECIDED = 'request.decided'
    REQUEST_DOCUMENT = 'request.document'
    FORM_PUBLISHED = 'form.published'
    KIND_CHOICES = [
        (REQUEST_SUBMITTED, 'A student asked for something'),
        (REQUEST_FORWARDED, 'A request needs a decision'),
        (REQUEST_DECIDED, 'A request was answered'),
        (REQUEST_DOCUMENT, 'A document was sent'),
        (FORM_PUBLISHED, 'A form was opened'),
    ]

    # Exactly one of these two is set: staff sign in as Django users, students
    # on a session keyed to their profile.
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True,
        related_name='notifications',
    )
    profile = models.ForeignKey(
        'StudentProfile', on_delete=models.CASCADE, null=True, blank=True,
        related_name='notifications',
    )
    kind = models.CharField(max_length=32, choices=KIND_CHOICES)
    title = models.CharField(max_length=200)
    body = models.CharField(max_length=300, blank=True)
    #: Where to go to act on it — a page name the dashboard understands.
    link = models.CharField(max_length=120, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'read_at', '-created_at']),
            models.Index(fields=['profile', 'read_at', '-created_at']),
        ]

    def __str__(self):
        return self.title

    @property
    def is_read(self):
        return self.read_at is not None


class RequestAttachment(models.Model):
    """A document the college sends back on a service request.

    The decision note tells a student their letter is ready; this is the letter.
    Approving a request used to end with them walking to an office to collect a
    piece of paper the college had already produced — the point of the request
    going through the portal is that the answer can come back the same way.

    Downloads are gated (see attendance.views.request_attachment_download)
    rather than served from a public /media/ URL: a student's introduction
    letter carries their name, their registration number and the facility they
    are going to, and belongs to them alone.
    """
    request = models.ForeignKey(
        FormResponse, on_delete=models.CASCADE, related_name='attachments')
    file = models.FileField(
        upload_to='request_answers/%Y/%m/',
        validators=[FileExtensionValidator(['pdf', 'doc', 'docx', 'jpg', 'jpeg', 'png'])],
        help_text='The signed letter or document, as a PDF, Word file or scan.',
    )
    original_name = models.CharField(max_length=255, blank=True)
    note = models.CharField(
        max_length=200, blank=True,
        help_text='Shown to the student beside the file — "signed and stamped", say.',
    )
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='request_attachments',
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['uploaded_at']

    def __str__(self):
        return self.display_name

    @property
    def display_name(self):
        return self.original_name or self.file.name.rsplit('/', 1)[-1]

    @property
    def size(self):
        try:
            return self.file.size
        except (OSError, ValueError):
            return 0


class FormAnswer(models.Model):
    """One answer, shaped by its question's type.

    `value` holds a string for text and single choice, a list for multi-choice
    and a table of text, and a {row: choice} mapping for a rating table. One
    row per question rather than per matrix cell, because the export and the
    charts both want the question's answer whole.
    """
    response = models.ForeignKey(FormResponse, on_delete=models.CASCADE, related_name='answers')
    question = models.ForeignKey(FormQuestion, on_delete=models.CASCADE, related_name='answers')
    value = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['question__section__order', 'question__order']
        constraints = [
            models.UniqueConstraint(fields=['response', 'question'], name='unique_answer_per_question'),
        ]


class FormSubmissionReceipt(models.Model):
    """That a student has answered a form — never what they said.

    This is what stops a student being asked twice while keeping an anonymous
    form genuinely anonymous. It deliberately holds no pointer to FormResponse:
    if it did, anonymity would only be a convention.
    """
    form = models.ForeignKey(Form, on_delete=models.CASCADE, related_name='receipts')
    profile = models.ForeignKey(
        StudentProfile, on_delete=models.CASCADE, related_name='form_receipts')
    submitted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-submitted_at']
        constraints = [
            models.UniqueConstraint(fields=['form', 'profile'], name='one_receipt_per_student_per_form'),
        ]
