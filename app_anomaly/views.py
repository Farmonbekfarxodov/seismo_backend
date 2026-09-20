# app_anomaly/views.py

import logging
import pandas as pd
from datetime import datetime
from dateutil.relativedelta import relativedelta
from django.core.cache import cache
from sqlalchemy import create_engine, text
from decouple import config as env_config
import re

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status

from .models import AnomalyRecord
from .serializers import AnomalyRecordSerializer

logger = logging.getLogger(__name__)

DEFAULT_ELEMENTS_GROUPS = {
    "gazli": ["He", "H2", "O2", "N2", "CH4", "CO2"],
    "kimyoviy": ["F", "C2H6", "pH", "Eh", "HCO3", "Cl2"],
    "fizikaviy": ["T0", "Q", "P", "EOCC"],
}


def get_all_parameters():
    all_params = []
    for group_name, params_list in DEFAULT_ELEMENTS_GROUPS.items():
        all_params.extend(params_list)
    return sorted(list(set(all_params)))


def get_db_config():
    return {
        'db': env_config('DB_NAME'),
        'user': env_config('DB_USER'),
        'psw': env_config('DB_PASSWORD'),
        'ip': env_config('DB_HOST', default='localhost')
    }


def get_parameter_data_for_period(ssdi_id, time_period_months):
    try:
        cache_key = f"param_{ssdi_id}_{time_period_months}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        # Validate ssdi_id - bu column name yoki ID bo'lishi mumkin
        # String yoki integer bo'lishi kerak
        if ssdi_id is None:
            logger.warning(f"Invalid ssdi_id: None")
            return pd.DataFrame()

        # Integer bo'lsa, string ga o'tkazamiz
        ssdi_id_str = str(ssdi_id)

        # Faqat alphanumeric va _ belgisi ruxsat
        if not re.match(r'^[A-Za-z0-9_]+$', ssdi_id_str):
            logger.warning(f"Invalid ssdi_id provided: {ssdi_id}")
            return pd.DataFrame()

        config = get_db_config()
        engine = create_engine(
            f"mysql+mysqlconnector://{config['user']}:{config['psw']}@{config['ip']}/{config['db']}"
        )
        conn = engine.connect()

        today = datetime.now().date()
        start_date = today - relativedelta(months=int(time_period_months))

        query = text(f"""
            SELECT date, `{ssdi_id_str}` 
            FROM alldata 
            WHERE `{ssdi_id_str}` IS NOT NULL 
            AND `{ssdi_id_str}` != 0
            AND date >= '{start_date}'
            AND date <= '{today}'
            ORDER BY date ASC
        """)

        try:
            data = conn.execute(query).fetchall()
        except Exception as e:
            logger.error(f"Query error for ssdi_id={ssdi_id_str}: {e}")
            conn.close()
            engine.dispose()
            return pd.DataFrame()

        conn.close()
        engine.dispose()

        if not data:
            cache.set(cache_key, pd.DataFrame(), 3600)
            return pd.DataFrame()

        df = pd.DataFrame(data, columns=['date', 'value'])
        df['date'] = pd.to_datetime(df['date'], errors='coerce')
        df = df.sort_values('date')

        cache.set(cache_key, df, 3600)
        return df

    except Exception as e:
        logger.error(f"Ma'lumot yuklashda xato: {e}")
        return pd.DataFrame()


