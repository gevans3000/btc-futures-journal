from __future__ import annotations

import json, os, re, time
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

ET = ZoneInfo("America/New_York")
OKX_CANDLES = "https://www.okx.com/api/v5/market/candles"
INST_ID = os.getenv("OKX_INST_ID", "BTC-USDT-SWAP")
BAR = os.getenv("OKX_BAR", "15m")  # keep aligned with trigger language
FORCE_RESCORE = (os.getenv("FORCE_RESCORE", "").strip().lower() in {"1","true","yes","y"})

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

def journal_path_for(date_et: datetime) -> str:
    y = date_et.strftime("%Y")
    d = date_et.strftime("%Y-%m-%d")
    return os.path.join("journal", y, f"{d}.json")

def parse_trigger(trigger: str) -> tuple[str, float] | None:
    # expects e.g. "15m close >= 87362.71"
    m = re.search(r"(>=|<=)\s*([0-9]+(?:\.[0-9]+)?)", trigger or "")
    if not m:
        return None
    op = m.group(1)
    lvl = float(m.group(2))
    return op, lvl

@dataclass
class Candle:
    ts_ms: int
    o: float
    h: float
    l: float
    c: float

def fetch_okx_candles_range(start_ms: int, end_ms: int) -> list[Candle]:
    """
    OKX returns newest-first. We page backward using 'after' (timestamp cursor).
    We'll keep pulling until we've covered start_ms.
    """
    out: list[Candle] = []
    after = None
    loops = 0

    while loops < 12:
        loops += 1
        params = {"instId": INST_ID, "bar": BAR, "limit": "100"}
        if after is not None:
            params["after"] = str(after)

        r = requests.get(OKX_CANDLES, params=params, timeout=20, headers={"User-Agent":"btc-journal-bot/1.0"})
        r.raise_for_status()
        payload = r.json()
        rows = payload.get("data") or []
        if not rows:
            break

        # rows are arrays: [ts, o, h, l, c, vol, ...]
        oldest_in_page = int(rows[-1][0])
        for row in rows:
            ts = int(row[0])
            if start_ms <= ts <= end_ms:
                out.append(Candle(ts, float(row[1]), float(row[2]), float(row[3]), float(row[4])))

        # if we've reached older than start, we can stop
        if oldest_in_page <= start_ms:
            break

        after = oldest_in_page
        time.sleep(0.15)

    # unique + sort ascending
    uniq = {c.ts_ms: c for c in out}
    return [uniq[k] for k in sorted(uniq.keys())]

def r_multiple(side: str, entry: float, stop: float, price: float) -> float:
    if side == "long":
        risk = entry - stop
        if risk <= 0:
            return 0.0
        return (price - entry) / risk
    else:
        risk = stop - entry
        if risk <= 0:
            return 0.0
        return (entry - price) / risk

