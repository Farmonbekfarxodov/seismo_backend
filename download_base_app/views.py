import json
import time
import logging
import datetime as dt

import pandas as pd
import mysql.connector
import requests

from django.shortcuts import render
from django.http import JsonResponse
from decouple import config
from datetime import timedelta
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from .serializers import MagnitkaSyncRequestSerializer, ApiUploadRequestSerializer

# ✅ Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('seismic_app.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ✅ API maydon nomlarini bazaga mapping qilish
API_TO_DB_FIELD_MAPPING = {
    'he': 'He',
    'h2': 'H2',
    'o2': 'O2',
    'n2': 'N2',
    'ch4': 'CH4',
    'co2': 'CO2',
    'c2h6': 'C2H6',
    'ph': 'pH',
    'eh': 'Eh',
    'hco3': 'HCO3',
    'ci2': 'Cl2',
    'f': 'F',
    't0': 'T0',
    'q': 'Q',
    'p': 'P',
    'eocc': 'EOCC',
}

# ✅ Stansiya va quduqlar ro'yxati
STATIONS_AND_WELLS = {
    "SMRM": {
        "name": "SMRM",
        "wells": [
            {"api_well": "A.Yassaviy", "db_well": "A.Yassaviy"},
            {"api_well": "Fozilova", "db_well": "Fozilova"},
            {"api_well": "Nazarbek", "db_well": "Nazarbek"},
            {"api_well": "Sabzavotchilik instituti", "db_well": "Sabzavotchilik instituti"},
            {"api_well": "Tekstil", "db_well": "Tekstil"},
            {"api_well": "Zam-zam", "db_well": "Zam-zam"},
            {"api_well": "Yangi yo'l", "db_well": "Yangi yo'l"},
            {"api_well": "Chinobod", "db_well": "Chinobod"},
            {"api_well": "Semurg'", "db_well": "Semurg'"}
        ]
    },
    "CHRT": {
        "name": "Chortoq",
        "wells": [
            {"api_well": "Chortoq 2", "db_well": "Chortoq 2"},
            {"api_well": "Chortoq 3", "db_well": "Chortoq 3"},
            {"api_well": "Chortoq 6", "db_well": "Chortoq 6"}
        ]
    },
    "Chimyon KPS": {
        "name": "Chimyon KPS",
        "wells": [
            {"api_well": "Chimyon 1-Yu", "db_well": "Chimyon 1-YU"},
            {"api_well": "Chimyon NP-1", "db_well": "Chimyon NP-1"},
            {"api_well": "Chimyon P-1", "db_well": "Chimyon P-1"}
        ]
    },
    "Namangan KPS": {
        "name": "Namangan KPS",
        "wells": [
            {"api_well": "Namangan 1", "db_well": "Namangan 1"},
            {"api_well": "Namangan 2", "db_well": "Namangan 2"},
            {"api_well": "Namangan 3", "db_well": "Namangan 3"},
            {"api_well": "Ming bulog'", "db_well": "Ming bulog'"}
        ]
    },
    "JMB": {
        "name": "Jumabozor",
        "wells": [
            {"api_well": "Jumabozor 1", "db_well": "Jumabozor 1"},
            {"api_well": "Jumabozor 2", "db_well": "Jumabozor 2"},
            {"api_well": "Navro'z", "db_well": "Navro'z"},
            {"api_well": "Ibn Sino", "db_well": "Ibn Sino"}
        ]
    },
    "DJAN": {
        "name": "Jongeldi",
        "wells": [
            {"api_well": "Tuyaq-R", "db_well": "Tuyaqochar"},
            {"api_well": "meteo-ya", "db_well": "Meteostansiya"},
            {"api_well": "Gumbaz", "db_well": "Gumbaz"}
        ]
    },
    "Ozodboshi": {
        "name": "Ozodboshi",
        "wells": [
            {"api_well": "Minora", "db_well": "Minora"},
            {"api_well": "GAI", "db_well": "GAI"},
            {"api_well": "Chotqol", "db_well": "Chotqol"},
            {"api_well": "Ozodbosh bulog'i", "db_well": "Ozodbosh bulog'i"}
        ]
    },
    "Sho'rchi": {
        "name": "Sho'rchi",
        "wells": [
            {"api_well": "Sho'rchi 5", "db_well": "Sho'rchi 5"},
            {"api_well": "Sho'rchi 7", "db_well": "Sho'rchi 7"},
            {"api_well": "Sho'rchi 8", "db_well": "Sho'rchi 8"},
            {"api_well": "Sho'rchi 295", "db_well": "Sho'rchi 295"},
            {"api_well": "Sho'rchi 293", "db_well": "Sho'rchi 293"},
            {"api_well": "Sho'rchi 292", "db_well": "Sho'rchi 292"}
        ]
    },
    "XRB": {
        "name": "Xarabek",
        "wells": [
            {"api_well": "Xaрабek", "db_well": "Xarabek"},
            {"api_well": "Bobur bulog'i", "db_well": "Bobur bulog'i"}
        ]
    },
    "Buxoro": {
        "name": "Yangi Buxoro",
        "wells": [
            {"api_well": "Buxoro", "db_well": "Buxoro"},
            {"api_well": "Jo'yzar", "db_well": "Jo'yzar"},
            {"api_well": "Setorai moxixosa", "db_well": "Setorai moxixosa"},
            {"api_well": "Xarbiy bo'lim", "db_well": "Xarbiy bo'lim"},
            {"api_well": "Yangi obod", "db_well": "Yangi obod"}
        ]
    },
    "Guliston": {
        "name": "Guliston",
        "wells": [
            {"api_well": "Guliston", "db_well": "Guliston"}
        ]
    },
    "Jizzax KPS": {
        "name": "Jizzax KPS",
        "wells": [
            {"api_well": "Xavotog' 8", "db_well": "Xavotogʻ 8"},
            {"api_well": "Xavotog' 7", "db_well": "Xavotogʻ 7"}
        ]
    },
}

