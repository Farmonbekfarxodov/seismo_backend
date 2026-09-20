from django.urls import path
from . import views

app_name = "magnitka"

urlpatterns = [
    # JSON API (React frontend uchun)
    path("api/stations/",     views.api_stations,     name="api_stations"),
    path("api/measurements/", views.api_measurements, name="api_measurements"),
    path("api/earthquakes/",  views.api_earthquakes,  name="api_earthquakes"),
]
