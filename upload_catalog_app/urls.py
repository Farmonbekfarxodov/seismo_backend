
from django.urls import path
from .views import upload_catalog

app_name = "catalog"

urlpatterns = [
    path("", upload_catalog, name="upload_catalog"),
]
