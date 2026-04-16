from django.contrib import admin
from .models import AcademicYear, Semester, ClassLevel, Module, Student, Session, AttendanceRecord, TeacherProfile


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


@admin.register(TeacherProfile)
class TeacherProfileAdmin(admin.ModelAdmin):
    list_display = ['full_name', 'user']
    search_fields = ['full_name', 'user__username']


@admin.register(Module)
class ModuleAdmin(admin.ModelAdmin):
    list_display = ['code', 'name', 'teacher', 'class_level', 'semester']
    list_filter = ['class_level', 'semester__academic_year']
    search_fields = ['name', 'code', 'teacher']


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = ['nactvet_reg_no', 'name', 'module', 'get_class_level']
    list_filter = ['module__class_level', 'module__semester']
    search_fields = ['nactvet_reg_no', 'name']

    @admin.display(description='Class Level', ordering='module__class_level__order')
    def get_class_level(self, obj):
        return obj.module.class_level.name


class AttendanceRecordInline(admin.TabularInline):
    model = AttendanceRecord
    extra = 0


@admin.register(Session)
class SessionAdmin(admin.ModelAdmin):
    list_display = ['module', 'session_type', 'date', 'label', 'topic', 'get_class_level']
    list_filter = ['session_type', 'module__class_level', 'module__semester', 'date']
    search_fields = ['topic', 'label']
    inlines = [AttendanceRecordInline]

    @admin.display(description='Class Level')
    def get_class_level(self, obj):
        return obj.module.class_level.name


@admin.register(AttendanceRecord)
class AttendanceRecordAdmin(admin.ModelAdmin):
    list_display = ['session', 'student', 'status']
    list_filter = ['status', 'session__module__class_level', 'session__module']
