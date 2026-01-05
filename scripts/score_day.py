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

def fetch_15m_binance_vision(date_et: str) -> list[dict]:
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

def _fmt(dt: datetime | None) -> str | None:
    return None if dt is None else dt.strftime("%Y-%m-%d %H:%M")

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

    if not lt or not st:
        j["daily_result"] = "no_trigger_fields"
        j["daily_R"] = 0.0
        j["paper_test_trade_review"] = {"status": "no_trigger_fields"}
        save_json(path, j)
        return {"status": "no_trigger_fields", "path": path}

    long_entry = float(long["entry"]); long_stop = float(long["stop"])
    long_tps = [float(x) for x in (long.get("tps") or [])]
    short_entry = float(short["entry"]); short_stop = float(short["stop"])
    short_tps = [float(x) for x in (short.get("tps") or [])]

    candles = fetch_15m_binance_vision(date_et)
    if not candles:
        j["daily_result"] = "no_candles"
        j["daily_R"] = 0.0
        j["paper_test_trade_review"] = {"status": "no_candles"}
        save_json(path, j)
        return {"status": "no_candles", "path": path}

    # earliest trigger by 15m CLOSE
    long_idx = None
    short_idx = None
    for i, c in enumerate(candles):
        if long_idx is None and c["close"] >= lt[1]:
            long_idx = i
        if short_idx is None and c["close"] <= st[1]:
            short_idx = i

    if long_idx is None and short_idx is None:
        triggered, trig_idx = "none", None
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

    scored_at = datetime.now(tz=ET)
    trig_time = None
    if trig_idx is not None:
        trig_open = datetime.fromtimestamp(candles[trig_idx]["t_open_ms"]/1000, tz=timezone.utc).astimezone(ET)
        trig_time = trig_open + timedelta(minutes=15)  # candle close

    # Conservative BE: arm on candle CLOSE; effective from NEXT candle
    try:
        be_at_r = float((os.getenv("BE_AT_R") or "0.5").strip())
    except Exception:
        be_at_r = 0.5
    if be_at_r < 0:
        be_at_r = 0.0

    review = {
        "status": "scored",
        "date_et": date_et,
        "scored_at_et": scored_at.strftime("%Y-%m-%d %H:%M:%S"),
        "triggered": triggered,
        "trigger_time_et": _fmt(trig_time),
        "filled": False,
        "fill_time_et": None,
        "exit": "no_trigger",
        "exit_price": None,
        "exit_time_et": None,
        "R": 0.0,
        "max_favorable_R": 0.0,
        "max_adverse_R": 0.0,
        "management": (f"be_after_{be_at_r}R_next_candle" if be_at_r > 0 else "none"),
    }

    if triggered in ("none", "conflict"):
        j["daily_result"] = "no_trigger" if triggered == "none" else "conflict"
        j["daily_R"] = 0.0
        review["exit"] = j["daily_result"]
        j["paper_test_trade_review"] = review
        save_json(path, j)
        return {"status": triggered, "path": path}

    # Fill must occur AFTER trigger candle close => start scanning at trig_idx+1
    fill_idx = None
    fill_time = None
    scan_start = (trig_idx + 1) if trig_idx is not None else 0

    for k in range(scan_start, len(candles)):
        c = candles[k]
        t_open = datetime.fromtimestamp(c["t_open_ms"]/1000, tz=timezone.utc).astimezone(ET)
        if triggered == "long":
            if c["high"] >= long_entry:
                fill_idx = k
                fill_time = t_open
                break
        else:
            if c["low"] <= short_entry:
                fill_idx = k
                fill_time = t_open
                break

    if fill_idx is None:
        review["exit"] = "armed_not_filled"
        j["daily_result"] = f"{triggered}:armed_not_filled"
        j["daily_R"] = 0.0
        j["paper_test_trade_review"] = review
        save_json(path, j)
        return {"status": "armed_not_filled", "path": path}

    review["filled"] = True
    review["fill_time_et"] = _fmt(fill_time)

    risk_long = abs(long_entry - long_stop) or 1.0
    risk_short = abs(short_stop - short_entry) or 1.0

    max_fav_R = 0.0
    max_adv_R = 0.0

    exit_reason = "open"
    exit_price = None
    exit_time = None

    be_effective_from = None  # candle index from which stop=entry is used

    y, m, d = [int(x) for x in date_et.split("-")]
    expiry = datetime(y, m, d, 17, 0, tzinfo=ET)

    last_close = None
    last_close_time = None

    for k in range(fill_idx, len(candles)):
        c = candles[k]
        t_open = datetime.fromtimestamp(c["t_open_ms"]/1000, tz=timezone.utc).astimezone(ET)
        if t_open >= expiry:
            break

        hi, lo, cl = c["high"], c["low"], c["close"]
        last_close = cl
        last_close_time = t_open + timedelta(minutes=15)

        if triggered == "long":
            risk = risk_long
            max_fav_R = max(max_fav_R, (hi - long_entry) / risk)
            max_adv_R = max(max_adv_R, (long_entry - lo) / risk)

            # current stop (BE only effective from next candle after arming)
            cur_stop = long_entry if (be_effective_from is not None and k >= be_effective_from) else long_stop

            # stop/TP checks (intrabar ambiguity -> assume stop first)
            stop_hit = lo <= cur_stop
            tp_hit = None
            for tp in sorted(long_tps):
                if hi >= tp:
                    tp_hit = tp

            if stop_hit and tp_hit is not None:
                exit_reason = "ambiguous_stop_and_tp_same_candle"
                exit_price = cur_stop
                exit_time = t_open
                break
            if stop_hit:
                exit_reason = "stopped_be" if cur_stop == long_entry else "stopped"
                exit_price = cur_stop
                exit_time = t_open
                break
            if tp_hit is not None:
                exit_reason = f"tp_hit_{tp_hit}"
                exit_price = tp_hit
                exit_time = t_open
                break

            # Arm BE on candle CLOSE, effective next candle
            if be_at_r > 0 and be_effective_from is None and k > fill_idx:
                be_level = long_entry + be_at_r * risk
                if cl >= be_level:
                    be_effective_from = k + 1

        else:
            risk = risk_short
            max_fav_R = max(max_fav_R, (short_entry - lo) / risk)
            max_adv_R = max(max_adv_R, (hi - short_entry) / risk)

            cur_stop = short_entry if (be_effective_from is not None and k >= be_effective_from) else short_stop

            stop_hit = hi >= cur_stop
            tp_hit = None
            for tp in sorted(short_tps, reverse=True):
                if lo <= tp:
                    tp_hit = tp

            if stop_hit and tp_hit is not None:
                exit_reason = "ambiguous_stop_and_tp_same_candle"
                exit_price = cur_stop
                exit_time = t_open
                break
            if stop_hit:
                exit_reason = "stopped_be" if cur_stop == short_entry else "stopped"
                exit_price = cur_stop
                exit_time = t_open
                break
            if tp_hit is not None:
                exit_reason = f"tp_hit_{tp_hit}"
                exit_price = tp_hit
                exit_time = t_open
                break

            if be_at_r > 0 and be_effective_from is None and k > fill_idx:
                be_level = short_entry - be_at_r * risk
                if cl <= be_level:
                    be_effective_from = k + 1

    if exit_price is None and last_close is not None:
        exit_reason = "expired_close"
        exit_price = float(last_close)
        exit_time = last_close_time if last_close_time is not None else expiry

    R = 0.0
    if exit_price is not None:
        if triggered == "long":
            R = (exit_price - long_entry) / risk_long
        else:
            R = (short_entry - exit_price) / risk_short

    review.update({
        "exit": exit_reason,
        "exit_price": exit_price,
        "exit_time_et": _fmt(exit_time),
        "max_favorable_R": round(max_fav_R, 3),
        "max_adverse_R": round(max_adv_R, 3),
        "R": round(R, 3),
    })

    j["paper_test_trade_review"] = review
    j["daily_result"] = f"{triggered}:{exit_reason}"
    j["daily_R"] = round(R, 3)
    save_json(path, j)
    return {"status": "ok", "path": path, "result": j["daily_result"], "R": j["daily_R"]}

def main():
    date_et = (os.getenv("DATE_ET") or "").strip()
    if not date_et:
        date_et = (datetime.now(tz=ET) - timedelta(days=1)).strftime("%Y-%m-%d")
    out = score(date_et)
    print(json.dumps(out, indent=2))

if __name__ == "__main__":
    main()
