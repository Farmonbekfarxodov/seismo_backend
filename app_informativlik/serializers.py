from rest_framework import serializers


class InformativityRequestSerializer(serializers.Serializer):
    """POST /informativlik/api/analyze/ so'rov tanasi (eski defaultlar bilan)."""

    wells = serializers.ListField(
        child=serializers.CharField(), min_length=1,
        error_messages={"min_length": "Kamida bitta skvajina tanlang.",
                        "required": "Kamida bitta skvajina tanlang."},
    )
    params = serializers.ListField(
        child=serializers.CharField(), min_length=1,
        error_messages={"min_length": "Kamida bitta parametr tanlang.",
                        "required": "Kamida bitta parametr tanlang."},
    )
    window_years = serializers.IntegerField(required=False, default=5, min_value=1, max_value=50)
    anomaly_duration = serializers.IntegerField(required=False, default=5, min_value=1)
    std_factor = serializers.FloatField(required=False, default=2.0, min_value=0.01)
    timedelta_before = serializers.IntegerField(required=False, default=30, min_value=0)
    timedelta_after = serializers.IntegerField(required=False, default=10, min_value=0)
    min_mag = serializers.FloatField(required=False, default=4.0)
    min_mlgr = serializers.FloatField(required=False, default=2.5)
    median_window = serializers.IntegerField(required=False, allow_null=True, default=None, min_value=1)
    start_date = serializers.DateField(required=False, allow_null=True, default=None)
    end_date = serializers.DateField(required=False, allow_null=True, default=None)

    def validate(self, attrs):
        s, e = attrs.get("start_date"), attrs.get("end_date")
        if s and e and e < s:
            raise serializers.ValidationError(
                "Tugash sanasi boshlanish sanasidan keyin bo'lishi kerak."
            )
        return attrs


class ExportRequestSerializer(serializers.Serializer):
    """POST /informativlik/api/export/ — natijalar ro'yxati."""

    results = serializers.ListField(
        child=serializers.DictField(), min_length=1,
        error_messages={"min_length": "Natijalar yo'q. Avval tahlil o'tkazing.",
                        "required": "Natijalar yo'q. Avval tahlil o'tkazing."},
    )
