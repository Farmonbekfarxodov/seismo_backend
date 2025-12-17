from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver
from .models import LoginHistory


def get_client_ip(request):
    """Request dan real IP manzilni olish"""
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0]
    else:
        ip = request.META.get('REMOTE_ADDR')
    return ip


@receiver(user_logged_in)
def log_user_login(sender, request, user, **kwargs):
    """
    Foydalanuvchi login qilganda avtomatik ravishda tarixga yozish
    """
    try:
        ip_address = get_client_ip(request)
        user_agent = request.META.get('HTTP_USER_AGENT', '')[:255]

        LoginHistory.objects.create(
            user=user,
            ip_address=ip_address,
            user_agent=user_agent,
            success=True
        )
    except Exception as e:
        # Xatolik bo'lsa ham login jarayonini to'xtatmaslik
        print(f"Login tarixini saqlashda xatolik: {e}")