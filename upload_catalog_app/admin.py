from django.contrib import admin
from .models import Catalog


@admin.register(Catalog)
class CatalogAdmin(admin.ModelAdmin):
    list_display = ("Event_date", "Event_time", "Latitude", "Longitude", "Depth", "Mb", "Epicenter")
    list_filter = ("Event_date", "Epicenter")
    search_fields = ("Epicenter",)
    ordering = ("-Event_date", "-Event_time")
