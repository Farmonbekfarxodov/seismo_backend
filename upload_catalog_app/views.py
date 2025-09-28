import requests
import json
import datetime
import time

from django.shortcuts import render
from django.http import JsonResponse
from django.utils import timezone


from .models import Catalog

DATA_URL = "https://api.smrm.uz/api/earthquakes/central-asia"


def fetch_data_from_api(params):
    all_data = []
    url = DATA_URL

    try:
        while url:
            response = requests.get(url,params=params, timeout=30)
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

        # Bazadagi eng oxirgi API id yoki sana/vaqtni topamiz
    last_record = Catalog.objects.order_by("-Date", "-Time").first()
    rows_to_create = []

    for item in data_list:
        # Date
        try:
            formatted_date_naive = datetime.datetime.strptime(item.get("date"), "%d.%m.%Y").date()
        except (ValueError, TypeError):
            continue  # noto‘g‘ri sana bo‘lsa, o‘tkazib yuboramiz

        # Time
        try:
            formatted_time = datetime.datetime.strptime(item.get("time"), "%H:%M:%S").time()
        except (ValueError, TypeError):
            formatted_time = None

        # Agar eski yozuvdan keyingi bo‘lmasa → o‘tkazib yuboramiz
        if last_record and (formatted_date_naive < last_record.Date or
                            (formatted_date_naive == last_record.Date and formatted_time <= last_record.Time)):
            continue

        # Yangi yozuv
        new_record = Catalog(
            Date=formatted_date_naive,
            Time=formatted_time,
            Latitude=float(item.get('latitude')),
            Longitude=float(item.get('longitude')),
            Depth=float(item.get('depth')),
            Mb=float(item.get('magnitude')),
            Epicenter=item.get('epicenter')
        )
        rows_to_create.append(new_record)

    Catalog.objects.bulk_create(rows_to_create)
    return len(rows_to_create)

def upload_catalog(request):
    """
    API'dan yangi ma'lumotlarni olib DB ga yozadi.
    """
    params = {"sort": "datetime_desc", "per_page": 50, "page": 1}
    data = fetch_data_from_api(params)
    new_count = save_data_to_db(data)

    return JsonResponse({
        "status": "success",
        "new_records": new_count
    })







