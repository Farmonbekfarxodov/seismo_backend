"""
Upload endpointlari serializer validatsiyasi testlari.

Ishga tushirish:
    python manage.py test download_base_app --settings=seismo_project.settings_test
"""

from django.test import SimpleTestCase

from .serializers import MagnitkaSyncRequestSerializer, ApiUploadRequestSerializer


class MagnitkaSyncSerializerTests(SimpleTestCase):
    def test_valid_request(self):
        s = MagnitkaSyncRequestSerializer(data={
            "date_start": "2026-01-01", "date_end": "2026-02-01",
        })
        self.assertTrue(s.is_valid(), s.errors)
        self.assertEqual(s.validated_data["chunk_days"], 7)
        self.assertIsNone(s.validated_data["station_code"])

    def test_end_before_start_rejected(self):
        s = MagnitkaSyncRequestSerializer(data={
            "date_start": "2026-02-01", "date_end": "2026-01-01",
        })
        self.assertFalse(s.is_valid())

    def test_missing_dates_rejected(self):
        s = MagnitkaSyncRequestSerializer(data={"date_start": "2026-01-01"})
        self.assertFalse(s.is_valid())


class ApiUploadSerializerTests(SimpleTestCase):
    def test_valid_ddmmyyyy(self):
        s = ApiUploadRequestSerializer(data={
            "station": "all", "well": "all_wells",
            "start_date": "01.01.2026", "end_date": "01.02.2026",
        })
        self.assertTrue(s.is_valid(), s.errors)

    def test_iso_format_rejected(self):
        # Bu endpoint DD.MM.YYYY kutadi — ISO format qabul qilinmasligi kerak
        s = ApiUploadRequestSerializer(data={
            "station": "all", "well": "all_wells",
            "start_date": "2026-01-01", "end_date": "2026-02-01",
        })
        self.assertFalse(s.is_valid())

    def test_missing_fields_rejected(self):
        s = ApiUploadRequestSerializer(data={"station": "all"})
        self.assertFalse(s.is_valid())
