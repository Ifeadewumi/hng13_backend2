# utils.py
import requests
import os
from datetime import datetime, timezone

RESTCOUNTRIES_URL = "https://restcountries.com/v2/all?fields=name,capital,region,population,flag,currencies"
EXCHANGE_URL = "https://open.er-api.com/v6/latest/USD"

def fetch_countries(timeout=10):
    try:
        resp = requests.get(RESTCOUNTRIES_URL, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        raise RuntimeError(f"RESTCOUNTRIES_ERROR: {e}")

def fetch_exchange_rates(timeout=10):
    try:
        resp = requests.get(EXCHANGE_URL, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        # The API returns something like { "result":"success", "rates": {...}, ... }
        if "rates" in data:
            return data["rates"]
        # Some variants put rates under "rates" or "data"; handle defensively
        raise RuntimeError("EXCHANGE_FORMAT_ERROR")
    except Exception as e:
        raise RuntimeError(f"EXCHANGE_ERROR: {e}")

def utcnow():
    return datetime.now(timezone.utc)
