import logging
from datetime import datetime, timedelta
from typing import Optional, List

import pandas as pd

from django.http import JsonResponse
from django.core.cache import cache
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated

from .models import Station, Measurement, Catalog
from .serializers import (
    StationSerializer,
    MeasurementsQuerySerializer,
    EarthquakesQuerySerializer,
)

logger = logging.getLogger(__name__)

# ─── Konstantalar ────────────────────────────────────────────────────────────

COLOR_PALETTE = [
    "#2196F3", "#4CAF50", "#FF9800", "#9C27B0",
    "#F44336", "#00BCD4", "#795548", "#607D8B",
    "#E91E63", "#009688", "#FF5722", "#3F51B5",
]

MAGNITUDE_LINE_COLOR = "rgba(220, 38, 38, 0.55)"  # qizil, yarim shaffof

# ✅ Delta hisoblash uchun baza stantsiya nomi
BASE_STATION_NAME = "Yangibozor"

# Qiymat O'Z STANSIYASINING medianasidan shunchadan ko'p chetlashsa, u
# yaroqsiz (sensor yoki yozuv nosozligi) deb hisoblanadi va hisobga olinmaydi.
#
# Filtr ikki joyda kerak:
#   1) Baza (Yangibozor) — barcha stansiyalar Δ = qiymat − Yangibozor tarzida
#      chiziladi, shuning uchun bazaning bitta buzuq nuqtasi o'sha vaqtdagi
#      HAMMA grafikni buzadi. 2022-05-11 da Yangibozor kun bo'yi ~543 yozgan
#      (odatda ~53 655) — natijada barcha stansiyalarda Δ ≈ 53 000 chiqqan.
#   2) O'lchovchi stansiyaning o'zi — o'z sensori nosoz bo'lsa, faqat o'sha
#      stansiyaning grafigi buziladi. 2024-02-18 da Xumson ~32 434 yozgan
#      (odatda ~53 740), natijada Δ = −21 221 bo'lgan (odatdagi +85 o'rniga).
#
# Chegara nega 5000: haqiqiy magnit o'zgarishlar (kunlik variatsiya, magnit
# bo'ronlar) yuzlab nT bo'ladi — kuzatilgan eng katta haqiqiy chetlanish
# +3177 nT. Nosozliklar esa o'n minglab nT. 5000 nT ikkalasini ishonchli
# ajratadi: haqiqiy bo'ronlar saqlanadi, sensor nosozligi tashlanadi.
MAX_DEVIATION = 5000.0

PERIOD_OPTIONS = [
    ("7",   "1 hafta"),
    ("30",  "1 oy"),
    ("90",  "3 oy"),
    ("180", "6 oy"),
    ("365", "1 yil"),
    ("730", "2 yil"),
    ("0",   "Barchasi"),
]

# ─── Yordamchi funksiyalar ────────────────────────────────────────────────────

def get_active_stations() -> List[Station]:
    """Faol stantsiyalar ro'yxatini cache bilan qaytaradi."""
    cache_key = "magnitka_active_stations"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    stations = list(Station.objects.filter(is_active=True).order_by("name"))
    cache.set(cache_key, stations, 300)
    logger.info(f"✅ Fetched {len(stations)} active stations from DB")
    return stations


def fetch_measurements(
    station_ids: List[int],
    days: int = 365,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
) -> pd.DataFrame:
    """Berilgan stantsiyalar uchun o'lchov ma'lumotlarini qaytaradi."""
    try:
        qs = Measurement.objects.filter(
            station_id__in=station_ids,
            value__isnull=False,
        ).select_related("station").order_by("measured_at")

        if start_date and end_date:
            qs = qs.filter(measured_at__range=(start_date, end_date))
        elif days and days > 0:
            cutoff = timezone.now() - timedelta(days=days)
            qs = qs.filter(measured_at__gte=cutoff)

        values = qs.values("station_id", "station__name", "measured_at", "value")

        if not values.exists():
            logger.warning(f"⚠️ No measurements found for stations {station_ids}")
            return pd.DataFrame()

        df = pd.DataFrame(list(values))
        df.rename(columns={"station__name": "station_name"}, inplace=True)
        df["measured_at"] = pd.to_datetime(df["measured_at"])
        # ✅ Timezone-naive qilish (earthquakes_df bilan solishtirish uchun)
        if df["measured_at"].dt.tz is not None:
            df["measured_at"] = df["measured_at"].dt.tz_localize(None)
        df.sort_values("measured_at", inplace=True)
        df.reset_index(drop=True, inplace=True)

        logger.info(f"✅ Fetched {len(df)} measurements for stations {station_ids}")
        return df

    except Exception as e:
        logger.error(f"❌ fetch_measurements error: {e}", exc_info=True)
        return pd.DataFrame()