# ✅ API credentials
LOGIN_URL = config("LOGIN")
DATA_URL = config("DATA")
USERNAME = config("USER_NAME")
PASSWORD = config("PASS_WORD")

LOGIN_URL_MAG = config("LOGIN_MAG")
DATA_URL_MAG = config("DATA_MAG")
USERNAME_MAG = config("USER_NAME_MAG")
PASSWORD_MAG = config("PASS_WORD_MAG")

# ✅ Asosiy DB credentials
DB_HOST = config("DB_HOST", default="localhost")
DB_USER = config("DB_USER", default="root")
DB_PASSWORD = config("DB_PASSWORD", default="1111")
DB_NAME = config("DB_NAME", default="seismik")

# ✅ Yangi (ko'chirish uchun) DB credentials
NEW_DB_HOST = config("NEW_DB_HOST", default="localhost")
NEW_DB_USER = config("NEW_DB_USER", default="root")
NEW_DB_PASSWORD = config("NEW_DB_PASSWORD", default="")
NEW_DB_NAME = config("NEW_DB_NAME", default="seismik_backup")


# ==================== Helpers ====================

def get_db_connection():
    """Asosiy bazaga ulanish"""
    return mysql.connector.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
    )


def get_custom_db_connection(host, user, password, db_name):
    """Yangi (ko'chirish) bazaga ulanish"""
    return mysql.connector.connect(
        host=host,
        user=user,
        password=password,
        database=db_name,
        port=int(config("NEW_DB_PORT", default="3306")),
        connect_timeout=10,
    )


def _parse_to_datetime(value):
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value
    if isinstance(value, dt.date):
        return dt.datetime.combine(value, dt.time())
    try:
        return dt.datetime.strptime(value, '%d.%m.%Y')
    except Exception:
        return None


def normalize_string(s):
    if not s:
        return ""
    s = s.replace('ʻ', "'").replace('\u2018', "'").replace('\u2019', "'")
    return s.strip().lower()


# ==================== API flow ====================

def get_auth_token():
    payload = {"username": USERNAME, "password": PASSWORD}
    try:
        response = requests.post(LOGIN_URL, json=payload, timeout=10)
        response.raise_for_status()
        token = response.json().get("result", {}).get("token")
        return token
    except requests.exceptions.ConnectionError:
        logger.error("❌ Token: internet ulanish xatosi")
        return None
    except requests.exceptions.Timeout:
        logger.error("⏱️ Token: so'rov vaqti tugadi")
        return None
    except Exception as e:
        logger.error(f"❌ Token olishda xato: {e}", exc_info=True)
        return None


def get_auth_token_magnitka():
    payload = {"username": USERNAME_MAG, "password": PASSWORD_MAG}
    try:
        response = requests.post(LOGIN_URL_MAG, json=payload, timeout=10)
        response.raise_for_status()
        token = response.json().get("result", {}).get("token")
        return token
    except requests.exceptions.ConnectionError:
        logger.error("❌ Token: internet ulanish xatosi")
        return None
    except requests.exceptions.Timeout:
        logger.error("⏱️ Token: so'rov vaqti tugadi")
        return None
    except Exception as e:
        logger.error(f"❌ Token olishda xato: {e}", exc_info=True)
        return None


def fetch_data_from_api(params, token):
    headers = {"Authorization": f"Bearer {token}"}
    all_data = []
    url = DATA_URL

    try:
        while url:
            response = requests.get(url, params=params, headers=headers, timeout=30)
            response.raise_for_status()
            response_data = response.json()

            if response_data and response_data.get("result"):
                data = response_data["result"].get("data", [])
                all_data.extend(data)
                url = response_data["result"].get("next_page_url")
                params = {}
            else:
                break

        return all_data

    except requests.exceptions.ConnectionError:
        logger.error("❌ API: internet ulanish xatosi")
        return None
    except requests.exceptions.Timeout:
        logger.error("⏱️ API: so'rov vaqti tugadi")
        return None
    except requests.exceptions.HTTPError as e:
        logger.error(f"🌐 API: HTTP xatosi: {e}")
        return None
    except Exception as e:
        logger.error(f"❌ API dan olishda xato: {e}", exc_info=True)
        return None


