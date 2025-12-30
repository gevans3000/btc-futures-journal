from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

ET = ZoneInfo("America/New_York")

def http_get_json(url: str, timeout: int = 25, params: dict | None = None):
    r = requests.get(url, timeout=timeout, params=params, headers={"User-Agent": "btc-journal-bot/1.0"})
    r.raise_for_status()
    return r.json()

def in_run_window(now_et: datetime) -> bool:
    start = now_et.replace(hour=6, minute=0, second=0, microsecond=0)
    end = start + timedelta(minutes=10)
    return start <= now_et < end

def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)

def fetch_klines_vision(symbol: str, interval: str, start_et: datetime, end_et: datetime, limit: int = 1000):
    url = "https://data-api.binance.vision/api/v3/klines"
    params = {
        "symbol": symbol,
        "interval": interval,
        "startTime": _ms(start_et.astimezone(timezone.utc)),
        "endTime": _ms(end_et.astimezone(timezone.utc)),
        "limit": limit,
    }
    data = http_get_json(url, params=params)
    if not isinstance(data, list) or not data:
        raise RuntimeError("Binance vision klines empty")
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

def fetch_btc_price_at_0600_et(now_et: datetime) -> float:
    # use the 1m candle at/just before 06:00 ET
    start = now_et - timedelta(minutes=6)
    end = now_et + timedelta(minutes=1)
    data = fetch_klines_vision("BTCUSDT", "1m", start, end, limit=20)

    target_ms = _ms(now_et.astimezone(timezone.utc))
    best = None
    for c in data:
        if c["t_open_ms"] <= target_ms:
            best = c
        else:
            break
    if best is None:
        best = data[0]
    return float(best["close"])

def fetch_btc_spot_usd_now() -> float:
    data = http_get_json("https://api.coinbase.com/v2/prices/BTC-USD/spot")
    return float(data["data"]["amount"])

def fetch_okx_funding_snapshot_now() -> dict:
    data = http_get_json("https://www.okx.com/api/v5/public/funding-rate?instId=BTC-USDT-SWAP")
    item = data["data"][0]
    return {
        "instId": item.get("instId"),
        "fundingRate": float(item["fundingRate"]),
        "premium": float(item["premium"]),
        "nextFundingTime": int(item["nextFundingTime"]),
        "fundingTime": int(item["fundingTime"]),
        "ts": int(item["ts"]),
        "method": item.get("method"),
        "asof": "okx_current",
    }

def atr14_15m(candles: list[dict]) -> float:
    if len(candles) < 20:
        raise RuntimeError("Not enough candles for ATR")
    trs = []
    prev_close = candles[0]["close"]
    for c in candles[1:]:
        hi, lo, cl = c["high"], c["low"], c["close"]
        tr = max(hi - lo, abs(hi - prev_close), abs(lo - prev_close))
        trs.append(tr)
        prev_close = cl
    window = trs[-14:]
    atr = sum(window) / max(1, len(window))
    return max(1.0, atr)

def build_playbook(now_et: datetime, p: float, okx: dict, lookback_15m: list[dict], meta: dict) -> dict:
    range_high = max(c["high"] for c in lookback_15m)
    range_low = min(c["low"] for c in lookback_15m)
    atr = atr14_15m(lookback_15m)

    buffer = 0.25 * atr
    risk = 1.5 * atr

    # Breakout style (may result in no-trigger days; that's GOOD signal)
    long_entry = round(range_high + buffer, 2)
    long_stop = round(long_entry - risk, 2)
    long_tp1 = round(long_entry + risk, 2)      # +1R
    long_tp2 = round(long_entry + 2 * risk, 2)  # +2R

    short_entry = round(range_low - buffer, 2)
    short_stop = round(short_entry + risk, 2)
    short_tp1 = round(short_entry - risk, 2)      # +1R
    short_tp2 = round(short_entry - 2 * risk, 2)  # +2R

    test_trade_id = f"BTC-{now_et:%Y-%m-%d}-0600-ET-TEST"

    return {
        "meta": {
            **meta,
            "strategy": "range_breakout_atr_15m",
            "range_lookback_hours": 24,
            "atr14_15m": round(atr, 2),
        },
        "run_timestamp_et": now_et.strftime("%Y-%m-%d %H:%M"),
        "price_time_et": now_et.strftime("%Y-%m-%d %H:%M"),
        "btc_spot_usd": round(p, 2),
        "derivatives_okx": okx,
        "levels": {"support": [round(range_low, 2)], "resistance": [round(range_high, 2)]},
        "risk_rules": {
            "max_risk_per_idea_R": 1.0,
            "daily_stop_R": 2.0,
            "funding_half_size_threshold": 0.0003,
            "funding_no_trade_threshold": 0.0010,
        },
        "paper_test_trade": {
            "test_trade_id": test_trade_id,
            "type": "OCO_conditional",
            "long": {
                "trigger": f"15m close >= {long_entry}",
                "entry": long_entry,
                "stop": long_stop,
                "tps": [long_tp1, long_tp2],
            },
            "short": {
                "trigger": f"15m close <= {short_entry}",
                "entry": short_entry,
                "stop": short_stop,
                "tps": [short_tp1, short_tp2],
            },
        },
    }

