import logging
import json
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Tuple

import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.http import require_GET
from django.core.cache import cache
from django.utils import timezone
from django.db.models import Q

from .models import Station, Measurement

logger = logging.getLogger(__name__)

# ─── Konstantalar ────────────────────────────────────────────────────────────

COLOR_PALETTE = [
    "#2196F3", "#4CAF50", "#FF9800", "#9C27B0",
    "#F44336", "#00BCD4", "#795548", "#607D8B",
    "#E91E63", "#009688", "#FF5722", "#3F51B5",
]

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
    cache_key = "monitoring_active_stations"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    stations = list(Station.objects.filter(is_active=True).order_by("name"))
    cache.set(cache_key, stations, 300)  # 5 daqiqa
    logger.info(f"✅ Fetched {len(stations)} active stations from DB")
    return stations


def fetch_measurements(
    station_ids: List[int],
    days: int = 365,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
) -> pd.DataFrame:
    """
    Berilgan stantsiyalar uchun o'lchov ma'lumotlarini qaytaradi.
    """
    try:
        qs = Measurement.objects.filter(
            station_id__in=station_ids,
            is_valid=True,
            value__isnull=False,
        ).select_related("station").order_by("measured_at")

        if start_date and end_date:
            qs = qs.filter(measured_at__range=(start_date, end_date))
        elif days and days > 0:
            cutoff = timezone.now() - timedelta(days=days)
            qs = qs.filter(measured_at__gte=cutoff)

        values = qs.values(
            "station_id",
            "station__name",
            "measured_at",
            "value",
        )

        if not values.exists():
            logger.warning(f"⚠️ No measurements found for stations {station_ids}")
            return pd.DataFrame()

        df = pd.DataFrame(list(values))
        df.rename(columns={
            "station__name": "station_name",
            "measured_at":   "measured_at",
            "value":         "value",
        }, inplace=True)
        df["measured_at"] = pd.to_datetime(df["measured_at"])
        df.sort_values("measured_at", inplace=True)
        df.reset_index(drop=True, inplace=True)

        logger.info(f"✅ Fetched {len(df)} measurements for stations {station_ids}")
        return df

    except Exception as e:
        logger.error(f"❌ fetch_measurements error: {e}", exc_info=True)
        return pd.DataFrame()


def compute_stats(series: pd.Series) -> Tuple[float, float]:
    """Mean va standart og'ishni hisoblaydi."""
    mean  = float(series.mean())
    sigma = float(series.std())
    return mean, sigma


