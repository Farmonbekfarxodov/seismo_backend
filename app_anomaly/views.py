# app_anomaly/views.py

import logging
import pandas as pd
import numpy as np
import folium
from datetime import datetime
from dateutil.relativedelta import relativedelta
from django.shortcuts import render
from django.contrib import messages
from django.views.decorators.http import require_http_methods
from django.core.cache import cache
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


def detect_anomalies_in_data(df, sigma=2.0, min_duration_days=5):
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
            sigma = float(form.cleaned_data.get('sigma', 2.0))

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
            anomalous_wells = set()

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

                        anomalies = detect_anomalies_in_data(df, sigma=sigma, min_duration_days=anomaly_duration)

                        if anomalies:
                            logger.info(f"✅ {well} - {param}: {len(anomalies)} ta anomaliya topildi!")
                            anomalous_wells.add(well.split(' | ')[1] if ' | ' in well else well)

                            well_name = well.split(' | ')[1] if ' | ' in well else well
                            well_lat, well_lon = well_coords.get(well_name, (None, None))

                            graph_html = create_anomaly_chart(
                                df,
                                anomalies,
                                well,
                                param,
                                sigma=sigma,
                                earthquakes_df=earthquakes_df,  # ✅ None bo'lishi mumkin
                                well_lat=well_lat,
                                well_lon=well_lon
                            )

                            if graph_html:
                                graphs_list.append({
                                    'well': well,
                                    'param': param,
                                    'html': graph_html,
                                    'anomaly_count': len(anomalies)
                                })
                        else:
                            logger.info(f"ℹ️ {well} - {param}: Anomaliya topilmadi (min_duration={anomaly_duration})")

                    except Exception as e:
                        logger.error(f"Grafik xato ({well} - {param}): {e}")
                        continue

            # ✅ XARITA - HAMMASI UCHUN
            map_html = None
            try:
                map_html = create_anomaly_map(
                    all_wells=list(lst_stansiya.keys()),
                    well_coords=well_coords,
                    anomalous_wells=anomalous_wells
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

            context.update({
                'form': form,
                'graphs_list': graphs_list,
                'map_html': map_html,  # ✅ XARITA
                'selected_wells': selected_wells,
                'selected_params': selected_params,
                'time_period': time_period,
                'anomaly_duration': anomaly_duration,
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


def create_anomaly_chart(df, anomalies, well_name, param_name, sigma=2.0,
                         earthquakes_df=None, well_lat=None, well_lon=None):
    """
    ✅ earthquakes_df=None bo'lsa zilzilalar ko'rsatilmaydi
    """
    try:
        values = df['value'].dropna()
        mean = values.mean()
        std = values.std()
        upper_bound = mean + sigma * std
        lower_bound = mean - sigma * std

        fig = make_subplots(specs=[[{"secondary_y": True}]])

        # ASOSIY CHIZIQ (BLUE)
        fig.add_trace(
            go.Scatter(
                x=df['date'],
                y=df['value'],
                mode='lines',
                name=f'{param_name}',
                line=dict(color='blue', width=2),
                hovertemplate='<b>Sana:</b> %{x|%d.%m.%Y}<br><b>Qiymat:</b> %{y:.3f}<extra></extra>'
            ),
            secondary_y=False
        )

        # UPPER BOUND (GREEN)
        fig.add_trace(
            go.Scatter(
                x=df['date'],
                y=[upper_bound] * len(df),
                mode='lines',
                name=f'UB (+{sigma}σ)',
                line=dict(color='green', width=2, dash='dash'),
            ),
            secondary_y=False
        )

        # MEAN (MAGENTA)
        fig.add_trace(
            go.Scatter(
                x=df['date'],
                y=[mean] * len(df),
                mode='lines',
                name='Mean',
                line=dict(color='magenta', width=2, dash='dash'),
            ),
            secondary_y=False
        )

        # LOWER BOUND (BLUE DASHED)
        fig.add_trace(
            go.Scatter(
                x=df['date'],
                y=[lower_bound] * len(df),
                mode='lines',
                name=f'LB (-{sigma}σ)',
                line=dict(color='blue', width=2, dash='dash'),
            ),
            secondary_y=False
        )

        # ✅ ANOMALIYALAR (QIZIL BO'YALGAN QISMLAR)
        for anomaly in anomalies:
            duration = (anomaly['end_date'] - anomaly['start_date']).days
            fig.add_vrect(
                x0=anomaly['start_date'],
                x1=anomaly['end_date'],
                fillcolor="red",
                opacity=0.3,
                layer="below",
                line_width=0,
                annotation_text=f"{duration} kun",
                annotation_position="top left",
                annotation_font_size=9,
                annotation_font_color="darkred",
            )

        # ✅ ZILZILALAR - FAQAT earthquakes_df bo'lsa
        if earthquakes_df is not None and not earthquakes_df.empty and well_lat is not None and well_lon is not None:
            try:
                earthquakes_df_copy = earthquakes_df.copy()
                earthquakes_df_copy['distance'] = destenc_vectorized(
                    well_lat, well_lon,
                    earthquakes_df_copy['Latitude'],
                    earthquakes_df_copy['Longitude']
                )

                # Create combined datetime without passing an invalid `format` parameter
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

                        hover_text = f"""
                        <b>Zilzila</b><br>
                        <b>Sana:</b> {date_val.strftime('%d.%m.%Y %H:%M')}<br>
                        <b>Magnituda:</b> {mag_val:.2f}<br>
                        <b>Chuqurlik:</b> {row['Depth']} km<br>
                        <b>Masofa:</b> {row['distance']:.1f} km
                        """

                        stem_x.extend([date_val, date_val, None])
                        stem_y.extend([0, mag_val, None])
                        hover_texts.extend(["", hover_text, ""])

                    fig.add_trace(
                        go.Scatter(
                            x=stem_x,
                            y=stem_y,
                            mode='lines',
                            line=dict(color='darkred', width=2),
                            name='Zilzilalar (Mb)',
                            hoverinfo='text',
                            text=hover_texts,
                            yaxis='y2'
                        ),
                        secondary_y=True
                    )

            except Exception as e:
                logger.warning(f"Zilzila qo'shish xato: {e}")

        fig.update_layout(
            title=f"<b>{well_name} - {param_name}</b><br><sub>({df['date'].min().strftime('%d.%m.%Y')} - {df['date'].max().strftime('%d.%m.%Y')})</sub>",
            xaxis_title="Sana",
            hovermode='x unified',
            height=500,
            template='plotly_white',
        )

        fig.update_yaxes(title_text=f"{param_name}", secondary_y=False)

        # Sanitize div id to keep only safe characters
        div_id_raw = f"graph_{well_name}_{param_name}"
        div_id_safe = re.sub(r'[^A-Za-z0-9_\-]', '_', div_id_raw)
        return fig.to_html(include_plotlyjs='cdn', div_id=div_id_safe)

    except Exception as e:
        logger.error(f"Grafik xato: {e}")
        return None


def create_anomaly_map(all_wells, well_coords, anomalous_wells):
    """
    ✅ YANGI: Yer yoriqlarining rangi avvalgidek (CONF va RATE bo'yicha)
    """
    try:
        if anomalous_wells:
            selected_coords = [well_coords[w] for w in anomalous_wells if w in well_coords]
            if selected_coords:
                center_lat = np.mean([c[0] for c in selected_coords])
                center_lon = np.mean([c[1] for c in selected_coords])
            else:
                center_lat, center_lon = 41.2995, 69.2401
        else:
            center_lat, center_lon = 41.2995, 69.2401

        # ✅ ASOSIY XARITA
        m = folium.Map(
            location=[center_lat, center_lon],
            zoom_start=7,
            tiles="OpenStreetMap",
            attr="© OpenStreetMap contributors"
        )

        # ✅ FULLSCREEN BUTTON
        from folium.plugins import Fullscreen
        Fullscreen(
            position='topleft',
            title='To\'liq ekran',
            title_cancel='Chiqish',
            force_separate_button=True
        ).add_to(m)

        # ✅ XARITA REJIMLAR

        folium.TileLayer(
            tiles='https://stamen-tiles.a.ssl.fastly.net/terrain/{z}/{x}/{y}.png',
            attr='Map tiles by <a href="http://stamen.com">Stamen Design</a>',
            name='Terrain',
            overlay=False,
            control=True
        ).add_to(m)

        folium.TileLayer(
            tiles='https://cartodb-basemaps-{s}.global.ssl.fastly.net/light_all/{z}/{x}/{y}.png',
            attr='© OpenStreetMap contributors © CARTO',
            name='Light Map',
            overlay=False,
            control=True
        ).add_to(m)

        folium.TileLayer(
            tiles='https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
            attr='Tiles © Esri',
            name='Satellite',
            overlay=False,
            control=True
        ).add_to(m)

        # ✅ YER YORIQLARI (FEATURE GROUP - CONF va RATE bo'yicha ranglar)
        cracks_layer = folium.FeatureGroup(name='🌍 Yer yoriqlari', show=True)
        try:
            all_cracks = load_all_cracks_shapefiles()
            if all_cracks is not None:
                # ✅ add_cracks_to_map() ishlatish - rangli chiqadi!
                add_cracks_to_map(cracks_layer, all_cracks)
                logger.info(f"✅ {len(all_cracks)} ta yer yorig'i qo'shildi")
        except Exception as e:
            logger.warning(f"Yer yoriqlari xato: {e}")

        cracks_layer.add_to(m)

        # ✅ SEYSMOGEN ZONALARI (FEATURE GROUP)
        seismo_layer = folium.FeatureGroup(name='🔴 Seysmogen zonalari', show=True)
        try:
            seismogenic = load_seismogenic_zones()
            if seismogenic is not None:
                add_seismogenic_zones_to_map(seismo_layer, seismogenic)
                logger.info("✅ Seysmogen zonalari qo'shildi")
        except Exception as e:
            logger.warning(f"Seysmogen xato: {e}")

        seismo_layer.add_to(m)

        # ✅ ANOMALIYA SKVAJINALAR (BLUE UCHBURCHAK)
        anomaly_wells_layer = folium.FeatureGroup(name='🔵 Anomaliya topilgan', show=True)
        for well in anomalous_wells:
            if well in well_coords:
                lat, lon = well_coords[well]

                triangle_icon = folium.DivIcon(
                    html=f'<div style="width: 0; height: 0; border-left: 10px solid transparent; border-right: 10px solid transparent; border-bottom: 20px solid blue;"></div>',
                    icon_size=(20, 20),
                    icon_anchor=(10, 20)
                )

                popup_html = f"""
                <div style="font-family: Arial; font-size: 12px; width: 250px;">
                    <h4 style="color: blue; margin-bottom: 10px;">🔴 ANOMALIYA TOPILGAN</h4>
                    <table style="width: 100%; border-collapse: collapse;">
                        <tr style="background-color: #f8f9fa;">
                            <td style="padding: 5px; border: 1px solid #dee2e6; font-weight: bold;">Skvajina:</td>
                            <td style="padding: 5px; border: 1px solid #dee2e6;"><b>{well}</b></td>
                        </tr>
                    </table>
                </div>
                """

                folium.Marker(
                    location=[lat, lon],
                    icon=triangle_icon,
                    popup=folium.Popup(popup_html, max_width=300),
                    tooltip=f"<b>{well}</b> - Anomaliya"
                ).add_to(anomaly_wells_layer)

        anomaly_wells_layer.add_to(m)

        # ✅ BOSHQA SKVAJINALAR (LIGHT BLUE UCHBURCHAK)
        normal_wells_layer = folium.FeatureGroup(name='⚪ Anomaliya yo\'q', show=True)
        for well_data in all_wells:
            well_name = well_data.split(' | ')[1] if ' | ' in well_data else well_data
            if well_name not in anomalous_wells and well_name in well_coords:
                lat, lon = well_coords[well_name]

                triangle_icon = folium.DivIcon(
                    html=f'<div style="width: 0; height: 0; border-left: 10px solid transparent; border-right: 10px solid transparent; border-bottom: 20px solid lightblue;"></div>',
                    icon_size=(20, 20),
                    icon_anchor=(10, 20)
                )

                popup_html = f"""
                <div style="font-family: Arial; font-size: 12px; width: 250px;">
                    <h4 style="color: lightblue; margin-bottom: 10px;">⚪ ANOMALIYA YO'Q</h4>
                    <table style="width: 100%; border-collapse: collapse;">
                        <tr style="background-color: #f8f9fa;">
                            <td style="padding: 5px; border: 1px solid #dee2e6; font-weight: bold;">Skvajina:</td>
                            <td style="padding: 5px; border: 1px solid #dee2e6;"><b>{well_name}</b></td>
                        </tr>
                    </table>
                </div>
                """

                folium.Marker(
                    location=[lat, lon],
                    icon=triangle_icon,
                    popup=folium.Popup(popup_html, max_width=300),
                    tooltip=f"<b>{well_name}</b> - Normal"
                ).add_to(normal_wells_layer)

        normal_wells_layer.add_to(m)

        # ✅ LAYER CONTROL (O'ng yuqorida - collapsed)
        folium.LayerControl(
            position='topright',
            collapsed=True,
            name='Rejimlar'
        ).add_to(m)

        # ✅ LEGEND
        legend_html = '''
        <div style="position: fixed; bottom: 100px; left: 50px; width: 280px; 
                    background-color: white; border:2px solid grey; z-index:9999;
                    font-size:12px; padding: 10px; border-radius: 5px;">
            <p style="margin: 0; font-weight: bold; border-bottom: 1px solid #ccc; padding-bottom: 5px;">
                📍 ANOMALIYA XARITASI
            </p>

            <p style="margin: 10px 0; font-weight: bold; color: #333;">SKVAJINALAR:</p>
            <p style="margin: 5px 0;">
                <span style="display: inline-block; width: 0; height: 0; border-left: 7px solid transparent; border-right: 7px solid transparent; border-bottom: 14px solid blue; margin-right: 8px;"></span>
                <b>Anomaliya topilgan</b>
            </p>
            <p style="margin: 5px 0;">
                <span style="display: inline-block; width: 0; height: 0; border-left: 7px solid transparent; border-right: 7px solid transparent; border-bottom: 14px solid lightblue; margin-right: 8px;"></span>
                <b>Anomaliya yo'q</b>
            </p>

           
        </div>
        '''

        m.get_root().html.add_child(folium.Element(legend_html))

        logger.info("✅ Xarita muvaffaqiyatli yaratildi")
        return m._repr_html_()

    except Exception as e:
        logger.error(f"Xarita xato: {e}", exc_info=True)
        return None


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