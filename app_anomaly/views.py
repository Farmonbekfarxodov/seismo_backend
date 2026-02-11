# app_anomaly/views.py

import logging
import pandas as pd
import numpy as np
import folium
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
from django.shortcuts import render
from django.contrib import messages
from django.views.decorators.http import require_http_methods
from django.core.cache import cache
from folium.plugins import Fullscreen
from sqlalchemy import create_engine, text
from decouple import config as env_config
from plotly.subplots import make_subplots
import plotly.graph_objects as go
import re

from seismos_app.views import (
    fetch_data,
    load_all_cracks_shapefiles,
    add_cracks_to_map,
    load_seismogenic_zones,
    add_seismogenic_zones_to_map,
    fetch_and_filter_earthquakes,
    destenc_vectorized,
    get_well_detailed_info,
)
from .models import AnomalyRecord
from .forms import AnomalyAnalysisForm

logger = logging.getLogger(__name__)

DEFAULT_ELEMENTS_GROUPS = {
    "gazli": ["He", "H2", "O2", "N2", "CH4", "CO2"],
    "kimyoviy": ["F", "C2H6", "pH", "Eh", "HCO3", "Cl2"],
    "fizikaviy": ["T0", "Q", "P", "EOCC"],
}


def get_all_parameters():
    all_params = []
    for group_name, params_list in DEFAULT_ELEMENTS_GROUPS.items():
        all_params.extend(params_list)
    return sorted(list(set(all_params)))


def get_db_config():
    return {
        'db': env_config('DB_NAME'),
        'user': env_config('DB_USER'),
        'psw': env_config('DB_PASSWORD'),
        'ip': env_config('DB_HOST', default='localhost')
    }


def get_parameter_data_for_period(ssdi_id, time_period_months):
    try:
        cache_key = f"param_{ssdi_id}_{time_period_months}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        # Validate ssdi_id - bu column name yoki ID bo'lishi mumkin
        # String yoki integer bo'lishi kerak
        if ssdi_id is None:
            logger.warning(f"Invalid ssdi_id: None")
            return pd.DataFrame()

        # Integer bo'lsa, string ga o'tkazamiz
        ssdi_id_str = str(ssdi_id)

        # Faqat alphanumeric va _ belgisi ruxsat
        if not re.match(r'^[A-Za-z0-9_]+$', ssdi_id_str):
            logger.warning(f"Invalid ssdi_id provided: {ssdi_id}")
            return pd.DataFrame()

        config = get_db_config()
        engine = create_engine(
            f"mysql+mysqlconnector://{config['user']}:{config['psw']}@{config['ip']}/{config['db']}"
        )
        conn = engine.connect()

        today = datetime.now().date()
        start_date = today - relativedelta(months=int(time_period_months))

        query = text(f"""
            SELECT date, `{ssdi_id_str}` 
            FROM alldata 
            WHERE `{ssdi_id_str}` IS NOT NULL 
            AND `{ssdi_id_str}` != 0
            AND date >= '{start_date}'
            AND date <= '{today}'
            ORDER BY date ASC
        """)

        try:
            data = conn.execute(query).fetchall()
        except Exception as e:
            logger.error(f"Query error for ssdi_id={ssdi_id_str}: {e}")
            conn.close()
            engine.dispose()
            return pd.DataFrame()

        conn.close()
        engine.dispose()

        if not data:
            cache.set(cache_key, pd.DataFrame(), 3600)
            return pd.DataFrame()

        df = pd.DataFrame(data, columns=['date', 'value'])
        df['date'] = pd.to_datetime(df['date'], errors='coerce')
        df = df.sort_values('date')

        cache.set(cache_key, df, 3600)
        return df

    except Exception as e:
        logger.error(f"Ma'lumot yuklashda xato: {e}")
        return pd.DataFrame()


