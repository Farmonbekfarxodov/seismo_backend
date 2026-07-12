"""
app_informativlik uchun JSON API qatlami (React frontend uchun).

Eski informativity_view'ga tegilmagan — bu fayl undagi tayyor funksiyalarni
qayta ishlatadi: calculate_informativity_improved.

MUHIM TUZATISH: eski view'da zilzilalar katalogi hardcoded yo'ldan
o'qilardi (/home/asus/PROJECT/...) — bu yerda settings.BASE_DIR ga
nisbatan olinadi, shuning uchun har qanday kompyuterda ishlaydi.

Endpointlar:
  GET  /informativlik/api/options/  — quduqlar, parametrlar
  POST /informativlik/api/analyze/  — informativlik tahlili (JSON)
  POST /informativlik/api/export/   — natijalar jadvalini Excel qilib qaytarish
"""

import io
import logging

import numpy as np
import pandas as pd
import xlsxwriter
from django.conf import settings
from django.http import HttpResponse
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from sqlalchemy import text

from seismos_app.views import (
    fetch_data,
    destenc_vectorized,
    get_db_engine,
    DEFAULT_ELEMENTS_GROUPS,
)
from .serializers import InformativityRequestSerializer, ExportRequestSerializer
from .views import calculate_informativity_improved

logger = logging.getLogger(__name__)

# Eski hardcoded yo'l o'rniga — loyiha ichidagi nisbiy yo'l
USGS_CATALOG_PATH = settings.BASE_DIR / "static" / "shapefiles" / "USGS catalog.xlsx"


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def api_options(request):
    lst_stansiya, _ = fetch_data()
    all_params = sorted(set(sum(DEFAULT_ELEMENTS_GROUPS.values(), [])))
    return Response({
        "wells": sorted(lst_stansiya.keys()),
        "params": all_params,
    })


