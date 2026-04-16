from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views

router = DefaultRouter()
router.register('academic-years', views.AcademicYearViewSet, basename='academicyear')
router.register('semesters', views.SemesterViewSet, basename='semester')
router.register('class-levels', views.ClassLevelViewSet, basename='classlevel')
router.register('modules', views.ModuleViewSet, basename='module')
router.register('students', views.StudentViewSet, basename='student')
router.register('sessions', views.SessionViewSet, basename='session')

urlpatterns = [
    path('', include(router.urls)),
    path('dashboard/', views.dashboard, name='dashboard'),
    path('report/', views.report, name='report'),
    path('all-modules/', views.all_modules, name='all-modules'),
]