def fetch_earthquakes(
    min_mag: Optional[float] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
) -> pd.DataFrame:
    """
    `catalog` jadvalidan zilzilalarni oladi.
    min_mag berilmasa yoki None bo'lsa - barchasi qaytariladi.
    """
    try:
        qs = Catalog.objects.all().order_by("event_date", "event_time")

        if min_mag is not None:
            qs = qs.filter(mb__gte=min_mag)

        if start_date and end_date:
            qs = qs.filter(event_date__range=(start_date.date(), end_date.date()))

        values = qs.values(
            "event_date", "event_time", "mb", "epicenter", "depth",
            "latitude", "longitude",
        )

        if not values.exists():
            return pd.DataFrame()

        df = pd.DataFrame(list(values))

        # Sana + vaqtni birlashtirish
        df["event_time"] = df["event_time"].fillna(pd.Timestamp("00:00:00").time())
        df["event_datetime"] = pd.to_datetime(
            df["event_date"].astype(str) + " " + df["event_time"].astype(str),
            errors="coerce",
        )
        df.dropna(subset=["event_datetime"], inplace=True)
        df.sort_values("event_datetime", inplace=True)
        df.reset_index(drop=True, inplace=True)

        logger.info(f"✅ Fetched {len(df)} earthquakes (min_mag={min_mag})")
        return df

    except Exception as e:
        logger.error(f"❌ fetch_earthquakes error: {e}", exc_info=True)
        return pd.DataFrame()


def compute_reference_median(
    station_id: int,
    start_date: Optional[datetime],
    end_date: Optional[datetime],
    pad_days: int = 30,
    min_samples: int = 100,
) -> Optional[float]:
    """So'ralgan sana oralig'i tashqarisidan ham ma'lumot olib, ishonchliroq
    median hisoblaydi (filter_outliers'ga beriladi).

    Muammo: agar foydalanuvchi tor sana oralig'ini tanlasa va aynan o'sha
    oraliq nosozlik davriga to'g'ri kelsa, filter_outliers'ning o'zi
    hisoblaydigan lokal median ham "buzilgan" nuqtalar ta'sirida siljib
    qoladi — natijada nosoz qiymatlar "normal" ko'rinib, filtrlanmay qoladi.
    Shuning uchun so'ralgan oyna atrofiga (oldin/keyin) `pad_days` kun
    qo'shib, kengroq va shu bilan barqarorroq median hisoblanadi.

    Agar bu ham yetarli nuqta bermasa (masalan yangi stantsiya yoki
    days=0/"hammasi" rejimida start/end berilmagan bo'lsa), None qaytariladi
    — chaqiruvchi o'zining lokal medianasiga qaytadi (eski xatti-harakat).
    """
    if start_date is None or end_date is None:
        return None
    try:
        padded_start = start_date - timedelta(days=pad_days)
        padded_end = end_date + timedelta(days=pad_days)
        values = list(
            Measurement.objects.filter(
                station_id=station_id,
                value__isnull=False,
                measured_at__range=(padded_start, padded_end),
            ).values_list("value", flat=True)
        )
        if len(values) < min_samples:
            return None
        return float(pd.Series(values).median())
    except Exception as e:
        logger.warning(f"⚠️ compute_reference_median xato (station={station_id}): {e}")
        return None


