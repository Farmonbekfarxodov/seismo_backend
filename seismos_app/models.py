from django.db import models



class Skvajina(models.Model):
    naim = models.CharField(max_length=100, db_index=True)
    Latitude = models.FloatField()
    Longitude = models.FloatField()


    class Meta:
        db_table = 'skvajina'
        managed = False
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
    ssdi_id = models.CharField(max_length=100, db_index=True)

    class Meta:
        db_table = 'all_izmereniya'
        managed = False
        indexes = [
            models.Index(fields=['stansiya', 'skvajina'], name='idx_station_well'),
            models.Index(fields=['ssdi_id'], name= 'idx_ssid'),
        ]

    def __str__(self):
        return  f"{self.stansiya}-{self.skvajina}"


class Malumot(models.Model):
    nomi = models.CharField(max_length=100, db_index=True)
    quduq_turi = models.CharField(max_length=100, blank=True, null=True)
    suv_qatlami = models.CharField(max_length=100, blank=True, null=True)
    chuqurlik = models.IntegerField(blank=True, null=True)
    seysmotektonik_holat = models.CharField(max_length=100, blank=True, null=True)
    strategrafik_taqsimoti = models.CharField(max_length=100, blank=True, null=True)
    litologik_tarkibi = models.CharField(max_length=100, blank=True, null=True)
    mineralizatsiya = models.BinaryField(blank=True,null=True)

    class Meta:
        db_table = 'malumot1'
        managed = False
        indexes = [
            models.Index(fields=['nomi'], name='idx_malumot_nomi'),
        ]

    def __str__(self):
        return self.nomi