def score_day(date_str: str) -> tuple[dict | None, str]:
    # date_str in YYYY-MM-DD (ET)
    date_et = datetime.fromisoformat(date_str).replace(tzinfo=ET)
    path = journal_path_for(date_et)

    data = load_json(path)
    if not data:
        return None, f"SKIP: missing or unreadable: {path}"

    if (not FORCE_RESCORE) and isinstance(data.get("auto_score"), dict) and data["auto_score"].get("status") == "scored":
        return None, f"SKIP: already scored: {path}"

    t = (data.get("paper_test_trade") or {})
    long_plan = (t.get("long") or {})
    short_plan = (t.get("short") or {})

    long_tr = parse_trigger(str(long_plan.get("trigger","")))
    short_tr = parse_trigger(str(short_plan.get("trigger","")))

    if not long_tr or not short_tr:
        data["auto_score"] = {"status":"error", "reason":"missing/invalid triggers"}
        data["daily_result"] = "error: invalid triggers"
        data["daily_R"] = 0.0
        save_json(path, data)
        return data, f"WROTE: error (invalid triggers): {path}"

    long_op, long_lvl = long_tr
    short_op, short_lvl = short_tr

    # Score window: 06:00 ET -> 23:59 ET for that date
    start_et = date_et.replace(hour=6, minute=0, second=0, microsecond=0)
    end_et   = date_et.replace(hour=23, minute=59, second=0, microsecond=0)

    start_ms = int(start_et.astimezone(ZoneInfo("UTC")).timestamp() * 1000)
    end_ms   = int(end_et.astimezone(ZoneInfo("UTC")).timestamp() * 1000)

    candles = fetch_okx_candles_range(start_ms, end_ms)
    if len(candles) < 10:
        data["auto_score"] = {"status":"insufficient_data", "bars":len(candles), "instId":INST_ID, "bar":BAR}
        data["daily_result"] = "pending: insufficient data"
        data["daily_R"] = 0.0
        save_json(path, data)
        return data, f"WROTE: insufficient data ({len(candles)} bars): {path}"

    # Find first trigger close
    def first_trigger(side: str) -> tuple[int,int] | None:
        # returns (index, ts_ms)
        if side == "long":
            lvl = long_lvl
            for i,c in enumerate(candles):
                if c.c >= lvl:  # close >= level
                    return i, c.ts_ms
        else:
            lvl = short_lvl
            for i,c in enumerate(candles):
                if c.c <= lvl:  # close <= level
                    return i, c.ts_ms
        return None

    lt = first_trigger("long")
    st = first_trigger("short")

    if not lt and not st:
        data["auto_score"] = {
            "status":"scored",
            "instId":INST_ID, "bar":BAR,
            "window_start_et": start_et.strftime("%Y-%m-%d %H:%M"),
            "window_end_et": end_et.strftime("%Y-%m-%d %H:%M"),
            "triggered": False,
        }
        data["daily_result"] = "no trigger"
        data["daily_R"] = 0.0
        save_json(path, data)
        return data, f"WROTE: no trigger: {path}"

    if lt and st:
        # pick earlier trigger candle time; tie => ambiguous
        if lt[1] < st[1]:
            side = "long"
            trig_i = lt[0]
        elif st[1] < lt[1]:
            side = "short"
            trig_i = st[0]
        else:
            data["auto_score"] = {"status":"scored","triggered":True,"result":"ambiguous (both triggered same bar)"}
            data["daily_result"] = "ambiguous trigger"
            data["daily_R"] = 0.0
            save_json(path, data)
            return data, f"WROTE: ambiguous trigger: {path}"
    else:
        side = "long" if lt else "short"
        trig_i = (lt[0] if lt else st[0])

    # After trigger, look for entry fill
    if side == "long":
        entry = float(long_plan["entry"])
        stop  = float(long_plan["stop"])
        tps   = [float(x) for x in (long_plan.get("tps") or [])]
    else:
        entry = float(short_plan["entry"])
        stop  = float(short_plan["stop"])
        tps   = [float(x) for x in (short_plan.get("tps") or [])]

    tp1 = tps[0] if len(tps) >= 1 else None
    tp2 = tps[1] if len(tps) >= 2 else None

    entry_i = None
    for i in range(trig_i, len(candles)):
        c = candles[i]
        if side == "long":
            if c.h >= entry:
                entry_i = i
                break
        else:
            if c.l <= entry:
                entry_i = i
                break

    if entry_i is None:
        data["auto_score"] = {
            "status":"scored", "instId":INST_ID, "bar":BAR,
            "window_start_et": start_et.strftime("%Y-%m-%d %H:%M"),
            "window_end_et": end_et.strftime("%Y-%m-%d %H:%M"),
            "triggered": True, "side": side,
            "result":"triggered_no_fill",
            "entry": entry, "stop": stop, "tps": tps,
        }
        data["daily_result"] = f"{side} triggered, no fill"
        data["daily_R"] = 0.0
        save_json(path, data)
        return data, f"WROTE: triggered_no_fill: {path}"

    # Simulate: 50% TP1, move stop->BE, 50% TP2; if still open EOD close
    realized_R = 0.0
    rem = 1.0
    stop_active = stop
    stage = 0  # 0=none, 1=TP1 done, 2=done
    exits = []

    def record_exit(label: str, price: float, frac: float, ts_ms: int):
        nonlocal realized_R, rem
        r = r_multiple(side, entry, stop, price) * frac
        realized_R += r
        rem -= frac
        exits.append({"label": label, "price": price, "frac": frac, "R": round(r, 4), "ts_ms": ts_ms})

    for i in range(entry_i, len(candles)):
        c = candles[i]

        # Conservative same-bar rule:
        # if stop is touched in a bar, assume it can stop you.
        if side == "long":
            if c.l <= stop_active:
                record_exit("stop", stop_active, rem, c.ts_ms)
                stage = 2
                break

            if stage == 0 and tp1 is not None and c.h >= tp1:
                record_exit("tp1", tp1, 0.5, c.ts_ms)
                stage = 1
                stop_active = entry  # move stop to BE for remainder

            if stage <= 1 and rem > 0 and tp2 is not None and c.h >= tp2:
                record_exit("tp2", tp2, rem, c.ts_ms)
                stage = 2
                break

        else:  # short
            if c.h >= stop_active:
                record_exit("stop", stop_active, rem, c.ts_ms)
                stage = 2
                break

            if stage == 0 and tp1 is not None and c.l <= tp1:
                record_exit("tp1", tp1, 0.5, c.ts_ms)
                stage = 1
                stop_active = entry  # move stop to BE

            if stage <= 1 and rem > 0 and tp2 is not None and c.l <= tp2:
                record_exit("tp2", tp2, rem, c.ts_ms)
                stage = 2
                break

    # If still open, close EOD at last close
    if rem > 0:
        last = candles[-1]
        record_exit("eod_close", last.c, rem, last.ts_ms)
        stage = 2

    # Label
    labels = [e["label"] for e in exits]
    if "stop" in labels and "tp1" not in labels:
        label = f"{side}: -1R (stop)"
    elif "tp2" in labels:
        label = f"{side}: TP2 (+{round(realized_R,3)}R)"
    elif "tp1" in labels:
        label = f"{side}: TP1 (+{round(realized_R,3)}R)"
    else:
        label = f"{side}: closed (+{round(realized_R,3)}R)"

    data["auto_score"] = {
        "status":"scored",
        "instId": INST_ID,
        "bar": BAR,
        "window_start_et": start_et.strftime("%Y-%m-%d %H:%M"),
        "window_end_et": end_et.strftime("%Y-%m-%d %H:%M"),
        "triggered": True,
        "side": side,
        "trigger_levels": {"long_close_ge": long_lvl, "short_close_le": short_lvl},
        "entry": entry,
        "stop": stop,
        "tps": tps,
        "exits": exits,
        "result_label": label,
        "R": round(realized_R, 4),
    }
    data["daily_result"] = label
    data["daily_R"] = round(realized_R, 4)

    save_json(path, data)
    return data, f"SCORED: {path} -> {label}"

def main() -> int:
    # default: score yesterday ET
    override = (os.getenv("SCORE_DATE_ET") or "").strip()  # optional: YYYY-MM-DD
    if override:
        date_str = override
    else:
        now_et = datetime.now(tz=ET)
        date_str = (now_et.date() - timedelta(days=1)).isoformat()

    _, msg = score_day(date_str)
    print(msg)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
