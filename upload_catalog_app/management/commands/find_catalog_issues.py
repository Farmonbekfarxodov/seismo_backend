"""
Sayt tekshiruvi hisobotida (2026-09-29) topilgan Katalog muammolarini
tekshirish uchun buyruq:
  1. Yaqin-dublikat hodisalar (bir necha soniya farqli, ehtimol bitta
     zilzila ikki marta yozilgan) — masalan 2025-10-05 Mb5.4/Mb5.7.
  2. Bir so'z ichida kirill-lotin aralashgan epitsentr nomlari —
     masalan "Аfg'oniston" (birinchi harf kirillcha).

Standart holatda HECH NARSANI o'zgartirmaydi — faqat topilganlarni chop
etadi. Epitsentr nomlarini --apply-epicenter-fix bilan xavfsiz tuzatish
mumkin (matnni normallashtiradi, boshqa hech narsaga tegmaydi). Dublikat
hodisalarni esa avtomatik o'chirmaydi — qaysi yozuvni saqlab, qaysi
birini o'chirish ilmiy jihatdan muhim qaror, shuning uchun faqat
ro'yxatini chiqaradi.

Ishlatish:
    python manage.py find_catalog_issues
    python manage.py find_catalog_issues --apply-epicenter-fix
"""

import datetime

from django.core.management.base import BaseCommand

from upload_catalog_app.models import Catalog
from upload_catalog_app.views import normalize_mixed_script, NEAR_DUP_MAX_SECONDS, NEAR_DUP_MAX_DEG


class Command(BaseCommand):
    help = "Katalogdagi yaqin-dublikat hodisalar va kirill/lotin aralash epitsentr nomlarini topadi."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply-epicenter-fix", action="store_true",
            help="Topilgan kirill/lotin aralash epitsentr nomlarini bazada normallashtiradi (xavfsiz matn tuzatish).",
        )

    def handle(self, *args, **options):
        self._find_near_duplicates()
        self._find_mixed_script_epicenters(apply_fix=options["apply_epicenter_fix"])

    def _find_near_duplicates(self):
        self.stdout.write(self.style.MIGRATE_HEADING("\n== Yaqin-dublikat hodisalar =="))
        records = list(
            Catalog.objects.all().order_by("Event_date", "Event_time")
            .values("id", "Event_date", "Event_time", "Latitude", "Longitude", "Mb", "Epicenter")
        )

        by_day = {}
        for r in records:
            by_day.setdefault(r["Event_date"], []).append(r)

        found = 0
        for day, rows in by_day.items():
            for i in range(len(rows)):
                for j in range(i + 1, len(rows)):
                    a, b = rows[i], rows[j]
                    if a["Event_time"] is None or b["Event_time"] is None:
                        continue
                    delta = abs((
                        datetime.datetime.combine(day, a["Event_time"]) -
                        datetime.datetime.combine(day, b["Event_time"])
                    ).total_seconds())
                    if (
                        delta <= NEAR_DUP_MAX_SECONDS
                        and abs(a["Latitude"] - b["Latitude"]) <= NEAR_DUP_MAX_DEG
                        and abs(a["Longitude"] - b["Longitude"]) <= NEAR_DUP_MAX_DEG
                    ):
                        found += 1
                        self.stdout.write(
                            f"  {day}  id={a['id']} {a['Event_time']} Mb={a['Mb']} {a['Epicenter']!r}\n"
                            f"  {day}  id={b['id']} {b['Event_time']} Mb={b['Mb']} {b['Epicenter']!r}\n"
                            f"    -> farq={delta:.0f}s\n"
                        )

        if not found:
            self.stdout.write(self.style.SUCCESS("  Yaqin-dublikat topilmadi."))
        else:
            self.stdout.write(self.style.NOTICE(
                f"  Jami {found} ta gumon qilingan juftlik. Qaysi id'ni o'chirish kerakligini "
                f"tanlab bering — men buni sizning tasdiqingizsiz o'chirmayman."
            ))

    def _find_mixed_script_epicenters(self, apply_fix: bool):
        self.stdout.write(self.style.MIGRATE_HEADING("\n== Kirill/lotin aralash epitsentr nomlari =="))
        records = Catalog.objects.exclude(Epicenter__isnull=True).exclude(Epicenter="")

        found = 0
        for rec in records.iterator():
            fixed = normalize_mixed_script(rec.Epicenter)
            if fixed != rec.Epicenter:
                found += 1
                self.stdout.write(f"  id={rec.id}: {rec.Epicenter!r} -> {fixed!r}")
                if apply_fix:
                    rec.Epicenter = fixed
                    rec.save(update_fields=["Epicenter"])

        if not found:
            self.stdout.write(self.style.SUCCESS("  Aralash yozuv topilmadi."))
        elif apply_fix:
            self.stdout.write(self.style.SUCCESS(f"  {found} ta yozuv tuzatildi."))
        else:
            self.stdout.write(self.style.NOTICE(
                f"  Jami {found} ta yozuv. Tuzatish uchun: "
                f"python manage.py find_catalog_issues --apply-epicenter-fix"
            ))
