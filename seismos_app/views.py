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
import base64

from datetime import timedelta
from math import pi, sin, cos, atan2, sqrt
from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.shortcuts import render, redirect
from sqlalchemy import create_engine, text
from plotly.subplots import make_subplots
from folium.plugins import Fullscreen
from scipy.stats import pearsonr, spearmanr
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from decouple import config as env_config
from typing import Dict, Tuple, List, Optional
from django.core.cache import cache
from sqlalchemy.pool import QueuePool, NullPool
from threading import Lock
from django.core.cache import caches

from download_base_app.views import logger
from .models import Skvajina, AllIzmereniya, Malumot
from upload_catalog_app.models import Catalog


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

# Global cache
_cached_cracks = None
_cached_seismogenic_zones = None
_cache_lock = Lock()

# Global variables
_global_engine = None
_engine_lock = Lock()


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




def get_db_engine():
    """
    ✅ SINGLETON: Bitta engine yaratish va qayta ishlatish
    Thread-safe implementation
    """
    global _global_engine

    if _global_engine is None:
        with _engine_lock:
            # Double-check locking
            if _global_engine is None:
                try:
                    config = get_db_config()

                    _global_engine = create_engine(
                        f"mysql+mysqlconnector://{config['user']}:{config['psw']}@{config['ip']}/{config['db']}",

                        # ✅ Connection pooling settings
                        poolclass=QueuePool,
                        pool_size=10,  # Normal connections
                        max_overflow=20,  # Extra connections agar kerak bo'lsa
                        pool_recycle=3600,  # 1 soat keyin connection refresh
                        pool_pre_ping=True,  # Connection alive tekshirish
                        pool_timeout=30,  # Connection kutish vaqti

                        # ✅ Performance settings
                        echo=False,  # SQL log'ni o'chirish (production)
                        connect_args={
                            'connect_timeout': 10,
                            'autocommit': True,
                        }
                    )

                    logger.info("✅ Created DB engine with connection pooling")

                except Exception as e:
                    logger.error(f"❌ Failed to create DB engine: {e}")
                    raise

    return _global_engine


def connect_db():
    """
    ✅ OPTIMIZED: Singleton engine ishlatish
    """
    return get_db_engine()


def close_db_engine():
    """
    Engine'ni yopish (graceful shutdown uchun)
    """
    global _global_engine

    if _global_engine is not None:
        _global_engine.dispose()
        _global_engine = None
        logger.info("✅ DB engine closed")


# --- Data Fetching ---

def fetch_data() -> Tuple[Dict[str, Dict[str, str]], Dict[str, Tuple[float, float]]]:
    """
    ✅ OPTIMIZED: Select related bilan
    """
    cache_key = 'seismos_fetch_data_v2'
    cached_data = cache.get(cache_key)

    if cached_data:
        logger.info("✅ Data from cache")
        return cached_data

    try:
        from .models import AllIzmereniya, Skvajina

        # ✅ Select only needed fields (kamroq memory)
        izmereniya_list = AllIzmereniya.objects.only(
            'stansiya', 'skvajina', 'izmereniya', 'ssdi_id'
        ).values('stansiya', 'skvajina', 'izmereniya', 'ssdi_id')

        lst_stansiya = {}
        for item in izmereniya_list:
            key = f"{item['stansiya']} | {item['skvajina']}"
            if key not in lst_stansiya:
                lst_stansiya[key] = {}
            lst_stansiya[key][item['izmereniya']] = item['ssdi_id']

        # ✅ Select only needed fields
        wells = Skvajina.objects.only('naim', 'Latitude', 'Longitude').filter(
            Latitude__isnull=False,
            Longitude__isnull=False
        ).values('naim', 'Latitude', 'Longitude')

        well_coords = {
            well['naim'].strip(): (well['Latitude'], well['Longitude'])
            for well in wells
        }

        result = (lst_stansiya, well_coords)
        cache.set(cache_key, result, 3600)

        logger.info(f"✅ Fetched {len(lst_stansiya)} stations, {len(well_coords)} wells")
        return result

    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        return {}, {}


def fetch_data_with_multi_cache():
    """
    ✅ Multi-level caching: Local memory → Redis → Database
    """
    cache_key = 'seismos_fetch_data_v2'

    # Level 1: Local memory (eng tez)
    local_cache = caches['local']
    cached = local_cache.get(cache_key)
    if cached:
        logger.info("✅ Data from local cache")
        return cached

    # Level 2: Redis (tez)
    redis_cache = caches['default']
    cached = redis_cache.get(cache_key)
    if cached:
        logger.info("✅ Data from Redis")
        local_cache.set(cache_key, cached, 300)  # Local'ga ham saqlash
        return cached

    # Level 3: Database (sekin)
    result = _fetch_data_from_db()

    # Cache'ga saqlash
    local_cache.set(cache_key, result, 300)
    redis_cache.set(cache_key, result, 3600)

    return result

