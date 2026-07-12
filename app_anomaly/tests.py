"""
detect_anomalies_in_data algoritmi uchun unit testlar.

Ishga tushirish:
    python manage.py test app_anomaly --settings=seismo_project.settings_test
"""

import pandas as pd
from django.test import SimpleTestCase

from .views import detect_anomalies_in_data


def make_df(values, start="2026-01-01"):
    dates = pd.date_range(start=start, periods=len(values), freq="D")
    return pd.DataFrame({"date": dates, "value": values})


class DetectAnomaliesTests(SimpleTestCase):
    def test_empty_dataframe_returns_empty_list(self):
        self.assertEqual(detect_anomalies_in_data(pd.DataFrame()), [])

    def test_too_few_rows_returns_empty_list(self):
        df = make_df([1, 2, 3, 4])  # < 5 qator
        self.assertEqual(detect_anomalies_in_data(df), [])

    def test_no_anomalies_in_flat_data(self):
        # Deyarli o'zgarmas ma'lumot — sigma chegarasidan chiqish yo'q
        df = make_df([10.0, 10.1, 9.9, 10.0, 10.05, 9.95, 10.0, 10.1, 9.9, 10.0])
        anomalies = detect_anomalies_in_data(df, sigma=2.0, min_duration_days=1)
        self.assertEqual(anomalies, [])

    def test_single_spike_detected_with_min_duration_1(self):
        # 20 ta normal qiymat orasida bitta keskin sakrash
        values = [10.0] * 10 + [100.0] + [10.0] * 10
        # Bir xil qiymatlar std=0 bermasligi uchun ozgina shovqin
        values = [v + (i % 3) * 0.01 for i, v in enumerate(values)]
        df = make_df(values)
        anomalies = detect_anomalies_in_data(df, sigma=2.0, min_duration_days=1)
        self.assertEqual(len(anomalies), 1)
        self.assertEqual(anomalies[0]["count"], 1)

    def test_short_anomaly_filtered_by_min_duration(self):
        # Xuddi shu sakrash, lekin min_duration=3 — 1 ta qiymat yetmaydi
        values = [10.0] * 10 + [100.0] + [10.0] * 10
        values = [v + (i % 3) * 0.01 for i, v in enumerate(values)]
        df = make_df(values)
        anomalies = detect_anomalies_in_data(df, sigma=2.0, min_duration_days=3)
        self.assertEqual(anomalies, [])

    def test_consecutive_anomaly_run_counted_correctly(self):
        # 4 ta ketma-ket anomal qiymat
        values = [10.0] * 10 + [100.0, 101.0, 102.0, 100.0] + [10.0] * 10
        values = [v + (i % 3) * 0.01 for i, v in enumerate(values)]
        df = make_df(values)
        anomalies = detect_anomalies_in_data(df, sigma=2.0, min_duration_days=3)
        self.assertEqual(len(anomalies), 1)
        self.assertEqual(anomalies[0]["count"], 4)
        # start/end sanalar to'g'ri: 11-kun (index 10) dan 14-kungacha
        self.assertEqual(
            anomalies[0]["start_date"].strftime("%Y-%m-%d"), "2026-01-11"
        )
        self.assertEqual(
            anomalies[0]["end_date"].strftime("%Y-%m-%d"), "2026-01-14"
        )

    def test_anomaly_at_end_of_series_is_captured(self):
        # Seriya oxirida tugagan anomaliya ham hisobga olinishi kerak
        values = [10.0] * 15 + [100.0, 101.0, 102.0]
        values = [v + (i % 3) * 0.01 for i, v in enumerate(values)]
        df = make_df(values)
        anomalies = detect_anomalies_in_data(df, sigma=2.0, min_duration_days=2)
        self.assertEqual(len(anomalies), 1)
        self.assertEqual(anomalies[0]["count"], 3)

    def test_negative_anomalies_below_lower_bound(self):
        # Pastga chetlashishlar ham anomaliya hisoblanadi
        values = [10.0] * 10 + [-80.0, -85.0] + [10.0] * 10
        values = [v + (i % 3) * 0.01 for i, v in enumerate(values)]
        df = make_df(values)
        anomalies = detect_anomalies_in_data(df, sigma=2.0, min_duration_days=2)
        self.assertEqual(len(anomalies), 1)

    def test_nan_values_are_skipped(self):
        values = [10.0] * 8 + [float("nan")] + [10.0] * 8
        df = make_df(values)
        anomalies = detect_anomalies_in_data(df, sigma=2.0, min_duration_days=1)
        self.assertEqual(anomalies, [])
