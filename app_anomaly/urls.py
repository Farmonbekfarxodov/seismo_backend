from django.urls import path
from . import views
from . import api_views

app_name = 'app_anomaly'

urlpatterns = [
    # Asosiy anomaliya tahlili sahifasi
    path(
        'analysis/',
        views.anomaly_analysis_view,
        name='analysis'
    ),

    # Anomaliya tarixini ko'rsatish
    path(
        'history/',
        views.anomaly_history_view,
        name='history'
    ),

    # JSON API (React frontend uchun)
    path('api/options/', api_views.api_options, name='api_options'),
    path('api/analyze/', api_views.api_analyze, name='api_analyze'),
]