def get_all_wells_coordinates() -> Dict[str, Tuple[float, float]]:
    cache_key = 'all_wells_coordinates'
    cached = cache.get(cache_key)

    if cached:
        return cached

    try:
        wells = Skvajina.objects.filter(
            Latitude__isnull = False,
            Longitude__isnull = False,
        ).values('naim','Latitude','Longitude')

        all_wells = {
            well['naim'].strip():(well['Latitude'], well['Longitude'])
            for well in wells
        }

        cache.set(cache_key, all_wells, 3600)
        return all_wells

    except Exception as e:
        logger.error(f"Error fetching wells coordinates: {e}")
        return {}
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
        segment_years=None,
        min_similarity=70,  # Yangi parametr: minimal o'xshashlik foizi
        min_anomaly_length=5,  # Minimal anomaliya segment uzunligi (nuqta soni)
        highlight_similar=True,  # O'xshash anomaliyalarni belgilashni yoqish/o'chirish
):
    """
    Ma'lumotlarning butun chizig'ini chizadi va anomaliya qismlarini qizil rangda belgilaydi.
    Qo'shimcha: Anomaliya segmentlari ichidan o'xshashlarini topib, yashil rangda belgilaydi.
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

    df = pd.DataFrame({'date': pd.to_datetime(x_val), 'value': y_val})

    # Global Chegaralarni hisoblash
    upper_bound = mean + btn_value * sigma
    lower_bound = mean - btn_value * sigma
    df['global_anomaly'] = (df['value'] > upper_bound) | (df['value'] < lower_bound)

    y_all_values = [y for y in y_val if not np.isnan(y)]
    y_all_values.extend([upper_bound, lower_bound, mean])

    has_segments = isinstance(segment_years, int) and segment_years > 0
    if has_segments:
        # 2. Segmental (Yillik) chegaralar
        df['year'] = df['date'].dt.year
        df['group'] = (df['year'] // segment_years) * segment_years

        seg_stats = df.groupby('group')['value'].agg(['mean', 'std']).reset_index()
        seg_stats.rename(columns={'mean': 'seg_mean', 'std': 'seg_std'}, inplace=True)
        seg_stats['seg_std'] = seg_stats['seg_std'].fillna(0)

        df = df.merge(seg_stats, on='group', how='left')

        df['seg_upper'] = df['seg_mean'] + btn_value * df['seg_std']
        df['seg_lower'] = df['seg_mean'] - btn_value * df['seg_std']

        df['segment_anomaly'] = (df['value'] > df['seg_upper']) | (df['value'] < df['seg_lower'])

        # 3. KUCHLI ANOMALIYA (Ikkalasi ham ishlaganda)
        df['is_anomalous'] = df['global_anomaly'] & df['segment_anomaly']

        y_all_values.extend(df['seg_upper'].dropna().tolist())
        y_all_values.extend(df['seg_lower'].dropna().tolist())
    else:
        df['is_anomalous'] = df['global_anomaly']

    is_anom_list = df['is_anomalous'].tolist()

    yaxis_index = (row_idx - 1) * 1 + col_idx
    yref = "y" if yaxis_index == 1 else f"y{2 * row_idx - 1}"
    fig.add_shape(
        type="rect",
        x0=min(x_val), x1=max(x_val),
        y0=lower_bound, y1=upper_bound,
        fillcolor="gray",  # Fon rangi
        opacity=0.15,  # 15% to'qlikda (ko'zni charchatmasligi uchun juda ochiq kulrang)
        layer="below",  # Chiziqlar va grafikning orqasiga (orqa fonga) tushirish
        line_width=0,  # To'rtburchakning o'zini qo'shimcha ramkasi bo'lmasligi uchun
        row=row_idx, col=col_idx, yref=yref, xref="x"
    )
    # UB, MEAN va LB chiziqlarini chizish (Global)
    # fig.add_shape(type="line", x0=min(x_val), x1=max(x_val), y0=upper_bound, y1=upper_bound,
    #               line=dict(color="green", width=1.5), row=row_idx, col=col_idx, yref=yref, xref="x")
    # fig.add_annotation(x=max(x_val), y=upper_bound, text=f"UB ({btn_value}σ)", showarrow=False,
    #                    font=dict(color="green", size=10), xanchor="right", yanchor="bottom",
    #                    row=row_idx, col=col_idx)

    # fig.add_shape(type="line", x0=min(x_val), x1=max(x_val), y0=mean, y1=mean,
    #               line=dict(color="magenta", width=1.5), row=row_idx, col=col_idx, yref=yref, xref="x")
    # fig.add_annotation(x=max(x_val), y=mean, text="Mean", showarrow=False,
    #                    font=dict(color="magenta", size=10), xanchor="right", yanchor="bottom",
    #                    row=row_idx, col=col_idx)

    # fig.add_shape(type="line", x0=min(x_val), x1=max(x_val), y0=lower_bound, y1=lower_bound,
    #               line=dict(color="blue", width=1.5), row=row_idx, col=col_idx, yref=yref, xref="x")
    # fig.add_annotation(x=max(x_val), y=lower_bound, text=f"LB ({-btn_value}σ)", showarrow=False,
    #                    font=dict(color="blue", size=10), xanchor="right", yanchor="top",
    #                    row=row_idx, col=col_idx)

    # === YILLIK (SEGMENTAL) CHEGARALARNI CHIZISH (YOTIQ/GORIZONTAL) ===
    if has_segments:
        unique_groups = df['group'].dropna().unique()
        show_leg = True

        for g in unique_groups:
            group_mask = df['group'] == g
            if not group_mask.any():
                continue

            start_date = df.loc[group_mask, 'date'].min()
            end_date = df.loc[group_mask, 'date'].max()

            seg_u = df.loc[group_mask, 'seg_upper'].iloc[0]
            seg_l = df.loc[group_mask, 'seg_lower'].iloc[0]

            if not pd.isna(seg_u):
                fig.add_trace(go.Scatter(
                    x=[start_date, end_date],
                    y=[seg_u, seg_u],
                    mode='lines',
                    line=dict(color='rgba(0,0,0,0.7)', width=3),
                    name=f"Yillik UB ({segment_years}y)",
                    legendgroup="seg_ub",
                    showlegend=show_leg,
                    hoverinfo='skip'
                ), row=row_idx, col=col_idx)

            if not pd.isna(seg_l):
                fig.add_trace(go.Scatter(
                    x=[start_date, end_date],
                    y=[seg_l, seg_l],
                    mode='lines',
                    line=dict(color='rgba(0,0,0,0.7)', width=3),
                    name=f"Yillik LB ({segment_years}y)",
                    legendgroup="seg_lb",
                    showlegend=show_leg,
                    hoverinfo='skip'
                ), row=row_idx, col=col_idx)

            show_leg = False

    # Asosiy ma'lumotlar grafigi
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

    # ==================== ANOMALIYA SEGMENTLARINI YIG'ISH VA CHIZISH ====================
    # Xatoni oldini olish: has_segments yo'q bo'lsa global chegara bilan ishlash
    if has_segments and 'seg_upper' in df.columns:
        seg_upper_list = df['seg_upper'].tolist()
        seg_lower_list = df['seg_lower'].tolist()
    else:
        seg_upper_list = [upper_bound] * len(x_val)
        seg_lower_list = [lower_bound] * len(x_val)

    anomaly_segments = []
    current_anomalous_segment_x = []
    current_anomalous_segment_y = []
    is_anomalous_prev = False

    for i in range(len(x_val)):
        x_curr, y_curr = x_val[i], y_val[i]
        is_anomalous_curr = is_anom_list[i]

        # Joriy nuqta uchun eng qat'iy chegarani aniqlash (Dinamik kesishish uchun)
        su_curr = seg_upper_list[i]
        sl_curr = seg_lower_list[i]
        eff_upper_curr = max(upper_bound, su_curr) if not np.isnan(su_curr) else upper_bound
        eff_lower_curr = min(lower_bound, sl_curr) if not np.isnan(sl_curr) else lower_bound

        if i == 0:
            if is_anomalous_curr:
                current_anomalous_segment_x.append(x_curr)
                current_anomalous_segment_y.append(y_curr)
            is_anomalous_prev = is_anomalous_curr
            continue

        x_prev, y_prev = x_val[i - 1], y_val[i - 1]

        # Oldingi nuqta uchun eng qat'iy chegara
        su_prev = seg_upper_list[i - 1]
        sl_prev = seg_lower_list[i - 1]
        eff_upper_prev = max(upper_bound, su_prev) if not np.isnan(su_prev) else upper_bound
        eff_lower_prev = min(lower_bound, sl_prev) if not np.isnan(sl_prev) else lower_bound

        intersect_x = None
        intersect_y = None

        if is_anomalous_curr != is_anomalous_prev:
            # Yuqori chegara bilan kesishish
            if (y_prev <= eff_upper_prev and y_curr > eff_upper_curr) or \
                    (y_curr <= eff_upper_curr and y_prev > eff_upper_prev):

                avg_bound = (eff_upper_curr + eff_upper_prev) / 2
                if abs(y_curr - y_prev) > 1e-9:
                    ratio = (avg_bound - y_prev) / (y_curr - y_prev)
                    if 0 <= ratio <= 1:
                        intersect_x = x_prev + (x_curr - x_prev) * ratio
                        intersect_y = avg_bound

            # Pastki chegara bilan kesishish
            if (y_prev >= eff_lower_prev and y_curr < eff_lower_curr) or \
                    (y_curr >= eff_lower_curr and y_prev < eff_lower_prev):

                avg_bound = (eff_lower_curr + eff_lower_prev) / 2
                if abs(y_curr - y_prev) > 1e-9:
                    ratio = (avg_bound - y_prev) / (y_curr - y_prev)
                    if intersect_x is None and 0 <= ratio <= 1:
                        intersect_x = x_prev + (x_curr - x_prev) * ratio
                        intersect_y = avg_bound

        # Qizil qismlarni yig'ish va grafikka chizish
        if is_anomalous_curr != is_anomalous_prev:
            if is_anomalous_prev and len(current_anomalous_segment_x) > 0:
                if intersect_x is not None:
                    current_anomalous_segment_x.append(intersect_x)
                    current_anomalous_segment_y.append(intersect_y)

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

    # Oxirgi qolib ketgan anomaliya segmentini yopish va chizish
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

    return y_all_values


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
            legend_name = f"{MAIN_MAGNITUDE_COLUMN} Magnituda (≥{min_mag}): Faqat Mb"
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
        gridcolor="#eaeded",
        #griddash="dot",
        row=row_index,
        col=col_index,
        secondary_y=False,
    )
    fig.update_yaxes(
        showgrid=False,
        showline=True,
        linewidth=1,
        linecolor='black',
        # gridwidth=0.15,
        # gridcolor="gray",
        # griddash="dot",
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
    """
    ✅ CACHED: Birinchi marta disk'dan, keyin memory'dan
    """
    global _cached_cracks

    if _cached_cracks is not None:
        logger.info("✅ Cracks loaded from memory cache")
        return _cached_cracks

    with _cache_lock:
        # Double-check
        if _cached_cracks is not None:
            return _cached_cracks

        shapefile_paths = get_all_shapefiles()

        if not shapefile_paths:
            logger.warning("No shapefiles found")
            return None

        all_cracks = []

        for shapefile_path in shapefile_paths:
            try:
                gdf = gpd.read_file(shapefile_path)

                if gdf.crs and gdf.crs != 'EPSG:4326':
                    gdf = gdf.to_crs('EPSG:4326')

                gdf['source_file'] = os.path.basename(shapefile_path)
                all_cracks.append(gdf)

                logger.info(f"Loaded: {os.path.basename(shapefile_path)} - {len(gdf)} features")

            except Exception as e:
                logger.error(f"Error loading {shapefile_path}: {e}")
                continue

        if not all_cracks:
            return None

        try:
            combined_gdf = gpd.GeoDataFrame(pd.concat(all_cracks, ignore_index=True))
            _cached_cracks = combined_gdf

            logger.info(f"✅ Cached {len(combined_gdf)} cracks in memory")
            return combined_gdf

        except Exception as e:
            logger.error(f"Error combining shapefiles: {e}")
            return None


def load_seismogenic_zones():
    """
    ✅ CACHED: Birinchi marta disk'dan, keyin memory'dan
    """
    global _cached_seismogenic_zones

    if _cached_seismogenic_zones is not None:
        logger.info("✅ Seismogenic zones loaded from memory cache")
        return _cached_seismogenic_zones

    with _cache_lock:
        # Double-check
        if _cached_seismogenic_zones is not None:
            return _cached_seismogenic_zones

        shapefile_paths = get_seismogenic_shapefiles()

        if not shapefile_paths:
            logger.warning("No seismogenic shapefiles found")
            return None

        all_zones = []

        for shapefile_path in shapefile_paths:
            try:
                filename = os.path.basename(shapefile_path)
                if 'SEYSMOGEN' not in filename.upper() and 'SEISMOGEN' not in filename.upper() and 'EXPORT' not in filename.upper():
                    continue

                gdf = gpd.read_file(shapefile_path)

                if gdf.crs is None:
                    gdf.set_crs('EPSG:4326', inplace=True)
                elif gdf.crs.to_string() != 'EPSG:4326':
                    gdf = gdf.to_crs('EPSG:4326')

                if gdf.empty or 'geometry' not in gdf.columns or gdf.geometry.isnull().all():
                    logger.warning(f"{filename} empty geometry")
                    continue

                gdf['source_file'] = filename
                all_zones.append(gdf)

                logger.info(f"Loaded seismogenic zone: {filename} - {len(gdf)} features")

            except Exception as e:
                logger.error(f"Error loading {shapefile_path}: {e}")
                continue

        if not all_zones:
            logger.warning("No seismogenic zones loaded")
            return None

        try:
            combined_gdf = gpd.GeoDataFrame(pd.concat(all_zones, ignore_index=True))
            _cached_seismogenic_zones = combined_gdf

            logger.info(f"✅ Cached {len(combined_gdf)} seismogenic zones in memory")
            return combined_gdf

        except Exception as e:
            logger.error(f"Error combining seismogenic zones: {e}")
            return None


def clear_shapefile_cache():
    """
    Cache'ni tozalash (agar shapefile'lar yangilansa)
    """
    global _cached_cracks, _cached_seismogenic_zones

    _cached_cracks = None
    _cached_seismogenic_zones = None

    logger.info("✅ Shapefile cache cleared")

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


# seismos_app/views.py

def get_well_detailed_info(well_name):
    """
    ✅ UNIVERSAL: String yoki List qabul qiladi
    """
    # List bo'lsa - batch loading
    if isinstance(well_name, list):
        return _get_multiple_wells_info(well_name)

    # String bo'lsa - single query
    if isinstance(well_name, str):
        return _get_single_well_info(well_name)

    # Invalid input
    return _get_default_well_info(str(well_name) if well_name else "Unknown")


def _get_single_well_info(well_name: str) -> Dict:
    base_key = f'well_info_{well_name.strip()}'
    cached = cache.get(base_key)
    if cached:
        return cached

    try:
        from .models import Malumot
        malumot = Malumot.objects.filter(nomi=well_name.strip()).first()

        if not malumot:
            result = _get_default_well_info(well_name)
            cache.set(base_key, result, 3600)
            return result

        # ✅ BLOB length bilan "version" qilish
        blob = getattr(malumot, "mineralizatsiya", None)
        blob_len = 0
        if blob:
            if isinstance(blob, memoryview):
                blob_len = len(blob.tobytes())
            elif isinstance(blob, (bytes, bytearray)):
                blob_len = len(blob)

        cache_key = f"{base_key}_imglen_{blob_len}"

        cached2 = cache.get(cache_key)
        if cached2:
            return cached2

        result = _build_well_info_dict(malumot)

        # eski base_key ni ham saqlamaymiz, faqat version key
        cache.set(cache_key, result, 3600)
        return result

    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        return _get_default_well_info(well_name)


def _get_multiple_wells_info(well_names: List[str]) -> Dict[str, Dict]:
    """
    ✅ BATCH LOADING: Bir marta barcha ma'lumotlarni olish
    """
    if not well_names:
        return {}

    cache_key = f'all_wells_info_{hash(tuple(sorted(well_names)))}'
    cached = cache.get(cache_key)

    if cached:
        logger.info(f"✅ Cache hit: {len(cached)} wells")
        return cached

    try:
        from .models import Malumot

        # ✅ FAQAT 1 TA QUERY!
        malumotlar = Malumot.objects.filter(nomi__in=well_names).select_related()

        result = {}

        for malumot in malumotlar:
            result[malumot.nomi.strip()] = _build_well_info_dict(malumot)

        # Topilmaganlari uchun default
        for well_name in well_names:
            if well_name not in result:
                result[well_name] = _get_default_well_info(well_name)

        cache.set(cache_key, result, 3600)
        logger.info(f"✅ Loaded {len(result)} wells in ONE query")

        return result

    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        return {name: _get_default_well_info(name) for name in well_names}


import base64

def _build_well_info_dict(malumot) -> Dict:
    result = {
        "nomi": malumot.nomi or "Ma'lumot yo'q",
        "quduq_turi": malumot.quduq_turi or "Ma'lumot yo'q",
        "suv_qatlami": malumot.suv_qatlami or "Ma'lumot yo'q",
        "chuqurlik": malumot.chuqurlik or "Ma'lumot yo'q",
        "seysmotektonik_holat": malumot.seysmotektonik_holat or "Ma'lumot yo'q",
        "strategrafik_taqsimoti": malumot.strategrafik_taqsimoti or "Ma'lumot yo'q",
        "litologik_tarkibi": malumot.litologik_tarkibi or "Ma'lumot yo'q",
        "mineralizatsiya_base64": None,
    }

    # ✅ DB'da MEDIUMBLOB: malumot.mineralizatsiya -> bytes
    blob = getattr(malumot, "mineralizatsiya", None)
    if blob:
        try:
            if isinstance(blob, memoryview):
                blob = blob.tobytes()

            if isinstance(blob, (bytes, bytearray)) and len(blob) > 0:
                encoded = base64.b64encode(blob).decode("utf-8")

                mime_type = "image/png"


                result["mineralizatsiya_base64"] = f"data:{mime_type};base64,{encoded}"

        except Exception as e:
            logger.error(f"Image blob encode error ({malumot.nomi}): {e}", exc_info=True)

    return result


def _get_default_well_info(well_name: str) -> Dict:
    """Default ma'lumot"""
    return {
        "nomi": well_name,
        "quduq_turi": "Ma'lumot yo'q",
        "suv_qatlami": "Ma'lumot yo'q",
        "chuqurlik": "Ma'lumot yo'q",
        "seysmotektonik_holat": "Ma'lumot yo'q",
        "strategrafik_taqsimoti": "Ma'lumot yo'q",
        "litologik_tarkibi": "Ma'lumot yo'q",
        "mineralizatsiya_base64": None,
    }


