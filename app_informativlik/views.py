from django.http import HttpResponse
from folium import Map
import numpy as np
import pandas as pd
import math
from math import erf
from scipy.stats import norm
import logging
from seismos_app.views import *

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






def merge_intervals(intervals):
    """
    Intervallarni birlashtirish (1-koddan)
    """
    if not intervals:
        return []

    intervals = sorted(intervals, key=lambda x: x[0])
    merged = [intervals[0]]

    for current in intervals[1:]:
        last = merged[-1]
        if current[0] <= last[1]:
            merged[-1] = (last[0], max(last[1], current[1]))
        else:
            merged.append(current)

    return merged


def compute_q_advanced(m, n, t, T):
    """
    1-koddagi TO'LIQ Q FORMULASI
    μ va δ tuzatish koeffitsientlari bilan
    """
    if n == 0 or T == 0 or t == 0:
        return 0.0, 0.0, 0.0

    mn = m / n
    P = t / T

    # Nolga bo'lishni oldini olish
    if mn == 0 or mn == 1 or P == 0 or P == 1:
        return 0.0, 0.0, 0.0

    try:
        # μ va δ (1-koddagi formula)
        mu = (1 - mn) / (0.5 + math.sqrt(0.25 + m * (1 - mn)))
        delta = (1 - mu) / (1 + mu)

        # Q formulasi (to'liq)
        numerator = (delta * mn / (1 - mn)) * ((1 - P) / P)

        if numerator <= 0:
            return 0.0, delta, mu

        q = 0.25 * math.log(numerator)

        return q, delta, mu

    except Exception as e:
        logging.error(f"Q hisoblashda xato: {e}")
        return 0.0, 0.0, 0.0


def gauss_phi(xi):
    """
    Gauss ehtimollik funksiyasi (1-koddan)
    """
    return 0.5 * (1 + erf(xi / math.sqrt(2)))


def filter_long_sequences(anom_series, min_len):
    """
    Faqat ketma-ket kamida `min_len` kunlik anomaliyalarni saqlaydi
    """
    if anom_series.empty:
        return anom_series

    seq = anom_series.copy()
    count = 0
    indices_to_zero = []

    for i in range(len(seq)):
        if seq.iloc[i] == 1:
            count += 1
        else:
            if 0 < count < min_len:
                indices_to_zero.extend(range(i - count, i))
            count = 0

    if 0 < count < min_len:
        indices_to_zero.extend(range(len(seq) - count, len(seq)))

    if indices_to_zero:
        seq.iloc[indices_to_zero] = 0

    return seq



