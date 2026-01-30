from django.apps import AppConfig


class SeismosAppConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'seismos_app'

    def ready(self):
        """
        ✅ App tayyor bo'lganda chaqiriladi
        Bu yerda signal'lar va startup kodlarni ishga tushirish mumkin
        """
        # Import'larni shu yerda qilish kerak
        pass  # Hozircha hech narsa qilmaslik kerak