def detect_anomalies_in_data(df, sigma=2.0, min_duration_days=1):
    """
    ✅ KETMA-KET ANOMAL QIYMATLARNI ANIQLASH

    min_duration_days - Bu aslida MINIMAL KETMA-KET ANOMAL QIYMATLAR SONI
    (calendar days emas, balki dataframe-dagi ketma-ket anomal qiymatlar)

    Misol: Agar min_duration_days=3 bo'lsa, kamida 3 ta ketma-ket anomal
    qiymat topilishi kerak.
    """
    if df.empty:
        return []

    try:
        # ✅ NaN va NaT qiymatlarni olib tashlaymiz
        df = df.dropna(subset=['date', 'value']).copy()
        df = df.sort_values('date').reset_index(drop=True)

        if df.empty or len(df) < 5:
            logger.warning(f"Ma'lumot juda kam: {len(df)} ta qator")
            return []

        values = df['value']
        mean = values.mean()
        std = values.std()

        upper_bound = mean + sigma * std
        lower_bound = mean - sigma * std

        logger.info(f"=== DEBUG ANOMALIYA ===")
        logger.info(f"Jami qatorlar: {len(df)}")
        logger.info(f"Mean: {mean:.3f}, Std: {std:.3f}")
        logger.info(f"UB: {upper_bound:.3f}, LB: {lower_bound:.3f}")
        logger.info(f"Minimal ketma-ket qiymatlar: {min_duration_days} ta")

        anomalies = []
        current_anomaly = None

        for idx, row in df.iterrows():
            value = row['value']
            date = row['date']

            # ✅ Qo'shimcha xavfsizlik tekshiruvi
            if pd.isna(value) or pd.isna(date):
                continue

            is_anomaly = (value > upper_bound) or (value < lower_bound)

            if is_anomaly:
                # ✅ Anomaliya qiymati topildi
                if current_anomaly is None:
                    current_anomaly = {
                        'start_date': date,
                        'end_date': date,
                        'values': [value],
                        'dates': [date],
                        'count': 1  # ✅ QIYMATLAR SONI
                    }
                else:
                    current_anomaly['end_date'] = date
                    current_anomaly['values'].append(value)
                    current_anomaly['dates'].append(date)
                    current_anomaly['count'] += 1  # ✅ +1
            else:
                # ✅ Anomaliya tugadi
                if current_anomaly is not None:
                    anomaly_count = current_anomaly['count']  # ✅ QIYMATLAR SONI

                    # ✅ Xavfsiz date formatlash
                    start_str = current_anomaly['start_date'].strftime('%Y-%m-%d') if pd.notna(current_anomaly['start_date']) else 'N/A'
                    end_str = current_anomaly['end_date'].strftime('%Y-%m-%d') if pd.notna(current_anomaly['end_date']) else 'N/A'

                    logger.info(f"Anomaliya: {start_str} ~ {end_str} = {anomaly_count} ta qiymat")

                    # ✅ QIYMATLAR SONIGA QA'RA TEKSHIRISH
                    if anomaly_count >= min_duration_days:
                        anomalies.append(current_anomaly)
                        logger.info(f"✅ Saqlandi: {anomaly_count} ta >= {min_duration_days} ta")
                    else:
                        logger.info(f"❌ Qisqa: {anomaly_count} ta < {min_duration_days} ta")

                    current_anomaly = None

        # ✅ Oxirgi anomaliya
        if current_anomaly is not None:
            anomaly_count = current_anomaly['count']

            # ✅ Xavfsiz date formatlash
            start_str = current_anomaly['start_date'].strftime('%Y-%m-%d') if pd.notna(current_anomaly['start_date']) else 'N/A'
            end_str = current_anomaly['end_date'].strftime('%Y-%m-%d') if pd.notna(current_anomaly['end_date']) else 'N/A'

            logger.info(f"Oxirgi anomaliya: {start_str} ~ {end_str} = {anomaly_count} ta qiymat")

            if anomaly_count >= min_duration_days:
                anomalies.append(current_anomaly)
                logger.info(f"✅ Saqlandi: {anomaly_count} ta >= {min_duration_days} ta")
            else:
                logger.info(f"❌ Qisqa: {anomaly_count} ta < {min_duration_days} ta")

        logger.info(f"Jami anomaliyalar: {len(anomalies)}")
        logger.info(f"======================\n")

        return anomalies

    except Exception as e:
        logger.error(f"Anomaliya aniqlashda xato: {e}")
        import traceback
        traceback.print_exc()
        return []