def build_chart(
    df_station: pd.DataFrame,
    station_name: str,
    color: str,
    sigma_multiplier: float,
) -> Optional[go.Figure]:
    """
    Bitta stantsiya uchun Plotly grafigi yaratadi.
    Mean ± N*sigma chiziqlarini va anomaliyalarni ko'rsatadi.
    """
    if df_station.empty or len(df_station) < 2:
        logger.warning(f"⚠️ Not enough data for {station_name}")
        return None

    x = df_station["measured_at"].tolist()
    y = df_station["value"].tolist()

    mean, sigma = compute_stats(df_station["value"])
    upper = mean + sigma_multiplier * sigma
    lower = mean - sigma_multiplier * sigma

    # Anomaliyalarni ajratish
    y_arr    = np.array(y)
    anom_idx = np.where((y_arr > upper) | (y_arr < lower))[0]
    norm_idx = np.setdiff1d(np.arange(len(y)), anom_idx)

    fig = make_subplots(specs=[[{"secondary_y": False}]])

    # Normal nuqtalar
    fig.add_trace(go.Scatter(
        x=[x[i] for i in norm_idx],
        y=[y[i] for i in norm_idx],
        mode="lines+markers",
        name=station_name,
        line=dict(color=color, width=1.5),
        marker=dict(size=4, color=color),
        hovertemplate="%{x|%d.%m.%Y %H:%M}<br>Qiymat: %{y:.3f}<extra></extra>",
    ))

    # Anomal nuqtalar
    if len(anom_idx) > 0:
        fig.add_trace(go.Scatter(
            x=[x[i] for i in anom_idx],
            y=[y[i] for i in anom_idx],
            mode="markers",
            name="Anomaliya",
            marker=dict(size=7, color="red", symbol="circle-open", line=dict(width=2)),
            hovertemplate="%{x|%d.%m.%Y %H:%M}<br><b>Anomaliya: %{y:.3f}</b><extra></extra>",
        ))

    # Mean chizig'i
    fig.add_hline(
        y=mean,
        line_dash="dash",
        line_color="gray",
        annotation_text=f"O'rtacha: {mean:.3f}",
        annotation_position="top left",
    )

    # Upper/lower band
    fig.add_hrect(
        y0=lower, y1=upper,
        fillcolor="rgba(200,200,200,0.15)",
        line_width=0,
        annotation_text=f"±{sigma_multiplier}σ",
        annotation_position="top right",
    )

    # Layout
    y_min = min(y)
    y_max = max(y)
    pad   = (y_max - y_min) * 0.1 or 1

    fig.update_layout(
        title=dict(text=station_name, font=dict(size=15)),
        height=420,
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
            title="Qiymat",
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
    """Plotly figurani HTML ga aylantiradi."""
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

def index(request):
    """
    Asosiy sahifa — stantsiya tanlash va filtrlar.
    """
    stations = get_active_stations()
    context = {
        "stations":       stations,
        "period_options": PERIOD_OPTIONS,
    }
    return render(request, "monitoring_app/index.html", context)


def charts(request):
    """
    Grafiklar sahifasi.
    GET parametrlari:
        station_ids  — vergul bilan ajratilgan stantsiya ID lari
        days         — necha kunlik ma'lumot (0 = barchasi)
        start_date   — boshlanish sanasi (YYYY-MM-DD)
        end_date     — tugash sanasi (YYYY-MM-DD)
        sigma        — sigma multiplikatori (default 2)
    """
    # ── Parametrlarni o'qish ──────────────────────────────────────────────
    raw_ids    = request.GET.get("station_ids", "")
    days_raw   = request.GET.get("days", "365")
    start_raw  = request.GET.get("start_date", "")
    end_raw    = request.GET.get("end_date", "")
    sigma_raw  = request.GET.get("sigma", "2")

    # Validatsiya
    try:
        station_ids = [int(i) for i in raw_ids.split(",") if i.strip().isdigit()]
    except (ValueError, AttributeError):
        station_ids = []

    try:
        days = int(days_raw)
    except ValueError:
        days = 365

    try:
        sigma_multiplier = float(sigma_raw)
        sigma_multiplier = max(1.0, min(sigma_multiplier, 5.0))
    except ValueError:
        sigma_multiplier = 2.0

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

    # ── Ma'lumot yo'qligi ─────────────────────────────────────────────────
    if not station_ids:
        return render(request, "monitoring_app/charts.html", {
            "error": "Kamida bitta stantsiyani tanlang.",
            "stations": get_active_stations(),
            "period_options": PERIOD_OPTIONS,
        })

    # ── Ma'lumotlarni olish ───────────────────────────────────────────────
    df = fetch_measurements(
        station_ids=station_ids,
        days=days,
        start_date=start_date,
        end_date=end_date,
    )

    graphs_list = []

    if df.empty:
        error_msg = "Tanlangan stantsiyalar uchun ma'lumot topilmadi."
    else:
        error_msg = None
        # Har bir stantsiya uchun alohida grafik
        for idx, station_id in enumerate(station_ids):
            df_s = df[df["station_id"] == station_id].copy()
            if df_s.empty:
                continue

            station_name = df_s["station_name"].iloc[0]
            color        = COLOR_PALETTE[idx % len(COLOR_PALETTE)]

            fig = build_chart(
                df_station=df_s,
                station_name=station_name,
                color=color,
                sigma_multiplier=sigma_multiplier,
            )
            if fig is None:
                continue

            div_id    = f"plot_{idx}"
            html_chunk = fig_to_html(fig, div_id)

            # Statistika
            mean, sigma = compute_stats(df_s["value"])
            anom_count  = int(((df_s["value"] > mean + sigma_multiplier * sigma) |
                               (df_s["value"] < mean - sigma_multiplier * sigma)).sum())

            graphs_list.append({
                "html":        html_chunk,
                "title":       station_name,
                "div_id":      div_id,
                "color":       color,
                "count":       len(df_s),
                "mean":        round(mean, 4),
                "sigma":       round(sigma, 4),
                "anom_count":  anom_count,
                "start":       df_s["measured_at"].min().strftime("%d.%m.%Y"),
                "end":         df_s["measured_at"].max().strftime("%d.%m.%Y"),
            })

    # Tanlangan stantsiyalar ma'lumoti
    selected_stations = Station.objects.filter(id__in=station_ids)

    context = {
        "graphs_list":       graphs_list,
        "error":             error_msg,
        "stations":          get_active_stations(),
        "selected_ids":      station_ids,
        "selected_stations": selected_stations,
        "period_options":    PERIOD_OPTIONS,
        "days":              days,
        "start_date":        start_raw,
        "end_date":          end_raw,
        "sigma":             sigma_multiplier,
        "total_graphs":      len(graphs_list),
    }
    return render(request, "monitoring_app/charts.html", context)


@require_GET
def api_stations(request):
    """
    AJAX: Barcha faol stantsiyalar ro'yxatini JSON da qaytaradi.
    """
    stations = get_active_stations()
    data = [
        {
            "id":       s.id,
            "name":     s.name,
            "code":     s.code,
            "location": s.location,
            "lat":      float(s.latitude)  if s.latitude  else None,
            "lon":      float(s.longitude) if s.longitude else None,
        }
        for s in stations
    ]
    return JsonResponse({"stations": data})


@require_GET
def api_measurements(request):
    """
    AJAX: Berilgan stantsiya uchun o'lchov ma'lumotlarini JSON da qaytaradi.
    Grafik sahifasini AJAX bilan yangilash uchun ishlatiladi.
    """
    raw_ids  = request.GET.get("station_ids", "")
    days_raw = request.GET.get("days", "30")

    try:
        station_ids = [int(i) for i in raw_ids.split(",") if i.strip().isdigit()]
        days        = int(days_raw)
    except (ValueError, AttributeError):
        return JsonResponse({"error": "Noto'g'ri parametrlar"}, status=400)

    if not station_ids:
        return JsonResponse({"error": "station_ids kerak"}, status=400)

    df = fetch_measurements(station_ids=station_ids, days=days)

    if df.empty:
        return JsonResponse({"data": []})

    result = []
    for sid in station_ids:
        sub = df[df["station_id"] == sid]
        if sub.empty:
            continue
        result.append({
            "station_id":   sid,
            "station_name": sub["station_name"].iloc[0],
            "dates":        sub["measured_at"].dt.strftime("%Y-%m-%dT%H:%M:%S").tolist(),
            "values":       sub["value"].tolist(),
        })

    return JsonResponse({"data": result})