def add_map_data_folium(selected_keys, well_coords, earthquake_data, min_mag, min_mlgr, filter_mode='mlgr'):
    """
    Folium yordamida interaktiv xarita yaratadi
    HAR BIR TANLANGAN SKVAJINA O'Z RANGIDA VA AYLANMALARI HAM O'SHA RANGDA
    """
    all_wells = get_all_wells_coordinates()

    selected_well_names = set()
    selected_well_names_list = []  # Rang uchun tartiblangan ro'yxat
    filtered_earthquakes_by_well = []

    all_well_names_on_map = list(all_wells.keys())
    wells_info_dict = get_well_detailed_info(all_well_names_on_map)
    logger.info(f"Loaded {len(wells_info_dict)} wells info in ONE query")
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
            well_info = wells_info_dict.get(well_name, {
                "nomi": well_name,
                "quduq_turi": "Ma'lumot yo'q",
                "suv_qatlami": "Ma'lumot yo'q",
                "chuqurlik": "Ma'lumot yo'q",
                "seysmotektonik_holat": "Ma'lumot yo'q",
                "strategrafik_taqsimoti": "Ma'lumot yo'q",
                "litologik_tarkibi": "Ma'lumot yo'q",
                "mineralizatsiya_base64": None,
            })

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

            well_info = wells_info_dict.get(skvajina, {
                "nomi": skvajina,
                "quduq_turi": "Ma'lumot yo'q",
                "suv_qatlami": "Ma'lumot yo'q",
                "chuqurlik": "Ma'lumot yo'q",
                "seysmotektonik_holat": "Ma'lumot yo'q",
                "strategrafik_taqsimoti": "Ma'lumot yo'q",
                "litologik_tarkibi": "Ma'lumot yo'q",
                "mineralizatsiya_base64": None,
            })

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

                        // Mb rejimida aylanalar darhol ko'rinadi
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
            distance_val = row.get("R(km)", "Noma'lum")
            mlgr_val = row.get("M/lgR", "Noma'lum")
            depth_val = row.get("Depth", "Noma'lum")

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
                        Masofa (km): {distance_val:.1f} km<br>
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
                                    <td style="padding: 5px; border: 1px solid #dee2e6;">{well_info.get('litologik_tarkibi', 'Ma\'lumot yo\'q')}</td>
                                </tr>
                                {mineralizatsiya_html}
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
                date_val = "Noma'lum"
            if isinstance(date_val, (pd.Timestamp, datetime.datetime)):
                date_val = date_val.strftime("%d.%m.%Y")
            distance_val = row.get("R(km)", "Noma'lum")
            mlgr_val = row.get("M/lgR", "Noma'lum")
            depth_val = row.get("Depth", "Noma'lum")

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
                        Masofa (km): {distance_val:.1f} km<br>
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
    #session va input validatsiyasi
    context = initialize_context(request)

    if 'error' in context:
        return render(request, "seismos_app/results1.html")

    #CACHED
    try:
        lst_stansiya, well_coords = fetch_data()

        page_title = generate_page_title(context['selected_keys'])
        context['page_title'] = page_title
        context['selected_well_names'] = extract_well_names(context['selected_keys'])

        #Sana filtrlarini tekshirish
        date_range = validate_and_parse_dates(
            context['filter_start_date'],
            context['filter_end_date']
        )

        if isinstance(date_range, dict) and 'error' in date_range:
            context.update(date_range)
            return render(request, 'seismos_app/results1.html', context)

        user_start_date, user_end_date, x_axis_start, x_axis_end = date_range

        #Zilzila ma'lumotlarini olish
        filtered_earthquakes_df = fetch_and_filter_earthquakes(
            min_mag=context['min_mag'],
            start_date = user_start_date,
            end_date = user_end_date
        )

        #Grafiklar yaratish
        graphs_list = []
        graph_data = []


        if not context['hide_graphs']:
            graphs_list, graph_data = generate_all_graphs(
                selected_keys = context['selected_keys'],
                selected_params = context['selected_params'],
                lst_stansiya = lst_stansiya,
                well_coords = well_coords,
                filtered_earthquakes_df = filtered_earthquakes_df,
                user_start_date = user_start_date,
                user_end_date = user_end_date,
                x_axis_start = x_axis_start,
                x_axis_end = x_axis_end,
                median_window = context['median_window'],
                min_mag = context['min_mag'],
                btn_value = context['btn_value'],
                min_mlgr = context['min_mlgr'],
                filter_mode = context['filter_mode'],
                segment_years = context['segment_years']
            )

            if not graphs_list:
                logger.warning(" No graphs were created!")
            else:
                logger.info(f"{len(graphs_list)} graphs created successfully")

        #Xarita yaratish
        folium_map_html = None

        if not context['hide_map']:
            folium_map_html = generate_folium_map(
                selected_keys = context['selected_keys'],
                well_coords = well_coords,
                filtered_earthquakes_df = filtered_earthquakes_df,
                min_mag = context['min_mag'],
                min_mlgr = context['min_mlgr'],
                filter_mode = context['filter_mode']
            )

        #Contextni to'ldirish
        context.update({
            'wells': lst_stansiya.keys(),
            'graphs_list':graphs_list,
            'folium_map': folium_map_html,
            'show_graphs': not context['hide_graphs'],
            'show_map': not context['hide_map'],


        })

        #Data diapozoni
        if graph_data:
            all_dates = [d for item in graph_data for d in item[0] if d]
            if all_dates:
                context["data_min_date"] = min(pd.to_datetime(all_dates)).strftime('%Y-%m-%d')
                context["data_max_date"] = max(pd.to_datetime(all_dates)).strftime('%Y-%m-%d')
        return render(request, 'seismos_app/results1.html', context)

    except Exception as e:
        logger.error(f"Results view error: {e}", exc_info=True)
        return render(request, 'seismos_app/results1.html',{
            "error":"Tizimda kutilmagan xatolik yuz berdi"
        })

