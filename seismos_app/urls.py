from django.urls import path
from .import views

app_name = "seismos"

urlpatterns = [
    path('',views.selection_view,name='selection'),
    path('parametrs/',views.parametrs_view,name='parametrs'),
    path('results/',views.results_view,name='results'),


# # Informativlik tahlili uchun yangi URLlar
#     path('informativity/', views.informativity_view, name='informativity'),
#     path('informativity/export/', views.export_informativity_excel, name='informativity_export'),
]