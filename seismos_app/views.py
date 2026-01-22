import datetime
import os
import json
import logging
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import folium
import geopandas as gpd
import glob
import io
import xlsxwriter
import base64

from datetime import timedelta
from math import pi, sin, cos, atan2, sqrt

from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.shortcuts import render, redirect
from django.core.files.storage import FileSystemStorage
from django.views.decorators.http import require_http_methods
from jinja2.utils import missing
from sqlalchemy import create_engine, text, exc
from plotly.subplots import make_subplots
from folium.plugins import Fullscreen
from scipy.stats import norm, pearsonr, spearmanr
from matplotlib.patches import Patch
from tslearn.metrics import dtw
from scipy.spatial.distance import euclidean
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from decouple import config as env_config

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    filename="seismic_app.log",
    format="%(asctime)s - %(levelname)s - %(message)s",
)

# --- Constants ---
DATE_COLUMN = "Event_date"
TIME_COLUMN = "Event_time"
LATITUDE_COLUMN = "Latitude"
LONGITUDE_COLUMN = "Longitude"

MAIN_MAGNITUDE_COLUMN = "Mb"

DEFAULT_ELEMENTS_GROUPS = {
    "gazli": ["He", "H2", "O2", "N2", "CH4", "CO2"],
    "kimyoviy": ["F", "C2H6", "pH", "Eh", "HCO3", "Cl2"],
    "fizikaviy": ["T0", "Q", "P", "EOCC"],
}

# --- Yangi ranglar palitrasi ---
COLOR_PALETTE = [
    "blue",
    "green",
    "orange",
    "purple",
    "yellow",
    "brown",
    "pink",
    "cyan",
    "lime",
    "teal",
    "gold",
    "navy",
    "magenta",
    "olive",
    "indigo",
    "turquoise",
    "plum",
]


# Database Utilities
def get_db_config():
    try:
        return{
            'db':env_config('DB_NAME'),
            'user':env_config('DB_USER'),
            'psw': env_config('DB_PASSWORD'),
            'ip': env_config('DB_HOST', default='localhost')
        }
    except Exception as e:
        logging.error(f"Database configuration error: {e}")
        logging.error(f"Make sure .env file exists and contains NAME, USER, PASSWORD, HOST")
        raise

def connect_db():
    """Establishes and returns a database engine connection."""
    try:
        config = get_db_config()
        engine = create_engine(
            f"mysql+mysqlconnector://{config['user']}:{config['psw']}@{config['ip']}/{config['db']}"
        )
        logging.info("Successfully created DB engine.")
        return engine
    except Exception as e:
        logging.error(f"DB engine creation error: {e}")
        raise


# --- Data Fetching ---
def fetch_data():
    """
    Fetches station, well, measurement, and coordinates data from the database.
    Returns:
        tuple: (dict of station measurements, dict of well coordinates)
    """
    engine = None
    try:
        engine = connect_db()

        query_izmereniya = (
            "SELECT stansiya, skvajina, izmereniya, ssdi_id FROM all_izmereniya"
        )
        df_izmereniya = pd.read_sql(query_izmereniya, engine)

        lst_stansiya = {}
        for (st, sk), group in df_izmereniya.groupby(["stansiya", "skvajina"]):
            lst_stansiya[f"{st} | {sk}"] = dict(
                zip(group["izmereniya"], group["ssdi_id"])
            )

        coords_query = "SELECT naim, Latitude, Longitude FROM skvajina"
        coords_df = pd.read_sql(coords_query, engine)
        well_coords = {
            row["naim"].strip(): (row["Latitude"], row["Longitude"])
            for _, row in coords_df.iterrows()
        }

        return lst_stansiya, well_coords
    except exc.SQLAlchemyError as e:
        logging.error(f"Database query error in fetch_data: {e}")
        return {}, {}
    except Exception as e:
        logging.error(f"An unexpected error occurred in fetch_data: {e}")
        return {}, {}
    finally:
        if engine:
            engine.dispose()


def get_all_wells_coordinates():
    """
    Barcha skvajinalarning koordinatalarini olish uchun alohida funksiya
    """
    engine = None
    try:
        engine = connect_db()
        coords_query = "SELECT naim, Latitude, Longitude FROM skvajina"
        coords_df = pd.read_sql(coords_query, engine)
        all_wells = {}
        for _, row in coords_df.iterrows():
            well_name = row["naim"].strip()
            if row["Latitude"] is not None and row["Longitude"] is not None:
                all_wells[well_name] = (row["Latitude"], row["Longitude"])
        return all_wells
    except Exception as e:
        logging.error(f"Error fetching all wells coordinates: {e}")
        return {}
    finally:
        if engine:
            engine.dispose()


# --- Utility Functions ---
def destenc_vectorized(lat1, lon1, lat2_series, lon2_series):
    """
    Calculates the Haversine distance in kilometers between a single point
    and a series of points.
    """
    deg_to_rad = pi / 180.0
    d_lat = (lat2_series - lat1) * deg_to_rad
    d_lon = (lon2_series - lon1) * deg_to_rad
    a = (
            np.sin(d_lat / 2) ** 2
            + np.cos(lat1 * deg_to_rad)
            * np.cos(lat2_series * deg_to_rad)
            * np.sin(d_lon / 2) ** 2
    )
    c = 2 * np.arctan2(np.sqrt(a), np.sqrt(1 - a))
    return 6371 * c


def process_dataframe(
        df,
        min_mag,
        min_mlgr,
        well_lat,
        well_lon,
        date_col,
        time_col,
        lat_col,
        lon_col,
        main_mag_col,
        secondary_mag_col,
):
    """
    Processes the earthquake DataFrame to filter by main magnitude, calculate distance
    and M/lgR, and format for plotting.
    Now only considers MAIN_MAGNITUDE_COLUMN for M/lgR calculation and filtering.
    """
    try:
        required_cols = [
            date_col,
            time_col,
            lat_col,
            lon_col,
            main_mag_col,
            secondary_mag_col,
        ]
        if not all(col in df.columns for col in required_cols):
            logging.error(
                f"Missing required columns in Excel file. Expected: {required_cols}"
            )
            return None

        df[main_mag_col] = pd.to_numeric(df[main_mag_col], errors="coerce")
        df[secondary_mag_col] = pd.to_numeric(df[secondary_mag_col], errors="coerce")

        df.dropna(subset=[main_mag_col], inplace=True)
        df = df[df[main_mag_col] >= min_mag].copy()

        df["R(km)"] = np.round(
            destenc_vectorized(well_lat, well_lon, df[lat_col], df[lon_col])
        )
        df["M/lgR"] = np.where(
            df["R(km)"] > 1, df[main_mag_col] / np.log10(df["R(km)"]), np.nan
        )

        df = df[df["M/lgR"] >= min_mlgr].copy()

        rows = []

        df["parsed_date"] = pd.to_datetime(
            df[date_col], format="%d.%m.%Y", errors="coerce"
        )

        df["time_str"] = df[time_col].astype(str)
        df["time_delta"] = pd.to_timedelta(
            df["time_str"].apply(lambda x: x if ":" in x else "00:00:00"),
            errors="coerce",
        )

        df["combined_datetime"] = df["parsed_date"] + df["time_delta"]

        df.sort_values(by=["combined_datetime"], inplace=True)
        df.dropna(subset=["combined_datetime"], inplace=True)

        for _, row_data in df.iterrows():
            current_datetime = row_data["combined_datetime"]

            rows.append(
                [
                    current_datetime.strftime("%d.%m.%Y"),
                    current_datetime.strftime("%H:%M:%S"),
                    row_data[main_mag_col],
                    row_data[secondary_mag_col],
                ]
            )
            rows.append(
                [
                    current_datetime.strftime("%d.%m.%Y"),
                    (current_datetime + timedelta(seconds=1)).strftime("%H:%M:%S"),
                    0,
                    0,
                ]
            )

        result = pd.DataFrame(
            rows, columns=[date_col, time_col, main_mag_col, secondary_mag_col]
        )
        result["datetime_combined"] = pd.to_datetime(
            result[date_col] + " " + result[time_col],
            format="%d.%m.%Y %H:%M:%S",
            errors="coerce",
        )
        result.sort_values(by=["datetime_combined"], inplace=True)
        result.dropna(subset=["datetime_combined"], inplace=True)

        return result
    except KeyError as e:
        logging.error(
            f"Missing expected column in DataFrame: {e}. Check your DATE_COLUMN, TIME_COLUMN, LATITUDE_COLUMN, LONGITUDE_COLUMN, MAIN_MAGNITUDE_COLUMN, and SECONDARY_MAGNITUDE_COLUMN constants."
        )
        return None
    except Exception as e:
        logging.error(f"DataFrame processing error: {e}")
        return None


def generate_colors(n):
    safe_colors = [
        'blue',  # Ko'k
        'green',  # Yashil
        'orange',  # To'q sariq
        'purple',  # Binafsha
        'yellow',  # Sariq
        'brown',  # Jigarrang
        'pink',  # Pushti
        'cyan',  # Moviy-yashil
        'lime',  # Yorqin yashil
        'teal',  # To'q moviy-yashil
        'gold',  # Oltin
        'navy',  # To'q ko'k
        'magenta',  # To'q pushti
        'olive',  # Zaytun yashil
        'indigo',  # Indigo
        'turquoise',  # To'q moviy-yashil
        'plum'  # Pushti-binafsha
    ]

    # Takrorlash agar ko'p ranglar kerak bo'lsa
    while len(safe_colors) < n:
        safe_colors.extend(safe_colors)

    return safe_colors[:n]


def generate_well_colors(well_names):
    """
    Har bir skvajina uchun unique rang va uning soyalarini generatsiya qiladi
    """
    base_colors = [

        '#0000FF',  # Ko'k
        '#FF00FF',  # Magenta
        '#00FFFF',  # Cyan
        '#FFA500',  # Orange
        '#800080',  # Purple
        '#FFD700',  # Gold
        '#FF1493',  # Deep Pink
        '#00CED1',  # Dark Turquoise
        '#FF4500',  # Orange Red
        '#32CD32',  # Lime Green
        '#BA55D3',  # Medium Orchid
        '#20B2AA',  # Light Sea Green

        '#4169E1',  # Royal Blue
        '#DC143C',  # Crimson
        '#7FFF00',  # Chartreuse
        '#FF8C00',  # Dark Orange
        '#9370DB',  # Medium Purple
    ]

    well_color_map = {}

    for idx, well_name in enumerate(well_names):
        # Agar ranglar tugasa, qaytadan boshlash
        base_color = base_colors[idx % len(base_colors)]

        # Asosiy rangdan 3 ta soya yaratish (ochroq -> to'qroq)
        # RGB formatga o'tkazish
        r = int(base_color[1:3], 16)
        g = int(base_color[3:5], 16)
        b = int(base_color[5:7], 16)

        # 3 ta soya: light (70% opacity), medium (85% opacity), dark (100%)
        shades = [
            f'rgba({r},{g},{b},0.9)',  # Eng ochiq (M=5)
            f'rgba({r},{g},{b},0.9)',  # O'rtacha (M=6)
            f'rgba({r},{g},{b},0.9)',  # To'q (M=7)
        ]

        well_color_map[well_name] = {
            'base': base_color,
            'triangle': base_color,  # Uchburchak uchun asosiy rang
            'shades': shades  # Aylanalar uchun soyalar
        }

    return well_color_map


# Custom DTW va normalizatsiya (oldin bor bo'lsa, qo'shmang)
def custom_dtw(x, y):
    x = np.array(x)
    y = np.array(y)
    n, m = len(x), len(y)
    dt = np.full((n+1, m+1), np.inf)
    dt[0, 0] = 0
    for i in range(1, n+1):
        for j in range(1, m+1):
            cost = abs(x[i-1] - y[j-1])
            dt[i, j] = cost + min(dt[i-1, j], dt[i, j-1], dt[i-1, j-1])
    return dt[-1, -1]

def normalize_series(series):
    series = np.array(series, dtype=float)
    min_val = np.min(series)
    max_val = np.max(series)
    if max_val - min_val == 0:
        return np.zeros_like(series)
    return (series - min_val) / (max_val - min_val)

def calculate_pattern_similarity(reference, candidate):
    ref_norm = normalize_series(reference)
    cand_norm = normalize_series(candidate)
    results = {'dtw_score': 0, 'pearson_score': 0, 'spearman_score': 0, 'combined_score': 0}
    try:
        dtw_distance = custom_dtw(ref_norm, cand_norm)
        max_possible_distance = len(ref_norm)
        dtw_score = max(0, 100 * (1 - dtw_distance / max_possible_distance))
        results['dtw_score'] = round(dtw_score, 2)
    except:
        pass
    try:
        pearson_corr, _ = pearsonr(ref_norm, cand_norm)
        results['pearson_score'] = round((pearson_corr + 1) * 50, 2)
    except:
        pass
    try:
        spearman_corr, _ = spearmanr(ref_norm, cand_norm)
        results['spearman_score'] = round((spearman_corr + 1) * 50, 2)
    except:
        pass
    results['combined_score'] = round(
        (results['dtw_score'] + results['pearson_score'] + results['spearman_score']) / 3, 2
    )
    return results

# Yangi: Anomaliya segmentlarida o'xshashlik topish
def find_similar_anomalies(anomaly_segments, min_similarity=70):
    if len(anomaly_segments) < 2:
        return []  # Hech bo'lmaganda 2 ta segment kerak
    similar_pairs = []
    reference = anomaly_segments[0]['values']  # Birinchi anomaliya reference
    for seg in anomaly_segments[1:]:
        similarity = calculate_pattern_similarity(reference, seg['values'])
        if similarity['combined_score'] >= min_similarity:
            similar_pairs.append({
                'start_date': seg['start_date'],
                'end_date': seg['end_date'],
                'similarity': similarity
            })
    similar_pairs.sort(key=lambda x: x['similarity']['combined_score'], reverse=True)
    return similar_pairs


