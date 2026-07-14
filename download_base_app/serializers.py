from rest_framework import serializers


class MagnitkaSyncRequestSerializer(serializers.Serializer):
    """POST /upload/magnitka/ — magnitka sinxronizatsiyasi.

    Sanalar YYYY-MM-DD (frontend date input formati).
    """

    date_start = serializers.DateField()
    date_end = serializers.DateField()
    station_code = serializers.CharField(required=False, allow_null=True, allow_blank=True, default=None)
    chunk_days = serializers.IntegerField(required=False, default=7, min_value=1, max_value=365)

    def validate(self, attrs):
        if attrs["date_end"] < attrs["date_start"]:
            raise serializers.ValidationError(
                "Yakuniy sana boshlang'ich sanadan keyin bo'lishi kerak."
            )
        return attrs


class ApiUploadRequestSerializer(serializers.Serializer):
    """POST /upload/api/ va /upload/transfer/ — sanalar DD.MM.YYYY formatida
    (eski sahifa va tashqi API shu formatni kutadi)."""

    station = serializers.CharField()
    well = serializers.CharField()
    start_date = serializers.CharField()
    end_date = serializers.CharField()

    def _check_ddmmyyyy(self, value, field):
        import re
        if not re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", value):
            raise serializers.ValidationError(
                {field: "Sana DD.MM.YYYY formatida bo'lishi kerak."}
            )

    def validate(self, attrs):
        self._check_ddmmyyyy(attrs["start_date"], "start_date")
        self._check_ddmmyyyy(attrs["end_date"], "end_date")
        return attrs


class SpmFolderRequestSerializer(serializers.Serializer):
    """POST /upload/spm/folder/ — serverdagi papkadan o'qish."""

    folder_path = serializers.CharField()
    # Desktop skript fayllarni o'qigach O'CHIRIB yuborardi; serverda bu
    # xavfli bo'lgani uchun standart holatda o'chirilmaydi
    delete_after = serializers.BooleanField(required=False, default=False)
