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


class Catalog(models.Model):
    """
    Zilzilalar katalogi - mavjud `catalog` jadvaliga mos
    (rasmda ko'rsatilgan ustunlar asosida)
    """
    event_date = models.DateField(verbose_name="Sana", db_column="Event_date")
    event_time = models.TimeField(verbose_name="Vaqt", db_column="Event_time", null=True, blank=True)
    latitude = models.FloatField(verbose_name="Kenglik", db_column="Latitude")
    longitude = models.FloatField(verbose_name="Uzunlik", db_column="Longitude")
    depth = models.FloatField(verbose_name="Chuqurlik (km)", db_column="Depth", null=True, blank=True)
    mb = models.FloatField(verbose_name="Magnituda (Mb)", db_column="Mb", null=True, blank=True)
    epicenter = models.CharField(max_length=150, verbose_name="Epitsentr", db_column="Epicenter", null=True, blank=True)

    class Meta:
        db_table = "catalog"
        verbose_name = "Zilzila"
        verbose_name_plural = "Zilzilalar katalogi"
        ordering = ["-event_date", "-event_time"]
        indexes = [
            models.Index(fields=["event_date"], name="idx_catalog_event_date"),
            models.Index(fields=["mb"], name="idx_catalog_mb"),
        ]

    def __str__(self):
        return f"{self.event_date} {self.event_time} | Mb={self.mb} | {self.epicenter}"

    @property
    def event_datetime_str(self):
        """Sana va vaqtni birlashtirib qaytaradi (frontend uchun qulay)."""
        if self.event_time:
            return f"{self.event_date}T{self.event_time}"
        return f"{self.event_date}T00:00:00"