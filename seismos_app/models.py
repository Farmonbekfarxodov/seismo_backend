from django.db import models



class Skvajina(models.Model):
    naim = models.CharField(max_length=100)
    Latitude = models.FloatField()
    Longitude = models.FloatField()


    class Meta:
        db_table = 'skvajina'

class AllIzmereniya(models.Model):
    stansiya = models.CharField(max_length=100)
    skvajina = models.CharField(max_length=100)
    izmereniya = models.CharField(max_length=100)
    ssid_id = models.CharField(max_length=100)

    class Meta:
        db_table = 'all_izmereniya'


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