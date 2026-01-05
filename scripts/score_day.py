from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

ET = ZoneInfo("America/New_York")

def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)

def http_get_json(url: str, params: dict | None = None, timeout: int = 25):
    r = requests.get(url, params=params, timeout=timeout, headers={"User-Agent": "btc-journal-bot/1.0"})
    r.raise_for_status()
    return r.json()

def fetch_15m_binance(date_et: str) -> list[dict]:
    y, m, d = [int(x) for x in date_et.split("-")]
    start_et = datetime(y, m, d, 6, 0, tzinfo=ET)
    end_et = start_et + timedelta(days=1)

    url = "https://data-api.binance.vision/api/v3/klines"
    params = {
        "symbol": "BTCUSDT",
        "interval": "15m",
        "startTime": _ms(start_et.astimezone(timezone.utc)),
        "endTime": _ms(end_et.astimezone(timezone.utc)),
        "limit": 1000,
    }
    data = http_get_json(url, params=params)
    rows = []
    for k in data:
        rows.append({
            "t_open_ms": int(k[0]),
            "open": float(k[1]),
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4]),
        })
    return rows

def parse_trigger(s: str) -> tuple[str, float] | None:
    # "15m close >= 87362.71" or "<="
    m = re.search(r"(>=|<=)\s*([0-9]+(?:\.[0-9]+)?)", s or "")
    if not m:
        return None
    return m.group(1), float(m.group(2))

def load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_json(path: str, obj: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, sort_keys=True)
        f.write("\n")

def journal_path(date_et: str) -> str:
    y = date_et.split("-")[0]
    return os.path.join("journal", y, f"{date_et}.json")

