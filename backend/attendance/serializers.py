from datetime import date
from decimal import Decimal

from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from django.db import transaction
from django.urls import reverse
from . import finance
from .grading import result_outcome
from .models import (
    AcademicYear, Semester, ClassLevel, Module, Student, Session, AttendanceRecord,
    StudentResult, PaymentCategory, StudentFinanceObligation, StudentPayment,
    StudentFinanceClearance, Announcement,
    EstateOfficerProfile, InventoryLocation, AssetCategory, InventoryItemType, Asset,
    AssetTransfer, AssetMaintenance, InventoryInspection, InventoryInspectionItem, AssetDisposal,
    ChargeType, FeeStructure, FeeInstallment, StudentProfile, StudentCharge,
    Invoice, InvoiceLine, Payment, PaymentAllocation, FinanceOverride, FinanceAuditLog,
    BankAccount, CollegeProfile,
    Form, FormSection, FormQuestion, FormResponse, ResultEntryWindow,
)

MAX_ANNOUNCEMENT_FILE_BYTES = 10 * 1024 * 1024  # matches nginx client_max_body_size 10M


# ── Weighted-mark helper ───────────────────────────────────────────────────────

def _wt(raw, weight):
    """Convert a raw /100 mark to its weighted contribution. None if not entered."""
    if raw is None:
        return None
    return round(float(raw) / 100 * weight, 2)


class SemesterSerializer(serializers.ModelSerializer):
    year_name    = serializers.CharField(source='academic_year.name', read_only=True)
    label        = serializers.CharField(read_only=True)
    module_count = serializers.SerializerMethodField()

    class Meta:
        model  = Semester
        fields = [
            'id', 'academic_year', 'year_name', 'number', 'label', 'is_active', 'module_count',
            'cat1_cutoff', 'cat2_cutoff', 'end_cutoff',
        ]
        read_only_fields = ['id']

    def get_module_count(self, obj):
        return obj.modules.count()


class AcademicYearSerializer(serializers.ModelSerializer):
    semesters = SemesterSerializer(many=True, read_only=True)

    class Meta:
        model = AcademicYear
        fields = ['id', 'name', 'is_active', 'semesters', 'created_at']
        read_only_fields = ['id', 'created_at']


class ClassLevelSerializer(serializers.ModelSerializer):
    module_count = serializers.SerializerMethodField()

    class Meta:
        model = ClassLevel
        fields = ['id', 'name', 'order', 'module_count']
        read_only_fields = ['id']

    def get_module_count(self, obj):
        return obj.modules.count()


class InventoryLocationSerializer(serializers.ModelSerializer):
    location_type_display = serializers.CharField(source='get_location_type_display', read_only=True)

    class Meta:
        model = InventoryLocation
        fields = ['id', 'name', 'location_type', 'location_type_display', 'is_active']
        read_only_fields = ['id']


class AssetCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = AssetCategory
        fields = ['id', 'name', 'is_active']
        read_only_fields = ['id']


class InventoryItemTypeSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source='category.name', read_only=True)

    class Meta:
        model = InventoryItemType
        fields = ['id', 'name', 'description', 'category', 'category_name', 'default_tag_prefix', 'is_active']
        read_only_fields = ['id']

    def validate_default_tag_prefix(self, value):
        value = value.strip().rstrip('/')
        qs = InventoryItemType.objects.filter(default_tag_prefix__iexact=value)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError('This tag prefix is already assigned to another item type.')
        return value


class AssetSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source='category.name', read_only=True)
    location_name = serializers.CharField(source='location.name', read_only=True)
    condition_display = serializers.CharField(source='get_condition_display', read_only=True)

    class Meta:
        model = Asset
        fields = [
            'id', 'asset_tag', 'name', 'description', 'category', 'category_name', 'item_type',
            'location', 'location_name', 'responsible_office', 'quantity',
            'condition', 'condition_display', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def validate_asset_tag(self, value):
        qs = Asset.objects.filter(asset_tag__iexact=value.strip())
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError('This asset number/tag is already registered.')
        return value.strip()


class AssetTransferSerializer(serializers.ModelSerializer):
    asset_tag = serializers.CharField(source='asset.asset_tag', read_only=True)
    asset_name = serializers.CharField(source='asset.name', read_only=True)
    from_location_name = serializers.CharField(source='from_location.name', read_only=True)
    to_location_name = serializers.CharField(source='to_location.name', read_only=True)
    resulting_asset_tag = serializers.CharField(source='resulting_asset.asset_tag', read_only=True)

    class Meta:
        model = AssetTransfer
        fields = ['id', 'asset', 'asset_tag', 'asset_name', 'from_location', 'from_location_name',
                  'to_location', 'to_location_name', 'quantity', 'resulting_asset', 'resulting_asset_tag', 'new_responsible_office', 'reason',
                  'transferred_at', 'created_at']
        read_only_fields = ['id', 'from_location', 'resulting_asset', 'created_at']

    def validate(self, attrs):
        asset = attrs.get('asset', getattr(self.instance, 'asset', None))
        quantity = attrs.get('quantity', getattr(self.instance, 'quantity', 1))
        if asset and quantity > asset.quantity:
            raise serializers.ValidationError({'quantity': f'Only {asset.quantity} item(s) are available in this batch.'})
        return attrs


class AssetMaintenanceSerializer(serializers.ModelSerializer):
    asset_tag = serializers.CharField(source='asset.asset_tag', read_only=True)
    asset_name = serializers.CharField(source='asset.name', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model = AssetMaintenance
        fields = ['id', 'asset', 'asset_tag', 'asset_name', 'quantity', 'issue', 'action_taken', 'provider',
                  'cost', 'status', 'status_display', 'reported_date', 'completed_date', 'created_at']
        read_only_fields = ['id', 'created_at']

    def validate(self, attrs):
        asset = attrs.get('asset', getattr(self.instance, 'asset', None))
        quantity = attrs.get('quantity', getattr(self.instance, 'quantity', 1))
        if asset and quantity > asset.quantity:
            raise serializers.ValidationError({'quantity': f'Only {asset.quantity} item(s) are registered in this batch.'})
        if attrs.get('status') == AssetMaintenance.COMPLETED and not attrs.get('completed_date', getattr(self.instance, 'completed_date', None)):
            raise serializers.ValidationError({'completed_date': 'Completion date is required when maintenance is completed.'})
        return attrs


class InventoryInspectionItemSerializer(serializers.ModelSerializer):
    asset_tag = serializers.CharField(source='asset.asset_tag', read_only=True)
    asset_name = serializers.CharField(source='asset.name', read_only=True)
    result_display = serializers.CharField(source='get_result_display', read_only=True)

    class Meta:
        model = InventoryInspectionItem
        fields = ['id', 'inspection', 'asset', 'asset_tag', 'asset_name', 'result', 'result_display', 'note']
        read_only_fields = ['id']


class InventoryInspectionSerializer(serializers.ModelSerializer):
    location_name = serializers.CharField(source='location.name', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    item_count = serializers.IntegerField(source='items.count', read_only=True)

    class Meta:
        model = InventoryInspection
        fields = ['id', 'location', 'location_name', 'inspection_date', 'inspector_name',
                  'notes', 'status', 'status_display', 'item_count', 'created_at']
        read_only_fields = ['id', 'created_at']


class AssetDisposalSerializer(serializers.ModelSerializer):
    asset_tag = serializers.CharField(source='asset.asset_tag', read_only=True)
    asset_name = serializers.CharField(source='asset.name', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model = AssetDisposal
        fields = ['id', 'asset', 'asset_tag', 'asset_name', 'reason', 'method', 'status',
                  'status_display', 'proposed_date', 'disposal_date', 'reference', 'created_at']
        read_only_fields = ['id', 'created_at']

    def validate(self, attrs):
        if attrs.get('status') == AssetDisposal.DISPOSED:
            if not attrs.get('disposal_date', getattr(self.instance, 'disposal_date', None)):
                raise serializers.ValidationError({'disposal_date': 'Disposal date is required when marked disposed.'})
            if not attrs.get('method', getattr(self.instance, 'method', '')):
                raise serializers.ValidationError({'method': 'Disposal method is required when marked disposed.'})
        return attrs


class ModuleSerializer(serializers.ModelSerializer):
    student_count = serializers.SerializerMethodField()
    session_count = serializers.SerializerMethodField()
    theory_count = serializers.SerializerMethodField()
    practical_count = serializers.SerializerMethodField()
    class_level_name = serializers.CharField(source='class_level.name', read_only=True)
    semester_label = serializers.CharField(source='semester.label', read_only=True)

    class Meta:
        model = Module
        fields = [
            'id', 'name', 'code', 'teacher', 'has_practical', 'is_field_module', 'credits',
            'class_level', 'class_level_name',
            'semester', 'semester_label',
            'student_count', 'session_count', 'theory_count', 'practical_count',
            'created_at',
        ]
        read_only_fields = ['id', 'created_at']

    def validate(self, attrs):
        if attrs.get('is_field_module'):
            attrs['has_practical'] = False
        return attrs

    def get_student_count(self, obj):
        return obj.students.count()

    def get_session_count(self, obj):
        return obj.sessions.count()

    def get_theory_count(self, obj):
        return obj.sessions.filter(session_type=Session.THEORY).count()

    def get_practical_count(self, obj):
        return obj.sessions.filter(session_type=Session.PRACTICAL).count()


class StudentSerializer(serializers.ModelSerializer):
    portal_pin = serializers.CharField(
        write_only=True, required=False, allow_blank=False, min_length=6
    )
    has_portal_pin = serializers.BooleanField(read_only=True)
    module_name = serializers.CharField(source='module.name', read_only=True)
    module_code = serializers.CharField(source='module.code', read_only=True)
    class_level_name = serializers.CharField(source='module.class_level.name', read_only=True)
    class_level_id = serializers.IntegerField(source='module.class_level.id', read_only=True)
    semester_label = serializers.CharField(source='module.semester.label', read_only=True)
    semester_id = serializers.IntegerField(source='module.semester.id', read_only=True)
    sessions_attended = serializers.SerializerMethodField()
    sessions_sick = serializers.SerializerMethodField()
    sessions_absent = serializers.SerializerMethodField()
    sessions_total = serializers.SerializerMethodField()
    theory_total = serializers.SerializerMethodField()
    practical_total = serializers.SerializerMethodField()
    attendance_pct = serializers.SerializerMethodField()

    class Meta:
        model = Student
        fields = [
            'id', 'nactvet_reg_no', 'name',
            'module', 'module_name', 'module_code',
            'class_level_id', 'class_level_name',
            'semester_id', 'semester_label',
            'sessions_attended', 'sessions_sick', 'sessions_absent',
            'sessions_total', 'theory_total', 'practical_total',
            'attendance_pct', 'has_portal_pin', 'portal_pin', 'created_at',
        ]
        read_only_fields = ['id', 'created_at']

    def create(self, validated_data):
        portal_pin = validated_data.pop('portal_pin', None)
        student = super().create(validated_data)
        if portal_pin:
            student.set_portal_pin(portal_pin)
            student.save(update_fields=['portal_pin_hash', 'must_change_portal_password'])
        return student

    def update(self, instance, validated_data):
        portal_pin = validated_data.pop('portal_pin', None)
        student = super().update(instance, validated_data)
        if portal_pin:
            student.set_portal_pin(portal_pin)
            student.save(update_fields=['portal_pin_hash', 'must_change_portal_password'])
        return student

    # Each of the fields below used to run its own `.filter(...).count()` query
    # (8 queries per student row total). `.all()` reuses attendance_records /
    # module.sessions prefetches when the caller set them up, and the counting
    # is cached per-instance so repeated field access within one row still
    # costs at most one query for records and one for sessions, instead of
    # eight — this used to make listing a class of N students cost 8N queries.
    def _attendance_records(self, obj):
        if not hasattr(obj, '_cached_attendance_records'):
            obj._cached_attendance_records = list(obj.attendance_records.all())
        return obj._cached_attendance_records

    def _module_sessions(self, obj):
        if not hasattr(obj, '_cached_module_sessions'):
            obj._cached_module_sessions = list(obj.module.sessions.all())
        return obj._cached_module_sessions

    def get_sessions_attended(self, obj):
        return sum(1 for r in self._attendance_records(obj) if r.status == 'P')

    def get_sessions_sick(self, obj):
        return sum(1 for r in self._attendance_records(obj) if r.status == 'S')

    def get_sessions_absent(self, obj):
        return sum(1 for r in self._attendance_records(obj) if r.status == 'A')

    def get_sessions_total(self, obj):
        return len(self._module_sessions(obj))

    def get_theory_total(self, obj):
        return sum(1 for s in self._module_sessions(obj) if s.session_type == Session.THEORY)

    def get_practical_total(self, obj):
        return sum(1 for s in self._module_sessions(obj) if s.session_type == Session.PRACTICAL)

    def get_attendance_pct(self, obj):
        total = len(self._module_sessions(obj))
        if not total:
            return 0
        effective = sum(1 for r in self._attendance_records(obj) if r.status in ('P', 'S'))
        return round((effective / total) * 100)


class FinanceStudentSerializer(serializers.ModelSerializer):
    module_name = serializers.CharField(source='module.name', read_only=True)
    module_code = serializers.CharField(source='module.code', read_only=True)
    class_level_name = serializers.CharField(source='module.class_level.name', read_only=True)
    semester_label = serializers.CharField(source='module.semester.label', read_only=True)

    class Meta:
        model = Student
        fields = [
            'id', 'nactvet_reg_no', 'name', 'module_name', 'module_code',
            'class_level_name', 'semester_label',
        ]


class PaymentCategorySerializer(serializers.ModelSerializer):
    category_type_display = serializers.CharField(source='get_category_type_display', read_only=True)

    class Meta:
        model = PaymentCategory
        fields = [
            'id', 'name', 'category_type', 'category_type_display',
            'semester', 'class_level', 'default_amount', 'installment_count',
            'is_active', 'created_at',
        ]
        read_only_fields = ['id', 'created_at']


class StudentFinanceObligationSerializer(serializers.ModelSerializer):
    student_name = serializers.CharField(source='student.name', read_only=True)
    student_reg_no = serializers.CharField(source='student.nactvet_reg_no', read_only=True)
    class_level = serializers.CharField(source='student.module.class_level.name', read_only=True)
    semester_label = serializers.CharField(source='semester.label', read_only=True)
    module_name = serializers.CharField(source='module.name', read_only=True)
    module_code = serializers.CharField(source='module.code', read_only=True)
    obligation_type_display = serializers.CharField(source='get_obligation_type_display', read_only=True)
    category_name = serializers.CharField(source='category.name', read_only=True)
    category_type = serializers.CharField(source='category.category_type', read_only=True)
    amount_paid = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    balance = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    is_finance_cleared = serializers.BooleanField(read_only=True)

    class Meta:
        model = StudentFinanceObligation
        fields = [
            'id', 'student', 'student_name', 'student_reg_no', 'class_level',
            'semester', 'semester_label', 'module', 'module_name', 'module_code',
            'obligation_type', 'obligation_type_display', 'category', 'category_name',
            'category_type', 'amount_required', 'amount_paid', 'balance',
            'is_finance_cleared', 'note', 'created_at',
        ]
        read_only_fields = ['id', 'created_at']
        extra_kwargs = {
            'category': {'required': False, 'allow_null': True},
            'amount_required': {'required': False},
        }


class StudentPaymentSerializer(serializers.ModelSerializer):
    student_name = serializers.CharField(source='student.name', read_only=True)
    student_reg_no = serializers.CharField(source='student.nactvet_reg_no', read_only=True)
    module_name = serializers.CharField(source='student.module.name', read_only=True)
    module_code = serializers.CharField(source='student.module.code', read_only=True)
    class_level = serializers.CharField(source='student.module.class_level.name', read_only=True)
    semester = serializers.CharField(source='student.module.semester.label', read_only=True)
    category_name = serializers.CharField(source='category.name', read_only=True)
    category_type = serializers.CharField(source='category.category_type', read_only=True)
    category_type_display = serializers.CharField(source='category.get_category_type_display', read_only=True)
    balance = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = StudentPayment
        fields = [
            'id', 'student', 'student_name', 'student_reg_no',
            'module_name', 'module_code', 'class_level', 'semester',
            'category', 'category_name', 'category_type', 'category_type_display',
            'obligation',
            'amount_required', 'amount_paid', 'balance',
            'installment_number', 'payment_date', 'reference', 'note', 'created_at',
        ]
        read_only_fields = ['id', 'created_at']


class StudentFinanceClearanceSerializer(serializers.ModelSerializer):
    student_name = serializers.CharField(source='student.name', read_only=True)
    student_reg_no = serializers.CharField(source='student.nactvet_reg_no', read_only=True)
    class_level = serializers.CharField(source='student.module.class_level.name', read_only=True)
    semester_label = serializers.CharField(source='semester.label', read_only=True)
    period_display = serializers.CharField(source='get_period_display', read_only=True)

    class Meta:
        model = StudentFinanceClearance
        fields = [
            'id', 'student', 'student_name', 'student_reg_no', 'class_level',
            'semester', 'semester_label', 'period', 'period_display',
            'is_cleared', 'note', 'updated_at',
        ]
        read_only_fields = ['id', 'authority_grade', 'authority_status', 'updated_at']


class AttendanceRecordSerializer(serializers.ModelSerializer):
    class Meta:
        model = AttendanceRecord
        fields = ['id', 'student', 'status', 'sick_note', 'certificate_submitted']


class SessionSerializer(serializers.ModelSerializer):
    module_name = serializers.CharField(source='module.name', read_only=True)
    session_type_display = serializers.CharField(source='get_session_type_display', read_only=True)
    exam_period_display = serializers.CharField(source='get_exam_period_display', read_only=True)
    records = AttendanceRecordSerializer(many=True, read_only=True)
    present_count = serializers.SerializerMethodField()
    sick_count = serializers.SerializerMethodField()
    absent_count = serializers.SerializerMethodField()
    attendance_pct = serializers.SerializerMethodField()

    class Meta:
        model = Session
        fields = [
            'id', 'module', 'module_name',
            'session_type', 'session_type_display',
            'exam_period', 'exam_period_display',
            'date', 'label', 'topic',
            'records', 'present_count', 'sick_count', 'absent_count',
            'attendance_pct', 'created_at',
        ]
        read_only_fields = ['id', 'created_at']

    def get_present_count(self, obj):
        return obj.records.filter(status='P').count()

    def get_sick_count(self, obj):
        return obj.records.filter(status='S').count()

    def get_absent_count(self, obj):
        return obj.records.filter(status='A').count()

    def get_attendance_pct(self, obj):
        total = obj.records.count()
        if not total:
            return 0
        return round((obj.records.filter(status__in=['P', 'S']).count() / total) * 100)


class SessionCreateSerializer(serializers.ModelSerializer):
    records = serializers.ListField(
        child=serializers.DictField(), write_only=True, required=False
    )

    class Meta:
        model = Session
        fields = ['id', 'module', 'session_type', 'exam_period', 'date', 'label', 'topic', 'records']
        read_only_fields = ['id']

    def validate(self, attrs):
        current = self.instance
        label = str(attrs.get('label', current.label if current else '')).strip()
        if 'label' in attrs:
            attrs['label'] = label
        duplicate = Session.objects.filter(
            module=attrs.get('module', current.module if current else None),
            session_type=attrs.get('session_type', current.session_type if current else Session.THEORY),
            exam_period=attrs.get('exam_period', current.exam_period if current else Session.GENERAL),
            date=attrs.get('date', current.date if current else None),
            label__iexact=label,
        )
        if self.instance:
            duplicate = duplicate.exclude(pk=self.instance.pk)
        if duplicate.exists():
            raise serializers.ValidationError({
                'detail': (
                    'This attendance session has already been recorded. '
                    'Use Attendance Upload History to remove the incorrect copy first.'
                )
            })
        return attrs

    def create(self, validated_data):
        records_data = validated_data.pop('records', [])
        session = Session.objects.create(**validated_data)
        for rec in records_data:
            reg_no = rec.get('nactvet_reg_no', '')
            status = rec.get('status', 'P')
            if status not in ('P', 'A', 'S'):
                status = 'P'
            sick_note = str(rec.get('sick_note', '')).strip() if status == 'S' else ''
            try:
                student = Student.objects.get(nactvet_reg_no=reg_no, module=session.module)
                AttendanceRecord.objects.create(
                    session=session, student=student, status=status, sick_note=sick_note
                )
            except Student.DoesNotExist:
                pass
        return session

    @transaction.atomic
    def update(self, instance, validated_data):
        """Update session details and any submitted attendance roster in place."""
        records_data = validated_data.pop('records', None)
        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.save()

        if records_data is not None:
            students = {
                student.nactvet_reg_no: student
                for student in Student.objects.filter(module=instance.module)
            }
            for rec in records_data:
                reg_no = str(rec.get('nactvet_reg_no', '')).strip()
                student = students.get(reg_no)
                if not student:
                    continue
                record_status = str(rec.get('status', AttendanceRecord.PRESENT)).upper()
                if record_status not in (
                    AttendanceRecord.PRESENT,
                    AttendanceRecord.ABSENT,
                    AttendanceRecord.SICK,
                ):
                    raise serializers.ValidationError({'detail': f'Invalid attendance status for {reg_no}.'})
                sick_note = str(rec.get('sick_note', '')).strip() if record_status == AttendanceRecord.SICK else ''
                defaults = {'status': record_status, 'sick_note': sick_note}
                if record_status != AttendanceRecord.SICK:
                    defaults['certificate_submitted'] = False
                AttendanceRecord.objects.update_or_create(
                    session=instance, student=student, defaults=defaults
                )
        return instance


class StudentResultSerializer(serializers.ModelSerializer):
    student_name   = serializers.CharField(source='student.name',           read_only=True)
    student_reg_no = serializers.CharField(source='student.nactvet_reg_no', read_only=True)
    module_id      = serializers.IntegerField(source='student.module.id',   read_only=True)
    has_practical  = serializers.BooleanField(source='student.module.has_practical', read_only=True)
    is_field_module = serializers.BooleanField(source='student.module.is_field_module', read_only=True)

    # Weighted marks (read-only, computed)
    assign1_w      = serializers.SerializerMethodField()
    assign2_w      = serializers.SerializerMethodField()
    cat1_theory_w  = serializers.SerializerMethodField()
    cat2_theory_w  = serializers.SerializerMethodField()
    cat1_prac_w    = serializers.SerializerMethodField()
    cat2_prac_w    = serializers.SerializerMethodField()

    # Totals & eligibility
    theory_ca          = serializers.SerializerMethodField()
    practical_ca       = serializers.SerializerMethodField()
    total_ca           = serializers.SerializerMethodField()
    theory_eligible    = serializers.SerializerMethodField()
    practical_eligible = serializers.SerializerMethodField()
    ca_eligible        = serializers.SerializerMethodField()

    # End-of-semester exam (weighted, read-only)
    end_theory_w  = serializers.SerializerMethodField()
    end_prac_w    = serializers.SerializerMethodField()
    end_exam_total = serializers.SerializerMethodField()
    final_total   = serializers.SerializerMethodField()
    end_exam_mark = serializers.SerializerMethodField()
    supplementary_required = serializers.SerializerMethodField()
    failed_end_components = serializers.SerializerMethodField()
    result_status = serializers.SerializerMethodField()
    grade = serializers.SerializerMethodField()
    grade_point = serializers.SerializerMethodField()
    grade_description = serializers.SerializerMethodField()

    class Meta:
        model  = StudentResult
        fields = [
            'id', 'student', 'student_name', 'student_reg_no', 'module_id', 'has_practical', 'is_field_module',
            'field_ca',
            'assign1', 'assign2', 'cat1_theory', 'cat2_theory', 'cat1_practical', 'cat2_practical',
            'assign1_absent', 'assign2_absent', 'cat1_theory_absent', 'cat2_theory_absent',
            'cat1_practical_absent', 'cat2_practical_absent',
            'assign1_w', 'assign2_w', 'cat1_theory_w', 'cat2_theory_w', 'cat1_prac_w', 'cat2_prac_w',
            'theory_ca', 'practical_ca', 'total_ca',
            'theory_eligible', 'practical_eligible', 'ca_eligible',
            'end_theory', 'end_practical',
            'end_theory_absent', 'end_practical_absent',
            'end_theory_w', 'end_prac_w', 'end_exam_total', 'final_total',
            'supplementary_mark', 'end_exam_mark', 'supplementary_required',
            'failed_end_components',
            'result_status', 'grade', 'grade_point', 'grade_description',
            'authority_grade', 'authority_status',
            'ca_approved', 'final_approved',
            'updated_at',
        ]
        read_only_fields = ['id', 'updated_at']

    def _hp(self, obj):
        return obj.student.module.has_practical

    def _field(self, obj):
        return obj.student.module.is_field_module

    def _mark(self, obj, field):
        """An explicitly absent assessment is complete and contributes zero."""
        return 0 if getattr(obj, f'{field}_absent') else getattr(obj, field)

    def _complete(self, obj, fields):
        return all(getattr(obj, field) is not None or getattr(obj, f'{field}_absent') for field in fields)

    def validate(self, attrs):
        # A component cannot simultaneously contain a mark and be absent. A newly
        # entered mark clears ABS; selecting ABS clears any existing mark.
        for field in ('assign1', 'assign2', 'cat1_theory', 'cat2_theory',
                      'cat1_practical', 'cat2_practical', 'end_theory', 'end_practical'):
            absent_field = f'{field}_absent'
            if attrs.get(absent_field) is True:
                attrs[field] = None
            elif field in attrs and attrs[field] is not None:
                attrs[absent_field] = False
        return attrs

    # ── Individual weighted fields ──────────────────────────────────────────────
    def get_assign1_w(self, obj):     return _wt(self._mark(obj, 'assign1'),        2 if self._hp(obj) else 5)
    def get_assign2_w(self, obj):     return _wt(self._mark(obj, 'assign2'),        2 if self._hp(obj) else 5)
    def get_cat1_theory_w(self, obj): return _wt(self._mark(obj, 'cat1_theory'),    8 if self._hp(obj) else 15)
    def get_cat2_theory_w(self, obj): return _wt(self._mark(obj, 'cat2_theory'),    8 if self._hp(obj) else 15)
    def get_cat1_prac_w(self, obj):   return _wt(self._mark(obj, 'cat1_practical'), 10) if self._hp(obj) else None
    def get_cat2_prac_w(self, obj):   return _wt(self._mark(obj, 'cat2_practical'), 10) if self._hp(obj) else None

    # ── Sub-totals ──────────────────────────────────────────────────────────────
    def get_theory_ca(self, obj):
        if self._field(obj):
            return _wt(obj.field_ca, 40)
        vals = [self.get_assign1_w(obj), self.get_assign2_w(obj),
                self.get_cat1_theory_w(obj), self.get_cat2_theory_w(obj)]
        filled = [v for v in vals if v is not None]
        return round(sum(filled), 2) if filled else None

    def get_practical_ca(self, obj):
        if not self._hp(obj):
            return None
        vals = [self.get_cat1_prac_w(obj), self.get_cat2_prac_w(obj)]
        filled = [v for v in vals if v is not None]
        return round(sum(filled), 2) if filled else None

    def get_total_ca(self, obj):
        if self._field(obj):
            return _wt(obj.field_ca, 40)
        required = ['assign1', 'assign2', 'cat1_theory', 'cat2_theory']
        if self._hp(obj):
            required += ['cat1_practical', 'cat2_practical']
        if not self._complete(obj, required):
            return None
        t = self.get_theory_ca(obj)
        p = self.get_practical_ca(obj)
        if t is None and p is None:
            return None
        return round((t or 0) + (p or 0), 2)

    # ── Eligibility (only set when all required marks are present) ──────────────
    def get_theory_eligible(self, obj):
        if self._field(obj):
            return None if obj.field_ca is None else float(obj.field_ca) >= 50
        if not self._complete(obj, ['assign1', 'assign2', 'cat1_theory', 'cat2_theory']):
            return None
        t = self.get_theory_ca(obj)
        return t >= (10 if self._hp(obj) else 20) if t is not None else None

    def get_practical_eligible(self, obj):
        if not self._hp(obj):
            return None
        if not self._complete(obj, ['cat1_practical', 'cat2_practical']):
            return None
        p = self.get_practical_ca(obj)
        return p >= 10 if p is not None else None

    def get_ca_eligible(self, obj):
        t_elig = self.get_theory_eligible(obj)
        if not self._hp(obj):
            return t_elig
        p_elig = self.get_practical_eligible(obj)
        if t_elig is None or p_elig is None:
            return None
        return t_elig and p_elig

    # ── End-of-semester exam ───────────────────────────────────────────────────
    def get_end_theory_w(self, obj):
        weight = 30 if self._hp(obj) else 60
        return _wt(self._mark(obj, 'end_theory'), weight)

    def get_end_prac_w(self, obj):
        return _wt(self._mark(obj, 'end_practical'), 30) if self._hp(obj) else None

    def get_end_exam_total(self, obj):
        end_fields = ['end_theory'] + (['end_practical'] if self._hp(obj) else [])
        if not self._complete(obj, end_fields):
            return None
        theory = self.get_end_theory_w(obj)
        practical = self.get_end_prac_w(obj)
        return round((theory or 0) + (practical or 0), 2)

    def get_final_total(self, obj):
        end_fields = ['end_theory'] + (['end_practical'] if self._hp(obj) else [])
        if self.get_total_ca(obj) is None or not self._complete(obj, end_fields):
            return None
        ca = self.get_total_ca(obj)
        end_total = self.get_end_exam_total(obj)
        if ca is None and end_total is None:
            return None
        return round((ca or 0) + (end_total or 0), 2)

    def _outcome(self, obj):
        return result_outcome(obj, self)

    def get_end_exam_mark(self, obj):
        return self._outcome(obj)['end_exam_mark']

    def get_supplementary_required(self, obj):
        return self._outcome(obj)['supplementary_required']

    def get_failed_end_components(self, obj):
        return self._outcome(obj).get('failed_end_components', [])

    def get_result_status(self, obj):
        return self._outcome(obj)['status']

    def get_grade(self, obj):
        return self._outcome(obj)['grade']

    def get_grade_point(self, obj):
        return self._outcome(obj)['grade_point']

    def get_grade_description(self, obj):
        return self._outcome(obj)['grade_description']


class BulkStudentSerializer(serializers.Serializer):
    module = serializers.PrimaryKeyRelatedField(queryset=Module.objects.all(), required=False, allow_null=True)
    students = serializers.ListField(child=serializers.DictField())

    def _resolve_module(self, value):
        if value is None:
            return None
        if isinstance(value, Module):
            return value
        if isinstance(value, int):
            return Module.objects.filter(id=value).first()
        try:
            raw = str(value).strip()
        except Exception:
            return None
        if not raw:
            return None
        if raw.isdigit():
            module = Module.objects.filter(id=int(raw)).first()
            if module:
                return module
        module = Module.objects.filter(code__iexact=raw).first()
        if module:
            return module
        module = Module.objects.filter(name__iexact=raw).first()
        if module:
            return module
        module = Module.objects.filter(code__icontains=raw).first()
        if module:
            return module
        return Module.objects.filter(name__icontains=raw).first()

    def validate(self, data):
        user = self.context.get('user')
        module = data.get('module')
        if module and user and not user.is_staff:
            if module not in user.modules_taught.all():
                raise PermissionDenied('You may only add students to modules you tutor.')
        return data

    def create(self, validated_data):
        default_module = validated_data.get('module')
        rows = validated_data['students']
        user = self.context.get('user')
        allowed_module_ids = None
        if user and not user.is_staff:
            allowed_module_ids = set(user.modules_taught.values_list('id', flat=True))

        added, skipped, pin_skipped = 0, 0, 0
        for row in rows:
            reg_no = str(row.get('nactvet_reg_no', '')).strip().upper()
            name = str(row.get('name', '')).strip()
            portal_pin = str(row.get('portal_pin', '')).strip()
            if not reg_no or not name:
                skipped += 1
                continue
            module = default_module
            module_source = row.get('module') or row.get('module_code') or row.get('module_name') or row.get('code')
            if module_source:
                resolved = self._resolve_module(module_source)
                if resolved:
                    module = resolved
            if not module:
                skipped += 1
                continue
            if allowed_module_ids is not None and module.id not in allowed_module_ids:
                raise PermissionDenied('You may only add students to modules you tutor.')
            student, created = Student.objects.get_or_create(
                nactvet_reg_no=reg_no, module=module,
                defaults={'name': name}
            )
            if created:
                # Only ever set a PIN here for the student we just created — a
                # too-short PIN just means "no PIN yet", it must never discard
                # the (valid) student record that was just added.
                if portal_pin:
                    if len(portal_pin) < 6:
                        pin_skipped += 1
                    else:
                        student.set_portal_pin(portal_pin)
                        student.save(update_fields=['portal_pin_hash', 'must_change_portal_password'])
                added += 1
            else:
                # An existing enrollment is just skipped. Re-uploading the same
                # roster (which may still carry a PIN column from a previous
                # import) must never silently reset an active student's login
                # PIN — use the dedicated "Set Password/PIN" action for that.
                skipped += 1
        return {'added': added, 'skipped': skipped, 'pin_skipped': pin_skipped}


class AnnouncementSerializer(serializers.ModelSerializer):
    # Written on upload, never read back — downloads only ever go through the
    # gated announcement_download view (see download_url), never a raw path.
    file = serializers.FileField(write_only=True)
    uploaded_by_name = serializers.SerializerMethodField()
    download_url = serializers.SerializerMethodField()

    class Meta:
        model = Announcement
        fields = ['id', 'title', 'note', 'file', 'uploaded_by_name', 'download_url', 'created_at']
        read_only_fields = ['id', 'created_at']

    def validate_file(self, value):
        if not value.name.lower().endswith('.pdf'):
            raise serializers.ValidationError('Only PDF files are accepted.')
        if value.size > MAX_ANNOUNCEMENT_FILE_BYTES:
            raise serializers.ValidationError('File must be 10MB or smaller.')
        return value

    def get_uploaded_by_name(self, obj):
        if not obj.uploaded_by:
            return 'Unknown'
        return obj.uploaded_by.get_full_name() or obj.uploaded_by.username

    def get_download_url(self, obj):
        return reverse('announcement-download', args=[obj.id])


# ── FEES LEDGER ───────────────────────────────────────────────────────────────
#
# Read serializers only expose derived figures; nothing here lets a client set
# a balance. Money enters the system through the finance service layer, which
# is the only place that writes Payment rows and the audit trail.


class ChargeTypeSerializer(serializers.ModelSerializer):
    family_display = serializers.CharField(source='get_family_display', read_only=True)
    applies_display = serializers.CharField(source='get_applies_display', read_only=True)
    frequency_display = serializers.CharField(source='get_frequency_display', read_only=True)
    group_label = serializers.CharField(read_only=True)
    bank_account_label = serializers.CharField(source='bank_account.purpose', read_only=True, default='')
    in_use = serializers.SerializerMethodField()

    class Meta:
        model = ChargeType
        fields = [
            'id', 'name', 'code', 'sort_order', 'family', 'family_display',
            'applies', 'applies_display',
            'frequency', 'frequency_display', 'invoice_group', 'group_label',
            'bank_account', 'bank_account_label',
            'blocks_registration', 'blocks_cat1', 'blocks_cat2', 'blocks_final',
            'blocks_results', 'is_active', 'in_use', 'created_at',
        ]
        read_only_fields = ['id', 'created_at']

    def get_in_use(self, obj):
        """Whether charges already exist — the UI warns before editing one."""
        return obj.charges.exists()


class FeeInstallmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = FeeInstallment
        fields = ['id', 'number', 'amount', 'due_date']
        read_only_fields = ['id']


class FeeStructureSerializer(serializers.ModelSerializer):
    charge_type_name = serializers.CharField(source='charge_type.name', read_only=True)
    family = serializers.CharField(source='charge_type.family', read_only=True)
    applies = serializers.CharField(source='charge_type.applies', read_only=True)
    class_level_name = serializers.CharField(source='class_level.name', read_only=True)
    academic_year_name = serializers.CharField(source='academic_year.name', read_only=True)
    billing_period_display = serializers.CharField(source='get_billing_period_display', read_only=True)
    schedule = FeeInstallmentSerializer(source='installment_schedule', many=True, read_only=True)
    # Write-only: the due date for each installment, in order. The service
    # layer splits the amount across them and puts any rounding remainder on
    # the last one, so a schedule always sums back to the full fee.
    due_dates = serializers.ListField(
        child=serializers.DateField(), write_only=True, required=False,
    )

    class Meta:
        model = FeeStructure
        fields = [
            'id', 'charge_type', 'charge_type_name', 'family', 'applies',
            'class_level', 'class_level_name', 'academic_year', 'academic_year_name',
            'amount', 'billing_period', 'billing_period_display', 'installments',
            'schedule', 'due_dates', 'is_active', 'created_at',
        ]
        read_only_fields = ['id', 'created_at']

    def validate(self, data):
        due_dates = data.get('due_dates')
        installments = data.get('installments', getattr(self.instance, 'installments', 1))
        if due_dates is not None and len(due_dates) != installments:
            raise serializers.ValidationError({
                'due_dates': f'Give one due date per installment — {installments} expected, '
                             f'{len(due_dates)} given.'
            })
        return data


class StudentChargeSerializer(serializers.ModelSerializer):
    charge_type_name = serializers.CharField(source='charge_type.name', read_only=True)
    family = serializers.CharField(source='charge_type.family', read_only=True)
    family_display = serializers.CharField(source='charge_type.get_family_display', read_only=True)
    student_name = serializers.CharField(source='profile.name', read_only=True)
    student_reg_no = serializers.CharField(source='profile.nactvet_reg_no', read_only=True)
    semester_label = serializers.CharField(source='semester.label', read_only=True, default=None)
    balance = serializers.SerializerMethodField()
    is_overdue = serializers.SerializerMethodField()

    class Meta:
        model = StudentCharge
        fields = [
            'id', 'profile', 'student_name', 'student_reg_no',
            'charge_type', 'charge_type_name', 'family', 'family_display',
            'academic_year', 'semester', 'semester_label',
            'installment_number', 'amount', 'waived_amount', 'waived_reason',
            'balance', 'due_date', 'is_overdue', 'source', 'note', 'created_at',
        ]
        read_only_fields = ['id', 'created_at', 'waived_amount', 'waived_reason']

    def get_balance(self, obj):
        return str(finance.charge_balance(obj))

    def get_is_overdue(self, obj):
        return obj.is_overdue


class InvoiceLineSerializer(serializers.ModelSerializer):
    charge_type_name = serializers.CharField(source='charge.charge_type.name', read_only=True)
    installment_number = serializers.IntegerField(source='charge.installment_number', read_only=True)
    due_date = serializers.DateField(source='charge.due_date', read_only=True)

    class Meta:
        model = InvoiceLine
        fields = ['id', 'charge', 'charge_type_name', 'installment_number', 'due_date', 'amount']


class InvoiceSerializer(serializers.ModelSerializer):
    student_name = serializers.CharField(source='profile.name', read_only=True)
    student_reg_no = serializers.CharField(source='profile.nactvet_reg_no', read_only=True)
    academic_year_name = serializers.CharField(source='academic_year.name', read_only=True)
    lines = InvoiceLineSerializer(many=True, read_only=True)
    total = serializers.SerializerMethodField()
    paid = serializers.SerializerMethodField()
    outstanding = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()
    expires_on = serializers.DateField(read_only=True)
    installment_count = serializers.SerializerMethodField()
    bank_name = serializers.CharField(source='bank_account.bank_name', read_only=True, default='')
    bank_account_number = serializers.CharField(
        source='bank_account.account_number', read_only=True, default='')
    bank_account_purpose = serializers.CharField(
        source='bank_account.purpose', read_only=True, default='')

    class Meta:
        model = Invoice
        fields = [
            'id', 'reference', 'profile', 'student_name', 'student_reg_no',
            'academic_year', 'academic_year_name', 'issued_on', 'due_date',
            'invoice_group', 'bank_account', 'bank_name', 'bank_account_number',
            'bank_account_purpose', 'source', 'lines', 'total', 'paid', 'outstanding',
            'status', 'expires_on', 'installment_count',
            'cancelled', 'cancelled_reason', 'created_at',
        ]
        read_only_fields = ['id', 'reference', 'issued_on', 'created_at']

    def get_total(self, obj):
        return str(finance.money(obj.total))

    def _paid(self, obj):
        # Three fields need this and it costs a query, so work it out once per
        # invoice rather than once per field.
        if not hasattr(obj, '_paid_cache'):
            obj._paid_cache = finance.invoice_paid(obj)
        return obj._paid_cache

    def get_paid(self, obj):
        return str(self._paid(obj))

    def get_outstanding(self, obj):
        return str(max(finance.money(obj.total) - self._paid(obj), finance.money(0)))

    def get_status(self, obj):
        return finance.invoice_status(obj)

    def get_installment_count(self, obj):
        """How many instalments this one reference covers — the whole point of
        the invoice, so it belongs on the wire.

        len() rather than .count() so a prefetched queryset is not re-queried
        once per invoice."""
        return len(obj.lines.all())


class PaymentAllocationSerializer(serializers.ModelSerializer):
    charge_type_name = serializers.CharField(source='charge.charge_type.name', read_only=True)
    installment_number = serializers.IntegerField(source='charge.installment_number', read_only=True)

    class Meta:
        model = PaymentAllocation
        fields = ['id', 'charge', 'charge_type_name', 'installment_number', 'amount']


class PaymentSerializer(serializers.ModelSerializer):
    """Read-only view of the ledger. Payments are created through the
    record-payment action and never edited — a correction is a reversal."""
    student_name = serializers.CharField(source='profile.name', read_only=True)
    student_reg_no = serializers.CharField(source='profile.nactvet_reg_no', read_only=True)
    invoice_reference = serializers.CharField(source='invoice.reference', read_only=True, default=None)
    channel_display = serializers.CharField(source='get_channel_display', read_only=True)
    payer_relation_display = serializers.CharField(source='get_payer_relation_display', read_only=True)
    recorded_by_name = serializers.CharField(source='recorded_by.username', read_only=True)
    allocations = PaymentAllocationSerializer(many=True, read_only=True)
    is_reversal = serializers.BooleanField(read_only=True)
    is_reversed = serializers.SerializerMethodField()

    class Meta:
        model = Payment
        fields = [
            'id', 'profile', 'student_name', 'student_reg_no',
            'invoice', 'invoice_reference', 'amount', 'payment_date',
            'channel', 'channel_display', 'bank_reference', 'efd_receipt_no',
            'payer_name', 'payer_relation', 'payer_relation_display',
            'note', 'allocations', 'is_reversal', 'is_reversed',
            'reverses', 'reversal_reason', 'recorded_by_name', 'created_at',
        ]
        read_only_fields = fields

    def get_is_reversed(self, obj):
        return hasattr(obj, 'reversal')


class RecordPaymentSerializer(serializers.Serializer):
    """What the accountant types in while holding the slip."""
    profile = serializers.PrimaryKeyRelatedField(queryset=StudentProfile.objects.all())
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal('0.01'))
    payment_date = serializers.DateField()
    invoice = serializers.PrimaryKeyRelatedField(
        queryset=Invoice.objects.all(), required=False, allow_null=True,
    )
    channel = serializers.ChoiceField(choices=Payment.CHANNEL_CHOICES, default=Payment.CRDB)
    bank_reference = serializers.CharField(max_length=100, required=False, allow_blank=True, default='')
    efd_receipt_no = serializers.CharField(max_length=100, required=False, allow_blank=True, default='')
    payer_name = serializers.CharField(max_length=200, required=False, allow_blank=True, default='')
    payer_relation = serializers.ChoiceField(choices=Payment.PAYER_CHOICES, default=Payment.SELF)
    proof = serializers.FileField(required=False, allow_null=True)
    note = serializers.CharField(max_length=300, required=False, allow_blank=True, default='')

    def validate_payment_date(self, value):
        # The date on the slip. It may be in the past — often is, when the
        # office records a week late — but it cannot be in the future.
        if value > date.today():
            raise serializers.ValidationError('A payment cannot be dated in the future.')
        return value

    def validate(self, data):
        invoice = data.get('invoice')
        if invoice and invoice.profile_id != data['profile'].id:
            raise serializers.ValidationError({
                'invoice': 'That invoice belongs to a different student.'
            })
        if invoice and invoice.cancelled:
            raise serializers.ValidationError({'invoice': 'That invoice was cancelled.'})

        # A receipt may only be banked once. Catches the double-keyed slip and
        # the same M-Pesa code claimed twice.
        for field, label in (('bank_reference', 'bank reference'), ('efd_receipt_no', 'EFD receipt number')):
            value = (data.get(field) or '').strip()
            if value and Payment.objects.filter(**{field: value}, reverses__isnull=True).exists():
                raise serializers.ValidationError({
                    field: f'That {label} has already been recorded.'
                })
        return data


class ReversePaymentSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=300)


class WaiveChargeSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal('0.00'))
    reason = serializers.CharField(max_length=300)


class FinanceOverrideSerializer(serializers.ModelSerializer):
    student_name = serializers.CharField(source='profile.name', read_only=True)
    student_reg_no = serializers.CharField(source='profile.nactvet_reg_no', read_only=True)
    period_display = serializers.CharField(source='get_period_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    approved_by_name = serializers.CharField(source='approved_by.username', read_only=True)

    class Meta:
        model = FinanceOverride
        fields = [
            'id', 'profile', 'student_name', 'student_reg_no',
            'academic_year', 'semester', 'period', 'period_display',
            'status', 'status_display', 'reason', 'expires_on', 'is_active',
            'approved_by_name', 'created_at',
        ]
        read_only_fields = ['id', 'created_at', 'approved_by_name']

    def validate_reason(self, value):
        # An override is the one place a human overrules the ledger. It is
        # worthless in an audit without a reason worth reading.
        if len(value.strip()) < 5:
            raise serializers.ValidationError('Give a reason — this overrules the ledger.')
        return value.strip()


class FinanceAuditLogSerializer(serializers.ModelSerializer):
    actor_name = serializers.CharField(source='actor.username', read_only=True, default='system')
    student_reg_no = serializers.CharField(source='profile.nactvet_reg_no', read_only=True, default=None)

    class Meta:
        model = FinanceAuditLog
        fields = [
            'id', 'at', 'actor_name', 'action', 'entity', 'entity_id',
            'student_reg_no', 'summary', 'before', 'after', 'ip_address',
        ]
        read_only_fields = fields


class BankAccountSerializer(serializers.ModelSerializer):
    class Meta:
        model = BankAccount
        fields = ['id', 'bank_name', 'account_name', 'account_number', 'purpose',
                  'is_active', 'created_at']
        read_only_fields = ['id', 'created_at']


class CollegeProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = CollegeProfile
        fields = ['id', 'name', 'short_name', 'po_box', 'town', 'country',
                  'phone', 'email', 'website', 'logo', 'invoice_terms', 'updated_at']
        read_only_fields = ['id', 'updated_at']


# ── EVALUATION FORMS ──────────────────────────────────────────────────────────

class ResultEntryWindowSerializer(serializers.ModelSerializer):
    semester_label = serializers.CharField(source='semester.label', read_only=True)
    kind_label = serializers.CharField(source='get_kind_display', read_only=True)
    status = serializers.SerializerMethodField()
    declared_by_name = serializers.SerializerMethodField()

    class Meta:
        model = ResultEntryWindow
        fields = ['id', 'semester', 'semester_label', 'kind', 'kind_label',
                  'opens_on', 'closes_on', 'is_active', 'note', 'status',
                  'declared_by_name', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']

    def get_status(self, obj):
        return obj.status()

    def get_declared_by_name(self, obj):
        if not obj.declared_by:
            return ''
        return obj.declared_by.get_full_name() or obj.declared_by.username

    def validate(self, data):
        opens = data.get('opens_on', getattr(self.instance, 'opens_on', None))
        closes = data.get('closes_on', getattr(self.instance, 'closes_on', None))
        if opens and closes and closes < opens:
            raise serializers.ValidationError(
                {'closes_on': 'The closing date cannot come before the opening date.'})
        return data


class FormQuestionSerializer(serializers.ModelSerializer):
    type_display = serializers.CharField(source='get_type_display', read_only=True)

    class Meta:
        model = FormQuestion
        fields = [
            'id', 'section', 'text', 'help_text', 'type', 'type_display', 'required',
            'order', 'options', 'rows', 'columns', 'max_rows',
        ]
        read_only_fields = ['id']

    def validate(self, data):
        """A question that cannot be answered is worse than no question.

        Caught here rather than at submission time, when it would be the
        student who hit the wall.
        """
        kind = data.get('type', getattr(self.instance, 'type', None))
        options = data.get('options', getattr(self.instance, 'options', []) or [])
        rows = data.get('rows', getattr(self.instance, 'rows', []) or [])
        columns = data.get('columns', getattr(self.instance, 'columns', []) or [])

        if kind in (FormQuestion.SINGLE_CHOICE, FormQuestion.MULTI_CHOICE) and not options:
            raise serializers.ValidationError(
                {'options': 'Give the student something to choose from.'})
        if kind == FormQuestion.MATRIX:
            if not options:
                raise serializers.ValidationError(
                    {'options': 'A rating table needs its rating columns, e.g. 1 to 5.'})
            if not rows:
                raise serializers.ValidationError(
                    {'rows': 'A rating table needs the things being rated.'})
        if kind == FormQuestion.GRID_TEXT and not columns:
            raise serializers.ValidationError(
                {'columns': 'A table of text needs its column headings.'})
        for field, values in (('options', options), ('rows', rows), ('columns', columns)):
            if values and len(set(map(str, values))) != len(values):
                raise serializers.ValidationError({field: 'Entries must be different.'})
        return data


class FormSectionSerializer(serializers.ModelSerializer):
    questions = FormQuestionSerializer(many=True, read_only=True)

    class Meta:
        model = FormSection
        fields = ['id', 'form', 'title', 'description', 'order', 'for_office', 'questions']
        read_only_fields = ['id']


class FormSerializer(serializers.ModelSerializer):
    sections = FormSectionSerializer(many=True, read_only=True)
    status = serializers.SerializerMethodField()
    response_count = serializers.SerializerMethodField()
    question_count = serializers.SerializerMethodField()
    academic_year_name = serializers.CharField(
        source='academic_year.name', read_only=True, default='')
    created_by_name = serializers.CharField(
        source='created_by.get_full_name', read_only=True, default='')
    level_names = serializers.SerializerMethodField()

    class Meta:
        model = Form
        fields = [
            'id', 'title', 'slug', 'intro', 'kind', 'audience',
            'academic_year', 'academic_year_name',
            'is_active', 'opens_on', 'closes_on', 'is_anonymous', 'allow_multiple',
            'is_mandatory', 'levels', 'level_names', 'print_note',
            'status', 'response_count', 'question_count', 'sections',
            'created_by_name', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']
        extra_kwargs = {'slug': {'required': False}}

    def get_status(self, obj):
        return obj.status()

    def get_level_names(self, obj):
        return obj.level_names

    def get_response_count(self, obj):
        return obj.responses.count()

    def get_question_count(self, obj):
        return FormQuestion.objects.filter(section__form=obj).count()

    def validate(self, data):
        def field(name):
            return data.get(name, getattr(self.instance, name, None))

        opens, closes = field('opens_on'), field('closes_on')
        if opens and closes and closes < opens:
            raise serializers.ValidationError(
                {'closes_on': 'The closing date cannot come before the opening date.'})

        # A service request is one student asking for one thing and waiting on
        # an answer. Anonymously, nobody can be told the answer; compulsorily,
        # every student is made to ask for something they may not want.
        if field('kind') == Form.REQUEST:
            if field('is_anonymous'):
                raise serializers.ValidationError({'is_anonymous':
                    'A service request cannot be anonymous — the college has to know '
                    'whose request it is answering.'})
            if field('is_mandatory'):
                raise serializers.ValidationError({'is_mandatory':
                    'A service request cannot be compulsory. Students ask for a sick '
                    'sheet or a letter when they need one.'})
        return data

    def create(self, validated_data):
        validated_data.setdefault('slug', self._slug_for(validated_data['title']))
        return super().create(validated_data)

    @staticmethod
    def _slug_for(title):
        from django.utils.text import slugify
        base = slugify(title)[:200] or 'form'
        slug, suffix = base, 2
        while Form.objects.filter(slug=slug).exists():
            slug = f'{base}-{suffix}'
            suffix += 1
        return slug


class StudentFormSerializer(serializers.ModelSerializer):
    """A form as the student sees it — no response counts, no admin fields."""
    sections = serializers.SerializerMethodField()
    level_names = serializers.CharField(read_only=True)

    class Meta:
        model = Form
        fields = ['id', 'title', 'slug', 'intro', 'kind', 'is_anonymous', 'closes_on',
                  'sections', 'level_names']

    def get_sections(self, obj):
        # The parts a health facility or a signing officer fills in on paper are
        # not part of the form the student is asked to complete.
        sections = [s for s in obj.sections.all() if not s.for_office]
        return FormSectionSerializer(sections, many=True).data


class FormResponseSerializer(serializers.ModelSerializer):
    student_name = serializers.SerializerMethodField()
    student_reg_no = serializers.SerializerMethodField()
    class_level_name = serializers.CharField(source='class_level.name', read_only=True, default='')
    answers = serializers.SerializerMethodField()

    form_title = serializers.CharField(source='form.title', read_only=True)
    form_kind = serializers.CharField(source='form.kind', read_only=True)
    decided_by_name = serializers.SerializerMethodField()
    attachments = serializers.SerializerMethodField()

    class Meta:
        model = FormResponse
        fields = ['id', 'form', 'form_title', 'form_kind', 'student_name', 'student_reg_no',
                  'class_level_name', 'submitted_at', 'answers',
                  'status', 'decision_note', 'decided_at', 'decided_by_name', 'attachments']
        read_only_fields = ['status', 'decision_note', 'decided_at']

    def get_student_name(self, obj):
        # An anonymous form has no profile at all; say so rather than showing a blank.
        return obj.profile.name if obj.profile else 'Anonymous'

    def get_student_reg_no(self, obj):
        return obj.profile.nactvet_reg_no if obj.profile else ''

    def get_answers(self, obj):
        return [
            {'question': answer.question_id, 'text': answer.question.text,
             'type': answer.question.type, 'value': answer.value}
            for answer in obj.answers.select_related('question')
        ]

    def get_attachments(self, obj):
        # No file URL: downloads go through a gated view, because a letter
        # carries the student's name, number and where they are going.
        return [
            {'id': a.id, 'name': a.display_name, 'note': a.note, 'size': a.size,
             'uploaded_at': a.uploaded_at, 'uploaded_by': (
                 a.uploaded_by.get_full_name() or a.uploaded_by.username)
             if a.uploaded_by else ''}
            for a in obj.attachments.all()
        ]

    def get_decided_by_name(self, obj):
        if not obj.decided_by:
            return ''
        return obj.decided_by.get_full_name() or obj.decided_by.username
