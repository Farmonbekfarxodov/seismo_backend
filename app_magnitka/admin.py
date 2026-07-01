from django.contrib import admin
from .models import Station, Measurement, Catalog


@admin.register(Station)
class StationAdmin(admin.ModelAdmin):
    list_display  = ("name", "code", "location", "is_active", "created_at")
    list_filter   = ("is_active",)
    search_fields = ("name", "code", "location")
    list_editable = ("is_active",)


@admin.register(Measurement)
class MeasurementAdmin(admin.ModelAdmin):
    list_display   = ("station", "measured_at", "value")
    list_filter    = ("station",)
    search_fields  = ("station__name",)
    date_hierarchy = "measured_at"
    ordering       = ("-measured_at",)
    raw_id_fields  = ("station",)


@admin.register(Catalog)
class CatalogAdmin(admin.ModelAdmin):
    list_display   = ("event_date", "event_time", "mb", "depth", "epicenter", "latitude", "longitude")
    list_filter    = ("epicenter",)
    search_fields  = ("epicenter",)
    date_hierarchy = "event_date"
    ordering       = ("-event_date", "-event_time")