from rest_framework import serializers


class SeriesRequestSerializer(serializers.Serializer):
    """POST /seismos/api/series/ so'rov tanasi validatsiyasi.

    Eski qo'lda parse qilingan mantiq bilan bir xil defaultlar.
    """

    selected_keys = serializers.ListField(
        child=serializers.CharField(), min_length=1,
        error_messages={"min_length": "Kamida bitta quduq tanlang.",
                        "required": "Kamida bitta quduq tanlang."},
    )
    selected_params = serializers.ListField(
        child=serializers.CharField(), required=False, allow_empty=True, default=list,
    )
    min_mag = serializers.FloatField(required=False, default=4.0)
    min_mlgr = serializers.FloatField(required=False, default=2.5)
    sigma = serializers.FloatField(required=False, default=1.0, min_value=0.01)
    segment_years = serializers.IntegerField(
        required=False, allow_null=True, default=None, min_value=1, max_value=50,
    )
    filter_mode = serializers.ChoiceField(
        choices=["mlgr", "mb"], required=False, default="mlgr",
    )
    median_window = serializers.IntegerField(
        required=False, allow_null=True, default=None, min_value=1,
    )
    start_date = serializers.DateField(required=False, allow_null=True, default=None)
    end_date = serializers.DateField(required=False, allow_null=True, default=None)

    def validate(self, attrs):
        s, e = attrs.get("start_date"), attrs.get("end_date")
        if s and e and e < s:
            raise serializers.ValidationError(
                "Tugash sanasi boshlanish sanasidan keyin bo'lishi kerak."
            )
        return attrs

class EpochRequestSerializer(serializers.Serializer):
    """POST /seismos/api/epoch-analysis/ — zilzila atrofidagi oyna tahlili.

    Sana oralig'i (start_date/end_date) ZILZILALARNI tanlash uchun ishlatiladi;
    har bir grafikning o'z oynasi esa days_before/days_after bilan belgilanadi
    va kerak bo'lsa shu oraliqdan chetga chiqishi mumkin.
    """
    selected_keys = serializers.ListField(
        child=serializers.CharField(), allow_empty=False,
        error_messages={"empty": "Kamida bitta skvajina tanlang."},
    )
    selected_params = serializers.ListField(
        child=serializers.CharField(), required=False, default=list,
    )
    min_mag = serializers.FloatField(required=False, default=4.0)
    min_mlgr = serializers.FloatField(required=False, default=2.5)
    days_before = serializers.IntegerField(required=False, default=30, min_value=1, max_value=365)
    days_after = serializers.IntegerField(required=False, default=10, min_value=1, max_value=365)
    start_date = serializers.DateField(required=False, allow_null=True)
    end_date = serializers.DateField(required=False, allow_null=True)

    def validate(self, attrs):
        s, e = attrs.get("start_date"), attrs.get("end_date")
        if s and e and e < s:
            raise serializers.ValidationError(
                "Tugash sanasi boshlanish sanasidan oldin bo'lishi mumkin emas."
            )
        return attrs