# ============================================================
# API dan magnitka ma'lumotlarini olish
# ============================================================
def split_date_range_by_week(start_date: str, end_date: str, chunk_days: int = 7):
    """
    Sana oralig'ini haftalik (7 kunlik) bo'laklarga ajratadi.
    Misol (chunk_days=7):
    2020-01-15 -> 2020-02-05
    [
        ('2020-01-15', '2020-01-21'),
        ('2020-01-22', '2020-01-28'),
        ('2020-01-29', '2020-02-05'),
    ]
    Agar API hali ham timeout bersa, chunk_days=3 yoki chunk_days=1 qilib kamaytiriladi.
    """
    start   = dt.datetime.strptime(start_date, "%Y-%m-%d").date()
    end     = dt.datetime.strptime(end_date, "%Y-%m-%d").date()
    ranges  = []
    current = start

    while current <= end:
        chunk_end = min(current + timedelta(days=chunk_days - 1), end)
        ranges.append((
            current.strftime("%Y-%m-%d"),
            chunk_end.strftime("%Y-%m-%d")
        ))
        current = chunk_end + timedelta(days=1)

    return ranges


def fetch_magnitka_chunk(date_start, date_end, token, station_code=None, max_retries=3):
    """
    Bitta kichik interval uchun magnitude-values ni pagination bilan yuklab oladi.
    """
    base_url = config("DATA_MAG")
    headers = {"Authorization": f"Bearer {token}"}

    params = {
        "date_start": date_start,
        "date_end": date_end,
    }
    if station_code:
        params["station_code"] = station_code

    all_data = []
    page = 1

    while True:
        current_params = params.copy()
        current_params["page"] = page

        success = False
        resp = None
        body = None

        for attempt in range(1, max_retries + 1):
            try:
                logger.info(
                    f"📄 Magnitka chunk: {date_start} -> {date_end} | "
                    f"page={page} | attempt={attempt}"
                )

                resp = requests.get(
                    base_url,
                    headers=headers,
                    params=current_params,
                    timeout=(10, 60)   # connect=10s, read=60s
                )
                resp.raise_for_status()

                body = resp.json()
                success = True
                break

            except requests.exceptions.HTTPError as e:
                status = e.response.status_code if e.response else None

                # vaqtinchalik server xatolari -> retry
                if status in (502, 503, 504):
                    logger.warning(
                        f"⚠️ HTTP {status}: {date_start} -> {date_end}, "
                        f"page={page}, attempt={attempt}/{max_retries}"
                    )
                    if attempt < max_retries:
                        time.sleep(2 * attempt)
                        continue

                logger.error(f"🌐 HTTPError: {e}", exc_info=True)
                logger.error(f"Response text: {resp.text if resp is not None else 'No response'}")
                return None

            except requests.exceptions.Timeout as e:
                logger.warning(
                    f"⏱️ Timeout: {date_start} -> {date_end}, "
                    f"page={page}, attempt={attempt}/{max_retries}"
                )
                if attempt < max_retries:
                    time.sleep(2 * attempt)
                    continue
                logger.error(f"❌ Timeout after retries: {e}", exc_info=True)
                return None

            except requests.exceptions.ConnectionError as e:
                logger.warning(
                    f"🌐 ConnectionError: {date_start} -> {date_end}, "
                    f"page={page}, attempt={attempt}/{max_retries}"
                )
                if attempt < max_retries:
                    time.sleep(2 * attempt)
                    continue
                logger.error(f"❌ ConnectionError after retries: {e}", exc_info=True)
                return None

            except ValueError as e:
                logger.error(f"❌ JSON parse xatosi: {e}", exc_info=True)
                logger.error(f"Response text: {resp.text if resp is not None else 'No response'}")
                return None

            except Exception as e:
                logger.error(f"❌ Kutilmagan chunk xato: {e}", exc_info=True)
                return None

        if not success:
            logger.error(f"❌ Chunk yuklanmadi: {date_start} -> {date_end}, page={page}")
            return None

        result = body.get("result", {}) if body else {}
        data = result.get("data", [])

        logger.info(
            f"✅ Chunk {date_start} -> {date_end}, "
            f"page={page}: {len(data)} ta yozuv"
        )

        if not data:
            logger.info(f"📄 {date_start} -> {date_end}, page={page}: data bo'sh, stop")
            break

        all_data.extend(data)

        next_page_url = result.get("next_page_url")
        if not next_page_url:
            logger.info(f"✅ {date_start} -> {date_end}: oxirgi sahifa")
            break

        page += 1

    return all_data


def fetch_magnitka_from_api(date_start, date_end, token, station_code=None, chunk_days: int = 7):
    """
    Katta sana oralig'ini haftalik (7 kunlik) bo'lib, pagination bilan yuklaydi.
    Agar API timeout bersa, chunk_days=3 yoki chunk_days=1 qilib kamaytiring.
    """
    all_data = []

    try:
        ranges = split_date_range_by_week(date_start, date_end, chunk_days=chunk_days)
        logger.info(f"📦 Sana oralig'i {len(ranges)} ta chunk ga bo'lindi ({chunk_days} kunlik)")

        failed_chunks = []

        for chunk_start, chunk_end in ranges:
            logger.info(f"🚀 Yuklanmoqda: {chunk_start} -> {chunk_end}")

            chunk_data = fetch_magnitka_chunk(
                date_start=chunk_start,
                date_end=chunk_end,
                token=token,
                station_code=station_code,
                max_retries=3,
            )

            if chunk_data is None:
                logger.error(f"❌ Chunk yuklanmadi: {chunk_start} -> {chunk_end}")
                failed_chunks.append((chunk_start, chunk_end))
                continue

            all_data.extend(chunk_data)
            logger.info(
                f"✅ Chunk tugadi: {chunk_start} -> {chunk_end} | "
                f"{len(chunk_data)} ta | jami={len(all_data)}"
            )

            # API ni biroz asrash uchun
            time.sleep(0.3)

        if failed_chunks:
            logger.warning(f"⚠️ Yuklanmagan chunklar: {failed_chunks}")

        logger.info(f"🎯 Magnitka API dan jami {len(all_data)} ta yozuv olindi")
        return all_data

    except Exception as e:
        logger.error(f"❌ fetch_magnitka_from_api umumiy xato: {e}", exc_info=True)
        return None


