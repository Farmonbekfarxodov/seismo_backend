from django.urls import path
from . import views
from . import api_views

app_name = "seismos"

urlpatterns = [
    path('',views.selection_view,name='selection'),
    path('parametrs/',views.parametrs_view,name='parametrs'),
    path('results/',views.results_view,name='results'),

    # JSON API (React frontend uchun)
    path('api/options/', api_views.api_options, name='api_options'),
    path('api/series/', api_views.api_series, name='api_series'),
    path('api/layers/', api_views.api_layers, name='api_layers'),
]