def out_path_for(now_et: datetime) -> str:
    return os.path.join("journal", now_et.strftime("%Y"), f"{now_et:%Y-%m-%d}.json")

def load_existing_updates(path: str) -> list[dict]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            x = json.load(f)
        if isinstance(x, dict) and isinstance(x.get("journal_updates"), list):
            return x["journal_updates"]
    except Exception:
        pass
    return []

def write_daily_json(now_et: datetime, playbook: dict, overwrite: bool) -> tuple[str, bool]:
    out_path = out_path_for(now_et)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    if os.path.exists(out_path) and (not overwrite):
        print(f"Already exists, skipping: {out_path}")
        return out_path, False

    if os.path.exists(out_path):
        playbook["journal_updates"] = load_existing_updates(out_path)

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(playbook, f, indent=2, sort_keys=True)
        f.write("\n")

    print(f"Wrote: {out_path}")
    return out_path, True

def main() -> None:
    date_et = (os.getenv("DATE_ET") or "").strip()
    force = (os.getenv("FORCE_WRITE", "") or "").strip().lower() in {"1", "true", "yes", "y"}
    overwrite = (os.getenv("FORCE_OVERWRITE", "") or "").strip().lower() in {"1", "true", "yes", "y"}
    strict_hist = (os.getenv("STRICT_HISTORICAL", "") or "").strip().lower() in {"1", "true", "yes", "y"}

    if date_et:
        y, m, d = [int(x) for x in date_et.split("-")]
        now_et = datetime(y, m, d, 6, 0, tzinfo=ET)
        force = True
        strict_hist = True
        run_source = "backfill"
    else:
        now_et = datetime.now(tz=ET)
        run_source = "daily"

    if (not force) and (not in_run_window(now_et)):
        print(f"Not in run window (ET): {now_et.isoformat()}")
        return

    out_path = out_path_for(now_et)
    if os.path.exists(out_path) and (not overwrite):
        print(f"Today already exists, no overwrite: {out_path}")
        return

    # Price at 06:00 ET
    try:
        btc = fetch_btc_price_at_0600_et(now_et)
        price_source = "binance_vision_1m_close_at_0600_et"
    except Exception as e:
        if strict_hist:
            raise
        btc = fetch_btc_spot_usd_now()
        price_source = f"coinbase_fallback_due_to:{type(e).__name__}"

    # 24h lookback candles for range/ATR
    start = now_et - timedelta(hours=24)
    end = now_et
    lookback_15m = fetch_klines_vision("BTCUSDT", "15m", start, end, limit=1000)

    # Funding snapshot (current)
    try:
        okx = fetch_okx_funding_snapshot_now()
        funding_source = "okx_current"
    except Exception:
        okx = {"asof": "missing"}
        funding_source = "missing"

    meta = {"source": run_source, "price_source": price_source, "funding_source": funding_source}
    playbook = build_playbook(now_et, btc, okx, lookback_15m, meta)
    write_daily_json(now_et, playbook, overwrite=overwrite)

if __name__ == "__main__":
    main()
