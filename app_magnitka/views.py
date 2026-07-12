import logging
from datetime import datetime, timedelta
from typing import Optional, List

import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from django.shortcuts import render
from django.http import JsonResponse
from django.core.cache import cache
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated

from .models import Station, Measurement, Catalog
from .serializers import (
    StationSerializer,
    MeasurementsQuerySerializer,
    EarthquakesQuerySerializer,
)

logger = logging.getLogger(__name__)

# ─── Konstantalar ────────────────────────────────────────────────────────────

COLOR_PALETTE = [
    "#2196F3", "#4CAF50", "#FF9800", "#9C27B0",
    "#F44336", "#00BCD4", "#795548", "#607D8B",
    "#E91E63", "#009688", "#FF5722", "#3F51B5",
]

MAGNITUDE_LINE_COLOR = "rgba(220, 38, 38, 0.55)"  # qizil, yarim shaffof

# ✅ Delta hisoblash uchun baza stantsiya nomi
BASE_STATION_NAME = "Yangibozor"

PERIOD_OPTIONS = [
    ("7",   "1 hafta"),
    ("30",  "1 oy"),
    ("90",  "3 oy"),
    ("180", "6 oy"),
    ("365", "1 yil"),
    ("730", "2 yil"),
    ("0",   "Barchasi"),
]

# ─── Yordamchi funksiyalar ────────────────────────────────────────────────────

def get_active_stations() -> List[Station]:
    """Faol stantsiyalar ro'yxatini cache bilan qaytaradi."""
    cache_key = "magnitka_active_stations"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    stations = list(Station.objects.filter(is_active=True).order_by("name"))
    cache.set(cache_key, stations, 300)
    logger.info(f"✅ Fetched {len(stations)} active stations from DB")
    return stations


def fetch_measurements(
    station_ids: List[int],
    days: int = 365,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
) -> pd.DataFrame:
    """Berilgan stantsiyalar uchun o'lchov ma'lumotlarini qaytaradi."""
    try:
        qs = Measurement.objects.filter(
            station_id__in=station_ids,
            value__isnull=False,
        ).select_related("station").order_by("measured_at")

        if start_date and end_date:
            qs = qs.filter(measured_at__range=(start_date, end_date))
        elif days and days > 0:
            cutoff = timezone.now() - timedelta(days=days)
            qs = qs.filter(measured_at__gte=cutoff)

        values = qs.values("station_id", "station__name", "measured_at", "value")

        if not values.exists():
            logger.warning(f"⚠️ No measurements found for stations {station_ids}")
            return pd.DataFrame()

        df = pd.DataFrame(list(values))
        df.rename(columns={"station__name": "station_name"}, inplace=True)
        df["measured_at"] = pd.to_datetime(df["measured_at"])
        # ✅ Timezone-naive qilish (earthquakes_df bilan solishtirish uchun)
        if df["measured_at"].dt.tz is not None:
            df["measured_at"] = df["measured_at"].dt.tz_localize(None)
        df.sort_values("measured_at", inplace=True)
        df.reset_index(drop=True, inplace=True)

        logger.info(f"✅ Fetched {len(df)} measurements for stations {station_ids}")
        return df

    except Exception as e:
        logger.error(f"❌ fetch_measurements error: {e}", exc_info=True)
        return pd.DataFrame()


