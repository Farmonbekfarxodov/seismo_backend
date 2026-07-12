"""
Katalog serializer validatsiyasi uchun unit testlar.

Ishga tushirish:
    python manage.py test upload_catalog_app --settings=seismo_project.settings_test

Eslatma: Catalog modeli `managed = False` (jadval MySQL'da qo'lda yaratilgan),
shuning uchun testlarda bazaga yozmaymiz — faqat maydon validatorlarini
tekshiramiz (ular sof funksiyalar).
"""

from django.test import SimpleTestCase
from rest_framework import serializers

from .serializers import ManualEntrySerializer


class ManualEntryValidatorTests(SimpleTestCase):
    def setUp(self):
        self.s = ManualEntrySerializer()

    # --- Latitude ---
    def test_valid_latitude_passes(self):
        self.assertEqual(self.s.validate_Latitude(41.3), 41.3)

    def test_latitude_above_90_rejected(self):
        with self.assertRaises(serializers.ValidationError):
            self.s.validate_Latitude(91)

    def test_latitude_below_minus_90_rejected(self):
        with self.assertRaises(serializers.ValidationError):
            self.s.validate_Latitude(-90.5)

    def test_latitude_boundaries_allowed(self):
        self.assertEqual(self.s.validate_Latitude(90), 90)
        self.assertEqual(self.s.validate_Latitude(-90), -90)

    # --- Longitude ---
    def test_valid_longitude_passes(self):
        self.assertEqual(self.s.validate_Longitude(69.24), 69.24)

    def test_longitude_out_of_range_rejected(self):
        with self.assertRaises(serializers.ValidationError):
            self.s.validate_Longitude(181)
        with self.assertRaises(serializers.ValidationError):
            self.s.validate_Longitude(-180.1)

    # --- Depth ---
    def test_negative_depth_rejected(self):
        with self.assertRaises(serializers.ValidationError):
            self.s.validate_Depth(-1)

    def test_zero_depth_allowed(self):
        self.assertEqual(self.s.validate_Depth(0), 0)

    # --- Mb ---
    def test_negative_magnitude_rejected(self):
        with self.assertRaises(serializers.ValidationError):
            self.s.validate_Mb(-0.5)

    def test_valid_magnitude_passes(self):
        self.assertEqual(self.s.validate_Mb(4.2), 4.2)
