from django.contrib import admin
from .models import Skvajina, AllIzmereniya,Malumot
from django.utils.html import format_html

@admin.register(Skvajina)
class SkvajinaAdmin(admin.ModelAdmin):
    list_display = ("naim", "Latitude", "Longitude")
    search_fields = ("naim",)
    list_filter = ("naim",)
    ordering = ("naim",)


@admin.register(AllIzmereniya)
class AllIzmereniyaAdmin(admin.ModelAdmin):
    list_display = ("stansiya", "skvajina", "izmereniya", "ssid_id")
    search_fields = ("stansiya", "skvajina", "izmereniya", "ssid_id")
    list_filter = ("stansiya", "skvajina")
    ordering = ("stansiya", "skvajina")


@admin.register(Malumot)
class MalumotAdmin(admin.ModelAdmin):
    list_display = ('nomi', 'mineralizatsiya_preview')
    fields = ('nomi', 'mineralizatsiya')  # faqat 2 ta ustun

    readonly_fields = ('nomi',)  # nomi o‘zgartirilmaydi!

    def mineralizatsiya_preview(self, obj):
        if obj.mineralizatsiya:
            return format_html(
                '<img src="{}" width="100" style="border-radius:6px;">',
                obj.mineralizatsiya.url
            )
        return "Rasm yo‘q"

    mineralizatsiya_preview.short_description = "Rasm"


