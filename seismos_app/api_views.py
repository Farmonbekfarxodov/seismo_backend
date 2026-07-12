"""
seismos_app uchun JSON API qatlami (React frontend uchun).

Eski views.py'dagi HTML render qiladigan view'larga tegilmagan — bu fayl
ularning yonida ishlaydi va o'sha yerdagi tayyor funksiyalarni qayta ishlatadi:
fetch_data, fetch_and_filter_earthquakes, destenc_vectorized.

Endpointlar:
  GET  /seismos/api/options/  — quduqlar, parametrlar, median oynalari, koordinatalar
  POST /seismos/api/series/   — tanlangan quduq+parametrlar uchun vaqt qatorlari,
                                statistika va tegishli zilzilalar (M/lgR filtri bilan)
"""

import logging

import numpy as np
import pandas as pd
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from sqlalchemy import text

from .serializers import SeriesRequestSerializer
from .views import (
    fetch_data,
    fetch_and_filter_earthquakes,
    destenc_vectorized,
    get_db_engine,
    get_well_detailed_info,
    DEFAULT_ELEMENTS_GROUPS,
)

logger = logging.getLogger(__name__)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def api_well_info(request):
    """Xaritada skvajina ustiga bosilganda chiqadigan batafsil ma'lumot.

    Eski folium popup'idagi jadvalning aynan manbasi: Malumot jadvalidan
    quduq turi, chuqurlik, seysmotektonik holat, strategrafik taqsimot,
    litologik tarkib va mineralizatsiya rasmi (base64).
    GET /seismos/api/well-info/?name=<skvajina>
    """
    name = (request.GET.get("name") or "").strip()
    if not name:
        return Response({"error": "name parametri kerak."}, status=status.HTTP_400_BAD_REQUEST)
    info = get_well_detailed_info(name)
    return Response(info)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def api_layers(request):
    """Xarita qatlamlari: yer yoriqlari (AFEAD) va seysmogen zonalar.

    Shapefile'lar GeoJSON'ga aylantirilib, geometriya soddalashtiriladi
    (payload ~1MB gacha) va 24 soat keshlanadi.
    Eski folium xaritadagi load_all_cracks_shapefiles /
    load_seismogenic_zones qatlamlarining aynan o'zi.
    """
    import glob
    import json
    import os

    import geopandas as gpd
    from django.conf import settings
    from django.core.cache import cache

    cached = cache.get("seismos_map_layers_v2")
    if cached:
        return Response(cached)

    shp_dir = os.path.join(settings.BASE_DIR, "static", "shapefiles")

    def load_many(patterns, keep_cols):
        frames = []
        for pattern in patterns:
            for path in glob.glob(os.path.join(shp_dir, pattern)):
                try:
                    gdf = gpd.read_file(path)
                    if gdf.crs and gdf.crs.to_epsg() != 4326:
                        gdf = gdf.to_crs(4326)
                    cols = [c for c in keep_cols if c in gdf.columns]
                    frames.append(gdf[cols + ["geometry"]])
                except Exception as e:
                    logger.error(f"Shapefile o'qishda xato {path}: {e}")
        if not frames:
            return None
        merged = pd.concat(frames, ignore_index=True)
        # Kuchliroq soddalashtirish: xarita masshtabida farq sezilmaydi,
        # lekin nuqtalar soni (va render vaqti) bir necha barobar kamayadi
        merged["geometry"] = merged.geometry.simplify(0.005)
        geo = json.loads(merged.to_json())

        # Koordinatalarni 4 xonagacha yaxlitlash (~11 m aniqlik) — JSON hajmi ~40% kichrayadi
        def _round(coords):
            if isinstance(coords, (list, tuple)):
                if coords and isinstance(coords[0], (int, float)):
                    return [round(coords[0], 4), round(coords[1], 4)]
                return [_round(x) for x in coords]
            return coords

        for feat in geo.get("features", []):
            geom = feat.get("geometry") or {}
            if "coordinates" in geom:
                geom["coordinates"] = _round(geom["coordinates"])
        return geo

    cracks = load_many(["AFEAD_*.shp", "Export_Output*.shp"], ["NAME", "RATE"])
    zones = load_many(
        ["Seysmogen_*.shp"], ["seysmogen_", "hududiy_ma", "seysmogen1"]
    )

    payload = {"cracks": cracks, "zones": zones}
    cache.set("seismos_map_layers_v2", payload, 60 * 60 * 24)
    return Response(payload)

MEDIAN_VALUES = [3, 5, 7, 15, 31, 91, 183, 365, 731]

