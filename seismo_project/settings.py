from datetime import timedelta
from email.policy import default
from pathlib import Path
import os
import json
from decouple import config, Csv

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = config('SECRET_KEY')

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = config('DEBUG', default=False, cast=bool)

ALLOWED_HOSTS = config('ALLOWED_HOSTS',default='localhost',cast=Csv())

# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    'rest_framework',
    'rest_framework_simplejwt',
    'corsheaders',

    #my_apps
    'seismos_app',
    'download_base_app',
    'app_users',
    'upload_catalog_app',
    'app_informativlik',
    'app_anomaly',
    'app_magnitka',
]

CORS_ALLOWED_ORIGINS = config('CORS_ALLOWED_ORIGINS', cast=Csv())

MIDDLEWARE = [
    # JSON javoblarni siqish (masalan, xarita qatlamlari 1.1MB -> ~0.2MB)
    'django.middleware.gzip.GZipMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

AUTH_USER_MODEL = 'app_users.CustomUser'

REST_FRAMEWORK = {

    'DEFAULT_AUTHENTICATION_CLASSES': (

        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ),
}

ROOT_URLCONF = 'seismo_project.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'seismo_project.wsgi.application'


DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.mysql',
        'NAME': config('DB_NAME'),
        'USER': config('DB_USER'),
        'PASSWORD': config('DB_PASSWORD'),
        'HOST': config('DB_HOST', default='localhost'),
        'PORT': config('DB_PORT', default='3306'),
        'OPTIONS': {
            'init_command': "SET sql_mode='STRICT_TRANS_TABLES', time_zone='+05:00'",
            'charset': 'utf8mb4',
        }
    }
}

LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,

    "formatters": {
        "verbose": {
            "format": "%(asctime)s %(levelname)s [%(name)s:%(lineno)d] %(message)s",
        },
    },

    "handlers": {
        "warnings_file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(LOG_DIR / "warnings.log"),
            "maxBytes": 20 * 1024 * 1024,  # 20MB
            "backupCount": 10,
            "formatter": "verbose",
            "level": "WARNING",  # ✅ faqat WARNING+ (WARNING, ERROR, CRITICAL)
        },
        # ✅ hech qanday console handler yo'q
    },

    # Root logger: faqat faylga, WARNING dan boshlab
    "root": {
        "handlers": ["warnings_file"],
        "level": "WARNING",
    },

    "loggers": {
        # Django server (request) loglari ham faqat faylga
        "django.server": {
            "handlers": ["warnings_file"],
            "level": "WARNING",
            "propagate": False,
        },
        # SQL loglari umuman chiqmasin (faqat ERROR bo'lsa ham root ushlaydi)
        "django.db.backends": {
            "handlers": ["warnings_file"],
            "level": "ERROR",
            "propagate": False,
        },
    },
}

CACHES = {
    'default': {
        # ✅ TO'G'RI: django_redis backend
        'BACKEND': 'django_redis.cache.RedisCache',
        'LOCATION': 'redis://127.0.0.1:6379/1',

        # ✅ OPTIONS to'g'ri strukturada
        'OPTIONS': {
            'CLIENT_CLASS': 'django_redis.client.DefaultClient',
            'CONNECTION_POOL_KWARGS': {
                'max_connections': 50,
                'retry_on_timeout': True,
            },
            'SOCKET_CONNECT_TIMEOUT': 5,
            'SOCKET_TIMEOUT': 5,
        },
        'KEY_PREFIX': 'seismo',
        'TIMEOUT': 3600,
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/5.2/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'Asia/Tashkent'

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/5.2/howto/static-files/



STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / "static"]   # developmentda ishlatish uchun
STATIC_ROOT = BASE_DIR / "staticfiles"     # collectstatic uchun

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media/'

# Default primary key field type
# https://docs.djangoproject.com/en/5.2/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'


SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(minutes=5),  # Tokenning amal qilish muddati
    'REFRESH_TOKEN_LIFETIME': timedelta(days=1),   # Yangilash tokenining muddati
    'ROTATE_REFRESH_TOKENS': False,
    'BLACKLIST_AFTER_ROTATION': False,
    'UPDATE_LAST_LOGIN': False,
}