def fetch_earthquakes(
    min_mag: Optional[float] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
) -> pd.DataFrame:
    """
    `catalog` jadvalidan zilzilalarni oladi.
    min_mag berilmasa yoki None bo'lsa - barchasi qaytariladi.
    """
    try:
        qs = Catalog.objects.all().order_by("event_date", "event_time")

        if min_mag is not None:
            qs = qs.filter(mb__gte=min_mag)

        if start_date and end_date:
            qs = qs.filter(event_date__range=(start_date.date(), end_date.date()))

        values = qs.values("event_date", "event_time", "mb", "epicenter", "depth")

        if not values.exists():
            return pd.DataFrame()

        df = pd.DataFrame(list(values))

        # Sana + vaqtni birlashtirish
        df["event_time"] = df["event_time"].fillna(pd.Timestamp("00:00:00").time())
        df["event_datetime"] = pd.to_datetime(
            df["event_date"].astype(str) + " " + df["event_time"].astype(str),
            errors="coerce",
        )
        df.dropna(subset=["event_datetime"], inplace=True)
        df.sort_values("event_datetime", inplace=True)
        df.reset_index(drop=True, inplace=True)

        logger.info(f"✅ Fetched {len(df)} earthquakes (min_mag={min_mag})")
        return df

    except Exception as e:
        logger.error(f"❌ fetch_earthquakes error: {e}", exc_info=True)
        return pd.DataFrame()


def add_magnitude_lines(fig: go.Figure, earthquakes_df: pd.DataFrame, x_range_start, x_range_end):
    """
    Zilzilalarni vertikal chiziq sifatida grafikka qo'shadi.
    Faqat grafik X o'qi oralig'iga tushadigan zilzilalar chiziladi.
    """
    if earthquakes_df.empty:
        return

    # ✅ Himoya: ikkala tomonni ham timezone-naive holatga keltirish
    eq_dt = earthquakes_df["event_datetime"]
    if hasattr(eq_dt.dt, "tz") and eq_dt.dt.tz is not None:
        eq_dt = eq_dt.dt.tz_localize(None)

    if hasattr(x_range_start, "tzinfo") and x_range_start.tzinfo is not None:
        x_range_start = x_range_start.replace(tzinfo=None)
    if hasattr(x_range_end, "tzinfo") and x_range_end.tzinfo is not None:
        x_range_end = x_range_end.replace(tzinfo=None)

    in_range = earthquakes_df[
        (eq_dt >= x_range_start) &
        (eq_dt <= x_range_end)
    ]

    if in_range.empty:
        return

    for _, row in in_range.iterrows():
        mag_text = f"{row['mb']:.1f}" if pd.notnull(row["mb"]) else "?"

        # ✅ Sana qiymatini ISO-string ko'rinishida uzatamiz —
        # Plotly'ning add_vline ichidagi Timestamp+int arifmetika xatosini chetlab o'tish uchun
        x_val = row["event_datetime"]
        x_str = x_val.strftime("%Y-%m-%d %H:%M:%S") if hasattr(x_val, "strftime") else str(x_val)

        fig.add_shape(
            type="line",
            x0=x_str, x1=x_str,
            y0=0, y1=1,
            xref="x", yref="paper",
            line=dict(
                color=MAGNITUDE_LINE_COLOR,
                width=1,
                dash="dot",
            ),
        )
        fig.add_annotation(
            x=x_str,
            y=1,
            xref="x", yref="paper",
            text=f"M{mag_text}",
            showarrow=False,
            yshift=10,
            textangle=-90,
            font=dict(size=9, color=MAGNITUDE_LINE_COLOR),
        )

    logger.info(f"✅ Added {len(in_range)} magnitude lines to chart")


