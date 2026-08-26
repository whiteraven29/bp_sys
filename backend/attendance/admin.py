from django.contrib import admin
from .models import (
    AcademicYear, Semester, ClassLevel, Module, Student, Session, AttendanceRecord,
    TeacherProfile, AccountantProfile, StudentResult, PaymentCategory, StudentPayment,
    StudentFinanceObligation, StudentFinanceClearance, Announcement,
    EstateOfficerProfile,
)


@admin.register(AcademicYear)
class AcademicYearAdmin(admin.ModelAdmin):
    list_display = ['name', 'is_active', 'created_at']
    list_filter = ['is_active']
    ordering = ['-name']


@admin.register(Semester)
class SemesterAdmin(admin.ModelAdmin):
    list_display = ['academic_year', 'number', 'is_active']
    list_filter = ['is_active', 'academic_year']
    ordering = ['-academic_year__name', 'number']


@admin.register(ClassLevel)
class ClassLevelAdmin(admin.ModelAdmin):
    list_display = ['name', 'order']
    ordering = ['order']


@admin.register(Announcement)
class AnnouncementAdmin(admin.ModelAdmin):
    list_display = ['title', 'uploaded_by', 'created_at']
    search_fields = ['title', 'note']
    ordering = ['-created_at']


@admin.register(TeacherProfile)
class TeacherProfileAdmin(admin.ModelAdmin):
    list_display = ['full_name', 'user']
    search_fields = ['full_name', 'user__username']


@admin.register(AccountantProfile)
class AccountantProfileAdmin(admin.ModelAdmin):
    list_display = ['full_name', 'user', 'is_active']
    list_filter = ['is_active']
    search_fields = ['full_name', 'user__username']


@admin.register(EstateOfficerProfile)
class EstateOfficerProfileAdmin(admin.ModelAdmin):
    list_display = ['full_name', 'user', 'is_active']
    list_filter = ['is_active']


@admin.register(Module)
class ModuleAdmin(admin.ModelAdmin):
    list_display = ['code', 'name', 'teacher', 'has_practical', 'class_level', 'semester']
    list_filter = ['has_practical', 'class_level', 'semester__academic_year']
    search_fields = ['name', 'code', 'teacher']


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = ['nactvet_reg_no', 'name', 'module', 'get_class_level', 'has_portal_access']
    list_filter = ['module__class_level', 'module__semester']
    search_fields = ['nactvet_reg_no', 'name']

    @admin.display(description='Class Level', ordering='module__class_level__order')
    def get_class_level(self, obj):
        return obj.module.class_level.name

    @admin.display(boolean=True, description='Portal PIN')
    def has_portal_access(self, obj):
        return obj.has_portal_pin


class AttendanceRecordInline(admin.TabularInline):
    model = AttendanceRecord
    extra = 0


@admin.register(Session)
class SessionAdmin(admin.ModelAdmin):
    list_display = ['module', 'session_type', 'exam_period', 'date', 'label', 'topic', 'get_class_level']
    list_filter = ['session_type', 'exam_period', 'module__class_level', 'module__semester', 'date']
    search_fields = ['topic', 'label']
    inlines = [AttendanceRecordInline]

    @admin.display(description='Class Level')
    def get_class_level(self, obj):
        return obj.module.class_level.name


@admin.register(AttendanceRecord)
class AttendanceRecordAdmin(admin.ModelAdmin):
    list_display = ['session', 'student', 'status', 'certificate_submitted', 'sick_note']
    list_filter = ['status', 'certificate_submitted', 'session__module__class_level', 'session__module']
    search_fields = ['sick_note', 'student__name', 'student__nactvet_reg_no']


@admin.register(StudentResult)
class StudentResultAdmin(admin.ModelAdmin):
    list_display = ['student', 'assign1', 'assign2', 'cat1_theory', 'cat2_theory', 'cat1_practical', 'cat2_practical', 'supplementary_mark', 'ca_approved', 'final_approved', 'updated_at']
    list_filter  = ['ca_approved', 'final_approved', 'student__module__class_level', 'student__module__semester', 'student__module__has_practical']
    search_fields = ['student__name', 'student__nactvet_reg_no']
    readonly_fields = ['updated_at']


@admin.register(PaymentCategory)
class PaymentCategoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'category_type', 'semester', 'class_level', 'default_amount', 'installment_count', 'is_active', 'created_by', 'created_at']
    list_filter = ['category_type', 'semester', 'class_level', 'is_active']
    search_fields = ['name']


@admin.register(StudentPayment)
class StudentPaymentAdmin(admin.ModelAdmin):
    list_display = ['student', 'category', 'obligation', 'installment_number', 'amount_required', 'amount_paid', 'balance', 'payment_date', 'reference', 'recorded_by']
    list_filter = ['category__category_type', 'payment_date', 'student__module__class_level']
    search_fields = ['student__name', 'student__nactvet_reg_no', 'reference', 'note']


@admin.register(StudentFinanceObligation)
class StudentFinanceObligationAdmin(admin.ModelAdmin):
    list_display = ['student', 'obligation_type', 'semester', 'module', 'category', 'amount_required', 'balance', 'declared_by', 'created_at']
    list_filter = ['obligation_type', 'semester', 'student__module__class_level']
    search_fields = ['student__name', 'student__nactvet_reg_no', 'note']


@admin.register(StudentFinanceClearance)
class StudentFinanceClearanceAdmin(admin.ModelAdmin):
    list_display = ['student', 'semester', 'period', 'is_cleared', 'approved_by', 'updated_at']
    list_filter = ['semester', 'period', 'is_cleared']
    search_fields = ['student__name', 'student__nactvet_reg_no', 'note']
