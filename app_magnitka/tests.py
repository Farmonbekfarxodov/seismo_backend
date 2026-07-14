"""
API endpoint testlari: autentifikatsiya, himoya va kirish validatsiyasi.

Ishga tushirish:
    python manage.py test app_users --settings=seismo_project.settings_test

Eslatma: bu testlar tashqi MySQL/Redis'ga tegmaydi (settings_test SQLite +
LocMem kesh ishlatadi). Ma'lumot bazasiga bog'liq og'ir endpointlar
(masalan, /seismos/api/series/ ning to'liq oqimi) bu yerda faqat
validatsiya darajasida tekshiriladi — chunki ular haqiqiy `alldata`
jadvalini talab qiladi.
"""

from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

User = get_user_model()


def make_user(username="testuser", password="testpass123"):
    return User.objects.create_user(username=username, password=password)


class JwtAuthTests(APITestCase):
    """JWT olish oqimi (POST /api/token/)."""

    def setUp(self):
        make_user()

    def test_token_obtain_success(self):
        resp = self.client.post(
            "/api/token/", {"username": "testuser", "password": "testpass123"}
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn("access", resp.data)
        self.assertIn("refresh", resp.data)

    def test_token_obtain_wrong_password(self):
        resp = self.client.post(
            "/api/token/", {"username": "testuser", "password": "notright"}
        )
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_token_refresh(self):
        obtain = self.client.post(
            "/api/token/", {"username": "testuser", "password": "testpass123"}
        )
        resp = self.client.post("/api/token/refresh/", {"refresh": obtain.data["refresh"]})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn("access", resp.data)


class EndpointSecurityTests(APITestCase):
    """Barcha API endpointlar tokensiz 401 qaytarishi SHART."""

    PROTECTED_GET = [
        "/seismos/api/options/",
        "/seismos/api/layers/",
        "/magnitka/api/stations/",
        "/magnitka/api/measurements/",
        "/magnitka/api/earthquakes/",
        "/anomaly/history/",
        "/anomaly/api/options/",
        "/informativlik/api/options/",
        "/upload/stations-wells/",
        "/upload/get-stations/",
    ]
    PROTECTED_POST = [
        "/seismos/api/series/",
        "/anomaly/api/analyze/",
        "/informativlik/api/analyze/",
        "/informativlik/api/export/",
        "/upload/magnitka/",
        "/upload/api/",
        "/upload/transfer/",
    ]

    def test_get_endpoints_require_auth(self):
        for url in self.PROTECTED_GET:
            resp = self.client.get(url)
            self.assertEqual(
                resp.status_code, status.HTTP_401_UNAUTHORIZED,
                f"{url} himoyalanmagan! ({resp.status_code})",
            )

    def test_post_endpoints_require_auth(self):
        for url in self.PROTECTED_POST:
            resp = self.client.post(url, {}, format="json")
            self.assertEqual(
                resp.status_code, status.HTTP_401_UNAUTHORIZED,
                f"{url} himoyalanmagan! ({resp.status_code})",
            )


class InputValidationTests(APITestCase):
    """Serializer validatsiyasi: noto'g'ri kirish -> 400 (bazaga tegmasdan)."""

    def setUp(self):
        self.user = make_user()
        self.client.force_authenticate(user=self.user)

    def test_seismos_series_requires_wells(self):
        resp = self.client.post("/seismos/api/series/", {"selected_keys": []}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_seismos_series_rejects_bad_filter_mode(self):
        resp = self.client.post(
            "/seismos/api/series/",
            {"selected_keys": ["X | Y"], "filter_mode": "noto'g'ri"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_anomaly_analyze_requires_wells_and_params(self):
        resp = self.client.post(
            "/anomaly/api/analyze/", {"wells": [], "parameters": []}, format="json"
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_informativlik_analyze_requires_selection(self):
        resp = self.client.post(
            "/informativlik/api/analyze/", {"wells": [], "params": []}, format="json"
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_informativlik_export_requires_results(self):
        resp = self.client.post("/informativlik/api/export/", {"results": []}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_upload_magnitka_requires_dates(self):
        resp = self.client.post("/upload/magnitka/", {}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_magnitka_measurements_requires_station_ids(self):
        resp = self.client.get("/magnitka/api/measurements/")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)


class WorkingEndpointTests(APITestCase):
    """Boshqariladigan (managed) modellarga tayanadigan endpointlarning ish oqimi."""

    def setUp(self):
        self.user = make_user()
        self.client.force_authenticate(user=self.user)

    def test_magnitka_stations_returns_list(self):
        from app_magnitka.models import Station

        Station.objects.create(name="Test stansiya", code="TST",
                               latitude=41.3, longitude=69.2, is_active=True)
        resp = self.client.get("/magnitka/api/stations/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        data = resp.json()
        self.assertEqual(len(data["stations"]), 1)
        st = data["stations"][0]
        self.assertEqual(st["code"], "TST")
        self.assertAlmostEqual(st["lat"], 41.3)

    def test_anomaly_history_returns_records(self):
        from app_anomaly.models import AnomalyRecord

        AnomalyRecord.objects.create(
            skvajina="Skv-1", parameter="He",
            time_period_months=6, anomaly_duration_days=3,
            recent_days_filter=7, detected_anomalies_count=2, is_active=True,
        )
        resp = self.client.get("/anomaly/history/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        data = resp.json()
        self.assertEqual(data["total_count"], 1)
        self.assertEqual(data["records"][0]["skvajina"], "Skv-1")

    def test_anomaly_inactive_records_hidden(self):
        from app_anomaly.models import AnomalyRecord

        AnomalyRecord.objects.create(
            skvajina="Skv-2", parameter="CO2",
            time_period_months=6, anomaly_duration_days=3,
            recent_days_filter=7, detected_anomalies_count=1, is_active=False,
        )
        resp = self.client.get("/anomaly/history/")
        self.assertEqual(resp.json()["total_count"], 0)
