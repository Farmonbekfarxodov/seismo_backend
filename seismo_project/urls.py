# asosiy_loyiha/urls.py

from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path('admin/', admin.site.urls),
    path('seismos/', include('seismos_app.urls', namespace="seismos")),
    path('', include('download_base_app.urls', namespace="download_base")),
    path('catalog-list/', include('upload_catalog_app.urls', namespace="catalog")),
    path('informativlik/', include('app_informativlik.urls', namespace="informativlik")),
    path('api/', include('app_users.urls')),
    path('anomaly/', include('app_anomaly.urls', namespace="app_anomaly")),
    path('magnitka/', include('app_magnitka.urls', namespace='magnitka')),
]

if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)