def calculate_informativity_improved(data_series, earthquakes_df, window_years,
                                     anomaly_duration, std_factor,
                                     timedelta_before, timedelta_after):
    """
    YAXSHILANGAN VERSIYA:
    1. 1-koddagi intervallarni birlashtirish
    2. 1-koddagi to'liq Q formulasi
    3. 2-koddagi median silliqlashtirish
    4. 2-koddagi segment tahlili
    """
    if data_series.empty or earthquakes_df.empty:
        logging.warning("Ma'lumotlar yoki zilzilalar bo'sh")
        return None

    captured_earthquake_indices = []
    total_t = 0
    total_m = 0
    segment_results = []

    all_anomalies = pd.Series(0, index=data_series.index)

    start_year = data_series.index.min().year
    end_year = data_series.index.max().year

    # ============================================
    # 1. HAR SEGMENT UCHUN ANOMALIYA ANIQLASH
    # ============================================
    for year_start in range(start_year, end_year + 1, window_years):
        year_end = year_start + window_years - 1

        mask = (data_series.index >= f"{year_start}-01-01") & \
               (data_series.index <= f"{year_end}-12-31")
        segment = data_series.loc[mask].copy()

        if segment.empty:
            continue

        mean_val = segment.mean()
        std_val = segment.std()

        if std_val == 0 or np.isnan(std_val):
            continue

        upper = mean_val + std_factor * std_val
        lower = mean_val - std_factor * std_val

        segment_df = pd.DataFrame({'value': segment})
        segment_df['Anomaly'] = (
                (segment_df['value'] > upper) |
                (segment_df['value'] < lower)
        ).astype(int)

        # MUHIM: Qisqa anomaliyalarni filtrlash
        segment_df['Anomaly'] = filter_long_sequences(
            segment_df['Anomaly'], anomaly_duration
        )

        all_anomalies.loc[segment_df.index] = segment_df['Anomaly']

        # 🔴 YANGI: INTERVALLARNI BIRLASHTIRISH (1-koddan)
        anomalies = segment_df[segment_df['Anomaly'] == 1]

        if len(anomalies) > 0:
            intervals = []
            current_start = anomalies.index[0]

            for i in range(1, len(anomalies)):
                prev = anomalies.index[i - 1]
                now = anomalies.index[i]

                if (now - prev).days > 1:
                    intervals.append((current_start, prev))
                    current_start = now

            intervals.append((current_start, anomalies.index[-1]))

            # Intervallarni birlashtirish
            merged = merge_intervals(intervals)

            # t = birlashtirilgan intervallar uzunligi
            t_segment = sum((end - start).days + 1 for start, end in merged)
        else:
            t_segment = 0
            merged = []

        total_t += t_segment

        # Zilzilalar
        segment_start = pd.Timestamp(f"{year_start}-01-01")
        segment_end = pd.Timestamp(f"{year_end}-12-31")

        seg_eq = earthquakes_df[
            (earthquakes_df['Event_date'] >= segment_start) &
            (earthquakes_df['Event_date'] <= segment_end)
            ]

        segment_results.append({
            'year_start': year_start,
            'year_end': year_end,
            'T': len(segment_df),
            't': t_segment,
            'n': len(seg_eq),
            'm': 0,
            'mean': float(mean_val),
            'std': float(std_val),
            'upper': float(upper),
            'lower': float(lower),
            'merged_intervals': merged  # 🔴 YANGI
        })

    # ============================================
    # 2. ZILZILALARNI TEKSHIRISH (TO'G'RI USUL)
    # ============================================
    for eq_idx, eq_row in earthquakes_df.iterrows():
        eq_date = pd.to_datetime(eq_row['Event_date'])

        window_start = eq_date - pd.Timedelta(days=timedelta_before)
        window_end = eq_date

        # 🔴 YANGI: Intervallar bo'yicha tekshirish
        is_captured = False

        for seg in segment_results:
            if seg['year_start'] <= eq_date.year <= seg['year_end']:
                for start, end in seg['merged_intervals']:
                    # Zilzila intervalga tushyaptimi?
                    if start <= eq_date <= end:
                        # Va interval oldinda boshlanganmi?
                        if start >= window_start:
                            is_captured = True
                            seg['m'] += 1
                            break

                if is_captured:
                    break

        if is_captured:
            total_m += 1
            if eq_idx not in captured_earthquake_indices:
                captured_earthquake_indices.append(eq_idx)

    # ============================================
    # 3. UMUMIY STATISTIKA
    # ============================================
    T = len(data_series)
    n = len(earthquakes_df)

    if T == 0 or n == 0 or total_t == 0:
        logging.warning(f"Yetarli ma'lumot yo'q: T={T}, n={n}, t={total_t}")
        return None

    t_T = float(total_t / T)
    m_n = float(total_m / n if n > 0 else 0)

    # ξ va Φ(ξ)
    try:
        denominator = np.sqrt((1 / n) * t_T * (1 - t_T))
        if denominator == 0:
            xi = 0.0
            phi_xi = 0.5
        else:
            xi = float((m_n - t_T) / denominator)
            phi_xi = float(norm.cdf(xi))
    except Exception as e:
        logging.error(f"Phi(xi) xato: {e}")
        xi = 0.0
        phi_xi = 0.0

    # 🔴 YANGI: 1-KODDAGI TO'LIQ Q FORMULASI
    q, delta, mu = compute_q_advanced(total_m, n, total_t, T)

    # Baholash
    if phi_xi > 0.95:
        reliability = "Ishonchli (tasodifiy emas)"
        reliability_level = "Yuqori"
    else:
        reliability = "Ishonchsiz (tasodifiy bo'lishi mumkin)"
        reliability_level = "Past"

    if q > 0.5:
        informativity = "Informativ darakchi"
        informativity_level = "Yuqori"
    elif q > 0.3:
        informativity = "Foydali darakchi"
        informativity_level = "O'rtacha"
    elif q > 0.2:
        informativity = "Noaniq darakchi"
        informativity_level = "Past"
    else:
        informativity = "Informativ emas"
        informativity_level = "Juda past"

    logging.info(
        f"📊 Natija: T={T}, t={total_t}, n={n}, m={total_m}, "
        f"t/T={t_T:.4f}, m/n={m_n:.4f}, Φ(ξ)={phi_xi:.4f}, "
        f"q={q:.4f}, δ={delta:.4f}, μ={mu:.4f}"
    )

    # 🔴 Session uchun segment_results ni tozalash
    clean_segment_results = []
    for seg in segment_results:
        clean_seg = seg.copy()
        # Timestamp obyektlarini olib tashlash
        clean_seg.pop('merged_intervals', None)
        clean_segment_results.append(clean_seg)

    return {
        'T': T,
        't': total_t,
        'n': n,
        'm': total_m,
        't_T': t_T,
        'm_n': m_n,
        'xi': xi,
        'phi_xi': phi_xi,
        'q': q,
        'delta': delta,  # 🔴 YANGI
        'mu': mu,  # 🔴 YANGI
        'reliability': reliability,
        'reliability_level': reliability_level,
        'informativity': informativity,
        'informativity_level': informativity_level,
        'segment_results': clean_segment_results,
        'segment_results_full': segment_results,  # Intervallar bilan (sessionga saqlanmaydi)
        'captured_earthquakes': captured_earthquake_indices,
        'all_anomalies': all_anomalies
    }



