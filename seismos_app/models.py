from django.db import models



class Skvajina(models.Model):
    naim = models.CharField(max_length=100, db_index=True)
    Latitude = models.FloatField()
    Longitude = models.FloatField()


    class Meta:
        db_table = 'skvajina'
        indexes = [
            models.Index(fields=['naim'], name='idx_skvajina_name'),
            models.Index(fields=['Latitude','Longitude'], name='idx_skvajina_coords'),
        ]
    def __str__(self):
        return self.naim


class AllIzmereniya(models.Model):
    stansiya = models.CharField(max_length=100, db_index=True)
    skvajina = models.CharField(max_length=100, db_index=True)
    izmereniya = models.CharField(max_length=100)
    ssid_id = models.CharField(max_length=100, db_index=True)

    class Meta:
        db_table = 'all_izmereniya'
        indexes = [
            models.Index(fields=['stansiya', 'skvajina'], name='idx_station_well'),
            models.Index(fields=['ssid_id'], name= 'idx_ssid'),
        ]

    def __str__(self):
        return  f"{self.stansiya}-{self.skvajina}"


class Malumot(models.Model):
    nomi = models.CharField(max_length=100)


    mineralizatsiya = models.ImageField(
        upload_to='mineralizatsiya/',
        blank=True,
        null=True
    )

    class Meta:
        db_table = 'malumot1'

    def __str__(self):
        return self.nomi