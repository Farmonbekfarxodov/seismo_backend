#!/usr/bin/env python
"""
Anomaliya aniqlash muammolarini diagnostika qilish scripti
"""

import os
import sys
import django

# Django setup
sys.path.insert(0, '/home/asus/PROJECT/Seismo')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'seismo_project.settings')
django.setup()

import pandas as pd
from app_anomaly.views import get_parameter_data_for_period, detect_anomalies_in_data
from seismos_app.views import fetch_data

def test_single_well_param():
    """Test bitta skvajina va parametr uchun anomaliya aniqlash"""

    print("=" * 60)
    print("ANOMALIYA DIAGNOSTIKA SCRIPT")
    print("=" * 60)

    # 1. Ma'lumotlarni yuklash
    print("\n1. Ma'lumotlarni yuklamoqda...")
    try:
        lst_stansiya, well_coords = fetch_data()
        print(f"✅ {len(lst_stansiya)} ta skvajina topildi")

        # Birinchi skvajinani tanlaymiz
        first_key = list(lst_stansiya.keys())[0]
        print(f"Test uchun: {first_key}")

        # Birinchi parametrni tanlaymiz
        params = lst_stansiya[first_key]
        first_param = None
        for param_name, ssdi_id in params.items():
            if ssdi_id:
                first_param = param_name
                first_ssdi = ssdi_id
                break

        if not first_param:
            print("❌ Parametr topilmadi")
            return

        print(f"Test parametr: {first_param} (ssdi_id={first_ssdi})")

    except Exception as e:
        print(f"❌ Ma'lumot yuklash xatosi: {e}")
        return

    # 2. Parametr ma'lumotlarini yuklash
    print(f"\n2. {first_param} uchun ma'lumotlarni yuklamoqda (6 oy)...")
    try:
        df = get_parameter_data_for_period(first_ssdi, 6)
        print(f"✅ {len(df)} ta qator yuklandi")

        if df.empty:
            print("❌ Ma'lumot bo'sh!")
            return

        print(f"Date range: {df['date'].min()} - {df['date'].max()}")
        print(f"Value range: {df['value'].min():.3f} - {df['value'].max():.3f}")
        print(f"NaN qiymatlar: {df['value'].isna().sum()} ta")

    except Exception as e:
        print(f"❌ Ma'lumot yuklash xatosi: {e}")
        return

    # 3. Turli sigma va min_duration bilan test
    print("\n3. Anomaliya aniqlash testi:")
    print("-" * 60)

    test_configs = [
        {'sigma': 2.0, 'min_duration': 1},
        {'sigma': 2.0, 'min_duration': 3},
        {'sigma': 2.0, 'min_duration': 5},
        {'sigma': 1.5, 'min_duration': 1},
        {'sigma': 1.5, 'min_duration': 3},
    ]

    for config in test_configs:
        sigma = config['sigma']
        min_dur = config['min_duration']

        print(f"\n📊 Sigma={sigma}, Min={min_dur} ta qiymat:")

        try:
            anomalies = detect_anomalies_in_data(df, sigma=sigma, min_duration_days=min_dur)
            print(f"   Topilgan anomaliyalar: {len(anomalies)} ta")

            if anomalies:
                for i, anom in enumerate(anomalies[:3], 1):  # Faqat birinchi 3 ta
                    start = anom['start_date'].strftime('%Y-%m-%d')
                    end = anom['end_date'].strftime('%Y-%m-%d')
                    count = anom['count']
                    print(f"   #{i}: {start} ~ {end} ({count} ta qiymat)")

                if len(anomalies) > 3:
                    print(f"   ... va yana {len(anomalies) - 3} ta")

        except Exception as e:
            print(f"   ❌ Xato: {e}")

    print("\n" + "=" * 60)
    print("DIAGNOSTIKA TUGADI")
    print("=" * 60)

if __name__ == '__main__':
    test_single_well_param()
