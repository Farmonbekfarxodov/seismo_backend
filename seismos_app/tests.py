"""
SeriesRequestSerializer validatsiyasi uchun unit testlar.

Ishga tushirish:
    python manage.py test seismos_app --settings=seismo_project.settings_test
"""

from django.test import SimpleTestCase

from .serializers import SeriesRequestSerializer


class SeriesRequestSerializerTests(SimpleTestCase):
    def test_minimal_valid_request_gets_defaults(self):
        s = SeriesRequestSerializer(data={"selected_keys": ["Stansiya | Skv-1"]})
        self.assertTrue(s.is_valid(), s.errors)
        v = s.validated_data
        self.assertEqual(v["min_mag"], 4.0)
        self.assertEqual(v["min_mlgr"], 2.5)
        self.assertEqual(v["sigma"], 1.0)
        self.assertEqual(v["filter_mode"], "mlgr")
        self.assertIsNone(v["segment_years"])
        self.assertIsNone(v["median_window"])

    def test_empty_keys_rejected(self):
        s = SeriesRequestSerializer(data={"selected_keys": []})
        self.assertFalse(s.is_valid())

    def test_missing_keys_rejected(self):
        s = SeriesRequestSerializer(data={})
        self.assertFalse(s.is_valid())

    def test_invalid_filter_mode_rejected(self):
        s = SeriesRequestSerializer(
            data={"selected_keys": ["A | B"], "filter_mode": "xyz"}
        )
        self.assertFalse(s.is_valid())

    def test_end_before_start_rejected(self):
        s = SeriesRequestSerializer(data={
            "selected_keys": ["A | B"],
            "start_date": "2025-01-01", "end_date": "2024-01-01",
        })
        self.assertFalse(s.is_valid())

    def test_non_numeric_sigma_rejected(self):
        s = SeriesRequestSerializer(
            data={"selected_keys": ["A | B"], "sigma": "abc"}
        )
        self.assertFalse(s.is_valid())

    def test_negative_segment_years_rejected(self):
        s = SeriesRequestSerializer(
            data={"selected_keys": ["A | B"], "segment_years": -2}
        )
        self.assertFalse(s.is_valid())
