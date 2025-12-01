
from django.urls import path
from .views import upload_catalog,catalog_list,upload_from_file,manual_entry

app_name = "catalog"

urlpatterns = [
    path("", catalog_list, name="catalog_list"),
    path("upload-catalog/",upload_catalog, name="upload_catalog"),
    path("upload-file/",upload_from_file,name="upload_from_file"),
    path("manual-entry/",manual_entry,name="manual_entry")
]
