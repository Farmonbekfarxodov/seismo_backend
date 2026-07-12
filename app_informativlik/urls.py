from django.urls import path
from .import views
from . import api_views

app_name = "informativlik"

urlpatterns = [

# Informativlik tahlili uchun yangi URLlar
    path('informativity/', views.informativity_view, name='informativity'),
    path('informativity/export/', views.export_informativity_excel, name='informativity_export'),

    # JSON API (React frontend uchun)
    path('api/options/', api_views.api_options, name='api_options'),
    path('api/analyze/', api_views.api_analyze, name='api_analyze'),
    path('api/export/', api_views.api_export, name='api_export'),
]

