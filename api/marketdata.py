import os
import requests

def _get_config() -> str:
    key = os.environ.get("TWELVE_DATA_API_KEY")
    if not key:
        raise ValueError(
            "TWELVE_DATA_API_KEY not set.\n"
            "Run: export TWELVE_DATA_API_KEY='your_key_here'"
        )
    return key

SUPPORTED = ["GBP/USD", "XAU/USD"]
BASE_URL = "https://api.twelvedata.com"

def fetch_candles(instrument: str, granularity: str = "1h", count: int = 50) -> list[dict]:
    key = _get_config()

    url = f"{BASE_URL}/time_series"

    full_url = (
        f"{url}?symbol={instrument}&interval={granularity}"
        f"&outputsize={count}&apikey={key}"
    )

    response = requests.get(full_url, timeout=10)
    response.raise_for_status()
    data = response.json()

    if data.get("status") == "error":
        raise ValueError(f"Twelve Data error: {data.get('message')}")
    
    candles = []
    for bar in reversed(data["values"]):
        candles.append({
            "time":  bar["datetime"],          # "2025-01-01 09:00:00"
            "open":  float(bar["open"]),
            "high":  float(bar["high"]),
            "low":   float(bar["low"]),
            "close": float(bar["close"]),
        })

    return candles

def get_latest_price(instrument: str) -> dict:
    key = _get_config()

    url = f"{BASE_URL}/price"

    full_url = f"{BASE_URL}/price?symbol={instrument}&apikey={key}"


    response = requests.get(full_url, timeout=10)
    response.raise_for_status()
    data = response.json()

    if data.get("status") == "error":
        raise ValueError(f"Twelve Data error: {data.get('message')}")

    price = float(data["price"])

    return {
        "instrument": instrument,
        "price":      price,
        "time":       "live",
    }