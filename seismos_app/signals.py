import logging
from django.core.signals import request_finished
from django.dispatch import receiver

logger = logging.getLogger(__name__)


@receiver(request_finished)
def cleanup_on_shutdown(sender, **kwargs):
    """
    ✅ Server to'xtaganda tozalash
    Lekin bu har bir request'dan keyin chaqiriladi, shuning uchun
    faqat process to'xtaganda kerak
    """
    pass  # Hozircha bo'sh


