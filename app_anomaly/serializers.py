from rest_framework import serializers
from .models import AnomalyRecord


class AnomalyAnalyzeRequestSerializer(serializers.Serializer):
    """POST /anomaly/api/analyze/ so'rov tanasi validatsiyasi."""

    wells = serializers.ListField(
        child=serializers.CharField(), min_length=1,
        error_messages={"min_length": "Kamida bitta quduq va bitta parametr tanlang.",
                        "required": "Kamida bitta quduq va bitta parametr tanlang."},
    )
    parameters = serializers.ListField(
        child=serializers.CharField(), min_length=1,
        error_messages={"min_length": "Kamida bitta quduq va bitta parametr tanlang.",
                        "required": "Kamida bitta quduq va bitta parametr tanlang."},
    )
    time_period = serializers.IntegerField(required=False, default=6, min_value=1)
    anomaly_duration = serializers.IntegerField(required=False, default=3, min_value=1)
    recent_days = serializers.IntegerField(required=False, default=7, min_value=1)
    sigma = serializers.FloatField(required=False, default=2.0, min_value=0.01)
    magnitude = serializers.FloatField(required=False, allow_null=True, default=None)


class AnomalyRecordSerializer(serializers.ModelSerializer):
    time_period_label = serializers.CharField(
        source="get_time_period_months_display", read_only=True
    )
    anomaly_duration_label = serializers.CharField(
        source="get_anomaly_duration_days_display", read_only=True
    )

    class Meta:
        model = AnomalyRecord
        fields = [
            "id", "skvajina", "parameter",
            "time_period_months", "time_period_label",
            "anomaly_duration_days", "anomaly_duration_label",
            "magnitude", "recent_days_filter",
            "detected_anomalies_count",
            "anomaly_start_date", "anomaly_end_date",
            "is_active", "created_at", "updated_at",
        ]
