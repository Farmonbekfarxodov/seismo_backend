from django.contrib.auth.models import AbstractUser
from django.db import models
from django.conf import settings
from django.core.exceptions import ValidationError
import re

class CustomUser(AbstractUser):
    """
    Bu model foydalanuvchining shaxsiy ma'lumotlarini o'z ichiga oladi.
    """
    

    username = models.CharField(
        max_length=150,
        unique=True,
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


# Mavjud CustomUser modelingizdan keyin qo'shing:

class LoginHistory(models.Model):
    """
    Foydalanuvchilarning login tarixini saqlash uchun model
    """
    user = models.ForeignKey(
        CustomUser,
        on_delete=models.CASCADE,
        related_name='login_history',
        verbose_name='Foydalanuvchi'
    )
    login_time = models.DateTimeField(
        auto_now_add=True,
        verbose_name='Login vaqti'
    )
    ip_address = models.GenericIPAddressField(
        null=True,
        blank=True,
        verbose_name='IP manzil'
    )
    user_agent = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        verbose_name='Brauzer ma\'lumoti'
    )
    success = models.BooleanField(
        default=True,
        verbose_name='Muvaffaqiyatli'
    )

    class Meta:
        verbose_name = 'Login tarixi'
        verbose_name_plural = 'Login tarixi'
        ordering = ['-login_time']
        indexes = [
            models.Index(fields=['-login_time']),
            models.Index(fields=['user', '-login_time']),
        ]

    def __str__(self):
        return f"{self.user.username} - {self.login_time.strftime('%Y-%m-%d %H:%M:%S')}"