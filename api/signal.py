"""
api/signal.py
-------------
ICT Signal Engine — Muditha's Exact 3-Min London Kill Zone Strategy

Rules implemented in order:
  1. London Kill Zone gate        (02:00–05:00 NY time only)
  2. Midnight Open Price          (00:00 NY time reference level)
  3. CHoCH detection              (body close beyond swing high/low)
  4. Liquidity sweep check        (wick beyond recent high/low)
  5. Body close confirmation      (1 or 2 closes beyond CHoCH)
  6. Direction filter             (price vs midnight open price)
  7. FVG detection + validation   (gap must still be intact)
  8. Entry at FVG 50% midpoint
  9. Stop beyond FVG + buffer
  10. Target at 2R

CHoCH definition:
  Bullish: candle BODY closes ABOVE recent swing high (last 5 candles)
  Bearish: candle BODY closes BELOW recent swing low  (last 5 candles)
  WICKS DO NOT COUNT — body close only.

Timeframe: 3-minute candles (resampled from 1-min in marketdata.py)
Session:   London Kill Zone only (02:00–05:00 NY)
"""

from datetime import datetime, timezone, timedelta
from enum import Enum


# ── Constants ─────────────────────────────────────────────────────────────────

LONDON_KZ_START = 2    # 02:00 NY time
LONDON_KZ_END   = 5    # 05:00 NY time
SWING_LOOKBACK  = 5    # candles back to define swing high/low for CHoCH
FVG_LOOKBACK    = 10   # candles back to scan for FVG
PIP             = 0.0001  # GBP/USD pip size
STOP_BUFFER     = 2 * PIP # 2 pip buffer beyond FVG for stop loss


# ── State enum — where are we in the setup sequence ──────────────────────────

class SetupState(Enum):
    """
    Tracks which stage of the setup we're currently in.
    The signal engine is a state machine — it moves through
    these stages in order. Think of it like a checklist.
    """
    WAITING_FOR_KILLZONE    = "waiting_for_killzone"
    WAITING_FOR_CHOCH       = "waiting_for_choch"
    WAITING_FOR_CONFIRMATION= "waiting_for_confirmation"  # Rule 3: second body close
    WAITING_FOR_FVG         = "waiting_for_fvg"
    SIGNAL_READY            = "signal_ready"
    INVALID                 = "invalid"


# ── Kill Zone ─────────────────────────────────────────────────────────────────

def get_kill_zone_status() -> dict:
    """
    Check if we are currently inside the London Kill Zone.
    London KZ: 02:00 – 05:00 NY time.
    NY time = UTC - 4 (summer) / UTC - 5 (winter).
    We use -4 as approximation.
    """
    now_utc = datetime.now(timezone.utc)
    ny_hour = (now_utc.hour - 4) % 24

    in_london = LONDON_KZ_START <= ny_hour < LONDON_KZ_END

    return {
        "in_kill_zone": in_london,
        "session":      "London" if in_london else "None",
        "ny_hour":      ny_hour,
        "ny_time":      f"{ny_hour:02d}:{now_utc.minute:02d}",
    }


# ── Midnight Open Price ───────────────────────────────────────────────────────

def get_midnight_open(candles: list[dict]) -> float | None:
    """
    Find the open price of the candle at 00:00 NY time (midnight).
    This is the key reference level ICT uses to determine daily bias:
      Price ABOVE midnight open → institutional bias BULLISH → only take buys
      Price BELOW midnight open → institutional bias BEARISH → only take sells

    We scan the candles list for the one whose time matches 00:00 NY.
    NY time = UTC - 4, so 00:00 NY = 04:00 UTC.

    Returns the open price or None if not found in the candle data.
    """
    for candle in candles:
        # Parse the candle time
        try:
            t = datetime.fromisoformat(candle["time"].replace(" ", "T"))
        except Exception:
            continue

        # Convert to NY time (UTC-4)
        ny_hour   = (t.hour - 4) % 24
        ny_minute = t.minute

        # Find the candle at exactly 00:00 NY time
        if ny_hour == 0 and ny_minute == 0:
            return float(candle["open"])

    # If we don't have midnight candle in our data window,
    # use the open of the first candle as a fallback
    if candles:
        return float(candles[0]["open"])

    return None


# ── Swing High / Low ──────────────────────────────────────────────────────────

