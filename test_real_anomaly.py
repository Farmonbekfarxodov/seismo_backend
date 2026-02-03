#!/usr/bin/env python
"""
Real anomaliya topish va grafik yaratish testi
"""

import os
import sys
import django

sys.path.insert(0, '/home/asus/PROJECT/Seismo')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'seismo_project.settings')
django.setup()

from app_anomaly.views import (
    get_parameter_data_for_period,
    detect_anomalies_in_data,
    create_anomaly_chart
)
from seismos_app.views import fetch_data

def test_graph_creation():
    """Grafik yaratilishini test qilish"""

    print("=" * 70)
    print("GRAFIK YARATISH TESTI")
    print("=" * 70)

    # Ma'lumotlarni yuklash
    lst_stansiya, well_coords = fetch_data()

    # Birinchi skvajina va parametrni olish
    first_key = list(lst_stansiya.keys())[0]
    params = lst_stansiya[first_key]

    first_param = None
    first_ssdi = None
    for param_name, ssdi_id in params.items():
        if ssdi_id:
            first_param = param_name
            first_ssdi = ssdi_id
            break

    if not first_param:
        print("❌ Parametr topilmadi")
        return

    well_name = first_key.split(' | ')[1] if ' | ' in first_key else first_key

    print(f"\nTest parametrlari:")
    print(f"  Skvajina: {first_key}")
    print(f"  Parametr: {first_param}")
    print(f"  SSDI ID: {first_ssdi}")

    # Ma'lumotlarni yuklash
    df = get_parameter_data_for_period(first_ssdi, 6)

    if df.empty:
        print("❌ Ma'lumot bo'sh")
        return

    print(f"\n✅ {len(df)} ta qator yuklandi")

    # Anomaliya aniqlash (minimal=1)
    anomalies = detect_anomalies_in_data(df, sigma=2.0, min_duration_days=1)

    print(f"\n✅ {len(anomalies)} ta anomaliya topildi")

    if not anomalies:
        print("\n⚠️ Anomaliya topilmadi - sigma ni kamaytiramiz...")
        anomalies = detect_anomalies_in_data(df, sigma=1.5, min_duration_days=1)
        print(f"✅ Sigma=1.5 da {len(anomalies)} ta anomaliya topildi")

    if not anomalies:
        print("❌ Hali ham anomaliya yo'q")
        return

    # Grafik yaratish
    print("\n📊 Grafik yaratilmoqda...")

    well_lat, well_lon = well_coords.get(well_name, (None, None))

    graph_html = create_anomaly_chart(
        df=df,
        anomalies=anomalies,
        well_name=first_key,
        param_name=first_param,
        sigma=2.0,
        earthquakes_df=None,  # Zilzilasiz
        well_lat=well_lat,
        well_lon=well_lon
    )

    if graph_html:
        print("✅ Grafik muvaffaqiyatli yaratildi!")
        print(f"   HTML uzunligi: {len(graph_html)} bayt")

        # HTML faylga saqlash
        output_file = '/tmp/test_graph.html'
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Test Anomaliya Grafik</title>
</head>
<body>
    <h1>Test Grafik: {first_key} - {first_param}</h1>
    {graph_html}
</body>
</html>
            """)

        print(f"   📁 Grafik saqlandi: {output_file}")
        print(f"   🌐 Brauzerda ochish: file://{output_file}")
    else:
        print("❌ Grafik yaratilmadi!")

    print("\n" + "=" * 70)
    print("TEST TUGADI")
    print("=" * 70)

if __name__ == '__main__':
    test_graph_creation()
