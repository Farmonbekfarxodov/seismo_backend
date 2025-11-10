import mysql.connector
import requests
import json
import datetime as dt
import time
import logging
from collections import defaultdict

from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

# ✅ Logging sozlamalari
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
            {"api_well": "Yangi yo'l", "db_well": "Yangi yo'l"}
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
            {"api_well": "Chimyon 1-YU", "db_well": "Chimyon 1-Yu"},
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
}

LOGIN_URL = "https://api.geofizik.uz/api/login"
DATA_URL = "https://api.geofizik.uz/api/hydrogen-seismologies"
USERNAME = "Rasulov Alisher"
PASSWORD = "rasulovalisher"


def get_auth_token():
    try:
        payload = {"username": USERNAME, "password": PASSWORD}
        response = requests.post(LOGIN_URL, json=payload, timeout=10)
        response.raise_for_status()
        token = response.json().get("result", {}).get("token")
        if token:
            logger.info("✅ Token muvaffaqiyatli olindi")
        return token
    except (requests.exceptions.RequestException, json.JSONDecodeError) as e:
        logger.error(f"❌ Login xatosi: {e}")
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

            if response_data and response_data.get('result'):
                data = response_data['result'].get('data', [])
                all_data.extend(data)
                logger.info(f"📥 {len(data)} ta yozuv olindi. Jami: {len(all_data)}")
                url = response_data['result'].get('next_page_url')
                params = {}
            else:
                break

        logger.info(f"✅ API dan jami {len(all_data)} ta yozuv olindi")
        return all_data
    except (requests.exceptions.RequestException, json.JSONDecodeError) as e:
        logger.error(f"❌ API dan ma'lumot olishda xato: {e}")
        return None


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
    """Stringni normalizatsiya qilish - bo'sh joylar, kichik/katta harflar"""
    if not s:
        return ""
    s = s.replace('ʻ', "'").replace('’', "'").replace('‘', "'")
    return s.strip().lower()


def save_data_to_db(connection, data_list, db):
    """
    Diagnostika bilan yaxshilangan versiya
    """
    if not data_list:
        logger.warning("⚠️ Ma'lumotlar ro'yxati bo'sh.")
        return 0

    cursor = connection.cursor()

    # Diagnostika uchun hisoblagichlar
    stats = {
        'total_items': len(data_list),
        'skipped_old_dates': 0,
        'skipped_no_station': 0,
        'skipped_no_well_mapping': 0,
        'skipped_no_ssdi': 0,
        'skipped_empty_values': 0,
        'inserted': 0,
        'errors': 0
    }

    unmapped_wells = defaultdict(list)  # Moslik topilmagan quduqlar
    missing_ssdi = defaultdict(set)  # ssdi_id topilmagan kombinatsiyalar

    # Bazadagi eng so'nggi sanani olish
    cursor.execute("SELECT MAX(date) FROM alldata")
    last_date_row = cursor.fetchone()
    raw_last_date = last_date_row[0] if last_date_row and last_date_row[0] else None
    last_date = _parse_to_datetime(raw_last_date)

    if last_date:
        logger.info(f"🕓 Bazadagi eng so'nggi sana: {last_date.strftime('%d.%m.%Y')}")
    else:
        logger.info("⚠️ Bazada sana topilmadi, barcha ma'lumotlar qo'shiladi.")

    # all_izmereniya jadvalidagi barcha ssdi_id larni oldindan keshga olish
    cursor.execute("SELECT skvajina, izmereniya, ssdi_id FROM all_izmereniya")
    ssdi_cache = {}
    for row in cursor.fetchall():
        key = (normalize_string(row[0]), normalize_string(row[1]))
        ssdi_cache[key] = str(row[2])

    logger.info(f"📊 Keshda {len(ssdi_cache)} ta ssdi_id mavjud")

    # Ma'lumotlarni sanaga qarab saralash
    parsed_items = []
    for item in data_list:
        parsed_date = _parse_to_datetime(item.get('date'))
        if parsed_date:
            parsed_items.append((parsed_date, item))
    parsed_items.sort(key=lambda x: x[0])

    for parsed_date, item in parsed_items:
        # Eski sanalarni o'tkazib yuborish
        if last_date and parsed_date <= last_date:
            stats['skipped_old_dates'] += 1
            continue

        station_code = item.get('station_code')
        well_api = item.get('well_code')

        if not station_code or not well_api:
            stats['skipped_no_station'] += 1
            logger.warning(f"⚠️ Stansiya yoki quduq kodi yo'q: {item}")
            continue

        # API well nomini DB well nomiga o'tkazish
        db_well = None
        station_info = STATIONS_AND_WELLS.get(station_code)
        if station_info:
            for well in station_info["wells"]:
                # Normalizatsiya qilingan taqqoslash
                if normalize_string(well["api_well"]) == normalize_string(well_api):
                    db_well = well["db_well"]
                    break

        if not db_well:
            stats['skipped_no_well_mapping'] += 1
            unmapped_wells[station_code].append(well_api)
            logger.warning(f"⚠️ {station_code}/{well_api} uchun db_well topilmadi")
            continue

        values_dict = {'date': parsed_date}
        found_any_value = False

        for key, value in item.items():
            if key in ['date', 'station_code', 'well_code', 'id', 'created_at', 'updated_at', 'station', 'well']:
                continue
            # ⚠️ Faqat None va bo'sh stringlarni o'tkazib yuborish, 0 ni yozish
            if value is None or value == '':
                continue

            # ✅ API maydon nomini bazaga mapping qilish
            db_field_name = API_TO_DB_FIELD_MAPPING.get(key.lower(), key)

            # Keshdan qidirish
            cache_key = (normalize_string(db_well), normalize_string(db_field_name))
            ssdi_id = ssdi_cache.get(cache_key)

            if not ssdi_id:
                missing_ssdi[db_well].add(f"{key} → {db_field_name}")
                continue

            values_dict[ssdi_id] = value
            found_any_value = True

        if not found_any_value:
            stats['skipped_empty_values'] += 1
            continue

        columns = ", ".join(f"`{col}`" for col in values_dict.keys())
        placeholders = ", ".join(["%s"] * len(values_dict))
        sql = f"INSERT INTO alldata ({columns}) VALUES ({placeholders})"

        try:
            cursor.execute(sql, tuple(values_dict.values()))
            stats['inserted'] += 1
        except Exception as e:
            stats['errors'] += 1
            logger.error(f"❌ INSERT xato ({parsed_date}, {db_well}): {e}")

    connection.commit()
    cursor.close()

    # ✅ Batafsil hisobot
    logger.info("=" * 60)
    logger.info("📊 NATIJALAR HISOBOTI:")
    logger.info(f"  Jami yozuvlar: {stats['total_items']}")
    logger.info(f"  ✅ Qo'shildi: {stats['inserted']}")
    logger.info(f"  ⏭️  Eski sanalar: {stats['skipped_old_dates']}")
    logger.info(f"  ⚠️  Stansiya/quduq yo'q: {stats['skipped_no_station']}")
    logger.info(f"  ⚠️  Well mapping yo'q: {stats['skipped_no_well_mapping']}")
    logger.info(f"  ⚠️  SSDI topilmadi: {stats['skipped_no_ssdi']}")
    logger.info(f"  ⚠️  Bo'sh qiymatlar: {stats['skipped_empty_values']}")
    logger.info(f"  ❌ Xatolar: {stats['errors']}")

    if unmapped_wells:
        logger.warning("\n⚠️ MOSLIK TOPILMAGAN QUDUQLAR:")
        for station, wells in unmapped_wells.items():
            logger.warning(f"  {station}: {', '.join(set(wells))}")

    if missing_ssdi:
        logger.warning("\n⚠️ SSDI_ID TOPILMAGAN KOMBINATSIYALAR:")
        for well, measurements in missing_ssdi.items():
            logger.warning(f"  {well}: {', '.join(measurements)}")

    logger.info("=" * 60)

    return stats['inserted']


