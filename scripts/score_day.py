from __future__ import annotations

import json, os, re
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

def _dt_open_et(candle: dict) -> datetime:
    return datetime.fromtimestamp(candle["t_open_ms"] / 1000, tz=timezone.utc).astimezone(ET)

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

    # required prices
    try:
        long_entry = float(long["entry"]); long_stop = float(long["stop"])
        short_entry = float(short["entry"]); short_stop = float(short["stop"])
    except Exception:
        j["daily_result"] = "bad_trade_fields"
        j["daily_R"] = 0.0
        j["paper_test_trade_review"] = {"status": "bad_trade_fields"}
        save_json(path, j)
        return {"status": "bad_trade_fields", "path": path}

    long_tps = [float(x) for x in (long.get("tps") or [])]
    short_tps = [float(x) for x in (short.get("tps") or [])]

    candles = fetch_15m_binance(date_et)
    if not candles:
        j["daily_result"] = "no_candles"
        j["daily_R"] = 0.0
        j["paper_test_trade_review"] = {"status": "no_candles"}
        save_json(path, j)
        return {"status": "no_candles", "path": path}

    # earliest trigger (by candle close rule)
    triggered = "none"
    trig_idx = None
    for i, c in enumerate(candles):
        long_hit = c["close"] >= lt[1]
        short_hit = c["close"] <= st[1]
        if long_hit or short_hit:
            trig_idx = i
            if long_hit and short_hit:
                triggered = "conflict"
            else:
                triggered = "long" if long_hit else "short"
            break

    scored_at = datetime.now(tz=ET).strftime("%Y-%m-%d %H:%M:%S")
    review = {
        "status": "scored",
        "date_et": date_et,
        "scored_at_et": scored_at,
        "triggered": triggered,
        "trigger_time_et": None,
        "filled": False,
        "fill_time_et": None,
        "exit": "no_trigger",
        "exit_price": None,
        "exit_time_et": None,
        "max_favorable_R": 0.0,
        "max_adverse_R": 0.0,
        "R": 0.0,
    }

    if triggered in ("none", "conflict"):
        review["exit"] = "no_trigger" if triggered == "none" else "conflict"
        j["paper_test_trade_review"] = review
        j["daily_result"] = "no_trigger" if triggered == "none" else "conflict"
        j["daily_R"] = 0.0
        save_json(path, j)
        return {"status": triggered, "path": path}

    # trigger time = candle close time (open + 15m)
    trig_open = _dt_open_et(candles[trig_idx])
    review["trigger_time_et"] = (trig_open + timedelta(minutes=15)).strftime("%Y-%m-%d %H:%M")

    # require entry fill AFTER trigger (OCO conditional arms, then entry must trade)
    fill_idx = None
    for k in range(trig_idx + 1, len(candles)):
        c = candles[k]
        if triggered == "long":
            if c["high"] >= long_entry:
                fill_idx = k
                break
        else:
            if c["low"] <= short_entry:
                fill_idx = k
                break

    if fill_idx is None:
        review["exit"] = "armed_not_filled"
        j["paper_test_trade_review"] = review
        j["daily_result"] = f"{triggered}:armed_not_filled"
        j["daily_R"] = 0.0
        save_json(path, j)
        return {"status": "armed_not_filled", "path": path}

    review["filled"] = True
    review["fill_time_et"] = _dt_open_et(candles[fill_idx]).strftime("%Y-%m-%d %H:%M")

    # risk (avoid div by zero)
    risk_long = abs(long_entry - long_stop)
    risk_short = abs(short_stop - short_entry)
    if risk_long == 0 or risk_short == 0:
        review["exit"] = "bad_risk_zero"
        j["paper_test_trade_review"] = review
        j["daily_result"] = "bad_risk_zero"
        j["daily_R"] = 0.0
        save_json(path, j)
        return {"status": "bad_risk_zero", "path": path}

    max_fav_R = 0.0
    max_adv_R = 0.0
    exit_reason = None
    exit_price = None
    exit_time_et = None

    # simulate from fill candle forward
    for c in candles[fill_idx:]:
        t_open = _dt_open_et(c)
        hi, lo, cl = c["high"], c["low"], c["close"]

        if triggered == "long":
            max_fav_R = max(max_fav_R, (hi - long_entry) / risk_long)
            max_adv_R = max(max_adv_R, (long_entry - lo) / risk_long)

            stop_hit = lo <= long_stop
            tp_hit = None
            for tp in sorted(long_tps):
                if hi >= tp:
                    tp_hit = tp
                    break  # first TP only (conservative)

            if stop_hit and tp_hit is not None:
                exit_reason = "ambiguous_stop_and_tp_same_candle"
                exit_price = long_stop
                exit_time_et = t_open.strftime("%Y-%m-%d %H:%M")
                break
            if stop_hit:
                exit_reason = "stopped"
                exit_price = long_stop
                exit_time_et = t_open.strftime("%Y-%m-%d %H:%M")
                break
            if tp_hit is not None:
                exit_reason = f"tp_hit_{tp_hit}"
                exit_price = tp_hit
                exit_time_et = t_open.strftime("%Y-%m-%d %H:%M")
                break

        else:  # short
            max_fav_R = max(max_fav_R, (short_entry - lo) / risk_short)
            max_adv_R = max(max_adv_R, (hi - short_entry) / risk_short)

            stop_hit = hi >= short_stop
            tp_hit = None
            for tp in sorted(short_tps, reverse=True):
                if lo <= tp:
                    tp_hit = tp
                    break  # first TP only (conservative)

            if stop_hit and tp_hit is not None:
                exit_reason = "ambiguous_stop_and_tp_same_candle"
                exit_price = short_stop
                exit_time_et = t_open.strftime("%Y-%m-%d %H:%M")
                break
            if stop_hit:
                exit_reason = "stopped"
                exit_price = short_stop
                exit_time_et = t_open.strftime("%Y-%m-%d %H:%M")
                break
            if tp_hit is not None:
                exit_reason = f"tp_hit_{tp_hit}"
                exit_price = tp_hit
                exit_time_et = t_open.strftime("%Y-%m-%d %H:%M")
                break

    # expiry-close if nothing hit
    if exit_price is None:
        last = candles[-1]
        last_open = _dt_open_et(last)
        exit_reason = "expired_close"
        exit_price = float(last["close"])
        exit_time_et = (last_open + timedelta(minutes=15)).strftime("%Y-%m-%d %H:%M")

    # realized R
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

