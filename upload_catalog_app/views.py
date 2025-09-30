import requests
import json
import datetime

from django.contrib import messages
from django.shortcuts import render,redirect,reverse
from django.db.models import Min, Max

from .models import Catalog

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

    # Oxirgi yozuvni olib kelamiz
    last_record = Catalog.objects.order_by("-Event_date", "-Event_time").first()
    rows_to_create = []

    for item in data_list:
        # Date
        try:
            formatted_date_naive = datetime.datetime.strptime(item.get("date"), "%d.%m.%Y").date()
        except (ValueError, TypeError):
            continue

        # Time
        try:
            formatted_time = datetime.datetime.strptime(item.get("time"), "%H:%M:%S").time()
        except (ValueError, TypeError):
            formatted_time = None

        # Oxirgi yozuvdan keyingi bo‘lmasa → o‘tkazib yuboramiz
        if last_record and (
            formatted_date_naive < last_record.Event_date or
            (formatted_date_naive == last_record.Event_date and formatted_time <= last_record.Event_time)
        ):
            continue

        # Yangi yozuv
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
        rows_to_create.sort(key=lambda r:(r.Event_date,r.Event_time))

    Catalog.objects.bulk_create(rows_to_create)
    return len(rows_to_create)

def catalog_list(request):
    """
    Catalog jadvalining oxirgi 10 ta yozuvini ko‘rsatadi.
    """
    all_records = Catalog.objects.all().order_by("-Event_date", "-Event_time")
    records = all_records[:20]

    date_range = Catalog.objects.aggregate(
        start_date=Min("Event_date"),
        end_date=Max("Event_date")
    )

    context = {
        "records": records,
        "start_date": date_range["start_date"],
        "end_date": date_range["end_date"],
    }
    return render(request, "upload_catalog_app/catalog_list.html", context)


def upload_catalog(request):
    """
    API'dan yangi ma'lumotlarni olib DB ga yozadi.
    """
    params = {"sort": "datetime_desc", "per_page": 50, "page": 1}
    data = fetch_data_from_api(params)
    new_count = save_data_to_db(data)

    if new_count > 0:
        messages.success(request, f"{new_count} ta yangi ma’lumot qo‘shildi.")
    else:
        messages.info(request, "Yangi ma’lumotlar yo‘q.")

    return redirect(reverse("catalog:catalog_list"))
