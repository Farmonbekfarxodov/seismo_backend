from django.db import models


class Catalog(models.Model):
    Date = models.DateField(primary_key=True)
    Time = models.TimeField()
    Latitude = models.FloatField(max_length=255)
    Longitude = models.FloatField(max_length=255)
    Depth = models.FloatField(max_length=255)
    Mb = models.FloatField(max_length=255)
    Epicenter = models.CharField(max_length=255)

    class Meta:
        db_table = 'catalog'
        managed = False

    def __str__(self):
        return f"{self.Date}"