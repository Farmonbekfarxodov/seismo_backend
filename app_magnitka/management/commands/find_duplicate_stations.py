"""
Sayt tekshiruvi hisobotida (2026-09-29) topilgan stansiya takrorlarini
(MAN/MNG, TVKS/TVQS, Sho'rchi/SHRCH) tekshirish uchun FAQAT O'QUVCHI buyruq.

Bu buyruq hech narsani O'ZGARTIRMAYDI — faqat har bir gumon qilingan
juftlik uchun bazadagi haqiqiy yozuvlarni (id, nomi, kodi, faolligi,
o'lchov soni, birinchi/oxirgi sana) chiqarib beradi. Qaysi yozuvni asosiy
deb qoldirish, qaysi birini o'chirish/deaktivatsiya qilish — bu ma'lumot
egasining qarori, avtomatik bajarilmaydi (ikkita "duplikat" stansiyaning
o'lchovlari odatda BOSHQA-BOSHQA bo'ladi, shuning uchun birini shunchaki
o'chirish haqiqiy o'lchov tarixini yo'qotib qo'yishi mumkin).

Ishlatish:
    python manage.py find_duplicate_stations
"""

from django.core.management.base import BaseCommand
from django.db.models import Q

from app_magnitka.models import Station, Measurement

# Sayt tekshiruvida aniq ko'rsatilgan gumon qilingan juftliklar.
# Har bir element — bir xil FIZIK stansiyaga tegishli bo'lishi mumkin
# bo'lgan nom/kod belgilari ro'yxati.
SUSPECTED_GROUPS = [
    ["MAN", "MNG"],
    ["TVKS", "TVQS"],
    ["Sho'rchi", "SHRCH", "Shorchi", "SHR"],
]


class Command(BaseCommand):
    help = (
        "Magnitka stansiyalar ro'yxatidagi gumon qilingan takrorlarni "
        "(MAN/MNG, TVKS/TVQS, Sho'rchi/SHRCH) hech narsani o'zgartirmasdan "
        "ko'rsatadi."
    )

    def handle(self, *args, **options):
        any_found = False

        for group in SUSPECTED_GROUPS:
            q = Q()
            for label in group:
                q |= Q(code__iexact=label) | Q(name__iexact=label) | Q(code__icontains=label) | Q(name__icontains=label)

            stations = Station.objects.filter(q).order_by("id")
            if stations.count() < 2:
                self.stdout.write(self.style.WARNING(
                    f"\n[{' / '.join(group)}] — 2 tadan kam yozuv topildi ({stations.count()}), o'tkazib yuborildi."
                ))
                continue

            any_found = True
            self.stdout.write(self.style.MIGRATE_HEADING(f"\n[{' / '.join(group)}] — {stations.count()} ta yozuv:"))
            for st in stations:
                agg = Measurement.objects.filter(station=st).order_by("measured_at")
                count = agg.count()
                first = agg.first()
                last = agg.last()
                self.stdout.write(
                    f"  id={st.id:<5} nomi={st.name!r:<20} kodi={st.code!r:<10} "
                    f"faol={st.is_active!s:<5} lat={st.latitude} lon={st.longitude}\n"
                    f"           o'lchovlar soni={count:<8} "
                    f"birinchi={first.measured_at if first else '-'} "
                    f"oxirgi={last.measured_at if last else '-'}"
                )

        if not any_found:
            self.stdout.write(self.style.SUCCESS("\nHech qanday gumon qilingan takror topilmadi."))
            return

        self.stdout.write(self.style.NOTICE(
            "\n\nBu buyruq hech narsani o'zgartirmadi. Yuqoridagi ro'yxatdan har bir "
            "juftlik/guruh uchun qaysi id'ni ASOSIY (faol) deb qoldirish kerakligini "
            "tanlang — boshqasini shunchaki o'chirib bo'lmaydi, chunki uning "
            "o'lchovlari (measurements) odatda boshqacha sana oralig'iga tegishli "
            "bo'ladi va o'chirilsa o'sha tarix butunlay yo'qoladi. To'g'ri yechim — "
            "ikkinchi darajali yozuvni is_active=False qilish (ro'yxatda "
            "ko'rinmay qoladi, lekin tarixi saqlanib qoladi) yoki uning "
            "o'lchovlarini asosiy stansiyaga ko'chirish. Buni qaysi id bilan "
            "qilishni aytsangiz, keyingi qadamda shu o'zgarishni tayyorlab beraman."
        ))