def plot_data_with_anomalies(
        fig,
        x_val,
        y_val,
        mean,
        sigma,
        btn_value,
        row_idx,
        col_idx,
        trace_color,
        element_name,
        key_name,
        min_similarity=70,  # Yangi parametr: minimal o'xshashlik foizi
        min_anomaly_length=5,  # Minimal anomaliya segment uzunligi (nuqta soni)
        highlight_similar=True,  # O'xshash anomaliyalarni belgilashni yoqish/o'chirish
):
    """
    Ma'lumotlarning butun chizig'ini chizadi va anomaliya qismlarini qizil rangda belgilaydi.
    Qo'shimcha: Anomaliya segmentlari ichidan o'xshashlarini topib, yashil rangda belgilaydi.

    Yangi parametrlar:
        min_similarity: O'xshash deb hisoblash uchun minimal foiz (default 70%)
        min_anomaly_length: Minimal anomaliya segment uzunligi (nuqta soni)
        highlight_similar: O'xshash anomaliyalarni grafikda ko'rsatish (True/False)
    """
    if isinstance(x_val, pd.Series):
        x_val = x_val.tolist()
    if isinstance(y_val, pd.Series):
        y_val = y_val.tolist()

    if len(x_val) != len(y_val):
        logging.error(f"x_val va y_val uzunliklari mos emas: {len(x_val)} vs {len(y_val)}")
        return [mean]

    # NaN qiymatlarni filtrlash va tartiblash
    valid_mask = ~pd.isna(y_val) & ~pd.isna(x_val)
    x_val = [x for i, x in enumerate(x_val) if valid_mask[i]]
    y_val = [y for i, y in enumerate(y_val) if valid_mask[i]]

    if len(x_val) == 0:
        logging.warning(f"{key_name} - {element_name} uchun valid ma'lumot yo'q")
        return [mean]

    # x_val ni tartiblash (vaqt bo'yicha)
    sorted_indices = np.argsort(x_val)
    x_val = [x_val[i] for i in sorted_indices]
    y_val = [y_val[i] for i in sorted_indices]

    # Chegaralarni hisoblash
    upper_bound = mean + btn_value * sigma
    lower_bound = mean - btn_value * sigma

    y_all_values = [y for y in y_val if not np.isnan(y)]
    y_all_values.extend([upper_bound, lower_bound, mean])

    yaxis_index = (row_idx - 1) * 1 + col_idx
    yref = "y" if yaxis_index == 1 else f"y{2 * row_idx - 1}"

    # UB, MEAN va LB chiziqlarini chizish
    fig.add_shape(type="line", x0=min(x_val), x1=max(x_val), y0=upper_bound, y1=upper_bound,
                  line=dict(color="green", width=1.5), row=row_idx, col=col_idx, yref=yref, xref="x")
    fig.add_annotation(x=max(x_val), y=upper_bound, text=f"UB ({btn_value}σ)", showarrow=False,
                       font=dict(color="green", size=10), xanchor="right", yanchor="bottom",
                       row=row_idx, col=col_idx)

    fig.add_shape(type="line", x0=min(x_val), x1=max(x_val), y0=mean, y1=mean,
                  line=dict(color="magenta", width=1.5), row=row_idx, col=col_idx, yref=yref, xref="x")
    fig.add_annotation(x=max(x_val), y=mean, text="Mean", showarrow=False,
                       font=dict(color="magenta", size=10), xanchor="right", yanchor="bottom",
                       row=row_idx, col=col_idx)

    fig.add_shape(type="line", x0=min(x_val), x1=max(x_val), y0=lower_bound, y1=lower_bound,
                  line=dict(color="blue", width=1.5), row=row_idx, col=col_idx, yref=yref, xref="x")
    fig.add_annotation(x=max(x_val), y=lower_bound, text=f"LB ({-btn_value}σ)", showarrow=False,
                       font=dict(color="blue", size=10), xanchor="right", yanchor="top",
                       row=row_idx, col=col_idx)

    # Asosiy grafik
    fig.add_trace(
        go.Scatter(
            x=x_val, y=y_val, mode="lines",
            line=dict(color=trace_color, width=1.5),
            name=f"{element_name} ({key_name})",
            showlegend=True,
            hoverinfo="x+y",
            hovertemplate=f"Vaqt: %{{x|%d-%m-%Y}}<br>{element_name} Qiymati: %{{y}}<extra></extra>",
            connectgaps=False
        ),
        row=row_idx, col=col_idx, secondary_y=False
    )

    # ==================== ANOMALIYA SEGMENTLARINI YIG'ISH ====================
    anomaly_segments = []  # Yangi: barcha anomaliya segmentlarini saqlash
    current_anomalous_segment_x = []
    current_anomalous_segment_y = []
    is_anomalous_prev = False

    for i in range(len(x_val)):
        x_curr, y_curr = x_val[i], y_val[i]
        is_anomalous_curr = (y_curr > upper_bound) or (y_curr < lower_bound)

        if i == 0:
            if is_anomalous_curr:
                current_anomalous_segment_x.append(x_curr)
                current_anomalous_segment_y.append(y_curr)
            is_anomalous_prev = is_anomalous_curr
            continue

        x_prev, y_prev = x_val[i - 1], y_val[i - 1]

        intersect_x = None
        intersect_y = None

        # Upper bound bilan kesishish
        if (y_prev < upper_bound <= y_curr) or (y_curr < upper_bound <= y_prev):
            if abs(y_curr - y_prev) > 1e-9:
                ratio = (upper_bound - y_prev) / (y_curr - y_prev)
                if 0 <= ratio <= 1:
                    intersect_x = x_prev + (x_curr - x_prev) * ratio
                    intersect_y = upper_bound

        # Lower bound bilan kesishish
        if (y_prev > lower_bound >= y_curr) or (y_curr > lower_bound >= y_prev):
            if abs(y_curr - y_prev) > 1e-9:
                ratio = (lower_bound - y_prev) / (y_curr - y_prev)
                new_intersect_x = x_prev + (x_curr - x_prev) * ratio
                new_intersect_y = lower_bound
                if intersect_x is None and 0 <= ratio <= 1:
                    intersect_x = new_intersect_x
                    intersect_y = new_intersect_y
                elif intersect_x and abs((new_intersect_x - x_prev).total_seconds()) < abs(
                        (intersect_x - x_prev).total_seconds()) and 0 <= ratio <= 1:
                    intersect_x = new_intersect_x
                    intersect_y = new_intersect_y

        # Anomaliya o'tishini tekshirish
        if is_anomalous_curr != is_anomalous_prev:
            if is_anomalous_prev and len(current_anomalous_segment_x) > 1:
                if intersect_x is not None:
                    current_anomalous_segment_x.append(intersect_x)
                    current_anomalous_segment_y.append(intersect_y)

                # Anomaliya segmentini saqlash (faqat yetarli uzunlikdagi)
                if len(current_anomalous_segment_x) >= min_anomaly_length:
                    anomaly_segments.append({
                        'start_date': current_anomalous_segment_x[0],
                        'end_date': current_anomalous_segment_x[-1],
                        'values': current_anomalous_segment_y[:]
                    })

                fig.add_trace(
                    go.Scatter(
                        x=current_anomalous_segment_x,
                        y=current_anomalous_segment_y,
                        mode="lines",
                        line=dict(color="red", width=3),
                        showlegend=False,
                        hoverinfo="x+y",
                        hovertemplate="Vaqt: %{x|%d-%m-%Y}<br>Anomaliya: %{y}<extra></extra>",
                        connectgaps=False
                    ),
                    row=row_idx, col=col_idx, secondary_y=False
                )
                current_anomalous_segment_x = []
                current_anomalous_segment_y = []

            if is_anomalous_curr:
                if intersect_x is not None:
                    current_anomalous_segment_x.append(intersect_x)
                    current_anomalous_segment_y.append(intersect_y)
                current_anomalous_segment_x.append(x_curr)
                current_anomalous_segment_y.append(y_curr)

        elif is_anomalous_curr:
            current_anomalous_segment_x.append(x_curr)
            current_anomalous_segment_y.append(y_curr)

        is_anomalous_prev = is_anomalous_curr

    # Oxirgi segmentni yopish va saqlash
    if is_anomalous_prev and len(current_anomalous_segment_x) > 1:
        if len(current_anomalous_segment_x) >= min_anomaly_length:
            anomaly_segments.append({
                'start_date': current_anomalous_segment_x[0],
                'end_date': current_anomalous_segment_x[-1],
                'values': current_anomalous_segment_y[:]
            })
        fig.add_trace(
            go.Scatter(
                x=current_anomalous_segment_x,
                y=current_anomalous_segment_y,
                mode="lines",
                line=dict(color="red", width=3),
                showlegend=False,
                hoverinfo="x+y",
                hovertemplate="Vaqt: %{x|%d-%m-%Y}<br>Anomaliya: %{y}<extra></extra>",
                connectgaps=False
            ),
            row=row_idx, col=col_idx, secondary_y=False
        )

    # ==================== ANOMALIYA SEGMENTLARI ICHIDA O'XSHASHLIKNI TOPISH ====================
    if highlight_similar and len(anomaly_segments) >= 2:
        try:
            # Birinchi anomaliya - reference sifatida olinadi
            reference_values = anomaly_segments[0]['values']

            for seg in anomaly_segments[1:]:
                similarity = calculate_pattern_similarity(reference_values, seg['values'])
                if similarity['combined_score'] >= min_similarity:
                    # O'xshash anomaliyani yashil shaffof to'rtburchak bilan belgilash
                    fig.add_vrect(
                        x0=seg['start_date'],
                        x1=seg['end_date'],
                        fillcolor="green",
                        opacity=0.25,
                        line_width=0,
                        annotation_text=f"{similarity['combined_score']}%",
                        annotation_position="top left",
                        annotation=dict(font_size=9, font_color="darkgreen", bgcolor="rgba(255,255,255,0.7)"),
                        row=row_idx,
                        col=col_idx,
                        yref=yref,
                        xref="x"
                    )
        except Exception as e:
            logging.warning(f"Anomaliya o'xshashlik hisobida xato: {e}")

    return y_all_values


@csrf_exempt  # AJAX uchun vaqtincha, keyin token bilan xavfsiz qilish mumkin
def set_reference_segment(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            segment = data.get('segment')

            if not segment:
                return JsonResponse({'status': 'error', 'message': 'Segment ma\'lumotlari yo\'q'}, status=400)

            # Sessionda saqlash
            request.session['selected_reference_segment'] = {
                'index': segment.get('index'),
                'start': segment.get('start'),
                'end': segment.get('end'),
                # Agar values ham yuborilgan bo'lsa qo'shish mumkin
            }
            request.session.modified = True

            return JsonResponse({
                'status': 'success',
                'message': 'Reference segment tanlandi',
                'selected': segment
            })

        except json.JSONDecodeError:
            return JsonResponse({'status': 'error', 'message': 'JSON format xatosi'}, status=400)
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)}, status=500)

    return JsonResponse({'status': 'error', 'message': 'Faqat POST so\'rov qabul qilinadi'}, status=405)


def draw_magnitude_values(fig, original_df, row_index, col_index=1, min_mag=4,
                          well_lat=0, well_lon=0, min_mlgr=0, filter_mode='mlgr'):
    """
    Grafikda zilzilalarni chizish

    Parameters:
    -----------
    filter_mode : str
        'mlgr' - M/lgR bo'yicha filtrlash (default)
        'mb' - Faqat Mb bo'yicha filtrlash
    """
    if original_df is None or original_df.empty:
        logging.info(f"draw_magnitude_values: original_df is empty for row {row_index}")
        return [0, 1]

    df = original_df.copy()

    # Vaqt ma'lumotlarini tayyorlash
    df["combined_datetime"] = pd.to_datetime(
        df[DATE_COLUMN].astype(str) + " " + df[TIME_COLUMN].astype(str),
        format="mixed",
        errors="coerce"
    )
    df.dropna(subset=["combined_datetime"], inplace=True)

    # Magnituda ma'lumotlarini tayyorlash
    df[MAIN_MAGNITUDE_COLUMN] = pd.to_numeric(df[MAIN_MAGNITUDE_COLUMN], errors="coerce")
    df.dropna(subset=[MAIN_MAGNITUDE_COLUMN], inplace=True)

    # ✅ ASOSIY O'ZGARISH 1: Filter rejimiga qarab turli filtrlash
    if filter_mode == 'mb':
        # ============================================================
        # REJIM 1: FAQAT MAGNITUDA BO'YICHA (Masofa hisoblanmaydi)
        # ============================================================
        logging.info(f"[MB REJIMI] Faqat Mb >= {min_mag} bo'yicha filtrlash")

        valid_earthquakes = df[
            (df[MAIN_MAGNITUDE_COLUMN] >= min_mag)
        ].copy()

        logging.info(f"[MB REJIMI] Filtrlangan zilzilalar: {len(valid_earthquakes)} ta")

    else:
        # ============================================================
        # REJIM 2: M/lgR BO'YICHA (Masofa + Magnituda)
        # ============================================================
        logging.info(f"[M/lgR REJIMI] Mb >= {min_mag} va M/lgR >= {min_mlgr}")

        # Masofani hisoblash (faqat M/lgR rejimida kerak)
        df["R(km)"] = np.round(
            destenc_vectorized(well_lat, well_lon, df[LATITUDE_COLUMN], df[LONGITUDE_COLUMN])
        )

        # M/lgR ni xavfsiz hisoblash
        with np.errstate(divide='ignore', invalid='ignore'):
            df["M/lgR"] = np.where(
                df["R(km)"] > 1,
                df[MAIN_MAGNITUDE_COLUMN] / np.log10(df["R(km)"]),
                np.nan
            )

        # Filtrlash: Mb va M/lgR bo'yicha
        valid_earthquakes = df[
            (df[MAIN_MAGNITUDE_COLUMN] >= min_mag) &
            (df["M/lgR"] >= min_mlgr) &
            (df["M/lgR"].notna())
            ].copy()

        logging.info(f"[M/lgR REJIMI] Filtrlangan zilzilalar: {len(valid_earthquakes)} ta")

    # Agar hech qanday zilzila topilmasa
    if valid_earthquakes.empty:
        logging.info(f"draw_magnitude_values: No valid earthquakes for row {row_index}")
        return [0, 1]

    # Y o'qi diapazoni
    max_mag_for_y_axis = valid_earthquakes[MAIN_MAGNITUDE_COLUMN].max() * 1.1
    min_mag_for_y_axis = 0

    fig.update_yaxes(
        range=[min_mag_for_y_axis, max_mag_for_y_axis],
        secondary_y=True,
        title_text="Magnituda (Mb)",
        row=row_index,
        col=col_index,
    )

    # ✅ ASOSIY O'ZGARISH 2: Hover text rejimga qarab
    stem_x = []
    stem_y = []
    hover_texts = []

    for _, row in valid_earthquakes.iterrows():
        lat = row.get(LATITUDE_COLUMN, None)
        lon = row.get(LONGITUDE_COLUMN, None)
        mag_val = row.get(MAIN_MAGNITUDE_COLUMN, None)

        # Sanani formatlash
        date_val = row.get(DATE_COLUMN, "Noma'lum")
        try:
            date_val = pd.to_datetime(date_val).strftime("%d.%m.%Y")
        except:
            date_val = "Noma'lum"

        depth_val = row.get("Depth", "Noma'lum")

        # ✅ HOVER TEXT: Rejimga qarab turli ma'lumot
        if filter_mode == 'mb':
            # Faqat Mb rejimida - masofani ko'rsatmaslik
            hover_text = f"""
                <b>Zilzila</b><br>
                Sana: {date_val}<br>
                Magnituda (Mb): {mag_val:.2f}<br>
                Chuqurlik: {depth_val} km<br>
                <i>Masofa hisoblanmagan (Faqat Mb rejimi)</i>
            """
        else:
            # M/lgR rejimida - barcha ma'lumotlar
            distance_val = row.get("R(km)", "Noma'lum")
            mlgr_val = row.get("M/lgR", "Noma'lum")

            hover_text = f"""
                <b>Zilzila</b><br>
                Sana: {date_val}<br>
                Magnituda (Mb): {mag_val:.2f}<br>
                Chuqurlik: {depth_val} km<br>
                Masofa: {distance_val:.1f} km<br>
                M/lgR: {mlgr_val:.2f}
            """

        if mag_val is not None and not np.isnan(mag_val) and mag_val > 0:
            stem_x.extend([row["combined_datetime"], row["combined_datetime"], None])
            stem_y.extend([0, mag_val, None])
            hover_texts.extend(["", hover_text, ""])

    # Stem plot chizish
    if stem_x:
        # ✅ LEGEND: Rejimga qarab turli nom
        if filter_mode == 'mb':
            legend_name = f"{MAIN_MAGNITUDE_COLUMN} Magnituda (≥{min_mag}) - Faqat Mb"
        else:
            legend_name = f"{MAIN_MAGNITUDE_COLUMN} Magnituda (≥{min_mag}, M/lgR≥{min_mlgr})"

        fig.add_trace(
            go.Scatter(
                x=stem_x,
                y=stem_y,
                mode="lines",
                line=dict(color="navy", width=2),
                name=legend_name,
                hoverinfo="text",
                text=hover_texts,
                showlegend=True,
                legendgroup="magnitudes_mb",
                yaxis=f"y{2 * row_index}",
            ),
            row=row_index,
            col=col_index,
            secondary_y=True,
        )

    # Grid sozlamalari
    fig.update_xaxes(
        matches=f'x{row_index}',
        row=row_index,
        col=col_index,
    )
    fig.update_yaxes(
        showgrid=True,
        gridwidth=0.15,
        gridcolor="black",
        griddash="dot",
        row=row_index,
        col=col_index,
        secondary_y=False,
    )
    fig.update_yaxes(
        showgrid=True,
        gridwidth=0.15,
        gridcolor="gray",
        griddash="dot",
        row=row_index,
        col=col_index,
        secondary_y=True,
    )

    return [min_mag_for_y_axis, max_mag_for_y_axis]