def aggregate_base_to_10min(base_df: pd.DataFrame) -> pd.DataFrame:
    """
    Yangibozor (baza stantsiya) ma'lumotini 10 minutlik qadamga keltiradi.

    Yangibozor har 1 minutda o'lchov yuboradi, qolgan stantsiyalar esa
    har 10 minutda. Taqqoslash to'g'ri bo'lishi uchun Yangibozorning
    1-minutdan 10-minutgacha bo'lgan qiymatlari qo'shilib, ularning
    o'rtachasi olinadi (oynada 10 tadan kam qiymat bo'lsa — mavjudlari
    soniga bo'linadi, ya'ni oddiy o'rtacha).

    Vaqt belgisi oyna OXIRIga yoziladi: 10:01–10:10 qiymatlari -> 10:10.
    Bu boshqa stantsiyalarning :00, :10, :20 ... dagi o'lchov vaqtlariga
    aynan mos keladi (delta hisoblashda measured_at bo'yicha join qilinadi).
    """
    if base_df.empty:
        return base_df

    df = base_df.copy()
    df["measured_at"] = pd.to_datetime(df["measured_at"])

    resampled = (
        df.set_index("measured_at")["value"]
        .resample("10min", closed="right", label="right")
        .mean()
        .dropna()
        .reset_index()
    )

    # Asl ustunlarni tiklash (station_id, station_name saqlanadi)
    for col in ("station_id", "station_name"):
        if col in base_df.columns:
            resampled[col] = base_df[col].iloc[0]

    logger.info(
        f"✅ {BASE_STATION_NAME}: {len(base_df)} ta 1-minutlik -> "
        f"{len(resampled)} ta 10-minutlik nuqta"
    )
    return resampled


