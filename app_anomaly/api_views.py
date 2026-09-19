"""
app_anomaly uchun JSON API qatlami (React frontend uchun).

Eski anomaly_analysis_view'ga tegilmagan — bu fayl undagi tayyor
funksiyalarni qayta ishlatadi: get_parameter_data_for_period,
detect_anomalies_in_data, get_all_parameters.

v1 (Seismo) dagi create_anomaly_chart HTML grafigi React'da qayta
chiziladi, shuning uchun uning ikkita o'ziga xos hisobi shu yerga
ko'chirildi (natija eski grafik bilan bir xil bo'lishi uchun):
  * _anomaly_segments  — ±sigma chegarasini kesib o'tgan qizil segmentlar,
                         chetlari matematik interpolatsiya bilan aniq
                         kesishish nuqtasiga qo'yiladi;
  * _well_earthquakes  — har bir quduq uchun zilzilalar + quduqdan
                         Haversine masofa (km), grafikda ikkinchi Y o'qida
                         stem chiziq sifatida chiziladi.

Endpointlar:
  GET  /anomaly/api/options/  — quduqlar, parametrlar, tanlov variantlari
  POST /anomaly/api/analyze/  — anomaliya tahlili (natija JSON, grafik frontendda chiziladi)
"""

import logging
from datetime import datetime, timedelta

import pandas as pd
from dateutil.relativedelta import relativedelta
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status

from seismos_app.views import (
    fetch_data,
    fetch_and_filter_earthquakes,
    destenc_vectorized,
)
from .views import (
    get_all_parameters,
    get_parameter_data_for_period,
    detect_anomalies_in_data,
)
from .models import AnomalyRecord
from .serializers import AnomalyAnalyzeRequestSerializer

logger = logging.getLogger(__name__)

# v1 create_anomaly_chart: x o'qiga ikki tomondan 10 kun bo'sh joy qo'shilgan
X_PADDING_DAYS = 10

DT_FMT = "%Y-%m-%d %H:%M:%S"


def _anomaly_segments(dates, values, upper_bound, lower_bound, filter_start_date):
    """v1 create_anomaly_chart dagi qizil segment mantig'ining aynan nusxasi.

    Qaytaradi: [{"dates": [...], "values": [...]}, ...]

    Diqqat: bu yerda min_duration_days QO'LLANMAYDI — eski grafikda ham
    chegaradan chiqqan har bir uzluksiz bo'lak qizil chizilgan, faqat
    segment OXIRI `filter_start_date` dan keyin bo'lishi shart bo'lgan.
    `filter_start_date` esa ma'lumotning oxirgi kunidan hisoblanadi
    (bugungi kundan emas) — eski kod bilan bir xil.
    """
    segments = []
    cur_x, cur_y = [], []
    prev_anom = False

    def close_segment():
        if cur_x and cur_x[-1] >= filter_start_date:
            segments.append({
                "dates": [d.strftime(DT_FMT) for d in cur_x],
                "values": [round(float(val), 6) for val in cur_y],
            })

    for i in range(len(dates)):
        x_curr, y_curr = dates[i], values[i]

        if pd.isna(y_curr):
            prev_anom = False
            cur_x, cur_y = [], []
            continue

        is_anom = (y_curr > upper_bound) or (y_curr < lower_bound)

        if i == 0:
            if is_anom:
                cur_x.append(x_curr)
                cur_y.append(y_curr)
            prev_anom = is_anom
            continue

        x_prev, y_prev = dates[i - 1], values[i - 1]
        intersect_x = None
        intersect_y = None

        # Upper bound bilan kesishish
        if (y_prev < upper_bound <= y_curr) or (y_curr < upper_bound <= y_prev):
            if abs(y_curr - y_prev) > 1e-9:
                ratio = (upper_bound - y_prev) / (y_curr - y_prev)
                if 0 <= ratio <= 1:
                    time_diff = (x_curr - x_prev).total_seconds()
                    intersect_x = x_prev + timedelta(seconds=time_diff * ratio)
                    intersect_y = upper_bound

        # Lower bound bilan kesishish
        if (y_prev > lower_bound >= y_curr) or (y_curr > lower_bound >= y_prev):
            if abs(y_curr - y_prev) > 1e-9:
                ratio = (lower_bound - y_prev) / (y_curr - y_prev)
                if 0 <= ratio <= 1:
                    time_diff = (x_curr - x_prev).total_seconds()
                    intersect_x = x_prev + timedelta(seconds=time_diff * ratio)
                    intersect_y = lower_bound

        if is_anom != prev_anom:
            if prev_anom and cur_x:
                # Anomaliya tugadi — kesishish nuqtasi bilan yopamiz
                if intersect_x is not None:
                    cur_x.append(intersect_x)
                    cur_y.append(intersect_y)
                close_segment()
                cur_x, cur_y = [], []

            if is_anom:
                # Anomaliya boshlandi — kesishish nuqtasidan boshlaymiz
                if intersect_x is not None:
                    cur_x.append(intersect_x)
                    cur_y.append(intersect_y)
                cur_x.append(x_curr)
                cur_y.append(y_curr)

        elif is_anom:
            cur_x.append(x_curr)
            cur_y.append(y_curr)

        prev_anom = is_anom

    if prev_anom and cur_x:
        close_segment()

    return segments


