from rest_framework import serializers
from .models import Station


class StationSerializer(serializers.ModelSerializer):
    """api_stations javobidagi stansiya (eski qo'lda qurilgan dict bilan bir xil)."""

    lat = serializers.SerializerMethodField()
    lon = serializers.SerializerMethodField()

    class Meta:
        model = Station
        fields = ["id", "name", "code", "location", "lat", "lon"]

    def get_lat(self, obj):
        return float(obj.latitude) if obj.latitude else None

    def get_lon(self, obj):
        return float(obj.longitude) if obj.longitude else None


class MeasurementsQuerySerializer(serializers.Serializer):
    """GET /magnitka/api/measurements/ query parametrlari."""

    station_ids = serializers.CharField()
    days = serializers.IntegerField(required=False, default=30, min_value=1, max_value=3650)

    def validate_station_ids(self, value):
        try:
            ids = [int(x) for x in value.split(",") if x.strip()]
        except ValueError:
            raise serializers.ValidationError("station_ids vergul bilan ajratilgan sonlar bo'lishi kerak.")
        if not ids:
            raise serializers.ValidationError("Kamida bitta stansiya ID kerak.")
        return ids


class EarthquakesQuerySerializer(serializers.Serializer):
    """GET /magnitka/api/earthquakes/ query parametrlari."""

    min_magnitude = serializers.FloatField(required=False, allow_null=True, default=None)
