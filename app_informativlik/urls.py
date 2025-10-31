from django.urls import path
from .import views

app_name = "informativlik"

urlpatterns = [

# Informativlik tahlili uchun yangi URLlar
    path('informativity/', views.informativity_view, name='informativity'),
    path('informativity/export/', views.export_informativity_excel, name='informativity_export'),
]