def get_swing_levels(candles: list[dict], lookback: int = SWING_LOOKBACK) -> dict:
    """
    Find the most recent swing high and swing low
    using the last `lookback` candles.

    Swing high = highest HIGH in the lookback window
    Swing low  = lowest  LOW  in the lookback window

    These are the levels that a CHoCH must break with a BODY CLOSE.

    Parameters
    ----------
    candles  : list of candle dicts, oldest → newest
    lookback : how many candles back to look (default 5 = 15 minutes on 3-min)
    """
    if len(candles) < lookback:
        return {"swing_high": None, "swing_low": None}

    # Take the last `lookback` candles EXCLUDING the current candle
    # (we don't want the current bar to define its own swing level)
    window = candles[-(lookback + 1):-1]

    swing_high = max(c["high"] for c in window)
    swing_low  = min(c["low"]  for c in window)

    return {
        "swing_high": round(swing_high, 5),
        "swing_low":  round(swing_low, 5),
    }


# ── CHoCH Detection ───────────────────────────────────────────────────────────

def detect_choch(candles: list[dict]) -> dict:
    """
    Detect a Change of Character (CHoCH) on the current candle.

    CHoCH definition (Muditha's rules — body close only, NO wicks):
      Bullish CHoCH: current candle's CLOSE > recent swing HIGH
        → market structure shifted bullish
        → price closed ABOVE the high of the last 5 candles

      Bearish CHoCH: current candle's CLOSE < recent swing LOW
        → market structure shifted bearish
        → price closed BELOW the low of the last 5 candles

    Returns
    -------
    dict with:
      detected   : bool
      direction  : "Bullish" | "Bearish" | None
      choch_level: the swing level that was broken
      candle     : the candle that made the CHoCH
    """
    if len(candles) < SWING_LOOKBACK + 2:
        return {"detected": False, "direction": None}

    # Current candle (the one we're analysing)
    current = candles[-1]

    # Get swing levels from the candles BEFORE this one
    levels = get_swing_levels(candles[:-1], SWING_LOOKBACK)

    if levels["swing_high"] is None:
        return {"detected": False, "direction": None}

    swing_high = levels["swing_high"]
    swing_low  = levels["swing_low"]

    # ── Bullish CHoCH: BODY close above swing high ────────────────────────────
    # We check close (not high) — body close only, per Muditha's rules
    if current["close"] > swing_high:
        return {
            "detected":    True,
            "direction":   "Bullish",
            "choch_level": swing_high,
            "close":       current["close"],
            "candle_time": current["time"],
            "description": f"Body closed above swing high {swing_high} → Bullish CHoCH",
        }

    # ── Bearish CHoCH: BODY close below swing low ─────────────────────────────
    if current["close"] < swing_low:
        return {
            "detected":    True,
            "direction":   "Bearish",
            "choch_level": swing_low,
            "close":       current["close"],
            "candle_time": current["time"],
            "description": f"Body closed below swing low {swing_low} → Bearish CHoCH",
        }

    return {"detected": False, "direction": None}


# ── Liquidity Sweep ───────────────────────────────────────────────────────────

def detect_liquidity_sweep(candles: list[dict], direction: str) -> dict:
    """
    Detect if a liquidity sweep occurred before the CHoCH.

    A liquidity sweep = price WICK went beyond a recent level
    but the BODY did NOT close beyond it (fake-out / stop hunt).

    Bullish sweep: wick below recent low, body closed above it
      → big players hunted stops below the low, then reversed up
    Bearish sweep: wick above recent high, body closed below it
      → big players hunted stops above the high, then reversed down

    If a sweep is detected, Rule 2 applies:
      → need body close confirmation (and Rule 3: double close)

    Parameters
    ----------
    candles   : list of candle dicts
    direction : "Bullish" or "Bearish" (direction of the CHoCH)
    """
    if len(candles) < 10:
        return {"swept": False}

    # Look at the last 5 candles for a sweep
    recent   = candles[-5:]
    # Reference level from 5-20 candles ago
    lookback = candles[-20:-5]

    if not lookback:
        return {"swept": False}

    if direction == "Bullish":
        # Find the lowest point in the lookback period
        swing_low = min(c["low"] for c in lookback)

        # Check if any recent candle's WICK went below but BODY closed above
        for bar in recent:
            wick_swept  = bar["low"]   < swing_low   # wick went below
            body_closed = bar["close"] > swing_low   # body closed above
            if wick_swept and body_closed:
                return {
                    "swept":       True,
                    "swept_level": round(swing_low, 5),
                    "description": f"Wick swept low at {swing_low:.5f}, body closed above → liquidity grab",
                    "rule":        "Rule 2 applies: wait for body close confirmation",
                }

    elif direction == "Bearish":
        # Find the highest point in the lookback period
        swing_high = max(c["high"] for c in lookback)

        # Check if any recent candle's WICK went above but BODY closed below
        for bar in recent:
            wick_swept  = bar["high"]  > swing_high  # wick went above
            body_closed = bar["close"] < swing_high  # body closed below
            if wick_swept and body_closed:
                return {
                    "swept":       True,
                    "swept_level": round(swing_high, 5),
                    "description": f"Wick swept high at {swing_high:.5f}, body closed below → liquidity grab",
                    "rule":        "Rule 2 applies: wait for body close confirmation",
                }

    return {"swept": False}


