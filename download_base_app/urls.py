from django.urls import path
from . import views

app_name = "download_base"

urlpatterns = [
    path("upload/", views.index, name="index"),

    # 1) API dan yuklash (POST)
    path("upload/api/", views.upload_api, name="upload_api"),

    # 2) Excel yuklash (POST)
    path("upload/excel/", views.upload_excel, name="upload_excel"),
]