def distance_haversine(lat1, lon1, lat2, lon2):
    """
    Haversine formulasi yordamida ikki geografik nuqta orasidagi masofani (km) hisoblaydi.
    """
    degree_to_rad = pi / 180.0
    d_lat = (lat2 - lat1) * degree_to_rad
    d_lon = (lon2 - lon1) * degree_to_rad
    a = pow(sin(d_lat / 2), 2) + cos(lat1 * degree_to_rad) * cos(
        lat2 * degree_to_rad
    ) * pow(sin(d_lon / 2), 2)
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    d = 6371 * c
    return d

def get_all_shapefiles():
    shapefile_paths = []

    search_paths = [
        os.path.join(settings.BASE_DIR, 'static', 'shapefiles', 'AFEAD_*.shp'),
        os.path.join(settings.BASE_DIR, 'media', 'shapefiles', 'AFEAD_*.shp'),
        os.path.join(settings.BASE_DIR, 'data', 'AFEAD_*.shp'),
        os.path.join(settings.BASE_DIR, 'shapefiles', 'AFEAD_*.shp'),
        os.path.join(settings.BASE_DIR, 'static', 'data', 'AFEAD_*.shp'),
        os.path.join(settings.BASE_DIR, 'AFEAD_*.shp'),
        os.path.join(settings.BASE_DIR, 'Export_Output*.shp'),
        os.path.join(settings.BASE_DIR, 'Seysmogen_zonalar*.shp'),
    ]

    if hasattr(settings, 'CRACKS_SHAPEFILES_DIR'):
        search_paths.append(os.path.join(settings.CRACKS_SHAPEFILES_DIR, '*.shp'))

    for search_path in search_paths:
        found_files = glob.glob(search_path)
        shapefile_paths.extend(found_files)

        # dublikatlarni olib tashlash
        shapefile_paths = list(set(shapefile_paths))

        logging.info(f"Topilgan shapefilelar: {len(shapefile_paths)} ta")
        for path in shapefile_paths:
            logging.info(f" -{os.path.basename(path)}")

        return shapefile_paths


def get_seismogenic_shapefiles():
    """
    Seysmogen zonalar uchun shapefilelarni qidirish
    """
    shapefile_paths = []

    search_paths = [
        # Seysmogen zonalar uchun
        os.path.join(settings.BASE_DIR, 'static', 'shapefiles', 'Seysmogen_*.shp'),
        os.path.join(settings.BASE_DIR, 'media', 'shapefiles', 'Seysmogen_*.shp'),
        os.path.join(settings.BASE_DIR, 'data', 'Seysmogen_*.shp'),
        os.path.join(settings.BASE_DIR, 'shapefiles', 'Seysmogen_*.shp'),
        os.path.join(settings.BASE_DIR, 'static', 'data', 'Seysmogen_*.shp'),
        os.path.join(settings.BASE_DIR, 'Seysmogen_*.shp'),

        # Export formatidagi fayllar
        os.path.join(settings.BASE_DIR, 'Export_*.shp'),
    ]

    if hasattr(settings, 'CRACKS_SHAPEFILES_DIR'):
        search_paths.append(os.path.join(settings.CRACKS_SHAPEFILES_DIR, '*.shp'))

    for search_path in search_paths:
        found_files = glob.glob(search_path)
        shapefile_paths.extend(found_files)

    # Dublikatlarni olib tashlash
    shapefile_paths = list(set(shapefile_paths))

    logging.info(f"Topilgan seysmogen zonalar shapefilelari: {len(shapefile_paths)} ta")
    for path in shapefile_paths:
        logging.info(f" - {os.path.basename(path)}")

    return shapefile_paths

def load_seismogenic_zones():
    """
    Seysmogen zonalar shapefilelarini yuklash
    """
    shapefile_paths = get_seismogenic_shapefiles()  # Mavjud funksiyadan foydalanish

    if not shapefile_paths:
        logging.warning("Hech qanday shapefile topilmadi")
        return None

    all_zones = []

    for shapefile_path in shapefile_paths:
        try:
            # Faqat Seysmogen va Export fayllarini yuklash
            filename = os.path.basename(shapefile_path)
            if 'SEYSMOGEN' not in filename.upper() and 'SEISMOGEN' not in filename.upper() and 'EXPORT' not in filename.upper():
                continue

            gdf = gpd.read_file(shapefile_path)

            if gdf.crs is None:
                gdf.set_crs('EPSG:4326', inplace=True)
            elif gdf.crs.to_string() != 'EPSG:4326':
                gdf = gdf.to_crs('EPSG:4326')

            # Geometry tekshiruvi
            if gdf.empty or 'geometry' not in gdf.columns or gdf.geometry.isnull().all():
                logging.warning(f"{filename} faylida geometriya mavjud emas yoki bo‘sh")
                continue

            gdf['source_file'] = filename
            all_zones.append(gdf)

            logging.info(f"Seysmogen zona yuklandi: {filename} - {len(gdf)} ta")

        except Exception as e:
            logging.error(f"Shapefile {shapefile_path} yuklashda xato: {e}")
            continue

    if not all_zones:
        logging.warning("Hech qanday seysmogen zona yuklanmadi")
        return None

    try:
        combined_gdf = gpd.GeoDataFrame(pd.concat(all_zones, ignore_index=True))
        logging.info(f"Umumiy seysmogen zonalar: {len(combined_gdf)} ta")
        return combined_gdf
    except Exception as e:
        logging.error(f"Seysmogen zonalarni birlashtirishda xato: {e}")
        return None


