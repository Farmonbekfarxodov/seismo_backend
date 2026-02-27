import json
import time
import logging
import datetime as dt
from collections import defaultdict

import pandas as pd
import mysql.connector
import requests

from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from decouple import config


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
    "Jizzax KPS":{
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

# ✅ DB credentials (tavsiya: .env dan oling)
DB_HOST = config("DB_HOST", default="localhost")
DB_USER = config("DB_USER", default="root")
DB_PASSWORD = config("DB_PASSWORD", default="1111")
DB_NAME = config("DB_NAME", default="seismik")


# -------------------- Helpers --------------------

def get_db_connection():
    return mysql.connector.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
    )


def _parse_to_datetime(value):
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value
    if isinstance(value, dt.date):
        return dt.datetime.combine(value, dt.time())
    try:
        # API/Excel dan kelishi mumkin bo'lgan format
        return dt.datetime.strptime(value, '%d.%m.%Y')
    except Exception:
        return None


def normalize_string(s):
    if not s:
        return ""
    s = s.replace('ʻ', "'").replace('’', "'").replace('‘', "'")
    return s.strip().lower()


# -------------------- API flow --------------------

def get_auth_token():
    payload = {"username": USERNAME, "password": PASSWORD}
    try:
        response = requests.post(LOGIN_URL, json=payload, timeout=10)
        response.raise_for_status()
        token = response.json().get("result", {}).get("token")
        return token
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
                params = {}  # next_page_url bo'lsa paramsni tozalaymiz
            else:
                break

        return all_data
    except Exception as e:
        logger.error(f"❌ API dan olishda xato: {e}", exc_info=True)
        return None


def save_api_data_to_db(connection, data_list):
    """
    Siz yuborgan views.py dagi save_data_to_db logikasi (qisqartirib joyladim),
    maqsad: API dan kelgan data_list ni alldata ga INSERT qilish.
    """
    if not data_list:
        return 0

    cursor = connection.cursor()

    # last date
    cursor.execute("SELECT MAX(date) FROM alldata")
    last_date_row = cursor.fetchone()
    last_date = _parse_to_datetime(last_date_row[0]) if last_date_row else None

    # ssdi cache
    cursor.execute("SELECT skvajina, izmereniya, ssdi_id FROM all_izmereniya")
    ssdi_cache = {}
    for row in cursor.fetchall():
        ssdi_cache[(normalize_string(row[0]), normalize_string(row[1]))] = str(row[2])

    # sort by date
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


# -------------------- Excel flow --------------------

def _extract_skvajina_name_from_filename(filename: str) -> str:
    # Gidrogeoseysmologiya-XXXX_...xlsx formatidan XXXX ni olish
    name = filename.replace("Gidrogeoseysmologiya-", "").split("_")[0]
    return name.replace("'", "ʻ")


def save_excel_files_to_db(connection, uploaded_files):
    """
    Django orqali yuklangan excel fayllarni o'qib DBga UPDATE/INSERT qilish.
    Sizning eski (excel->db) skriptingizdagi g'oya:
    - Excelni o'qish
    - all_izmereniya dan ssdi_id topish
    - alldata da ustun bo'lmasa ALTER TABLE bilan qo'shish
    - date bo'yicha UPDATE qilish
    """
    cursor = connection.cursor()

    # alldata dagi oxirgi sanadan keyingi sanalarni to'ldirish (sizdagi logika)
    cursor.execute("SELECT MAX(date) FROM alldata")
    last_date_row = cursor.fetchone()
    last_date = last_date_row[0] if last_date_row and last_date_row[0] else None

    # Agar alldata bo'sh bo'lsa, bu qismni sizning talabingizga qarab o'zgartirish mumkin
    # Hozircha alldata bo'sh bo'lsa "days insert"ni o'tkazib yuboramiz.
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

        # pandas ExcelFile: file-like obyektni o'qiy oladi
        df = pd.read_excel(f)
        df = df.fillna(0)

        # Sarlavha qayta tiklash (sizdagi kabi: 1-qator header)
        name = df.iloc[0].to_list()[2:]
        name.insert(0, "T/r")
        name.insert(1, "Sana")
        df.columns = name
        df.drop(0, inplace=True)

        df = df.set_index("T/r")
        df["Sana"] = pd.to_datetime(df["Sana"], format="%d.%m.%Y", errors="coerce")

        # 0 bo'lgan ustunlarni o'chirish (butun ustun 0 bo'lsa)
        drop_cols = []
        for col in df.columns:
            if col == "Sana":
                continue
            if df[col].eq(0).all():
                drop_cols.append(col)
        df = df.drop(columns=drop_cols)

        for col in df.columns:
            if col == "Sana":
                continue

            # all_izmereniya dan ssdi_id olish
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

            # ustun bor-yo'qligi
            cursor.execute(
                "SELECT COUNT(*) FROM information_schema.columns "
                "WHERE table_schema=%s AND table_name='alldata' AND column_name=%s",
                (DB_NAME, ssdi_id),
            )
            exists = cursor.fetchone()[0] > 0
            if not exists:
                cursor.execute(f"ALTER TABLE alldata ADD COLUMN {col_name_sql} FLOAT;")
                connection.commit()

            # bazada oxirgi to'ldirilgan sanani topish
            cursor.execute(
                f"SELECT date FROM alldata WHERE {col_name_sql} IS NOT NULL ORDER BY date DESC LIMIT 1"
            )
            last_filled = cursor.fetchone()
            last_filled_date = last_filled[0] if last_filled else None
            if last_filled_date:
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


# -------------------- Views (1 page, 2 actions) --------------------

def index(request):
    # Bitta sahifada 2 bo'lim chiqishi uchun context qaytaramiz
    return render(request, "download_base_app/index.html", {"stations": STATIONS_AND_WELLS})


@csrf_exempt
def upload_api(request):
    """
    1) Saytdan/API dan yuklab olib DBga yozish endpoint
    """
    if request.method != "POST":
        return JsonResponse({"success": False, "message": "POST kerak."}, status=405)

    try:
        data = json.loads(request.body)
        station_code = data.get("station")
        well_code = data.get("well")
        start_date = data.get("start_date")
        end_date = data.get("end_date")

        if not all([station_code, well_code, start_date, end_date]):
            return JsonResponse({"success": False, "message": "Ma'lumotlar to'liq emas."}, status=400)

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


@csrf_exempt
def upload_excel(request):
    """
    2) Excel fayl yuklab DBga yozish endpoint
    Form-data (multipart) orqali keladi: files[]
    """
    if request.method != "POST":
        return JsonResponse({"success": False, "message": "POST kerak."}, status=405)

    try:
        files = request.FILES.getlist("files")
        if not files:
            return JsonResponse({"success": False, "message": "Excel fayl yuborilmadi."}, status=400)

        conn = get_db_connection()
        updated_cells = save_excel_files_to_db(conn, files)
        conn.close()

        return JsonResponse({"success": True, "message": f"Excel orqali {updated_cells} ta qiymat yangilandi."})

    except Exception as e:
        logger.error(f"❌ upload_excel xato: {e}", exc_info=True)
        return JsonResponse({"success": False, "message": str(e)}, status=500)