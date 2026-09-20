import logging
import base64
import pandas as pd
import numpy as np

from math import pi
from sqlalchemy import create_engine
from decouple import config as env_config
from typing import Dict, Tuple, List, Optional
from django.core.cache import cache
from sqlalchemy.pool import QueuePool
from threading import Lock

from download_base_app.views import logger
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