def save_api_data_to_db(connection, data_list):
    """API dan kelgan ma'lumotlarni asosiy bazaga INSERT qilish"""
    if not data_list:
        return 0

    cursor = connection.cursor()

    cursor.execute("SELECT MAX(date) FROM alldata")
    last_date_row = cursor.fetchone()
    last_date = _parse_to_datetime(last_date_row[0]) if last_date_row else None

    cursor.execute("SELECT skvajina, izmereniya, ssdi_id FROM all_izmereniya")
    ssdi_cache = {}
    for row in cursor.fetchall():
        ssdi_cache[(normalize_string(row[0]), normalize_string(row[1]))] = str(row[2])

    parsed_items = []
    for item in data_list:
        parsed_date = _parse_to_datetime(item.get("date"))
        if parsed_date:
            parsed_items.append((parsed_date, item))
    parsed_items.sort(key=lambda x: x[0])

    inserted = 0

    for parsed_date, item in parsed_items:
        if last_date and parsed_date <= last_date:
            continue

        station_code = item.get("station_code")
        well_api = item.get("well_code")
        if not station_code or not well_api:
            continue

        db_well = None
        station_info = STATIONS_AND_WELLS.get(station_code)
        if station_info:
            for w in station_info["wells"]:
                if normalize_string(w["api_well"]) == normalize_string(well_api):
                    db_well = w["db_well"]
                    break
        if not db_well:
            continue

        values_dict = {"date": parsed_date}
        found_any = False

        for key, value in item.items():
            if key in ["date", "station_code", "well_code", "id", "created_at", "updated_at", "station", "well"]:
                continue
            if value is None or value == "":
                continue

            db_field_name = API_TO_DB_FIELD_MAPPING.get(key.lower(), key)
            ssdi_id = ssdi_cache.get((normalize_string(db_well), normalize_string(db_field_name)))
            if not ssdi_id:
                continue

            values_dict[ssdi_id] = value
            found_any = True

        if not found_any:
            continue

        columns = ", ".join(f"`{c}`" for c in values_dict.keys())
        placeholders = ", ".join(["%s"] * len(values_dict))
        sql = f"INSERT INTO alldata ({columns}) VALUES ({placeholders})"

        try:
            cursor.execute(sql, tuple(values_dict.values()))
            inserted += 1
        except Exception as e:
            logger.error(f"❌ API INSERT xato ({parsed_date}, {db_well}): {e}")

    connection.commit()
    cursor.close()
    return inserted


# ==================== Transfer flow ====================

