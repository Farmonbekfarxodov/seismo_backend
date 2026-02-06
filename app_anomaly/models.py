from django.db import models
from django.utils import timezone


class AnomalyRecord(models.Model):
    """
    Anomaliya kayitleri - foydalanuvchi anomaliya kiritgan har safar saqlash
    """

    TIME_PERIOD_CHOICES = [
        (1, '1 oy'),
        (3, '3 oy'),
        (6, '6 oy'),
        (12, '12 oy'),
        (24, '24 oy'),
    ]

    ANOMALY_DURATION_CHOICES = [
        (3, '3 kun'),
        (5, '5 kun'),
        (7, '7 kun'),
        (10, '10 kun'),
        (14, '14 kun'),
        (30, '30 kun'),
        (60, '60 kun'),
    ]

    # Asosiy maydonlar
    skvajina = models.CharField(
        max_length=255,
        db_index=True,
        verbose_name='Skvajina nomi'
    )

    parameter = models.CharField(
        max_length=100,
        db_index=True,
        verbose_name='Parameter (CH4, He, O2, etc.)'
    )

    time_period_months = models.IntegerField(
        choices=TIME_PERIOD_CHOICES,
        verbose_name='Vaqt oralig\'i (oylar)'
    )

    anomaly_duration_days = models.IntegerField(
        choices=ANOMALY_DURATION_CHOICES,
        verbose_name='Anomaliya davomiyligi (kunlar)'
    )

    magnitude = models.FloatField(
        null=True,
        blank=True,
        verbose_name='Magnitude (optional)'
    )

    recent_days_filter = models.IntegerField(
        default=7,
        verbose_name="Oxirgi necha kunlik anomaliyalar filtrlanadi"
    )

    # Natijalar
    detected_anomalies_count = models.IntegerField(
        default=0,
        verbose_name='Aniqlangan anomaliyalar soni'
    )

    anomaly_start_date = models.DateField(
        null=True,
        blank=True,
        verbose_name='Anomaliya boshlangan sana'
    )

    anomaly_end_date = models.DateField(
        null=True,
        blank=True,
        verbose_name='Anomaliya tugagan sana'
    )

    # Metadata
    is_active = models.BooleanField(
        default=True,
        verbose_name='Faol'
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name='Yaratilgan sana'
    )

    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name='Yangilangan sana'
    )

    # Session / User info (optional - keyingi versiya uchun)
    session_id = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        verbose_name='Session ID'
    )

    class Meta:
        db_table = 'app_anomaly_records'
        verbose_name = 'Anomaliya Kayit'
        verbose_name_plural = 'Anomaliya Kayitlari'
        indexes = [
            models.Index(fields=['skvajina', 'parameter']),
            models.Index(fields=['created_at']),
            models.Index(fields=['is_active']),
        ]
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.skvajina} - {self.parameter} ({self.get_time_period_months_display()})"

    def get_analysis_label(self):
        """Tahlil qilish uchun label"""
        return f"{self.skvajina} | {self.parameter} | {self.get_time_period_months_display()} | {self.get_anomaly_duration_days_display()}"