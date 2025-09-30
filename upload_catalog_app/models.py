from django.db import models


class Catalog(models.Model):
    Event_date = models.DateField()
    Event_time = models.TimeField()
    Latitude = models.FloatField()
    Longitude = models.FloatField()
    Depth = models.FloatField()
    Mb = models.FloatField()
    Epicenter = models.CharField(max_length=255)

    class Meta:
        db_table = 'catalog'
        managed = False  # agar sizning jadvalingiz MySQL'da oldin yaratilgan bo'lsa

    def __str__(self):
        return f"{self.Event_date} {self.Event_time}"
