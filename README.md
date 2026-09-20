# Seysmologiya — Backend (Django REST API)

Seysmoprognostik ma'lumotlarni tahlil qilish tizimining backend qismi.
Faqat JSON API beradi — barcha sahifalar alohida repo'dagi React SPA'da
chiziladi: [`seismo_fronted`](https://github.com/Farmonbekfarxodov/seismo_fronted).

Eski versiyada (arxiv: `Seismo`) sahifalar Django shablonlari bilan server
tomonda render qilinardi. Hozirgi versiyada shablonlar olib tashlangan.

## Texnologiyalar

Django 5.2 · Django REST Framework · SimpleJWT · MySQL · Redis (kesh) ·
pandas / numpy / scipy · geopandas + shapely (shapefile → GeoJSON) · gunicorn

## Ishga tushirish (development)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # va qiymatlarni to'ldiring
python manage.py migrate
python manage.py runserver
```

MySQL va Redis ishlab turishi kerak. Frontend alohida ko'tariladi
(`npm run dev`, 5173-port) va API so'rovlarini shu serverga proxy qiladi.

## Production (Docker)

```bash
docker compose up -d --build
```

`docker-compose.yml` ikkita xizmatni ko'taradi: `backend` (gunicorn, 8000-port)
va `frontend` (nginx, 80-port). Frontend build konteksti `../seismo_fronted`,
ya'ni ikkala repo yonma-yon klon qilingan bo'lishi kerak:

```
<ota-papka>/
  seismo_backend/     <- shu repo
  seismo_fronted/
```

`docker-entrypoint.sh` har ishga tushishda `migrate` va `collectstatic`
bajaradi. MySQL va Redis konteynerda emas, xost mashinada turadi deb
hisoblanadi (`extra_hosts: host.docker.internal:host-gateway`).

## `.env` o'zgaruvchilari

Django:

```
SECRET_KEY, DEBUG, ALLOWED_HOSTS, CORS_ALLOWED_ORIGINS
DB_NAME, DB_USER, DB_PASSWORD, DB_HOST, DB_PORT
REDIS_URL
```

Tashqi manbalardan ma'lumot yuklash (`download_base_app`):

```
LOGIN, USER_NAME, PASS_WORD, DATA                 # geoseysmo API
LOGIN_MAG, USER_NAME_MAG, PASS_WORD_MAG, DATA_MAG # magnitka API
NEW_DB_NAME, NEW_DB_USER, NEW_DB_PASSWORD, NEW_DB_HOST, NEW_DB_PORT
```

`.env` git'ga kiritilmaydi — har bir muhitda o'z nusxasi bo'lishi kerak.

## Applar va endpointlar

Barcha API endpointlari JWT talab qiladi (`Authorization: Bearer <access>`).

### `app_users` — `/api/`
| Metod | Yo'l | Vazifasi |
| --- | --- | --- |
| POST | `/api/token/` | login, access + refresh token |
| POST | `/api/token/refresh/` | access tokenni yangilash |
| GET/POST | `/api/users/` | foydalanuvchilar ro'yxati / yaratish |

`CustomUser` (AUTH_USER_MODEL) va `LoginHistory` modellari.
Access token 5 daqiqa, refresh 1 kun.

### `seismos_app` — `/seismos/`
| Metod | Yo'l | Vazifasi |
| --- | --- | --- |
| GET | `api/options/` | quduqlar, parametrlar, koordinatalar |
| POST | `api/series/` | tanlangan quduq+parametrlar bo'yicha vaqt qatorlari va zilzilalar |
| GET | `api/layers/` | yer yoriqlari va seysmogen zonalar (GeoJSON, 24 soat keshlanadi) |
| GET | `api/well-info/?name=` | quduqning batafsil ma'lumoti + mineralizatsiya rasmi |
| POST | `api/epoch-analysis/` | zilzila atrofidagi oyna tahlili |

Modellar mavjud MySQL jadvallariga bog'langan (`managed = False`):
`skvajina`, `all_izmereniya`, `malumot1`.

### `app_anomaly` — `/anomaly/`
| Metod | Yo'l | Vazifasi |
| --- | --- | --- |
| GET | `api/options/` | tanlov variantlari |
| POST | `api/analyze/` | anomaliya tahlili (segmentlar, zilzilalar, xarita) |
| GET | `history/` | oxirgi 50 ta anomaliya yozuvi |

`AnomalyRecord` — yagona `managed` model. Bir xil tahlil qayta bosilsa
dublikat yaratilmaydi (`update_or_create`).

### `app_magnitka` — `/magnitka/`
| Metod | Yo'l | Vazifasi |
| --- | --- | --- |
| GET | `api/stations/` | stansiyalar |
| GET | `api/measurements/` | o'lchovlar (kunlik o'rtacha, Yangibozorga nisbatan delta) |
| GET | `api/earthquakes/` | zilzilalar |

Modellar: `stations`, `measurements`, `catalog` jadvallari.

### `app_informativlik` — `/informativlik/`
| Metod | Yo'l | Vazifasi |
| --- | --- | --- |
| GET | `api/options/` | tanlov variantlari |
| POST | `api/analyze/` | informativlik hisobi |
| POST | `api/export/` | natijani Excel qilib berish |

### `upload_catalog_app` — `/catalog-list/`
| Metod | Yo'l | Vazifasi |
| --- | --- | --- |
| GET | `` | zilzilalar katalogi (qidiruv bilan) |
| POST | `upload-catalog/` | tashqi API'dan yuklash |
| POST | `upload-file/` | Excel fayldan yuklash |
| POST | `manual-entry/` | qo'lda kiritish |

### `download_base_app` — `/upload/`
| Metod | Yo'l | Vazifasi |
| --- | --- | --- |
| GET | `upload/stations-wells/` | stansiya va quduqlar ro'yxati |
| GET | `upload/get-stations/` | magnitka stansiyalari |
| POST | `upload/api/` | geoseysmo API'dan yuklash |
| POST | `upload/excel/` | Excel fayldan yuklash |
| POST | `upload/transfer/` | ikkinchi bazaga ko'chirish |
| POST | `upload/magnitka/` | magnitka o'lchovlarini sinxronlash |
| POST | `upload/spm/files/` | SPM fayllardan yuklash |
| POST | `upload/spm/folder/` | serverdagi papkadan yuklash |

## Papka tuzilishi

```
seismo_project/     — settings, urls, wsgi/asgi
seismos_app/        — asosiy tahlil (views.py: baza, quduq ma'lumoti, zilzilalar;
                      api_views.py: JSON endpointlar)
app_anomaly/        — anomaliya aniqlash
app_magnitka/       — magnit o'lchovlari
app_informativlik/  — informativlik hisobi
upload_catalog_app/ — zilzilalar katalogi
download_base_app/  — tashqi manbalardan ma'lumot yuklash
app_users/          — foydalanuvchilar va JWT
static/shapefiles/  — yer yoriqlari va seysmogen zonalar (api/layers/ o'qiydi)
static/images/      — logotiplar (frontend /static/images/ orqali oladi)
media/              — yuklangan fayllar (git'ga kirmaydi)
```

## Testlar

```bash
python manage.py test
```

Serializer validatsiyasi, JWT autentifikatsiyasi va endpoint xavfsizligi
bo'yicha testlar `*/tests.py` fayllarida.

## Loglar

`logs/warnings.log` — faqat WARNING va undan yuqori darajalar, 20 MB'lik
aylanma fayllar. Konsolga log yozilmaydi.
