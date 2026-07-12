"""
Test uchun sozlamalar: MySQL va Redis'siz ishlaydi.

Ishga tushirish:
    python manage.py test --settings=seismo_project.settings_test
"""

from .settings import *  # noqa: F401,F403

# Testlarda haqiqiy bazaga tegmaslik uchun — vaqtinchalik SQLite
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

# Redis o'rniga xotiradagi kesh
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    }
}

# Testlarda parol xeshlashni tezlashtirish
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