def compute_delta_series(
    df_station: pd.DataFrame,
    base_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Berilgan stantsiya ma'lumotlaridan baza stantsiya (Yangibozor) qiymatini ayiradi.
    Faqat measured_at vaqti aniq mos kelganda delta hisoblanadi —
    mos kelmasa o'sha qator tashlab ketiladi.
    """
    if base_df.empty:
        logger.warning("⚠️ Baza stantsiya (Yangibozor) ma'lumoti yo'q — delta hisoblanmaydi")
        return pd.DataFrame()

    base_lookup = base_df.set_index("measured_at")["value"]

    merged = df_station.copy()
    merged["base_value"] = merged["measured_at"].map(base_lookup)

    # Bazada mos vaqt topilmagan qatorlarni tashlab yuborish
    merged.dropna(subset=["base_value"], inplace=True)

    merged["value"] = merged["value"] - merged["base_value"]
    merged.drop(columns=["base_value"], inplace=True)
    merged.reset_index(drop=True, inplace=True)

    return merged


def build_chart(
    df_station: pd.DataFrame,
    station_name: str,
    color: str,
    earthquakes_df: Optional[pd.DataFrame] = None,
    is_delta: bool = False,
) -> Optional[go.Figure]:
    """
    Bitta stantsiya uchun Plotly grafigi yaratadi: Sana (X) va Qiymat yoki Delta (Y).
    earthquakes_df berilgan bo'lsa, zilzilalar vertikal chiziq sifatida qo'shiladi.
    """
    if df_station.empty or len(df_station) < 2:
        logger.warning(f"⚠️ Not enough data for {station_name}")
        return None

    x = df_station["measured_at"].tolist()
    y = df_station["value"].tolist()

    y_axis_title = f"Δ (farq, {BASE_STATION_NAME} ga nisbatan)" if is_delta else "Qiymat"
    hover_label = "Δ" if is_delta else "Qiymat"

    fig = make_subplots(specs=[[{"secondary_y": False}]])

    fig.add_trace(go.Scatter(
        x=x,
        y=y,
        mode="lines+markers",
        name=station_name,
        line=dict(color=color, width=1.5),
        marker=dict(size=4, color=color),
        hovertemplate=f"%{{x|%d.%m.%Y %H:%M}}<br>{hover_label}: %{{y:.3f}}<extra></extra>",
    ))

    # Delta grafigida 0 chizig'ini ko'rsatish foydali
    if is_delta:
        fig.add_shape(
            type="line",
            x0=0, x1=1, xref="paper",
            y0=0, y1=0, yref="y",
            line=dict(color="#9E9E9E", width=1, dash="dash"),
        )

    x_start = min(x)
    x_end = max(x)

    # ── Magnitudalarni qo'shish (ixtiyoriy) ───────────────────────────────
    if earthquakes_df is not None and not earthquakes_df.empty:
        add_magnitude_lines(fig, earthquakes_df, x_start, x_end)

    y_min = min(y)
    y_max = max(y)
    pad = (y_max - y_min) * 0.1 or 1

    fig.update_layout(
        title=dict(text=station_name, font=dict(size=15)),
        height=440,
        autosize=True,
        plot_bgcolor="white",
        paper_bgcolor="white",
        hovermode="x unified",
        showlegend=True,
        margin=dict(l=60, r=40, t=60, b=50),
        legend=dict(x=0.01, y=0.99, bgcolor="rgba(255,255,255,0.85)"),
        xaxis=dict(
            showgrid=False,
            showline=True,
            linecolor="black",
            linewidth=1,
            type="date",
        ),
        yaxis=dict(
            title=y_axis_title,
            range=[y_min - pad, y_max + pad],
            showgrid=True,
            gridcolor="#f0f0f0",
            showline=True,
            linecolor="black",
            linewidth=1,
        ),
    )

    return fig


def fig_to_html(fig: go.Figure, div_id: str) -> str:
    config = {
        "displayModeBar": True,
        "displaylogo": False,
        "responsive": True,
        "modeBarButtonsToRemove": ["toImage"],
    }
    return fig.to_html(
        full_html=False,
        include_plotlyjs=False,
        config=config,
        div_id=div_id,
    )


# ─── Views ───────────────────────────────────────────────────────────────────

def results(request):
    """
    Yagona sahifa: stantsiya/filtr tanlash formasi + natija (grafiklar) bir joyda.
    GET  — bo'sh forma ko'rsatiladi.
    POST — forma yuborilgach, shu sahifaning o'zida grafiklar chiqadi.

    POST parametrlari:
        station_ids    — checkbox qiymatlari ro'yxati (getlist)
        days           — necha kunlik ma'lumot (0 = barchasi)
        start_date     — boshlanish sanasi (YYYY-MM-DD)
        end_date       — tugash sanasi (YYYY-MM-DD)
        show_magnitude — "1" bo'lsa, zilzilalar vertikal chiziq sifatida qo'shiladi
        min_magnitude  — magnitudalar uchun minimal chegara (default 0 = barchasi)
    """
    stations = get_active_stations()

    context = {
        "stations":          stations,
        "period_options":    PERIOD_OPTIONS,
        "base_station_name": BASE_STATION_NAME,
        "submitted":         False,
    }

    if request.method != "POST":
        return render(request, "app_magnitka/results.html", context)

    # ── POST parametrlarini o'qish ────────────────────────────────────────
    raw_ids   = request.POST.getlist("station_ids")
    days_raw  = request.POST.get("days", "365")
    start_raw = request.POST.get("start_date", "")
    end_raw   = request.POST.get("end_date", "")

    show_magnitude = request.POST.get("show_magnitude") == "1"
    min_mag_raw    = request.POST.get("min_magnitude", "0")

    try:
        station_ids = [int(i) for i in raw_ids if str(i).strip().isdigit()]
    except (ValueError, AttributeError):
        station_ids = []

    try:
        days = int(days_raw)
    except ValueError:
        days = 365

    try:
        min_magnitude = float(min_mag_raw)
    except ValueError:
        min_magnitude = 0.0

    start_date = end_date = None
    try:
        if start_raw:
            start_date = datetime.strptime(start_raw, "%Y-%m-%d")
        if end_raw:
            end_date = datetime.strptime(end_raw, "%Y-%m-%d").replace(
                hour=23, minute=59, second=59
            )
    except ValueError:
        pass

    context.update({
        "submitted":      True,
        "selected_ids":   station_ids,
        "days":           days,
        "start_date":     start_raw,
        "end_date":       end_raw,
        "show_magnitude": show_magnitude,
        "min_magnitude":  min_magnitude,
    })

    if not station_ids:
        context["error"] = "Kamida bitta stantsiyani tanlang."
        return render(request, "app_magnitka/results.html", context)

    df = fetch_measurements(
        station_ids=station_ids,
        days=days,
        start_date=start_date,
        end_date=end_date,
    )

    # Zilzilalar faqat foydalanuvchi so'raganda yuklanadi
    earthquakes_df = pd.DataFrame()
    if show_magnitude:
        earthquakes_df = fetch_earthquakes(
            min_mag=min_magnitude if min_magnitude > 0 else None,
            start_date=start_date,
            end_date=end_date,
        )

    graphs_list = []

    if df.empty:
        error_msg = "Tanlangan stantsiyalar uchun ma'lumot topilmadi."
    else:
        error_msg = None

        # ── Baza stantsiya (Yangibozor) ma'lumotini alohida ajratish ──────
        base_station_obj = Station.objects.filter(name=BASE_STATION_NAME).first()
        base_df = pd.DataFrame()

        if base_station_obj:
            if base_station_obj.id in station_ids:
                base_df = df[df["station_id"] == base_station_obj.id].copy()
            else:
                base_df = fetch_measurements(
                    station_ids=[base_station_obj.id],
                    days=days,
                    start_date=start_date,
                    end_date=end_date,
                )

            if base_df.empty:
                logger.warning(
                    f"⚠️ '{BASE_STATION_NAME}' stantsiyasi uchun shu davrda ma'lumot yo'q — delta hisoblanmaydi"
                )
            else:
                # MUHIM: Yangibozor 1-minutlik keladi — barcha hisob-kitoblardan
                # OLDIN 10-minutlik o'rtachaga keltiriladi
                base_df = aggregate_base_to_10min(base_df)
        else:
            logger.warning(f"⚠️ '{BASE_STATION_NAME}' nomli stantsiya bazada topilmadi")

        for idx, station_id in enumerate(station_ids):
            df_s = df[df["station_id"] == station_id].copy()
            if df_s.empty:
                continue

            station_name = df_s["station_name"].iloc[0]
            color = COLOR_PALETTE[idx % len(COLOR_PALETTE)]

            # ✅ Yangibozorning o'zi uchun — 10-minutlik o'rtacha (deltasiz)
            is_base_station = (station_name == BASE_STATION_NAME)

            if is_base_station:
                chart_df = base_df if not base_df.empty else aggregate_base_to_10min(df_s)
                is_delta = False
            elif base_df.empty:
                chart_df = df_s
                is_delta = False
            else:
                chart_df = compute_delta_series(df_s, base_df)
                is_delta = True

                if chart_df.empty:
                    logger.warning(
                        f"⚠️ {station_name}: {BASE_STATION_NAME} bilan mos vaqt topilmadi, grafik o'tkazib yuborildi"
                    )
                    continue

            fig = build_chart(
                df_station=chart_df,
                station_name=station_name,
                color=color,
                earthquakes_df=earthquakes_df if show_magnitude else None,
                is_delta=is_delta,
            )
            if fig is None:
                continue

            div_id = f"plot_{idx}"
            html_chunk = fig_to_html(fig, div_id)
            filename = f"Magnitka_{station_name}_{datetime.now().strftime('%Y%m%d')}"

            graphs_list.append({
                "html":     html_chunk,
                "title":    station_name,
                "div_id":   div_id,
                "filename": filename,
                "color":    color,
                "count":    len(chart_df),
                "is_delta": is_delta,
                "start":    chart_df["measured_at"].min().strftime("%d.%m.%Y"),
                "end":      chart_df["measured_at"].max().strftime("%d.%m.%Y"),
            })

    selected_stations = Station.objects.filter(id__in=station_ids)

    context.update({
        "graphs_list":       graphs_list,
        "error":             error_msg,
        "selected_stations": selected_stations,
        "earthquake_count":  len(earthquakes_df) if show_magnitude else 0,
        "total_graphs":      len(graphs_list),
    })
    return render(request, "app_magnitka/results.html", context)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def api_stations(request):
    """AJAX: Barcha faol stantsiyalar ro'yxatini JSON da qaytaradi."""
    stations = get_active_stations()
    data = StationSerializer(stations, many=True).data
    return JsonResponse({"stations": data})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def api_measurements(request):
    """AJAX: Berilgan stantsiyalar uchun o'lchov seriyalarini JSON da qaytaradi.

    Eski results_view bilan bir xil hisob mantiq:
      1. Yangibozor (baza) ma'lumoti avval 10-minutlik o'rtachaga keltiriladi
         (u 1-minutlik keladi, boshqalar 10-minutlik).
      2. Yangibozorning o'z seriyasi — shu 10-minutlik qiymatlar (deltasiz).
      3. Boshqa stantsiyalar — Δ = qiymat − Yangibozor (bir xil vaqtda),
         mos vaqt topilmagan nuqtalar tashlab yuboriladi.
    """
    query = MeasurementsQuerySerializer(data=request.GET)
    if not query.is_valid():
        return JsonResponse({"error": "Noto'g'ri parametrlar", "details": query.errors}, status=400)
    station_ids = query.validated_data["station_ids"]
    days = query.validated_data["days"]

    df = fetch_measurements(station_ids=station_ids, days=days)

    if df.empty:
        return JsonResponse({"data": [], "base_station": BASE_STATION_NAME})

    # ── Baza stantsiya (Yangibozor)ni topish va 10-minutlikka keltirish ──
    base_station_obj = Station.objects.filter(name=BASE_STATION_NAME).first()
    base_df = pd.DataFrame()
    if base_station_obj:
        if base_station_obj.id in station_ids:
            base_df = df[df["station_id"] == base_station_obj.id].copy()
        else:
            base_df = fetch_measurements(
                station_ids=[base_station_obj.id], days=days
            )
        if not base_df.empty:
            base_df = aggregate_base_to_10min(base_df)

    result = []
    for sid in station_ids:
        sub = df[df["station_id"] == sid].copy()
        if sub.empty:
            continue
        station_name = sub["station_name"].iloc[0]
        is_base = (station_name == BASE_STATION_NAME)

        if is_base:
            series_df = base_df if not base_df.empty else aggregate_base_to_10min(sub)
            is_delta = False
        elif base_df.empty:
            series_df = sub
            is_delta = False
        else:
            series_df = compute_delta_series(sub, base_df)
            is_delta = True
            if series_df.empty:
                # Yangibozor bilan mos vaqt topilmadi — seriyani belgilab qaytaramiz
                result.append({
                    "station_id": sid,
                    "station_name": station_name,
                    "dates": [], "values": [],
                    "is_delta": True, "no_match": True,
                })
                continue

        result.append({
            "station_id":   sid,
            "station_name": station_name,
            "dates":        pd.to_datetime(series_df["measured_at"]).dt.strftime("%Y-%m-%dT%H:%M:%S").tolist(),
            "values":       [round(float(v), 6) for v in series_df["value"]],
            "is_delta":     is_delta,
            "is_base":      is_base,
        })

    return JsonResponse({"data": result, "base_station": BASE_STATION_NAME})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def api_earthquakes(request):
    """AJAX: Zilzilalar katalogini JSON da qaytaradi (min_magnitude bilan filtrlash mumkin)."""
    query = EarthquakesQuerySerializer(data=request.GET)
    min_mag = (
        query.validated_data.get("min_magnitude") if query.is_valid() else None
    )

    df = fetch_earthquakes(min_mag=min_mag)

    if df.empty:
        return JsonResponse({"data": []})

    result = df.apply(lambda r: {
        "datetime":  r["event_datetime"].strftime("%Y-%m-%dT%H:%M:%S"),
        "magnitude": r["mb"],
        "epicenter": r["epicenter"],
        "depth":     r["depth"],
    }, axis=1).tolist()

    return JsonResponse({"data": result})