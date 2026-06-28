from datetime import datetime, timezone

def get_kill_zone_status() -> dict:
    now_utc = datetime.now(timezone.utc)

    ny_hour = (now_utc.hour - 4) % 24

    # London Kill Zone: 02:00 – 05:00 NY time
    in_london = 2 <= ny_hour < 5

    # New York Kill Zone: 07:00 – 10:00 NY time
    in_ny = 7 <= ny_hour < 10

    if in_london:
        session = "London"
    elif in_ny:
        session = "New York"
    else:
        session = "None"

    return {
        "in_kill_zone": in_london or in_ny,
        "session":      session,
        "ny_hour":      ny_hour,
    }

def get_market_structure(candles: list[dict]) -> dict:
    if len(candles) <20:
        return {"bias": "Ranging", "swing_high": 0, "swing_low": 0, "current_price": 0}
    
    recent = candles[-30:]

    swing_highs = []
    swing_lows  = []
    window = 2

    for i in range(window, len(recent) - window):
        bar = recent[i]

        left_bars  = recent[max(0, i - window) : i]
        right_bars = recent[i + 1 : min(len(recent), i + window + 1)]

        if not left_bars or not right_bars:
            continue

        is_swing_high = (
            bar["high"] > max(b["high"] for b in left_bars) and
            bar["high"] > max(b["high"] for b in right_bars)
        )

        is_swing_low = (
            bar["low"] < min(b["low"] for b in left_bars) and
            bar["low"] < min(b["low"] for b in right_bars)
        )

        if is_swing_high:
            swing_highs.append(bar["high"])
        if is_swing_low:
            swing_lows.append(bar["low"])

        current_price = candles[-1]["close"]

        last_swing_high = swing_highs[-1] if swing_highs else max(c["high"] for c in recent)
        last_swing_low  = swing_lows[-1]  if swing_lows  else min(c["low"]  for c in recent)

        if current_price > last_swing_high:
            bias = "Bullish"
        elif current_price < last_swing_low:
            bias = "Bearish"
        else:
            bias = "Ranging"

        return {
            "bias":          bias,
            "swing_high":    round(last_swing_high, 5),
            "swing_low":     round(last_swing_low, 5),
            "current_price": round(current_price, 5),
        }


def find_fvg(candles: list[dict], direction: str) -> dict:
    scan_range = candles[-10:]

    if len(scan_range) < 3:
        return {"found": False}
    
    for i in range(len(scan_range) - 2, 0, -1):
        prev = scan_range[i-1]
        curr = scan_range[i]
        nxt = scan_range[i+1]

        if direction == "Bullish":
            if prev["high"] < nxt["low"]:
                fvg_low  = prev["high"]
                fvg_high = nxt["low"]
                return {
                    "found":     True,
                    "fvg_high":  round(fvg_high, 5),
                    "fvg_low":   round(fvg_low, 5),
                    "fvg_mid":   round((fvg_high + fvg_low) / 2, 5),
                    "direction": "Bullish",
                    "bar_time":  curr["time"],
                }
        elif direction == "Bearish":
            if prev["low"] > nxt["high"]:
                fvg_low  = nxt["high"]
                fvg_high = prev["low"]
                return {
                    "found":     True,
                    "fvg_high":  round(fvg_high, 5),
                    "fvg_low":   round(fvg_low, 5),
                    "fvg_mid":   round((fvg_high + fvg_low) / 2, 5),
                    "direction": "Bearish",
                    "bar_time":  curr["time"],
                }
            
    return {"found": False}

def check_liquidity_sweep(candles: list[dict], direction: str) -> dict:
    if len(candles) < 10:
        return {"swept": False}
    
    recent   = candles[-5:]
    lookback  = candles[-20:-5]

    if not lookback:
        return {"swept": False}
    
    if direction == "Bullish":
        swing_low = min(c["low"] for c in lookback)

        for bar in recent:
            if bar["low"] < swing_low and bar["close"] > swing_low:
                return {
                    "swept":        True,
                    "swept_level":  round(swing_low, 5),
                    "description":  f"Price swept low at {swing_low:.5f} then closed above",
                }
            
    elif direction == "Bearish":
        swing_high = max(c["high"] for c in lookback)

        for bar in recent:
            if bar["high"] > swing_high and bar["close"] < swing_high:
                return {
                    "swept":        True,
                    "swept_level":  round(swing_high, 5),
                    "description":  f"Price swept high at {swing_high:.5f} then closed below",
                }
    return {"swept": False}