def create_anomaly_chart(df, anomalies, well_name, param_name, sigma=2.0,
                         earthquakes_df=None, well_lat=None, well_lon=None, recent_days=7):
    """
    GRAFIK CHIZISH (YANGILANGAN - Reference code asosida)
    Matematik interpolatsiya yordamida aniq kesishish nuqtalarini topadi.
    """
    try:
        # Sanalarni datetime formatiga o'tkazish
        df['date'] = pd.to_datetime(df['date'])

        # Ma'lumotlarni tayyorlash
        x_val = df['date'].tolist()
        y_val = df['value'].tolist()

        min_date = df['date'].min()
        max_date = df['date'].max()

        padding = pd.Timedelta(days=10)
        range_start = min_date - padding
        range_end = max_date + padding

        values = df['value'].dropna()
        mean = values.mean()
        std = values.std()
        upper_bound = mean + sigma * std
        lower_bound = mean - sigma * std

        # Filtr sanasini aniqlash (Recent days uchun)
        last_date = df['date'].max()
        filter_start_date = last_date - pd.Timedelta(days=recent_days)

        fig = make_subplots(specs=[[{"secondary_y": True}]])

        # 1. ASOSIY CHIZIQ (KOK)
        fig.add_trace(
            go.Scatter(
                x=x_val,
                y=y_val,
                mode='lines',
                name=f'{param_name}',
                line=dict(color='blue', width=1.5),
                hovertemplate='<b>Sana:</b> %{x|%d.%m.%Y}<br><b>Qiymat:</b> %{y:.3f}<extra></extra>'
            ),
            secondary_y=False
        )

        # 2. CHEGARALAR (UB, MEAN, LB)
        # Upper Bound (Yashil)
        fig.add_trace(go.Scatter(
            x=x_val, y=[upper_bound] * len(x_val), mode='lines',
            name=f'UB (+{sigma}σ)', line=dict(color='green', width=1.5, dash='dash'),
            hoverinfo='skip'
        ), secondary_y=False)

        # Mean (Magenta)
        fig.add_trace(go.Scatter(
            x=x_val, y=[mean] * len(x_val), mode='lines',
            name='Mean', line=dict(color='magenta', width=1.5, dash='dash'),
            hoverinfo='skip'
        ), secondary_y=False)

        # Lower Bound (Ko'k)
        fig.add_trace(go.Scatter(
            x=x_val, y=[lower_bound] * len(x_val), mode='lines',
            name=f'LB (-{sigma}σ)', line=dict(color='blue', width=1.5, dash='dash'),
            hoverinfo='skip'
        ), secondary_y=False)

        # =========================================================================
        # 3. ANOMALIYA CHIZIQLARINI HISOBLASH (Reference fayldagi mantiq)
        # =========================================================================

        current_segment_x = []
        current_segment_y = []
        is_anomalous_prev = False

        # Bitta sikl ichida hamma nuqtalarni tekshiramiz
        for i in range(len(x_val)):
            x_curr, y_curr = x_val[i], y_val[i]

            # NaN tekshiruvi
            if pd.isna(y_curr):
                is_anomalous_prev = False
                current_segment_x = []
                current_segment_y = []
                continue

            is_anomalous_curr = (y_curr > upper_bound) or (y_curr < lower_bound)

            # Birinchi nuqta
            if i == 0:
                if is_anomalous_curr:
                    current_segment_x.append(x_curr)
                    current_segment_y.append(y_curr)
                is_anomalous_prev = is_anomalous_curr
                continue

            x_prev, y_prev = x_val[i - 1], y_val[i - 1]
            intersect_x = None
            intersect_y = None

            # --- INTERPOLATSIYA (KESISHISH NUQTASINI TOPISH) ---

            # Upper bound bilan kesishish
            if (y_prev < upper_bound <= y_curr) or (y_curr < upper_bound <= y_prev):
                if abs(y_curr - y_prev) > 1e-9:
                    ratio = (upper_bound - y_prev) / (y_curr - y_prev)
                    if 0 <= ratio <= 1:
                        # Time delta orqali x ni hisoblash
                        time_diff = (x_curr - x_prev).total_seconds()
                        intersect_time = x_prev + timedelta(seconds=time_diff * ratio)
                        intersect_x = intersect_time
                        intersect_y = upper_bound

            # Lower bound bilan kesishish
            if (y_prev > lower_bound >= y_curr) or (y_curr > lower_bound >= y_prev):
                if abs(y_curr - y_prev) > 1e-9:
                    ratio = (lower_bound - y_prev) / (y_curr - y_prev)
                    if 0 <= ratio <= 1:
                        # Agar upper bilan ham kesishgan bo'lsa (juda kam hollarda), yaqinrog'ini olamiz
                        # Bu yerda soddalik uchun lower boundni olamiz
                        time_diff = (x_curr - x_prev).total_seconds()
                        intersect_time = x_prev + timedelta(seconds=time_diff * ratio)
                        intersect_x = intersect_time
                        intersect_y = lower_bound

            # --- SEGMENTNI SHAKLLANTIRISH ---

            if is_anomalous_curr != is_anomalous_prev:
                # Holat o'zgardi (Anomaliya -> Normal YOKI Normal -> Anomaliya)

                if is_anomalous_prev and len(current_segment_x) > 0:
                    # Anomaliya tugadi. Kesishish nuqtasini qo'shib, segmentni yopamiz.
                    if intersect_x is not None:
                        current_segment_x.append(intersect_x)
                        current_segment_y.append(intersect_y)

                    # FILTR: Segment oxiri 'recent_days' ichidami?
                    if current_segment_x[-1] >= filter_start_date:
                        fig.add_trace(go.Scatter(
                            x=current_segment_x, y=current_segment_y,
                            mode='lines',
                            line=dict(color='red', width=3),  # QIZIL QALIN CHIZIQ
                            showlegend=False,
                            hoverinfo='skip'
                        ), secondary_y=False)

                    # Segmentni tozalaymiz
                    current_segment_x = []
                    current_segment_y = []

                if is_anomalous_curr:
                    # Anomaliya boshlandi. Kesishish nuqtasidan boshlaymiz.
                    if intersect_x is not None:
                        current_segment_x.append(intersect_x)
                        current_segment_y.append(intersect_y)

                    current_segment_x.append(x_curr)
                    current_segment_y.append(y_curr)

            elif is_anomalous_curr:
                # Anomaliya davom etmoqda
                current_segment_x.append(x_curr)
                current_segment_y.append(y_curr)

            is_anomalous_prev = is_anomalous_curr

        # Sikl tugagandan keyin ochiq qolgan segmentni tekshirish
        if is_anomalous_prev and len(current_segment_x) > 0:
            # FILTR: Segment oxiri 'recent_days' ichidami?
            if current_segment_x[-1] >= filter_start_date:
                fig.add_trace(go.Scatter(
                    x=current_segment_x, y=current_segment_y,
                    mode='lines',
                    line=dict(color='red', width=3),
                    showlegend=False,
                    hoverinfo='skip'
                ), secondary_y=False)

        # 4. ZILZILALAR (O'zgarishsiz)
        if earthquakes_df is not None and not earthquakes_df.empty and well_lat and well_lon:
            try:
                earthquakes_df_copy = earthquakes_df.copy()
                earthquakes_df_copy['distance'] = destenc_vectorized(
                    well_lat, well_lon,
                    earthquakes_df_copy['Latitude'],
                    earthquakes_df_copy['Longitude']
                )

                earthquakes_df_copy['combined_datetime'] = pd.to_datetime(
                    earthquakes_df_copy['Event_date'].astype(str) + " " +
                    earthquakes_df_copy['Event_time'].astype(str),
                    errors="coerce"
                )

                earthquakes_df_copy = earthquakes_df_copy[
                    (earthquakes_df_copy['combined_datetime'] >= df['date'].min()) &
                    (earthquakes_df_copy['combined_datetime'] <= df['date'].max())
                    ]

                if not earthquakes_df_copy.empty:
                    max_mag = earthquakes_df_copy['Mb'].max() * 1.1
                    fig.update_yaxes(
                        range=[0, max_mag],
                        secondary_y=True,
                        title_text="Magnituda (Mb)"
                    )

                    stem_x = []
                    stem_y = []
                    hover_texts = []

                    for _, row in earthquakes_df_copy.iterrows():
                        mag_val = row['Mb']
                        date_val = row['combined_datetime']
                        hover_text = f"M: {mag_val:.2f}, D: {row['distance']:.1f} km"

                        stem_x.extend([date_val, date_val, None])
                        stem_y.extend([0, mag_val, None])
                        hover_texts.extend(["", hover_text, ""])

                    fig.add_trace(
                        go.Scatter(
                            x=stem_x, y=stem_y, mode='lines',
                            line=dict(color='darkred', width=2),
                            name='Zilzilalar', hoverinfo='text',
                            text=hover_texts, yaxis='y2'
                        ), secondary_y=True
                    )
            except Exception:
                pass

        fig.update_layout(
            title=f"<b>{well_name} - {param_name}</b>",
            xaxis_title="Sana",
            hovermode='x unified',
            height=500,
            template='plotly_white',
        )
        fig.update_xaxes(
            range=[range_start, range_end],
        )

        div_id_safe = re.sub(r'[^A-Za-z0-9_\-]', '_', f"graph_{well_name}_{param_name}")
        return fig.to_html(include_plotlyjs='cdn', div_id=div_id_safe)

    except Exception as e:
        logger.error(f"Grafik xato: {e}")
        return None