def initialize_context(request) -> Dict:
    """ Session va POST malu'motlarini validatsiya qilish"""

    # ORM barcha ma'lumotlarni olish
    lst_stansiya,_ = fetch_data()
    all_params = sorted(list(set(sum(DEFAULT_ELEMENTS_GROUPS.values(),[]))))

    if request.method == "POST":
        selected_keys = request.POST.getlist("wells")
        selected_params = request.POST.getlist("params") or all_params
        filter_mode  = request.POST.get("filter_mode", "mlgr")

        #Ko'rsatish nazorati
        hide_map = request.POST.get('hide_map') == '1'
        hide_graphs = request.POST.get('hide_graphs') == '1'

        #Ikkisi ham yoqilgan bo'lsa rad etish
        if hide_map and hide_graphs:
            hide_map = False
            hide_graphs = False

        #Raqamli parametrlar
        try:
            min_mag = float(request.POST.get("min_mag", 4.0))
            btn_value = float(request.POST.get("sigma", 2.0))
            min_mlgr = float(request.POST.get("min_mlgr", 2.5))
        except (ValueError,TypeError):
            min_mag, btn_value, min_mlgr = 4.0, 2.0, 2.5

        #Sana filtrlari
        filter_start_date = request.POST.get("start_date", "").strip()
        filter_end_date = request.POST.get("end_date", "").strip()

        #Median window
        median_window_raw = request.POST.get("median_window", "").strip()
        try:
            median_window = int(median_window_raw) if median_window_raw else None
        except ValueError:
            median_window = None

        #yillik sigma kiritish uchun
        segment_years_raw = request.POST.get("segment_years", "").strip()
        try:
            segment_years = int(segment_years_raw) if segment_years_raw else None
        except ValueError:
            segment_years = None

        #Sessionga saqlash
        request.session.update({
            'selected_keys':selected_keys,
            'selected_params': selected_params,
            'min_mag': min_mag,
            'btn_value': btn_value,
            'min_mlgr':min_mlgr,
            'filter_start_date':filter_start_date,
            'filter_end_date':filter_end_date,
            'median_window':median_window,
            'segment_years': segment_years,
            'use_catalog':True,
            'filter_mode':filter_mode,
            'hide_map':hide_map,
            'hide_graphs':hide_graphs,
        })

    else:
        #GET request sessiondan olish
        selected_keys = request.session.get("selected_keys", [])
        selected_params = request.session.get("selected_params", all_params)
        min_mag = request.session.get("min_mag", 4.0)
        btn_value = request.session.get("btn_value", 2.0)
        min_mlgr = request.session.get("min_mlgr",2.5)
        filter_start_date = request.session.get("filter_start_date", "")
        filter_end_date = request.session.get("filter_end_date", "")
        segment_years = request.session.get("segment_years", None)
        use_catalog = request.session.get("use_catalog", False)
        median_window = request.session.get("median_window", None)
        filter_mode = request.session.get("filter_mode", "mlgr")
        hide_map = request.session.get("hide_map", False)  # ✅ YANGI
        hide_graphs = request.session.get("hide_graphs", False)  # ✅ YANGI

    #Validatsiya
    if not selected_keys:
        return{
            "error":"Skvajinalar tanlanmagan iltimos avval skvajina tanlang",
            "wells": lst_stansiya.keys(),
            "params": all_params,
            "median_values": [ 3, 5 ,7, 15, 31, 91, 183, 365, 731 ],
        }

    #Context qaytarish
    return {
        'selected_keys':selected_keys,
        'selected_params':selected_params,
        'min_mag':min_mag,
        'btn_value':btn_value,
        'min_mlgr':min_mlgr,
        'filter_start_date': filter_start_date,
        'filter_end_date':filter_end_date,
        'median_window': median_window,
        'segment_years': segment_years,
        'filter_mode': filter_mode,
        'hide_map': hide_map,
        'hide_graphs': hide_graphs,
        'params':all_params,
        'median_values':[3,5,7,15,31,91,183,365,731],
        #Forma uchun joriy qiymatlar
        'current_min_mag':min_mag,
        'current_sigma':btn_value,
        'current_min_mlgr':min_mlgr,
        'current_start_date':filter_start_date,
        'current_end_date':filter_end_date,
        'current_median_window': median_window or "",
        'segment_years': segment_years or "",
        'current_filter_mode': filter_mode,
        'current_hide_map':hide_map,
        'current_hide_graphs':hide_graphs,
        'select_wells':selected_keys,
    }