def _well_earthquakes(eq_df, well_lat, well_lon, date_min, date_max):
    """v1 create_anomaly_chart dagi zilzila qismi: grafik sanasi oralig'iga
    tushgan zilzilalar + quduqdan Haversine masofa (km)."""
    if eq_df is None or eq_df.empty or well_lat is None or well_lon is None:
        return [], None

    try:
        df = eq_df.copy()
        df["distance"] = destenc_vectorized(
            well_lat, well_lon, df["Latitude"], df["Longitude"]
        )
        df = df[
            (df["combined_datetime"] >= date_min) & (df["combined_datetime"] <= date_max)
        ]
        if df.empty:
            return [], None

        out = [
            {
                "datetime": row["combined_datetime"].strftime(DT_FMT),
                "mb": round(float(row["Mb"]), 2),
                "distance": round(float(row["distance"]), 1),
            }
            for _, row in df.iterrows()
        ]
        # Eski kod: o'ng o'q diapazoni [0, max(Mb) * 1.1]
        axis_max = round(float(df["Mb"].max()) * 1.1, 3)
        return out, axis_max
    except Exception as e:
        logger.warning(f"Zilzila (quduq bo'yicha) xato: {e}")
        return [], None


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
    eq_df = None
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
                        "depth": (
                            None if pd.isna(row.get("Depth"))
                            else round(float(row["Depth"]), 1)
                        ),
                    }
                    for _, row in eq_df.iterrows()
                ]
        except Exception as e:
            logger.warning(f"Zilzila error: {e}")
            eq_df = None

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
            upper = mean + sigma * std
            lower = mean - sigma * std

            date_min = clean["date"].min()
            date_max = clean["date"].max()

            # v1: segment filtri MA'LUMOTNING oxirgi kunidan hisoblanadi
            seg_filter_start = date_max - pd.Timedelta(days=recent_days)
            segments = _anomaly_segments(
                list(clean["date"]),
                list(clean["value"]),
                upper,
                lower,
                seg_filter_start,
            )

            well_lat = coords[0] if coords else None
            well_lon = coords[1] if coords else None
            well_eqs, eq_axis_max = _well_earthquakes(
                eq_df, well_lat, well_lon, date_min, date_max
            )

            padding = pd.Timedelta(days=X_PADDING_DAYS)

            results.append({
                "well": well,
                "skvajina": well_name,
                "param": param,
                "lat": well_lat,
                "lon": well_lon,
                "dates": [d.strftime("%Y-%m-%d") for d in clean["date"]],
                "values": [round(float(val), 6) for val in clean["value"]],
                "mean": round(mean, 6),
                "sigma_value": round(std, 6),
                "upper": round(upper, 6),
                "lower": round(lower, 6),
                # v1 grafigidagi qizil segmentlar (interpolatsiyalangan chetlar bilan)
                "segments": segments,
                # v1 grafigidagi x o'qi diapazoni (±10 kun)
                "x_range": [
                    (date_min - padding).strftime(DT_FMT),
                    (date_max + padding).strftime(DT_FMT),
                ],
                # v1 grafigidagi o'ng o'q (Magnituda) — shu quduqqa tegishli zilzilalar
                "earthquakes": well_eqs,
                "eq_axis_max": eq_axis_max,
                "anomalies": [
                    {
                        "start_date": a["start_date"].strftime("%Y-%m-%d"),
                        "end_date": a["end_date"].strftime("%Y-%m-%d"),
                        "count": a["count"],
                        "dates": [d.strftime("%Y-%m-%d") for d in a["dates"]],
                        "values": [round(float(val), 6) for val in a["values"]],
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