def _do_transfer_data(src_conn, dst_conn, station_code, well_code, start_date, end_date):
    src_cursor = src_conn.cursor(dictionary=True)
    dst_cursor = dst_conn.cursor()

    # 1. Quduqlarni aniqlash
    target_wells = []
    if station_code == "all":
        for st_info in STATIONS_AND_WELLS.values():
            for w in st_info["wells"]:
                target_wells.append(w["db_well"])
    else:
        st_info = STATIONS_AND_WELLS.get(station_code)
        if not st_info:
            src_cursor.close();
            dst_cursor.close()
            return 0, 0, "Noto'g'ri stansiya kodi"
        if well_code == "all_wells":
            for w in st_info["wells"]:
                target_wells.append(w["db_well"])
        else:
            target_wells.append(well_code)

    if not target_wells:
        src_cursor.close();
        dst_cursor.close()
        return 0, 0, "Quduq topilmadi"

    # 2. ✅ save_api_data_to_db kabi — DST bazadan last_date olish
    dst_cursor.execute("SELECT MAX(date) FROM alldata")
    last_date_row = dst_cursor.fetchone()
    last_date = _parse_to_datetime(last_date_row[0]) if last_date_row else None
    logger.info(f"📅 DST last_date: {last_date}")

    # 3. ✅ save_api_data_to_db kabi — ssdi_cache to'liq yuklanadi
    src_cursor.execute("SELECT skvajina, izmereniya, ssdi_id FROM all_izmereniya")
    ssdi_cache = {}
    for row in src_cursor.fetchall():
        ssdi_cache[
            (normalize_string(row["skvajina"]), normalize_string(row["izmereniya"]))
        ] = str(row["ssdi_id"])

    # 4. src bazadan sana oralig'ini parse qilish
    start_dt = _parse_to_datetime(start_date)
    end_dt = _parse_to_datetime(end_date)
    if not start_dt or not end_dt:
        src_cursor.close();
        dst_cursor.close()
        return 0, 0, "Sana formati noto'g'ri (dd.mm.yyyy bo'lishi kerak)"

    # 5. src bazadan tanlangan quduqlarning ssdi_id larini olish
    placeholders = ", ".join(["%s"] * len(target_wells))
    src_cursor.execute(
        f"SELECT DISTINCT skvajina, ssdi_id FROM all_izmereniya "
        f"WHERE skvajina IN ({placeholders})",
        target_wells
    )
    well_ssdi_map = {}  # {db_well: [ssdi_id, ...]}
    for row in src_cursor.fetchall():
        well = row["skvajina"]
        sid = str(row["ssdi_id"])
        well_ssdi_map.setdefault(well, []).append(sid)

    if not well_ssdi_map:
        src_cursor.close();
        dst_cursor.close()
        return 0, 0, "all_izmereniya da mos quduqlar topilmadi"

    all_ssdi_ids = [sid for sids in well_ssdi_map.values() for sid in sids]

    # 6. src bazada mavjud ustunlarni tekshirish
    src_cursor.execute(
        "SELECT COLUMN_NAME FROM information_schema.columns "
        "WHERE table_schema=DATABASE() AND table_name='alldata'"
    )
    src_cols = {r["COLUMN_NAME"] for r in src_cursor.fetchall()}
    valid_ssdi = [sid for sid in all_ssdi_ids if sid in src_cols]

    if not valid_ssdi:
        src_cursor.close();
        dst_cursor.close()
        return 0, 0, "src bazada mos ustunlar topilmadi"

    # 7. dst bazada alldata mavjudligini tekshirish
    try:
        dst_cursor.execute("SELECT 1 FROM alldata LIMIT 1")
        dst_cursor.fetchall()
    except Exception:
        src_cursor.close();
        dst_cursor.close()
        return 0, 0, "Yangi bazada 'alldata' jadvali mavjud emas"

    # 8. dst bazada ustunlar yo'q bo'lsa qo'shish
    for sid in valid_ssdi:
        dst_cursor.execute(
            "SELECT COUNT(*) FROM information_schema.columns "
            "WHERE table_schema=DATABASE() AND table_name='alldata' AND column_name=%s",
            (sid,)
        )
        if dst_cursor.fetchone()[0] == 0:
            dst_cursor.execute(f"ALTER TABLE alldata ADD COLUMN `{sid}` FLOAT")
            dst_conn.commit()
            logger.info(f"🆕 DST ga ustun qo'shildi: {sid}")

    # 9. src dan ma'lumotlarni o'qish
    col_list = ", ".join(f"`{sid}`" for sid in valid_ssdi)
    src_cursor.execute(
        f"SELECT date, {col_list} FROM alldata "
        f"WHERE date BETWEEN %s AND %s ORDER BY date",
        (start_dt, end_dt)
    )
    rows = src_cursor.fetchall()
    logger.info(f"📊 src dan {len(rows)} ta qator o'qildi")

    if not rows:
        src_cursor.close();
        dst_cursor.close()
        return 0, 0, "Tanlangan oraliqda ma'lumot topilmadi"

    # 10. ✅ save_api_data_to_db kabi — INSERT logikasi
    newly_inserted = 0
    updated = 0

    for row in rows:
        parsed_date = _parse_to_datetime(row["date"])
        if not parsed_date:
            continue

        values_dict = {}
        for sid in valid_ssdi:
            val = row.get(sid)
            if val is not None and val != "":
                values_dict[sid] = val

        if not values_dict:
            continue

        # ✅ INSERT + ON DUPLICATE KEY UPDATE — ikkalasini birga hal qiladi
        cols = ["date"] + list(values_dict.keys())
        vals = [parsed_date] + list(values_dict.values())
        col_str = ", ".join(f"`{c}`" for c in cols)
        ph_str = ", ".join(["%s"] * len(vals))

        # Mavjud qiymatni o'zgartirmaydi, faqat NULL bo'lsa yozadi
        update_parts = [
            f"`{sid}` = COALESCE(`{sid}`, VALUES(`{sid}`))"
            for sid in values_dict.keys()
        ]

        sql = (
            f"INSERT INTO alldata ({col_str}) VALUES ({ph_str}) "
            f"ON DUPLICATE KEY UPDATE {', '.join(update_parts)}"
        )

        try:
            dst_cursor.execute(sql, vals)
            newly_inserted += 1
        except Exception as e:
            logger.error(f"❌ INSERT/UPDATE xato ({parsed_date}): {e}")

        else:
            # ✅ Mavjud sana — UPDATE (bo'sh ustunlarni to'ldirish)
            set_parts = [f"`{sid}`=COALESCE(`{sid}`, %s)"
                         for sid in values_dict if sid != "date"]
            vals = [v for k, v in values_dict.items() if k != "date"]
            vals.append(parsed_date)
            if set_parts:
                dst_cursor.execute(
                    f"UPDATE alldata SET {', '.join(set_parts)} WHERE date=%s",
                    vals
                )
                updated += 1

    dst_conn.commit()
    src_cursor.close()
    dst_cursor.close()

    logger.info(f"✅ INSERT={newly_inserted}, UPDATE={updated}")
    return newly_inserted, updated, None


def _extract_skvajina_name_from_filename(filename: str) -> str:
    name = filename.replace("Gidrogeoseysmologiya-", "").split("_")[0]
    return name.replace("'", "ʻ")


