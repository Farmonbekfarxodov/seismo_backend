from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import CustomUser, LoginHistory


@admin.register(LoginHistory)
class LoginHistoryAdmin(admin.ModelAdmin):
    """
    Login tarixini ko'rish va boshqarish uchun admin sinfi
    """
    list_display = ('user', 'login_time', 'ip_address', 'success', 'formatted_user_agent')
    list_filter = ('success', 'login_time', 'user')
    search_fields = ('user__username', 'user__email', 'ip_address')
    readonly_fields = ('user', 'login_time', 'ip_address', 'user_agent', 'success')
    date_hierarchy = 'login_time'

    def formatted_user_agent(self, obj):
        """User agent qisqartirilgan ko'rinishda"""
        if obj.user_agent:
            return obj.user_agent[:50] + '...' if len(obj.user_agent) > 50 else obj.user_agent
        return '-'

    formatted_user_agent.short_description = 'Brauzer'

    def has_add_permission(self, request):
        """Qo'lda qo'shishni taqiqlash"""
        return False

    def has_change_permission(self, request, obj=None):
        """O'zgartirishni taqiqlash"""
        return False


@admin.register(CustomUser)
class CustomUserAdmin(BaseUserAdmin):
    """
    Django admin panelida CustomUser modelini boshqarish uchun
    maxsus admin sinfi.
    """

    # Ro'yxatda ko'rinadigan ustunlar - last_login qo'shildi
    list_display = (
        'username',
        'email',
        'first_name',
        'last_name',
        'is_active',
        'is_admin',
        'is_superuser',
        'last_login',  # Yangi qo'shildi
        'login_count',  # Login sonini ko'rsatish
    )

    # Filtrlash uchun maydonlar - last_login qo'shildi
    list_filter = ('is_active', 'is_admin', 'is_superuser', 'last_login', 'date_joined')

    # Foydalanuvchini tahrirlash sahifasida ko'rsatiladigan bo'limlar
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
        ('Login tarixi', {'fields': ('view_login_history',)}),  # Yangi bo'lim
    )

    # Faqat o'qish uchun (readonly) bo'lgan maydonlar
    readonly_fields = ('last_login', 'date_joined', 'last_visit', 'view_login_history')

    # Yangi foydalanuvchi qo'shish sahifasi uchun maydonlar
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
    ordering = ('-last_login',)  # Oxirgi login qilganlar birinchi
    filter_horizontal = ('groups', 'user_permissions',)

    def login_count(self, obj):
        """Foydalanuvchining jami login sonini ko'rsatish"""
        return obj.login_history.count()

    login_count.short_description = 'Login soni'

    def view_login_history(self, obj):
        """Login tarixini ko'rish uchun havolalar"""
        from django.utils.html import format_html
        from django.urls import reverse

        if obj.pk:
            # O'zgartirilgan qator: 'your_app_name' o'rniga 'app_users' ishlatildi
            url = reverse('admin:app_users_loginhistory_changelist') + f'?user__id__exact={obj.pk}'
            count = obj.login_history.count()
            return format_html(
                '<a href="{}" target="_blank">Login tarixini ko\'rish ({} marta)</a>',
                url, count
            )
        return '-'

    view_login_history.short_description = 'Login tarixi'