def index(request):
    return render(request, 'download_base_app/index.html', {'stations': STATIONS_AND_WELLS})


@csrf_exempt
def upload(request):
    if request.method == 'GET':
        return render(request, 'download_base_app/index.html', {'stations': STATIONS_AND_WELLS})

    elif request.method == 'POST':
        try:
            data = json.loads(request.body)
            station_code = data.get('station')
            well_code = data.get('well')
            start_date = data.get('start_date')
            end_date = data.get('end_date')

            logger.info(f"📤 Yuklash so'rovi: {station_code}/{well_code} ({start_date} - {end_date})")

            if not all([station_code, well_code, start_date, end_date]):
                return JsonResponse({"success": False, "message": "Ma'lumotlar to'liq emas."}, status=400)

            auth_token = get_auth_token()
            if not auth_token:
                return JsonResponse({"success": False, "message": "Login xato yoki token olinmadi."}, status=401)

            all_data = []
            api_stations_to_fetch = []

            if station_code == "all":
                for st_code, station_info in STATIONS_AND_WELLS.items():
                    for well in station_info["wells"]:
                        api_stations_to_fetch.append({"station_code": st_code, "well_code": well["api_well"]})
            else:
                station_info = STATIONS_AND_WELLS.get(station_code)
                if not station_info:
                    return JsonResponse({"success": False, "message": f"Noto'g'ri stansiya kodi: {station_code}"},
                                        status=400)

                if well_code == "all_wells":
                    for well in station_info["wells"]:
                        api_stations_to_fetch.append({"station_code": station_code, "well_code": well["api_well"]})
                else:
                    api_stations_to_fetch.append({"station_code": station_code, "well_code": well_code})

            logger.info(f"🔄 Jami {len(api_stations_to_fetch)} ta quduqdan ma'lumot olinadi")

            for fetch_item in api_stations_to_fetch:
                params = {
                    'station_code': fetch_item["station_code"],
                    'well_code': fetch_item["well_code"],
                    'date_start': start_date,
                    'date_end': end_date
                }

                fetched_data = fetch_data_from_api(params, auth_token)

                if fetched_data is None:
                    return JsonResponse({"success": False, "message": "API'dan ma'lumot olishda xato."}, status=500)

                all_data.extend(fetched_data)
                time.sleep(1)

            connection = mysql.connector.connect(
                host='localhost',
                user='root',
                password='1111',
                database='seismik'
            )

            rows_inserted = save_data_to_db(connection, all_data, 'seismik')
            connection.close()

            return JsonResponse(
                {"success": True, "message": f"{rows_inserted} ta yozuv bazaga saqlandi. Batafsil ma'lumot logda."})

        except json.JSONDecodeError:
            logger.error("❌ Noto'g'ri JSON format")
            return JsonResponse({"success": False, "message": "Noto'g'ri JSON format."}, status=400)
        except Exception as e:
            logger.error(f"❌ Kutilmagan xatolik: {type(e).__name__}: {str(e)}", exc_info=True)
            return JsonResponse({"success": False, "message": f"Kutilmagan xatolik: {type(e).__name__}: {str(e)}"},
                                status=500)

    return JsonResponse({"success": False, "message": "Noto'g'ri so'rov usuli."}, status=405)