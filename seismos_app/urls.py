from django.urls import path
from .import views

app_name = "seismos"

urlpatterns = [
    path('',views.selection_view,name='selection'),
    path('parametrs/',views.parametrs_view,name='parametrs'),
    path('results/',views.results_view,name='results'),
    path('set-reference/',views.set_reference_segment,name='set_reference_segment'),
]