def generate_page_title(selected_keys: List[str]) -> str:
    """ Page title yaratish """
    well_names = extract_well_names(selected_keys)

    if not well_names:
        return  "Seysmik Tahlil"
    elif len(well_names) == 1:
        return f"{well_names[0]} -Seysmik Tahlil"
    elif len(well_names) <=3:
        return f"{', '.join(well_names)} -Seysmik Tahlil"
    else:
        return f"{', '.join(well_names[:3])} va boshqalar - Seysmik Tahlil"


def extract_well_names(selected_keys: List[str]) -> List[str]:
    """selected keys dan well nomlarini ajratib olish"""
    well_names = []
    for key in selected_keys:
        if " | " in key:
            _, well_name = key.split(" | ",1)
            well_names.append(well_name)
    return well_names

def validate_and_parse_dates(
        filter_start_date: str,
        filter_end_date:str,
) -> Tuple:

    default_start_date = pd.to_datetime("1984-01-01")
    today = pd.to_datetime("today").normalize()


    user_start_date = None
    user_end_date = None

    if filter_start_date:
        try:
            user_start_date = pd.to_datetime(filter_start_date)
            user_end_date = pd.to_datetime(filter_end_date)

            if user_start_date > user_end_date:
                return {'error': "Boshlang'ich sana oxirgi sanadan katta bo'lmasligi kerak"}

        except (ValueError,TypeError) as e:
            logger.warning(f"Sana filtri xatosi: {e}")
            return {'error':f"Noto'g'ri sana formati: {e}"}

    # X o'qi uchun sanalar
    x_axis_start = user_start_date if user_start_date else default_start_date
    x_axis_end = user_end_date if user_end_date else today + relativedelta(months=2)

    return user_start_date, user_end_date, x_axis_start, x_axis_end

