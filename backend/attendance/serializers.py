from rest_framework import serializers
from .models import AcademicYear, Semester, ClassLevel, Module, Student, Session, AttendanceRecord


class SemesterSerializer(serializers.ModelSerializer):
    year_name = serializers.CharField(source='academic_year.name', read_only=True)
    label = serializers.CharField(read_only=True)
    module_count = serializers.SerializerMethodField()

    class Meta:
        model = Semester
        fields = ['id', 'academic_year', 'year_name', 'number', 'label', 'is_active', 'module_count']
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
            'id', 'name', 'code', 'teacher',
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
            'attendance_pct', 'created_at',
        ]
        read_only_fields = ['id', 'created_at']

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


class AttendanceRecordSerializer(serializers.ModelSerializer):
    class Meta:
        model = AttendanceRecord
        fields = ['id', 'student', 'status']


class SessionSerializer(serializers.ModelSerializer):
    module_name = serializers.CharField(source='module.name', read_only=True)
    session_type_display = serializers.CharField(source='get_session_type_display', read_only=True)
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
        fields = ['id', 'module', 'session_type', 'date', 'label', 'topic', 'records']
        read_only_fields = ['id']

    def create(self, validated_data):
        records_data = validated_data.pop('records')
        session = Session.objects.create(**validated_data)
        for rec in records_data:
            reg_no = rec.get('nactvet_reg_no', '')
            status = rec.get('status', 'P')
            if status not in ('P', 'A', 'S'):
                status = 'P'
            try:
                student = Student.objects.get(nactvet_reg_no=reg_no, module=session.module)
                AttendanceRecord.objects.create(session=session, student=student, status=status)
            except Student.DoesNotExist:
                pass
        return session


class BulkStudentSerializer(serializers.Serializer):
    module = serializers.PrimaryKeyRelatedField(queryset=Module.objects.all())
    students = serializers.ListField(child=serializers.DictField())

    def create(self, validated_data):
        module = validated_data['module']
        rows = validated_data['students']
        added, skipped = 0, 0
        for row in rows:
            reg_no = str(row.get('nactvet_reg_no', '')).strip().upper()
            name = str(row.get('name', '')).strip()
            if not reg_no or not name:
                skipped += 1
                continue
            _, created = Student.objects.get_or_create(
                nactvet_reg_no=reg_no, module=module,
                defaults={'name': name}
            )
            if created:
                added += 1
            else:
                skipped += 1
        return {'added': added, 'skipped': skipped}
