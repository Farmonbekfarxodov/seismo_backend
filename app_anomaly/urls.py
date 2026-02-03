from django.urls import path
from . import views

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
]