def fetch_and_filter_earthquakes(
        min_mag: float,
        start_date: Optional[pd.Timestamp] = None,
        end_date: Optional[pd.Timestamp] = None
) -> pd.DataFrame:
    """
    ✅ OPTIMIZED: dtype va vectorization
    """
    cache_key = f'earthquakes_{min_mag}_{start_date}_{end_date}'
    cached = cache.get(cache_key)

    if cached is not None:
        logger.info("✅ Earthquakes from cache")
        return cached

    try:
        queryset = Catalog.objects.filter(Mb__gte=min_mag)

        if start_date is None:
            start_date = pd.to_datetime("1984-01-01")

        queryset = queryset.filter(Event_date__gte=start_date.date())

        if end_date:
            queryset = queryset.filter(Event_date__lte=end_date.date())

        earthquakes = queryset.values(
            'Event_date', 'Event_time', 'Latitude', 'Longitude', 'Depth', 'Mb'
        ).order_by('-Event_date', '-Event_time')

        # ✅ OPTIMIZED: dtype specification
        df = pd.DataFrame(list(earthquakes))

        if df.empty:
            cache.set(cache_key, df, 1800)
            return df

        # ✅ Optimize dtypes
        df = df.astype({
            'Latitude': 'float32',  # float64 → float32 (2x kam memory)
            'Longitude': 'float32',
            'Depth': 'float32',
            'Mb': 'float32',
        })

        # ✅ Time processing (vectorized)
        if TIME_COLUMN in df.columns:
            df[TIME_COLUMN] = df[TIME_COLUMN].apply(
                lambda x: x.strftime('%H:%M:%S') if hasattr(x, 'strftime') else str(x)
            )

        # ✅ Combined datetime (vectorized)
        df["combined_datetime"] = pd.to_datetime(
            df[DATE_COLUMN].astype(str) + " " + df[TIME_COLUMN].astype(str),
            format="%Y-%m-%d %H:%M:%S",
            errors="coerce"
        )
        df.dropna(subset=["combined_datetime"], inplace=True)

        logger.info(f"✅ Fetched {len(df)} earthquakes")
        cache.set(cache_key, df, 1800)

        return df

    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        return pd.DataFrame()


