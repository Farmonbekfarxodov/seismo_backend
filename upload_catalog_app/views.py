import pandas as pd
import requests
import json
import datetime
from datetime import timedelta
import csv
import io

from django.db.models import Min, Max
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status

from .models import Catalog
from .serializers import CatalogSerializer, ManualEntrySerializer

DATA_URL = "https://api.smrm.uz/api/earthquakes/central-asia"


def fetch_data_from_api(params):
    all_data = []
    url = DATA_URL
    try:
        while url:
            response = requests.get(url, params=params, timeout=30)
            response.raise_for_status()
            response_data = response.json()

            if response_data and response_data.get('result'):
                data = response_data['result'].get('data', [])
                all_data.extend(data)
                url = response_data['result'].get('next_page_url')
                params = {}
            else:
                break
        return all_data
    except (requests.exceptions.RequestException, json.JSONDecodeError) as e:
        print(f"API dan ma'lumot olishda xato: {e}")
        return None


def save_data_to_db(data_list):
    if not data_list:
        return 0

    last_record = Catalog.objects.order_by("-Event_date", "-Event_time").first()
    rows_to_create = []

    for item in data_list:
        try:
            formatted_date_naive = datetime.datetime.strptime(item.get("date"), "%d.%m.%Y").date()
        except (ValueError, TypeError):
            continue

        try:
            formatted_time = datetime.datetime.strptime(item.get("time"), "%H:%M:%S").time()
        except (ValueError, TypeError):
            formatted_time = None

        if last_record and (
                formatted_date_naive < last_record.Event_date or
                (formatted_date_naive == last_record.Event_date and formatted_time <= last_record.Event_time)
        ):
            continue

        new_record = Catalog(
            Event_date=formatted_date_naive,
            Event_time=formatted_time,
            Latitude=float(item.get('latitude')),
            Longitude=float(item.get('longitude')),
            Depth=float(item.get('depth')),
            Mb=float(item.get('magnitude')),
            Epicenter=item.get('epicenter')
        )
        rows_to_create.append(new_record)

    rows_to_create.sort(key=lambda r: (r.Event_date, r.Event_time))
    Catalog.objects.bulk_create(rows_to_create)
    return len(rows_to_create)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def catalog_list(request):
    """GET /api/catalog/  — so'nggi 20 ta yozuv + sana oralig'i."""
    all_records = Catalog.objects.all().order_by("-Event_date", "-Event_time")
    records = all_records[:20]

    date_range = Catalog.objects.aggregate(
        start_date=Min("Event_date"),
        end_date=Max("Event_date")
    )

    return Response({
        "records": CatalogSerializer(records, many=True).data,
        "start_date": date_range["start_date"],
        "end_date": date_range["end_date"],
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def upload_catalog(request):
    """POST /api/catalog/upload-catalog/ — tashqi API'dan zilzilalarni tortib olish."""
    params = {"sort": "datetime_desc", "per_page": 50, "page": 1}
    data = fetch_data_from_api(params)

    if data is None:
        return Response(
            {"success": False, "message": "Tashqi API'dan ma'lumot olishda xatolik."},
            status=status.HTTP_502_BAD_GATEWAY,
        )

    new_count = save_data_to_db(data)

    return Response({
        "success": True,
        "added": new_count,
        "message": f"{new_count} ta yangi ma'lumot qo'shildi." if new_count > 0 else "Yangi ma'lumotlar yo'q.",
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def upload_from_file(request):
    """POST /api/catalog/upload-file/  (multipart/form-data, 'file' maydoni bilan)."""
    uploaded_file = request.FILES.get("file")

    if not uploaded_file:
        return Response(
            {"success": False, "message": "Iltimos, fayl tanlang!"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    ext = uploaded_file.name.split(".")[-1].lower()

    try:
        if ext == "csv":
            df = pd.read_csv(uploaded_file, encoding="utf-8-sig")
        elif ext in ["xlsx", "xls"]:
            df = pd.read_excel(uploaded_file)
        else:
            return Response(
                {"success": False, "message": f"Noto'g'ri fayl turi: {ext}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        added_count, errors = save_file_data_to_db(df)

        return Response({
            "success": True,
            "added": added_count,
            "errors": errors[:10],
            "error_count": len(errors),
        })

    except Exception as e:
        return Response(
            {"success": False, "message": f"Xatolik: {str(e)}"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


def save_file_data_to_db(df):
    """
    Optimallashtirilgan va bardavom versiya:
    - Pandas bilan to'g'ri konvertatsiya (numeric/date/time)
    - Bo'sh yoki noto'g'ri qiymatlarni aniqlash va o'tkazib yuborish
    - Dublikatlar bitta so'rov bilan tekshirish
    - bulk_create bilan saqlash
    """

    # pastga tushuncha: ustun nomlarini kichik harflarga o'tkazamiz
    df.columns = [c.lower() for c in df.columns]

    required_columns = ["date", "time", "latitude", "longitude", "depth", "mb", "epicenter"]
    for col in required_columns:
        if col not in df.columns:
            raise ValueError(f"{col} ustuni topilmadi!")

    # 1) Sana va vaqtlarni parse qilish
    parsed_dates = pd.to_datetime(df['date'], dayfirst=True, errors='coerce')

    # TIME PARSE QILISH - TUZATILGAN QISM
    # Turli xil formatlarni qo'llab-quvvatlash uchun
    def parse_time_column(time_series):
        """Vaqtni parse qilish - turli formatlarni qo'llab-quvvatlaydi"""
        parsed_times = []

        for val in time_series:
            if pd.isna(val) or val == '' or str(val).strip() == '':
                parsed_times.append(None)
                continue

            val_str = str(val).strip()

            try:
                # Avval to'g'ridan-to'g'ri time formatida parse qilishga harakat
                if ':' in val_str:
                    # Format: "14:30:45" yoki "14:30"
                    parts = val_str.split(':')
                    if len(parts) == 3:
                        h, m, s = int(parts[0]), int(parts[1]), int(parts[2])
                        parsed_times.append(datetime.time(h, m, s))
                    elif len(parts) == 2:
                        h, m = int(parts[0]), int(parts[1])
                        parsed_times.append(datetime.time(h, m, 0))
                    else:
                        parsed_times.append(None)
                else:
                    # Agar ':' bo'lmasa, pd.to_datetime bilan harakat qilamiz
                    dt = pd.to_datetime(val_str, errors='coerce')
                    if pd.notna(dt):
                        parsed_times.append(dt.time())
                    else:
                        parsed_times.append(None)
            except:
                parsed_times.append(None)

        return parsed_times

    parsed_times = parse_time_column(df['time'])

    # 2) Raqamli ustunlarni numeric ga o'tkazish
    def to_numeric_series(s):
        s = s.astype(str).str.replace(',', '.')
        s = s.replace(r'^\s*$', pd.NA, regex=True)
        return pd.to_numeric(s, errors='coerce')

    lat_s = to_numeric_series(df['latitude'])
    lon_s = to_numeric_series(df['longitude'])
    depth_s = to_numeric_series(df['depth'])
    mag_s = to_numeric_series(df['mb'])

    # 3) Yangi DataFrame
    clean_df = pd.DataFrame({
        'date': parsed_dates.dt.date,
        'time': parsed_times,
        'latitude': lat_s,
        'longitude': lon_s,
        'depth': depth_s,
        'magnitude': mag_s,
        'epicenter': df['epicenter'].astype(str).fillna('').replace('nan', '')
    }, index=df.index)

    # 4) Aniqlash: qaysi qatorlar yetishmayapti
    missing_mask = (
            clean_df['date'].isna() |
            clean_df['time'].isna() |
            clean_df['latitude'].isna() |
            clean_df['longitude'].isna() |
            clean_df['depth'].isna() |
            clean_df['magnitude'].isna()
    )

    errors = []
    if missing_mask.any():
        missing_idxs = clean_df[missing_mask].index.tolist()
        for idx in missing_idxs[:20]:
            row = df.loc[idx].to_dict()
            missing_cols = clean_df.columns[clean_df.loc[idx].isna()].tolist()
            errors.append(
                f"Qator {idx + 2}: Yetishmayotgan yoki noto'g'ri qiymatlar: {', '.join(missing_cols)} | Asl qiymat: time='{df.loc[idx, 'time']}'")
        if len(missing_idxs) > 20:
            errors.append(f"... va yana {len(missing_idxs) - 20} ta qator noto'g'ri yoki bo'sh qiymatga ega.")

    # 5) Faqat to'g'ri qatorlarni saqlaymiz
    good_df = clean_df[~missing_mask].copy()
    if good_df.empty:
        print("⚠️ Saqlanadigan yangi ma'lumotlar yo'q - barcha qatorlarda yetishmaydigan qiymatlar bor.")
        if errors:
            print("Birinchi xatolar (maks 20):")
            for e in errors[:20]:
                print(" -", e)
        return 0, errors

    # 6) Dublikatlarni tekshirish
    existing_keys = set(
        Catalog.objects.values_list("Event_date", "Event_time", "Latitude", "Longitude")
    )

    rows_to_create = []
    skipped_duplicates = 0
    for idx, row in good_df.iterrows():
        key = (row['date'], row['time'], float(row['latitude']), float(row['longitude']))
        if key in existing_keys:
            skipped_duplicates += 1
            continue

        rows_to_create.append(Catalog(
            Event_date=row['date'],
            Event_time=row['time'],
            Latitude=float(row['latitude']),
            Longitude=float(row['longitude']),
            Depth=float(row['depth']),
            Mb=float(row['magnitude']),
            Epicenter=str(row['epicenter']).strip()
        ))
        existing_keys.add(key)

    # 7) Bulk save
    if rows_to_create:
        Catalog.objects.bulk_create(rows_to_create, ignore_conflicts=True)

    # 8) Konsolga summarizatsiya
    print(
        f"✅ Fayldan ishlov berildi: Jami qatorlar: {len(df)}, to'g'ri qatorlar: {len(good_df)}, saqlandi: {len(rows_to_create)}, dublikat o'tkazib yuborildi: {skipped_duplicates}, xatolar: {len(errors)}")

    return len(rows_to_create), errors



@api_view(['POST'])
@permission_classes([IsAuthenticated])
def manual_entry(request):
    """
    POST /api/catalog/manual-entry/
    Body (JSON): { event_date, event_time, latitude, longitude, depth, magnitude, epicenter }
    """
    payload = {
        "Event_date": request.data.get("event_date"),
        "Event_time": request.data.get("event_time"),
        "Latitude": request.data.get("latitude"),
        "Longitude": request.data.get("longitude"),
        "Depth": request.data.get("depth"),
        "Mb": request.data.get("magnitude"),
        "Epicenter": request.data.get("epicenter", ""),
    }

    serializer = ManualEntrySerializer(data=payload)
    if not serializer.is_valid():
        return Response(
            {"success": False, "errors": serializer.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )

    instance = serializer.save()
    return Response({
        "success": True,
        "message": "Ma'lumot muvaffaqiyatli qo'shildi.",
        "record": CatalogSerializer(instance).data,
    }, status=status.HTTP_201_CREATED)