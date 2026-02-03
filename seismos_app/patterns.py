import numpy as np
import logging
from scipy.stats import pearsonr, spearmanr
from typing import List, Dict

logger = logging.getLogger(__name__)


def custom_dtw(x, y):
    """Simple DTW distance between two 1-D sequences.

    Note: O(n*m) time and memory. Keep sequences reasonably short.
    """
    x = np.array(x, dtype=float)
    y = np.array(y, dtype=float)
    n, m = len(x), len(y)
    if n == 0 or m == 0:
        return float('inf')

    dt = np.full((n + 1, m + 1), np.inf)
    dt[0, 0] = 0.0
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cost = abs(x[i - 1] - y[j - 1])
            dt[i, j] = cost + min(dt[i - 1, j], dt[i, j - 1], dt[i - 1, j - 1])
    return float(dt[n, m])


def normalize_series(series):
    """Min-max normalize a 1-D sequence to [0,1].

    Returns a numpy array of same length. If max==min returns zeros.
    """
    arr = np.array(series, dtype=float)
    if arr.size == 0:
        return arr
    min_val = np.nanmin(arr)
    max_val = np.nanmax(arr)
    if np.isnan(min_val) or np.isnan(max_val) or max_val - min_val == 0:
        return np.zeros_like(arr)
    return (arr - min_val) / (max_val - min_val)


def calculate_pattern_similarity(reference, candidate):
    """Calculate similarity between two sequences.

    Returns dict with dtw_score, pearson_score, spearman_score, combined_score.
    Scores are in 0..100 range. Missing/invalid metrics fall back to 0.
    """
    results = {'dtw_score': 0.0, 'pearson_score': 0.0, 'spearman_score': 0.0, 'combined_score': 0.0}

    try:
        ref_norm = normalize_series(reference)
        cand_norm = normalize_series(candidate)

        # DTW
        try:
            dtw_distance = custom_dtw(ref_norm, cand_norm)
            denom = max(len(ref_norm), len(cand_norm), 1)
            dtw_score = max(0.0, 100.0 * (1.0 - (dtw_distance / denom)))
            results['dtw_score'] = round(float(dtw_score), 2)
        except Exception:
            results['dtw_score'] = 0.0

        # Pearson and Spearman require at least 2 points and some variance
        try:
            if len(ref_norm) >= 2 and len(cand_norm) >= 2:
                # Truncate/pad to same length for correlation: use the shorter length
                L = min(len(ref_norm), len(cand_norm))
                a = ref_norm[:L]
                b = cand_norm[:L]
                if np.nanstd(a) > 0 and np.nanstd(b) > 0:
                    pearson_corr, _ = pearsonr(a, b)
                    results['pearson_score'] = round((pearson_corr + 1.0) * 50.0, 2)
        except Exception:
            results['pearson_score'] = 0.0

        try:
            if len(ref_norm) >= 2 and len(cand_norm) >= 2:
                L = min(len(ref_norm), len(cand_norm))
                a = ref_norm[:L]
                b = cand_norm[:L]
                if np.nanstd(a) > 0 and np.nanstd(b) > 0:
                    spearman_corr, _ = spearmanr(a, b)
                    results['spearman_score'] = round((spearman_corr + 1.0) * 50.0, 2)
        except Exception:
            results['spearman_score'] = 0.0

        results['combined_score'] = round(
            (results['dtw_score'] + results['pearson_score'] + results['spearman_score']) / 3.0, 2
        )

    except Exception as e:
        logger.exception(f"Error in calculate_pattern_similarity: {e}")

    return results


def find_similar_anomalies(anomaly_segments: List[Dict], min_similarity: float = 70.0):
    """Find anomalies similar to the first (reference) anomaly.

    anomaly_segments: list of dicts with at least keys 'start_date','end_date','values'
    Returns list of dicts {'start_date','end_date','similarity'} sorted by similarity desc.
    """
    if not anomaly_segments or len(anomaly_segments) < 2:
        return []

    similar_pairs = []
    reference = anomaly_segments[0].get('values', [])

    for seg in anomaly_segments[1:]:
        cand = seg.get('values', [])
        similarity = calculate_pattern_similarity(reference, cand)
        if similarity.get('combined_score', 0.0) >= min_similarity:
            similar_pairs.append({
                'start_date': seg.get('start_date'),
                'end_date': seg.get('end_date'),
                'similarity': similarity
            })

    similar_pairs.sort(key=lambda x: x['similarity'].get('combined_score', 0.0), reverse=True)
    return similar_pairs


__all__ = [
    'custom_dtw', 'normalize_series', 'calculate_pattern_similarity', 'find_similar_anomalies'
]