def generate_all_graphs(
        selected_keys: List[str],
        selected_params: List[str],
        lst_stansiya: Dict,
        well_coords: Dict,
        filtered_earthquakes_df: pd.DataFrame,
        user_start_date: Optional[pd.Timestamp],
        user_end_date: Optional[pd.Timestamp],
        x_axis_start: pd.Timestamp,
        x_axis_end: pd.Timestamp,
        median_window: Optional[int],
        min_mag: float,
        btn_value: float,
        min_mlgr: float,
        filter_mode: str,
        segment_years: Optional[int] = None
) -> Tuple[List[Dict], List[Tuple]]:
    """
    ✅ OPTIMIZED: Faqat mavjud ma'lumotli grafiklarni yaratish
    """
    graphs_list = []
    graph_data = []

    default_start_date = pd.to_datetime("1984-01-01")

    try:
        from decouple import config as env_config
        from sqlalchemy import create_engine, text

        config = {
            'db': env_config('DB_NAME'),
            'user': env_config('DB_USER'),
            'psw': env_config('DB_PASSWORD'),
            'ip': env_config('DB_HOST', default='localhost')
        }
        engine = create_engine(
            f"mysql+mysqlconnector://{config['user']}:{config['psw']}@{config['ip']}/{config['db']}"
        )
        conn = engine.connect()

        # ============================================
        # MA'LUMOT YIG'ISH
        # ============================================
        for key in selected_keys:
            for param in selected_params:
                ssdi_id = lst_stansiya.get(key, {}).get(param)
                if not ssdi_id:
                    logger.warning(f"⚠️ ssdi_id topilmadi: {key} - {param}")
                    continue

                # Query
                query = text(f"SELECT date, `{ssdi_id}` FROM alldata WHERE `{ssdi_id}` IS NOT NULL")

                try:
                    data = conn.execute(query).fetchall()
                except Exception as e:
                    logger.error(f"❌ Query error {key} - {param}: {e}")
                    continue

                # ✅ YANGI: Ma'lumot bo'sh bo'lsa, o'tkazib yuborish
                if not data or len(data) == 0:
                    logger.warning(f"⚠️ No data found for {key} - {param}")
                    continue

                # DataFrame yaratish
                df_temp = pd.DataFrame(data, columns=['date', 'value'])
                df_temp['date'] = pd.to_datetime(df_temp['date'], errors='coerce')
                df_temp['value'] = pd.to_numeric(df_temp['value'], errors='coerce')
                df_temp.dropna(subset=['date', 'value'], inplace=True)

                # ✅ YANGI: Tozalagandan keyin ham bo'sh bo'lsa
                if df_temp.empty:
                    logger.warning(f"⚠️ Empty DataFrame after cleaning: {key} - {param}")
                    continue

                # Sana filtri
                if user_start_date and user_end_date:
                    df_temp = df_temp[
                        (df_temp['date'] >= user_start_date) &
                        (df_temp['date'] <= user_end_date)
                        ].copy()
                else:
                    df_temp = df_temp[df_temp['date'] >= default_start_date].copy()

                # ✅ YANGI: Filtrlashdan keyin ham bo'sh bo'lsa
                if df_temp.empty:
                    logger.warning(f"⚠️ No data in date range for {key} - {param}")
                    continue

                x_val = df_temp['date'].tolist()
                y_val = df_temp['value'].tolist()

                # ✅ YANGI: y_val bo'sh yoki barcha qiymatlar NaN bo'lsa
                if not y_val or all(pd.isna(y_val)):
                    logger.warning(f"⚠️ All values are NaN: {key} - {param}")
                    continue

                # ✅ YANGI: Kamida 2 ta nuqta kerak grafik uchun
                if len(y_val) < 2:
                    logger.warning(f"⚠️ Not enough data points ({len(y_val)}): {key} - {param}")
                    continue

                # Median window processing
                if median_window and median_window > 0:
                    df_temp_med = pd.DataFrame({'date': x_val, 'value': y_val})
                    df_temp_med['date'] = pd.to_datetime(df_temp_med['date'])

                    df_daily_median = (
                        df_temp_med.groupby(df_temp_med['date'].dt.date)['value']
                        .median()
                        .reset_index()
                        .rename(columns={'value': 'daily_median'})
                    )

                    # ✅ FIXED: No space!
                    df_daily_median['rolling_median'] = (
                        df_daily_median['daily_median']
                        .rolling(window=median_window, min_periods=1, center=True)
                        .median()
                    )

                    x_val = pd.to_datetime(df_daily_median['date']).tolist()
                    y_val = df_daily_median['rolling_median'].tolist()

                    # ✅ YANGI: Median'dan keyin ham tekshirish
                    if not y_val or all(pd.isna(y_val)):
                        logger.warning(f"⚠️ All median values are NaN: {key} - {param}")
                        continue

                # Mean va sigma
                mean, sigma = np.mean(y_val), np.std(y_val)

                # ✅ YANGI: Sigma 0 bo'lsa yoki NaN bo'lsa
                if sigma == 0 or np.isnan(sigma) or np.isnan(mean):
                    logger.warning(f"⚠️ Invalid statistics (mean={mean}, sigma={sigma}): {key} - {param}")
                    continue

                _, skvajina = key.split(" | ")

                # ✅ YANGI: Muvaffaqiyatli ma'lumotni saqlash
                graph_data.append((x_val, y_val, mean, sigma, param, key, skvajina))
                logger.info(f"✅ Added graph data: {key} - {param} ({len(y_val)} points)")

        conn.close()
        engine.dispose()

        # ============================================
        # GRAFIKLAR YARATISH
        # ============================================
        if not graph_data:
            logger.warning("⚠️ No valid graph data found!")
            return [], []

        logger.info(f"✅ Creating {len(graph_data)} graphs...")

        delta = timedelta(days=15)
        color_pool = generate_colors(len(graph_data))

        for idx, (x, y, mean, sigma, param, key, skv) in enumerate(graph_data):
            try:
                fig = create_single_graph(
                    x=x,
                    y=y,
                    mean=mean,
                    sigma=sigma,
                    param=param,
                    key=key,
                    skv=skv,
                    color=color_pool[idx],
                    filtered_earthquakes_df=filtered_earthquakes_df,
                    well_coords=well_coords,
                    x_axis_start=x_axis_start,
                    x_axis_end=x_axis_end,
                    delta=delta,
                    btn_value=btn_value,
                    min_mag=min_mag,
                    min_mlgr=min_mlgr,
                    filter_mode=filter_mode,
                    user_start_date=user_start_date,
                    user_end_date=user_end_date,
                    segment_years=segment_years
                )

                # ✅ YANGI: Grafik yaratilmasa, o'tkazib yuborish
                if fig is None:
                    logger.warning(f"⚠️ Failed to create graph: {key} - {param}")
                    continue

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
                    'title': f"{key} - {param}",
                    'div_id': div_id,
                    'filename': filename
                })

                logger.info(f"✅ Graph created: {key} - {param}")

            except Exception as e:
                logger.error(f"❌ Error creating graph {key} - {param}: {e}", exc_info=True)
                continue

        logger.info(f"✅ Successfully created {len(graphs_list)} graphs")

    except Exception as e:
        logger.error(f"❌ Error in generate_all_graphs: {e}", exc_info=True)

    return graphs_list, graph_data


