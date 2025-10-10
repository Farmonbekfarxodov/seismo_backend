from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import CustomUser


@admin.register(CustomUser)
class CustomUserAdmin(BaseUserAdmin):
    """
    Django admin panelida CustomUser modelini boshqarish uchun
    maxsus admin sinfi.
    """

    # Ro‘yxatda ko‘rinadigan ustunlar
    list_display = (
        'username',
        'email',
        'first_name',
        'last_name',
        'is_active',
        'is_admin',
        'is_superuser',
    )

    # Filtrlash uchun maydonlar
    list_filter = ('is_active', 'is_admin', 'is_superuser')

    # Foydalanuvchini tahrirlash sahifasida ko‘rsatiladigan bo‘limlar
    fieldsets = (
        (None, {'fields': ('username', 'password')}),
        ('Shaxsiy ma\'lumotlar', {'fields': ('first_name', 'last_name', 'email')}),
        ('Ruxsatlar', {
            'fields': (
                'is_active',
                'is_admin',
                'is_superuser',
                'is_staff',  # Django admin tizimi uchun zarur
                'groups',
                'user_permissions',
            ),
        }),
        ('Muhim sanalar', {'fields': ('last_login', 'date_joined', 'last_visit')}),
    )

    # Faqat o‘qish uchun (readonly) bo‘lgan maydonlar
    readonly_fields = ('last_login', 'date_joined', 'last_visit')

    # Yangi foydalanuvchi qo‘shish sahifasi uchun maydonlar
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': (
                'username',
                'email',
                'first_name',
                'last_name',
                'password1',
                'password2',
                'is_active',
                'is_admin',
                'is_superuser',
                'is_staff',
            ),
        }),
    )

    search_fields = ('username', 'email')
    ordering = ('username',)
    filter_horizontal = ('groups', 'user_permissions',)