def save_excel_files_to_db(connection, uploaded_files):
    """Excel fayllarni o'qib asosiy bazaga UPDATE qilish"""
    cursor = connection.cursor()

    cursor.execute("SELECT MAX(date) FROM alldata")
    last_date_row = cursor.fetchone()
    last_date = last_date_row[0] if last_date_row and last_date_row[0] else None

    if last_date:
        curr = _parse_to_datetime(last_date) + dt.timedelta(days=1)
        while curr.date() <= dt.datetime.now().date():
            cursor.execute("INSERT INTO alldata (date) VALUES (%s)", (curr,))
            curr += dt.timedelta(days=1)
        connection.commit()

    updated_cells = 0

    for f in uploaded_files:
        filename = getattr(f, "name", "uploaded.xlsx")
        skvajina_name = _extract_skvajina_name_from_filename(filename)

        df = pd.read_excel(f)
        df = df.fillna(0)

        name = df.iloc[0].to_list()[2:]
        name.insert(0, "T/r")
        name.insert(1, "Sana")
        df.columns = name
        df.drop(0, inplace=True)

        df = df.set_index("T/r")
        df["Sana"] = pd.to_datetime(df["Sana"], format="%d.%m.%Y", errors="coerce")

        drop_cols = [
            col for col in df.columns
            if col != "Sana" and df[col].eq(0).all()
        ]
        df = df.drop(columns=drop_cols)

        for col in df.columns:
            if col == "Sana":
                continue

            cursor.execute(
                "SELECT ssdi_id FROM all_izmereniya WHERE skvajina=%s AND izmereniya=%s",
                (skvajina_name, col),
            )
            row = cursor.fetchone()
            if not row:
                logger.warning(f"⚠️ all_izmereniya da topilmadi: {skvajina_name} / {col}")
                continue

            ssdi_id = str(row[0])
            col_name_sql = f"`{ssdi_id}`"

            cursor.execute(
                "SELECT COUNT(*) FROM information_schema.columns "
                "WHERE table_schema=%s AND table_name='alldata' AND column_name=%s",
                (DB_NAME, ssdi_id),
            )
            exists = cursor.fetchone()[0] > 0
            if not exists:
                cursor.execute(f"ALTER TABLE alldata ADD COLUMN {col_name_sql} FLOAT;")
                connection.commit()

            cursor.execute(
                f"SELECT date FROM alldata WHERE {col_name_sql} IS NOT NULL ORDER BY date DESC LIMIT 1"
            )
            last_filled = cursor.fetchone()
            last_filled_date = last_filled[0] if last_filled else None

            if last_filled_date:
                last_filled_date = pd.to_datetime(last_filled_date)
                df1 = df.loc[:, ["Sana", col]]
                df1 = df1[df1["Sana"] >= last_filled_date]
            else:
                df1 = df.loc[:, ["Sana", col]]

            df1 = df1[df1[col] != 0].sort_values(by="Sana", ascending=True)

            for _, r in df1.iterrows():
                d = r["Sana"]
                v = r[col]
                if pd.isna(d) or v == 0:
                    continue
                cursor.execute(
                    f"UPDATE alldata SET {col_name_sql}=%s WHERE date=%s",
                    (float(v), d.to_pydatetime()),
                )
                updated_cells += 1

            connection.commit()

    cursor.close()
    return updated_cells


# ============================================================
# stations jadvalini API ma'lumotlari bilan avtomatik to'ldirish/yangilash
# ============================================================
def sync_stations(connection, api_data):
    """
    API javobidagi 'station' obyektidan stations jadvalini to'ldiradi.
    Faqat yangi code lar uchun INSERT qiladi, mavjudlarini yangilamaydi.
    """
    cursor = connection.cursor()

    # Mavjud kodlarni olish
    cursor.execute("SELECT code FROM stations")
    existing_codes = {row[0] for row in cursor.fetchall()}

    seen = set()
    for item in api_data:
        station_info = item.get("station") or {}
        code = item.get("station_code")
        if not code or code in existing_codes or code in seen:
            continue

        name = (station_info.get("name") or {}).get("uz") or code
        lat = station_info.get("latitude") or None
        lon = station_info.get("longitude") or None

        cursor.execute(
            """
            INSERT INTO stations (name, code, latitude, longitude)
            VALUES (%s, %s, %s, %s)
            """,
            (name, code, lat, lon)
        )
        seen.add(code)
        logger.info(f"➕ Yangi stansiya qo'shildi: {code} ({name})")

    connection.commit()
    cursor.close()


# ============================================================
# ASOSIY FUNKSIYA: measurements jadvaliga yozish
# ============================================================