def filter_outliers(
    base_df: pd.DataFrame,
    nom: str = BASE_STATION_NAME,
    max_deviation: float = MAX_DEVIATION,
    reference_median: Optional[float] = None,
) -> pd.DataFrame:
    """Stansiyaning yaroqsiz nuqtalarini olib tashlaydi.

    Qiymat medianadan `max_deviation` dan ko'p chetlashsa — bu magnit
    o'zgarish emas, sensor/yozuv nosozligi. Bunday nuqtalar hisobdan
    chiqarib tashlanadi; natijada o'sha vaqtlarda grafik bo'sh qoladi
    (soxta cho'qqi chizilmaydi).

    `reference_median` berilsa (compute_reference_median orqali, so'ralgan
    oyna atrofidagi kengroq ma'lumotdan hisoblangan), o'sha ishlatiladi —
    aks holda shu `base_df`ning o'z medianasiga qaytiladi (eski xatti-harakat,
    "hammasi" rejimida yoki yetarli qo'shimcha ma'lumot bo'lmaganda).

    Ham baza (Yangibozor), ham har bir o'lchovchi stansiya uchun ishlatiladi.
    Delta hisoblash va 10-minutlik o'rtachaga keltirishdan OLDIN chaqirilishi
    kerak — aks holda bitta oynada yaroqli va yaroqsiz qiymatlar aralashib,
    o'rtachasi "ishonarli" ko'rinib qolishi mumkin.
    """
    if base_df.empty or "value" not in base_df.columns:
        return base_df

    median = reference_median if reference_median is not None else base_df["value"].median()
    yaroqli = (base_df["value"] - median).abs() <= max_deviation
    tashlandi = int((~yaroqli).sum())

    if tashlandi:
        sanalar = sorted(
            set(pd.to_datetime(base_df.loc[~yaroqli, "measured_at"]).dt.date.astype(str))
        )
        logger.warning(
            f"⚠️ {nom}: {tashlandi} ta yaroqsiz nuqta tashlandi "
            f"(median={median:.1f}, chegara=±{max_deviation:.0f}). "
            f"Sanalar: {', '.join(sanalar)}"
        )

    return base_df.loc[yaroqli].reset_index(drop=True)


def aggregate_base_to_10min(base_df: pd.DataFrame) -> pd.DataFrame:
    """
    Yangibozor (baza stantsiya) ma'lumotini 10 minutlik qadamga keltiradi.

    Yangibozor har 1 minutda o'lchov yuboradi, qolgan stantsiyalar esa
    har 10 minutda. Taqqoslash to'g'ri bo'lishi uchun Yangibozorning
    1-minutdan 10-minutgacha bo'lgan qiymatlari qo'shilib, ularning
    o'rtachasi olinadi (oynada 10 tadan kam qiymat bo'lsa — mavjudlari
    soniga bo'linadi, ya'ni oddiy o'rtacha).

    Vaqt belgisi oyna OXIRIga yoziladi: 10:01–10:10 qiymatlari -> 10:10.
    Bu boshqa stantsiyalarning :00, :10, :20 ... dagi o'lchov vaqtlariga
    aynan mos keladi (delta hisoblashda measured_at bo'yicha join qilinadi).
    """
    if base_df.empty:
        return base_df

    df = base_df.copy()
    df["measured_at"] = pd.to_datetime(df["measured_at"])

    resampled = (
        df.set_index("measured_at")["value"]
        .resample("10min", closed="right", label="right")
        .mean()
        .dropna()
        .reset_index()
    )

    # Asl ustunlarni tiklash (station_id, station_name saqlanadi)
    for col in ("station_id", "station_name"):
        if col in base_df.columns:
            resampled[col] = base_df[col].iloc[0]

    logger.info(
        f"✅ {BASE_STATION_NAME}: {len(base_df)} ta 1-minutlik -> "
        f"{len(resampled)} ta 10-minutlik nuqta"
    )
    return resampled


