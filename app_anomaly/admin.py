from django.contrib import admin
from django.utils.html import format_html
from .models import AnomalyRecord


@admin.register(AnomalyRecord)
class AnomalyRecordAdmin(admin.ModelAdmin):
    """
    Django Admin panel uchun AnomalyRecord admin interface
    """

    # Ro'yxat ko'rinishi
    list_display = (
        'skvajina',
        'parameter',
        'time_period_display',
        'anomaly_duration_display',
        'magnitude_display',
        'detected_anomalies_count',
        'is_active',  # 👈 REAL FIELD
        'is_active_display',  # 👈 CHIROYLI KO‘RINISH
        'created_at_display'
    )

    # Filtrlar
    list_filter = (
        'is_active',
        'time_period_months',
        'anomaly_duration_days',
        'created_at',
    )

    # Qidiruv
    search_fields = (
        'skvajina',
        'parameter',
    )

    # Tartiblash
    ordering = ('-created_at',)

    # Ro'yxatda tahrir
    list_editable = (
        'is_active',
    )

    # Read-only fields
    readonly_fields = (
        'created_at',
        'updated_at',
        'anomaly_details_display',
    )

    # Fieldset'lar - admin formada tuzilgan ko'rinish
    fieldsets = (
        ('Asosiy Ma\'lumotlar', {
            'fields': ('skvajina', 'parameter', 'session_id')
        }),
        ('Tahlil Parametrlari', {
            'fields': ('time_period_months', 'anomaly_duration_days', 'magnitude')
        }),
        ('Natijalar', {
            'fields': (
                'detected_anomalies_count',
                'anomaly_start_date',
                'anomaly_end_date',
                'anomaly_details_display'
            ),
            'classes': ('collapse',)
        }),
        ('Status', {
            'fields': ('is_active',)
        }),
        ('Vaqt', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    # Qo'shimcha aksiyalar
    actions = [
        'activate_records',
        'deactivate_records',
        'export_as_csv'
    ]

    # Pagination
    list_per_page = 50

    # Display methods

    def time_period_display(self, obj):
        """Vaqt oralig'i ko'rsatish"""
        return obj.get_time_period_months_display()

    time_period_display.short_description = 'Vaqt Oralig\'i'

    def anomaly_duration_display(self, obj):
        """Anomaliya davomiyligi ko'rsatish"""
        return obj.get_anomaly_duration_days_display()

    anomaly_duration_display.short_description = 'Anomaliya Davomiyligi'

    def magnitude_display(self, obj):
        """Magnitude ko'rsatish (optional)"""
        if obj.magnitude is None:
            return format_html('<span style="color: gray;">Kiritilmagan</span>')
        return f"{obj.magnitude}"

    magnitude_display.short_description = 'Magnitude'

    def is_active_display(self, obj):
        """Faol/Nofaol status ko'rsatish"""
        if obj.is_active:
            return format_html(
                '<span style="color: green; font-weight: bold;">✓ Faol</span>'
            )
        else:
            return format_html(
                '<span style="color: red; font-weight: bold;">✗ Nofaol</span>'
            )

    is_active_display.short_description = 'Status'

    def created_at_display(self, obj):
        """Yaratilgan sana va vaqt"""
        return obj.created_at.strftime('%d.%m.%Y %H:%M')

    created_at_display.short_description = 'Yaratilgan'

    def anomaly_details_display(self, obj):
        """Anomaliya detallariga ko'rinish"""
        if obj.detected_anomalies_count == 0:
            return format_html('<span style="color: gray;">Anomaliya topilmadi</span>')

        details = f"""
        <div style="background-color: #f0f0f0; padding: 10px; border-radius: 5px;">
            <p><strong>Aniqlangan Anomaliyalar:</strong> {obj.detected_anomalies_count}</p>
        """

        if obj.anomaly_start_date and obj.anomaly_end_date:
            details += f"""
            <p><strong>Anomaliya Oralig'i:</strong><br/>
            {obj.anomaly_start_date.strftime('%d.%m.%Y')} - {obj.anomaly_end_date.strftime('%d.%m.%Y')}</p>
            """

        details += "</div>"
        return format_html(details)

    anomaly_details_display.short_description = 'Anomaliya Detalları'

    # Custom actions

    def activate_records(self, request, queryset):
        """Qayd'larni faol qilish"""
        count = queryset.update(is_active=True)
        self.message_user(request, f'{count} ta qayd faol qilindi.')

    activate_records.short_description = 'Tanlangan qayd\'larni faol qilish'

    def deactivate_records(self, request, queryset):
        """Qayd'larni nofaol qilish"""
        count = queryset.update(is_active=False)
        self.message_user(request, f'{count} ta qayd nofaol qilindi.')

    deactivate_records.short_description = 'Tanlangan qayd\'larni nofaol qilish'

    def export_as_csv(self, request, queryset):
        """CSV sifatida eksport qilish"""
        import csv
        from django.http import HttpResponse

        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="anomalies.csv"'

        writer = csv.writer(response)
        writer.writerow([
            'Skvajina',
            'Parametr',
            'Vaqt Oralig\'i (oy)',
            'Anomaliya Davomiyligi (kun)',
            'Magnitude',
            'Aniqlangan Anomaliyalar',
            'Yaratilgan',
        ])

        for record in queryset:
            writer.writerow([
                record.skvajina,
                record.parameter,
                record.get_time_period_months_display(),
                record.get_anomaly_duration_days_display(),
                record.magnitude or '-',
                record.detected_anomalies_count,
                record.created_at.strftime('%d.%m.%Y %H:%M'),
            ])

        return response

    export_as_csv.short_description = 'CSV sifatida eksport qilish'

    # Admin panel title
    def changelist_view(self, request, extra_context=None):
        """Changelist sahifasini customiz qilish"""
        extra_context = extra_context or {}
        extra_context['title'] = 'Anomaliya Qayd\'lari'
        return super().changelist_view(request, extra_context=extra_context)