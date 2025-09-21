from django.contrib.auth.models import AbstractUser
from django.db import models
from django.core.exceptions import ValidationError
import re

class CustomUser(AbstractUser):
    """
    Bu model foydalanuvchining shaxsiy ma'lumotlarini o'z ichiga oladi.
    """
    
    # username uchun maxsus validator. Faqat harflar, sonlar va _,-,.,@,+, belgilari bo'lishi mumkin.
    def validate_username(value):
        if not re.match(r'^[a-zA-Z0-9_.-@+]+$', value):
            raise ValidationError(
                "Foydalanuvchi nomi faqat harflar, sonlar va _,-,.,@,+ belgilardan iborat bo'lishi kerak."
            )

    username = models.CharField(
        max_length=150,
        unique=True,
        validators=[validate_username],
        error_messages={
            'unique': "Bu foydalanuvchi nomi allaqachon mavjud."
        },
    )

    last_visit = models.DateTimeField(
        auto_now_add=True,
        verbose_name="Oxirgi tashrif vaqti"
    )

    # is_staff maydonini is_admin deb o'zgartirdik va o'zimizning 'admin' huquqimizni berdik
    is_admin = models.BooleanField(
        default=False,
        verbose_name="Admin huquqlari"
    )
    
    # Standart is_staff va is_superuser maydonlari saqlanib qoladi.
    # is_superuser = True bo'lsa, bu foydalanuvchi 'Superadmin' hisoblanadi.

    def __str__(self):
        return self.username