"""
api/marketdata.py
-----------------
Fetch live candle data from Twelve Data API.
Includes resampling from 1-min → 3-min for Muditha's strategy.
"""

import os
import requests
from datetime import datetime


BASE_URL = "https://api.twelvedata.com"


def _get_config() -> str:
    key = os.environ.get("TWELVE_DATA_API_KEY")
    if not key:
        raise ValueError(
            "TWELVE_DATA_API_KEY not set.\n"
            "Run: export TWELVE_DATA_API_KEY='your_key_here'"
        )
    return key


def fetch_candles_raw(instrument: str, interval: str = "1min", count: int = 200) -> list[dict]:
    """
    Fetch raw candles from Twelve Data.
    We fetch 1-min candles then resample to 3-min ourselves.
    This gives us more control and works on the free tier.

    Parameters
    ----------
    instrument : "GBP/USD" or "XAU/USD"
    interval   : "1min" for raw data
    count      : number of 1-min candles (200 = ~3 hours, covers full London KZ)
    """
    key = _get_config()

    # Build URL manually to avoid slash encoding issue
    full_url = (
        f"{BASE_URL}/time_series"
        f"?symbol={instrument}"
        f"&interval={interval}"
        f"&outputsize={count}"
        f"&apikey={key}"
    )

    response = requests.get(full_url, timeout=10)
    response.raise_for_status()
    data = response.json()

    if data.get("status") == "error":
        raise ValueError(f"Twelve Data error: {data.get('message')}")

    # Parse candles — Twelve Data returns newest first, reverse to oldest first
    candles = []
    for bar in reversed(data["values"]):
        candles.append({
            "time":  bar["datetime"],
            "open":  float(bar["open"]),
            "high":  float(bar["high"]),
            "low":   float(bar["low"]),
            "close": float(bar["close"]),
        })

    return candles


def resample_to_3min(candles_1min: list[dict]) -> list[dict]:
    """
    Resample 1-minute candles into 3-minute candles.

    Groups every 3 consecutive 1-min candles into one 3-min candle:
      open  = first candle's open
      high  = highest high across 3 candles
      low   = lowest low across 3 candles
      close = last candle's close
      time  = first candle's time

    This is exactly how TradingView builds 3-min candles.
    """
    candles_3min = []

    # Process in groups of 3
    for i in range(0, len(candles_1min) - 2, 3):
        group = candles_1min[i:i + 3]

        if len(group) < 3:
            continue

        candles_3min.append({
            "time":  group[0]["time"],                        # open time of first bar
            "open":  group[0]["open"],                        # open of first bar
            "high":  max(c["high"] for c in group),          # highest high
            "low":   min(c["low"]  for c in group),          # lowest low
            "close": group[-1]["close"],                      # close of last bar
        })

    return candles_3min


def fetch_candles(instrument: str, granularity: str = "3min", count: int = 200) -> list[dict]:
    """
    Main function used by server.py.
    Fetches 1-min data and resamples to 3-min.

    Returns ~65 three-minute candles (enough for full London session analysis).
    """
    # Always fetch 1-min and resample — more reliable than requesting 3-min directly
    raw = fetch_candles_raw(instrument, interval="1min", count=count)
    candles_3min = resample_to_3min(raw)
    return candles_3min


def get_latest_price(instrument: str) -> dict:
    """Get the current live price."""
    key = _get_config()
    full_url = f"{BASE_URL}/price?symbol={instrument}&apikey={key}"

    response = requests.get(full_url, timeout=10)
    response.raise_for_status()
    data = response.json()

    if data.get("status") == "error":
        raise ValueError(f"Twelve Data error: {data.get('message')}")

    return {
        "instrument": instrument,
        "price":      float(data["price"]),
        "time":       "live",
    }