def compute_delta_series(
    df_station: pd.DataFrame,
    base_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Berilgan stantsiya ma'lumotlaridan baza stantsiya (Yangibozor) qiymatini ayiradi.
    Faqat measured_at vaqti aniq mos kelganda delta hisoblanadi —
    mos kelmasa o'sha qator tashlab ketiladi.
    """
    if base_df.empty:
        logger.warning("⚠️ Baza stantsiya (Yangibozor) ma'lumoti yo'q — delta hisoblanmaydi")
        return pd.DataFrame()

    base_lookup = base_df.set_index("measured_at")["value"]

    merged = df_station.copy()
    merged["base_value"] = merged["measured_at"].map(base_lookup)

    # Bazada mos vaqt topilmagan qatorlarni tashlab yuborish
    merged.dropna(subset=["base_value"], inplace=True)

    merged["value"] = merged["value"] - merged["base_value"]
    merged.drop(columns=["base_value"], inplace=True)
    merged.reset_index(drop=True, inplace=True)

    return merged


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def api_stations(request):
    """AJAX: Barcha faol stantsiyalar ro'yxatini JSON da qaytaradi."""
    stations = get_active_stations()
    data = StationSerializer(stations, many=True).data
    return JsonResponse({"stations": data})

def aggregate_to_daily(df: pd.DataFrame) -> pd.DataFrame:
    """
    Grafik uchun YAKUNIY qadam: 10 (yoki 1) minutlik nuqtalarni SUTKALIK
    o'rtachaga aylantiradi. Yangibozor 1->10 min konversiyasi va delta
    hisoblash yuqorida O'ZGARISHSIZ qoladi — bu faqat natija ustiga
    qo'shiladigan qo'shimcha bosqich.

    Bir kunda odatda 144 ta nuqta (10 minutda bitta) bo'ladi, lekin ba'zi
    kunlarda uzilish sabab kamroq (masalan 130 ta) bo'lishi mumkin.
    pandas .mean() har doim MAVJUD nuqtalar soniga bo'ladi — 144 bo'lsa
    144 ga, 130 bo'lsa 130 ga (hech qachon zo'rlab 144 ga bo'lmaydi).
    """
    if df.empty:
        return df
    d = df.copy()
    d["measured_at"] = pd.to_datetime(d["measured_at"])
    daily = (
        d.set_index("measured_at")["value"]
        .resample("1D")
        .mean()
        .dropna()
        .reset_index()
    )
    return daily

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def api_measurements(request):
    """AJAX: Berilgan stantsiyalar uchun o'lchov seriyalarini JSON da qaytaradi.

    Eski results_view bilan bir xil hisob mantiq:
      1. Yangibozor (baza) ma'lumoti avval 10-minutlik o'rtachaga keltiriladi
         (u 1-minutlik keladi, boshqalar 10-minutlik).
      2. Yangibozorning o'z seriyasi — shu 10-minutlik qiymatlar (deltasiz).
      3. Boshqa stantsiyalar — Δ = qiymat − Yangibozor (bir xil vaqtda),
         mos vaqt topilmagan nuqtalar tashlab yuboriladi.
    """
    query = MeasurementsQuerySerializer(data=request.GET)
    if not query.is_valid():
        return JsonResponse({"error": "Noto'g'ri parametrlar", "details": query.errors}, status=400)
    station_ids = query.validated_data["station_ids"]
    start_raw = query.validated_data.get("start_date")
    end_raw = query.validated_data.get("end_date")

    # Sana oralig'ini tayyorlash: faqat bittasi berilsa ham ishlaydi;
    # ikkalasi bo'sh bo'lsa — days=0, ya'ni BARCHA ma'lumot
    start_date = end_date = None
    if start_raw or end_raw:
        start_date = (
            datetime.combine(start_raw, datetime.min.time())
            if start_raw else datetime(1900, 1, 1)
        )
        end_date = (
            datetime.combine(end_raw, datetime.max.time().replace(microsecond=0))
            if end_raw else datetime.now()
        )

    df = fetch_measurements(
        station_ids=station_ids, days=0,
        start_date=start_date, end_date=end_date,
    )

    if df.empty:
        return JsonResponse({"data": [], "base_station": BASE_STATION_NAME})

    # ── Baza stantsiya (Yangibozor)ni topish va 10-minutlikka keltirish ──
    base_station_obj = Station.objects.filter(name=BASE_STATION_NAME).first()
    base_df = pd.DataFrame()
    if base_station_obj:
        if base_station_obj.id in station_ids:
            base_df = df[df["station_id"] == base_station_obj.id].copy()
        else:
            base_df = fetch_measurements(
                station_ids=[base_station_obj.id], days=0,
                start_date=start_date, end_date=end_date,
            )
        if not base_df.empty:
            # Avval yaroqsiz nuqtalar tashlanadi, keyin 10-minutlikka keltiriladi.
            # Median so'ralgan oyna atrofidagi kengroq ma'lumotdan hisoblanadi
            # (tor oyna nosozlik davriga to'g'ri kelib qolsa ham ishonchli bo'lsin).
            ref_median = compute_reference_median(base_station_obj.id, start_date, end_date)
            base_df = filter_outliers(base_df, BASE_STATION_NAME, reference_median=ref_median)
        if not base_df.empty:
            base_df = aggregate_base_to_10min(base_df)

    result = []
    for sid in station_ids:
        sub = df[df["station_id"] == sid].copy()
        if sub.empty:
            continue
        station_name = sub["station_name"].iloc[0]
        is_base = (station_name == BASE_STATION_NAME)

        ref_median = compute_reference_median(sid, start_date, end_date)

        if is_base:
            series_df = (
                base_df if not base_df.empty
                else aggregate_base_to_10min(filter_outliers(sub, station_name, reference_median=ref_median))
            )
            is_delta = False
        else:
            # Stansiyaning O'Z nosoz nuqtalari ham tashlanadi — aks holda
            # uning sensori buzilgan kunda Δ soxta cho'qqi berib yuboradi.
            sub = filter_outliers(sub, station_name, reference_median=ref_median)
            if sub.empty:
                result.append({
                    "station_id": sid,
                    "station_name": station_name,
                    "dates": [], "values": [],
                    "is_delta": not base_df.empty, "no_match": True,
                })
                continue

            if base_df.empty:
                series_df = sub
                is_delta = False
            else:
                series_df = compute_delta_series(sub, base_df)
                is_delta = True
                if series_df.empty:
                    # Yangibozor bilan mos vaqt topilmadi — seriyani belgilab qaytaramiz
                    result.append({
                        "station_id": sid,
                        "station_name": station_name,
                        "dates": [], "values": [],
                        "is_delta": True, "no_match": True,
                    })
                    continue
        series_df = aggregate_to_daily(series_df)
        result.append({
            "station_id":   sid,
            "station_name": station_name,
            "dates":        pd.to_datetime(series_df["measured_at"]).dt.strftime("%Y-%m-%dT%H:%M:%S").tolist(),
            "values":       [round(float(v), 6) for v in series_df["value"]],
            "is_delta":     is_delta,
            "is_base":      is_base,
        })

    return JsonResponse({"data": result, "base_station": BASE_STATION_NAME})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def api_earthquakes(request):
    """AJAX: Zilzilalar katalogini JSON da qaytaradi (min_magnitude bilan filtrlash mumkin)."""
    query = EarthquakesQuerySerializer(data=request.GET)
    min_mag = (
        query.validated_data.get("min_magnitude") if query.is_valid() else None
    )

    df = fetch_earthquakes(min_mag=min_mag)

    if df.empty:
        return JsonResponse({"data": []})

    result = df.apply(lambda r: {
        "datetime":  r["event_datetime"].strftime("%Y-%m-%dT%H:%M:%S"),
        "magnitude": r["mb"],
        "epicenter": r["epicenter"],
        "depth":     r["depth"],
        # Stansiyadan masofani hisoblash uchun (frontendda M/lgR filtri kerak) —
        # ilgari yo'q edi, shu sabab Magnitka'da HAR bir zilzila HAMMA
        # stansiya grafigida bir xil (masofadan qat'i nazar) chizilar edi.
        "lat":       r["latitude"],
        "lon":       r["longitude"],
    }, axis=1).tolist()

    return JsonResponse({"data": result})