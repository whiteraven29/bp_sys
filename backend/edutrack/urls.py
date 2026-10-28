from django.contrib import admin
from django.contrib.auth.decorators import login_required
from django.urls import path, include
from django.views.generic import TemplateView
from attendance import consent_views, views as att_views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/', include('attendance.urls')),
    path('login/', att_views.login_view, name='login'),
    path('logout/', att_views.logout_view, name='logout'),
    path('keep-alive/', att_views.keep_alive, name='keep-alive'),
    path('register/', att_views.register_view, name='register'),
    path('student-logout/', att_views.student_logout_view, name='student-logout'),
    path('student-dashboard/', att_views.student_dashboard, name='student-dashboard'),
    path('course-portal/', att_views.course_portal, name='course-portal'),
    path('my-semester-pass/', att_views.my_semester_pass, name='my-semester-pass'),
    path('semester-pass/<int:profile_id>/', att_views.semester_pass_print, name='semester-pass-print'),
    path('semester-pass/check/', att_views.semester_pass_check, name='semester-pass-check'),
    path('password/change/', att_views.password_change_page, name='password-change'),
    path('announcements/<int:pk>/download/', att_views.announcement_download, name='announcement-download'),
    path('invoice/<str:reference>/', att_views.invoice_print, name='invoice-print'),
    path('statement/<int:profile_id>/', att_views.statement_print, name='statement-print'),
    # An approved service request, as the college's own paper form.
    path('request/<int:pk>/', att_views.request_print, name='request-print'),
    # A supplementary student's consent form, to print, sign and bring back.
    path('supp-consent/<int:pk>/print/', consent_views.supp_consent_print, name='supp-consent-print'),
    path('', login_required(TemplateView.as_view(template_name='index.html')), name='frontend'),
]
