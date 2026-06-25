from django.db import models


class Station(models.Model):
    """
    Monitoring stantsiyalari - mavjud `stations` jadvaliga mos
    """
    name = models.CharField(max_length=100, verbose_name="Nomi")
    code = models.CharField(max_length=20, unique=True, null=True, blank=True, verbose_name="Kodi")
    location = models.CharField(max_length=200, null=True, blank=True, verbose_name="Joylashuvi")
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True, verbose_name="Kenglik")
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True, verbose_name="Uzunlik")
    is_active = models.BooleanField(default=True, verbose_name="Faolmi")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Yaratilgan sana")

    class Meta:
        db_table = "stations"
        verbose_name = "Stantsiya"
        verbose_name_plural = "Stantsiyalar"
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.code})" if self.code else self.name


class Measurement(models.Model):
    """
    O'lchov ma'lumotlari - mavjud `measurements` jadvaliga mos
    """
    station = models.ForeignKey(
        Station,
        on_delete=models.PROTECT,
        related_name="measurements",
        verbose_name="Stantsiya",
        db_column="station_id"
    )
    measured_at = models.DateTimeField(verbose_name="O'lchov vaqti", db_index=True)
    value = models.FloatField(null=True, blank=True, verbose_name="Qiymat")
    is_valid = models.BooleanField(default=True, verbose_name="Yaroqli")

    class Meta:
        db_table = "measurements"
        verbose_name = "O'lchov"
        verbose_name_plural = "O'lchovlar"
        unique_together = [("station", "measured_at")]
        indexes = [
            models.Index(fields=["measured_at"], name="idx_measured_at"),
            models.Index(fields=["station", "measured_at"], name="idx_station_time"),
        ]

    def __str__(self):
        return f"{self.station.name} | {self.measured_at} = {self.value}"