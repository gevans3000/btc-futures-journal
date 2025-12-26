from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

ET = ZoneInfo("America/New_York")

KRAKEN_OHLC = "https://api.kraken.com/0/public/OHLC"
PAIR = "XBTUSD"  # Kraken spot pair (proxy for BTC)

def iso_et(dt: datetime) -> str:
    return dt.astimezone(ET).strftime("%Y-%m-%d %H:%M:%S ET")

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

def journal_path_for(date_et) -> str:
    y = date_et.strftime("%Y")
    d = date_et.strftime("%Y-%m-%d")
    return os.path.join("journal", y, f"{d}.json")

def fetch_ohlc(interval_min: int, since_unix: int) -> list[dict]:
    # Kraken returns: result: { <pairKey>: [[time, open, high, low, close, vwap, volume, count], ...], "last": <unix> }
    params = {"pair": PAIR, "interval": interval_min, "since": since_unix}
    r = requests.get(KRAKEN_OHLC, params=params, timeout=25, headers={"User-Agent": "btc-journal-bot/1.0"})
    r.raise_for_status()
    j = r.json()
    if j.get("error"):
        raise RuntimeError(f"Kraken error: {j['error']}")
    res = j.get("result", {}) or {}
    keys = [k for k in res.keys() if k != "last"]
    if not keys:
        return []
    rows = res[keys[0]]
    out = []
    for row in rows:
        # time, open, high, low, close, vwap, volume, count
        out.append({
            "t": int(row[0]),
            "o": float(row[1]),
            "h": float(row[2]),
            "l": float(row[3]),
            "c": float(row[4]),
        })
    return out

_num = re.compile(r"([-+]?\d+(\.\d+)?)")

def parse_trigger_threshold(s: str) -> float:
    # examples: "15m close >= 12345.67" or "15m close <= 12345.67"
    m = _num.findall(s or "")
    if not m:
        raise ValueError(f"Cannot parse trigger threshold from: {s}")
    return float(m[-1][0])

@dataclass
class SidePlan:
    trigger: str
    entry: float
    stop: float
    tps: list[float]

@dataclass
class Score:
    status: str
    chosen_side: str
    trigger_time_et: str | None
    entry: float | None
    stop: float | None
    exit_price: float | None
    exit_time_et: str | None
    outcome: str
    R: float

def first_trigger_time_15m(candles_15m: list[dict], start_unix: int, end_unix: int, op: str, thresh: float) -> int | None:
    # treat trigger at candle close time = candle start + interval
    interval = 15 * 60
    for c in candles_15m:
        t0 = c["t"]
        t_close = t0 + interval
        if t_close < start_unix or t_close > end_unix:
            continue
        close = c["c"]
        if op == ">=" and close >= thresh:
            return t_close
        if op == "<=" and close <= thresh:
            return t_close
    return None

def score_path(side: str, plan: SidePlan, candles_5m: list[dict], start_unix: int, end_unix: int) -> tuple[str, float, int | None, float | None]:
    # returns (outcome, R, exit_time_unix, exit_price)
    entry = plan.entry
    stop = plan.stop
    tps = plan.tps[:] if plan.tps else []

    risk = abs(entry - stop)
    if risk <= 0:
        return ("invalid_risk", 0.0, None, None)

    # Scan forward and pick the earliest decisive event.
    for c in candles_5m:
        t0 = c["t"]
        if t0 < start_unix or t0 > end_unix:
            continue
        hi = c["h"]
        lo = c["l"]

        if side == "long":
            stop_hit = lo <= stop
            tp_hits = [tp for tp in tps if hi >= tp]
            if stop_hit and tp_hits:
                # ambiguous intrabar -> conservative
                return ("ambiguous_stop_vs_tp", -1.0, t0, stop)
            if stop_hit:
                return ("stopped", -1.0, t0, stop)
            if tp_hits:
                best = max(tp_hits)
                R = (best - entry) / (entry - stop)
                label = "tp" + str(tps.index(best) + 1) if best in tps else "tp"
                return (label, float(R), t0, best)

        else:  # short
            stop_hit = hi >= stop
            tp_hits = [tp for tp in tps if lo <= tp]
            if stop_hit and tp_hits:
                return ("ambiguous_stop_vs_tp", -1.0, t0, stop)
            if stop_hit:
                return ("stopped", -1.0, t0, stop)
            if tp_hits:
                best = min(tp_hits)  # lowest reached
                R = (entry - best) / (stop - entry)
                label = "tp" + str(tps.index(best) + 1) if best in tps else "tp"
                return (label, float(R), t0, best)

    # nothing hit by end
    return ("open_end", 0.0, None, None)