# ── Second Body Close Confirmation (Rule 3) ───────────────────────────────────

def check_double_confirmation(
    candles: list[dict],
    choch_level: float,
    direction: str,
) -> dict:
    """
    Rule 3: After a liquidity sweep + CHoCH, wait for TWO candle
    body closes beyond the CHoCH level before confirming the setup.

    This filters out false breakouts — if price truly broke structure,
    it should be able to close beyond the level twice.

    Parameters
    ----------
    candles     : recent candles
    choch_level : the swing level that was broken (CHoCH level)
    direction   : "Bullish" or "Bearish"

    Returns
    -------
    dict with:
      confirmed      : bool — have we seen 2 body closes beyond CHoCH?
      closes_counted : int  — how many closes we've seen (0, 1, or 2)
    """
    if len(candles) < 2:
        return {"confirmed": False, "closes_counted": 0}

    # Count how many of the last 3 candles have a body close beyond CHoCH
    recent = candles[-3:]
    count  = 0

    for bar in recent:
        if direction == "Bullish" and bar["close"] > choch_level:
            count += 1
        elif direction == "Bearish" and bar["close"] < choch_level:
            count += 1

    return {
        "confirmed":      count >= 2,
        "closes_counted": count,
    }


# ── Midnight Open Direction Filter (Rule 6) ───────────────────────────────────

def check_midnight_filter(
    current_price: float,
    midnight_open: float,
    choch_direction: str,
) -> dict:
    """
    Rule 6: The midnight open price defines the daily institutional bias.
      Buy  trades: price must be ABOVE midnight open
      Sell trades: price must be BELOW midnight open

    Also resolves Rule 5 (conflicting CHoCH):
      If you have both a buy and sell CHoCH, the midnight open
      tells you which direction to trust.

    Parameters
    ----------
    current_price   : latest close price
    midnight_open   : open price at 00:00 NY time
    choch_direction : "Bullish" or "Bearish"

    Returns
    -------
    dict with:
      passes  : bool — does direction align with midnight bias?
      bias    : "Bullish" | "Bearish" (what midnight open says)
      reason  : explanation string
    """
    # Determine institutional bias from midnight open
    if current_price > midnight_open:
        midnight_bias = "Bullish"
    else:
        midnight_bias = "Bearish"

    # Check if CHoCH direction matches midnight bias
    aligned = choch_direction == midnight_bias

    if aligned:
        reason = (
            f"Price {current_price} is "
            f"{'above' if midnight_bias == 'Bullish' else 'below'} "
            f"midnight open {midnight_open} → "
            f"Bias confirms {choch_direction} CHoCH ✓"
        )
    else:
        reason = (
            f"Price {current_price} is "
            f"{'above' if midnight_bias == 'Bullish' else 'below'} "
            f"midnight open {midnight_open} → "
            f"Bias is {midnight_bias} but CHoCH is {choch_direction} → SKIP"
        )

    return {
        "passes":        aligned,
        "midnight_bias": midnight_bias,
        "midnight_open": round(midnight_open, 5),
        "current_price": round(current_price, 5),
        "reason":        reason,
    }


# ── FVG Detection ─────────────────────────────────────────────────────────────