def save_to_measurements(connection, api_data):
    """
    API dan kelgan magnitude ma'lumotlarini measurements jadvaliga yozadi.
    is_valid va recived_at ustunlari yozilmaydi (jadvaldagi DEFAULT qiymatlar ishlatiladi).
    """
    if not api_data:
        logger.warning("⚠️ Bo'sh ma'lumot keldi")
        return 0

    cursor = connection.cursor()

    # --- code → local station_id mapping ---
    cursor.execute("SELECT id, code FROM stations")
    code_to_id = {row[1]: row[0] for row in cursor.fetchall()}

    rows_to_insert = []
    skipped = 0
    unknown_codes = set()

    for item in api_data:

        # ── station_id (code orqali) ────────────────────────────────
        code = item.get("station_code")
        station_id = code_to_id.get(code)
        if not station_id:
            unknown_codes.add(code)
            skipped += 1
            continue

        # ── measured_at ──────────────────────────────────────────────
        raw_dt = item.get("datetime")
        if not raw_dt:
            skipped += 1
            continue
        try:
            measured_at = dt.datetime.strptime(raw_dt, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            logger.warning(f"⚠️ Vaqt formati xato: {raw_dt}")
            skipped += 1
            continue

        # ── value ─────────────────────────────────────────────────────
        raw_value = item.get("value")
        if raw_value is None or raw_value == "":
            skipped += 1
            continue
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            logger.warning(f"⚠️ Qiymat float ga o'tkazilmadi: {raw_value}")
            skipped += 1
            continue

        rows_to_insert.append((station_id, measured_at, value))

    if unknown_codes:
        logger.warning(f"⚠️ Noma'lum stansiya kodlari: {unknown_codes}")

    logger.info(f"📊 Tayyorlandi: {len(rows_to_insert)} ta | O'tkazildi: {skipped} ta")

    if not rows_to_insert:
        cursor.close()
        return 0

    # --- Batch INSERT (1000 tadan bo'lib) ---
    BATCH_SIZE = 1000
    affected = 0

    for i in range(0, len(rows_to_insert), BATCH_SIZE):
        batch = rows_to_insert[i: i + BATCH_SIZE]
        cursor.executemany(
            """
            INSERT INTO measurements (station_id, measured_at, value)
            VALUES (%s, %s, %s)
            ON DUPLICATE KEY UPDATE
                value    = VALUES(value)
            """,
            batch,
        )
        connection.commit()
        affected += cursor.rowcount
        logger.info(f"💾 {i + len(batch)} / {len(rows_to_insert)} yuborildi...")

    cursor.close()
    logger.info(f"✅ Jami {len(rows_to_insert)} ta qatordan {affected} ta ta'sirlandi")
    return len(rows_to_insert)


# ============================================================
# VIEW
# ============================================================
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def upload_measurements(request):
    if request.method != "POST":
        return JsonResponse({"success": False, "message": "POST kerak."}, status=405)

    try:
        req = MagnitkaSyncRequestSerializer(data=request.data)
        if not req.is_valid():
            first_error = next(iter(req.errors.values()))
            msg = first_error[0] if isinstance(first_error, list) else str(first_error)
            return JsonResponse({"success": False, "message": str(msg)}, status=400)
        v = req.validated_data
        date_start   = v["date_start"].isoformat()
        date_end     = v["date_end"].isoformat()
        station_code = v["station_code"] or None
        chunk_days   = v["chunk_days"]

        logger.info(
            f"📥 upload_measurements: date_start={date_start}, date_end={date_end}, "
            f"station_code={station_code}, chunk_days={chunk_days}"
        )

        if not date_start or not date_end:
            return JsonResponse(
                {"success": False, "message": "date_start va date_end kerak (YYYY-MM-DD)."},
                status=400
            )

        logger.info("🔐 Token olinmoqda (magnitka)...")
        token = get_auth_token_magnitka()
        if not token:
            logger.error("❌ Token olinmadi!")
            return JsonResponse({"success": False, "message": "Token olinmadi."}, status=401)

        logger.info(f"✅ Token olindi. API dan ma'lumot olinmoqda ({chunk_days} kunlik chunk)...")
        api_data = fetch_magnitka_from_api(date_start, date_end, token, station_code, chunk_days=chunk_days)

        if api_data is None:
            logger.error("❌ API dan olishda xato!")
            return JsonResponse({
                "success": False,
                "message": "API dan olishda xato. Log faylini tekshiring."
            }, status=500)

        if not api_data:
            logger.warning("⚠️ API bo'sh javob berdi")
            return JsonResponse({
                "success": False,
                "message": "API bo'sh javob berdi. Sana oralig'ini tekshiring."
            }, status=400)

        logger.info(f"✅ API dan {len(api_data)} ta qator olindi")

        conn = get_db_connection()

        # Yangi stansiyalar bo'lsa avtomatik qo'shadi
        sync_stations(conn, api_data)

        inserted = save_to_measurements(conn, api_data)
        conn.close()

        logger.info(f"✅ Muvaffaqiyat! {inserted} ta yozuv qayta ishlandi")

        return JsonResponse({
            "success": True,
            "processed": inserted,
            "message": f"Jami {inserted} ta yozuv qayta ishlandi."
        })

    except json.JSONDecodeError as e:
        logger.error(f"❌ JSON parse xatosi: {e}", exc_info=True)
        return JsonResponse({"success": False, "message": f"JSON xatosi: {e}"}, status=400)
    except Exception as e:
        logger.error(f"❌ upload_measurements: {e}", exc_info=True)
        return JsonResponse({"success": False, "message": str(e)}, status=500)


# ==================== Views ====================

def index(request):
    return render(request, "download_base_app/index.html", {"stations": STATIONS_AND_WELLS})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_stations_and_wells(request):
    """React frontend uchun: 1- va 3-bo'limlardagi stansiya/quduq ro'yxati.

    Eski sahifada bu ro'yxat template orqali (json_script) uzatilardi,
    React'da esa JSON endpoint kerak.
    """
    return JsonResponse({"success": True, "data": STATIONS_AND_WELLS})


# views.py

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_magnitka_stations(request):
    """
    Database stations jadvalidan stansiyalarni JSON bilan qaytarish
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT id, code, name, latitude, longitude FROM stations ORDER BY name"
        )
        stations_list = cursor.fetchall()
        cursor.close()
        conn.close()

        logger.info(f"✅ {len(stations_list)} ta stansiya DB dan olindi")

        return JsonResponse({
            "success": True,
            "data": stations_list
        })
    except Exception as e:
        logger.error(f"❌ Stansiyalarni olishda xato: {e}", exc_info=True)
        return JsonResponse({
            "success": False,
            "message": str(e)
        }, status=500)

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def upload_api(request):
    """1) API dan yuklab asosiy DBga yozish"""
    if request.method != "POST":
        return JsonResponse({"success": False, "message": "POST kerak."}, status=405)

    try:
        req = ApiUploadRequestSerializer(data=request.data)
        if not req.is_valid():
            first_error = next(iter(req.errors.values()))
            msg = first_error[0] if isinstance(first_error, list) else str(first_error)
            return JsonResponse({"success": False, "message": str(msg)}, status=400)
        v = req.validated_data
        station_code = v["station"]
        well_code = v["well"]
        start_date = v["start_date"]
        end_date = v["end_date"]

        token = get_auth_token()
        if not token:
            return JsonResponse({"success": False, "message": "Token olinmadi."}, status=401)

        api_targets = []
        if station_code == "all":
            for st_code, st_info in STATIONS_AND_WELLS.items():
                for w in st_info["wells"]:
                    api_targets.append({"station_code": st_code, "well_code": w["api_well"]})
        else:
            st_info = STATIONS_AND_WELLS.get(station_code)
            if not st_info:
                return JsonResponse({"success": False, "message": "Noto'g'ri stansiya."}, status=400)
            if well_code == "all_wells":
                for w in st_info["wells"]:
                    api_targets.append({"station_code": station_code, "well_code": w["api_well"]})
            else:
                api_targets.append({"station_code": station_code, "well_code": well_code})

        all_data = []
        for t in api_targets:
            params = {
                "station_code": t["station_code"],
                "well_code": t["well_code"],
                "date_start": start_date,
                "date_end": end_date,
            }
            chunk = fetch_data_from_api(params, token)
            if chunk is None:
                return JsonResponse({"success": False, "message": "API'dan olishda xato."}, status=500)
            all_data.extend(chunk)
            time.sleep(1)

        conn = get_db_connection()
        inserted = save_api_data_to_db(conn, all_data)
        conn.close()

        return JsonResponse({"success": True, "message": f"API orqali {inserted} ta yozuv qo'shildi."})

    except Exception as e:
        logger.error(f"❌ upload_api xato: {e}", exc_info=True)
        return JsonResponse({"success": False, "message": str(e)}, status=500)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def upload_excel(request):
    """2) Excel fayl yuklab asosiy DBga yozish"""
    if request.method != "POST":
        return JsonResponse({"success": False, "message": "POST kerak."}, status=405)

    try:
        file = request.FILES.getlist("file")
        if not file:
            return JsonResponse({"success": False, "message": "Excel fayl yuborilmadi."}, status=400)

        conn = get_db_connection()
        updated_cells = save_excel_files_to_db(conn, file)
        conn.close()

        return JsonResponse({"success": True, "message": f"Excel orqali {updated_cells} ta qiymat yangilandi."})

    except Exception as e:
        logger.error(f"❌ upload_excel xato: {e}", exc_info=True)
        return JsonResponse({"success": False, "message": str(e)}, status=500)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def transfer_to_new_db(request):
    """3) Asosiy bazadan yangi bazaga ma'lumot ko'chirish (.env dan olinadi)"""
    if request.method != "POST":
        return JsonResponse({"success": False, "message": "POST kerak."}, status=405)

    try:
        req = ApiUploadRequestSerializer(data=request.data)
        if not req.is_valid():
            first_error = next(iter(req.errors.values()))
            msg = first_error[0] if isinstance(first_error, list) else str(first_error)
            return JsonResponse({"success": False, "message": str(msg)}, status=400)
        v = req.validated_data
        station_code = v["station"]
        well_code = v["well"]
        start_date = v["start_date"]
        end_date = v["end_date"]

        # Asosiy bazaga ulanish
        src_conn = get_db_connection()

        # Yangi bazaga ulanish (.env dan)
        try:
            dst_conn = get_custom_db_connection(
                NEW_DB_HOST, NEW_DB_USER, NEW_DB_PASSWORD, NEW_DB_NAME
            )
        except mysql.connector.Error as e:
            src_conn.close()
            logger.error(f"❌ Bazaga ulanishda xato: {e}")
            return JsonResponse(
                {"success": False, "message": f" Bazaga ulanib bo'lmadi: {e}"},
                status=400
            )

        newly_inserted, updated, error = _do_transfer_data(
            src_conn, dst_conn,
            station_code, well_code,
            start_date, end_date
        )

        src_conn.close()
        dst_conn.close()

        if error:
            return JsonResponse({"success": False, "message": error}, status=400)

        return JsonResponse({
            "success": True,
            "message": (
                f"Yangi qo'shildi: {newly_inserted} ta | "
                f"Yangilandi: {updated} ta | "
                f"Jami: {newly_inserted + updated} ta"
            )
        })

    except Exception as e:
        logger.error(f"❌ transfer_to_new_db xato: {e}", exc_info=True)
        return JsonResponse({"success": False, "message": str(e)}, status=500)