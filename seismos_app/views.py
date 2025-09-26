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


from datetime import timedelta
from math import pi, sin, cos, atan2, sqrt
from django.conf import settings
from django.shortcuts import render, redirect
from django.core.files.storage import FileSystemStorage
from sqlalchemy import create_engine, text, exc
from plotly.subplots import make_subplots


# Setup logging
logging.basicConfig(
    level=logging.INFO,
    filename="seismic_app.log",
    format="%(asctime)s - %(levelname)s - %(message)s",
)

# --- Constants ---
DATE_COLUMN = "Date"
TIME_COLUMN = "Time"
LATITUDE_COLUMN = "Latitude"
LONGITUDE_COLUMN = "Longitude"

MAIN_MAGNITUDE_COLUMN = "Mb"
SECONDARY_MAGNITUDE_COLUMN = "Ml"

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
    """Reads database configuration from user_info.json."""
    config_path = os.path.join(settings.BASE_DIR, "user_info.json")
    try:
        with open(config_path) as f:
            return json.load(f)
    except FileNotFoundError:
        logging.error(f"Configuration file not found at {config_path}")
        raise
    except json.JSONDecodeError:
        logging.error(f"Error decoding JSON from {config_path}. Check file format.")
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
):
    """
    Ma'lumotlarning butun chizig'ini chizadi va anomaliya qismlarini qizil rangda belgilaydi.
    """
    
    if isinstance(x_val, pd.Series):
        x_val = x_val.tolist()
    if isinstance(y_val, pd.Series):
        y_val = y_val.tolist()

    if len(x_val) != len(y_val):
        logging.error(f"x_val va y_val uzunliklari mos emas: {len(x_val)} vs {len(y_val)}")
        return [mean]
    

    upper_bound = mean + btn_value * sigma
    lower_bound = mean - btn_value * sigma

    y_all_values = list(y_val)
    y_all_values.extend([upper_bound, lower_bound, mean])

    yaxis_index = (row_idx - 1) * 1 + col_idx
    yref = "y" if yaxis_index == 1 else f"y{2 * row_idx - 1}"

    # UB (Upper Bound) chizig'i
    fig.add_shape(
        type="line",
        x0=min(x_val),
        x1=max(x_val),
        y0=upper_bound,
        y1=upper_bound,
        line=dict(
            color="green",
            width=1.5,
        ),
        row=row_idx,
        col=col_idx,
        yref=yref,
        xref="x",
    )
    fig.add_annotation(
        x=max(x_val),
        y=upper_bound,
        text=f"UB ({btn_value}σ)",
        showarrow=False,
        font=dict(color="green", size=10),
        xanchor="right",
        yanchor="bottom",
        row=row_idx,
        col=col_idx,
    )

    # MEAN chizig'i
    fig.add_shape(
        type="line",
        x0=min(x_val),
        x1=max(x_val),
        y0=mean,
        y1=mean,
        line=dict(
            color="magenta",
            width=1.5,
        ),
        row=row_idx,
        col=col_idx,
        yref=yref,
        xref="x",
    )
    fig.add_annotation(
        x=max(x_val),
        y=mean,
        text="Mean",
        showarrow=False,
        font=dict(color="magenta", size=10),
        xanchor="right",
        yanchor="bottom",
        row=row_idx,
        col=col_idx,
    )

    # LB (Lower Bound) chizig'i
    fig.add_shape(
        type="line",
        x0=min(x_val),
        x1=max(x_val),
        y0=lower_bound,
        y1=lower_bound,
        line=dict(
            color="blue",
            width=1.5,
        ),
        row=row_idx,
        col=col_idx,
        yref=yref,
        xref="x",
    )
    fig.add_annotation(
        x=max(x_val),
        y=lower_bound,
        text=f"LB ({-btn_value}σ)",
        showarrow=False,
        font=dict(color="blue", size=10),
        xanchor="right",
        yanchor="top",
        row=row_idx,
        col=col_idx,
    )

    # Asosiy grafik
    fig.add_trace(
        go.Scatter(
            x=x_val,
            y=y_val,
            mode="lines",
            line=dict(color=trace_color, width=1.5),
            name=f"{element_name} ({key_name})",
            showlegend=True,
            hoverinfo="x+y",
            hovertemplate=f"Vaqt: %{{x|%d-%m-%Y}}<br>{element_name} Qiymati: %{{y}}<extra></extra>",
        ),
        row=row_idx,
        col=col_idx,
        secondary_y=False,
    )

    # Anomaliya chiziqlari
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

        if (y_prev < upper_bound <= y_curr) or (y_curr < upper_bound <= y_prev):
            if abs(y_curr - y_prev) > 1e-9:
                ratio = (upper_bound - y_prev) / (y_curr - y_prev)
                intersect_x = x_prev + (x_curr - x_prev) * ratio
                intersect_y = upper_bound

        if (y_prev > lower_bound >= y_curr) or (y_curr > lower_bound >= y_prev):
            if abs(y_curr - y_prev) > 1e-9:
                ratio = (lower_bound - y_prev) / (y_curr - y_prev)
                new_intersect_x = x_prev + (x_curr - x_prev) * ratio
                new_intersect_y = lower_bound

                if intersect_x is None or (
                    intersect_x
                    and abs((new_intersect_x - x_prev).total_seconds())
                    < abs((intersect_x - x_prev).total_seconds())
                ):
                    intersect_x = new_intersect_x
                    intersect_y = new_intersect_y

        if is_anomalous_curr != is_anomalous_prev and intersect_x is not None:
            if is_anomalous_prev:
                current_anomalous_segment_x.append(intersect_x)
                current_anomalous_segment_y.append(intersect_y)
                if current_anomalous_segment_x:
                    fig.add_trace(
                        go.Scatter(
                            x=current_anomalous_segment_x,
                            y=current_anomalous_segment_y,
                            mode="lines",
                            line=dict(color="red", width=3),
                            showlegend=False,
                            hoverinfo="x+y",
                            hovertemplate="Vaqt: %{x|%d-%m-%Y}<br>Anomaliya: %{y}<extra></extra>",
                        ),
                        row=row_idx,
                        col=col_idx,
                        secondary_y=False,
                    )
                current_anomalous_segment_x = []
                current_anomalous_segment_y = []

            if is_anomalous_curr:
                current_anomalous_segment_x.append(intersect_x)
                current_anomalous_segment_y.append(intersect_y)
                current_anomalous_segment_x.append(x_curr)
                current_anomalous_segment_y.append(y_curr)

        elif is_anomalous_curr:
            current_anomalous_segment_x.append(x_curr)
            current_anomalous_segment_y.append(y_curr)
        elif not is_anomalous_curr and is_anomalous_prev:
            current_anomalous_segment_x.append(x_curr)
            current_anomalous_segment_y.append(y_curr)
            if current_anomalous_segment_x:
                fig.add_trace(
                    go.Scatter(
                        x=current_anomalous_segment_x,
                        y=current_anomalous_segment_y,
                        mode="lines",
                        line=dict(color="red", width=3),
                        showlegend=False,
                        hoverinfo="x+y",
                        hovertemplate="Vaqt: %{x|%d-%m-%Y}<br>Anomaliya: %{y}<extra></extra>",
                    ),
                    row=row_idx,
                    col=col_idx,
                    secondary_y=False,
                )
            current_anomalous_segment_x = []
            current_anomalous_segment_y = []
        else:
            current_anomalous_segment_x = []
            current_anomalous_segment_y = []

        is_anomalous_prev = is_anomalous_curr

    if current_anomalous_segment_x:
        fig.add_trace(
            go.Scatter(
                x=current_anomalous_segment_x,
                y=current_anomalous_segment_y,
                mode="lines",
                line=dict(color="red", width=3),
                showlegend=False,
                hoverinfo="x+y",
                hovertemplate="Vaqt: %{x|%d-%m-%Y}<br>Anomaliya: %{y}<extra></extra>",
            ),
            row=row_idx,
            col=col_idx,
            secondary_y=False,
        )

    return y_all_values