def find_fvg(candles: list[dict], direction: str) -> dict:
    """
    Find a Fair Value Gap (FVG) in the direction of the CHoCH.

    FVG = 3-candle imbalance where price moved so fast it left a gap:
      Bullish FVG: bar[i-1].high < bar[i+1].low
        → gap between previous high and next low
        → price is expected to return and fill this gap
        → we enter LONG when price returns to this zone

      Bearish FVG: bar[i-1].low > bar[i+1].high
        → gap between previous low and next high
        → we enter SHORT when price returns to this zone

    Rule 4: If price has already closed BEYOND the FVG (broken it),
    the FVG is invalid. We mark it as broken and wait for a new one.

    Parameters
    ----------
    candles   : list of 3-min candle dicts
    direction : "Bullish" or "Bearish"
    """
    if len(candles) < 3:
        return {"found": False}

    scan = candles[-FVG_LOOKBACK:]
    current_price = candles[-1]["close"]

    # Scan newest to oldest — most recent FVG takes priority
    for i in range(len(scan) - 2, 0, -1):
        prev = scan[i - 1]   # candle before the impulse
        curr = scan[i]       # impulse candle
        nxt  = scan[i + 1]   # candle after the impulse

        if direction == "Bullish":
            # Bullish FVG: gap between prev high and next low
            if prev["high"] < nxt["low"]:
                fvg_low  = prev["high"]
                fvg_high = nxt["low"]
                fvg_mid  = (fvg_high + fvg_low) / 2

                # Rule 4: check if FVG is still valid
                # (current price hasn't closed BELOW fvg_low)
                broken = current_price < fvg_low

                return {
                    "found":     True,
                    "valid":     not broken,
                    "broken":    broken,
                    "fvg_high":  round(fvg_high, 5),
                    "fvg_low":   round(fvg_low, 5),
                    "fvg_mid":   round(fvg_mid, 5),   # Rule 7: enter here
                    "direction": "Bullish",
                    "bar_time":  curr["time"],
                    "note":      "FVG broken — wait for new FVG" if broken else "FVG valid — watch for price to return",
                }

        elif direction == "Bearish":
            # Bearish FVG: gap between prev low and next high
            if prev["low"] > nxt["high"]:
                fvg_high = prev["low"]
                fvg_low  = nxt["high"]
                fvg_mid  = (fvg_high + fvg_low) / 2

                # Rule 4: check if FVG is still valid
                # (current price hasn't closed ABOVE fvg_high)
                broken = current_price > fvg_high

                return {
                    "found":     True,
                    "valid":     not broken,
                    "broken":    broken,
                    "fvg_high":  round(fvg_high, 5),
                    "fvg_low":   round(fvg_low, 5),
                    "fvg_mid":   round(fvg_mid, 5),
                    "direction": "Bearish",
                    "bar_time":  curr["time"],
                    "note":      "FVG broken — wait for new FVG" if broken else "FVG valid — watch for price to return",
                }

    return {"found": False}


# ── Trade Levels ──────────────────────────────────────────────────────────────

def calculate_trade_levels(signal: str, fvg: dict) -> dict:
    """
    Calculate exact entry, stop loss, and take profit.

    Rule 7: Enter at 50% midpoint of FVG
    Rule 8: 1:2 Risk-to-Reward

    Entry    : FVG midpoint (50%)
    Stop     : beyond FVG edge + 2 pip buffer
    Target   : entry + 2 × risk
    """
    if not fvg.get("found") or not fvg.get("valid"):
        return {}

    entry = fvg["fvg_mid"]   # Rule 7: 50% of FVG

    if signal == "LONG":
        stop_loss   = fvg["fvg_low"] - STOP_BUFFER
        risk        = entry - stop_loss
        take_profit = entry + (risk * 2)   # Rule 8: 1:2 RR

    else:  # SHORT
        stop_loss   = fvg["fvg_high"] + STOP_BUFFER
        risk        = stop_loss - entry
        take_profit = entry - (risk * 2)   # Rule 8: 1:2 RR

    return {
        "entry":       round(entry, 5),
        "stop_loss":   round(stop_loss, 5),
        "take_profit": round(take_profit, 5),
        "risk_pips":   round(risk / PIP, 1),
        "reward_pips": round((risk * 2) / PIP, 1),
        "rr_ratio":    "1:2",
    }


# ── Confidence Score ──────────────────────────────────────────────────────────

