from django.urls import path
from . import api_views

app_name = "informativlik"

urlpatterns = [
    # JSON API (React frontend uchun)
    path('api/options/', api_views.api_options, name='api_options'),
    path('api/analyze/', api_views.api_analyze, name='api_analyze'),
    path('api/export/', api_views.api_export, name='api_export'),
]
