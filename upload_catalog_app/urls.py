
from django.urls import path
from .views import upload_catalog,catalog_list

app_name = "catalog"

urlpatterns = [
    path("", catalog_list, name="catalog_list"),
    path("upload-catalog/",upload_catalog, name="upload_catalog"),
]