def calculate_confidence(
    kz: dict,
    choch: dict,
    sweep: dict,
    midnight_filter: dict,
    fvg: dict,
    double_confirmed: bool,
) -> dict:
    """
    Score the setup quality based on how many conditions are met.
    Returns a score, label, and per-condition breakdown for the dashboard.
    """
    conditions = [
        {
            "rule":    "Rule 1 — Kill Zone",
            "passes":  kz["in_kill_zone"],
            "detail":  f"London KZ active at NY {kz['ny_time']}" if kz["in_kill_zone"] else f"Outside KZ — NY {kz['ny_time']}",
        },
        {
            "rule":    "Rule 1 — CHoCH detected",
            "passes":  choch["detected"],
            "detail":  choch.get("description", "No CHoCH yet"),
        },
        {
            "rule":    "Rule 6 — Midnight Open filter",
            "passes":  midnight_filter.get("passes", False),
            "detail":  midnight_filter.get("reason", "N/A"),
        },
        {
            "rule":    "Rule 2 — Liquidity sweep",
            "passes":  sweep["swept"],
            "detail":  sweep.get("description", "No sweep detected"),
        },
        {
            "rule":    "Rule 3 — Double confirmation",
            "passes":  double_confirmed,
            "detail":  "Two body closes beyond CHoCH confirmed" if double_confirmed else "Waiting for second body close",
        },
        {
            "rule":    "Rule 4 — Valid FVG",
            "passes":  fvg.get("found") and fvg.get("valid"),
            "detail":  fvg.get("note", "No FVG found yet"),
        },
    ]

    # Score = number of passing conditions
    score     = sum(1 for c in conditions if c["passes"])
    max_score = len(conditions)
    pct       = round(score / max_score * 100)

    # Label based on score
    if score == max_score:
        label = "HIGH"
    elif score >= max_score - 2:
        label = "MEDIUM"
    else:
        label = "LOW"

    return {
        "label":      label,
        "score":      score,
        "max_score":  max_score,
        "percentage": pct,
        "conditions": conditions,
    }


# ── Master Signal Function ────────────────────────────────────────────────────

