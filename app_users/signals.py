# app_users/signals.py

from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver
from .models import CustomUser
from django.utils import timezone

@receiver(user_logged_in)
def update_last_visit(sender, request, user, **kwargs):
    """
    Foydalanuvchi login qilganda, uning last_visit maydonini yangilaydi.
    """
    if isinstance(user, CustomUser):
        user.last_visit = timezone.now()
        user.save()