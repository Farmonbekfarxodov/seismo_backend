import numpy as np
import pandas as pd
import math
from math import erf
from scipy.stats import norm
import logging

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


