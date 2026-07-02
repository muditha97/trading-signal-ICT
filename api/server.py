from fastapi import FastAPI, HTTPException
from datetime import datetime, timezone
from fastapi.middleware.cors import CORSMiddleware

from api.marketdata import fetch_candles, get_latest_price
from api.signal import generate_signal

app = FastAPI(
    title="Trading Signal API - ICT",
    description="ICT-based signal engine for GBP/USD and XAU/USD",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8000",
        "http://127.0.0.1:5500",
        "https://trading-signal-ict.vercel.app",
    ],
    allow_methods=["*"],
    allow_credentials=True,
    allow_headers=["*"],
)

@app.get("/")
def root():
    return {"message": "Trading signal API is running"}

@app.get("/health")
def health():
    now_utc = datetime.now(timezone.utc)

    ny_hour = (now_utc.hour - 4) % 24

    in_london_kz  = 2 <= ny_hour < 5     # London Kill Zone: 02:00–05:00 NY
    in_ny_kz      = 7 <= ny_hour < 10    # NY Kill Zone:     07:00–10:00 NY
    in_kill_zone  = in_london_kz or in_ny_kz

    if in_london_kz:
        active_session = "London Kill Zone 🟢"
    elif in_ny_kz:
        active_session= "New York Kill Zone 🟢"
    else:
        active_session= "No Kill Zone — waiting ⚪"

    return {
        "status": "ok",
        "server_time_utc": now_utc.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "ny_hour": ny_hour,
        "in_kill_zone": in_kill_zone,
        "active_session": active_session,
        "instruments": ["GBP/USD", "XAU/USD"]
    }

@app.get("/data/{instrument}")
def get_market_data(instrument: str):
    instrument_map = {
        "GBPUSD": "GBP/USD",
        "XAUUSD": "XAU/USD",
    }

    clean_instrument = instrument_map.get(instrument.upper())
    if not clean_instrument:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown instrument '{instrument}'. Use GBPUSD or XAUUSD."
        )
    
    try:
        candles      = fetch_candles(clean_instrument, granularity="1h", count=50)
        latest_price = get_latest_price(clean_instrument)
    except ValueError as e:
        # Missing API token
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        # Twelve Data API error
        raise HTTPException(status_code=502, detail=f"TwelveData error: {str(e)}")
    
    return {
        "instrument":   clean_instrument,
        "granularity":  "H1",
        "candle_count": len(candles),
        "latest_price": latest_price,
        "candles":      candles,        # last 50 H1 candles
    }

@app.get("/signal/{instrument}")
def get_signal(instrument: str):
    instrument_map = {
        "GBPUSD": "GBP/USD",
        "XAUUSD": "XAU/USD",
    }

    clean_instrument = instrument_map.get(instrument.upper())
    if not clean_instrument:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown instrument '{instrument}'. Use GBPUSD or XAUUSD."
        )

    try:
        # Step 1: fetch live candles (from Step 3)
        candles = fetch_candles(clean_instrument, granularity="3min", count=200)

        # Step 2: run ICT signal logic (Step 4)
        signal  = generate_signal(clean_instrument, candles)

    except ValueError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Error: {str(e)}")

    return signal

@app.get("/hello")
def hello():
    return {"message": "Hello Muditha!", "status": "ok"}

@app.get("/debug/{instrument}")
def debug(instrument: str):
    """Temporary debug route — remove after fixing."""
    instrument_map = {"GBPUSD": "GBP/USD", "XAUUSD": "XAU/USD"}
    clean = instrument_map.get(instrument.upper())

    try:
        candles = fetch_candles(clean, granularity="3min", count=200)
        candle_count = len(candles)
        last_candle  = candles[-1] if candles else None
    except Exception as e:
        return {"step": "fetch_candles", "error": str(e)}

    try:
        signal = generate_signal(clean, candles)
    except Exception as e:
        return {"step": "generate_signal", "error": str(e), "candle_count": candle_count}

    return {"step": "all_ok", "candle_count": candle_count, "last_candle": last_candle}