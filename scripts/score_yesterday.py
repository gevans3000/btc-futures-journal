from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

ET = ZoneInfo("America/New_York")
UTC = timezone.utc

COINBASE_CANDLES = "https://api.exchange.coinbase.com/products/BTC-USD/candles"

def http_get_json(url: str, params: dict, timeout: int = 25):
    r = requests.get(url, params=params, timeout=timeout, headers={"User-Agent": "btc-journal-bot/1.0"})
    r.raise_for_status()
    return r.json()

def iso_z(dt: datetime) -> str:
    dt = dt.astimezone(UTC)
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")

def parse_last_float(s: str) -> float | None:
    if not isinstance(s, str):
        return None
    nums = re.findall(r"[-+]?\d+(?:\.\d+)?", s.replace(",", ""))
    if not nums:
        return None
    try:
        return float(nums[-1])
    except Exception:
        return None

@dataclass
class Candle:
    t: int       # start time, epoch seconds (UTC)
    low: float
    high: float
    open: float
    close: float
    volume: float

def fetch_5m_candles(start_utc: datetime, end_utc: datetime) -> list[Candle]:
    # Coinbase Exchange candles endpoint returns up to 300 rows; 24h @ 5m = 288 (fits).
    params = {"granularity": 300, "start": iso_z(start_utc), "end": iso_z(end_utc)}
    raw = http_get_json(COINBASE_CANDLES, params=params)
    out: list[Candle] = []
    for row in raw or []:
        try:
            t, low, high, op, close, vol = row
            out.append(Candle(int(t), float(low), float(high), float(op), float(close), float(vol)))
        except Exception:
            continue
    out.sort(key=lambda c: c.t)
    # filter strict window
    s = int(start_utc.timestamp())
    e = int(end_utc.timestamp())
    return [c for c in out if s <= c.t < e]

def build_15m_closes(candles: list[Candle], start_ts: int) -> list[tuple[int, float]]:
    # returns list of (close_time_ts, close_price) for 15m bars aligned to ET-midnight boundary (converted to UTC)
    if not candles:
        return []
    # align first index to start_ts on 5m grid
    i0 = None
    for i, c in enumerate(candles):
        if c.t >= start_ts and ((c.t - start_ts) % 300 == 0):
            i0 = i
            break
    if i0 is None:
        return []
    closes = []
    i = i0 + 2
    while i < len(candles):
        c3 = candles[i]
        # 15m close occurs at end of 3rd 5m candle
        close_time = c3.t + 300
        closes.append((close_time, c3.close))
        i += 3
    return closes

def find_trigger_time(closes15: list[tuple[int, float]], direction: str, trigger: float) -> int | None:
    if direction == "long":
        for t, px in closes15:
            if px >= trigger:
                return t
    else:
        for t, px in closes15:
            if px <= trigger:
                return t
    return None

def find_fill_time(candles: list[Candle], after_ts: int, direction: str, entry: float) -> int | None:
    for c in candles:
        if c.t < after_ts:
            continue
        if direction == "long" and c.high >= entry:
            return c.t
        if direction == "short" and c.low <= entry:
            return c.t
    return None

def simulate_exit(candles: list[Candle], fill_ts: int, direction: str, entry: float, stop: float, tp1: float) -> tuple[str, int, float]:
    # Conservative: if both stop and tp hit in same candle, count stop first.
    last_close_ts = None
    last_close_px = None
    for c in candles:
        if c.t < fill_ts:
            continue
        last_close_ts = c.t + 300
        last_close_px = c.close

        if direction == "long":
            if c.low <= stop:
                return ("SL", c.t, stop)
            if c.high >= tp1:
                return ("TP1", c.t, tp1)
        else:
            if c.high >= stop:
                return ("SL", c.t, stop)
            if c.low <= tp1:
                return ("TP1", c.t, tp1)

    # If nothing hit, close EOD at last close
    if last_close_ts is not None and last_close_px is not None:
        return ("EOD_CLOSE", last_close_ts, float(last_close_px))
    return ("NO_DATA", fill_ts, entry)

def r_multiple(direction: str, entry: float, stop: float, exit_px: float) -> float | None:
    risk = (entry - stop) if direction == "long" else (stop - entry)
    if risk <= 0:
        return None
    pnl = (exit_px - entry) if direction == "long" else (entry - exit_px)
    return pnl / risk

def path_for_date(d: datetime) -> str:
    return os.path.join("journal", d.strftime("%Y"), f"{d.strftime('%Y-%m-%d')}.json")

def load_json(path: str) -> dict | None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            x = json.load(f)
        return x if isinstance(x, dict) else None
    except Exception:
        return None

def save_json(path: str, obj: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, sort_keys=True)
        f.write("\n")