def draw_magnitude_values(fig, original_df, row_index, col_index=1, min_mag=4, well_lat=0, well_lon=0, min_mlgr=0):
    """
    Barcha Mb magnitudalarni ikkinchi Y-o'qda vertikal chiziqlar orqali chizadi.
    original_df - asl Excel fayli ma'lumotlari
    """
    if original_df is None or original_df.empty:
        logging.info(f"draw_magnitude_values: original_df is empty for row {row_index}")
        return [0, 1]

    # Asl ma'lumotlarni qayta ishlash
    df = original_df.copy()
    
    # Vaqt ustunini yaratish
    df["combined_datetime"] = pd.to_datetime(
        df[DATE_COLUMN].astype(str) + " " + df[TIME_COLUMN].astype(str),
        format="mixed",
        errors="coerce"
    )
    df.dropna(subset=["combined_datetime"], inplace=True)
    
    # Mb qiymatlarini raqamga aylantirish
    df[MAIN_MAGNITUDE_COLUMN] = pd.to_numeric(df[MAIN_MAGNITUDE_COLUMN], errors="coerce")
    df.dropna(subset=[MAIN_MAGNITUDE_COLUMN], inplace=True)
    
    # Masofani hisoblash
    df["R(km)"] = np.round(
        destenc_vectorized(well_lat, well_lon, df[LATITUDE_COLUMN], df[LONGITUDE_COLUMN])
    )
    df["M/lgR"] = np.where(
        df["R(km)"] > 1, df[MAIN_MAGNITUDE_COLUMN] / np.log10(df["R(km)"]), np.nan
    )
    
    # Filtrlash: min_mag va min_mlgr bo'yicha
    valid_earthquakes = df[
        (df[MAIN_MAGNITUDE_COLUMN] >= min_mag) & 
        (df["M/lgR"] >= min_mlgr)
    ].copy()
    
    if valid_earthquakes.empty:
        logging.info(f"draw_magnitude_values: No valid earthquakes for row {row_index}")
        return [0, 1]
    
    # Y-o'qi diapazonini belgilash
    max_mag_for_y_axis = valid_earthquakes[MAIN_MAGNITUDE_COLUMN].max() * 1.1
    min_mag_for_y_axis = 0

    fig.update_yaxes(
        range=[min_mag_for_y_axis, max_mag_for_y_axis],
        secondary_y=True,
        title_text="Magnituda (Mb)",
        row=row_index,
        col=col_index,
    )

    # Vertikal chiziqlar uchun ma'lumotlar
    stem_x = []
    stem_y = []
    hover_texts = []

    for _, row in valid_earthquakes.iterrows():
        time_str = row["combined_datetime"].strftime("%d.%m.%Y")
        mag_val = row[MAIN_MAGNITUDE_COLUMN]
        distance = row["R(km)"]
        mlgr_val = row["M/lgR"]

        stem_x.extend([row["combined_datetime"], row["combined_datetime"], None])
        stem_y.extend([0, mag_val, None])

        hover_text = (f"Vaqt: {time_str}<br>"
                     f"Mb: {mag_val:.2f}<br>"
                     f"Masofa: {distance:.1f} km<br>"
                     f"M/lgR: {mlgr_val:.2f}")
        hover_texts.extend(["", hover_text, ""])

    if stem_x:
        fig.add_trace(
            go.Scatter(
                x=stem_x,
                y=stem_y,
                mode="lines",
                line=dict(color="navy", width=2),
                name=f"{MAIN_MAGNITUDE_COLUMN} Magnituda (≥{min_mag})",
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

    fig.update_xaxes(
        showgrid=True,
        gridwidth=0.15,
        gridcolor="black",
        griddash="dot",
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
        os.path.join(settings.BASE_DIR, 'Export_*.shp'),
        os.path.join(settings.BASE_DIR, 'Seysmogen_*.shp'),
    ]

    if hasattr(settings, 'CRACKS_SHAPEFILES_DIR'):
        search_paths.append(os.path.join(settings.CRACKS_SHAPEFILES_DIR, '*.shp'))

    for search_path in search_paths:
        found_files = glob.glob(search_path)
        shapefile_paths.extend(found_files)

        #dublikatlarni olib tashlash
        shapefile_paths = list(set(shapefile_paths))

        logging.info(f"Topilgan shapefilelar: {len(shapefile_paths)} ta")
        for path in shapefile_paths:
            logging.info(f" -{os.path.basename(path)}")

        return shapefile_paths

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

            #Fayl nomini qo'shish
            gdf['source_file'] = os.path.basename(shapefile_path)

            all_cracks.append(gdf)
            logging.info(f"Yuklandi:{os.path.basename(shapefile_path)}-{len(gdf)} ta yoriq")

        except Exception as e:
            logging.error(f"Shapefile {shapefile_path}:{e}")
            continue

    if not all_cracks:
        logging.warning("Hech qanday shapefile yuklanmadi")
        return None

    #Barcha malumotlarni  birlashtirish
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
        "A": (255, 0, 0),      # qizil
        "B": (200, 0, 0),      # qizil (biroz farqli bo'lishi mumkin)
        "C": (255, 165, 0),    # sariq/oranj
        "D": (128, 128, 128),  # kulrang
    }

    # RATE bo'yicha qalinlik va alpha
    rate_styles = {
        "1": {"weight": 6, "alpha": 1.0},   # eng qalin, to‘q
        "2": {"weight": 4, "alpha": 0.8},   # o‘rtacha
        "3": {"weight": 2, "alpha": 0.6},   # eng yupqa, eng och
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
    Bitta quduq haqida batafsil ma'lumot olish
    """
    engine = None
    try:
        engine = connect_db()

        query = """
            SELECT 
                Nomi,
                Quduq_turlari,
                Grunt,
                Chuqurlik,
                Suv_qatlami
            FROM malumot
            WHERE Nomi = %s
        """

        df = pd.read_sql(query, engine, params=(well_name.strip(),))

        if not df.empty:
            row = df.iloc[0]
            return {
                "nomi": row["Nomi"] or "Ma'lumot yo'q",
                "quduq_turi": row["Quduq_turlari"] or "Ma'lumot yo'q",
                "grunt": row["Grunt"] or "Ma'lumot yo'q",
                "chuqurlik": row["Chuqurlik"] or "Ma'lumot yo'q",
                "suv_qatlami": row["Suv_qatlami"] or "Ma'lumot yo'q",
            }
        else:
            return {
                "nomi": "Ma'lumot topilmadi",
                "quduq_turi": "Ma'lumot topilmadi",
                "grunt": "Ma'lumot topilmadi",
                "chuqurlik": "Ma'lumot topilmadi",
                "suv_qatlami": "Ma'lumot topilmadi",
            }

    except Exception as e:
        logging.error(f"Skvajina ma'lumotlarini yuklashda xatolik ({well_name}): {e}")
        return {
            "nomi": "Xatolik yuz berdi",
            "quduq_turi": "Xatolik yuz berdi",
            "grunt": "Xatolik yuz berdi",
            "chuqurlik": "Xatolik yuz berdi",
            "suv_qatlami": "Xatolik yuz berdi",
        }
    finally:
        if engine:
            engine.dispose()



def add_map_data_folium(selected_keys, well_coords, earthquake_data, min_mag, min_mlgr):
    """
    Folium yordamida interaktiv xarita yaratadi va unga barcha
    skvajinalar, tanlangan skvajinalar, filtrlangan zilzilalar va
    BARCHA yer yoriqlarini qo'shadi.
    """
    all_wells = get_all_wells_coordinates()

    selected_well_names = set()
    filtered_earthquakes_by_well = []

    # Har bir tanlangan skvajina uchun filtrlangan zilzilalarni hisoblash
    for key in selected_keys:
        _, skvajina = key.split(" | ")
        selected_well_names.add(skvajina)

        lat, lon = well_coords.get(skvajina, (None, None))
        if lat is not None and lon is not None:
            df = earthquake_data.copy()
            df[MAIN_MAGNITUDE_COLUMN] = pd.to_numeric(df[MAIN_MAGNITUDE_COLUMN], errors="coerce")
            df.dropna(subset=[MAIN_MAGNITUDE_COLUMN], inplace=True)
            df["R(km)"] = np.round(
                destenc_vectorized(lat, lon, df[LATITUDE_COLUMN], df[LONGITUDE_COLUMN])
            )
            df["M/lgR"] = np.where(
                df["R(km)"] > 1, df[MAIN_MAGNITUDE_COLUMN] / np.log10(df["R(km)"]), np.nan
            )
            valid_earthquakes = df[
                (df[MAIN_MAGNITUDE_COLUMN] >= min_mag) &
                (df["M/lgR"] >= min_mlgr)
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

    # Xarita markazini aniqlash
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

    m = folium.Map(
        location=[center_lat, center_lon],
        zoom_start=8,
        tiles="OpenStreetMap",
        attr = "© OpenStreetMap contributors"
    )

    # Turli xil fon xaritalari qo'shish (xatolikni oldini olish uchun try-except)
    try:
        folium.TileLayer(
            tiles='https://stamen-tiles.a.ssl.fastly.net/terrain/{z}/{x}/{y}.png',
            
            attr='Map tiles by <a href="http://stamen.com">Stamen Design</a>, under <a href="http://creativecommons.org/licenses/by/3.0">CC BY 3.0</a>. Data by <a href="http://openstreetmap.org">OpenStreetMap</a>, under <a href="http://creativecommons.org/licenses/by-sa/3.0">CC BY SA</a>.',
            
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
            
            attr='Tiles © Esri — Source: Esri, i-cubed, USDA, USGS, AEX, GeoEye, Getmapping, Aerogrid, IGN, IGP, UPR-EGP, and the GIS User Community',
            
            name='Satellite',
            overlay=False,
            control=True
        ).add_to(m)
    except:
        logging.warning("Satellite tiles yuklanmadi")

    # BARCHA YER YORIQLARINI QO'SHISH - Avtomatik
    logging.info("Yer yoriqlarini yuklash boshlandi...")
    all_cracks = load_all_cracks_shapefiles()
    crack_colors = {}
    if all_cracks is not None:
        crack_colors = add_cracks_to_map(m, all_cracks)
        logging.info(f"Yer yoriqlari xaritaga qo'shildi: {len(all_cracks)} ta")
    else:
        logging.warning("Yer yoriqlari yuklanmadi")

    # Barcha skvajinalarni xaritaga qo'shish (kulrang rangda)
    for well_name, (lat, lon) in all_wells.items():
        if well_name not in selected_well_names:
            # Bazadan to'liq ma'lumotlarni olish
            well_info = get_well_detailed_info(well_name)  # Yangi funksiya

            # HTML popup yaratish
            popup_html = f"""
                <div style="width: 300px; font-family: Arial; font-size: 12px;">
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
                        <tr style="background-color: #f8f9fa;">
                            <td style="padding: 5px; border: 1px solid #dee2e6; font-weight: bold;">Grunt:</td>
                            <td style="padding: 5px; border: 1px solid #dee2e6;">{well_info.get('grunt', 'Ma\'lumot yo\'q')}</td>
                        </tr>
                        <tr>
                            <td style="padding: 5px; border: 1px solid #dee2e6; font-weight: bold;">Chuqurlik:</td>
                            <td style="padding: 5px; border: 1px solid #dee2e6;">{well_info.get('chuqurlik', 'Ma\'lumot yo\'q')}</td>
                        </tr>
                        <tr style="background-color: #f8f9fa;">
                            <td style="padding: 5px; border: 1px solid #dee2e6; font-weight: bold;">Suv qatlami:</td>
                            <td style="padding: 5px; border: 1px solid #dee2e6;">{well_info.get('suv_qatlami', 'Ma\'lumot yo\'q')}</td>
                        </tr>
                        <tr>
                        </table>
                    <p style="margin-top: 10px; color: #6c757d; font-style: italic;">Tanlanmagan skvajina</p>
                </div>
                """

            tooltip_text = f"<b>Skvajina:</b> {well_name}<br>(Tanlanmagan)<br>Batafsil ma'lumot uchun bosing"
            triangle_icon = folium.DivIcon(
                html='<div style="width: 0; height: 0; border-left: 10px solid transparent; border-right: 10px solid transparent; border-bottom: 20px solid lightblue;"></div>',
                icon_size=(20, 20),
                icon_anchor=(10, 20)
            )
            folium.Marker(
                location=[lat, lon],
                tooltip=tooltip_text,
                popup=folium.Popup(popup_html, max_width=370),
                icon=triangle_icon,
            ).add_to(m)


    # Tanlangan skvajinalarni xaritaga qo'shish (pushti rangda)
    for key in selected_keys:
        _, skvajina = key.split(" | ")
        lat, lon = well_coords.get(skvajina, (None, None))
        if lat is not None and lon is not None:
            # Bazadan to'liq ma'lumotlarni olish
            well_info = get_well_detailed_info(skvajina)  # Yangi funksiya

            # Filtrlangan zilzilalar sonini hisoblash
            well_earthquakes = len([eq for eq in filtered_earthquakes_by_well if
                                    eq['skvajina'].iloc[0] == skvajina]) if filtered_earthquakes_by_well else 0

            # HTML popup yaratish
            popup_html = f"""
                <div style="width: 350px; font-family: Arial; font-size: 12px;">
                    <h4 style="color: #1e88e5; margin-bottom: 10px;">Tanlangan skvajina</h4>
                    <table style="width: 100%; border-collapse: collapse;">
                        <tr style="background-color: #e3f2fd;">
                            <td style="padding: 5px; border: 1px solid #90caf9; font-weight: bold;">Nomi:</td>
                            <td style="padding: 5px; border: 1px solid #90caf9;">{well_info.get('nomi', 'Ma\'lumot yo\'q')}</td>
                        </tr>
                        <tr>
                            <td style="padding: 5px; border: 1px solid #90caf9; font-weight: bold;">Quduq turi:</td>
                            <td style="padding: 5px; border: 1px solid #90caf9;">{well_info.get('quduq_turi', 'Ma\'lumot yo\'q')}</td>
                        </tr>
                        <tr style="background-color: #e3f2fd;">
                            <td style="padding: 5px; border: 1px solid #90caf9; font-weight: bold;">Grunt:</td>
                            <td style="padding: 5px; border: 1px solid #90caf9;">{well_info.get('grunt', 'Ma\'lumot yo\'q')}</td>
                        </tr>
                        <tr>
                            <td style="padding: 5px; border: 1px solid #90caf9; font-weight: bold;">Chuqurlik:</td>
                            <td style="padding: 5px; border: 1px solid #90caf9;">{well_info.get('chuqurlik', 'Ma\'lumot yo\'q')}</td>
                        </tr>
                        <tr style="background-color: #e3f2fd;">
                            <td style="padding: 5px; border: 1px solid #90caf9; font-weight: bold;">Suv qatlami:</td>
                            <td style="padding: 5px; border: 1px solid #90caf9;">{well_info.get('suv_qatlami', 'Ma\'lumot yo\'q')}</td>
                        </tr>
                        </table>
                    <p style="margin-top: 10px; color: #1565c0; font-weight: bold;">✓ Tanlangan skvajina</p>
                </div>
                """

            tooltip_text = f"<b>Tanlangan skvajina:</b> {skvajina}<br>Batafsil ma'lumot uchun bosing"

            triangle_icon = folium.DivIcon(
                html='<div style="width: 0; height: 0; border-left: 10px solid transparent; border-right: 10px solid transparent; border-bottom: 20px solid blue;"></div>',
                icon_size=(20, 20),
                icon_anchor=(10, 20)
            )

            folium.Marker(
                location=[lat, lon],
                tooltip=tooltip_text,
                popup=folium.Popup(popup_html, max_width=370),
                icon=triangle_icon,
            ).add_to(m)

    # Filtrlangan zilzilalarni xaritaga qo'shish
    if not all_filtered_earthquakes.empty:
        for idx, row in all_filtered_earthquakes.iterrows():
            mag_val = row.get(MAIN_MAGNITUDE_COLUMN, None)
            date_val = row.get(DATE_COLUMN, "Nomalum")
            if isinstance(date_val, (pd.Timestamp, datetime.datetime)):
                date_val = date_val.strftime("%Y-%m-%d")
            distance_val = row.get("R(km)", "Nomalum")
            mlgr_val = row.get("M/lgR", "Nomalum")
            depth_val = row.get("Depth", "Nomalum")

            if mag_val is not None and not np.isnan(mag_val) and mag_val > 0:
                tooltip_html = f"""
                <b>Filtrlangan zilzila</b><br>
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
                    location=[row[LATITUDE_COLUMN], row[LONGITUDE_COLUMN]],
                    radius=radius,
                    color=color,
                    fill=True,
                    fill_color=color,
                    fill_opacity=0.7,
                    stroke=True,
                    weight=2,
                    tooltip=tooltip_html,
                ).add_to(m)

    # Dinamik Legend (yer yoriqlari va zilzilalar bilan)
    legend_items = [
        '<p><b>Xarita elementlari:</b></p>',
        '<p><i class="fa fa-circle" style="color:darkred"></i> Zilzila Mb ≥ 6.0</p>',
        '<p><i class="fa fa-circle" style="color:red"></i> Zilzila Mb 5.0-5.9</p>',
        '<p><i class="fa fa-circle" style="color:orange"></i> Zilzila Mb 4.0-4.9</p>',
        '<p><i class="fa fa-circle" style="color:yellow"></i> Zilzila Mb < 4.0</p>',
        '<p><div style="width: 0; height: 0; border-left: 8px solid transparent; border-right: 8px solid transparent; border-bottom: 15px solid blue; display: inline-block; margin-right: 8px;"></div>->Tanlangan skvajinalar</p>',
        '<p><div style="width: 0; height: 0; border-left: 8px solid transparent; border-right: 8px solid transparent; border-bottom: 15px solid lightblue; display: inline-block; margin-right: 8px;"></div>-> Tanlanmagan skvajinalar</p>',
    ]

    legend_html = f'''
    <div style="position: fixed; 
                bottom: 50px; left: 50px; width: 160px; height: 160px; 
                background-color: white; border:2px solid grey; z-index:9999; 
                font-size:12px; padding: 10px; overflow-y: auto;">
    {''.join(legend_items)}
    </div>
    '''
    m.get_root().html.add_child(folium.Element(legend_html))

    # Layer control qo'shish
    folium.LayerControl().add_to(m)

    return m._repr_html_()

def selection_view(request):
    """
    Handles the selection of wells and parametrs for analysis.
    """
    lst_stansiya, _ = fetch_data()
    all_params = []
    for group_name, params_list in DEFAULT_ELEMENTS_GROUPS.items():
        all_params.extend(params_list)
    all_params = sorted(list(set(all_params)))

    if request.method == "POST":
        selected_keys = request.POST.getlist("wells")
        selected_params = request.POST.getlist("params")

        if not selected_keys:
            return render(
                request,
                "seismos_app/selection.html",
                {
                    "wells": lst_stansiya.keys(),
                    "params": all_params,
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
        "seismos_app/selection.html",
        {"wells": lst_stansiya.keys(), "params": all_params},
    )


def parametrs_view(request):
    """
    Handles input for seismic parametrs and file upload.
    """
    if request.method == "POST":
        try:
            min_mag = float(request.POST["min_mag"])
            btn_value = float(request.POST["sigma"])
            min_mlgr = float(request.POST["min_mlgr"])

            if "excel_file" not in request.FILES:
                return render(
                    request,
                    "seismos_app/parametrs.html",
                    {"error": "Iltimos, Excel faylni yuklang."},
                )

            file = request.FILES["excel_file"]
            fs = FileSystemStorage()
            filename = fs.save(file.name, file)

            request.session.update(
                {
                    "excel_file": filename,
                    "min_mag": min_mag,
                    "btn_value": btn_value,
                    "min_mlgr": min_mlgr,
                }
            )
            return redirect("seismos:results")
        except ValueError:
            logging.error("Invalid input for numeric parametrs.")
            return render(
                request,
                "seismos_app/parametrs.html",
                {"error": "Iltimos, barcha sonli maydonlarga to'g'ri qiymat kiriting."},
            )
        except Exception as e:
            logging.error(f"Parameter input or file upload error: {e}")
            return render(
                request,
                "seismos_app/parametrs.html",
                {"error": f"Xato yuz berdi: {e}. Iltimos, qayta urinib ko'ring."},
            )
    return render(request, "seismos_app/parametrs.html")


def results_view(request):
    """
    Generates and displays seismic analysis results with graphs, and a separate map
    at the bottom of the page showing all wells and selected wells with radii.
    Now includes date filtering functionality.
    """
    selected_keys = request.session.get("selected_keys", [])
    selected_params = request.session.get("selected_params", [])
    min_mag = request.session.get("min_mag")
    btn_value = request.session.get("btn_value")
    min_mlgr = request.session.get("min_mlgr")
    excel_file = request.session.get("excel_file")

    # Input validatsiyasi
    if not all([
        selected_keys,
        excel_file,
        min_mag is not None,
        btn_value is not None,
        min_mlgr is not None,
    ]):
        return render(
            request,
            "seismos_app/results.html",
            {
                "error": "To'liq ma'lumotlar mavjud emas. Iltimos, oldingi qadamlarga qayting."
            },
        )

    if not selected_params:
        selected_params = sorted(list(set(sum(DEFAULT_ELEMENTS_GROUPS.values(), []))))

    # Sana filtrini olish va validatsiya qilish
    filter_start_date = request.GET.get('start_date')
    filter_end_date = request.GET.get('end_date')
    
    # Sana filtrlari bo'sh bo'lsa, standart qiymatlarga o'tkazish
    if filter_start_date and filter_end_date:
        try:
            start_date = pd.to_datetime(filter_start_date)
            end_date = pd.to_datetime(filter_end_date)
            if start_date > end_date:
                return render(
                    request,
                    "seismos_app/results.html",
                    {"error": "Boshlang'ich sana oxirgi sanadan katta bo'lmasligi kerak."},
                )
        except (ValueError, TypeError) as e:
            logging.warning(f"Sana filtri xatosi: {e}")
            return render(
                request,
                "seismos_app/results.html",
                {"error": f"Noto'g'ri sana formati: {e}"},
            )

    engine = None
    conn = None

    try:
        # Excel faylini tekshirish
        if not excel_file:
            return render(
                request,
                "seismos_app/results.html",
                {"error": "Excel fayli tanlanmagan."},
            )
        file_path = os.path.join(settings.MEDIA_ROOT, excel_file)
        if not os.path.exists(file_path):
            return render(
                request,
                "seismos_app/results.html",
                {"error": "Yuklangan Excel fayli topilmadi."},
            )

        dfe = pd.read_excel(file_path)
        required_cols = [
            DATE_COLUMN,
            TIME_COLUMN,
            LATITUDE_COLUMN,
            LONGITUDE_COLUMN,
            MAIN_MAGNITUDE_COLUMN,
            SECONDARY_MAGNITUDE_COLUMN,
        ]
        if not all(col in dfe.columns for col in required_cols):
            missing = [col for col in required_cols if col in dfe.columns]
            return render(
                request,
                "seismos_app/results.html",
                {
                    "error": f"Excel faylida kerakli ustunlar yo'q: {', '.join(missing)}."
                },
            )

        # Ma'lumotlar bazasidan ma'lumot olish
        lst_stansiya, well_coords = fetch_data()
        if not lst_stansiya or not well_coords:
            return render(
                request,
                "seismos_app/results.html",
                {"error": "Bazadan ma'lumotlar olinmadi."},
            )

        try:
            engine = connect_db()
            if not engine:
                return render(
                    request,
                    "seismos_app/results.html",
                    {"error": "Ma'lumotlar bazasiga ulanish imkonsiz."},
                )
            conn = engine.connect()
        except Exception as e:
            logging.error(f"Ma'lumotlar bazasiga ulanish xatosi: {e}")
            return render(
                request,
                "seismos_app/results.html",
                {"error": f"Ma'lumotlar bazasiga ulanishda xato: {e}"},
            )

        graph_data = []
        first_anomaly_date = None

        # Ma'lumotlarni bazadan olish va filtrlaish
        # `results_view` ichidagi ma'lumotlarni olish va filtrlaish qismi
        for key in selected_keys:
            for param in selected_params:
                ssdi_id = lst_stansiya.get(key, {}).get(param)
                if not ssdi_id:
                    logging.warning(f"{key} uchun {param} parametri topilmadi")
                    continue
                query = text(f"SELECT date, `{ssdi_id}` FROM alldata WHERE `{ssdi_id}` IS NOT NULL")
                try:
                    data = conn.execute(query).fetchall()
                except Exception as e:
                    logging.error(f"Query xatosi {key} - {param}: {e}")
                    continue
                if not data:
                    logging.warning(f"{key} - {param} uchun ma'lumot topilmadi")
                    continue
                
                df_temp = pd.DataFrame(data, columns=['date', 'value'])
                df_temp['date'] = pd.to_datetime(df_temp['date'], errors='coerce')
                df_temp.dropna(subset=['date', 'value'], inplace=True)
                
                if filter_start_date and filter_end_date:
                    try:
                        start_date = pd.to_datetime(filter_start_date, format="%Y-%m-%d")
                        end_date = pd.to_datetime(filter_end_date, format="%Y-%m-%d")
                        df_filtered = df_temp[(df_temp['date'] >= start_date) & (df_temp['date'] <= end_date)]
                        
                        if len(df_filtered) == 0:
                            logging.warning(f"{key} - {param} uchun filtrlangan ma'lumot yo'q")
                            continue
                        
                        x_val = df_filtered['date'].tolist()  # O'ZGARTIRISH: Series -> List
                        y_val = df_filtered['value'].tolist()
                    except (ValueError, TypeError) as e:
                        logging.warning(f"Sana filtri xatosi {key} - {param}: {e}")
                        x_val = df_temp['date'].tolist()  # O'ZGARTIRISH: Series -> List
                        y_val = df_temp['value'].tolist()
                else:
                    x_val = df_temp['date'].tolist()  # O'ZGARTIRISH: Series -> List
                    y_val = df_temp['value'].tolist()

                if len(y_val) == 0:
                    logging.warning(f"{key} - {param} uchun y_val bo'sh")
                    continue

                y_series = pd.Series(y_val, index=x_val)  # Bu hali ham Series uchun ishlaydi
                mean, sigma = np.mean(y_val), np.std(y_val)
                
                if sigma == 0:
                    logging.warning(f"{key} - {param} uchun sigma nolga teng")
                    continue
                
                stansiya, skvajina = key.split(" | ")
                graph_data.append((x_val, y_val, mean, sigma, param, key, skvajina))

                # Anomaliya sanasini topish
                upper_bound = mean + btn_value * sigma
                lower_bound = mean - btn_value * sigma
                anomalies = y_series[
                    (y_series > upper_bound) | (y_series < lower_bound)
                ]
                if not anomalies.empty:
                    anomaly_start_date = anomalies.index[0]
                    logging.info(f"Anomaliya topildi: {key} - {param}, sana: {anomaly_start_date}")
                    if (
                        first_anomaly_date is None
                        or anomaly_start_date < first_anomaly_date
                    ):
                        first_anomaly_date = anomaly_start_date

        if not graph_data:
            return render(
                request,
                "seismos_app/results.html",
                {"error": "Tanlangan sana oralig'ida hech qanday mos keluvchi ma'lumot topilmadi."},
            )

        # Excel fayldagi zilzilalar ma'lumotlarini yuklash va filtrlaish
        all_earthquakes_df = dfe.copy()
        all_earthquakes_df["combined_datetime"] = pd.to_datetime(
            all_earthquakes_df[DATE_COLUMN].astype(str)
            + " "
            + all_earthquakes_df[TIME_COLUMN].astype(str),
            format="mixed",
            errors="coerce",
        )
        all_earthquakes_df.dropna(subset=["combined_datetime"], inplace=True)
        
        # Zilzilalarni ham sana bo'yicha filtrlaish
        if filter_start_date and filter_end_date:
            try:
                start_date = pd.to_datetime(filter_start_date)
                end_date = pd.to_datetime(filter_end_date)
                earthquake_mask = (all_earthquakes_df["combined_datetime"] >= start_date) & (all_earthquakes_df["combined_datetime"] <= end_date)
                all_earthquakes_df = all_earthquakes_df[earthquake_mask]
            except (ValueError, TypeError) as e:
                logging.warning(f"Zilzilalar uchun sana filtri xatosi: {e}")

        logging.info(f"Filtrlangan zilzilalar: {len(all_earthquakes_df)} ta")

        # Grafiklar chizish
        num_graphs = len(graph_data)
        single_graph_height = 500
        total_figure_height = num_graphs * single_graph_height
        max_total_height = 20000
        if total_figure_height > max_total_height:
            scale_factor = max_total_height / total_figure_height
            single_graph_height = int(single_graph_height * scale_factor)
            total_figure_height = max_total_height

        subplot_titles = [
            f"{key} - {param}" for (_, _, _, _, param, key, _) in graph_data
        ]
        specs = [[{"secondary_y": True}]] * num_graphs

        vertical_spacing = 0.05 if num_graphs <= 3 else 0.03 if num_graphs <= 5 else max(0.005, 0.1 / num_graphs)

        fig = make_subplots(
            rows=num_graphs,
            cols=1,
            subplot_titles=subplot_titles,
            vertical_spacing=vertical_spacing,
            specs=specs,
        )

        # X-o'qi diapazonini aniqlash
        today = pd.to_datetime('today').normalize()
        
        if filter_start_date and filter_end_date:
            try:
                min_date = pd.to_datetime(filter_start_date)
                max_date = pd.to_datetime(filter_end_date)
                delta = timedelta(days=1)
                global_min_date = min_date
                global_max_date = max_date
            except (ValueError, TypeError):
                if first_anomaly_date is not None:
                    min_date = first_anomaly_date
                    max_date = today
                    delta = timedelta(days=15)
                    global_min_date = min_date
                    global_max_date = max_date
                else:
                    all_dates = []
                    for x_val, _, _, _, _, _, _ in graph_data:
                        all_dates.extend(x_val)
                    if all_dates:
                        min_date = min(pd.to_datetime(all_dates))
                        max_date = min(max(pd.to_datetime(all_dates)), today)
                        delta = (max_date - min_date) * 0.05 if (max_date - min_date) > timedelta(0) else timedelta(days=1)
                    else:
                        min_date = pd.to_datetime("2020-01-01")
                        max_date = today
                        delta = timedelta(days=1)
                    global_min_date = min_date
                    global_max_date = max_date
        else:
            if first_anomaly_date is not None:
                all_dates = []
                for x_val, _, _, _, _, _, _ in graph_data:
                    all_dates.extend(x_val)
                all_earthquake_dates = list(all_earthquakes_df["combined_datetime"])
                
                all_combined_dates = pd.to_datetime(all_dates + all_earthquake_dates, errors="coerce")
                full_max_date = max(all_combined_dates) if len(all_combined_dates) > 0 else today
                full_min_date = min(all_combined_dates) if len(all_combined_dates) > 0 else pd.to_datetime("2020-01-01")
                
                min_date = first_anomaly_date
                max_date = min(full_max_date, today)
                delta = timedelta(days=15)
                global_min_date = full_min_date
                global_max_date = min(full_max_date, today)
            else:
                all_dates = []
                for x_val, _, _, _, _, _, _ in graph_data:
                    all_dates.extend(x_val)
                all_earthquake_dates = list(all_earthquakes_df["combined_datetime"])

                if all_dates or all_earthquake_dates:
                    all_combined_dates = pd.to_datetime(all_dates + all_earthquake_dates, errors="coerce")
                    min_date = min(all_combined_dates)
                    max_date = min(max(all_combined_dates), today)
                    delta = (max_date - min_date) * 0.05 if (max_date - min_date) > timedelta(0) else timedelta(days=1)
                else:
                    min_date = pd.to_datetime("2020-01-01")
                    max_date = today
                    delta = timedelta(days=1)
                    
                global_min_date = min_date
                global_max_date = max_date

        color_pool = generate_colors(num_graphs)
        for idx, (x, y, mean, sigma, param, key, skv) in enumerate(graph_data):
            row, col = idx + 1, 1
            trace_color = color_pool[idx]
            y_all = plot_data_with_anomalies(
                fig, x, y, mean, sigma, btn_value, row, col, trace_color, param, key
            )
            fig.update_yaxes(
                title_text=f"{param} Qiymati",
                range=[min(y_all) * 0.9, max(y_all) * 1.1],
                row=row,
                col=col,
            )

            lat, lon = well_coords.get(skv, (0, 0))
            
            if not all_earthquakes_df.empty:
                draw_magnitude_values(
                    fig, 
                    all_earthquakes_df,  # Filtrlangan Excel fayli ma'lumotlari
                    row, 
                    col, 
                    min_mag=min_mag, 
                    well_lat=lat, 
                    well_lon=lon, 
                    min_mlgr=min_mlgr
                )

            # X-o'qi diapazonini belgilash
            fig.update_xaxes(
                range=[min_date - delta, max_date + delta],
                type="date",
                tickformat=None,
                showgrid=True,
                griddash="dot",
                dtick=None,
                row=row,
                col=col,
                autorange=False,
            )

        # Layout sozlamalari
        fig.update_layout(
            title_text="Tahlil natijalari" + (f" ({filter_start_date} - {filter_end_date})" if filter_start_date and filter_end_date else ""),
            height=total_figure_height,
            showlegend=False,
            plot_bgcolor="gainsboro",
            hovermode="x unified",
            legend=dict(
                x=0.01,
                y=0.99,
                bgcolor="rgba(255,255,255,0.9)",
                bordercolor="rgba(0,0,0,0.3)",
                borderwidth=1,
                xanchor="left",
                yanchor="top",
                font=dict(size=10),
            ),
            title=dict(font=dict(size=20), x=0.5, xanchor="center"),
            margin=dict(l=20, r=20, t=80, b=20),
            autosize=True,
        )

        config = {
            "displayModeBar": True,
            "scrollZoom": True,
            "doubleClick": "reset+autosize",
            "modeBarButtonsToAdd": [
                "pan2d",
                "zoomIn2d",
                "zoomOut2d",
                "autoScale2d",
                "resetScale2d",
            ],
            "responsive": True,
            "showTips": True,
            "displaylogo": False,
            "toImageButtonOptions": {
                "format": "png",
                "filename": "seismic_analysis",
                "height": 500,
                "width": 700,
                "scale": 1
            }
        }
        plotly_html = fig.to_html(
            full_html=False, include_plotlyjs="cdn", config=config
        )

        # Folium xaritasini yaratish (filtrlangan zilzilalar bilan)
        try:
            folium_map_html = add_map_data_folium(
                selected_keys, well_coords, all_earthquakes_df, min_mag, min_mlgr
            )
        except Exception as e:
            logging.error(f"Folium xaritasini yaratishda xato: {e}")
            folium_map_html = "<p>Xarita yaratishda xato yuz berdi.</p>"

        # Template uchun context
        context = {
            "plotly_graph": plotly_html, 
            "folium_map": folium_map_html,
            "current_start_date": filter_start_date or "",
            "current_end_date": filter_end_date or "",
        }

        # Ma'lumotlar diapazonini aniqlash (filtr uchun)
        if graph_data:
            all_dates = []
            for x_val, _, _, _, _, _, _ in graph_data:
                all_dates.extend(x_val)
            if all_dates:
                context["data_min_date"] = min(pd.to_datetime(all_dates)).strftime('%Y-%m-%d')
                context["data_max_date"] = max(pd.to_datetime(all_dates)).strftime('%Y-%m-%d')

        return render(request, "seismos_app/results.html", context)

    except Exception as e:
        logging.error(f"Results view error: {e}", exc_info=True)
        return render(
            request,
            "seismos_app/results.html",
            {"error": "Tizimda kutilmagan xato yuz berdi. Iltimos, administrator bilan bog'laning."},
        )

    finally:
        try:
            if conn:
                conn.close()
            if engine:
                engine.dispose()
        except Exception as e:
            logging.error(f"Resurslarni yopishda xato: {e}")