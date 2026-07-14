from django.urls import path
from . import views

app_name = "download_base"

urlpatterns = [
    path("upload/", views.index, name="index"),

    # 1) API dan yuklash (POST)
    path("upload/api/", views.upload_api, name="upload_api"),

    # 2) Excel yuklash (POST)
    path("upload/excel/", views.upload_excel, name="upload_excel"),

    path('upload/transfer/', views.transfer_to_new_db),  # ← yangi

    path('upload/magnitka/', views.upload_measurements, name='upload_magnitka'),

    path('upload/get-stations/', views.get_magnitka_stations, name='get_stations'),

    # React frontend uchun: STATIONS_AND_WELLS ro'yxati
    path('upload/stations-wells/', views.get_stations_and_wells, name='stations_wells'),

    # SPM fayldan geoseysmoga yuklash (5-bo'lim)
    path('upload/spm/files/', views.spm_upload_files, name='spm_files'),
    path('upload/spm/folder/', views.spm_upload_folder, name='spm_folder'),
]