def detect_anomalies_in_data(df, sigma=2.0, min_duration_days=1):
    """
    ✅ KETMA-KET ANOMAL QIYMATLARNI ANIQLASH

    min_duration_days - Bu aslida MINIMAL KETMA-KET ANOMAL QIYMATLAR SONI
    (calendar days emas, balki dataframe-dagi ketma-ket anomal qiymatlar)

    Misol: Agar min_duration_days=3 bo'lsa, kamida 3 ta ketma-ket anomal
    qiymat topilishi kerak.
    """
    if df.empty:
        return []

    try:
        # ✅ NaN va NaT qiymatlarni olib tashlaymiz
        df = df.dropna(subset=['date', 'value']).copy()
        df = df.sort_values('date').reset_index(drop=True)

        if df.empty or len(df) < 5:
            logger.warning(f"Ma'lumot juda kam: {len(df)} ta qator")
            return []

        values = df['value']
        mean = values.mean()
        std = values.std()

        upper_bound = mean + sigma * std
        lower_bound = mean - sigma * std

        logger.info(f"=== DEBUG ANOMALIYA ===")
        logger.info(f"Jami qatorlar: {len(df)}")
        logger.info(f"Mean: {mean:.3f}, Std: {std:.3f}")
        logger.info(f"UB: {upper_bound:.3f}, LB: {lower_bound:.3f}")
        logger.info(f"Minimal ketma-ket qiymatlar: {min_duration_days} ta")

        anomalies = []
        current_anomaly = None

        for idx, row in df.iterrows():
            value = row['value']
            date = row['date']

            # ✅ Qo'shimcha xavfsizlik tekshiruvi
            if pd.isna(value) or pd.isna(date):
                continue

            is_anomaly = (value > upper_bound) or (value < lower_bound)

            if is_anomaly:
                # ✅ Anomaliya qiymati topildi
                if current_anomaly is None:
                    current_anomaly = {
                        'start_date': date,
                        'end_date': date,
                        'values': [value],
                        'dates': [date],
                        'count': 1  # ✅ QIYMATLAR SONI
                    }
                else:
                    current_anomaly['end_date'] = date
                    current_anomaly['values'].append(value)
                    current_anomaly['dates'].append(date)
                    current_anomaly['count'] += 1  # ✅ +1
            else:
                # ✅ Anomaliya tugadi
                if current_anomaly is not None:
                    anomaly_count = current_anomaly['count']  # ✅ QIYMATLAR SONI

                    # ✅ Xavfsiz date formatlash
                    start_str = current_anomaly['start_date'].strftime('%Y-%m-%d') if pd.notna(current_anomaly['start_date']) else 'N/A'
                    end_str = current_anomaly['end_date'].strftime('%Y-%m-%d') if pd.notna(current_anomaly['end_date']) else 'N/A'

                    logger.info(f"Anomaliya: {start_str} ~ {end_str} = {anomaly_count} ta qiymat")

                    # ✅ QIYMATLAR SONIGA QA'RA TEKSHIRISH
                    if anomaly_count >= min_duration_days:
                        anomalies.append(current_anomaly)
                        logger.info(f"✅ Saqlandi: {anomaly_count} ta >= {min_duration_days} ta")
                    else:
                        logger.info(f"❌ Qisqa: {anomaly_count} ta < {min_duration_days} ta")

                    current_anomaly = None

        # ✅ Oxirgi anomaliya
        if current_anomaly is not None:
            anomaly_count = current_anomaly['count']

            # ✅ Xavfsiz date formatlash
            start_str = current_anomaly['start_date'].strftime('%Y-%m-%d') if pd.notna(current_anomaly['start_date']) else 'N/A'
            end_str = current_anomaly['end_date'].strftime('%Y-%m-%d') if pd.notna(current_anomaly['end_date']) else 'N/A'

            logger.info(f"Oxirgi anomaliya: {start_str} ~ {end_str} = {anomaly_count} ta qiymat")

            if anomaly_count >= min_duration_days:
                anomalies.append(current_anomaly)
                logger.info(f"✅ Saqlandi: {anomaly_count} ta >= {min_duration_days} ta")
            else:
                logger.info(f"❌ Qisqa: {anomaly_count} ta < {min_duration_days} ta")

        logger.info(f"Jami anomaliyalar: {len(anomalies)}")
        logger.info(f"======================\n")

        return anomalies

    except Exception as e:
        logger.error(f"Anomaliya aniqlashda xato: {e}")
        import traceback
        traceback.print_exc()
        return []

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def anomaly_history_view(request):
    """GET /anomaly/history/ — so'nggi 50 ta faol anomaliya yozuvi."""
    try:
        records = AnomalyRecord.objects.filter(is_active=True).order_by('-created_at')[:50]
        return Response({
            'records': AnomalyRecordSerializer(records, many=True).data,
            'total_count': AnomalyRecord.objects.filter(is_active=True).count(),
        })
    except Exception as e:
        logger.error(f"Tarix xato: {e}")
        return Response(
            {'error': 'Xatolik yuz berdi'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )