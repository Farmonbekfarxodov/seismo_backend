from rest_framework import generics
from rest_framework.permissions import IsAuthenticated, DjangoModelPermissions
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response


from .models import CustomUser
from .serializers import UserSerializer, UserCreateSerializer

class UserListCreateView(generics.ListCreateAPIView):
    queryset = CustomUser.objects.all()
    
    def get_serializer_class(self):
        if self.request.method == 'POST':
            return UserCreateSerializer
        return UserSerializer

    def get_permissions(self):
        if self.request.method == 'POST':
            # Yangi foydalanuvchi yaratish uchun ruxsatlar
            if self.request.user.is_superuser:
                # Superadmin hamma foydalanuvchini, shu jumladan adminlarni ham yarata oladi
                self.permission_classes = [IsAuthenticated]
            elif self.request.user.is_admin:
                # Adminlar faqat "oddiy" foydalanuvchilarni yarata oladi
                self.permission_classes = [IsAuthenticated]
            else:
                self.permission_classes = [IsAuthenticated] # Boshqa foydalanuvchilar uchun
        else:
            # Foydalanuvchilar ro'yxatini ko'rish uchun ruxsat
            self.permission_classes = [IsAuthenticated, DjangoModelPermissions]
        
        return [permission() for permission in self.permission_classes]

    def perform_create(self, serializer):
        user = self.request.user
        
        # Superadmin foydalanuvchi yaratishda 'is_admin' ni belgilashi mumkin
        if user.is_superuser:
            is_admin = self.request.data.get('is_admin', True)
            serializer.save(is_admin=is_admin)
        # Adminlar faqat oddiy foydalanuvchilar yarata oladi
        elif user.is_admin:
            serializer.save(is_admin=False)
        else:
            serializer.save()





@api_view(['GET'])
@permission_classes([IsAuthenticated])
def protected_data_view(request):
    """
    Faqa token bilan kirgan foydalanuvchilar kira oladigan endpoint.
    """
    return Response({
        "message": "Siz tizimga muvaffaqiyatli kirdingiz!",
        "username": request.user.username,
        "is_admin": request.user.is_admin,
        "is_superuser": request.user.is_superuser
    })