from django.db import models


class Catalog(models.Model):
    Event_date = models.DateField(db_index=True)
    Event_time = models.TimeField()
    Latitude = models.FloatField()
    Longitude = models.FloatField()
    Depth = models.FloatField()
    Mb = models.FloatField(db_index=True)
    Epicenter = models.CharField(max_length=255)

    class Meta:
        db_table = 'catalog'

        indexes = [
            models.Index(fields=['Event_date', 'Mb'], name='idx_date_magnitude'),

            models.Index(fields=['Latitude','Longitude'], name='idx_coordinates'),

            models.Index(fields=['-Event_date','-Event_time'], name='idx_datetime_desc'),
        ]
        ordering = ['-Event_date','-Event_time']
        managed = False  # agar sizning jadvalingiz MySQL'da oldin yaratilgan bo'lsa

    def __str__(self):
        return f"{self.Event_date} {self.Event_time}"