def create_anomaly_map_detailed(all_wells_list, well_coords, anomalous_wells_dict):
    """
    BOYITILGAN XARITA (Ko'p qatlamli va boshqariladigan)
    """
    try:
        # Xarita markazi
        if anomalous_wells_dict:
            selected_coords = [well_coords[w] for w in anomalous_wells_dict.keys() if w in well_coords]
            if selected_coords:
                center_lat = np.mean([c[0] for c in selected_coords])
                center_lon = np.mean([c[1] for c in selected_coords])
            else:
                center_lat, center_lon = 41.2995, 69.2401
        else:
            center_lat, center_lon = 41.2995, 69.2401

        # 1. ASOSIY XARITA (OpenStreetMap)
        m = folium.Map(location=[center_lat, center_lon], zoom_start=7, tiles="OpenStreetMap")

        # 2. QO'SHIMCHA XARITA QATLAMLARI (Tiles)
        # Satellite (Yo'ldosh)
        folium.TileLayer(
            tiles='https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
            attr='Tiles © Esri',
            name='Satellite',
            overlay=False
        ).add_to(m)

        # Fullscreen tugmasi
        from folium.plugins import Fullscreen
        Fullscreen(position='topleft').add_to(m)

        # 3. GEOLOGIK QATLAMLAR (FeatureGroups)
        # Bu qatlamlarni alohida guruhlarga olamiz
        cracks_layer = folium.FeatureGroup(name="🌍 Yer yoriqlari", show=True)
        zones_layer = folium.FeatureGroup(name="🔴 Seysmogen zonalar", show=True)
        anomalous_layer = folium.FeatureGroup(name="⚠️ Anomal Skvajinalar", show=True)
        normal_layer = folium.FeatureGroup(name="✅ Normal Skvajinalar", show=True)

        try:
            cracks = load_all_cracks_shapefiles()
            if cracks is not None: add_cracks_to_map(cracks_layer, cracks)  # m o'rniga layer

            zones = load_seismogenic_zones()
            if zones is not None: add_seismogenic_zones_to_map(zones_layer, zones)  # m o'rniga layer
        except Exception:
            pass

        # 4. SKVAJINALARNI AJRATISH VA CHIZISH
        for well_key in all_wells_list:
            well_name = well_key.split(' | ')[1] if ' | ' in well_key else well_key
            if well_name not in well_coords: continue
            lat, lon = well_coords[well_name]

            # Import qilingan ma'lumot funksiyasi
            well_info = get_well_detailed_info(well_name)

            # Rasm qismi
            mineralizatsiya_html = ""
            mineral_src = well_info.get('mineralizatsiya_base64')
            if mineral_src:
                # ✅ Debug: faqat prefix va uzunlik (logda butun base64 chiqmasin)
                try:
                    logger.debug(
                        "mineralizatsiya src for %s: prefix=%s, len=%s",
                        well_name,
                        str(mineral_src)[:30],
                        len(str(mineral_src)),
                    )
                except Exception:
                    pass

                mineralizatsiya_html = f"""
                    <tr>
                        <td colspan="2" style="padding: 10px; border: 1px solid #dee2e6; text-align: center;">
                            <b>Mineralizatsiya:</b><br>
                            <img src="{mineral_src}"
                                 loading="lazy"
                                 style="display:block; max-width: 300px; max-height: 200px; width:100%; height:auto; margin-top: 5px; border-radius: 5px;" 
                                 alt="Mineralizatsiya"/>
                        </td>
                    </tr>
                """

            popup_html = f"""
                            <div style="width: 300px; font-family: Arial; font-size: 10px;">
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
                            </div>
                        """

            # ✅ Popup'ni IFrame bilan beramiz (rasm va katta HTML uchun barqarorroq)
            iframe = folium.IFrame(html=popup_html, width=340, height=320)
            popup = folium.Popup(iframe, max_width=450)

            is_anomalous = well_name in anomalous_wells_dict

            if is_anomalous:
                # --- ANOMAL SKVAJINA ---
                anom_params = ", ".join(anomalous_wells_dict[well_name])
                tooltip_text = f"""
                <div style="font-size: 13px;">
                    <b>{well_name}</b><br>
                    <span style="color: red;">⚠️ Anomaliya: {anom_params}</span>
                </div>
                """
                # Qizil pulsatsiyalanuvchi doira yoki shunchaki qizil marker
                icon = folium.DivIcon(
                    html=f"""
                    <div style="position: relative;">
                        <div style="
                            width: 20px; height: 20px; 
                            background-color: rgba(255, 0, 0, 0.6); 
                            border-radius: 50%; 
                            animation: pulse 1.5s infinite;">
                        </div>
                        <div style="
                            position: absolute; top: 50%; left: 50%; 
                            transform: translate(-50%, -50%);
                            width: 0; height: 0; 
                            border-left: 8px solid transparent; 
                            border-right: 8px solid transparent; 
                            border-bottom: 16px solid red;">
                        </div>
                    </div>
                    <style>
                        @keyframes pulse {{
                            0% {{ transform: scale(0.8); opacity: 1; }}
                            100% {{ transform: scale(2.5); opacity: 0; }}
                        }}
                    </style>
                    """,
                    icon_size=(24, 24), icon_anchor=(12, 12)
                )

                folium.Marker(
                    location=[lat, lon],
                    icon=icon,
                    popup=popup,
                    tooltip=tooltip_text
                ).add_to(anomalous_layer)

            else:
                # --- NORMAL SKVAJINA ---
                tooltip_text = f"<b>{well_name}</b><br><span style='color:green'>Normal</span>"
                icon = folium.DivIcon(
                    html='<div style="width: 0; height: 0; border-left: 6px solid transparent; border-right: 6px solid transparent; border-bottom: 12px solid #3388ff;"></div>',
                    icon_size=(12, 12), icon_anchor=(6, 6)
                )

                folium.Marker(
                    location=[lat, lon],
                    icon=icon,
                    popup=popup,
                    tooltip=tooltip_text
                ).add_to(normal_layer)

        # 5. BARCHA QATLAMNI XARITAGA QO'SHISH
        cracks_layer.add_to(m)
        zones_layer.add_to(m)
        normal_layer.add_to(m)  # Normal birinchi
        anomalous_layer.add_to(m)  # Anomal ustida tursin

        # 6. LAYER CONTROL (Boshqaruv paneli)
        folium.LayerControl(position='topright', collapsed=True).add_to(m)

        # 7. LEGEND (Afsona)
        legend_html = '''
        <div style="position: fixed; bottom: 100px; left: 30px; width: 200px; 
                    background-color: white; border:2px solid grey; z-index:9999;
                    font-size:12px; padding: 10px; border-radius: 5px; opacity: 0.9;">
            <p style="margin: 0; font-weight: bold; border-bottom: 1px solid #ccc; padding-bottom: 5px;">
                SHARTLI BELGILAR
            </p>
            <div style="margin-top: 5px;">
                <i style="background:red; width:10px; height:10px; display:inline-block; border-radius:50%;"></i> 
                <b>Anomal Skvajina</b>
            </div>
            <div style="margin-top: 5px;">
                <i style="background:#3388ff; width:10px; height:10px; display:inline-block; border-radius:50%;"></i> 
                <b>Normal Skvajina</b>
            </div>
            <div style="margin-top: 5px;">
                <i style="border-bottom: 2px solid orange; width:15px; display:inline-block;"></i> 
                Yer Yorig'i
            </div>
            <div style="margin-top: 5px;">
                <i style="background:pink; width:10px; height:10px; display:inline-block; opacity:0.5;"></i> 
                Seysmogen Zona
            </div>
        </div>
        '''
        m.get_root().html.add_child(folium.Element(legend_html))

        return m._repr_html_()

    except Exception as e:
        logger.error(f"Xarita xato: {e}")
        return None