def create_single_graph(
        x: List,
        y: List,
        mean: float,
        sigma: float,
        param: str,
        key: str,
        skv: str,
        color: str,
        filtered_earthquakes_df: pd.DataFrame,
        well_coords: Dict,
        x_axis_start: pd.Timestamp,
        x_axis_end: pd.Timestamp,
        delta: timedelta,
        btn_value: float,
        min_mag: float,
        min_mlgr: float,
        filter_mode: str,
        user_start_date: Optional[pd.Timestamp],
        user_end_date: Optional[pd.Timestamp],
            segment_years: Optional[int] = None
) -> Optional[go.Figure]:
    """
    ✅ SAFE: None qaytaradi agar grafik yaratish mumkin bo'lmasa
    """
    try:
        # ✅ YANGI: Input validation
        if not x or not y:
            logger.warning(f"⚠️ Empty x or y data for {key} - {param}")
            return None

        if len(x) != len(y):
            logger.warning(f"⚠️ x and y length mismatch: {len(x)} vs {len(y)}")
            return None

        if len(x) < 2:
            logger.warning(f"⚠️ Not enough data points: {len(x)}")
            return None

        fig = make_subplots(specs=[[{"secondary_y": True}]])

        row, col = 1, 1

        # Asosiy ma'lumotlarni chizish
        try:
            y_all = plot_data_with_anomalies(
                fig, x, y, mean, sigma, btn_value, row, col,
                color, param, key, segment_years,
            )
        except Exception as e:
            logger.error(f"❌ Error in plot_data_with_anomalies: {e}")
            return None

        # Y o'qi sozlamalari
        if y_all and len(y_all) > 0:
            y_min = min(y_all)
            y_max = max(y_all)

            # ✅ YANGI: Division by zero check
            if y_min == y_max:
                y_range = [y_min - 1, y_max + 1]
            else:
                y_range = [y_min * 0.9, y_max * 1.1]

            fig.update_yaxes(
                title_text=f"{param} Qiymati",
                range=y_range,
                showgrid=True,          # Yengil chiziq qoldirish
                gridcolor="#f0f0f0",    # Juda ochiq kulrang (deyarli ko'rinmaydi)
                showline=True,          # O'q chekka chizig'i
                linewidth=1,
                linecolor='black',
                row=row, col=col,
                secondary_y=False
            )

        # Zilzilalarni qo'shish
        lat, lon = well_coords.get(skv, (0, 0))

        if not filtered_earthquakes_df.empty and lat and lon:
            try:
                draw_magnitude_values(
                    fig=fig,
                    original_df=filtered_earthquakes_df,
                    row_index=row,
                    col_index=col,
                    min_mag=min_mag, well_lat=lat, well_lon=lon, min_mlgr=min_mlgr, filter_mode=filter_mode
                )
            except Exception as e:
                logger.warning(f" Could not add earthquakes: {e}")
                # Grafikni baribir qaytarish (zilzilalarsiz)

        # X o'qi sozlamalari
        fig.update_xaxes(
            range=[x_axis_start - delta, x_axis_end + delta],
            type="date",
            showgrid=False,
            showline=True,
            linewidth=1,
            linecolor='black',
            griddash="dot",
            row=row, col=col
        )

        # Layout
        graph_title = f"{key} - {param}"
        title_date = ""

        if user_start_date and user_end_date:
            start_str = user_start_date.strftime('%d.%m.%Y')
            end_str = user_end_date.strftime('%d.%m.%Y')
            title_date = f" ({start_str} - {end_str})"

        fig.update_layout(
            title_text=f"{graph_title}{title_date}",
            height=500,
            autosize=True,
            showlegend=False,
            plot_bgcolor="white",
            paper_bgcolor="white",
            hovermode="x unified",
            hoverdistance=1,
            margin=dict(l=60, r=60, t=80, b=60),
            legend=dict(x=0.01, y=0.99, bgcolor="rgba(255,255,255,0.9)")
        )

        return fig

    except Exception as e:
        logger.error(f"❌ Error creating single graph: {e}", exc_info=True)
        return None

def generate_folium_map(
        selected_keys:List[str],
        well_coords:Dict,
        filtered_earthquakes_df: pd.DataFrame,
        min_mag:float,
        min_mlgr:float,
        filter_mode: str
) -> str:
    try:
        folium_map_html = add_map_data_folium(
            selected_keys=selected_keys,
            well_coords=well_coords,
            earthquake_data=filtered_earthquakes_df,
            min_mag=min_mag,
            min_mlgr=min_mlgr,
            filter_mode=filter_mode
        )
        return folium_map_html

    except Exception as e:
        logger.error(f"Error creating Folium map: {e}", exc_info=True)
        return "<p> Xarita yaratishda xato yuz berdi.</p>"


import atexit


def _cleanup_db_engine():
    """
    ✅ Process to'xtaganda engine'ni yopish
    """
    global _global_engine

    if _global_engine is not None:
        try:
            _global_engine.dispose()
            _global_engine = None
            logger.info("✅ DB engine closed on shutdown")
        except Exception as e:
            logger.error(f"❌ Error closing DB engine: {e}")


# ✅ Atexit ro'yxatdan o'tkazish
atexit.register(_cleanup_db_engine)

logger.info("✅ DB engine cleanup registered")

