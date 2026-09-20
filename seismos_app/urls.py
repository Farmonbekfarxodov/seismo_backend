from django.urls import path
from . import api_views

app_name = "seismos"

urlpatterns = [
    # JSON API (React frontend uchun)
    path('api/options/', api_views.api_options, name='api_options'),
    path('api/series/', api_views.api_series, name='api_series'),
    path('api/layers/', api_views.api_layers, name='api_layers'),
    path('api/well-info/', api_views.api_well_info, name='api_well_info'),
    path('api/epoch-analysis/', api_views.api_epoch_analysis, name='api_epoch_analysis'),
]
