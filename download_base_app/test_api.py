# test_api.py
from decouple import config
import requests

# Token
resp = requests.post(
    config("LOGIN_MAG"),
    json={"username": config("USER_NAME_MAG"), "password": config("PASS_WORD_MAG")},
    timeout=10,
)
print("LOGIN STATUS:", resp.status_code)
print("LOGIN BODY:", resp.text[:500])# test_api.py
from decouple import config
import requests

# Token
resp = requests.post(
    config("LOGIN_MAG"),
    json={"username": config("USER_NAME_MAG"), "password": config("PASS_WORD_MAG")},
    timeout=10,
)
print("LOGIN STATUS:", resp.status_code)
print("LOGIN BODY:", resp.text[:500])

token = resp.json().get("result", {}).get("token")
print("TOKEN:", token)

# Data
resp2 = requests.get(
    "https://api.geofizik.uz/api/magnitude-values",
    params={"date_start": "2026-06-13", "date_end": "2026-06-13", "page": 1},
    headers={"Authorization": f"Bearer {token}"},
    timeout=30,
)
print("DATA STATUS:", resp2.status_code)
print("DATA BODY:", resp2.text[:1000])

token = resp.json().get("result", {}).get("token")
print("TOKEN:", token)

# Data
resp2 = requests.get(
    "https://api.geofizik.uz/api/magnitude-values",
    params={"date_start": "2026-06-13", "date_end": "2026-06-13", "page": 1},
    headers={"Authorization": f"Bearer {token}"},
    timeout=30,
)
print("DATA STATUS:", resp2.status_code)
print("DATA BODY:", resp2.text[:1000])