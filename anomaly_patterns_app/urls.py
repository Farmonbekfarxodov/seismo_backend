from django.urls import path
from . import views

app_name = "anomaly_patterns"

urlpatterns = [
    path('set-reference/',views.set_reference_segment,name='set_reference_segment'),
]