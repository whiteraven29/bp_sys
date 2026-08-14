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
router.register('finance-students', views.FinanceStudentViewSet, basename='finance-student')
router.register('payment-categories', views.PaymentCategoryViewSet, basename='payment-category')
router.register('finance-obligations', views.StudentFinanceObligationViewSet, basename='finance-obligation')
router.register('student-payments', views.StudentPaymentViewSet, basename='student-payment')
router.register('finance-clearances', views.StudentFinanceClearanceViewSet, basename='finance-clearance')
router.register('inventory-locations', views.InventoryLocationViewSet, basename='inventory-location')
router.register('asset-categories', views.AssetCategoryViewSet, basename='asset-category')
router.register('inventory-item-types', views.InventoryItemTypeViewSet, basename='inventory-item-type')
router.register('assets', views.AssetViewSet, basename='asset')
router.register('asset-transfers', views.AssetTransferViewSet, basename='asset-transfer')
router.register('asset-maintenance', views.AssetMaintenanceViewSet, basename='asset-maintenance')
router.register('inventory-inspections', views.InventoryInspectionViewSet, basename='inventory-inspection')
router.register('inventory-inspection-items', views.InventoryInspectionItemViewSet, basename='inventory-inspection-item')
router.register('asset-disposals', views.AssetDisposalViewSet, basename='asset-disposal')

urlpatterns = [
    path('', include(router.urls)),
    path('dashboard/', views.dashboard, name='dashboard'),
    path('report/', views.report, name='report'),
    path('all-modules/', views.all_modules, name='all-modules'),
    path('eligibility/', views.eligibility, name='eligibility'),
    path('sick-records/', views.sick_records, name='sick-records'),
    path('sick-records/<int:pk>/', views.update_sick_record, name='update-sick-record'),
    path('records/<int:pk>/status/', views.update_attendance_status, name='update-attendance-status'),
    path('change-password/', views.change_password, name='change-password'),
    path('staff-accounts/', views.create_staff_account, name='staff-accounts'),
    path('inventory/template/', views.inventory_template, name='inventory-template'),
    path('inventory/import/', views.inventory_import, name='inventory-import'),
    path('inventory/bulk-create/', views.inventory_bulk_create, name='inventory-bulk-create'),
    path('inventory/office-register/', views.inventory_office_register, name='inventory-office-register'),
    path('inventory/report/', views.inventory_report, name='inventory-report'),
    path('results/download/', views.download_results, name='results-download'),
    path('results/download/ca-signoff/', views.download_ca_signoff, name='results-download-ca-signoff'),
    path('results/download/final/', views.download_final_results, name='results-download-final'),
    path('eligibility/download/', views.download_eligibility_excel, name='eligibility-download'),
    path('eligibility/final/download/', views.download_final_eligibility_excel, name='final-eligibility-download'),
]