def create_informativity_graph(graph_data, std_factor, min_mag, min_mlgr):
    """
    Informativlik uchun grafik yaratadi (results_view formatida)
    Har bir grafik ostida segment jadvali bilan
    """
    num_graphs = len(graph_data)
    if num_graphs == 0:
        return None

    # Har bir grafik uchun HTML yaratish
    graphs_html = []

    for idx, data in enumerate(graph_data):
        # Grafik uchun figure yaratish
        fig = make_subplots(
            rows=1,
            cols=1,
            specs=[[{"secondary_y": True}]],
        )

        trace_color = COLOR_PALETTE[idx % len(COLOR_PALETTE)]

        x_val = data['x']
        y_val = data['y']
        mean = data['mean']
        sigma = data['sigma']
        param = data['param']
        skvajina = data['skvajina']
        key = data['key']
        earthquakes_all = data['earthquakes_all']
        captured_indices = data['captured_indices']
        segment_results = data.get('segment_results', [])

        # Parametr grafikini chizish
        y_all = plot_data_with_anomalies(
            fig, x_val, y_val, mean, sigma, std_factor,
            1, 1, trace_color, param, key
        )

        fig.update_yaxes(
            title_text=f"{param} Qiymati",
            range=[min(y_all) * 0.9, max(y_all) * 1.1],
            row=1,
            col=1,
            secondary_y=False,
        )

        # Zilzilalarni chizish
        if not earthquakes_all.empty:
            mag_col = 'Mb' if 'Mb' in earthquakes_all.columns else MAIN_MAGNITUDE_COLUMN
            earthquakes_all[mag_col] = pd.to_numeric(
                earthquakes_all[mag_col], errors='coerce'
            )
            earthquakes_all.dropna(subset=[mag_col], inplace=True)

            if not earthquakes_all.empty:
                # Fon zilzilalar
                background_earthquakes = earthquakes_all.loc[
                    ~earthquakes_all.index.isin(captured_indices)
                ].copy()

                if not background_earthquakes.empty:
                    stem_x_bg = []
                    stem_y_bg = []
                    hover_texts_bg = []

                    for _, eq_row in background_earthquakes.iterrows():
                        eq_date = eq_row['Event_date']
                        mag_val = eq_row[mag_col]
                        distance = eq_row.get('R(km)', 'N/A')
                        mlgr_val = eq_row.get('M/lgR', 'N/A')
                        time_str = eq_date.strftime("%d.%m.%Y")

                        stem_x_bg.extend([eq_date, eq_date, None])
                        stem_y_bg.extend([0, mag_val, None])

                        hover_text = (
                            f"<b>⚫ FON ZILZILA</b><br>"
                            f"Vaqt: {time_str}<br>"
                            f"Mb: {mag_val:.2f}<br>"
                            f"Masofa: {distance:.1f} km<br>"
                            f"M/lgR: {mlgr_val:.2f}"
                        )
                        hover_texts_bg.extend(["", hover_text, ""])

                    if stem_x_bg:
                        fig.add_trace(
                            go.Scatter(
                                x=stem_x_bg,
                                y=stem_y_bg,
                                mode="lines",
                                line=dict(color="blue", width=1.5, dash='dot'),
                                name=f"Fon Zilzilalar (≥{min_mag})",
                                hoverinfo="text",
                                text=hover_texts_bg,
                                showlegend=True,
                                yaxis="y2",
                            ),
                            row=1,
                            col=1,
                            secondary_y=True,
                        )

                # Informativ zilzilalar
                informative_earthquakes = earthquakes_all.loc[
                    earthquakes_all.index.isin(captured_indices)
                ].copy()

                if not informative_earthquakes.empty:
                    stem_x = []
                    stem_y = []
                    hover_texts = []

                    for _, eq_row in informative_earthquakes.iterrows():
                        eq_date = eq_row['Event_date']
                        mag_val = eq_row[mag_col]
                        distance = eq_row.get('R(km)', 'N/A')
                        mlgr_val = eq_row.get('M/lgR', 'N/A')
                        time_str = eq_date.strftime("%d.%m.%Y")

                        stem_x.extend([eq_date, eq_date, None])
                        stem_y.extend([0, mag_val, None])

                        hover_text = (
                            f"<b>🔴 INFORMATIV ZILZILA</b><br>"
                            f"Vaqt: {time_str}<br>"
                            f"Mb: {mag_val:.2f}<br>"
                            f"Masofa: {distance:.1f} km<br>"
                            f"M/lgR: {mlgr_val:.2f}"
                        )
                        hover_texts.extend(["", hover_text, ""])

                    if stem_x:
                        fig.add_trace(
                            go.Scatter(
                                x=stem_x,
                                y=stem_y,
                                mode="lines",
                                line=dict(color="blue", width=3),
                                name=f"Informativ Zilzilalar (≥{min_mag})",
                                hoverinfo="text",
                                text=hover_texts,
                                showlegend=True,
                                yaxis="y2",
                            ),
                            row=1,
                            col=1,
                            secondary_y=True,
                        )

                    max_mag = earthquakes_all[mag_col].max() * 1.1
                    fig.update_yaxes(
                        range=[0, max_mag],
                        secondary_y=True,
                        title_text="Magnituda (Mb)",
                        row=1,
                        col=1,
                    )

        # X-o'qni moslashtirish
        if x_val:
            delta = timedelta(days=15)
            x_min = min(x_val)
            x_max = max(x_val)

            fig.update_xaxes(
                range=[x_min - delta, x_max + delta],
                type="date",
                showgrid=True,
                griddash="dot",
                row=1,
                col=1,
            )

        # Grid
        fig.update_yaxes(
            showgrid=True,
            gridwidth=0.15,
            gridcolor="black",
            griddash="dot",
            row=1,
            col=1,
            secondary_y=False,
        )
        fig.update_yaxes(
            showgrid=True,
            gridwidth=0.15,
            gridcolor="gray",
            griddash="dot",
            row=1,
            col=1,
            secondary_y=True,
        )

        # Layout
        fig.update_layout(
            title_text=f"{skvajina} - {param}",
            height=600,
            width=1200,
            autosize=True,
            showlegend=True,
            plot_bgcolor="gainsboro",
            hovermode="x unified",
            legend=dict(
                x=0.01, y=0.99,
                bgcolor="rgba(255,255,255,0.9)",
                bordercolor="rgba(0,0,0,0.3)",
                borderwidth=1,
                xanchor="left", yanchor="top",
                font=dict(size=10),
            ),
            title=dict(font=dict(size=18), x=0.5, xanchor="center"),
            margin=dict(l=60, r=60, t=80, b=60),
        )

        config = {
            "displayModeBar": True,
            "scrollZoom": True,
            "doubleClick": "reset+autosize",
            "modeBarButtonsToAdd": ["pan2d", "zoomIn2d", "zoomOut2d", "autoScale2d", "resetScale2d"],
            "responsive": True,
            "displaylogo": False,
        }

        graph_html = fig.to_html(full_html=False, include_plotlyjs="cdn", config=config)

        # Segment jadvali HTML
        table_html = f"""
        <div style="margin: 20px 0; padding: 15px; background: #f8f9fa; border-radius: 8px;">
            <h5 style="color: #28a745; margin-bottom: 15px;">📊 {skvajina} - {param} uchun Segmentlar Jadvali</h5>
            <div style="overflow-x: auto;">
                <table style="width: 100%; border-collapse: collapse; font-size: 14px;">
                    <thead>
                        <tr style="background: #28a745; color: white;">
                            <th style="padding: 10px; border: 1px solid #ddd; text-align: center;">Davr</th>
                            <th style="padding: 10px; border: 1px solid #ddd; text-align: center;">T (kun)</th>
                            <th style="padding: 10px; border: 1px solid #ddd; text-align: center;">t (anom.)</th>
                            <th style="padding: 10px; border: 1px solid #ddd; text-align: center;">n (zilz.)</th>
                            <th style="padding: 10px; border: 1px solid #ddd; text-align: center;">m (tut.)</th>
                            <th style="padding: 10px; border: 1px solid #ddd; text-align: center;">O'rtacha</th>
                            <th style="padding: 10px; border: 1px solid #ddd; text-align: center;">Std</th>
                        </tr>
                    </thead>
                    <tbody>
        """

        for seg in segment_results:
            row_bg = "#ffffff" if segment_results.index(seg) % 2 == 0 else "#f8f9fa"
            table_html += f"""
                <tr style="background: {row_bg};">
                    <td style="padding: 8px; border: 1px solid #ddd; text-align: center;">{seg['year_start']}-{seg['year_end']}</td>
                    <td style="padding: 8px; border: 1px solid #ddd; text-align: center;">{seg['T']}</td>
                    <td style="padding: 8px; border: 1px solid #ddd; text-align: center;">{seg['t']}</td>
                    <td style="padding: 8px; border: 1px solid #ddd; text-align: center;">{seg['n']}</td>
                    <td style="padding: 8px; border: 1px solid #ddd; text-align: center;">{seg['m']}</td>
                    <td style="padding: 8px; border: 1px solid #ddd; text-align: center;">{seg['mean']:.4f}</td>
                    <td style="padding: 8px; border: 1px solid #ddd; text-align: center;">{seg['std']:.4f}</td>
                </tr>
            """

        table_html += """
                    </tbody>
                </table>
            </div>
        </div>
        """

        # Grafik va jadval birlashtirish
        combined_html = f"""
        <div style="margin-bottom: 40px; padding: 20px; background: white; border-radius: 10px; box-shadow: 0 2px 8px rgba(0,0,0,0.1);">
            {graph_html}
            {table_html}
        </div>
        """

        graphs_html.append(combined_html)

    # Barcha grafiklarni birlashtirish
    return "\n".join(graphs_html)

def add_map_data_folium(selected_keys, well_coords, earthquake_data, min_mag, min_mlgr,
                        captured_earthquake_indices=None, show_radius=True):
    """
    Folium yordamida interaktiv xarita yaratadi.

    Parametrlar:
    - selected_keys: Tanlangan skvajinalar ro'yxati
    - well_coords: Skvajina koordinatalari
    - earthquake_data: Zilzilalar DataFrame
    - min_mag: Minimal magnituda
    - min_mlgr: Minimal M/lgR
    - captured_earthquake_indices: Informativ zilzilalar indekslari (list yoki set)
    - show_radius: Radius doiralarini ko'rsatish (True/False)
    """
    all_wells = get_all_wells_coordinates()
    all_filtered_earthquakes = earthquake_data.copy()
    selected_well_names = set()

    for key in selected_keys:
        selected_well_names.add(key.split(" | ")[1])

    # Dublikatlarni olib tashlash (faqat mavjud ustunlar bo'yicha)
    if not all_filtered_earthquakes.empty:
        # Asosiy ustunlar
        essential_cols = ['Event_date', 'Latitude', 'Longitude']

        # Qo'shimcha ustunlar (mavjud bo'lsa)
        optional_cols = ['Event_time', 'Depth', 'Mb', 'M/lgR', 'R(km)']

        # Mavjud ustunlarni aniqlash
        subset_cols = [col for col in essential_cols if col in all_filtered_earthquakes.columns]

        if len(subset_cols) > 0:
            try:
                all_filtered_earthquakes = all_filtered_earthquakes.drop_duplicates(
                    subset=subset_cols,
                    keep='first'
                )
            except Exception as e:
                logging.warning(f"Dublikatlarni olib tashlashda xato: {e}")

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

    m: Map = folium.Map(
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

    # Turli xil fon xaritalari qo'shish
    try:
        folium.TileLayer(
            tiles='https://stamen-tiles.a.ssl.fastly.net/terrain/{z}/{x}/{y}.png',
            attr='Map tiles by Stamen Design',
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

    # YER YORIQLARINI QO'SHISH
    logging.info("Yer yoriqlarini yuklash boshlandi...")
    all_cracks = load_all_cracks_shapefiles()
    crack_colors = {}
    if all_cracks is not None:
        crack_colors = add_cracks_to_map(m, all_cracks)
        logging.info(f"Yer yoriqlari xaritaga qo'shildi: {len(all_cracks)} ta")
    else:
        logging.warning("Yer yoriqlari yuklanmadi")

    # SEYSMOGEN ZONALARNI QO'SHISH
    logging.info("Seysmogen zonalarni yuklash boshlandi...")
    seismogenic_zones = load_seismogenic_zones()
    zone_colors = {}
    if seismogenic_zones is not None:
        zone_colors = add_seismogenic_zones_to_map(m, seismogenic_zones)
        logging.info(f"Seysmogen zonalar xaritaga qo'shildi: {len(seismogenic_zones)} ta")
    else:
        logging.warning("Seysmogen zonalar yuklanmadi")

    # Barcha skvajinalarni xaritaga qo'shish (TANLANMAGANLAR)
    for well_name, (lat, lon) in all_wells.items():
        if well_name not in selected_well_names:
            well_info = get_well_detailed_info(well_name)

            tooltip_html = f"""
                            <div style="width: 450px; font-family: Arial; font-size: 12px;">
                                <h4 style="color: #2c3e50; margin-bottom: 10px;">Skvajina ma'lumotlari</h4>
                                <table style="width: 100%; border-collapse: collapse;">
                                    <tr style="background-color: #f8f9fa;">
                                        <td style="padding: 5px; border: 1px solid #dee2e6; font-weight: bold;">nomi:</td>
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

    # Tanlangan skvajinalarni xaritaga qo'shish
    for key in selected_keys:
        _, skvajina = key.split(" | ")
        lat, lon = well_coords.get(skvajina, (None, None))
        if lat is not None and lon is not None:
            well_info = get_well_detailed_info(skvajina)

            tooltip_html = f"""
                            <div style="width: 450px; font-family: Arial; font-size: 12px;">
                                <h4 style="color: #1e88e5; margin-bottom: 10px;">Tanlangan skvajina</h4>
                                <table style="width: 100%; border-collapse: collapse;">
                                   <tr style="background-color: #f8f9fa;">
                                        <td style="padding: 5px; border: 1px solid #dee2e6; font-weight: bold;">nomi:</td>
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

                                </table>
                                <p style="margin-top: 10px; color: #1565c0; font-weight: bold;">✓ Tanlangan skvajina</p>
                            </div>
                        """

            triangle_icon = folium.DivIcon(
                html='<div style="width: 0; height: 0; border-left: 10px solid transparent; border-right: 10px solid transparent; border-bottom: 20px solid blue;"></div>',
                icon_size=(20, 20),
                icon_anchor=(10, 20)
            )

            folium.Marker(
                location=[lat, lon],
                tooltip=folium.Tooltip(tooltip_html, sticky=True),
                icon=triangle_icon,
            ).add_to(m)
            # JavaScript yordamida radiuslarni dastlab ko'rsatish va click bilan boshqarish
            try:
                mlgr_val = min_mlgr if min_mlgr > 0 else 0.5
                radii_data = [
                    (5, "#66ccff"),
                    (6, "#3399ff"),
                    (7, "#0033cc"),
                ]

                # Har bir radius uchun ma'lumot tayyorlash
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

                # JavaScript kodi - radiuslarni dastlab ko'rsatish va toggle qilish
                js_code = f"""
                            <script>
                            (function() {{
                                var wellLat = {lat};
                                var wellLon = {lon};
                                var wellName = "{skvajina}";
                                var circlesInfo = {circles_info};
                                var circlesLayer = null;
                                var isVisible = true;

                                document.addEventListener("DOMContentLoaded", function() {{
                                    var map = window.map || Object.values(window).find(v => v instanceof L.Map);
                                    if (!map) {{
                                        console.error("Xarita topilmadi");
                                        return;
                                    }}

                                    // Dastlab aylanalarni ko'rsatish
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
                                    circlesLayer.addTo(map);

                                    // Barcha markerlarni topish
                                    map.eachLayer(function(layer) {{
                                        if (layer instanceof L.Marker) {{
                                            var latlng = layer.getLatLng();
                                            if (Math.abs(latlng.lat - wellLat) < 0.0001 && 
                                                Math.abs(latlng.lng - wellLon) < 0.0001) {{

                                                // Markerga click listener qo'shish (faqat aylanalarni toggle qilish)
                                                layer.on('click', function(e) {{
                                                    L.DomEvent.stopPropagation(e);

                                                    if (isVisible) {{
                                                        // Aylanalarni yashirish
                                                        map.removeLayer(circlesLayer);
                                                        isVisible = false;
                                                    }} else {{
                                                        // Aylanalarni ko'rsatish
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

    # Zilzilalarni xaritaga qo'shish
    if not all_filtered_earthquakes.empty:
        # captured_earthquake_indices ni set ga aylantirish
        captured_set = set(captured_earthquake_indices) if captured_earthquake_indices else set()

        # Magnituda ustunini aniqlash
        mag_col = MAIN_MAGNITUDE_COLUMN
        if 'Mb' in all_filtered_earthquakes.columns:
            mag_col = 'Mb'
        elif 'M' in all_filtered_earthquakes.columns:
            mag_col = 'M'

        for idx, row in all_filtered_earthquakes.iterrows():
            lat = row.get(LATITUDE_COLUMN, None)
            lon = row.get(LONGITUDE_COLUMN, None)
            mag_val = row.get(mag_col, None)
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
                # Zilzila informativ yoki fon ekanligini aniqlash
                is_captured = idx in captured_set

                if is_captured:
                    # INFORMATIV ZILZILA - QIZIL
                    color = "red"
                    radius = mag_val * 3
                    tooltip_prefix = "🔴 INFORMATIV"
                else:
                    # FON ZILZILA - Magnituda bo'yicha rang
                    if mag_val >= 6:
                        color = "darkblue"
                        radius = mag_val * 2
                    elif mag_val >= 5:
                        color = "blue"
                        radius = mag_val * 1.8
                    elif mag_val >= 4:
                        color = "lightblue"
                        radius = mag_val * 1.5
                    else:
                        color = "gray"
                        radius = mag_val * 1.2
                    tooltip_prefix = "🔵 FON"

                tooltip_html = f"""
                    <b>{tooltip_prefix} ZILZILA</b><br>
                    Sana: {date_val}<br>
                    Magnituda ({mag_col}): {mag_val:.2f}<br>
                    Chuqurlik (km): {depth_val}<br>
                    Masofa (km): {distance_val}<br>
                    M/lgR: {mlgr_val}<br>
                """

                folium.CircleMarker(
                    location=[lat, lon],
                    radius=radius,
                    color=color,
                    fill=True,
                    fill_color=color,
                    fill_opacity=0.7 if is_captured else 0.5,
                    stroke=True,
                    weight=2 if is_captured else 1,
                    tooltip=tooltip_html
                ).add_to(m)

    # Legend
    legend_items = [
        '<p><b>Xarita elementlari:</b></p>',
        '<p><i class="fa fa-circle" style="color:red"></i> 🔴 Informativ zilzilalar</p>',
        '<p><i class="fa fa-circle" style="color:darkblue"></i> 🔵 Zilzilalar (Mb ≥ 6.0)</p>',
        '<p><i class="fa fa-circle" style="color:blue"></i> Zilzilalar (Mb 5.0-5.9)</p>',
        '<p><i class="fa fa-circle" style="color:lightblue"></i> Zilzilalar (Mb 4.0-4.9)</p>',
        '<p><i class="fa fa-circle" style="color:gray"></i> Zilzilalar (Mb < 4.0)</p>',
        '<p><div style="width: 0; height: 0; border-left: 8px solid transparent; border-right: 8px solid transparent; border-bottom: 15px solid blue; display: inline-block; margin-right: 8px;"></div> Tanlangan skvajinalar</p>',
        '<p><div style="width: 0; height: 0; border-left: 8px solid transparent; border-right: 8px solid transparent; border-bottom: 15px solid lightblue; display: inline-block; margin-right: 8px;"></div> Tanlanmagan skvajinalar</p>',
    ]

    legend_html = f'''
    <div style="position: fixed; 
                bottom: 50px; left: 50px; width: 200px; height: auto; 
                background-color: white; border:2px solid grey; z-index:9999; 
                font-size:12px; padding: 10px; overflow-y: auto;">
    {''.join(legend_items)}
    </div>
    '''
    m.get_root().html.add_child(folium.Element(legend_html))

    # Layer control qo'shish
    folium.LayerControl().add_to(m)

    return m._repr_html_()


def informativity_view(request):
    """
    Informativlik tahlili - forma, natijalar va grafiklar
    YANGI: Median → Informativlik (to'g'ri tartib)
    """
    lst_stansiya, well_coords = fetch_data()
    all_params = sorted(list(set(sum(DEFAULT_ELEMENTS_GROUPS.values(), []))))

    context = {
        "wells": lst_stansiya.keys(),
        "params": all_params,
        "inf_selected_keys": [],
        "inf_selected_params": [],
        "inf_window_years": "",
        "inf_anomaly_duration": "",
        "inf_std_factor": "",
        "inf_timedelta_before": "",
        "inf_timedelta_after": "",
        "inf_min_mag": "",
        "inf_min_mlgr": "",
        "inf_filter_start_date": "",
        "inf_filter_end_date": "",
        "inf_median_window": "",
    }

    if request.method == "POST":
        selected_keys = request.POST.getlist("wells")
        selected_params = request.POST.getlist("params")

        try:
            window_years = int(request.POST.get("window_years"))
            anomaly_duration = int(request.POST.get("anomaly_duration"))
            std_factor = float(request.POST.get("std_factor"))
            timedelta_before = int(request.POST.get("timedelta_before"))
            timedelta_after = int(request.POST.get("timedelta_after"))
            min_mag = float(request.POST.get("min_mag"))
            min_mlgr = float(request.POST.get("min_mlgr"))

            # Median window
            median_window_raw = request.POST.get("median_window", "").strip()
            median_window = int(median_window_raw) if median_window_raw else None

        except ValueError as e:
            context["error"] = f"Raqamli maydonlarda xato: {str(e)}"
            return render(request, "seismos_app/informativity_results.html", context)

        filter_start_date = request.POST.get("start_date", "").strip() or None
        filter_end_date = request.POST.get("end_date", "").strip() or None

        if not selected_keys:
            context["error"] = "Kamida bitta skvajina tanlang."
            return render(request, "seismos_app/informativity_results.html", context)

        if not selected_params:
            context["error"] = "Kamida bitta parametr tanlang."
            return render(request, "seismos_app/informativity_results.html", context)

        context.update({
            "inf_selected_keys": selected_keys,
            "inf_selected_params": selected_params,
            "inf_window_years": window_years,
            "inf_anomaly_duration": anomaly_duration,
            "inf_std_factor": std_factor,
            "inf_timedelta_before": timedelta_before,
            "inf_timedelta_after": timedelta_after,
            "inf_min_mag": min_mag,
            "inf_min_mlgr": min_mlgr,
            "inf_filter_start_date": filter_start_date or "",
            "inf_filter_end_date": filter_end_date or "",
            "inf_median_window": median_window or "",
        })

        conn = None
        try:
            engine = connect_db()
            if not engine:
                context["error"] = "Ma'lumotlar bazasiga ulanish imkonsiz"
                return render(request, "seismos_app/informativity_results.html", context)

            conn = engine.connect()

            # Zilzilalar ma'lumotlarini yuklash
            excel_file_path = "/home/asus/PROJECT/Seismo/static/shapefiles/USGS catalog.xlsx"
            earthquakes_df = pd.read_excel(excel_file_path)
            earthquakes_df['Event_date'] = pd.to_datetime(earthquakes_df['Event_date'], errors='coerce')

            mag_cols = [col for col in earthquakes_df.columns if col.lower() in ['mb', 'm', 'ml']]
            mag_col = mag_cols[0] if mag_cols else 'Mb'

            earthquakes_df[mag_col] = pd.to_numeric(earthquakes_df[mag_col], errors='coerce')
            earthquakes_df.dropna(subset=['Event_date', mag_col], inplace=True)

            # Sana filtri
            if filter_start_date:
                earthquakes_df = earthquakes_df[earthquakes_df['Event_date'] >= pd.to_datetime(filter_start_date)]
            if filter_end_date:
                earthquakes_df = earthquakes_df[earthquakes_df['Event_date'] <= pd.to_datetime(filter_end_date)]

            earthquakes_df = earthquakes_df[earthquakes_df[mag_col] >= min_mag]

            if earthquakes_df.empty:
                context["error"] = "Tanlangan parametrlar bo'yicha zilzilalar topilmadi."
                return render(request, "seismos_app/informativity_results.html", context)

            results = []
            graph_data = []

            for key in selected_keys:
                _, skvajina = key.split(" | ")
                lat, lon = well_coords.get(skvajina, (None, None))

                if lat is None or lon is None:
                    logging.warning(f"Koordinatalar topilmadi: {skvajina}")
                    continue

                # Zilzilalarni filtrlash
                earthquakes_filtered = earthquakes_df.copy()
                earthquakes_filtered['R(km)'] = np.round(
                    destenc_vectorized(lat, lon, earthquakes_filtered['Latitude'], earthquakes_filtered['Longitude'])
                )
                earthquakes_filtered['M/lgR'] = np.where(
                    earthquakes_filtered['R(km)'] > 1,
                    earthquakes_filtered[mag_col] / np.log10(earthquakes_filtered['R(km)']),
                    np.nan
                )
                earthquakes_filtered = earthquakes_filtered[earthquakes_filtered['M/lgR'] >= min_mlgr]

                if earthquakes_filtered.empty:
                    logging.warning(f"{skvajina} uchun filtrlangan zilzilalar topilmadi")
                    continue

                for param in selected_params:
                    ssdi_id = lst_stansiya.get(key, {}).get(param)
                    if not ssdi_id:
                        logging.warning(f"{key} uchun {param} topilmadi")
                        continue

                    # Ma'lumotlarni olish
                    query_text = text(f"SELECT date, `{ssdi_id}` FROM alldata WHERE `{ssdi_id}` IS NOT NULL")
                    try:
                        data = conn.execute(query_text).fetchall()
                    except Exception as e:
                        logging.error(f"Query xatosi {key} - {param}: {e}")
                        continue

                    if not data:
                        logging.warning(f"{key} - {param} uchun ma'lumot yo'q")
                        continue

                    # DataFrame yaratish
                    df_temp = pd.DataFrame(data, columns=['date', 'value'])
                    df_temp['date'] = pd.to_datetime(df_temp['date'], errors='coerce')
                    df_temp.dropna(subset=['date', 'value'], inplace=True)

                    # Sana filtri
                    if filter_start_date:
                        df_temp = df_temp[df_temp['date'] >= pd.to_datetime(filter_start_date)]
                    if filter_end_date:
                        df_temp = df_temp[df_temp['date'] <= pd.to_datetime(filter_end_date)]

                    if df_temp.empty:
                        logging.warning(f"{key} - {param} uchun filtrlangan ma'lumot yo'q")
                        continue

                    # ============================================
                    # 🔴 1-QADAM: MEDIAN HISOBLASH (agar kerak bo'lsa)
                    # ============================================
                    original_data_for_graph = df_temp.copy()  # Asl ma'lumot grafik uchun

                    if median_window and median_window > 0:
                        logging.info(f"🔄 {key} - {param}: {median_window} kunlik median boshlanmoqda...")

                        # Kunlik medianani hisoblash
                        df_daily_median = (
                            df_temp.groupby(df_temp['date'].dt.date)['value']
                            .median()
                            .reset_index()
                            .rename(columns={'date': 'date', 'value': 'daily_median'})
                        )

                        # Rolling median (center=True)
                        df_daily_median['rolling_median'] = (
                            df_daily_median['daily_median']
                            .rolling(window=median_window, min_periods=1, center=True)
                            .median()
                        )

                        # Yangi DataFrame yaratish (median ma'lumotlar bilan)
                        df_temp = pd.DataFrame({
                            'date': pd.to_datetime(df_daily_median['date']),
                            'value': df_daily_median['rolling_median'].values
                        })

                        df_temp.dropna(subset=['value'], inplace=True)

                        logging.info(f"✅ {key} - {param}: Median hisoblandi. "
                                     f"Asl: {len(original_data_for_graph)} → Median: {len(df_temp)} nuqta")

                    else:
                        logging.info(f"ℹ️ {key} - {param}: Median qo'llanmaydi (oddiy ma'lumot)")

                    # ============================================
                    # 🔴 2-QADAM: INFORMATIVLIK HISOBLASH
                    # (MEDIAN MA'LUMOTLARI BILAN)
                    # ============================================

                    if df_temp.empty:
                        logging.warning(f"{key} - {param} uchun ma'lumot bo'sh (median keyingi)")
                        continue

                    # Index ni o'rnatish (date)
                    df_temp_indexed = df_temp.set_index('date')

                    # Kunlik qilib olish va interpolatsiya
                    df_temp_indexed = df_temp_indexed.asfreq('D')
                    df_temp_indexed = df_temp_indexed.interpolate(method='time', limit_direction='both')
                    df_temp_indexed.dropna(inplace=True)

                    if df_temp_indexed.empty:
                        logging.warning(f"{key} - {param} uchun interpolatsiya keyingi ma'lumot bo'sh")
                        continue

                    # INFORMATIVLIK HISOBLASH
                    logging.info(f"📊 {key} - {param}: Informativlik hisoblanmoqda...")

                    inf_result = calculate_informativity_improved(
                        df_temp_indexed['value'],  # MEDIAN QILINGAN MA'LUMOT!
                        earthquakes_filtered,
                        window_years,
                        anomaly_duration,
                        std_factor,
                        timedelta_before,
                        timedelta_after
                    )

                    if inf_result:
                        inf_result['skvajina'] = skvajina
                        inf_result['parametr'] = param
                        inf_result['key'] = key
                        results.append(inf_result)

                        # Grafik uchun ma'lumotlar (MEDIAN QILINGAN)
                        x_val = df_temp_indexed.index.tolist()
                        y_val = df_temp_indexed['value'].tolist()
                        mean = np.mean(y_val)
                        sigma = np.std(y_val)

                        captured_indices = inf_result.get('captured_earthquakes', [])
                        segment_results = inf_result.get('segment_results', [])

                        available_cols = [mag_col, 'Event_date', 'Latitude', 'Longitude', 'R(km)', 'M/lgR']
                        if 'Event_time' in earthquakes_filtered.columns:
                            available_cols.insert(2, 'Event_time')
                        if 'Depth' in earthquakes_filtered.columns and 'Depth' not in available_cols:
                            available_cols.append('Depth')

                        graph_data.append({
                            'x': x_val,
                            'y': y_val,
                            'mean': mean,
                            'sigma': sigma,
                            'param': param,
                            'key': key,
                            'skvajina': skvajina,
                            'earthquakes_all': earthquakes_filtered[available_cols].copy(),
                            'captured_indices': captured_indices,
                            'lat': lat,
                            'lon': lon,
                            'segment_results': segment_results
                        })

                        logging.info(f"✅ {key} - {param}: Informativlik tayyor. "
                                     f"q={inf_result['q']:.4f}, Φ(ξ)={inf_result['phi_xi']:.4f}")

            # views.py da informativity_view funksiyasi oxirida

            # ... (barcha hisoblashlar) ...

            if not results:
                context['error'] = 'Hech qanday natija topilmadi.'
                return render(request, "seismos_app/informativity_results.html", context)

            results = sorted(results, key=lambda x: x['q'], reverse=True)

            plotly_html = None
            folium_map_html = None

            if graph_data:
                plotly_html = create_informativity_graph(
                    graph_data,
                    std_factor,
                    min_mag,
                    min_mlgr
                )

                all_captured_indices = set()
                for data in graph_data:
                    captured_indices = data.get('captured_indices', [])
                    all_captured_indices.update(captured_indices)

                all_earthquakes_for_map = pd.DataFrame()
                for data in graph_data:
                    eq_df = data['earthquakes_all']
                    if not eq_df.empty:
                        all_earthquakes_for_map = pd.concat([all_earthquakes_for_map, eq_df])

                if not all_earthquakes_for_map.empty:
                    all_earthquakes_for_map = all_earthquakes_for_map[
                        ~all_earthquakes_for_map.index.duplicated(keep='first')]

                try:
                    folium_map_html = add_map_data_folium(
                        selected_keys=selected_keys,
                        well_coords=well_coords,
                        earthquake_data=all_earthquakes_for_map if not all_earthquakes_for_map.empty else pd.DataFrame(),
                        min_mag=min_mag,
                        min_mlgr=min_mlgr,
                        captured_earthquake_indices=list(all_captured_indices),
                        show_radius=True
                    )
                    logging.info(f"🗺️ Xarita yaratildi: {len(all_captured_indices)} ta informativ zilzila")
                except Exception as e:
                    logging.error(f"Folium xaritasini yaratishda xato: {e}", exc_info=True)
                    folium_map_html = "<p>Xarita yaratishda xato yuz berdi.</p>"

            # ============================================
            # 🔴 SESSION UCHUN MA'LUMOTLARNI TOZALASH
            # ============================================

            def make_json_serializable(obj):
                """
                Har qanday obyektni JSON formatiga aylantirish
                """
                if isinstance(obj, (pd.Timestamp, datetime.datetime, datetime.date)):
                    return obj.isoformat()
                elif isinstance(obj, (np.integer, np.int64, np.int32)):
                    return int(obj)
                elif isinstance(obj, (np.floating, np.float64, np.float32)):
                    return float(obj)
                elif isinstance(obj, np.ndarray):
                    return obj.tolist()
                elif isinstance(obj, tuple):
                    return list(obj)
                elif isinstance(obj, pd.Series):
                    return obj.tolist()
                elif isinstance(obj, dict):
                    return {k: make_json_serializable(v) for k, v in obj.items()}
                elif isinstance(obj, list):
                    return [make_json_serializable(item) for item in obj]
                else:
                    return obj

            session_results = []
            for res in results:
                # Asosiy ma'lumotlar
                clean_res = {
                    'skvajina': str(res['skvajina']),
                    'parametr': str(res['parametr']),
                    'key': str(res['key']),
                    'T': int(res['T']),
                    't': int(res['t']),
                    'n': int(res['n']),
                    'm': int(res['m']),
                    't_T': float(res['t_T']),
                    'm_n': float(res['m_n']),
                    'phi_xi': float(res['phi_xi']),
                    'q': float(res['q']),
                    'delta': float(res.get('delta', 0.0)),
                    'mu': float(res.get('mu', 0.0)),
                    'reliability': str(res['reliability']),
                    'reliability_level': str(res['reliability_level']),
                    'informativity': str(res['informativity']),
                    'informativity_level': str(res['informativity_level']),
                    'captured_count': int(len(res.get('captured_earthquakes', [])))
                }

                # Segment ma'lumotlarini tozalash
                if 'segment_results' in res:
                    clean_segments = []
                    for seg in res['segment_results']:
                        clean_seg = {
                            'year_start': int(seg['year_start']),
                            'year_end': int(seg['year_end']),
                            'T': int(seg['T']),
                            't': int(seg['t']),
                            'n': int(seg['n']),
                            'm': int(seg['m']),
                            'mean': float(seg['mean']),
                            'std': float(seg['std']),
                            'upper': float(seg.get('upper', 0.0)),
                            'lower': float(seg.get('lower', 0.0))
                        }
                        # MUHIM: merged_intervals ni o'tkazib yuborish!
                        clean_segments.append(clean_seg)

                    clean_res['segment_results'] = clean_segments

                session_results.append(clean_res)

            # Session ga saqlash
            try:
                request.session['informativity_results'] = session_results
                logging.info(f"✅ Session saqlandi: {len(session_results)} ta natija")
            except Exception as e:
                logging.error(f"❌ Session saqlashda xato: {e}")
                # Session xatosini ignore qilish (asosiy funktsionallik ishlayveradi)

            # ============================================
            # CONTEXT YARATISH
            # ============================================

            context.update({
                'results': results,
                'plotly_main_graph': plotly_html,
                'folium_map': folium_map_html,
                'total_results': len(results),
            })



        except Exception as e:
            logging.error(f"Informativity error: {e}", exc_info=True)
            context["error"] = f"Hisoblashda xatolik: {str(e)}"
            return render(request, "seismos_app/informativity_results.html", context)

        finally:
            if conn:
                conn.close()
            if engine:
                engine.dispose()

        return render(request, "seismos_app/informativity_results.html", context)

    return render(request, "seismos_app/informativity_results.html", context)
def export_informativity_excel(request):
    """
    Informativlik natijalarini Excel formatida eksport qilish
    """
    results = request.session.get('informativity_results', [])
    if not results:
        return HttpResponse("Natijalar yo'q. Iltimos, avval hisoblang.", content_type="text/plain")

    output = io.BytesIO()
    workbook = xlsxwriter.Workbook(output, {'in_memory': True})
    worksheet = workbook.add_worksheet('Informativlik')

    # Formatlar
    header = workbook.add_format({
        'bold': True,
        'bg_color': '#28a745',
        'font_color': 'white',
        'border': 1,
        'align': 'center',
        'valign': 'vcenter'
    })
    cell = workbook.add_format({'border': 1, 'align': 'center'})
    cell_left = workbook.add_format({'border': 1, 'align': 'left'})

    headers = [
        '№', 'Skvajina', 'Parametr', 'T (kun)', 't (anom.)', 'n (zilz.)', 'm (tut.)',
        't/T', 'm/n', 'Φ(ξ)', 'q', 'Ishonchlilik', 'Informativlik'
    ]

    # Ustunlar kengligini o'rnatish
    worksheet.set_column('A:A', 5)
    worksheet.set_column('B:B', 15)
    worksheet.set_column('C:C', 15)
    worksheet.set_column('D:G', 10)
    worksheet.set_column('H:K', 12)
    worksheet.set_column('L:M', 20)

    # Headerlarni yozish
    for col, h in enumerate(headers):
        worksheet.write(0, col, h, header)

    # Ma'lumotlarni yozish
    for row, res in enumerate(results, start=1):
        worksheet.write(row, 0, row, cell)
        worksheet.write(row, 1, res['skvajina'], cell_left)
        worksheet.write(row, 2, res['parametr'], cell_left)
        worksheet.write(row, 3, res['T'], cell)
        worksheet.write(row, 4, res['t'], cell)
        worksheet.write(row, 5, res['n'], cell)
        worksheet.write(row, 6, res['m'], cell)
        worksheet.write(row, 7, round(res['t_T'], 4), cell)
        worksheet.write(row, 8, round(res['m_n'], 4), cell)
        worksheet.write(row, 9, round(res['phi_xi'], 4), cell)
        worksheet.write(row, 10, round(res['q'], 4), cell)
        worksheet.write(row, 11, res['reliability'], cell)
        worksheet.write(row, 12, res['informativity'], cell)

    workbook.close()
    output.seek(0)

    response = HttpResponse(
        output.read(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = 'attachment; filename="informativlik_natijalari.xlsx"'
    return response