def fmt_et(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=UTC).astimezone(ET).strftime("%Y-%m-%d %H:%M:%S")

def main() -> int:
    now_et = datetime.now(tz=ET)

    # score the PRIOR calendar day in ET
    y_et = (now_et - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    y_end_et = y_et + timedelta(days=1)

    y_path = path_for_date(y_et)
    data = load_json(y_path)
    if not data:
        print(f"No yesterday file to score: {y_path}")
        return 0

    pr = data.get("prior_day_review")
    if isinstance(pr, dict) and pr.get("status") == "scored":
        print(f"Already scored: {y_path}")
        return 0

    ptt = data.get("paper_test_trade") or {}
    if not isinstance(ptt, dict):
        print(f"No paper_test_trade found in: {y_path}")
        return 0

    long = ptt.get("long") or {}
    short = ptt.get("short") or {}

    lt = parse_last_float(long.get("trigger", ""))
    st = parse_last_float(short.get("trigger", ""))
    le = float(long.get("entry"))
    ls = float(long.get("stop"))
    ltp1 = float((long.get("tps") or [None])[0])
    se = float(short.get("entry"))
    ss = float(short.get("stop"))
    stp1 = float((short.get("tps") or [None])[0])

    if lt is None or st is None:
        print(f"Could not parse trigger prices in: {y_path}")
        return 0

    start_utc = y_et.astimezone(UTC)
    end_utc = y_end_et.astimezone(UTC)
    candles = fetch_5m_candles(start_utc, end_utc)
    if len(candles) < 50:
        print("Not enough candles; skipping.")
        return 0

    closes15 = build_15m_closes(candles, start_ts=int(start_utc.timestamp()))
    t_long = find_trigger_time(closes15, "long", lt)
    t_short = find_trigger_time(closes15, "short", st)

    if t_long is None and t_short is None:
        result = {
            "status": "scored",
            "reviewed_at_et": now_et.strftime("%Y-%m-%d %H:%M:%S"),
            "market": "BTC-USD",
            "granularity_sec": 300,
            "day_scored_et": y_et.strftime("%Y-%m-%d"),
            "outcome": "NO_TRIGGER",
        }
        data["prior_day_review"] = result
        save_json(y_path, data)
        print(f"Scored: {y_path} => NO_TRIGGER")
        return 0

    # Choose earliest trigger
    choice = None
    if t_long is not None and (t_short is None or t_long < t_short):
        choice = ("long", t_long, le, ls, ltp1)
    elif t_short is not None and (t_long is None or t_short < t_long):
        choice = ("short", t_short, se, ss, stp1)
    else:
        # tie or both: pick long by default but record both
        choice = ("long", t_long or t_short, le, ls, ltp1)

    direction, trigger_ts, entry, stop, tp1 = choice

    fill_ts = find_fill_time(candles, after_ts=trigger_ts, direction=direction, entry=entry)
    if fill_ts is None:
        result = {
            "status": "scored",
            "reviewed_at_et": now_et.strftime("%Y-%m-%d %H:%M:%S"),
            "market": "BTC-USD",
            "granularity_sec": 300,
            "day_scored_et": y_et.strftime("%Y-%m-%d"),
            "direction": direction,
            "outcome": "TRIGGER_NO_FILL",
            "trigger_time_et": fmt_et(trigger_ts),
            "entry": entry,
            "stop": stop,
            "tp1": tp1,
        }
        data["prior_day_review"] = result
        save_json(y_path, data)
        print(f"Scored: {y_path} => TRIGGER_NO_FILL ({direction})")
        return 0

    outcome, exit_ts, exit_px = simulate_exit(candles, fill_ts, direction, entry, stop, tp1)
    r = r_multiple(direction, entry, stop, exit_px)

    result = {
        "status": "scored",
        "reviewed_at_et": now_et.strftime("%Y-%m-%d %H:%M:%S"),
        "market": "BTC-USD",
        "granularity_sec": 300,
        "day_scored_et": y_et.strftime("%Y-%m-%d"),
        "direction": direction,
        "outcome": outcome,
        "trigger_time_et": fmt_et(trigger_ts),
        "fill_time_et": fmt_et(fill_ts),
        "exit_time_et": fmt_et(exit_ts),
        "entry": entry,
        "stop": stop,
        "tp1": tp1,
        "exit": exit_px,
        "r_multiple": None if r is None else round(float(r), 4),
        "assumption": "Conservative: if SL and TP hit in same 5m candle, counts SL first.",
    }

    data["prior_day_review"] = result
    save_json(y_path, data)
    print(f"Scored: {y_path} => {direction.upper()} {outcome} (R={result['r_multiple']})")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