# Bitta seriya uchun frontendga yuboriladigan maksimal nuqtalar soni.
# Bundan ko'p bo'lsa kunlik medianga avtomatik siqiladi (grafik sifatiga
# ta'sir qilmaydi, brauzer esa sekinlashmaydi).
MAX_POINTS_PER_SERIES = 3000


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def api_options(request):
    """Tanlash sahifasi uchun boshlang'ich ma'lumotlar."""
    lst_stansiya, well_coords = fetch_data()

    all_params = sorted(set(sum(DEFAULT_ELEMENTS_GROUPS.values(), [])))

    # "Mavjud ma'lumotlar oralig'i" banneri uchun (eski sahifadagidek)
    data_min_date, data_max_date = None, None
    try:
        engine = get_db_engine()
        with engine.connect() as conn:
            row = conn.execute(text("SELECT MIN(date), MAX(date) FROM alldata")).fetchone()
            if row and row[0]:
                data_min_date = pd.to_datetime(row[0]).strftime("%Y-%m-%d")
                data_max_date = pd.to_datetime(row[1]).strftime("%Y-%m-%d")
    except Exception as e:
        logger.warning(f"Data oralig'ini olishda xato: {e}")

    return Response({
        "wells": sorted(lst_stansiya.keys()),
        "params": all_params,
        "param_groups": DEFAULT_ELEMENTS_GROUPS,
        "median_values": MEDIAN_VALUES,
        "data_min_date": data_min_date,
        "data_max_date": data_max_date,
        "well_coords": {
            name: {"lat": lat, "lon": lon}
            for name, (lat, lon) in well_coords.items()
        },
    })


def _load_series(conn, ssdi_id, start_date, end_date, median_window):
    """Bitta (quduq, parametr) juftligi uchun tozalangan vaqt qatorini yuklaydi.

    Eski generate_all_graphs ichidagi ma'lumot yig'ish mantiqining ko'chirmasi,
    faqat grafiksiz — sof sonlar qaytaradi.
    """
    query = text(f"SELECT date, `{ssdi_id}` FROM alldata WHERE `{ssdi_id}` IS NOT NULL")
    data = conn.execute(query).fetchall()
    if not data:
        return None

    df = pd.DataFrame(data, columns=["date", "value"])
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df = df[(df["value"] != 0) & df["value"].notna() & df["date"].notna()]
    if df.empty:
        return None

    if start_date is not None:
        df = df[df["date"] >= start_date]
    if end_date is not None:
        df = df[df["date"] <= end_date]
    if df.empty or len(df) < 2:
        return None

    # Median oyna (foydalanuvchi tanlagan bo'lsa) — kunlik median + rolling median
    if median_window and median_window > 0:
        daily = (
            df.groupby(df["date"].dt.date)["value"]
            .median()
            .reset_index()
            .rename(columns={"value": "daily_median"})
        )
        daily["rolling"] = (
            daily["daily_median"]
            .rolling(window=median_window, min_periods=1, center=True)
            .median()
        )
        dates = pd.to_datetime(daily["date"])
        values = daily["rolling"]
    else:
        dates = df["date"]
        values = df["value"]

    # Juda katta seriyalarni kunlik medianga siqish (frontend uchun)
    if len(values) > MAX_POINTS_PER_SERIES:
        tmp = pd.DataFrame({"date": dates, "value": values})
        daily = tmp.groupby(tmp["date"].dt.date)["value"].median().reset_index()
        dates = pd.to_datetime(daily["date"])
        values = daily["value"]

    values = pd.to_numeric(values, errors="coerce")
    mask = values.notna()
    dates, values = dates[mask], values[mask]
    if len(values) < 2:
        return None

    mean = float(np.mean(values))
    sigma = float(np.std(values))
    if sigma == 0 or np.isnan(sigma) or np.isnan(mean):
        return None

    return {
        "dates": [d.strftime("%Y-%m-%d") for d in dates],
        "values": [round(float(v), 6) for v in values],
        "mean": round(mean, 6),
        "sigma": round(sigma, 6),
    }


