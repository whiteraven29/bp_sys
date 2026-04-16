from django.contrib import admin
from django.contrib.auth.decorators import login_required
from django.urls import path, include
from django.views.generic import TemplateView
from attendance import views as att_views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/', include('attendance.urls')),
    path('login/', att_views.login_view, name='login'),
    path('logout/', att_views.logout_view, name='logout'),
    path('register/', att_views.register_view, name='register'),
    path('', login_required(TemplateView.as_view(template_name='index.html')), name='frontend'),
]