def anomaly_analysis_view(request):
    context = {}

    try:
        lst_stansiya, well_coords = fetch_data()
        all_wells_list = list(lst_stansiya.keys())
        all_params = get_all_parameters()

        context['all_wells'] = all_wells_list
        context['all_params'] = all_params

        if request.method == 'GET':
            form = AnomalyAnalysisForm(
                wells_choices=[(w, w) for w in all_wells_list],
                params_choices=[(p, p) for p in all_params]
            )
            context['form'] = form
            return render(request, 'app_anomaly/index.html', context)

        elif request.method == 'POST':
            form = AnomalyAnalysisForm(
                request.POST,
                wells_choices=[(w, w) for w in all_wells_list],
                params_choices=[(p, p) for p in all_params]
            )

            if not form.is_valid():
                context['form'] = form
                context['error'] = 'Formada xatolar mavjud'
                return render(request, 'app_anomaly/index.html', context)

            selected_wells = form.cleaned_data['wells']
            selected_params = form.cleaned_data['parameters']
            time_period = int(form.cleaned_data['time_period'])
            anomaly_duration = int(form.cleaned_data['anomaly_duration'])
            magnitude = form.cleaned_data.get('magnitude')  # ✅ None bo'lishi mumkin
            recent_days = int(form.cleaned_data['recent_days'])
            sigma = float(form.cleaned_data.get('sigma', 2.0))

            today = datetime.now().date()
            filter_start_date = pd.Timestamp(today - relativedelta(days=recent_days))

            # ✅ ZILZILALAR - FAQAT MAGNITUDE KIRITILGAN BO'LSA
            earthquakes_df = None
            if magnitude is not None:
                try:
                    # ✅ Magnitude qiymatini float ga o'tkazamiz
                    mag_value = float(magnitude)
                    today = datetime.now().date()
                    start_date = today - relativedelta(months=time_period)

                    earthquakes_df = fetch_and_filter_earthquakes(
                        min_mag=mag_value,  # ✅ Foydalanuvchi kiritgan qiymat
                        start_date=pd.Timestamp(start_date),
                        end_date=pd.Timestamp(today)
                    )
                    logger.info(f"✅ {len(earthquakes_df)} ta zilzila yuklandi (min_mag={mag_value})")
                except Exception as e:
                    logger.warning(f"Zilzila error: {e}")
                    earthquakes_df = None
            else:
                logger.info("⚠️ Magnitude kiritilmadi - zilzilalar ko'rsatilmaydi")

            # GRAFIKLAR
            graphs_list = []
            # Keep a mapping of well_name -> list of anomalous parameters
            # create_anomaly_map_detailed expects a dict with well -> [params]
            anomalous_wells_dict = {}

            for well in selected_wells:
                for param in selected_params:
                    try:
                        key = f"{well.split(' | ')[0]} | {well.split(' | ')[1]}" if ' | ' in well else well
                        ssdi_id = lst_stansiya.get(key, {}).get(param)

                        if not ssdi_id:
                            logger.warning(f"⚠️ {well} - {param}: ssdi_id topilmadi")
                            continue

                        df = get_parameter_data_for_period(ssdi_id, time_period)

                        if df.empty:
                            logger.warning(f"⚠️ {well} - {param}: Ma'lumot yo'q")
                            continue

                        logger.info(f"🔍 {well} - {param}: {len(df)} ta ma'lumot topildi")

                        all_anomalies = detect_anomalies_in_data(df, sigma=sigma, min_duration_days=anomaly_duration)
                        recent_anomalies = []

                        if all_anomalies:
                            for anomaly in all_anomalies:
                                if anomaly['end_date'] >= filter_start_date:
                                    recent_anomalies.append(anomaly)

                        #filtrdan o'tgan anomaliya bo'lsa
                        if recent_anomalies:
                            logger.info(f"✅ {well} - {param}: {len(recent_anomalies)} ta anomaliya topildi!")

                            # well_name is the display name used in well_coords and map popups/tooltips
                            well_name = well.split(' | ')[1] if ' | ' in well else well
                            # Record parameter under this well for map popups/tooltips
                            anomalous_wells_dict.setdefault(well_name, []).append(param)

                            well_lat, well_lon = well_coords.get(well_name, (None, None))

                            graph_html = create_anomaly_chart(
                                df,
                                recent_anomalies,
                                well,
                                param,
                                sigma=sigma,
                                earthquakes_df=earthquakes_df,  # ✅ None bo'lishi mumkin
                                well_lat=well_lat,
                                well_lon=well_lon,
                                recent_days=recent_days,
                            )

                            if graph_html:
                                graphs_list.append({
                                    'well': well,
                                    'param': param,
                                    'html': graph_html,
                                    'anomaly_count': len(recent_anomalies)
                                })
                        else:
                            logger.info(f"ℹ️ {well} - {param}: Anomaliya topilmadi (min_duration={anomaly_duration})")

                    except Exception as e:
                        logger.error(f"Grafik xato ({well} - {param}): {e}")
                        continue

            # ✅ XARITA - HAMMASI UCHUN
            map_html = None
            try:
                map_html = create_anomaly_map_detailed(
                    all_wells_list=list(lst_stansiya.keys()),
                    well_coords=well_coords,
                    anomalous_wells_dict=anomalous_wells_dict
                )
                logger.info("✅ Xarita yaratildi")
            except Exception as e:
                logger.error(f"Xarita xato: {e}")

            # DATABASE
            try:
                for well in selected_wells:
                    for param in selected_params:
                        anomaly_count = len([g for g in graphs_list if g['well'] == well and g['param'] == param])

                        if anomaly_count > 0:
                            well_name = well.split(' | ')[1] if ' | ' in well else well
                            AnomalyRecord.objects.create(
                                skvajina=well_name,
                                parameter=param,
                                time_period_months=time_period,
                                anomaly_duration_days=anomaly_duration,
                                magnitude=magnitude,
                                detected_anomalies_count=anomaly_count,
                                is_active=True,
                                session_id=request.session.session_key
                            )
            except Exception as e:
                logger.error(f"Database xato: {e}")

            # derive set for counts/messages from the dict keys
            anomalous_wells = set(anomalous_wells_dict.keys())

            context.update({
                'form': form,
                'graphs_list': graphs_list,
                'map_html': map_html,  # ✅ XARITA
                'selected_wells': selected_wells,
                'selected_params': selected_params,
                'time_period': time_period,
                'anomaly_duration': anomaly_duration,
                'recent_days': recent_days,
                'magnitude': magnitude or 'Kiritilmagan',
                'anomalous_wells_count': len(anomalous_wells),
                'show_results': True,
            })

            messages.success(request, f'{len(anomalous_wells)} ta skvajinada anomaliya topildi!')
            return render(request, 'app_anomaly/index.html', context)

    except Exception as e:
        logger.error(f"View xato: {e}", exc_info=True)
        context['error'] = f'Xatolik: {str(e)}'
        context['form'] = AnomalyAnalysisForm(
            wells_choices=[(w, w) for w in []],
            params_choices=[(p, p) for p in []]
        )
        return render(request, 'app_anomaly/index.html', context)

@require_http_methods(["GET"])
def anomaly_history_view(request):
    try:
        records = AnomalyRecord.objects.filter(is_active=True).order_by('-created_at')[:50]
        context = {
            'records': records,
            'total_count': AnomalyRecord.objects.filter(is_active=True).count(),
        }
        return render(request, 'app_anomaly/history.html', context)
    except Exception as e:
        logger.error(f"Tarix xato: {e}")
        return render(request, 'app_anomaly/history.html', {'error': 'Xatolik yuz berdi'})