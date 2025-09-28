# asosiy_loyiha/urls.py

from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.views.generic import TemplateView


urlpatterns = [
    path('admin/', admin.site.urls),
    path('seismos/', include('seismos_app.urls', namespace="seismos")),
    path('upload/', include('download_base_app.urls', namespace="download_base")),
    path('upload-catalog/',include('upload_catalog_app.urls',namespace = "catalog")),
    
    path('api/', include('app_users.urls')),
    
    path('', TemplateView.as_view(template_name='app_users/login.html'), name='login'),
    path('index/', TemplateView.as_view(template_name='index.html'), name='index'),
]


if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root = settings.STATIC_ROOT)
    urlpatterns += static(settings.MEDIA_URL,document_root = settings.MEDIA_ROOT)