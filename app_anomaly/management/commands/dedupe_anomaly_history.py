"""
Sayt tekshiruvida topilgan "Anomaliya tarixida takroriy yozuvlar" muammosi
uchun bir martalik tozalash buyrug'i.

Sabab (endi api_views.py'da tuzatilgan): ilgari `update_or_create` lookup
kaliti ichida anomaly_start_date/anomaly_end_date ham bor edi. Yangi kun
ma'lumoti kelishi bilan aniqlangan anomaliya oynasi biroz siljib qolsa,
"bir xil" tahlil qayta bosilganda eski yozuv topilmay, deyarli bir xil
YANGI qator qo'shilardi. Bu buyruq shu tarzda to'plangan eski takrorlarni
(skvajina+parametr+davr+davomiylik+recent_days_filter bo'yicha bir xil
guruhdagi, faqat ENG SO'NGGISINI qoldirib) tozalaydi.

Standart holatda faqat KO'RSATADI (hech narsani o'chirmaydi).
Ishlatish:
    python manage.py dedupe_anomaly_history            # faqat ko'rish
    python manage.py dedupe_anomaly_history --apply     # eskilarini o'chirish
"""

from collections import defaultdict

from django.core.management.base import BaseCommand

from app_anomaly.models import AnomalyRecord


class Command(BaseCommand):
    help = "Anomaliya tarixidagi takroriy (bir xil sozlamali) yozuvlarni topadi/tozalaydi."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Eng so'nggisidan boshqasini o'chiradi.")

    def handle(self, *args, **options):
        groups = defaultdict(list)
        for rec in AnomalyRecord.objects.all().order_by("-created_at"):
            key = (rec.skvajina, rec.parameter, rec.time_period_months,
                   rec.anomaly_duration_days, rec.recent_days_filter)
            groups[key].append(rec)

        total_extra = 0
        for key, records in groups.items():
            if len(records) <= 1:
                continue
            keep, extras = records[0], records[1:]
            total_extra += len(extras)
            self.stdout.write(
                f"\n{key[0]} | {key[1]} | {key[2]} oy | {key[3]} kun | recent={key[4]}:\n"
                f"  SAQLANADI: id={keep.id} ({keep.created_at}) "
                f"oraliq={keep.anomaly_start_date}—{keep.anomaly_end_date}"
            )
            for ex in extras:
                self.stdout.write(
                    f"  {'OʻCHIRILADI' if options['apply'] else 'ESKI'}: id={ex.id} ({ex.created_at}) "
                    f"oraliq={ex.anomaly_start_date}—{ex.anomaly_end_date}"
                )
                if options["apply"]:
                    ex.delete()

        if total_extra == 0:
            self.stdout.write(self.style.SUCCESS("Takroriy yozuv topilmadi."))
        elif options["apply"]:
            self.stdout.write(self.style.SUCCESS(f"\n{total_extra} ta eski takror o'chirildi."))
        else:
            self.stdout.write(self.style.NOTICE(
                f"\nJami {total_extra} ta eski takror. O'chirish uchun: "
                f"python manage.py dedupe_anomaly_history --apply"
            ))