def score(date_et: str) -> dict:
    path = journal_path(date_et)
    if not os.path.exists(path):
        return {"status": "missing_file", "path": path}

    j = load_json(path)
    t = (j.get("paper_test_trade") or {})
    long = (t.get("long") or {})
    short = (t.get("short") or {})

    lt = parse_trigger(str(long.get("trigger", "")))
    st = parse_trigger(str(short.get("trigger", "")))

    scored_at = datetime.now(tz=ET).strftime("%Y-%m-%d %H:%M:%S")

    def write_and_return(status: str, review: dict):
        j["paper_test_trade_review"] = review
        j["daily_result"] = review.get("daily_result", status)
        j["daily_R"] = float(review.get("R", 0.0) or 0.0)
        save_json(path, j)
        return {"status": status, "path": path}

    if not lt or not st:
        return write_and_return("no_trigger_fields", {
            "status": "no_trigger_fields",
            "date_et": date_et,
            "scored_at_et": scored_at,
            "triggered": "missing",
            "filled": False,
            "exit": "no_trigger_fields",
            "R": 0.0,
            "daily_result": "no_trigger_fields",
        })

    # prices
    long_entry = float(long["entry"])
    long_stop  = float(long["stop"])
    long_tps   = [float(x) for x in (long.get("tps") or [])]
    short_entry = float(short["entry"])
    short_stop  = float(short["stop"])
    short_tps   = [float(x) for x in (short.get("tps") or [])]

    candles = fetch_15m_binance(date_et)
    if not candles:
        return write_and_return("no_candles", {
            "status": "no_candles",
            "date_et": date_et,
            "scored_at_et": scored_at,
            "triggered": "missing",
            "filled": False,
            "exit": "no_candles",
            "R": 0.0,
            "daily_result": "no_candles",
        })

    # Session expiry: 17:00 ET hard stop for paper test trade
    y, m, d = [int(x) for x in date_et.split("-")]
    expiry_et = datetime(y, m, d, 17, 0, tzinfo=ET)

    # Find the last close before expiry (for expired_close settlement)
    last_close = None
    last_close_time_et = None
    for c in candles:
        t_open = datetime.fromtimestamp(c["t_open_ms"]/1000, tz=timezone.utc).astimezone(ET)
        if t_open >= expiry_et:
            break
        last_close = c["close"]
        last_close_time_et = (t_open + timedelta(minutes=15)).strftime("%Y-%m-%d %H:%M")

    # find earliest trigger (based on candle close)
    long_idx = None
    short_idx = None
    for i, c in enumerate(candles):
        t_open = datetime.fromtimestamp(c["t_open_ms"]/1000, tz=timezone.utc).astimezone(ET)
        if t_open >= expiry_et:
            break
        if long_idx is None and c["close"] >= lt[1]:
            long_idx = i
        if short_idx is None and c["close"] <= st[1]:
            short_idx = i

    triggered = None
    trig_idx = None
    if long_idx is None and short_idx is None:
        triggered = "none"
    elif long_idx is None:
        triggered, trig_idx = "short", short_idx
    elif short_idx is None:
        triggered, trig_idx = "long", long_idx
    else:
        if long_idx < short_idx:
            triggered, trig_idx = "long", long_idx
        elif short_idx < long_idx:
            triggered, trig_idx = "short", short_idx
        else:
            triggered, trig_idx = "conflict", long_idx

    review = {
        "status": "scored",
        "date_et": date_et,
        "scored_at_et": scored_at,
        "triggered": triggered,
        "trigger_time_et": None,
        "filled": False,
        "fill_time_et": None,
        "be_armed": False,
        "be_armed_time_et": None,
        "exit": "no_trigger",
        "exit_price": None,
        "exit_time_et": None,
        "max_favorable_R": 0.0,
        "max_adverse_R": 0.0,
        "R": 0.0,
    }

    if triggered in ("none", "conflict"):
        review["exit"] = "no_trigger" if triggered == "none" else "conflict"
        review["daily_result"] = review["exit"] if triggered == "none" else "conflict"
        return write_and_return(triggered, review)

    trigger_open = datetime.fromtimestamp(candles[trig_idx]["t_open_ms"]/1000, tz=timezone.utc).astimezone(ET)
    review["trigger_time_et"] = (trigger_open + timedelta(minutes=15)).strftime("%Y-%m-%d %H:%M")  # close of trigger candle

    # Fill rules (treat entry as STOP after trigger): fill must occur AFTER trigger close, BEFORE expiry.
    fill_idx = None
    for k in range(trig_idx + 1, len(candles)):
        c = candles[k]
        t_open = datetime.fromtimestamp(c["t_open_ms"]/1000, tz=timezone.utc).astimezone(ET)
        if t_open >= expiry_et:
            break

        if triggered == "long":
            if c["high"] >= long_entry:
                fill_idx = k
                break
        else:  # short
            if c["low"] <= short_entry:
                fill_idx = k
                break

    if fill_idx is None:
        review["exit"] = "armed_not_filled"
        review["daily_result"] = f"{triggered}:armed_not_filled"
        return write_and_return("armed_not_filled", review)

    fill_open = datetime.fromtimestamp(candles[fill_idx]["t_open_ms"]/1000, tz=timezone.utc).astimezone(ET)
    review["filled"] = True
    review["fill_time_et"] = fill_open.strftime("%Y-%m-%d %H:%M")

    # simulate from fill candle through expiry
    risk_long = abs(long_entry - long_stop) if long_entry != long_stop else 1.0
    risk_short = abs(short_stop - short_entry) if short_stop != short_entry else 1.0

    max_fav_R = 0.0
    max_adv_R = 0.0

    exit_reason = None
    exit_price = None
    exit_time_et = None

    be_level = 0.5  # arm BE when 0.5R reached on CLOSE; effective next candle
    be_effective_from_idx = None
    cur_stop_long = long_stop
    cur_stop_short = short_stop

    for idx in range(fill_idx, len(candles)):
        c = candles[idx]
        t_open = datetime.fromtimestamp(c["t_open_ms"]/1000, tz=timezone.utc).astimezone(ET)
        if t_open >= expiry_et:
            break

        hi, lo, close = c["high"], c["low"], c["close"]

        # If BE was armed earlier, apply it now
        if be_effective_from_idx is not None and idx >= be_effective_from_idx:
            if triggered == "long":
                cur_stop_long = max(cur_stop_long, long_entry)
            else:
                cur_stop_short = min(cur_stop_short, short_entry)

        if triggered == "long":
            max_fav_R = max(max_fav_R, (hi - long_entry) / risk_long)
            max_adv_R = max(max_adv_R, (long_entry - lo) / risk_long)

            # Check stop/TP (conservative: stop wins if ambiguous)
            stop_hit = lo <= cur_stop_long
            tp_hit = None
            for tp in sorted(long_tps):
                if hi >= tp:
                    tp_hit = tp

            if stop_hit and tp_hit is not None:
                exit_reason = "ambiguous_stop_and_tp_same_candle"
                exit_price = cur_stop_long
                exit_time_et = t_open.strftime("%Y-%m-%d %H:%M")
                break
            if stop_hit:
                exit_reason = "stopped_be" if abs(cur_stop_long - long_entry) < 1e-9 else "stopped"
                exit_price = cur_stop_long
                exit_time_et = t_open.strftime("%Y-%m-%d %H:%M")
                break
            if tp_hit is not None:
                exit_reason = f"tp_hit_{tp_hit}"
                exit_price = tp_hit
                exit_time_et = t_open.strftime("%Y-%m-%d %H:%M")
                break

            # Arm BE on CLOSE; effective next candle
            if be_effective_from_idx is None:
                if close >= (long_entry + be_level * risk_long):
                    review["be_armed"] = True
                    review["be_armed_time_et"] = (t_open + timedelta(minutes=15)).strftime("%Y-%m-%d %H:%M")
                    be_effective_from_idx = idx + 1

        else:  # short
            max_fav_R = max(max_fav_R, (short_entry - lo) / risk_short)
            max_adv_R = max(max_adv_R, (hi - short_entry) / risk_short)

            stop_hit = hi >= cur_stop_short
            tp_hit = None
            for tp in sorted(short_tps, reverse=True):
                if lo <= tp:
                    tp_hit = tp

            if stop_hit and tp_hit is not None:
                exit_reason = "ambiguous_stop_and_tp_same_candle"
                exit_price = cur_stop_short
                exit_time_et = t_open.strftime("%Y-%m-%d %H:%M")
                break
            if stop_hit:
                exit_reason = "stopped_be" if abs(cur_stop_short - short_entry) < 1e-9 else "stopped"
                exit_price = cur_stop_short
                exit_time_et = t_open.strftime("%Y-%m-%d %H:%M")
                break
            if tp_hit is not None:
                exit_reason = f"tp_hit_{tp_hit}"
                exit_price = tp_hit
                exit_time_et = t_open.strftime("%Y-%m-%d %H:%M")
                break

            if be_effective_from_idx is None:
                if close <= (short_entry - be_level * risk_short):
                    review["be_armed"] = True
                    review["be_armed_time_et"] = (t_open + timedelta(minutes=15)).strftime("%Y-%m-%d %H:%M")
                    be_effective_from_idx = idx + 1

    # If no exit hit before expiry, settle at expiry using last close before expiry
    if exit_reason is None:
        exit_reason = "expired_close"
        exit_price = float(last_close) if last_close is not None else None
        exit_time_et = last_close_time_et

    R = 0.0
    if exit_price is not None:
        if triggered == "long":
            R = (exit_price - long_entry) / risk_long
        else:
            R = (short_entry - exit_price) / risk_short

    review.update({
        "exit": exit_reason,
        "exit_price": exit_price,
        "exit_time_et": exit_time_et,
        "max_favorable_R": round(max_fav_R, 3),
        "max_adverse_R": round(max_adv_R, 3),
        "R": round(R, 3),
    })
    review["daily_result"] = f"{triggered}:{exit_reason}"

    return write_and_return("ok", review)

def main():
    date_et = (os.getenv("DATE_ET") or "").strip()
    if not date_et:
        date_et = (datetime.now(tz=ET) - timedelta(days=1)).strftime("%Y-%m-%d")
    out = score(date_et)
    print(json.dumps(out, indent=2))

if __name__ == "__main__":
    main()