def _earthquakes_for_well(eq_df, well_lat, well_lon, min_mag, min_mlgr, filter_mode):
    """Berilgan quduq uchun tegishli zilzilalarni M/lgR yoki Mb bo'yicha filtrlaydi."""
    if eq_df is None or eq_df.empty:
        return []

    df = eq_df.copy()

    if filter_mode == "mb":
        df = df[df["Mb"] >= min_mag]
        df["R(km)"] = np.round(
            destenc_vectorized(well_lat, well_lon, df["Latitude"], df["Longitude"])
        )
        df["M/lgR"] = np.nan
    else:
        df["R(km)"] = np.round(
            destenc_vectorized(well_lat, well_lon, df["Latitude"], df["Longitude"])
        )
        with np.errstate(divide="ignore", invalid="ignore"):
            df["M/lgR"] = np.where(
                df["R(km)"] > 1, df["Mb"] / np.log10(df["R(km)"]), np.nan
            )
        df = df[(df["Mb"] >= min_mag) & (df["M/lgR"] >= min_mlgr)]

    df = df.dropna(subset=["combined_datetime"])

    return [
        {
            "datetime": row["combined_datetime"].strftime("%Y-%m-%dT%H:%M:%S"),
            "mb": round(float(row["Mb"]), 2),
            "r_km": float(row["R(km)"]) if pd.notna(row["R(km)"]) else None,
            "mlgr": round(float(row["M/lgR"]), 2) if pd.notna(row["M/lgR"]) else None,
        }
        for _, row in df.iterrows()
    ]


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def api_series(request):
    """
    Body (JSON):
    {
      "selected_keys":   ["Stansiya | Skvajina", ...],   (majburiy)
      "selected_params": ["He", "CH4", ...],             (bo'sh bo'lsa — barchasi)
      "min_mag":       4.0,
      "min_mlgr":      2.5,
      "filter_mode":   "mlgr" | "mb",
      "start_date":    "YYYY-MM-DD"  (ixtiyoriy),
      "end_date":      "YYYY-MM-DD"  (ixtiyoriy),
      "median_window": 7             (ixtiyoriy, MEDIAN_VALUES dan)
    }
    """
    req = SeriesRequestSerializer(data=request.data)
    if not req.is_valid():
        # Birinchi xato matnini eski uslubda qaytaramiz (frontend shu formatni kutadi)
        first_error = next(iter(req.errors.values()))
        msg = first_error[0] if isinstance(first_error, list) else str(first_error)
        return Response(
            {"error": str(msg), "details": req.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )
    v = req.validated_data

    selected_keys = v["selected_keys"]
    selected_params = v["selected_params"] or sorted(
        set(sum(DEFAULT_ELEMENTS_GROUPS.values(), []))
    )
    min_mag = v["min_mag"]
    min_mlgr = v["min_mlgr"]
    sigma_factor = v["sigma"]
    segment_years = v["segment_years"]
    median_window = v["median_window"]
    filter_mode = v["filter_mode"]

    start_date = pd.Timestamp(v["start_date"]) if v["start_date"] else pd.to_datetime("1984-01-01")
    end_date = pd.Timestamp(v["end_date"]) if v["end_date"] else None

    lst_stansiya, well_coords = fetch_data()
    eq_df = fetch_and_filter_earthquakes(min_mag=min_mag, start_date=start_date, end_date=end_date)

    series_out = []
    engine = get_db_engine()
    conn = engine.connect()
    try:
        for key in selected_keys:
            _, skvajina = key.split(" | ") if " | " in key else ("", key)
            coords = well_coords.get(skvajina.strip())

            # Shu quduq uchun zilzilalar (barcha parametrlariga bitta ro'yxat)
            well_earthquakes = []
            if coords:
                well_earthquakes = _earthquakes_for_well(
                    eq_df, coords[0], coords[1], min_mag, min_mlgr, filter_mode
                )

            for param in selected_params:
                ssdi_id = lst_stansiya.get(key, {}).get(param)
                if not ssdi_id:
                    continue
                try:
                    series = _load_series(conn, ssdi_id, start_date, end_date, median_window)
                except Exception as e:
                    logger.error(f"Series query error {key} - {param}: {e}")
                    continue
                if series is None:
                    continue

                # Sigma-faktorli chegaralar (eski grafikdagi mean ± σ·std)
                upper = series["mean"] + sigma_factor * series["sigma"]
                lower = series["mean"] - sigma_factor * series["sigma"]

                # Yillik segmentlar (eski "Yillik UB/LB (Ny)" chiziqlari):
                # ma'lumot N-yillik guruhlarga bo'linib, har biriga alohida chegara
                segments = []
                if segment_years and segment_years > 0:
                    seg_df = pd.DataFrame({
                        "date": pd.to_datetime(series["dates"]),
                        "value": series["values"],
                    })
                    seg_df["group"] = (seg_df["date"].dt.year // segment_years) * segment_years
                    for g, grp in seg_df.groupby("group"):
                        if len(grp) < 2:
                            continue
                        g_mean = float(grp["value"].mean())
                        g_std = float(grp["value"].std())
                        segments.append({
                            "start": grp["date"].min().strftime("%Y-%m-%d"),
                            "end": grp["date"].max().strftime("%Y-%m-%d"),
                            "ub": round(g_mean + sigma_factor * g_std, 6),
                            "lb": round(g_mean - sigma_factor * g_std, 6),
                        })

                series_out.append({
                    "key": key,
                    "skvajina": skvajina.strip(),
                    "param": param,
                    "lat": coords[0] if coords else None,
                    "lon": coords[1] if coords else None,
                    **series,
                    "upper": round(upper, 6),
                    "lower": round(lower, 6),
                    "segments": segments,
                    "earthquakes": well_earthquakes,
                })
    finally:
        conn.close()

    # Xarita uchun: barcha filtrlangan zilzilalar. Eski xaritadagidek tooltip
    # uchun tanlangan quduqlargacha bo'lgan ENG YAQIN masofa va M/lgR ham beriladi.
    selected_coords = [
        well_coords[k.split(" | ")[1].strip()]
        for k in selected_keys
        if " | " in k and k.split(" | ")[1].strip() in well_coords
    ]
    map_earthquakes = []
    if eq_df is not None and not eq_df.empty:
        eq = eq_df.reset_index(drop=True)
        # VEKTORLASHTIRILGAN: har bir tanlangan quduq uchun barcha zilzilalarga
        # masofa bir yo'la hisoblanadi, keyin element bo'yicha minimal olinadi.
        min_r = None
        if selected_coords:
            dist_cols = [
                np.asarray(destenc_vectorized(la, lo, eq["Latitude"], eq["Longitude"]), dtype=float)
                for la, lo in selected_coords
            ]
            min_r = np.round(np.minimum.reduce(dist_cols))
        with np.errstate(divide="ignore", invalid="ignore"):
            mlgr_arr = (
                np.where(min_r > 1, eq["Mb"].to_numpy(dtype=float) / np.log10(min_r), np.nan)
                if min_r is not None else None
            )
        for i, row in eq.iterrows():
            r_km = float(min_r[i]) if min_r is not None else None
            mlgr_v = (
                round(float(mlgr_arr[i]), 2)
                if mlgr_arr is not None and not np.isnan(mlgr_arr[i]) else None
            )
            # mlgr rejimida: hech bir tanlangan quduq mezonidan o'tmagan
            # zilzilalar xaritada ko'rsatilmaydi (eski folium xarita bilan
            # bir xil: min masofa -> maksimal M/lgR, ya'ni "kamida bitta
            # quduq uchun o'tadi" sharti bilan ekvivalent)
            if filter_mode == "mlgr" and selected_coords:
                if mlgr_v is None or mlgr_v < min_mlgr:
                    continue
            map_earthquakes.append({
                "datetime": row["combined_datetime"].strftime("%Y-%m-%dT%H:%M:%S"),
                "lat": float(row["Latitude"]),
                "lon": float(row["Longitude"]),
                "mb": round(float(row["Mb"]), 2),
                "depth": float(row["Depth"]) if pd.notna(row["Depth"]) else None,
                "r_km": r_km,
                "mlgr": mlgr_v,
            })

    selected_wells = {
        (k.split(" | ")[1].strip() if " | " in k else k) for k in selected_keys
    }

    # Juda ko'p zilzila brauzerni sekinlatadi — eng katta 1500 tasi ko'rsatiladi
    earthquakes_truncated = False
    MAX_MAP_EQ = 1500
    if len(map_earthquakes) > MAX_MAP_EQ:
        map_earthquakes.sort(key=lambda e: e["mb"], reverse=True)
        map_earthquakes = map_earthquakes[:MAX_MAP_EQ]
        earthquakes_truncated = True

    return Response({
        "series": series_out,
        "map": {
            "wells": [
                {
                    "name": name,
                    "lat": lat,
                    "lon": lon,
                    "selected": name in selected_wells,
                }
                for name, (lat, lon) in well_coords.items()
            ],
            "earthquakes": map_earthquakes,
        },
        "meta": {
            "min_mag": min_mag,
            "min_mlgr": min_mlgr,
            "filter_mode": filter_mode,
            "median_window": median_window,
            "series_count": len(series_out),
            "earthquakes_truncated": earthquakes_truncated,
        },
    })


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def api_well_info(request):
    """Bitta skvajinaning batafsil ma'lumoti (popup bosilganda yuklanadi).

    Mineralizatsiya rasmi (base64, bir necha MB bo'lishi mumkin) shu sababli
    asosiy tahlil javobiga qo'shilmaydi — faqat kerak bo'lganda olinadi.
    """
    name = (request.GET.get("name") or "").strip()
    if not name:
        return Response({"error": "name parametri kerak"}, status=status.HTTP_400_BAD_REQUEST)
    try:
        info = get_well_detailed_info(name)
        return Response(info)
    except Exception as e:
        logger.error(f"Well info xato ({name}): {e}")
        return Response({"error": "Ma'lumot olinmadi"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