def add_seismogenic_zones_to_map(folium_map, zones_gdf):
    """
    Folium xaritasiga seysmogen zonalarni pushti rangda qo'shadi va
    har bir zona markaziga rim raqamini joylashtiradi.
    LayerControl orqali yoqish/o'chirish imkoniyati bilan.
    """

    if zones_gdf is None or zones_gdf.empty:
        logging.warning("Seysmogen zonalar ma'lumotlari bo'sh")
        return {}

    pink_color = "#FFC0CB"
    legend_data = {"Seysmogen zonalar": pink_color}

    # Rim raqamlariga o'zgartirish funksiyasi
    def to_roman(num):
        """Butun sonni rim raqamiga o'zgartiradi"""
        val = [1000, 900, 500, 400, 100, 90, 50, 40, 10, 9, 5, 4, 1]
        syms = ['M', 'CM', 'D', 'CD', 'C', 'XC', 'L', 'XL', 'X', 'IX', 'V', 'IV', 'I']
        roman_num = ''
        i = 0
        while num > 0:
            for _ in range(num // val[i]):
                roman_num += syms[i]
                num -= val[i]
            i += 1
        return roman_num

    # FeatureGroup yaratish
    seismogenic_layer = folium.FeatureGroup(name='Seysmogen zonalar', show=True)

    try:
        for idx, row in zones_gdf.iterrows():
            geometry = row.geometry
            if geometry is None:
                continue

            # Zona raqamini aniqlash (OBJECTID dan)
            zone_number = None
            if 'OBJECTID' in row.index and pd.notnull(row['OBJECTID']):
                try:
                    zone_number = int(row['OBJECTID'])
                except:
                    zone_number = idx + 1
            else:
                zone_number = idx + 1

            # Rim raqamiga o'zgartirish
            roman_number = to_roman(zone_number)

            # Zona nomini aniqlash
            zone_name = f"Zona {roman_number}"  # Default qiymat
            if 'seysmogen_' in row.index and pd.notnull(row['seysmogen_']):
                zone_name = str(row['seysmogen_'])

            # Popup matni
            popup_text = f"""
            <div style='width: 250px; font-family: Arial; font-size: 12px;'>
                <h4 style='color: #2c3e50; margin-bottom: 8px;'>Seysmogen Zona {roman_number}</h4>
                <table style='width: 100%; border-collapse: collapse;'>
                    <tr style='background-color: #f8f9fa;'>
                        <td style='padding: 5px; border: 1px solid #dee2e6; font-weight: bold;'>Zona:</td>
                        <td style='padding: 5px; border: 1px solid #dee2e6;'>{zone_name}</td>
                    </tr>
                </table>
            </div>
            """

            tooltip_text = roman_number

            # Geometriya markazini hisoblash
            centroid = geometry.centroid
            centroid_coords = [centroid.y, centroid.x]

            # --- Geometriyani chizish (FeatureGroup ga qo'shish) ---
            if geometry.geom_type == 'Polygon':
                coords = [[y, x] for x, y in geometry.exterior.coords]
                folium.Polygon(
                    locations=coords,
                    color=pink_color,
                    fill=True,
                    fillColor=pink_color,
                    fillOpacity=0.5,
                    weight=2,
                    popup=folium.Popup(popup_text, max_width=300),
                    tooltip=tooltip_text
                ).add_to(seismogenic_layer)  # folium_map emas, seismogenic_layer ga

            elif geometry.geom_type == 'MultiPolygon':
                for poly in geometry.geoms:
                    coords = [[y, x] for x, y in poly.exterior.coords]
                    folium.Polygon(
                        locations=coords,
                        color=pink_color,
                        fill=True,
                        fillColor=pink_color,
                        fillOpacity=0.5,
                        weight=2,
                        popup=folium.Popup(popup_text, max_width=300),
                        tooltip=tooltip_text
                    ).add_to(seismogenic_layer)

            elif geometry.geom_type == 'LineString':
                coords = [[y, x] for x, y in geometry.coords]
                folium.PolyLine(
                    coords,
                    color=pink_color,
                    weight=3,
                    opacity=0.8,
                    popup=folium.Popup(popup_text, max_width=300),
                    tooltip=tooltip_text
                ).add_to(seismogenic_layer)

            elif geometry.geom_type == 'MultiLineString':
                for line in geometry.geoms:
                    coords = [[y, x] for x, y in line.coords]
                    folium.PolyLine(
                        coords,
                        color=pink_color,
                        weight=3,
                        opacity=0.8,
                        popup=folium.Popup(popup_text, max_width=300),
                        tooltip=tooltip_text
                    ).add_to(seismogenic_layer)

            elif geometry.geom_type == 'GeometryCollection':
                for geom in geometry.geoms:
                    if geom.geom_type == 'Polygon':
                        coords = [[y, x] for x, y in geom.exterior.coords]
                        folium.Polygon(
                            locations=coords,
                            color=pink_color,
                            fill=True,
                            fillColor=pink_color,
                            fillOpacity=0.5,
                            weight=2,
                            popup=folium.Popup(popup_text, max_width=300),
                            tooltip=tooltip_text
                        ).add_to(seismogenic_layer)

                    elif geom.geom_type == 'LineString':
                        coords = [[y, x] for x, y in geom.coords]
                        folium.PolyLine(
                            coords,
                            color=pink_color,
                            weight=3,
                            opacity=0.8,
                            popup=folium.Popup(popup_text, max_width=300),
                            tooltip=tooltip_text
                        ).add_to(seismogenic_layer)

            else:
                logging.warning(f"Tasdiqlanmagan geometriya turi: {geometry.geom_type}")

            # --- Rim raqamini xaritaga qo'shish ---
            folium.Marker(
                location=centroid_coords,
                icon=folium.DivIcon(html=f"""
                    <div style="
                        font-size: 16px;
                        font-weight: bold;
                        color: #8B008B;
                        text-shadow: 
                            -1px -1px 0 white,
                            1px -1px 0 white,
                            -1px 1px 0 white,
                            1px 1px 0 white,
                            0 0 3px white;
                        font-family: 'Times New Roman', serif;
                        pointer-events: none;
                    ">{roman_number}</div>
                """)
            ).add_to(seismogenic_layer)

        # FeatureGroup ni xaritaga qo'shish
        seismogenic_layer.add_to(folium_map)

        return legend_data

    except Exception as e:
        logging.error(f"Seysmogen zonalarni xaritaga qo'shishda xato: {e}")
        return {}

def load_all_cracks_shapefiles():
    shapefile_paths = get_all_shapefiles()

    if not shapefile_paths:
        logging.warning("Hech qanday shapefile topilmadi")
        return None

    all_cracks = []

    for shapefile_path in shapefile_paths:
        try:
            gdf = gpd.read_file(shapefile_path)

            if gdf.crs and gdf.crs != 'EPSG:4326':
                gdf = gdf.to_crs('EPSG:4326')

            # Fayl nomini qo'shish
            gdf['source_file'] = os.path.basename(shapefile_path)

            all_cracks.append(gdf)
            logging.info(f"Yuklandi:{os.path.basename(shapefile_path)}-{len(gdf)} ta yoriq")

        except Exception as e:
            logging.error(f"Shapefile {shapefile_path}:{e}")
            continue

    if not all_cracks:
        logging.warning("Hech qanday shapefile yuklanmadi")
        return None

    # Barcha malumotlarni  birlashtirish
    try:
        combined_gdf = gpd.GeoDataFrame(pd.concat(all_cracks, ignore_index=True))
        logging.info(f"Umumiy yoriqlar soni: {len(combined_gdf)}")
        return combined_gdf
    except Exception as e:
        logging.error(f"Shapefilelarni birlashtirishda xato:{e}")
        return None


def add_cracks_to_map(folium_map, cracks_gdf):
    """
    Folium xaritasiga yer yoriqlarini qo'shadi
    (RATE bo'yicha qalinlik va rang to'qligi, CONF bo'yicha rang tanlanadi)
    """
    if cracks_gdf is None or cracks_gdf.empty:
        return

    # CONF bo'yicha asosiy ranglar
    conf_colors = {
        "A": (255, 0, 0),  # qizil
        "B": (200, 0, 0),  # qizil (biroz farqli bo'lishi mumkin)
        "C": (255, 165, 0),  # sariq/oranj
        "D": (128, 128, 128),  # kulrang
    }

    # RATE bo'yicha qalinlik va alpha
    rate_styles = {
        "1": {"weight": 6, "alpha": 1.0},  # eng qalin, to‘q
        "2": {"weight": 4, "alpha": 0.8},  # o‘rtacha
        "3": {"weight": 2, "alpha": 0.6},  # eng yupqa, eng och
    }

    default_color = "blue"
    default_weight = 3

    try:
        for idx, row in cracks_gdf.iterrows():
            geometry = row.geometry

            # RATE va CONF qiymatlarini olish
            rate = str(row.get("RATE", "")).strip()
            conf = str(row.get("CONF", "")).strip().upper()

            # Rang va qalinlikni tanlash
            if conf in conf_colors and rate in rate_styles:
                base_rgb = conf_colors[conf]
                style = rate_styles[rate]

                # RGBA -> hex rang (shade)
                r, g, b = base_rgb
                alpha = style["alpha"]
                color = f"rgba({r},{g},{b},{alpha})"
                weight = style["weight"]
            else:
                color = default_color
                weight = default_weight

            # Popup va tooltip matn
            key = f"{rate}_{conf}" if rate and conf else "Nomalum"
            popup_text = f"<b>Yer Yorig'i</b><br><b>Kategoriya:</b> {key}<br>"
            for col in cracks_gdf.columns:
                if col != "geometry":
                    val = row[col]
                    if val is not None and str(val).strip():
                        popup_text += f"<b>{col}:</b> {val}<br>"

            tooltip_text = f"Yer Yorig'i ({key})"

            # Geometriya chizish
            if geometry.geom_type == "LineString":
                coords = [[y, x] for x, y in geometry.coords]
                folium.PolyLine(
                    coords, color=color, weight=weight, opacity=1.0,
                    popup=popup_text, tooltip=tooltip_text
                ).add_to(folium_map)

            elif geometry.geom_type == "MultiLineString":
                for line in geometry.geoms:
                    coords = [[y, x] for x, y in line.coords]
                    folium.PolyLine(
                        coords, color=color, weight=weight, opacity=1.0,
                        popup=popup_text, tooltip=tooltip_text
                    ).add_to(folium_map)

            elif geometry.geom_type == "Point":
                folium.CircleMarker(
                    location=[geometry.y, geometry.x],
                    color=color, fill=True, fillColor=color,
                    fillOpacity=0.9, radius=weight,
                    popup=popup_text, tooltip=tooltip_text
                ).add_to(folium_map)

        # Legend qaytarish
        legend_data = {
            "A-B (RATE 1–3)": "qizil (qalinlik darajaga qarab)",
            "C (RATE 1–3)": "sariq/oranj (qalinlik darajaga qarab)",
            "D (RATE 1–3)": "kulrang (qalinlik darajaga qarab)",
        }
        return legend_data

    except Exception as e:
        import logging
        logging.error(f"Yer yoriqlarini xaritaga qo'shishda xato: {e}")
        return {}


def get_well_detailed_info(well_name):
    """
    Bitta quduq haqida batafsil ma'lumot olish (mineralizatsiya rasmi bilan)
    """
    engine = None
    default_info = {
        "nomi": well_name.strip(),
        "quduq_turi": "Ma'lumot yo'q",
        "suv_qatlami": "Ma'lumot yo'q",
        "chuqurlik": "Ma'lumot yo'q",
        "seysmotektonik_holat": "Ma'lumot yo'q",
        "strategrafik_taqsimoti": "Ma'lumot yo'q",
        "litologik_tarkibi": "Ma'lumot yo'q",
        "mineralizatsiya_base64": None,
    }

    try:
        engine = connect_db()

        query = """
                SELECT nomi, 
                       quduq_turi, 
                       suv_qatlami, 
                       chuqurlik, 
                       seysmotektonik_holat, 
                       strategrafik_taqsimoti, 
                       litologik_tarkibi,
                       mineralizatsiya
                FROM malumot1
                WHERE nomi = %s
                """

        df = pd.read_sql(query, engine, params=(well_name.strip(),))

        if not df.empty:
            row = df.iloc[0]

            result = {
                "nomi": row.get("nomi", "Ma'lumot yo'q"),
                "quduq_turi": row.get("quduq_turi", "Ma'lumot yo'q"),
                "suv_qatlami": row.get("suv_qatlami", "Ma'lumot yo'q"),
                "chuqurlik": row.get("chuqurlik", "Ma'lumot yo'q"),
                "seysmotektonik_holat": row.get("seysmotektonik_holat", "Ma'lumot yo'q"),
                "strategrafik_taqsimoti": row.get("strategrafik_taqsimoti", "Ma'lumot yo'q"),
                "litologik_tarkibi": row.get("litologik_tarkibi", "Ma'lumot yo'q"),
                "mineralizatsiya_base64": None,
            }

            # MINERALIZATSIYA rasmini qayta ishlash
            mineralizatsiya = row.get("mineralizatsiya")

            if mineralizatsiya is not None and mineralizatsiya != "Ma'lumot yo'q":
                try:
                    # Variant 1: Agar fayl yo'li (string) bo'lsa
                    if isinstance(mineralizatsiya, str) and os.path.exists(mineralizatsiya):
                        with open(mineralizatsiya, 'rb') as img_file:
                            encoded = base64.b64encode(img_file.read()).decode('utf-8')
                            ext = os.path.splitext(mineralizatsiya)[1].lower()

                            if ext in ['.jpg', '.jpeg']:
                                mime_type = 'image/jpeg'
                            elif ext == '.png':
                                mime_type = 'image/png'
                            elif ext == '.gif':
                                mime_type = 'image/gif'
                            else:
                                mime_type = 'image/png'

                            result["mineralizatsiya_base64"] = f"data:{mime_type};base64,{encoded}"

                    # Variant 2: Agar binary data (bytes/BLOB) bo'lsa
                    elif isinstance(mineralizatsiya, bytes):
                        encoded = base64.b64encode(mineralizatsiya).decode('utf-8')
                        # Odatda PNG yoki JPEG deb faraz qilamiz
                        result["mineralizatsiya_base64"] = f"data:image/png;base64,{encoded}"

                except Exception as img_error:
                    logging.error(f"Mineralizatsiya rasmini yuklashda xato ({well_name}): {img_error}")
                    result["mineralizatsiya_base64"] = None

            return result
        else:
            logging.warning(f"Skvajina ma'lumotlari topilmadi ({well_name}).")
            return default_info

    except Exception as e:
        logging.error(f"Skvajina ma'lumotlarini yuklashda xatolik ({well_name}): {e}")
        return default_info

    finally:
        if engine:
            engine.dispose()


def add_map_data_folium(selected_keys, well_coords, earthquake_data, min_mag, min_mlgr, filter_mode='mlgr'):
    """
    Folium yordamida interaktiv xarita yaratadi
    HAR BIR TANLANGAN SKVAJINA O'Z RANGIDA VA AYLANMALARI HAM O'SHA RANGDA
    """
    all_wells = get_all_wells_coordinates()

    selected_well_names = set()
    selected_well_names_list = []  # Rang uchun tartiblangan ro'yxat
    filtered_earthquakes_by_well = []

    # ============================================================
    # 1-QISM: ZILZILALARNI FILTRLASH
    # ============================================================
    for key in selected_keys:
        _, skvajina = key.split(" | ")
        selected_well_names.add(skvajina)
        selected_well_names_list.append(skvajina)

        lat, lon = well_coords.get(skvajina, (None, None))
        if lat is not None and lon is not None:
            df = earthquake_data.copy()
            logging.info(f"\n{'=' * 50}")
            logging.info(f"Skvajina: {skvajina}")
            logging.info(f"Skvajina koordinatalari: Lat={lat}, Lon={lon}")
            logging.info(f"Jami zilzilalar (boshida): {len(df)}")
            logging.info(f"Zilzila koordinatalari (birinchi 5 ta):")
            logging.info(df[[LATITUDE_COLUMN, LONGITUDE_COLUMN, MAIN_MAGNITUDE_COLUMN]].head())

            df[MAIN_MAGNITUDE_COLUMN] = pd.to_numeric(df[MAIN_MAGNITUDE_COLUMN], errors="coerce")
            df.dropna(subset=[MAIN_MAGNITUDE_COLUMN], inplace=True)

            logging.info(f"Mb tozalashdan keyin: {len(df)} ta")

            # Masofani hisoblash
            df["R(km)"] = np.round(
                destenc_vectorized(lat, lon, df[LATITUDE_COLUMN], df[LONGITUDE_COLUMN])
            )

            # ← BU DEBUG KODNI QO'SHING:
            logging.info(f"Masofalar (R km) statistikasi:")
            logging.info(f"  Min: {df['R(km)'].min():.1f} km")
            logging.info(f"  Max: {df['R(km)'].max():.1f} km")
            logging.info(f"  O'rtacha: {df['R(km)'].mean():.1f} km")
            logging.info(f"Birinchi 10 ta zilzilaning masofalari:")
            logging.info(df[['R(km)', MAIN_MAGNITUDE_COLUMN, LATITUDE_COLUMN, LONGITUDE_COLUMN]].head(10))
            # M/lgR ni xavfsiz hisoblash (faqat R > 1 km bo'lganda)
            # np.errstate yordamida divide by zero warningni o'chirish
            with np.errstate(divide='ignore', invalid='ignore'):
                df["M/lgR"] = np.where(
                    df["R(km)"] > 1,
                    df[MAIN_MAGNITUDE_COLUMN] / np.log10(df["R(km)"]),
                    np.nan
                )
            # ← BU DEBUG KODNI QO'SHING:
            logging.info(f"M/lgR statistikasi:")
            logging.info(f"  Min: {df['M/lgR'].min():.2f}")
            logging.info(f"  Max: {df['M/lgR'].max():.2f}")
            logging.info(f"  O'rtacha: {df['M/lgR'].mean():.2f}")
            logging.info(f"  M/lgR >= {min_mlgr} bo'lganlar: {(df['M/lgR'] >= min_mlgr).sum()} ta")

            if filter_mode == 'mb':
                valid_earthquakes = df[
                    (df[MAIN_MAGNITUDE_COLUMN] >= min_mag)
                ].copy()
                logging.info(f"Filtrlash rejimi:Faqat Mb >= {min_mag}")
            else:
                df["R(km)"] = np.round(
                    destenc_vectorized(lat, lon, df[LATITUDE_COLUMN], df[LONGITUDE_COLUMN])
                )

                with np.errstate(divide='ignore', invalid='ignore'):
                    df["M/lgR"] = np.where(
                        df["R(km)"] > 1,
                        df[MAIN_MAGNITUDE_COLUMN] / np.log10(df["R(km)"]),
                        np.nan
                    )

                    # Filtrlash
                valid_earthquakes = df[
                    (df[MAIN_MAGNITUDE_COLUMN] >= min_mag) &
                    (df["M/lgR"] >= min_mlgr) &
                    (df["M/lgR"].notna())
                    ].copy()

            if not valid_earthquakes.empty:
                valid_earthquakes['skvajina'] = skvajina
                filtered_earthquakes_by_well.append(valid_earthquakes)

    if filtered_earthquakes_by_well:
        all_filtered_earthquakes = pd.concat(filtered_earthquakes_by_well, ignore_index=True)
        all_filtered_earthquakes = all_filtered_earthquakes.drop_duplicates(
            subset=[LATITUDE_COLUMN, LONGITUDE_COLUMN, DATE_COLUMN, TIME_COLUMN]
        )
    else:
        all_filtered_earthquakes = pd.DataFrame()

    # ============================================================
    # 2-QISM: HAR BIR SKVAJINA UCHUN RANG GENERATSIYA QILISH
    # ============================================================
    well_color_map = generate_well_colors(selected_well_names_list)

    # ============================================================
    # 3-QISM: XARITA MARKAZINI ANIQLASH
    # ============================================================
    if selected_keys:
        selected_lats = [
            well_coords[key.split(" | ")[1]][0]
            for key in selected_keys
            if well_coords.get(key.split(" | ")[1])
        ]
        selected_lons = [
            well_coords[key.split(" | ")[1]][1]
            for key in selected_keys
            if well_coords.get(key.split(" | ")[1])
        ]
        center_lat = np.mean(selected_lats) if selected_lats else 41.2995
        center_lon = np.mean(selected_lons) if selected_lons else 69.2401
    elif all_wells:
        all_lats = [coord[0] for coord in all_wells.values()]
        all_lons = [coord[1] for coord in all_wells.values()]
        center_lat = np.mean(all_lats)
        center_lon = np.mean(all_lons)
    else:
        center_lat, center_lon = 41.2995, 69.2401

    # ============================================================
    # 4-QISM: ASOSIY XARITA YARATISH
    # ============================================================
    m = folium.Map(
        location=[center_lat, center_lon],
        zoom_start=8,
        tiles="OpenStreetMap",
        attr="© OpenStreetMap contributors"
    )

    Fullscreen(
        position='topleft',
        title='To\'liq ekran',
        title_cancel='Chiqish',
        force_separate_button=True
    ).add_to(m)

    # ============================================================
    # 5-QISM: FON XARITALARI QO'SHISH
    # ============================================================
    try:
        folium.TileLayer(
            tiles='https://stamen-tiles.a.ssl.fastly.net/terrain/{z}/{x}/{y}.png',
            attr='Map tiles by <a href="http://stamen.com">Stamen Design</a>',
            name='Terrain'
        ).add_to(m)
    except:
        logging.warning("Terrain tiles yuklanmadi")

    try:
        folium.TileLayer(
            tiles='https://cartodb-basemaps-{s}.global.ssl.fastly.net/light_all/{z}/{x}/{y}.png',
            attr='© OpenStreetMap contributors © CARTO',
            name='Light Map'
        ).add_to(m)
    except:
        logging.warning("CartoDB tiles yuklanmadi")

    try:
        folium.TileLayer(
            tiles='https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
            attr='Tiles © Esri',
            name='Satellite',
            overlay=False,
            control=True
        ).add_to(m)
    except:
        logging.warning("Satellite tiles yuklanmadi")

    # ============================================================
    # 6-QISM: YER YORIQLARINI QO'SHISH
    # ============================================================
    logging.info("Yer yoriqlarini yuklash boshlandi...")
    all_cracks = load_all_cracks_shapefiles()
    if all_cracks is not None:
        add_cracks_to_map(m, all_cracks)
        logging.info(f"Yer yoriqlari xaritaga qo'shildi: {len(all_cracks)} ta")
    else:
        logging.warning("Yer yoriqlari yuklanmadi")

    # ============================================================
    # 7-QISM: SEYSMOGEN ZONALARNI QO'SHISH
    # ============================================================
    logging.info("Seysmogen zonalarni yuklash boshlandi...")
    seismogenic_zones = load_seismogenic_zones()
    if seismogenic_zones is not None:
        add_seismogenic_zones_to_map(m, seismogenic_zones)
        logging.info(f"Seysmogen zonalar xaritaga qo'shildi: {len(seismogenic_zones)} ta")
    else:
        logging.warning("Seysmogen zonalar yuklanmadi")

    # ============================================================
    # 8-QISM: TANLANMAGAN SKVAJINALARNI QO'SHISH (LIGHTBLUE)
    # ============================================================
    for well_name, (lat, lon) in all_wells.items():
        if well_name not in selected_well_names:
            well_info = get_well_detailed_info(well_name)

            mineralizatsiya_html = ""
            if well_info.get('mineralizatsiya_base64'):
                mineralizatsiya_html = f"""
                    <tr>
                        <td colspan="2" style="padding: 10px; border: 1px solid #dee2e6; text-align: center;">
                            <b>Mineralizatsiya:</b><br>
                            <img src="{well_info['mineralizatsiya_base64']}" 
                                 style="max-width: 420px; max-height: 300px; margin-top: 5px; border-radius: 5px;" 
                                 alt="Mineralizatsiya rasmi"/>
                        </td>
                    </tr>
                """

            tooltip_html = f"""
                <div style="width: 450px; font-family: Arial; font-size: 12px;">
                    <h4 style="color: #2c3e50; margin-bottom: 10px;">Skvajina ma'lumotlari</h4>
                    <table style="width: 100%; border-collapse: collapse;">
                        <tr style="background-color: #f8f9fa;">
                            <td style="padding: 5px; border: 1px solid #dee2e6; font-weight: bold;">Nomi:</td>
                            <td style="padding: 5px; border: 1px solid #dee2e6;">{well_info.get('nomi', 'Ma\'lumot yo\'q')}</td>
                        </tr>
                        <tr>
                            <td style="padding: 5px; border: 1px solid #dee2e6; font-weight: bold;">Quduq turi:</td>
                            <td style="padding: 5px; border: 1px solid #dee2e6;">{well_info.get('quduq_turi', 'Ma\'lumot yo\'q')}</td>
                        </tr>
                        <tr>
                            <td style="padding: 5px; border: 1px solid #dee2e6; font-weight: bold;">Chuqurlik:</td>
                            <td style="padding: 5px; border: 1px solid #dee2e6;">{well_info.get('chuqurlik', 'Ma\'lumot yo\'q')} m</td>
                        </tr>
                        <tr style="background-color: #f8f9fa;">
                            <td style="padding: 5px; border: 1px solid #dee2e6; font-weight: bold;">Seysmotektonik holat:</td>
                            <td style="padding: 5px; border: 1px solid #dee2e6;">{well_info.get('seysmotektonik_holat', 'Ma\'lumot yo\'q')}</td>
                        </tr>
                        <tr style="background-color: #f8f9fa;">
                            <td style="padding: 5px; border: 1px solid #dee2e6; font-weight: bold;">Strategrafik taqsimoti:</td>
                            <td style="padding: 5px; border: 1px solid #dee2e6;">{well_info.get('strategrafik_taqsimoti', 'Ma\'lumot yo\'q')}</td>
                        </tr>
                        <tr style="background-color: #f8f9fa;">
                            <td style="padding: 5px; border: 1px solid #dee2e6; font-weight: bold;">Litologik tarkibi:</td>
                            <td style="padding: 5px; border: 1px solid #dee2e6;">{well_info.get('litologik_tarkibi', 'Ma\'lumot yo\'q')}</td>
                        </tr>
                        {mineralizatsiya_html}
                    </table>
                    <p style="margin-top: 10px; color: #6c757d; font-style: italic;">Tanlanmagan skvajina</p>
                </div>
            """

            triangle_icon = folium.DivIcon(
                html='<div style="width: 0; height: 0; border-left: 10px solid transparent; border-right: 10px solid transparent; border-bottom: 20px solid lightblue;"></div>',
                icon_size=(20, 20),
                icon_anchor=(10, 20)
            )

            folium.Marker(
                location=[lat, lon],
                tooltip=folium.Tooltip(tooltip_html, sticky=True),
                icon=triangle_icon,
            ).add_to(m)
            # ✅ YANGI: Mb rejimida tanlanmagan skvajinalar uchun ham aylanalar
            if filter_mode == 'mb':
                try:
                    mlgr_val = 2.5

                    # Lightblue uchun soyalar (tanlanmagan skvajinalar)
                    lightblue_shades = [
                        'rgba(173, 216, 230, 0.9)',  # Ochiq
                        'rgba(173, 216, 230, 0.9)',  # O'rtacha
                        'rgba(173, 216, 230, 0.9)',  # To'q
                    ]

                    radii_data = [
                        (5, lightblue_shades[0]),
                        (6, lightblue_shades[1]),
                        (7, lightblue_shades[2]),
                    ]

                    circles_info = []
                    for M_value, color in radii_data:
                        R_km = float(10 ** (M_value / mlgr_val))
                        circles_info.append({
                            'radius': R_km * 1000,
                            'color': color,
                            'M': M_value,
                            'R_km': R_km,
                            'mlgr': mlgr_val
                        })

                    js_code = f"""
                    <script>
                    (function() {{
                        var wellLat = {lat};
                        var wellLon = {lon};
                        var wellName = "{well_name}";
                        var circlesInfo = {circles_info};
                        var circlesLayer = null;
                        var isVisible = false;  // TANLANMAGAN uchun default yashirin

                        document.addEventListener("DOMContentLoaded", function() {{
                            var map = window.map || Object.values(window).find(v => v instanceof L.Map);
                            if (!map) {{
                                console.error("Xarita topilmadi");
                                return;
                            }}

                            circlesLayer = L.layerGroup();
                            circlesInfo.forEach(function(info) {{
                                var circle = L.circle([wellLat, wellLon], {{
                                    radius: info.radius,
                                    color: info.color,
                                    weight: 2,
                                    fill: false,
                                    opacity: 0.7
                                }});

                                circle.bindTooltip(
                                    "M=" + info.M + ", R=" + info.R_km.toFixed(1) + " km (M/lgR=" + info.mlgr + ")",
                                    {{permanent: false, direction: 'top'}}
                                );

                                circlesLayer.addLayer(circle);
                            }});

                            //Tanlanmagan uchun darhol qo'shilmaydi (isVisible = false)

                            map.eachLayer(function(layer) {{
                                if (layer instanceof L.Marker) {{
                                    var latlng = layer.getLatLng();
                                    if (Math.abs(latlng.lat - wellLat) < 0.0001 && 
                                        Math.abs(latlng.lng - wellLon) < 0.0001) {{

                                        layer.on('click', function(e) {{
                                            L.DomEvent.stopPropagation(e);

                                            if (isVisible) {{
                                                map.removeLayer(circlesLayer);
                                                isVisible = false;
                                            }} else {{
                                                circlesLayer.addTo(map);
                                                isVisible = true;
                                            }}
                                        }});
                                    }}
                                }}
                            }});
                        }});
                    }})();
                    </script>
                    """
                    m.get_root().html.add_child(folium.Element(js_code))

                except Exception as e:
                    logging.error(f"Tanlanmagan skvajina uchun aylanalar qo'shishda xato ({well_name}): {e}")
    # ============================================================
    # 9-QISM: TANLANGAN SKVAJINALARNI O'Z RANGIDA QO'SHISH
    # ============================================================
    for key in selected_keys:
        _, skvajina = key.split(" | ")
        lat, lon = well_coords.get(skvajina, (None, None))

        if lat is not None and lon is not None:
            # BU SKVAJINA UCHUN RANGNI OLISH
            colors = well_color_map.get(skvajina, {
                'base': 'blue',
                'triangle': 'blue',
                'shades': ['rgba(0,0,255,0.5)', 'rgba(0,0,255,0.7)', 'rgba(0,0,255,0.9)']
            })

            well_info = get_well_detailed_info(skvajina)

            mineralizatsiya_html = ""
            if well_info.get('mineralizatsiya_base64'):
                mineralizatsiya_html = f"""
                    <tr>
                        <td colspan="2" style="padding: 10px; border: 1px solid #dee2e6; text-align: center;">
                            <b>Mineralizatsiya:</b><br>
                            <img src="{well_info['mineralizatsiya_base64']}" 
                                 style="max-width: 420px; max-height: 300px; margin-top: 5px; border-radius: 5px;" 
                                 alt="Mineralizatsiya rasmi"/>
                        </td>
                    </tr>
                """

            # TOOLTIP RANGINI O'ZGARTIRISH
            tooltip_html = f"""
                <div style="width: 450px; font-family: Arial; font-size: 12px;">
                    <h4 style="color: {colors['base']}; margin-bottom: 10px;">Tanlangan skvajina</h4>
                    <table style="width: 100%; border-collapse: collapse;">
                        <tr style="background-color: #f8f9fa;">
                            <td style="padding: 5px; border: 1px solid #dee2e6; font-weight: bold;">Nomi:</td>
                            <td style="padding: 5px; border: 1px solid #dee2e6;">{well_info.get('nomi', 'Ma\'lumot yo\'q')}</td>
                        </tr>
                        <tr>
                            <td style="padding: 5px; border: 1px solid #dee2e6; font-weight: bold;">Quduq turi:</td>
                            <td style="padding: 5px; border: 1px solid #dee2e6;">{well_info.get('quduq_turi', 'Ma\'lumot yo\'q')}</td>
                        </tr>
                        <tr>
                            <td style="padding: 5px; border: 1px solid #dee2e6; font-weight: bold;">Chuqurlik:</td>
                            <td style="padding: 5px; border: 1px solid #dee2e6;">{well_info.get('chuqurlik', 'Ma\'lumot yo\'q')} m</td>
                        </tr>
                        <tr style="background-color: #f8f9fa;">
                            <td style="padding: 5px; border: 1px solid #dee2e6; font-weight: bold;">Seysmotektonik holat:</td>
                            <td style="padding: 5px; border: 1px solid #dee2e6;">{well_info.get('seysmotektonik_holat', 'Ma\'lumot yo\'q')}</td>
                        </tr>
                        <tr style="background-color: #f8f9fa;">
                            <td style="padding: 5px; border: 1px solid #dee2e6; font-weight: bold;">Strategrafik taqsimoti:</td>
                            <td style="padding: 5px; border: 1px solid #dee2e6;">{well_info.get('strategrafik_taqsimoti', 'Ma\'lumot yo\'q')}</td>
                        </tr>
                        <tr style="background-color: #f8f9fa;">
                            <td style="padding: 5px; border: 1px solid #dee2e6; font-weight: bold;">Litologik tarkibi:</td>
                            <td style="padding: 5px; border: 1px solid #dee2e6;">{well_info.get('litologik_tarkibi', 'Ma\'lumot yo\'q')}</td>
                        </tr>
                        {mineralizatsiya_html}
                    </table>
                    <p style="margin-top: 10px; color: {colors['base']}; font-weight: bold;">✓ Tanlangan skvajina</p>
                </div>
            """

            # UCHBURCHAK MARKERINI RANGINI O'ZGARTIRISH
            triangle_icon = folium.DivIcon(
                html=f'<div style="width: 0; height: 0; border-left: 10px solid transparent; border-right: 10px solid transparent; border-bottom: 20px solid {colors["triangle"]};"></div>',
                icon_size=(20, 20),
                icon_anchor=(10, 20)
            )

            folium.Marker(
                location=[lat, lon],
                tooltip=folium.Tooltip(tooltip_html, sticky=True),
                icon=triangle_icon,
            ).add_to(m)

            # ============================================================
            # AYLANALARNI O'SHA RANGDA QO'SHISH
            # ============================================================
            try:
                if filter_mode == "mb":
                    mlgr_val = 2.5
                else:
                    mlgr_val = min_mlgr if min_mlgr > 0 else 0.5

                # HAR BIR AYLANA UCHUN O'SHA SKVAJINANING RANGIDAN FOYDALANISH
                radii_data = [
                    (5, colors['shades'][0]),  # Ochiq soya
                    (6, colors['shades'][1]),  # O'rtacha soya
                    (7, colors['shades'][2]),  # To'q soya
                ]

                circles_info = []
                for M_value, color in radii_data:
                    R_km = float(10 ** (M_value / mlgr_val))
                    circles_info.append({
                        'radius': R_km * 1000,
                        'color': color,
                        'M': M_value,
                        'R_km': R_km,
                        'mlgr': mlgr_val
                    })
                initial_visibility = "true"

                js_code = f"""
                <script>
                (function() {{
                    var wellLat = {lat};
                    var wellLon = {lon};
                    var wellName = "{skvajina}";
                    var circlesInfo = {circles_info};
                    var circlesLayer = null;
                    var isVisible = {initial_visibility};

                    document.addEventListener("DOMContentLoaded", function() {{
                        var map = window.map || Object.values(window).find(v => v instanceof L.Map);
                        if (!map) {{
                            console.error("Xarita topilmadi");
                            return;
                        }}

                        circlesLayer = L.layerGroup();
                        circlesInfo.forEach(function(info) {{
                            var circle = L.circle([wellLat, wellLon], {{
                                radius: info.radius,
                                color: info.color,
                                weight: 2,
                                fill: false,
                                opacity: 0.7
                            }});

                            circle.bindTooltip(
                                "M=" + info.M + ", R=" + info.R_km.toFixed(1) + " km (M/lgR=" + info.mlgr + ")",
                                {{permanent: false, direction: 'top'}}
                            );

                            circlesLayer.addLayer(circle);
                        }});

                        //Mb rejimida aylanalar darhol ko'rinadi
                        if (isVisible) {{
                            circlesLayer.addTo(map);
                        }}

                        map.eachLayer(function(layer) {{
                            if (layer instanceof L.Marker) {{
                                var latlng = layer.getLatLng();
                                if (Math.abs(latlng.lat - wellLat) < 0.0001 && 
                                    Math.abs(latlng.lng - wellLon) < 0.0001) {{

                                    layer.on('click', function(e) {{
                                        L.DomEvent.stopPropagation(e);

                                        if (isVisible) {{
                                            map.removeLayer(circlesLayer);
                                            isVisible = false;
                                        }} else {{
                                            circlesLayer.addTo(map);
                                            isVisible = true;
                                        }}
                                    }});
                                }}
                            }}
                        }});
                    }});
                }})();

                </script>
                """
                m.get_root().html.add_child(folium.Element(js_code))

            except Exception as e:
                logging.error(f"Radius JavaScript kodini qo'shishda xato ({skvajina}): {e}")

    # ============================================================
    # 10-QISM: ZILZILALARNI QO'SHISH
    # ============================================================
    if not all_filtered_earthquakes.empty:
        for idx, row in all_filtered_earthquakes.iterrows():
            lat = row.get(LATITUDE_COLUMN, None)
            lon = row.get(LONGITUDE_COLUMN, None)
            mag_val = row.get(MAIN_MAGNITUDE_COLUMN, None)
            date_val = row.get(DATE_COLUMN, "Nomalum")
            try:
                date_val = pd.to_datetime(date_val).strftime("%d.%m.%Y")
            except Exception:
                date_val = "Nomalum"
            if isinstance(date_val, (pd.Timestamp, datetime.datetime)):
                date_val = date_val.strftime("%d.%m.%Y")
            distance_val = row.get("R(km)", "Nomalum")
            mlgr_val = row.get("M/lgR", "Nomalum")
            depth_val = row.get("Depth", "Nomalum")

            if mag_val is not None and not np.isnan(mag_val) and mag_val > 0 and lat is not None and lon is not None:
                if filter_mode == 'mb':
                    tooltip_html = f"""
                        <b>Zilzila</b><br>
                        Sana:{date_val}<br>
                        Magnituda (Mb):{mag_val:.2f}<br>
                        Chuqurlik (km): {depth_val}<br>
                         <i>Faqat Mb rejimi (masofa hisoblanmagan)</i>
                """
                    if mag_val >= 2.8:
                        color = "red"
                        radius = mag_val * 2.5

                    elif mag_val >= 2.0:
                        color = "orange"
                        radius = mag_val * 2

                    else:
                        color = "yellow"
                        radius = mag_val * 1.5
                else:

                    tooltip_html = f"""
                        <b>Zilzila</b><br>
                        Sana: {date_val}<br>
                        Magnituda (Mb): {mag_val:.2f}<br>
                        Chuqurlik (km): {depth_val}<br>
                        Masofa (km): {distance_val:.1f}<br>
                        M/lgR: {mlgr_val:.2f}<br>
                    """

                    if mag_val >= 6:
                        color = "darkred"
                        radius = mag_val * 3
                    elif mag_val >= 5:
                        color = "red"
                        radius = mag_val * 2.5
                    elif mag_val >= 4:
                        color = "orange"
                        radius = mag_val * 2
                    else:
                        color = "yellow"
                        radius = mag_val * 1.5

                folium.CircleMarker(
                    location=[lat, lon],
                    radius=radius,
                    color=color,
                    fill=True,
                    fill_color=color,
                    fill_opacity=0.7,
                    stroke=True,
                    weight=2,
                    tooltip=tooltip_html
                ).add_to(m)

    # ============================================================
    # 11-QISM: LEGEND QO'SHISH
    # ============================================================
    if filter_mode == 'mb':
        legend_items = [
            '<p><b>Xarita elementlari:</b></p>',
            '<p><i class="fa fa-circle" style="color:red"></i> Zilzila Mb > 2.8</p>',
            '<p><i class="fa fa-circle" style="color:orange"></i> Zilzila Mb 2.0-2.8</p>',
            '<p><i class="fa fa-circle" style="color:yellow"></i> Zilzila Mb < 2.0</p>',
            '<p><div style="width: 0; height: 0; border-left: 8px solid transparent; border-right: 8px solid transparent; border-bottom: 15px solid #ADD8E6; display: inline-block; margin-right: 8px;"></div> Tanlanmagan skvajinalar</p>',
            '<p><div style="width: 0; height: 0; border-left: 8px solid transparent; border-right: 8px solid transparent; border-bottom: 15px solid #0B43FA; display: inline-block; margin-right: 8px;"></div> Tanlangan skvajinalar (har xil rangda)</p>',
            '<p style="font-style:italic; color:#666;">Filtrlash: Faqat Mb (masofa hisobsiz)</p>',
        ]
    else:
        legend_items = [
            '<p><b>Xarita elementlari:</b></p>',
            '<p><i class="fa fa-circle" style="color:darkred"></i> Zilzila Mb ≥ 6.0</p>',
            '<p><i class="fa fa-circle" style="color:red"></i> Zilzila Mb 5.0-5.9</p>',
            '<p><i class="fa fa-circle" style="color:orange"></i> Zilzila Mb 4.0-4.9</p>',
            '<p><i class="fa fa-circle" style="color:yellow"></i> Zilzila Mb < 4.0</p>',
            '<p><div style="width: 0; height: 0; border-left: 8px solid transparent; border-right: 8px solid transparent; border-bottom: 15px solid #ADD8E6; display: inline-block; margin-right: 8px;"></div> Tanlanmagan skvajinalar</p>',
            '<p><div style="width: 0; height: 0; border-left: 8px solid transparent; border-right: 8px solid transparent; border-bottom: 15px solid #0B43FA; display: inline-block; margin-right: 8px;"></div> Tanlangan skvajinalar (har xil rangda)</p>',

        ]

    legend_html = f'''
    <div style="position: fixed; 
                bottom: 50px; left: 50px; width: 160px; height: 180px; 
                background-color: white; border:2px solid grey; z-index:9999; 
                font-size:12px; padding: 10px; overflow-y: auto;">
    {''.join(legend_items)}
    </div>
    '''
    m.get_root().html.add_child(folium.Element(legend_html))

    folium.LayerControl().add_to(m)

    return m._repr_html_()


def earthquake_map_view(request):
    """
    Zilzilalar xaritasi - skvajinalarni tanlash va filtrlash bilan
    """
    # Barcha skvajinalar ro'yxatini olish
    all_wells = get_all_wells_coordinates()

    context = {
        'page_title': 'Zilzilalar Xaritasi',
        'error': None,
        'success': None,
        'map_html': None,
        'all_wells': sorted(all_wells.keys()) if all_wells else [],
        'current_min_mag': 4.0,
        'current_start_date': '',
        'current_end_date': '',
        'uploaded_file_info': None,
        'selected_wells': [],
    }

    if request.method == 'POST':
        try:
            # 1. PARAMETRLARNI OLISH
            min_mag = float(request.POST.get('min_mag', 4.0))
            filter_start_date = request.POST.get('start_date', '').strip()
            filter_end_date = request.POST.get('end_date', '').strip()

            # Tanlangan skvajinalar (dropdown dan)
            selected_wells = request.POST.getlist('selected_wells')

            if not selected_wells:
                context['error'] = 'Iltimos, kamida bitta skvajina tanlang'
                return render(request, 'seismos_app/map_only.html', context)

            # 2. FAYLNI YUKLASH
            uploaded_file = request.FILES.get('earthquake_file')

            if not uploaded_file:
                context['error'] = 'Iltimos, zilzilalar katalog faylini yuklang'
                context['selected_wells'] = selected_wells
                return render(request, 'seismos_app/map_only.html', context)

            # Fayl kengaytmasini tekshirish
            file_ext = os.path.splitext(uploaded_file.name)[1].lower()
            if file_ext not in ['.xlsx', '.xls', '.csv']:
                context['error'] = 'Faqat Excel (.xlsx, .xls) yoki CSV (.csv) fayllarini yuklash mumkin'
                context['selected_wells'] = selected_wells
                return render(request, 'seismos_app/map_only.html', context)

            # Faylni saqlash
            fs = FileSystemStorage()
            filename = fs.save(uploaded_file.name, uploaded_file)
            file_path = fs.path(filename)

            # 3. FAYLNI O'QISH
            try:
                if file_ext == '.csv':
                    df_earthquakes = pd.read_csv(file_path, encoding='utf-8')
                else:
                    df_earthquakes = pd.read_excel(file_path)

                logging.info(f"Fayl yuklandi: {uploaded_file.name}, Qatorlar: {len(df_earthquakes)}")

            except Exception as e:
                context['error'] = f'Faylni o\'qishda xato: {e}'
                context['selected_wells'] = selected_wells
                return render(request, 'seismos_app/map_only.html', context)

            # 4. USTUNLARNI TEKSHIRISH
            required_columns = ['Event_date', 'Latitude', 'Longitude', 'Mb']
            missing_cols = [col for col in required_columns if col not in df_earthquakes.columns]

            if missing_cols:
                context['error'] = f'Faylda quyidagi ustunlar topilmadi: {", ".join(missing_cols)}'
                context['info'] = f'Mavjud ustunlar: {", ".join(df_earthquakes.columns)}'
                context['selected_wells'] = selected_wells
                return render(request, 'seismos_app/map_only.html', context)

            # 5. MA'LUMOTLARNI TOZALASH
            df_earthquakes['Mb'] = pd.to_numeric(df_earthquakes['Mb'], errors='coerce')
            df_earthquakes['Latitude'] = pd.to_numeric(df_earthquakes['Latitude'], errors='coerce')
            df_earthquakes['Longitude'] = pd.to_numeric(df_earthquakes['Longitude'], errors='coerce')
            df_earthquakes.dropna(subset=['Mb', 'Latitude', 'Longitude'], inplace=True)

            if 'Depth' in df_earthquakes.columns:
                df_earthquakes['Depth'] = pd.to_numeric(df_earthquakes['Depth'], errors='coerce')
            else:
                df_earthquakes['Depth'] = None

            # 6. MAGNITUDA BO'YICHA FILTRLASH
            df_earthquakes = df_earthquakes[df_earthquakes['Mb'] >= min_mag].copy()

            if df_earthquakes.empty:
                context['error'] = f'Mb >= {min_mag} shartiga mos zilzilalar topilmadi'
                context['selected_wells'] = selected_wells
                return render(request, 'seismos_app/map_only.html', context)

            # 7. SANA BO'YICHA FILTRLASH
            if filter_start_date:
                try:
                    start_date = pd.to_datetime(filter_start_date)
                    df_earthquakes['Event_date'] = pd.to_datetime(df_earthquakes['Event_date'], errors='coerce')
                    df_earthquakes = df_earthquakes[df_earthquakes['Event_date'] >= start_date]

                    if filter_end_date:
                        end_date = pd.to_datetime(filter_end_date)
                        df_earthquakes = df_earthquakes[df_earthquakes['Event_date'] <= end_date]

                except Exception as e:
                    logging.warning(f'Sana filtrlashda xato: {e}')

            # 8. XARITA YARATISH
            map_html = create_earthquake_map_with_wells(
                df_earthquakes=df_earthquakes,
                all_wells=all_wells,
                selected_wells=selected_wells,
                min_mag=min_mag
            )

            # 9. CONTEXT YANGILASH
            context.update({
                'map_html': map_html,
                'current_min_mag': min_mag,
                'current_start_date': filter_start_date,
                'current_end_date': filter_end_date,
                'selected_wells': selected_wells,
                'success': f'Muvaffaqiyatli! {len(df_earthquakes)} ta zilzila ko\'rsatildi',
                'uploaded_file_info': {
                    'name': uploaded_file.name,
                    'size': f'{uploaded_file.size / 1024:.2f} KB',
                    'earthquakes_count': len(df_earthquakes)
                }
            })

            # Faylni o'chirish
            try:
                os.remove(file_path)
            except:
                pass

        except ValueError as e:
            context['error'] = f'Noto\'g\'ri qiymat kiritildi: {e}'
            logging.error(f'ValueError: {e}')

        except Exception as e:
            context['error'] = f'Kutilmagan xato: {e}'
            logging.error(f'Earthquake map error: {e}', exc_info=True)

    return render(request, 'seismos_app/map_only.html', context)

def create_earthquake_map_with_wells(df_earthquakes, all_wells, selected_wells, min_mag):
    """
    Tanlangan skvajinalar va aylanalar bilan xarita yaratish
    """
    # 1. XARITA MARKAZINI ANIQLASH
    if selected_wells:
        selected_coords = [all_wells[w] for w in selected_wells if w in all_wells]
        if selected_coords:
            center_lat = np.mean([c[0] for c in selected_coords])
            center_lon = np.mean([c[1] for c in selected_coords])
        else:
            center_lat, center_lon = 41.2995, 69.2401
    elif all_wells:
        all_lats = [coord[0] for coord in all_wells.values()]
        all_lons = [coord[1] for coord in all_wells.values()]
        center_lat = np.mean(all_lats)
        center_lon = np.mean(all_lons)
    else:
        center_lat, center_lon = 41.2995, 69.2401

    # 2. ASOSIY XARITA
    m = folium.Map(
        location=[center_lat, center_lon],
        zoom_start=7,
        tiles="OpenStreetMap",
        attr="© OpenStreetMap contributors"
    )

    Fullscreen(
        position='topleft',
        title='To\'liq ekran',
        title_cancel='Chiqish',
        force_separate_button=True
    ).add_to(m)

    # 3. QO'SHIMCHA FON XARITALARI
    folium.TileLayer(
        tiles='https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
        attr='Tiles © Esri',
        name='Satellite',
        overlay=False,
        control=True
    ).add_to(m)

    folium.TileLayer(
        tiles='https://stamen-tiles.a.ssl.fastly.net/terrain/{z}/{x}/{y}.png',
        attr='Map tiles by Stamen Design',
        name='Terrain',
        overlay=False,
        control=True
    ).add_to(m)

    # 4. YER YORIQLARINI QO'SHISH
    logging.info("Yer yoriqlarini yuklash...")
    all_cracks = load_all_cracks_shapefiles()
    if all_cracks is not None:
        add_cracks_to_map(m, all_cracks)

    # 5. SEYSMOGEN ZONALARNI QO'SHISH
    logging.info("Seysmogen zonalarni yuklash...")
    seismogenic_zones = load_seismogenic_zones()
    if seismogenic_zones is not None:
        add_seismogenic_zones_to_map(m, seismogenic_zones)

    # 6. RANGLAR GENERATSIYA QILISH
    well_color_map = generate_well_colors(selected_wells)

    # 7. FAQAT TANLANGAN SKVAJINALARNI QO'SHISH (har xil rangda)
    mlgr_val = 2.5  # Fix qiymat

    for well_name in selected_wells:
        if well_name not in all_wells:
            continue

        lat, lon = all_wells[well_name]
        colors = well_color_map.get(well_name, {
            'base': 'blue',
            'triangle': 'blue',
            'shades': ['rgba(0,0,255,0.5)', 'rgba(0,0,255,0.7)', 'rgba(0,0,255,0.9)']
        })

        well_info = get_well_detailed_info(well_name)

        tooltip_html = f"""
                        <div style="width: 450px; font-family: Arial; font-size: 12px;">
                            <h4 style="color: {colors['base']}; margin-bottom: 10px;">Tanlangan skvajina</h4>
                            <table style="width: 100%; border-collapse: collapse;">
                                <tr style="background-color: #f8f9fa;">
                                    <td style="padding: 5px; border: 1px solid #dee2e6; font-weight: bold;">Nomi:</td>
                                    <td style="padding: 5px; border: 1px solid #dee2e6;">{well_info.get('nomi', 'Ma\'lumot yo\'q')}</td>
                                </tr>
                                <tr>
                                    <td style="padding: 5px; border: 1px solid #dee2e6; font-weight: bold;">Quduq turi:</td>
                                    <td style="padding: 5px; border: 1px solid #dee2e6;">{well_info.get('quduq_turi', 'Ma\'lumot yo\'q')}</td>
                                </tr>
                                <tr>
                                    <td style="padding: 5px; border: 1px solid #dee2e6; font-weight: bold;">Chuqurlik:</td>
                                    <td style="padding: 5px; border: 1px solid #dee2e6;">{well_info.get('chuqurlik', 'Ma\'lumot yo\'q')} m</td>
                                </tr>
                                <tr style="background-color: #f8f9fa;">
                                    <td style="padding: 5px; border: 1px solid #dee2e6; font-weight: bold;">Seysmotektonik holat:</td>
                                    <td style="padding: 5px; border: 1px solid #dee2e6;">{well_info.get('seysmotektonik_holat', 'Ma\'lumot yo\'q')}</td>
                                </tr>
                                <tr style="background-color: #f8f9fa;">
                                    <td style="padding: 5px; border: 1px solid #dee2e6; font-weight: bold;">Strategrafik taqsimoti:</td>
                                    <td style="padding: 5px; border: 1px solid #dee2e6;">{well_info.get('strategrafik_taqsimoti', 'Ma\'lumot yo\'q')}</td>
                                </tr>
                                <tr style="background-color: #f8f9fa;">
                                    <td style="padding: 5px; border: 1px solid #dee2e6; font-weight: bold;">Litologik tarkibi:</td>

                                </tr>

                            </table>
                            <p style="margin-top: 10px; color: {colors['base']}; font-weight: bold;">✓ Tanlangan skvajina</p>
                        </div>
                    """

        # Uchburchak marker
        triangle_icon = folium.DivIcon(
            html=f'<div style="width: 0; height: 0; border-left: 10px solid transparent; border-right: 10px solid transparent; border-bottom: 20px solid {colors["triangle"]};"></div>',
            icon_size=(20, 20),
            icon_anchor=(10, 20)
        )

        folium.Marker(
            location=[lat, lon],
            tooltip=folium.Tooltip(tooltip_html, sticky=True),
            icon=triangle_icon,
        ).add_to(m)

        # 9. AYLANALARNI QO'SHISH (JavaScript bilan toggle)
        try:
            radii_data = [
                (5, colors['shades'][0]),  # M=5
                (6, colors['shades'][1]),  # M=6
                (7, colors['shades'][2]),  # M=7
            ]

            circles_info = []
            for M_value, color in radii_data:
                R_km = float(10 ** (M_value / mlgr_val))
                circles_info.append({
                    'radius': R_km * 1000,  # metrga aylantirish
                    'color': color,
                    'M': M_value,
                    'R_km': R_km,
                    'mlgr': mlgr_val
                })

            js_code = f"""
            <script>
            (function() {{
                var wellLat = {lat};
                var wellLon = {lon};
                var wellName = "{well_name}";
                var circlesInfo = {circles_info};
                var circlesLayer = null;
                var isVisible = true;  // Boshlang'ichda ko'rinadi

                document.addEventListener("DOMContentLoaded", function() {{
                    var map = window.map || Object.values(window).find(v => v instanceof L.Map);
                    if (!map) {{
                        console.error("Xarita topilmadi");
                        return;
                    }}

                    // Aylanalar layerini yaratish
                    circlesLayer = L.layerGroup();
                    circlesInfo.forEach(function(info) {{
                        var circle = L.circle([wellLat, wellLon], {{
                            radius: info.radius,
                            color: info.color,
                            weight: 2,
                            fill: false,
                            opacity: 0.7
                        }});

                        circle.bindTooltip(
                            "M=" + info.M + ", R=" + info.R_km.toFixed(1) + " km (M/lgR=" + info.mlgr + ")",
                            {{permanent: false, direction: 'top'}}
                        );

                        circlesLayer.addLayer(circle);
                    }});

                    // Boshlang'ichda ko'rsatish
                    circlesLayer.addTo(map);

                    // Marker topish va click hodisasini qo'shish
                    map.eachLayer(function(layer) {{
                        if (layer instanceof L.Marker) {{
                            var latlng = layer.getLatLng();
                            if (Math.abs(latlng.lat - wellLat) < 0.0001 && 
                                Math.abs(latlng.lng - wellLon) < 0.0001) {{

                                layer.on('click', function(e) {{
                                    L.DomEvent.stopPropagation(e);

                                    if (isVisible) {{
                                        map.removeLayer(circlesLayer);
                                        isVisible = false;
                                    }} else {{
                                        circlesLayer.addTo(map);
                                        isVisible = true;
                                    }}
                                }});
                            }}
                        }}
                    }});
                }});
            }})();
            </script>
            """
            m.get_root().html.add_child(folium.Element(js_code))

        except Exception as e:
            logging.error(f"Aylanalar qo'shishda xato ({well_name}): {e}")

    # 8. ZILZILALARNI QO'SHISH
    if not df_earthquakes.empty:
        for idx, row in df_earthquakes.iterrows():
            lat = row['Latitude']
            lon = row['Longitude']
            mag = row['Mb']

            try:
                date_str = pd.to_datetime(row['Event_date']).strftime('%d.%m.%Y')
            except:
                date_str = str(row['Event_date'])

            depth = row.get('Depth', 'Noma\'lum')

            tooltip_html = f"""
                <b>Zilzila</b><br>
                <b>Sana:</b> {date_str}<br>
                <b>Magnituda (Mb):</b> {mag:.2f}<br>
                <b>Chuqurlik:</b> {depth} km
            """

            if mag >= 6:
                color = "darkred"
                radius = mag * 3
            elif mag >= 5:
                color = "red"
                radius = mag * 2.5
            elif mag >= 4:
                color = "orange"
                radius = mag * 2
            else:
                color = "yellow"
                radius = mag * 1.5

            folium.CircleMarker(
                location=[lat, lon],
                radius=radius,
                color=color,
                fill=True,
                fillColor=color,
                fillOpacity=0.7,
                weight=2,
                tooltip=tooltip_html
            ).add_to(m)

    # 9. LEGEND
    legend_html = f'''
    <div style="position: fixed; 
                bottom: 50px; left: 50px; width: 200px; 
                background-color: white; border:2px solid grey; z-index:9999; 
                font-size:12px; padding: 10px; overflow-y: auto;">
        <p><b>Xarita Elementlari:</b></p>
        <p><i class="fa fa-circle" style="color:darkred"></i> Zilzila Mb ≥ 6.0</p>
        <p><i class="fa fa-circle" style="color:red"></i> Zilzila Mb 5.0-5.9</p>
        <p><i class="fa fa-circle" style="color:orange"></i> Zilzila Mb 4.0-4.9</p>
        <p><i class="fa fa-circle" style="color:yellow"></i> Zilzila Mb < 4.0</p>
        <p><div style="width: 0; height: 0; border-left: 8px solid transparent; border-right: 8px solid transparent; border-bottom: 15px solid #ADD8E6; display: inline-block; margin-right: 8px;"></div>Tanlanmagan skvajinalar</p>
        <p><div style="width: 0; height: 0; border-left: 8px solid transparent; border-right: 8px solid transparent; border-bottom: 15px solid blue; display: inline-block; margin-right: 8px;"></div>Tanlangan skvajinalar</p>
    </div>
    '''
    m.get_root().html.add_child(folium.Element(legend_html))

    folium.LayerControl().add_to(m)

    return m._repr_html_()


def selection_view(request):
    """
    Handles the selection of wells and parametrs for analysis.
    """
    lst_stansiya, _ = fetch_data()
    all_params = []
    median_values = [3, 5, 7, 15, 31, 91, 183, 365, 731]

    for group_name, params_list in DEFAULT_ELEMENTS_GROUPS.items():
        all_params.extend(params_list)
    all_params = sorted(list(set(all_params)))

    if request.method == "POST":
        selected_keys = request.POST.getlist("wells")
        selected_params = request.POST.getlist("params")

        if not selected_keys:
            return render(
                request,
                "seismos_app/results1.html",
                {
                    "wells": lst_stansiya.keys(),
                    "params": all_params,
                    "median_values": median_values,
                    "error": "Kamida bitta quduq tanlang.",
                },
            )

        if not selected_params:
            selected_params = sorted(
                list(set(sum(DEFAULT_ELEMENTS_GROUPS.values(), [])))
            )

        request.session["selected_keys"] = selected_keys
        request.session["selected_params"] = selected_params
        return redirect("seismos:parametrs")

    return render(
        request,
        "seismos_app/results1.html",
        {"wells": lst_stansiya.keys(), "params": all_params, "median_values": median_values},
    )


def parametrs_view(request):
    if request.method == "POST":
        try:
            min_mag = float(request.POST["min_mag"])
            btn_value = float(request.POST["sigma"])
            min_mlgr = float(request.POST["min_mlgr"])

            # catalog jadvalidan foydalanish
            request.session.update(
                {
                    "use_catalog": True,
                    "min_mag": min_mag,
                    "btn_value": btn_value,
                    "min_mlgr": min_mlgr
                }
            )
            return redirect("seismos:results")
        except ValueError:
            logging.error("Invalid input for numeric parametrs")
            return render(
                request,
                "seismos_app/results1.html",
                {"error": "Iltimos barcha sonli maydonlarga to'gri qiymat kiriting"}
            )
        except Exception as e:
            logging.error(f"Parametr input error:{e}")
            return render(
                request,
                "seismos_app/results1.html",
                {"error": f"Xato yuz berdi:{e},Iltimos qayta urinib ko'ring"}
            )
    return render(request, "seismos_app/results1.html")


def results_view(request):
    """
    Generates and displays seismic analysis results.
    Instead of one giant plot, it generates a list of individual plots
    allowing separate downloads for each graph.
    """

    # Ma'lumotlarni olish (wells va params ro'yxati uchun)
    lst_stansiya, well_coords = fetch_data()
    all_params = []
    for group_name, params_list in DEFAULT_ELEMENTS_GROUPS.items():
        all_params.extend(params_list)
    all_params = sorted(list(set(all_params)))

    if request.method == "POST":
        selected_keys = request.POST.getlist("wells")
        selected_params = request.POST.getlist("params")

        filter_mode = request.POST.get("filter_mode", "mlgr")

        # ✅ YANGI: Ko'rsatish nazorati
        hide_map = request.POST.get('hide_map') == '1'
        hide_graphs = request.POST.get('hide_graphs') == '1'

        # Agar ikkalasi ham belgilangan bo'lsa, ignore qilamiz
        if hide_map and hide_graphs:
            hide_map = False
            hide_graphs = False

        # Raqamli qiymatlarni xavfsiz olish
        try:
            min_mag = float(request.POST.get("min_mag", 0))
            btn_value = float(request.POST.get("sigma", 0))
            min_mlgr = float(request.POST.get("min_mlgr", 0))
        except (ValueError, TypeError):
            min_mag = 0.0
            btn_value = 0.0
            min_mlgr = 0.0

        filter_start_date = request.POST.get("start_date")
        filter_end_date = request.POST.get("end_date")
        median_window_raw = request.POST.get("median_window", "").strip()

        if not median_window_raw:
            median_window = None
        else:
            try:
                median_window = int(median_window_raw)
            except ValueError:
                median_window = None

        use_catalog = True
        request.session['use_catalog'] = True

        # sessionda saqlash (✅ ko'rsatish holatlari ham qo'shildi)
        request.session['selected_keys'] = selected_keys
        request.session['selected_params'] = selected_params
        request.session['min_mag'] = min_mag
        request.session['btn_value'] = btn_value
        request.session['min_mlgr'] = min_mlgr
        request.session['filter_start_date'] = filter_start_date
        request.session['filter_end_date'] = filter_end_date
        request.session['median_window'] = median_window
        request.session['use_catalog'] = True
        request.session['filter_mode'] = filter_mode
        request.session['hide_map'] = hide_map  # ✅ YANGI
        request.session['hide_graphs'] = hide_graphs  # ✅ YANGI

    else:
        selected_keys = request.session.get("selected_keys", [])
        selected_params = request.session.get("selected_params", [])
        min_mag = request.session.get("min_mag")
        btn_value = request.session.get("btn_value")
        min_mlgr = request.session.get("min_mlgr")
        filter_start_date = request.session.get("filter_start_date")
        filter_end_date = request.session.get("filter_end_date")
        use_catalog = request.session.get("use_catalog", False)
        median_window = request.session.get("median_window", None)
        filter_mode = request.session.get("filter_mode", "mlgr")
        hide_map = request.session.get("hide_map", False)  # ✅ YANGI
        hide_graphs = request.session.get("hide_graphs", False)  # ✅ YANGI

    # Tanlangan skvajinalar nomini olish
    selected_well_names = []
    for key in selected_keys:
        if " | " in key:
            _, well_name = key.split(" | ")
            selected_well_names.append(well_name)

    # Page title
    if selected_well_names:
        if len(selected_well_names) == 1:
            page_title = f"{selected_well_names[0]} - Seysmik Tahlil"
        elif len(selected_well_names) <= 3:
            page_title = f"{', '.join(selected_well_names)} - Seysmik Tahlil"
        else:
            page_title = f"{', '.join(selected_well_names[:3])} va boshqalar - Seysmik Tahlil"
    else:
        page_title = "Seysmik Tahlil"

    # Input validatsiyasi
    if not all([
        selected_keys,
        use_catalog,
        min_mag is not None,
        btn_value is not None,
        min_mlgr is not None,
    ]):
        return render(
            request,
            "seismos_app/results1.html",
            {
                "wells": lst_stansiya.keys(),
                "params": all_params,
                "selected_wells": selected_keys,
                "selected_params": selected_params,
                "current_min_mag": min_mag,
                "current_sigma": btn_value,
                "current_min_mlgr": min_mlgr,
                "current_start_date": filter_start_date or "",
                "current_end_date": filter_end_date or "",
                "current_hide_map": hide_map,  # ✅ YANGI
                "current_hide_graphs": hide_graphs,  # ✅ YANGI
                "error": "To'liq ma'lumotlar mavjud emas. Iltimos, oldingi qadamlarga qayting."
            },
        )

    if not selected_params:
        selected_params = sorted(list(set(sum(DEFAULT_ELEMENTS_GROUPS.values(), []))))
        request.session['selected_params'] = selected_params

    # Sana filtrlarini tekshirish
    user_start_date = None
    user_end_date = None

    if filter_start_date:
        try:
            user_start_date = pd.to_datetime(filter_start_date)
            if filter_end_date:
                user_end_date = pd.to_datetime(filter_end_date)
            else:
                user_end_date = pd.Timestamp.today().normalize()

            if user_start_date > user_end_date:
                return render(
                    request,
                    "seismos_app/results1.html",
                    {
                        "error": "Boshlang'ich sana oxirgi sanadan katta bo'lmasligi kerak."
                    },
                )
        except (ValueError, TypeError) as e:
            logging.warning(f"Sana filtri xatosi: {e}")
            return render(
                request,
                "seismos_app/results1.html",
                {"error": f"Noto'g'ri sana formati: {e}"},
            )

    # Default sanalar
    default_start_date = pd.to_datetime("1984-01-01")
    today = pd.to_datetime("today").normalize()

    # X-o'qi uchun sanalar
    x_axis_start = user_start_date if user_start_date else default_start_date
    x_axis_end = user_end_date if user_end_date else today + relativedelta(months=2)

    engine = None
    conn = None

    try:
        # Ma'lumotlar bazasiga ulanish
        engine = connect_db()
        if not engine:
            return render(request, "seismos_app/results1.html", {"error": "Bazaga ulanish xatosi"})
        conn = engine.connect()

        # Catalog jadvalidan zilzilalar ma'lumotlarini olish
        query = "SELECT `Event_date`, `Event_time`, `Latitude`, `Longitude`, `Depth`, `Mb` FROM catalog"
        dfe = pd.read_sql(query, engine)
        dfe[MAIN_MAGNITUDE_COLUMN] = pd.to_numeric(dfe[MAIN_MAGNITUDE_COLUMN], errors='coerce')
        dfe.dropna(subset=[MAIN_MAGNITUDE_COLUMN], inplace=True)

        # Zilzilalar ma'lumotlarini qayta ishlash
        all_earthquakes_df = dfe.copy()
        all_earthquakes_df[TIME_COLUMN] = (
            pd.to_timedelta(all_earthquakes_df[TIME_COLUMN])
            .dt.total_seconds()
            .astype(int)
        )
        all_earthquakes_df[TIME_COLUMN] = pd.to_datetime(
            all_earthquakes_df[TIME_COLUMN], unit="s"
        ).dt.strftime("%H:%M:%S")

        all_earthquakes_df["combined_datetime"] = pd.to_datetime(
            all_earthquakes_df[DATE_COLUMN].astype(str) + " " + all_earthquakes_df[TIME_COLUMN].astype(str),
            format="%Y-%m-%d %H:%M:%S",
            errors="coerce",
        )
        all_earthquakes_df.dropna(subset=["combined_datetime"], inplace=True)

        # Zilzilalarni filtrlash
        if user_start_date and user_end_date:
            earthquake_mask = (
                    (all_earthquakes_df["combined_datetime"] >= user_start_date)
                    & (all_earthquakes_df["combined_datetime"] <= user_end_date)
            )
            filtered_earthquakes_df = all_earthquakes_df[earthquake_mask].copy()
        else:
            earthquake_mask = (all_earthquakes_df["combined_datetime"] >= default_start_date)
            filtered_earthquakes_df = all_earthquakes_df[earthquake_mask].copy()

        # ✅ Grafiklar yaratish (faqat agar hide_graphs False bo'lsa)
        graphs_list = []

        if not hide_graphs:
            # Ma'lumot yig'ish (graph_data)
            graph_data = []

            for key in selected_keys:
                for param in selected_params:
                    ssdi_id = lst_stansiya.get(key, {}).get(param)
                    if not ssdi_id:
                        continue

                    query = text(f"SELECT date, `{ssdi_id}` FROM alldata WHERE `{ssdi_id}` IS NOT NULL")
                    try:
                        data = conn.execute(query).fetchall()
                    except Exception as e:
                        logging.error(f"Query xatosi {key} - {param}: {e}")
                        continue

                    if not data:
                        continue

                    df_temp = pd.DataFrame(data, columns=['date', 'value'])
                    df_temp['date'] = pd.to_datetime(df_temp['date'], errors='coerce')
                    df_temp.dropna(subset=['date', 'value'], inplace=True)

                    if user_start_date and user_end_date:
                        df_temp = df_temp[
                            (df_temp['date'] >= user_start_date) &
                            (df_temp['date'] <= user_end_date)
                            ].copy()
                    else:
                        df_temp = df_temp[df_temp['date'] >= default_start_date].copy()

                    if df_temp.empty:
                        continue

                    x_val = df_temp['date'].tolist()
                    y_val = df_temp['value'].tolist()

                    if not y_val:
                        continue

                    # Median window
                    if median_window and median_window > 0:
                        df_temp_med = pd.DataFrame({'date': x_val, 'value': y_val})
                        df_temp_med['date'] = pd.to_datetime(df_temp_med['date'])

                        df_daily_median = (
                            df_temp_med.groupby(df_temp_med['date'].dt.date)['value']
                            .median().reset_index().rename(columns={'value': 'daily_median'})
                        )
                        df_daily_median['rolling_median'] = (
                            df_daily_median['daily_median']
                            .rolling(window=median_window, min_periods=1, center=True).median()
                        )
                        x_val = pd.to_datetime(df_daily_median['date']).tolist()
                        y_val = df_daily_median['rolling_median'].tolist()

                    mean, sigma = np.mean(y_val), np.std(y_val)
                    if sigma == 0:
                        continue

                    _, skvajina = key.split(" | ")
                    graph_data.append((x_val, y_val, mean, sigma, param, key, skvajina))

            if graph_data:
                delta = timedelta(days=15)
                color_pool = generate_colors(len(graph_data))

                for idx, (x, y, mean, sigma, param, key, skv) in enumerate(graph_data):
                    fig = make_subplots(specs=[[{"secondary_y": True}]])

                    trace_color = color_pool[idx]
                    row = 1
                    col = 1

                    y_all = plot_data_with_anomalies(
                        fig, x, y, mean, sigma, btn_value, row, col, trace_color, param, key
                    )

                    fig.update_yaxes(
                        title_text=f"{param} Qiymati",
                        range=[min(y_all) * 0.9, max(y_all) * 1.1],
                        row=row, col=col, secondary_y=False
                    )

                    lat, lon = well_coords.get(skv, (0, 0))
                    if not filtered_earthquakes_df.empty:
                        draw_magnitude_values(
                            fig, filtered_earthquakes_df, row, col,
                            min_mag=min_mag, well_lat=lat, well_lon=lon, min_mlgr=min_mlgr, filter_mode=filter_mode
                        )

                    fig.update_xaxes(
                        range=[x_axis_start - delta, x_axis_end + delta],
                        type="date",
                        showgrid=True, griddash="dot",
                        row=row, col=col
                    )

                    graph_title = f"{key} - {param}"
                    title_date = f" ({filter_start_date} - {filter_end_date})" if filter_start_date and filter_end_date else " "

                    fig.update_layout(
                        title_text=f"{graph_title}{title_date}",
                        height=500,
                        autosize=True,
                        showlegend=False,
                        plot_bgcolor="gainsboro",
                        hovermode="x unified",
                        hoverdistance=1,
                        margin=dict(l=60, r=60, t=80, b=60),
                        legend=dict(x=0.01, y=0.99, bgcolor="rgba(255,255,255,0.9)")
                    )

                    div_id = f"plot_{idx}"
                    filename = f"Seysmik_{skv}_{param}_{datetime.datetime.now().strftime('%Y%m%d')}"

                    config = {
                        "displayModeBar": True,
                        "displaylogo": False,
                        "responsive": True,
                        "modeBarButtonsToRemove": ['toImage'],
                    }

                    graph_html = fig.to_html(
                        full_html=False,
                        include_plotlyjs=False,
                        config=config,
                        div_id=div_id
                    )

                    graphs_list.append({
                        'html': graph_html,
                        'title': graph_title,
                        'div_id': div_id,
                        'filename': filename
                    })

        # ✅ Folium xaritasini yaratish (faqat agar hide_map False bo'lsa)
        folium_map_html = None
        if not hide_map:
            try:
                folium_map_html = add_map_data_folium(
                    selected_keys, well_coords, filtered_earthquakes_df, min_mag, min_mlgr, filter_mode=filter_mode
                )
            except Exception as e:
                logging.error(f"Folium xaritasini yaratishda xato: {e}")
                folium_map_html = "<p>Xarita yaratishda xato yuz berdi.</p>"

        # Context tayyorlash
        context = {
            "wells": lst_stansiya.keys(),
            "params": all_params,
            "selected_wells": selected_keys,
            "selected_well_names": selected_well_names,
            "page_title": page_title,

            # Input qiymatlarini qaytarish
            "current_min_mag": min_mag,
            "current_sigma": btn_value,
            "current_min_mlgr": min_mlgr,
            "current_start_date": filter_start_date or "",
            "current_end_date": filter_end_date or "",
            "current_median_window": median_window or "",
            "current_filter_mode": filter_mode,
            "current_hide_map": hide_map,  # ✅ YANGI
            "current_hide_graphs": hide_graphs,  # ✅ YANGI
            "median_values": [3, 5, 7, 15, 31, 91, 183, 365, 731],

            # Asosiy natijalar
            "graphs_list": graphs_list,
            "folium_map": folium_map_html,

            # ✅ Ko'rsatish holatlarini template uchun
            "show_graphs": not hide_graphs,
            "show_map": not hide_map,
        }

        # Data diapazoni info uchun
        if not hide_graphs and graphs_list:
            all_dates = [d for item in graph_data for d in item[0]]
            if all_dates:
                context["data_min_date"] = min(pd.to_datetime(all_dates)).strftime('%Y-%m-%d')
                context["data_max_date"] = max(pd.to_datetime(all_dates)).strftime('%Y-%m-%d')

        return render(request, "seismos_app/results1.html", context)

    except Exception as e:
        logging.error(f"Results view error: {e}", exc_info=True)
        return render(request, "seismos_app/results1.html",
                      {"error": "Tizimda kutilmagan xato yuz berdi."})

    finally:
        try:
            if conn: conn.close()
            if engine: engine.dispose()
        except Exception:
            pass