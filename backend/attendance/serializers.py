from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from .grading import result_outcome
from .models import (
    AcademicYear, Semester, ClassLevel, Module, Student, Session, AttendanceRecord,
    StudentResult, PaymentCategory, StudentFinanceObligation, StudentPayment,
    StudentFinanceClearance,
    EstateOfficerProfile, InventoryLocation, AssetCategory, Asset,
    AssetTransfer, AssetMaintenance, InventoryInspection, InventoryInspectionItem, AssetDisposal,
)


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
    class Meta:
        model = InventoryLocation
        fields = ['id', 'name', 'is_active']
        read_only_fields = ['id']


class AssetCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = AssetCategory
        fields = ['id', 'name', 'is_active']
        read_only_fields = ['id']


class AssetSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source='category.name', read_only=True)
    location_name = serializers.CharField(source='location.name', read_only=True)
    condition_display = serializers.CharField(source='get_condition_display', read_only=True)

    class Meta:
        model = Asset
        fields = [
            'id', 'asset_tag', 'name', 'description', 'category', 'category_name',
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
            'id', 'name', 'code', 'teacher', 'has_practical', 'credits',
            'class_level', 'class_level_name',
            'semester', 'semester_label',
            'student_count', 'session_count', 'theory_count', 'practical_count',
            'created_at',
        ]
        read_only_fields = ['id', 'created_at']

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

    def get_sessions_attended(self, obj):
        return obj.attendance_records.filter(status='P').count()

    def get_sessions_sick(self, obj):
        return obj.attendance_records.filter(status='S').count()

    def get_sessions_absent(self, obj):
        return obj.attendance_records.filter(status='A').count()

    def get_sessions_total(self, obj):
        return Session.objects.filter(module=obj.module).count()

    def get_theory_total(self, obj):
        return Session.objects.filter(module=obj.module, session_type=Session.THEORY).count()

    def get_practical_total(self, obj):
        return Session.objects.filter(module=obj.module, session_type=Session.PRACTICAL).count()

    def get_attendance_pct(self, obj):
        total = Session.objects.filter(module=obj.module).count()
        if not total:
            return 0
        effective = obj.attendance_records.filter(status__in=['P', 'S']).count()
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
    records = serializers.ListField(child=serializers.DictField(), write_only=True)

    class Meta:
        model = Session
        fields = ['id', 'module', 'session_type', 'exam_period', 'date', 'label', 'topic', 'records']
        read_only_fields = ['id']

    def validate(self, attrs):
        label = str(attrs.get('label', '')).strip()
        attrs['label'] = label
        duplicate = Session.objects.filter(
            module=attrs.get('module'),
            session_type=attrs.get('session_type', Session.THEORY),
            exam_period=attrs.get('exam_period', Session.GENERAL),
            date=attrs.get('date'),
            label__iexact=label,
        )
        if duplicate.exists():
            raise serializers.ValidationError({
                'detail': (
                    'This attendance session has already been recorded. '
                    'Use Attendance Upload History to remove the incorrect copy first.'
                )
            })
        return attrs

    def create(self, validated_data):
        records_data = validated_data.pop('records')
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


class StudentResultSerializer(serializers.ModelSerializer):
    student_name   = serializers.CharField(source='student.name',           read_only=True)
    student_reg_no = serializers.CharField(source='student.nactvet_reg_no', read_only=True)
    module_id      = serializers.IntegerField(source='student.module.id',   read_only=True)
    has_practical  = serializers.BooleanField(source='student.module.has_practical', read_only=True)

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
            'id', 'student', 'student_name', 'student_reg_no', 'module_id', 'has_practical',
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

        added, skipped = 0, 0
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
                if portal_pin:
                    if len(portal_pin) < 6:
                        student.delete()
                        skipped += 1
                        continue
                    student.set_portal_pin(portal_pin)
                    student.save(update_fields=['portal_pin_hash', 'must_change_portal_password'])
                added += 1
            else:
                if portal_pin and len(portal_pin) >= 6:
                    student.set_portal_pin(portal_pin)
                    student.save(update_fields=['portal_pin_hash', 'must_change_portal_password'])
                skipped += 1
        return {'added': added, 'skipped': skipped}
