#!/bin/sh
set -e

echo "→ Migratsiyalar qo'llanmoqda..."
python manage.py migrate --noinput || echo "⚠ Migratsiya o'tkazib yuborildi"

echo "→ Statik fayllar to'planmoqda..."
python manage.py collectstatic --noinput || true

echo "→ Server ishga tushmoqda..."
exec "$@"
