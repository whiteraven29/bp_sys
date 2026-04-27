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
router.register('results', views.ResultViewSet, basename='result')

urlpatterns = [
    path('', include(router.urls)),
    path('dashboard/', views.dashboard, name='dashboard'),
    path('report/', views.report, name='report'),
    path('all-modules/', views.all_modules, name='all-modules'),
    path('eligibility/', views.eligibility, name='eligibility'),
    path('sick-records/', views.sick_records, name='sick-records'),
    path('sick-records/<int:pk>/', views.update_sick_record, name='update-sick-record'),
    path('results/download/', views.download_results, name='results-download'),
    path('results/download/final/', views.download_final_results, name='results-download-final'),
    path('eligibility/download/', views.download_eligibility_excel, name='eligibility-download'),
]