def calculate_trade_levels(candles: list[dict], signal: str, fvg: dict) -> dict:
    if not fvg.get("found"):
        return {}
    
    pip = 0.0001
    buffer = 2 * pip

    entry = fvg["fvg_mid"]

    if signal == "LONG":
        stop_loss   = fvg["fvg_low"] - buffer
        risk        = entry - stop_loss
        take_profit = entry + (risk * 2)

    else:
        stop_loss   = fvg["fvg_high"] + buffer
        risk        = stop_loss - entry
        take_profit = entry - (risk * 2)

    return {
        "entry":       round(entry, 5),
        "stop_loss":   round(stop_loss, 5),
        "take_profit": round(take_profit, 5),
        "risk_pips":   round(risk / pip, 1),
        "reward_pips": round((risk * 2) / pip, 1),
        "rr_ratio":    "2:1",
    }

def generate_signal(instrument: str, candles: list[dict]) -> dict:
    kz = get_kill_zone_status()

    structure = get_market_structure(candles)
    bias      = structure["bias"]

    fvg = {"found": False}
    if bias in ("Bullish", "Bearish"):
        fvg = find_fvg(candles, bias)

    sweep = {"swept": False}
    if bias in ("Bullish", "Bearish"):
        sweep = check_liquidity_sweep(candles, bias)

    signal = "WAIT"
    reason = ""
    trade_levels = {}

    if not kz["in_kill_zone"]:
        # Outside kill zone — never trade ICT outside these windows
        signal = "WAIT"
        reason = f"Outside kill zone. NY hour is {kz['ny_hour']}:00. " \
                 f"Next windows: London 02-05, NY 07-10."
        
    elif bias == "Ranging":
        # No clear trend — ICT needs structure
        signal = "WAIT"
        reason = "Market structure is ranging. Wait for a clear Higher High/Low or Lower High/Low."

    elif not fvg["found"]:
        # Right time, right direction, but no FVG entry trigger yet
        signal = "WAIT"
        reason = f"Structure is {bias} and in kill zone, but no Fair Value Gap found yet. " \
                 f"Watch for an imbalance to form."
        
    else:
        # All conditions met — generate a signal
        raw_signal = "LONG" if bias == "Bullish" else "SHORT"
        trade_levels = calculate_trade_levels(candles, raw_signal, fvg)

        if sweep["swept"]:
            # Full ICT setup — kill zone + structure + FVG + sweep
            signal = raw_signal
            reason = (
                f"{bias} structure in {kz['session']} Kill Zone. "
                f"Liquidity swept at {sweep.get('swept_level')}. "
                f"Bullish FVG at {fvg.get('fvg_low')}–{fvg.get('fvg_high')}. "
                f"Full ICT setup confirmed."
            )
        else:
            # Partial setup — no sweep but FVG present (lower confidence)
            signal = raw_signal
            reason = (
                f"{bias} structure in {kz['session']} Kill Zone. "
                f"FVG found at {fvg.get('fvg_low')}–{fvg.get('fvg_high')}. "
                f"No liquidity sweep — lower confidence setup."
            )

    return {
        "instrument":   instrument,
        "signal":       signal,           # LONG | SHORT | WAIT
        "reason":       reason,           # plain English explanation
        "confidence":   _confidence(signal, sweep, fvg, kz),
        "kill_zone":    kz,
        "structure":    structure,
        "fvg":          fvg,
        "sweep":        sweep,
        "trade_levels": trade_levels,     # entry / stop / target
        "analysed_at":  datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
    }

def _confidence(signal: str, sweep: dict, fvg: dict, kz: dict) -> str:
    if signal == "WAIT":
        return "WAIT"
    
    score = 0
    if kz["in_kill_zone"]:  score += 1
    if fvg.get("found"):    score += 1
    if sweep.get("swept"):  score += 1

    if score == 3:   return "HIGH"
    if score == 2:   return "MEDIUM"
    return "LOW"