"""
app_anomaly uchun JSON API qatlami (React frontend uchun).

Eski anomaly_analysis_view'ga tegilmagan — bu fayl undagi tayyor
funksiyalarni qayta ishlatadi: get_parameter_data_for_period,
detect_anomalies_in_data, get_all_parameters.

Endpointlar:
  GET  /anomaly/api/options/  — quduqlar, parametrlar, tanlov variantlari
  POST /anomaly/api/analyze/  — anomaliya tahlili (natija JSON, grafik frontendda chiziladi)
"""

import logging
from datetime import datetime

import pandas as pd
from dateutil.relativedelta import relativedelta
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status

from seismos_app.views import fetch_data, fetch_and_filter_earthquakes
from .views import (
    get_all_parameters,
    get_parameter_data_for_period,
    detect_anomalies_in_data,
)
from .models import AnomalyRecord
from .serializers import AnomalyAnalyzeRequestSerializer

logger = logging.getLogger(__name__)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def api_options(request):
    """Tahlil formasi uchun boshlang'ich ma'lumotlar."""
    lst_stansiya, well_coords = fetch_data()

    return Response({
        "wells": sorted(lst_stansiya.keys()),
        "params": get_all_parameters(),
        "time_periods": [c[0] for c in AnomalyRecord.TIME_PERIOD_CHOICES],
        "durations": [c[0] for c in AnomalyRecord.ANOMALY_DURATION_CHOICES],
        "well_coords": {
            name: {"lat": lat, "lon": lon}
            for name, (lat, lon) in well_coords.items()
        },
    })


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def api_analyze(request):
    """
    Body (JSON):
    {
      "wells":            ["Stansiya | Skvajina", ...],  (majburiy)
      "parameters":       ["He", ...],                   (majburiy)
      "time_period":      6,       (oy — TIME_PERIOD_CHOICES dan)
      "anomaly_duration": 3,       (minimal ketma-ket anomal qiymatlar soni)
      "recent_days":      7,       (oxirgi necha kunlik anomaliyalar)
      "sigma":            2.0,
      "magnitude":        4.0      (ixtiyoriy — berilsa zilzilalar ham qaytadi)
    }
    """
    req = AnomalyAnalyzeRequestSerializer(data=request.data)
    if not req.is_valid():
        first_error = next(iter(req.errors.values()))
        msg = first_error[0] if isinstance(first_error, list) else str(first_error)
        return Response(
            {"error": str(msg), "details": req.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )
    v = req.validated_data

    selected_wells = v["wells"]
    selected_params = v["parameters"]
    time_period = v["time_period"]
    anomaly_duration = v["anomaly_duration"]
    recent_days = v["recent_days"]
    sigma = v["sigma"]
    magnitude = v["magnitude"]

    lst_stansiya, well_coords = fetch_data()

    today = datetime.now().date()
    filter_start_date = pd.Timestamp(today - relativedelta(days=recent_days))

    # Zilzilalar — faqat magnitude berilgan bo'lsa (eski view bilan bir xil)
    earthquakes_out = []
    if magnitude is not None:
        try:
            eq_start = today - relativedelta(months=time_period)
            eq_df = fetch_and_filter_earthquakes(
                min_mag=magnitude,
                start_date=pd.Timestamp(eq_start),
                end_date=pd.Timestamp(today),
            )
            if eq_df is not None and not eq_df.empty:
                earthquakes_out = [
                    {
                        "datetime": row["combined_datetime"].strftime("%Y-%m-%dT%H:%M:%S"),
                        "mb": round(float(row["Mb"]), 2),
                        "lat": float(row["Latitude"]),
                        "lon": float(row["Longitude"]),
                    }
                    for _, row in eq_df.iterrows()
                ]
        except Exception as e:
            logger.warning(f"Zilzila error: {e}")

    results = []
    anomalous_wells_dict = {}

    for well in selected_wells:
        well_name = well.split(" | ")[1] if " | " in well else well
        coords = well_coords.get(well_name.strip())

        for param in selected_params:
            ssdi_id = lst_stansiya.get(well, {}).get(param)
            if not ssdi_id:
                continue

            df = get_parameter_data_for_period(ssdi_id, time_period)
            if df.empty:
                continue

            all_anomalies = detect_anomalies_in_data(
                df, sigma=sigma, min_duration_days=anomaly_duration
            )
            recent_anomalies = [
                a for a in all_anomalies if a["end_date"] >= filter_start_date
            ]
            if not recent_anomalies:
                continue

            anomalous_wells_dict.setdefault(well_name, []).append(param)

            clean = df.dropna(subset=["date", "value"]).sort_values("date")
            mean = float(clean["value"].mean())
            std = float(clean["value"].std())

            results.append({
                "well": well,
                "skvajina": well_name,
                "param": param,
                "lat": coords[0] if coords else None,
                "lon": coords[1] if coords else None,
                "dates": [d.strftime("%Y-%m-%d") for d in clean["date"]],
                "values": [round(float(v), 6) for v in clean["value"]],
                "mean": round(mean, 6),
                "sigma_value": round(std, 6),
                "upper": round(mean + sigma * std, 6),
                "lower": round(mean - sigma * std, 6),
                "anomalies": [
                    {
                        "start_date": a["start_date"].strftime("%Y-%m-%d"),
                        "end_date": a["end_date"].strftime("%Y-%m-%d"),
                        "count": a["count"],
                        "dates": [d.strftime("%Y-%m-%d") for d in a["dates"]],
                        "values": [round(float(v), 6) for v in a["values"]],
                    }
                    for a in recent_anomalies
                ],
            })

            # Bazaga yozish — bir xil tahlil qayta bosilsa DUBLIKAT YARATMAYDI:
            # o'sha kalit maydonlar bo'yicha mavjud yozuv yangilanadi
            try:
                first, last = recent_anomalies[0], recent_anomalies[-1]
                AnomalyRecord.objects.update_or_create(
                    skvajina=well_name,
                    parameter=param,
                    time_period_months=time_period,
                    anomaly_duration_days=anomaly_duration,
                    recent_days_filter=recent_days,
                    anomaly_start_date=first["start_date"].date(),
                    anomaly_end_date=last["end_date"].date(),
                    defaults={
                        "magnitude": magnitude,
                        "detected_anomalies_count": len(recent_anomalies),
                        "is_active": True,
                    },
                )
            except Exception as e:
                logger.error(f"Database xato: {e}")

    # Xarita: barcha quduqlar, anomaliyalilar belgilangan
    map_wells = [
        {
            "name": name,
            "lat": lat,
            "lon": lon,
            "anomalous": name in anomalous_wells_dict,
            "params": anomalous_wells_dict.get(name, []),
        }
        for name, (lat, lon) in well_coords.items()
    ]

    return Response({
        "results": results,
        "map": {"wells": map_wells, "earthquakes": earthquakes_out},
        "anomalous_wells_count": len(anomalous_wells_dict),
        "meta": {
            "time_period": time_period,
            "anomaly_duration": anomaly_duration,
            "recent_days": recent_days,
            "sigma": sigma,
            "magnitude": magnitude,
        },
    })