def _load_earthquakes(min_mag, start_date, end_date):
    """USGS katalogini o'qib, sana va magnituda bo'yicha filtrlaydi."""
    df = pd.read_excel(USGS_CATALOG_PATH)
    df["Event_date"] = pd.to_datetime(df["Event_date"], errors="coerce")

    mag_cols = [c for c in df.columns if c.lower() in ("mb", "m", "ml")]
    mag_col = mag_cols[0] if mag_cols else "Mb"

    df[mag_col] = pd.to_numeric(df[mag_col], errors="coerce")
    df.dropna(subset=["Event_date", mag_col], inplace=True)

    if start_date is not None:
        df = df[df["Event_date"] >= start_date]
    if end_date is not None:
        df = df[df["Event_date"] <= end_date]
    df = df[df[mag_col] >= min_mag]
    return df, mag_col


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def api_analyze(request):
    """
    Body (JSON):
    {
      "wells": [...], "params": [...],          (majburiy)
      "window_years": 5, "anomaly_duration": 5, "std_factor": 2.0,
      "timedelta_before": 30, "timedelta_after": 10,
      "min_mag": 4.0, "min_mlgr": 2.5,
      "start_date": "YYYY-MM-DD" (ixtiyoriy), "end_date": "..." (ixtiyoriy),
      "median_window": 7 (ixtiyoriy)
    }
    """
    req = InformativityRequestSerializer(data=request.data)
    if not req.is_valid():
        first_error = next(iter(req.errors.values()))
        msg = first_error[0] if isinstance(first_error, list) else str(first_error)
        return Response(
            {"error": str(msg), "details": req.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )
    v = req.validated_data

    selected_keys = v["wells"]
    selected_params = v["params"]
    window_years = v["window_years"]
    anomaly_duration = v["anomaly_duration"]
    std_factor = v["std_factor"]
    timedelta_before = v["timedelta_before"]
    timedelta_after = v["timedelta_after"]
    min_mag = v["min_mag"]
    min_mlgr = v["min_mlgr"]
    median_window = v["median_window"]

    start_date = pd.Timestamp(v["start_date"]) if v["start_date"] else None
    end_date = pd.Timestamp(v["end_date"]) if v["end_date"] else None

    lst_stansiya, well_coords = fetch_data()

    try:
        earthquakes_df, mag_col = _load_earthquakes(min_mag, start_date, end_date)
    except FileNotFoundError:
        return Response(
            {"error": f"USGS katalog fayli topilmadi: {USGS_CATALOG_PATH}"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    if earthquakes_df.empty:
        return Response(
            {"error": "Tanlangan parametrlar bo'yicha zilzilalar topilmadi."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    results_table = []
    series_out = []

    engine = get_db_engine()
    conn = engine.connect()
    try:
        for key in selected_keys:
            _, skvajina = key.split(" | ") if " | " in key else ("", key)
            skvajina = skvajina.strip()
            coords = well_coords.get(skvajina)
            if not coords:
                logger.warning(f"Koordinatalar topilmadi: {skvajina}")
                continue
            lat, lon = coords

            # Shu quduq uchun M/lgR filtri
            eq = earthquakes_df.copy()
            eq["R(km)"] = np.round(
                destenc_vectorized(lat, lon, eq["Latitude"], eq["Longitude"])
            )
            with np.errstate(divide="ignore", invalid="ignore"):
                eq["M/lgR"] = np.where(
                    eq["R(km)"] > 1, eq[mag_col] / np.log10(eq["R(km)"]), np.nan
                )
            eq = eq[eq["M/lgR"] >= min_mlgr]
            if eq.empty:
                continue

            for param in selected_params:
                ssdi_id = lst_stansiya.get(key, {}).get(param)
                if not ssdi_id:
                    continue

                try:
                    data = conn.execute(
                        text(f"SELECT date, `{ssdi_id}` FROM alldata WHERE `{ssdi_id}` IS NOT NULL")
                    ).fetchall()
                except Exception as e:
                    logger.error(f"Query xatosi {key} - {param}: {e}")
                    continue
                if not data:
                    continue

                df = pd.DataFrame(data, columns=["date", "value"])
                df["date"] = pd.to_datetime(df["date"], errors="coerce")
                df.dropna(subset=["date", "value"], inplace=True)
                if start_date is not None:
                    df = df[df["date"] >= start_date]
                if end_date is not None:
                    df = df[df["date"] <= end_date]
                if df.empty:
                    continue

                # 1-qadam: median (eski view bilan aynan bir xil tartib)
                if median_window and median_window > 0:
                    daily = (
                        df.groupby(df["date"].dt.date)["value"]
                        .median().reset_index()
                        .rename(columns={"value": "daily_median"})
                    )
                    daily["rolling_median"] = (
                        daily["daily_median"]
                        .rolling(window=median_window, min_periods=1, center=True)
                        .median()
                    )
                    df = pd.DataFrame({
                        "date": pd.to_datetime(daily["date"]),
                        "value": daily["rolling_median"].values,
                    }).dropna(subset=["value"])
                    if df.empty:
                        continue

                # 2-qadam: kunlik interpolatsiya + informativlik
                indexed = df.set_index("date").asfreq("D")
                indexed = indexed.interpolate(method="time", limit_direction="both")
                indexed.dropna(inplace=True)
                if indexed.empty:
                    continue

                inf = calculate_informativity_improved(
                    indexed["value"], eq, window_years,
                    anomaly_duration, std_factor,
                    timedelta_before, timedelta_after,
                )
                if not inf:
                    continue

                results_table.append({
                    "skvajina": skvajina,
                    "parametr": param,
                    "T": int(inf["T"]),
                    "t": int(inf["t"]),
                    "n": int(inf["n"]),
                    "m": int(inf["m"]),
                    "t_T": round(float(inf["t_T"]), 4),
                    "m_n": round(float(inf["m_n"]), 4),
                    "phi_xi": round(float(inf["phi_xi"]), 4),
                    "q": round(float(inf["q"]), 4),
                    "reliability": inf["reliability"],
                    "informativity": inf["informativity"],
                })

                # Grafik uchun seriya (nuqtalar ko'p bo'lsa siqish)
                dates = indexed.index
                values = indexed["value"]
                if len(values) > 5000:
                    tmp = pd.DataFrame({"date": dates, "value": values.values})
                    weekly = tmp.groupby(pd.Grouper(key="date", freq="W"))["value"].median().dropna()
                    dates, values = weekly.index, weekly

                captured = set(inf.get("captured_earthquakes", []))
                eq_list = [
                    {
                        "date": row["Event_date"].strftime("%Y-%m-%d"),
                        "mb": round(float(row[mag_col]), 2),
                        "captured": idx in captured,
                    }
                    for idx, row in eq.iterrows()
                ]

                series_out.append({
                    "key": key,
                    "skvajina": skvajina,
                    "param": param,
                    "lat": lat,
                    "lon": lon,
                    "q": round(float(inf["q"]), 4),
                    "dates": [d.strftime("%Y-%m-%d") for d in dates],
                    "values": [round(float(v), 6) for v in values],
                    "mean": round(float(np.mean(values)), 6),
                    "sigma": round(float(np.std(values)), 6),
                    "earthquakes": eq_list,
                })
    finally:
        conn.close()

    if not results_table:
        return Response(
            {"error": "Hech qanday natija topilmadi."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    results_table.sort(key=lambda r: r["q"], reverse=True)
    series_out.sort(key=lambda s: s["q"], reverse=True)

    return Response({
        "results": results_table,
        "series": series_out,
        "map": {
            "wells": [
                {"name": name, "lat": la, "lon": lo,
                 "selected": any(name == s["skvajina"] for s in series_out)}
                for name, (la, lo) in well_coords.items()
            ],
        },
        "meta": {
            "window_years": window_years, "std_factor": std_factor,
            "min_mag": min_mag, "min_mlgr": min_mlgr,
        },
    })


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def api_export(request):
    """
    Frontend tahlil natijalarini shu endpointga qaytarib yuboradi,
    javobda tayyor Excel fayl keladi. Session ishlatilmaydi (JWT bilan mos).

    Body: { "results": [ {skvajina, parametr, T, t, n, m, t_T, m_n, phi_xi, q,
                          reliability, informativity}, ... ] }
    """
    req = ExportRequestSerializer(data=request.data)
    if not req.is_valid():
        return Response(
            {"error": "Natijalar yo'q. Avval tahlil o'tkazing.", "details": req.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )
    results = req.validated_data["results"]

    output = io.BytesIO()
    workbook = xlsxwriter.Workbook(output, {"in_memory": True})
    ws = workbook.add_worksheet("Informativlik")

    header = workbook.add_format({
        "bold": True, "bg_color": "#28a745", "font_color": "white",
        "border": 1, "align": "center", "valign": "vcenter",
    })
    cell = workbook.add_format({"border": 1, "align": "center"})
    cell_left = workbook.add_format({"border": 1, "align": "left"})

    headers = [
        "№", "Skvajina", "Parametr", "T (kun)", "t (anom.)", "n (zilz.)",
        "m (tut.)", "t/T", "m/n", "Φ(ξ)", "q", "Ishonchlilik", "Informativlik",
    ]
    ws.set_column("A:A", 5)
    ws.set_column("B:C", 15)
    ws.set_column("D:G", 10)
    ws.set_column("H:K", 12)
    ws.set_column("L:M", 20)

    for col, h in enumerate(headers):
        ws.write(0, col, h, header)

    for row, res in enumerate(results, start=1):
        ws.write(row, 0, row, cell)
        ws.write(row, 1, res.get("skvajina", ""), cell_left)
        ws.write(row, 2, res.get("parametr", ""), cell_left)
        ws.write(row, 3, res.get("T", ""), cell)
        ws.write(row, 4, res.get("t", ""), cell)
        ws.write(row, 5, res.get("n", ""), cell)
        ws.write(row, 6, res.get("m", ""), cell)
        ws.write(row, 7, res.get("t_T", ""), cell)
        ws.write(row, 8, res.get("m_n", ""), cell)
        ws.write(row, 9, res.get("phi_xi", ""), cell)
        ws.write(row, 10, res.get("q", ""), cell)
        ws.write(row, 11, res.get("reliability", ""), cell_left)
        ws.write(row, 12, res.get("informativity", ""), cell_left)

    workbook.close()
    output.seek(0)

    response = HttpResponse(
        output.read(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = 'attachment; filename="informativlik.xlsx"'
    return response
