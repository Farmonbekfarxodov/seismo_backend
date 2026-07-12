from rest_framework import serializers
from .models import Catalog


class CatalogSerializer(serializers.ModelSerializer):
    class Meta:
        model = Catalog
        fields = [
            "id", "Event_date", "Event_time",
            "Latitude", "Longitude", "Depth", "Mb", "Epicenter",
        ]


class ManualEntrySerializer(serializers.ModelSerializer):
    """Qo'lda kiritish uchun — qiymatlarni validatsiya qiladi."""

    class Meta:
        model = Catalog
        fields = [
            "id", "Event_date", "Event_time",
            "Latitude", "Longitude", "Depth", "Mb", "Epicenter",
        ]

    def validate_Latitude(self, value):
        if not (-90 <= value <= 90):
            raise serializers.ValidationError("Kenglik -90 dan 90 gacha bo'lishi kerak.")
        return value

    def validate_Longitude(self, value):
        if not (-180 <= value <= 180):
            raise serializers.ValidationError("Uzunlik -180 dan 180 gacha bo'lishi kerak.")
        return value

    def validate_Depth(self, value):
        if value < 0:
            raise serializers.ValidationError("Chuqurlik musbat son bo'lishi kerak.")
        return value

    def validate_Mb(self, value):
        if value < 0:
            raise serializers.ValidationError("Magnitud musbat son bo'lishi kerak.")
        return value

    def validate(self, attrs):
        exists = Catalog.objects.filter(
            Event_date=attrs["Event_date"],
            Event_time=attrs["Event_time"],
            Latitude=attrs["Latitude"],
            Longitude=attrs["Longitude"],
        ).exists()
        if exists:
            raise serializers.ValidationError(
                "Bu ma'lumot allaqachon bazada mavjud."
            )
        return attrs