def main() -> int:
    now_et = datetime.now(tz=ET)

    # Score "yesterday 06:00 ET -> today 06:00 ET"
    today_0600 = now_et.replace(hour=6, minute=0, second=0, microsecond=0)
    if now_et < today_0600:
        today_0600 = today_0600 - timedelta(days=1)  # if run before 06:00 ET, treat as previous day boundary
    start = today_0600 - timedelta(days=1)
    end = today_0600

    y_path = journal_path_for(start)
    data = load_json(y_path)
    if not data:
        print(f"Skip (no file): {y_path}")
        return 0

    if isinstance(data.get("paper_test_trade_autoscore"), dict) and data["paper_test_trade_autoscore"].get("status") == "scored":
        print(f"Already scored: {y_path}")
        return 0

    ptt = data.get("paper_test_trade") or {}
    long_raw = (ptt.get("long") or {})
    short_raw = (ptt.get("short") or {})

    try:
        long_plan = SidePlan(
            trigger=str(long_raw.get("trigger") or ""),
            entry=float(long_raw.get("entry")),
            stop=float(long_raw.get("stop")),
            tps=[float(x) for x in (long_raw.get("tps") or [])],
        )
        short_plan = SidePlan(
            trigger=str(short_raw.get("trigger") or ""),
            entry=float(short_raw.get("entry")),
            stop=float(short_raw.get("stop")),
            tps=[float(x) for x in (short_raw.get("tps") or [])],
        )
    except Exception as e:
        data["paper_test_trade_autoscore"] = {
            "status": "error",
            "error": f"Missing/invalid paper_test_trade fields: {e}",
        }
        save_json(y_path, data)
        print(f"Wrote error autoscore to: {y_path}")
        return 0

    start_unix = int(start.astimezone(timezone.utc).timestamp())
    end_unix = int(end.astimezone(timezone.utc).timestamp())

    # Fetch candles once
    c15 = fetch_ohlc(15, start_unix - 3600)
    c5  = fetch_ohlc(5,  start_unix - 3600)

    # Parse triggers
    lt = parse_trigger_threshold(long_plan.trigger)
    st = parse_trigger_threshold(short_plan.trigger)

    long_t = first_trigger_time_15m(c15, start_unix, end_unix, ">=", lt)
    short_t = first_trigger_time_15m(c15, start_unix, end_unix, "<=", st)

    chosen = "none"
    trig_unix = None
    plan = None

    if long_t and short_t:
        if long_t == short_t:
            chosen = "none"
        elif long_t < short_t:
            chosen, trig_unix, plan = "long", long_t, long_plan
        else:
            chosen, trig_unix, plan = "short", short_t, short_plan
    elif long_t:
        chosen, trig_unix, plan = "long", long_t, long_plan
    elif short_t:
        chosen, trig_unix, plan = "short", short_t, short_plan

    if chosen == "none":
        score = Score(
            status="scored",
            chosen_side="none",
            trigger_time_et=None,
            entry=None,
            stop=None,
            exit_price=None,
            exit_time_et=None,
            outcome="no_trigger_or_ambiguous",
            R=0.0,
        )
    else:
        outcome, R, exit_t, exit_px = score_path(chosen, plan, c5, trig_unix, end_unix)
        score = Score(
            status="scored",
            chosen_side=chosen,
            trigger_time_et=iso_et(datetime.fromtimestamp(trig_unix, tz=timezone.utc)),
            entry=plan.entry,
            stop=plan.stop,
            exit_price=exit_px,
            exit_time_et=iso_et(datetime.fromtimestamp(exit_t, tz=timezone.utc)) if exit_t else None,
            outcome=outcome,
            R=float(R),
        )

    data["paper_test_trade_autoscore"] = {
        "status": score.status,
        "window_et": {"start": iso_et(start), "end": iso_et(end)},
        "data_source": {"provider": "kraken", "endpoint": "/public/OHLC", "pair": PAIR, "intervals_min": [5, 15]},
        "chosen_side": score.chosen_side,
        "trigger_time_et": score.trigger_time_et,
        "entry": score.entry,
        "stop": score.stop,
        "exit_price": score.exit_price,
        "exit_time_et": score.exit_time_et,
        "outcome": score.outcome,
        "R": score.R,
        "autoscore_version": "v1",
    }

    save_json(y_path, data)
    print(f"Scored + updated: {y_path}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