def generate_signal(instrument: str, candles: list[dict]) -> dict:
    """
    Master function — runs all rules in order and returns a complete signal.

    This is the function called by api/server.py.
    Implements Muditha's exact 3-min London Kill Zone strategy.

    Parameters
    ----------
    instrument : "GBP/USD" or "XAU/USD"
    candles    : list of 3-min OHLC candle dicts, oldest → newest

    Returns
    -------
    Complete signal dict for the dashboard.
    """

    # ── Rule 1a: Kill Zone gate ───────────────────────────────────────────────
    kz = get_kill_zone_status()

    if not kz["in_kill_zone"]:
        return _wait_signal(
            instrument, kz,
            signal="WAIT",
            reason=f"Outside London Kill Zone. NY time is {kz['ny_time']}. "
                   f"London KZ opens at 02:00 NY (07:30 IST).",
            state=SetupState.WAITING_FOR_KILLZONE.value,
            candles=candles,
        )

    # ── Midnight Open Price ───────────────────────────────────────────────────
    midnight_open = get_midnight_open(candles)
    current_price = candles[-1]["close"] if candles else 0

    # ── Rule 1b: CHoCH detection ──────────────────────────────────────────────
    choch = detect_choch(candles)

    if not choch["detected"]:
        return _wait_signal(
            instrument, kz,
            signal="WAIT",
            reason="In London Kill Zone. Watching for CHoCH "
                   "(body close beyond last 5-candle swing high or low).",
            state=SetupState.WAITING_FOR_CHOCH.value,
            candles=candles,
            midnight_open=midnight_open,
        )

    choch_direction = choch["direction"]

    # ── Rule 6: Midnight Open filter ─────────────────────────────────────────
    midnight_filter = check_midnight_filter(
        current_price, midnight_open, choch_direction
    ) if midnight_open else {"passes": True, "midnight_bias": "Unknown", "reason": "Midnight open not available"}

    if not midnight_filter["passes"]:
        return _wait_signal(
            instrument, kz,
            signal="WAIT",
            reason=f"CHoCH detected ({choch_direction}) but midnight open "
                   f"filter failed. {midnight_filter['reason']}",
            state=SetupState.WAITING_FOR_CHOCH.value,
            candles=candles,
            choch=choch,
            midnight_open=midnight_open,
            midnight_filter=midnight_filter,
        )

    # ── Rule 2: Liquidity sweep check ────────────────────────────────────────
    sweep = detect_liquidity_sweep(candles, choch_direction)

    # ── Rule 3: Double confirmation (only required if sweep detected) ─────────
    double_conf = {"confirmed": True, "closes_counted": 1}  # default: not required
    if sweep["swept"]:
        double_conf = check_double_confirmation(
            candles,
            choch["choch_level"],
            choch_direction,
        )
        if not double_conf["confirmed"]:
            return _wait_signal(
                instrument, kz,
                signal="WAIT",
                reason=f"{choch_direction} CHoCH confirmed with liquidity sweep. "
                       f"Rule 3: waiting for second body close beyond CHoCH. "
                       f"({double_conf['closes_counted']}/2 closes seen)",
                state=SetupState.WAITING_FOR_CONFIRMATION.value,
                candles=candles,
                choch=choch,
                sweep=sweep,
                midnight_open=midnight_open,
                midnight_filter=midnight_filter,
            )

    # ── Rule 1 + 4: FVG detection and validation ──────────────────────────────
    fvg = find_fvg(candles, choch_direction)

    if not fvg["found"]:
        return _wait_signal(
            instrument, kz,
            signal="WAIT",
            reason=f"{choch_direction} CHoCH confirmed. "
                   f"Waiting for FVG to form in {choch_direction} direction.",
            state=SetupState.WAITING_FOR_FVG.value,
            candles=candles,
            choch=choch,
            sweep=sweep,
            midnight_open=midnight_open,
            midnight_filter=midnight_filter,
        )

    if fvg.get("broken"):
        return _wait_signal(
            instrument, kz,
            signal="WAIT",
            reason=f"FVG was found but has been broken (Rule 4). "
                   f"Waiting for a new FVG to form.",
            state=SetupState.WAITING_FOR_FVG.value,
            candles=candles,
            choch=choch,
            sweep=sweep,
            fvg=fvg,
            midnight_open=midnight_open,
            midnight_filter=midnight_filter,
        )

    # ── All rules passed — generate signal ────────────────────────────────────
    raw_signal   = "LONG" if choch_direction == "Bullish" else "SHORT"
    trade_levels = calculate_trade_levels(raw_signal, fvg)
    confidence   = calculate_confidence(
        kz, choch, sweep, midnight_filter, fvg, double_conf["confirmed"]
    )

    # Build the reason string
    sweep_note = f"Liquidity sweep at {sweep['swept_level']} confirmed. " if sweep["swept"] else ""
    reason = (
        f"{choch_direction} CHoCH — body closed beyond swing level {choch['choch_level']}. "
        f"{sweep_note}"
        f"Midnight open {midnight_open} confirms {choch_direction} bias. "
        f"FVG at {fvg['fvg_low']}–{fvg['fvg_high']}. "
        f"Enter at 50% midpoint {fvg['fvg_mid']}. "
        f"Rule 4: FVG intact ✓"
    )

    return {
        "instrument":     instrument,
        "signal":         raw_signal,
        "reason":         reason,
        "state":          SetupState.SIGNAL_READY.value,
        "confidence":     confidence,
        "kill_zone":      kz,
        "midnight_open":  midnight_open,
        "midnight_filter":midnight_filter,
        "choch":          choch,
        "sweep":          sweep,
        "double_confirm": double_conf,
        "fvg":            fvg,
        "trade_levels":   trade_levels,
        "analysed_at":    datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
    }


# ── Helper: build a WAIT signal response ─────────────────────────────────────

def _wait_signal(
    instrument: str,
    kz: dict,
    signal: str,
    reason: str,
    state: str,
    candles: list[dict],
    choch: dict  = None,
    sweep: dict  = None,
    fvg: dict    = None,
    midnight_open: float = None,
    midnight_filter: dict = None,
) -> dict:
    """
    Helper to build a consistent WAIT response.
    Used when any rule in the chain fails.
    All optional fields default to empty/None so the
    dashboard always gets a complete consistent structure.
    """
    # Build a partial confidence score to show progress on dashboard
    confidence = calculate_confidence(
        kz,
        choch   or {"detected": False, "direction": None},
        sweep   or {"swept": False},
        midnight_filter or {"passes": False, "reason": "Not reached yet"},
        fvg     or {"found": False},
        False,
    )

    return {
        "instrument":      instrument,
        "signal":          signal,
        "reason":          reason,
        "state":           state,
        "confidence":      confidence,
        "kill_zone":       kz,
        "midnight_open":   midnight_open,
        "midnight_filter": midnight_filter,
        "choch":           choch   or {"detected": False},
        "sweep":           sweep   or {"swept": False},
        "double_confirm":  {"confirmed": False, "closes_counted": 0},
        "fvg":             fvg     or {"found": False},
        "trade_levels":    {},
        "analysed_at":     datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
    }