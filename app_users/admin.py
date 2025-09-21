from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import CustomUser

class CustomUserAdmin(BaseUserAdmin):
    """
    Django admin panelida CustomUser modelini boshqarish uchun
    maxsus admin sinfi.
    """
    
    list_display = (
        'username', 
        'email', 
        'first_name', 
        'last_name', 
        'is_active', 
        'is_admin', 
        'is_superuser'
    )
    
    list_filter = ('is_active', 'is_admin', 'is_superuser')
    
    fieldsets = (
        (None, {'fields': ('username', 'password')}),
        ('Shaxsiy ma\'lumotlar', {'fields': ('first_name', 'last_name', 'email')}),
        ('Ruxsatlar', {
            'fields': (
                'is_active', 
                'is_admin', 
                'is_superuser', 
                'groups', 
                'user_permissions'
            ),
        }),
        ('Muhim sanalar', {'fields': ('last_login', 'date_joined', 'last_visit')}),
    )
    
    # Yangi foydalanuvchi yaratish formasidagi maydonlar
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('username', 'email', 'first_name', 'last_name', 'password'),
        }),
    )
    
    search_fields = ('username', 'email')
    ordering = ('username',)
    
# CustomUser modelini admin panelida ro'yxatdan o'tkazamiz
admin.site.register(CustomUser, CustomUserAdmin)