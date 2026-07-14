"""
SPM_fayldan_serverga_yuklash.py desktop skriptining server (web) versiyasi.

Desktop skriptdagi mantiq AYNAN saqlangan:
  1. `alldata` jadvalida yetishmayotgan sanalar bugungacha to'ldiriladi
  2. Fayl nomidan skvajina nomi ajratiladi:
     "Gidrogeoseysmologiya-" prefiksi olib tashlanadi, oxirgi _timestamp
     kesiladi, '_' -> bo'sh joy, "'" -> "ʻ"
  3. Excel o'qiladi, sana bo'lmagan ustunlardagi bo'sh qiymatlar 0 bilan
     to'ldiriladi, birinchi qator ustun nomlari sifatida olinadi
  4. Butunlay 0 dan iborat ustunlar tashlanadi
  5. Har parametr uchun `all_izmereniya`dan ssdi_id topiladi; `alldata`da
     bunday ustun bo'lmasa ALTER TABLE bilan qo'shiladi; ustunning oxirgi
     to'ldirilgan sanasidan keyingi, 0 bo'lmagan qiymatlargina UPDATE qilinadi
  6. all_izmereniya'da topilmagan skvajina+parametr — ogohlantirish bilan
     o'tkazib yuboriladi

Yozish MANZILI: geoseysmo bazasi (.env dagi NEW_DB_* — 3-bo'lim bilan bir xil).
"""

import datetime as dt
import logging
import os

import pandas as pd
import pandas.api.types as ptypes

logger = logging.getLogger(__name__)


def extract_skvajina_name(filename: str) -> str:
    """Fayl nomidan skvajina nomini ajratadi (desktop skript bilan bir xil)."""
    base = os.path.basename(filename)
    name = base.replace("Gidrogeoseysmologiya-", "")
    name = name.rsplit(".", 1)[0]          # .xlsx kengaytmasini olib tashlash
    name = name.rsplit("_", 1)[0]          # oxirgi _timestamp ni kesish
    name = name.replace("_", " ")
    name = name.replace("'", "ʻ")
    return name


def fill_missing_dates(cursor, conn) -> int:
    """alldata'da oxirgi sanadan bugungacha yetishmayotgan sanalarni qo'shadi."""
    cursor.execute("SELECT date FROM alldata ORDER BY date DESC LIMIT 1")
    row = cursor.fetchone()
    if not row or not row[0]:
        return 0

    curr = row[0]
    if isinstance(curr, dt.date) and not isinstance(curr, dt.datetime):
        curr = dt.datetime.combine(curr, dt.time.min)
    curr += dt.timedelta(days=1)

    added = 0
    now = dt.datetime.now()
    while curr <= now:
        try:
            cursor.execute("INSERT INTO alldata (date) VALUES (%s)", (curr,))
            added += 1
        except Exception as ex:
            logger.warning(f"Sana qo'shishda xato {curr}: {ex}")
        curr += dt.timedelta(days=1)
    conn.commit()
    return added


def process_spm_file(file_obj, filename: str, cursor, conn, db_name: str) -> dict:
    """Bitta Gidrogeoseysmologiya faylini qayta ishlaydi.

    file_obj — ochiq fayl obyekti (yuklangan fayl yoki diskdagi yo'l).
    Natija: {file, skvajina, params: [...], warnings: [...]}
    """
    skvajina_name = extract_skvajina_name(filename)
    report = {"file": os.path.basename(filename), "skvajina": skvajina_name,
              "params": [], "warnings": []}

    df = pd.read_excel(file_obj)

    # Sana ustuniga tegmasdan bo'sh qiymatlarni 0 bilan to'ldirish
    for col in df.columns:
        if not ptypes.is_datetime64_any_dtype(df[col]):
            df[col] = df[col].fillna(0)

    # Birinchi qator — ustun nomlari (desktop skript bilan bir xil tartib)
    name = df.iloc[0].to_list()[2:]
    name.insert(0, "T/r")
    name.insert(1, "Sana")
    df.columns = name
    df.drop(0, inplace=True)
    df = df.set_index("T/r")
    df["Sana"] = pd.to_datetime(df["Sana"], format="%d.%m.%Y", errors="coerce")

    # Butunlay 0 dan iborat ustunlarni tashlash
    columns_to_drop = [c for c in df.columns if c != "Sana" and df[c].eq(0).all()]
    df = df.drop(columns=columns_to_drop)

    for column in df.columns:
        if column == "Sana":
            continue

        df_col = df.loc[1:, ["Sana", column]] if 1 in df.index else df[["Sana", column]]

        # ssdi_id ni topish
        cursor.execute(
            "SELECT ssdi_id FROM all_izmereniya WHERE skvajina=%s AND izmereniya=%s",
            (skvajina_name, column),
        )
        row = cursor.fetchone()
        if row is None:
            report["warnings"].append(
                f"all_izmereniya jadvalida '{skvajina_name}' skvajina va "
                f"'{column}' parametr topilmadi — o'tkazib yuborildi"
            )
            continue
        ssdi_id = row[0]

        # alldata'da ustun mavjudligini tekshirish, bo'lmasa qo'shish
        cursor.execute(
            "SELECT COUNT(*) FROM information_schema.columns "
            "WHERE table_name = 'alldata' AND table_schema = %s AND column_name = %s",
            (db_name, ssdi_id),
        )
        if cursor.fetchone()[0] == 0:
            cursor.execute(f"ALTER TABLE alldata ADD COLUMN `{ssdi_id}` FLOAT;")
            report["warnings"].append(f"'{ssdi_id}' ustuni alldata jadvaliga qo'shildi")
        conn.commit()

        # Ustunning oxirgi to'ldirilgan sanasi
        cursor.execute(
            f"SELECT date, `{ssdi_id}` FROM alldata "
            f"WHERE `{ssdi_id}` IS NOT NULL ORDER BY date DESC LIMIT 1"
        )
        info = cursor.fetchone()

        if info:
            df1 = df_col[df_col["Sana"] >= pd.Timestamp(info[0])]
        else:
            df1 = df_col
        df1 = df1[df1[column] != 0]
        df1_sort = df1.sort_values(by="Sana", ascending=True)

        updated = 0
        for i in range(len(df1_sort)):
            sana = df1_sort.iloc[i, 0]
            if pd.isna(sana):
                continue
            date_value = sana.strftime("%Y-%m-%d %H:%M:%S")
            value = df1_sort.iloc[i, 1]
            if value == 0:
                continue
            try:
                cursor.execute(
                    f"UPDATE alldata SET `{ssdi_id}` = %s WHERE date = %s",
                    (float(value), date_value),
                )
                conn.commit()
                updated += 1
            except Exception as ex:
                logger.warning(f"UPDATE xatosi {ssdi_id} {date_value}: {ex}")

        report["params"].append({
            "name": str(column),
            "ssdi_id": str(ssdi_id),
            "updated": updated,
        })

    return report
