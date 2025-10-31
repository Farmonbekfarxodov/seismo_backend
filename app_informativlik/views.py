from django.http import HttpResponse
from folium import Map

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


def calculate_informativity(data_series, earthquakes_df, window_years,
                            anomaly_duration, std_factor,
                            timedelta_before, timedelta_after):
    """
    Parametr uchun informativlikni hisoblaydi va informativ zilzilalar ro'yxatini qaytaradi

    Returns:
        dict: Informativlik ko'rsatkichlari + 'captured_earthquakes' (informativ zilzilalar)
    """
    if data_series.empty or earthquakes_df.empty:
        logging.warning("Ma'lumotlar yoki zilzilalar bo'sh")
        return None

    # Informativ zilzilalar ro'yxati
    captured_earthquake_indices = []

    total_t = 0
    total_m = 0
    segment_results = []

    start_year = data_series.index.min().year
    end_year = data_series.index.max().year

    # Yillar bo'yicha bo'laklash
    for year_start in range(start_year, end_year + 1, window_years):
        year_end = year_start + window_years - 1

        # Segment uchun zilzilalarni filtrlash
        seg_eq = earthquakes_df[
            (earthquakes_df['Event_date'] >= f"{year_start}-01-01") &
            (earthquakes_df['Event_date'] <= f"{year_end}-12-31")
            ]

        # Ma'lumotlar segmentini olish
        mask = (data_series.index >= f"{year_start}-01-01") & \
               (data_series.index <= f"{year_end}-12-31")
        segment = data_series.loc[mask].copy()

        if segment.empty:
            logging.info(f"Segment bo'sh: {year_start}-{year_end}")
            continue

        # O'rtacha va standart chetlanishni hisoblash
        mean_val = segment.mean()
        std_val = segment.std()

        if std_val == 0 or np.isnan(std_val):
            logging.warning(f"Standart chetlanish 0 yoki NaN: {year_start}-{year_end}")
            continue

        # Anomaliyalarni aniqlash
        segment_df = pd.DataFrame({'value': segment})
        segment_df['Anomaly'] = (
                (segment_df['value'] > mean_val + std_factor * std_val) |
                (segment_df['value'] < mean_val - std_factor * std_val)
        ).astype(int)

        # Davomiy anomaliyalarni filtrlash
        segment_df['Anomaly'] = filter_long_sequences(
            segment_df['Anomaly'], anomaly_duration
        )

        # t va m ni hisoblash
        t = int(segment_df['Anomaly'].sum())
        m = 0

        # YANGI: Har bir zilzilani tekshirish va informativ bo'lsa yozib qo'yish
        for eq_idx, eq_row in seg_eq.iterrows():
            eq_date = pd.to_datetime(eq_row['Event_date'])
            eq_window = (
                    (segment_df.index >= eq_date - timedelta(days=timedelta_before)) &
                    (segment_df.index <= eq_date + timedelta(days=timedelta_after))
            )
            if segment_df.loc[eq_window, 'Anomaly'].any():
                m += 1
                # YANGI: Informativ zilzilani ro'yxatga qo'shish
                captured_earthquake_indices.append(eq_idx)

        T_i = len(segment_df)
        n_i = len(seg_eq)
        total_t += t
        total_m += m

        segment_results.append({
            'year_start': year_start,
            'year_end': year_end,
            'T': T_i,
            't': t,
            'n': n_i,
            'm': m,
            'mean': float(mean_val),
            'std': float(std_val)
        })

    # Umumiy hisoblar
    T = len(data_series)
    n = len(earthquakes_df)

    total_t = int(total_t)
    total_m = int(total_m)

    if T == 0 or n == 0 or total_t == 0:
        logging.warning(f"Hisoblash uchun yetarli ma'lumot yo'q: T={T}, n={n}, total_t={total_t}")
        return None

    t_T = float(total_t / T)
    m_n = float(total_m / n if n > 0 else 0)

    # Xi va Phi(xi) hisoblash
    try:
        denominator = np.sqrt((1 / n) * t_T * (1 - t_T))
        if denominator == 0:
            xi = 0.0
            phi_xi = 0.5
        else:
            xi = float((m_n - t_T) / denominator)
            phi_xi = float(norm.cdf(xi))
    except Exception as e:
        logging.error(f"Phi(xi) hisoblashda xato: {e}")
        phi_xi = 0.0

    # q hisoblash
    try:
        if m_n == 0 or t_T == 0 or m_n == 1:
            q = 0.0
        else:
            mu = float((1 - m_n) / (0.5 + np.sqrt(0.25 + total_m * (1 - m_n))))
            delta = float((1 - mu) / (1 + 1 / n))

            numerator = delta * m_n * (1 - t_T)
            denominator = (1 - m_n) * t_T

            if numerator <= 0 or denominator <= 0:
                q = 0.0
            else:
                q = float(0.25 * np.log(numerator / denominator))
    except Exception as e:
        logging.error(f"q hisoblashda xato: {e}")
        q = 0.0

    # Ishonchlilik va Informativlik darjasini aniqlash
    if phi_xi > 0.95:
        reliability = "Anomaliya statistik jihatdan ishonchli (tasodifiy emas)"
        reliability_level = "Yuqori"
    else:
        reliability = "Anomaliya statistik jihatdan ishonchsiz (tasodifiy bo'lishi mumkin)"
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

    return {
        'T': T,
        't': total_t,
        'n': n,
        'm': total_m,
        't_T': t_T,
        'm_n': m_n,
        'phi_xi': phi_xi,
        'q': q,
        'reliability': reliability,
        'reliability_level': reliability_level,
        'informativity': informativity,
        'informativity_level': informativity_level,
        'segment_results': segment_results,
        'captured_earthquakes': captured_earthquake_indices  # YANGI QATOR
    }


def create_informativity_graph(graph_data, std_factor, min_mag, min_mlgr):
    """
    Informativlik uchun grafik yaratadi (results_view formatida)
    Faqat informativ zilzilalar qizil rangda ko'rsatiladi
    """
    num_graphs = len(graph_data)
    if num_graphs == 0:
        return None

    single_graph_height = 600
    total_figure_height = num_graphs * single_graph_height
    max_total_height = 30000

    if total_figure_height > max_total_height:
        scale_factor = max_total_height / total_figure_height
        single_graph_height = int(single_graph_height * scale_factor)
        total_figure_height = max_total_height

    # Subplot nomlarini SKVAJINA nomi bilan yaratish
    subplot_titles = [f"{d['skvajina']} - {d['param']}" for d in graph_data]
    specs = [[{"secondary_y": True}]] * num_graphs

    vertical_spacing = 0.04 if num_graphs <= 3 else 0.02 if num_graphs <= 5 else max(0.005, 0.08 / num_graphs)

    fig = make_subplots(
        rows=num_graphs,
        cols=1,
        subplot_titles=subplot_titles,
        vertical_spacing=vertical_spacing,
        specs=specs,
    )

    color_pool = generate_colors(num_graphs)

    for idx, data in enumerate(graph_data):
        row, col = idx + 1, 1
        trace_color = color_pool[idx]

        x_val = data['x']
        y_val = data['y']
        mean = data['mean']
        sigma = data['sigma']
        param = data['param']
        key = data['key']
        earthquakes_all = data['earthquakes_all']  # Barcha filtrlangan zilzilalar
        captured_indices = data['captured_indices']  # Informativ zilzilalar indekslari
        lat = data['lat']
        lon = data['lon']

        # Parametr grafikini chizish (anomaliyalar bilan)
        y_all = plot_data_with_anomalies(
            fig, x_val, y_val, mean, sigma, std_factor,
            row, col, trace_color, param, key
        )

        fig.update_yaxes(
            title_text=f"{param} Qiymati",
            range=[min(y_all) * 0.9, max(y_all) * 1.1],
            row=row,
            col=col,
            secondary_y=False,
        )

        # ----------------------------------------------------------------------------------
        # ZILZILALARNI CHIZISH (Fon + Informativ)
        # ----------------------------------------------------------------------------------

        if not earthquakes_all.empty:
            mag_col = 'Mb' if 'Mb' in earthquakes_all.columns else MAIN_MAGNITUDE_COLUMN
            earthquakes_all[mag_col] = pd.to_numeric(
                earthquakes_all[mag_col], errors='coerce'
            )
            earthquakes_all.dropna(subset=[mag_col], inplace=True)

            if not earthquakes_all.empty:
                # ----------------------------------------------------------------------
                # 1. ANOMALIYAGA TUSHMAGAN ZILZILALARNI CHIZISH (FON - UZUQ CHIZIQ)
                # ----------------------------------------------------------------------

                # Fon zilzilalarini ajratib olish (captured_indices da bo'lmaganlari)
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

                        # Stem ma'lumotlari
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
                                line=dict(color="blue", width=1.5, dash='dot'),  # UZUQ CHIZIQ
                                name=f"Fon Zilzilalar (≥{min_mag})",
                                hoverinfo="text",
                                text=hover_texts_bg,
                                showlegend=True,
                                legendgroup=f"earthquakes_{row}",
                                yaxis=f"y{2 * row}",
                            ),
                            row=row,
                            col=col,
                            secondary_y=True,
                        )

                # ----------------------------------------------------------------------
                # 2. INFORMATIV ZILZILALARNI CHIZISH (QATTIQ CHIZIQ)
                # ----------------------------------------------------------------------

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

                        # Stem ma'lumotlari
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
                                line=dict(color="blue", width=3),  # QATTIQ CHIZIQ - Informativ
                                name=f"Informativ Zilzilalar (≥{min_mag})",
                                hoverinfo="text",
                                text=hover_texts,
                                showlegend=True,
                                legendgroup=f"earthquakes_{row}",
                                yaxis=f"y{2 * row}",
                            ),
                            row=row,
                            col=col,
                            secondary_y=True,
                        )

                    # Y-o'qni moslashtirish (Fon va Informativ uchun umumiy)
                    max_mag = earthquakes_all[mag_col].max() * 1.1

                    fig.update_yaxes(
                        range=[0, max_mag],
                        secondary_y=True,
                        title_text="Magnituda (Mb)",
                        row=row,
                        col=col,
                    )
                # Agar faqat fon bo'lsa
                elif not background_earthquakes.empty:
                    max_mag = earthquakes_all[mag_col].max() * 1.1
                    fig.update_yaxes(
                        range=[0, max_mag],
                        secondary_y=True,
                        title_text="Magnituda (Mb)",
                        row=row,
                        col=col,
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
                row=row,
                col=col,
            )

        # Grid
        fig.update_yaxes(
            showgrid=True,
            gridwidth=0.15,
            gridcolor="black",
            griddash="dot",
            row=row,
            col=col,
            secondary_y=False,
        )
        fig.update_yaxes(
            showgrid=True,
            gridwidth=0.15,
            gridcolor="gray",
            griddash="dot",
            row=row,
            col=col,
            secondary_y=True,
        )

    # Layout
    fig.update_layout(
        title_text="Informativlik Tahlili - Parametrlar va Zilzilalar",
        height=total_figure_height,
        width=None,
        autosize=True,
        showlegend=False,  # Legend faqat pastki qatordagi trace-lar uchun ko'rsatiladi
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
        title=dict(font=dict(size=20), x=0.5, xanchor="center"),
        margin=dict(l=60, r=60, t=100, b=60),
    )

    config = {
        "displayModeBar": True,
        "scrollZoom": True,
        "doubleClick": "reset+autosize",
        "modeBarButtonsToAdd": ["pan2d", "zoomIn2d", "zoomOut2d", "autoScale2d", "resetScale2d"],
        "responsive": True,
        "displaylogo": False,
        "toImageButtonOptions": {
            "format": "png",
            "filename": "informativity_analysis",
            "height": 900,
            "width": 1400,
            "scale": 2
        }
    }

    return fig.to_html(full_html=False, include_plotlyjs="cdn", config=config)


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
    all_filtered_earthquakes = earthquake_data
    selected_well_names = set()

    for key in selected_keys:
        selected_well_names.add(key.split(" | ")[1])



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

            # # Radius doiralarini qo'shish (agar show_radius=True bo'lsa)
            # if show_radius:
            #     try:
            #         mlgr_val = min_mlgr if min_mlgr > 0 else 0.5
            #         radii_data = [
            #             (5, "#66ccff"),
            #             (6, "#3399ff"),
            #             (7, "#0033cc"),
            #         ]
            #
            #         circles_info = []
            #         for M_value, color in radii_data:
            #             R_km = float(10 ** (M_value / mlgr_val))
            #             circles_info.append({
            #                 'radius': R_km * 1000,
            #                 'color': color,
            #                 'M': M_value,
            #                 'R_km': R_km,
            #                 'mlgr': mlgr_val
            #             })
            #
            #         js_code = f"""
            #         <script>
            #         (function() {{
            #             var wellLat = {lat};
            #             var wellLon = {lon};
            #             var wellName = "{skvajina}";
            #             var circlesInfo = {circles_info};
            #             var circlesLayer = null;
            #             var isVisible = true;
            #
            #             document.addEventListener("DOMContentLoaded", function() {{
            #                 var map = window.map || Object.values(window).find(v => v instanceof L.Map);
            #                 if (!map) {{
            #                     console.error("Xarita topilmadi");
            #                     return;
            #                 }}
            #
            #                 circlesLayer = L.layerGroup();
            #                 circlesInfo.forEach(function(info) {{
            #                     var circle = L.circle([wellLat, wellLon], {{
            #                         radius: info.radius,
            #                         color: info.color,
            #                         weight: 2,
            #                         fill: false,
            #                         opacity: 0.7
            #                     }});
            #
            #                     circle.bindTooltip(
            #                         "M=" + info.M + ", R=" + info.R_km.toFixed(1) + " km (M/lgR=" + info.mlgr + ")",
            #                         {{permanent: false, direction: 'top'}}
            #                     );
            #
            #                     circlesLayer.addLayer(circle);
            #                 }});
            #                 circlesLayer.addTo(map);
            #
            #                 map.eachLayer(function(layer) {{
            #                     if (layer instanceof L.Marker) {{
            #                         var latlng = layer.getLatLng();
            #                         if (Math.abs(latlng.lat - wellLat) < 0.0001 &&
            #                             Math.abs(latlng.lng - wellLon) < 0.0001) {{
            #
            #                             layer.on('click', function(e) {{
            #                                 L.DomEvent.stopPropagation(e);
            #
            #                                 if (isVisible) {{
            #                                     map.removeLayer(circlesLayer);
            #                                     isVisible = false;
            #                                 }} else {{
            #                                     circlesLayer.addTo(map);
            #                                     isVisible = true;
            #                                 }}
            #                             }});
            #                         }}
            #                     }}
            #                 }});
            #             }});
            #         }})();
            #         </script>
            #         """
            #         m.get_root().html.add_child(folium.Element(js_code))

                # except Exception as e:
                #     logging.error(f"Radius JavaScript kodini qo'shishda xato ({skvajina}): {e}")

    # Zilzilalarni xaritaga qo'shish (YANGILANGAN QISM)
    if not all_filtered_earthquakes.empty:
        # captured_earthquake_indices ni set ga aylantirish
        captured_set = set(captured_earthquake_indices) if captured_earthquake_indices else set()

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
                    Magnituda (Mb): {mag_val:.2f}<br>
                    Chuqurlik (km): {depth_val}<br>
                    Masofa (km): {distance_val:.1f}<br>
                    M/lgR: {mlgr_val:.2f}<br>
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
    """
    # Eslatma: 'fetch_data', 'connect_db', 'destenc_vectorized',
    # 'calculate_informativity', 'create_informativity_graph',
    # 'DEFAULT_ELEMENTS_GROUPS', 'MAIN_MAGNITUDE_COLUMN' funksiyalari va konstantalari
    # ushbu faylning yuqori qismida mavjud deb faraz qilinadi.

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
        except ValueError as e:
            context["error"] = f"Raqamli maydonlarda xato: {str(e)}"
            return render(request, "seismos_app/informativity_results.html", context)

        filter_start_date = request.POST.get("start_date", "").strip() or None
        filter_end_date = request.POST.get("end_date", "").strip() or None

        # Validatsiya
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
        })

        engine = None
        conn = None

        try:
            engine = connect_db()
            if not engine:
                context["error"] = "Ma'lumotlar bazasiga ulanish imkonsiz"
                return render(request, "seismos_app/informativity_results.html", context)

            conn = engine.connect()

            # Zilzilalar ma'lumotini olish
            query = "SELECT `Event_date`, `Event_time`, `Latitude`, `Longitude`, `Depth`, `Mb` FROM catalog"
            earthquakes_df = pd.read_sql(query, engine)
            earthquakes_df['Event_date'] = pd.to_datetime(earthquakes_df['Event_date'], errors='coerce')
            # Zilzila turlari har xil bo'lishi mumkin, Mb ustunini tekshirish
            mag_cols = [col for col in earthquakes_df.columns if col.lower() in ['mb', 'm', 'ml']]
            if mag_cols:
                mag_col = mag_cols[0]
            else:
                mag_col = 'Mb'  # Agar topilmasa, Mb deb faraz qilamiz

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

            # Har bir skvajina va parametr uchun
            for key in selected_keys:
                _, skvajina = key.split(" | ")
                lat, lon = well_coords.get(skvajina, (None, None))

                if lat is None or lon is None:
                    logging.warning(f"Koordinatalar topilmadi: {skvajina}")
                    continue

                # Skvajinaga yaqin zilzilalarni filtrlash
                earthquakes_filtered = earthquakes_df.copy()
                earthquakes_filtered['R(km)'] = np.round(
                    destenc_vectorized(lat, lon, earthquakes_filtered['Latitude'], earthquakes_filtered['Longitude'])
                )

                # 'M/lgR' ni hisoblash
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

                    df_temp = pd.DataFrame(data, columns=['date', 'value'])
                    df_temp['date'] = pd.to_datetime(df_temp['date'], errors='coerce')
                    df_temp.dropna(subset=['date', 'value'], inplace=True)
                    df_temp = df_temp.set_index('date')

                    # Sana filtri
                    if filter_start_date:
                        df_temp = df_temp[df_temp.index >= pd.to_datetime(filter_start_date)]
                    if filter_end_date:
                        df_temp = df_temp[df_temp.index <= pd.to_datetime(filter_end_date)]

                    if df_temp.empty:
                        logging.warning(f"{key} - {param} uchun filtrlangan ma'lumot yo'q")
                        continue

                    # Kunlik ma'lumotlarni to'ldirish
                    df_temp = df_temp.asfreq('D')
                    df_temp = df_temp.interpolate(method='time', limit_direction='both')
                    df_temp.dropna(inplace=True)

                    if df_temp.empty:
                        continue

                    # Informativlikni hisoblash
                    inf_result = calculate_informativity(
                        df_temp['value'],
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

                        # Grafik uchun ma'lumotlar
                        x_val = df_temp.index.tolist()
                        y_val = df_temp['value'].tolist()
                        mean = np.mean(y_val)
                        sigma = np.std(y_val)

                        # Informativ zilzilalar indekslarini olish
                        captured_indices = inf_result.get('captured_earthquakes', [])

                        graph_data.append({
                            'x': x_val,
                            'y': y_val,
                            'mean': mean,
                            'sigma': sigma,
                            'param': param,
                            'key': key,
                            'skvajina': skvajina,
                            # Faqat kerakli ustunlarni saqlash uchun nusxa oling
                            'earthquakes_all': earthquakes_filtered[
                                [mag_col, 'Event_date', 'Event_time','Latitude', 'Longitude', 'R(km)', 'M/lgR']],
                            'captured_indices': captured_indices,
                            'lat': lat,
                            'lon': lon
                        })

            if not results:
                context['error'] = 'Hech qanday natija topilmadi.'
                return render(request, "seismos_app/informativity_results.html", context)

            results = sorted(results, key=lambda x: x['q'], reverse=True)

            # ASOSIY GRAFIK YARATISH
            plotly_html = None
            folium_map_html = None  # Xarita natijasini boshlang'ich qiymatga sozlash

            if graph_data:
                plotly_html = create_informativity_graph(
                    graph_data,
                    std_factor,
                    min_mag,
                    min_mlgr
                )

                # --- FOLIUM XARITASINI YARATISH (YANGILANGAN) ---
                # Barcha informativ zilzilalar indekslarini to'plash
                all_captured_indices = set()
                for data in graph_data:
                    captured_indices = data.get('captured_indices', [])
                    all_captured_indices.update(captured_indices)

                # Barcha skvajinalar bo'yicha filtrlangan zilzilalarni birlashtirish
                all_earthquakes_for_map = pd.DataFrame()
                for data in graph_data:
                    eq_df = data['earthquakes_all']
                    if not eq_df.empty:
                        # Faqat unikal indekslarni saqlash uchun konsolidatsiya qilinadi
                        all_earthquakes_for_map = pd.concat([all_earthquakes_for_map, eq_df])

                # Dublikatlarni olib tashlash (index bo'yicha)
                if not all_earthquakes_for_map.empty:
                    all_earthquakes_for_map = all_earthquakes_for_map[
                        ~all_earthquakes_for_map.index.duplicated(keep='first')]

                # add_map_data_folium funksiyasini chaqirish
                try:
                    folium_map_html = add_map_data_folium(
                        selected_keys=selected_keys,
                        well_coords=well_coords,
                        # Xarita funksiyasi faqatgina filtrlangan, unikal ma'lumotlar bilan ta'minlanadi
                        earthquake_data=all_earthquakes_for_map if not all_earthquakes_for_map.empty else pd.DataFrame(),
                        min_mag=min_mag,
                        min_mlgr=min_mlgr,
                        # Ushbu parametr xarita funksiyasida zilzilalarni ranglarga ajratish uchun ishlatiladi
                        captured_earthquake_indices=list(all_captured_indices),
                        show_radius=True
                    )
                    logging.info(f"Xarita yaratildi: {len(all_captured_indices)} ta informativ zilzila")
                except Exception as e:
                    logging.error(f"Folium xaritasini yaratishda xato: {e}", exc_info=True)
                    folium_map_html = "<p>Xarita yaratishda xato yuz berdi.</p>"

            request.session['informativity_results'] = results

            context.update({
                'results': results,
                'plotly_main_graph': plotly_html,
                'folium_map': folium_map_html,
                'total_results': len(results),
            })

            # Hisoblash try blokining yakuni
        except Exception as e:
            logging.error(f"Informativity error: {e}", exc_info=True)
            context["error"] = f"Hisoblashda xatolik: {str(e)}"
            return render(request, "seismos_app/informativity_results.html", context)

            # finally bloki try/except bloki bilan to'g'ri joylashgan
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
        worksheet.write(row, 11, res['reliability_level'], cell)
        worksheet.write(row, 12, res['informativity_level'], cell)

    workbook.close()
    output.seek(0)

    response = HttpResponse(
        output.read(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = 'attachment; filename="informativlik_natijalari.xlsx"'
    return response

