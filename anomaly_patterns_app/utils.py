import numpy as np
from scipy.stats import pearsonr, spearmanr


def custom_dtw(x, y):
    """Dynamic Time Warping masofasini hisoblash"""
    x = np.array(x)
    y = np.array(y)
    n, m = len(x), len(y)
    dt = np.full((n + 1, m + 1), np.inf)
    dt[0, 0] = 0
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cost = abs(x[i - 1] - y[j - 1])
            dt[i, j] = cost + min(dt[i - 1, j], dt[i, j - 1], dt[i - 1, j - 1])
    return dt[-1, -1]


def normalize_series(series):
    """Seriyani 0-1 oralig'iga normalizatsiya qilish"""
    series = np.array(series, dtype=float)
    min_val = np.min(series)
    max_val = np.max(series)
    if max_val - min_val == 0:
        return np.zeros_like(series)
    return (series - min_val) / (max_val - min_val)


def calculate_pattern_similarity(reference, candidate):
    """
    Ikki pattern o'rtasidagi o'xshashlikni hisoblash.
    Qaytaradi: {'dtw_score': ..., 'pearson_score': ..., 'spearman_score': ..., 'combined_score': ...}
    """
    ref_norm = normalize_series(reference)
    cand_norm = normalize_series(candidate)

    results = {'dtw_score': 0, 'pearson_score': 0, 'spearman_score': 0, 'combined_score': 0}

    try:
        dtw_distance = custom_dtw(ref_norm, cand_norm)
        max_possible = len(ref_norm)
        results['dtw_score'] = max(0, 100 * (1 - dtw_distance / max_possible))
    except Exception as e:
        print(f"DTW xatosi: {e}")

    try:
        pearson_corr, _ = pearsonr(ref_norm, cand_norm)
        results['pearson_score'] = round((pearson_corr + 1) * 50, 2)
    except Exception as e:
        print(f"Pearson xatosi: {e}")

    try:
        spearman_corr, _ = spearmanr(ref_norm, cand_norm)
        results['spearman_score'] = round((spearman_corr + 1) * 50, 2)
    except Exception as e:
        print(f"Spearman xatosi: {e}")

    results['combined_score'] = round(
        (results['dtw_score'] + results['pearson_score'] + results['spearman_score']) / 3, 2
    )

    return results


def find_similar_anomalies(anomaly_segments, reference=None, min_similarity=70):
    """
    Anomaliya segmentlari ichida o'xshashlarni topish.
    Agar reference berilmagan bo'lsa → birinchi segment reference sifatida olinadi.
    """
    if len(anomaly_segments) < 2:
        return []

    if reference is None and anomaly_segments:
        reference = anomaly_segments[0]['values']
    elif reference is None:
        return []

    similar = []
    for seg in anomaly_segments:
        if seg['values'] is reference:  # o'zini o'zi solishtirmaslik
            continue

        sim = calculate_pattern_similarity(reference, seg['values'])
        if sim['combined_score'] >= min_similarity:
            similar.append({
                'segment': seg,
                'similarity': sim
            })

    similar.sort(key=lambda x: x['similarity']['combined_score'], reverse=True)
    return similar