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

def fetch_btc_price_binance_at(now_et: datetime) -> float:
    # Historical-safe endpoint (GitHub runners often fail on api.binance.com)
    start = now_et - timedelta(minutes=6)
    end = now_et + timedelta(minutes=1)

    url = "https://data-api.binance.vision/api/v3/klines"
    params = {
        "symbol": "BTCUSDT",
        "interval": "1m",
        "startTime": _ms(start.astimezone(timezone.utc)),
        "endTime": _ms(end.astimezone(timezone.utc)),
        "limit": 20,
    }
    data = http_get_json(url, params=params)
    if not isinstance(data, list) or not data:
        raise RuntimeError("Binance vision klines empty")

    target_ms = _ms(now_et.astimezone(timezone.utc))
    best = None
    for k in data:
        ot = int(k[0])
        if ot <= target_ms:
            best = k
        else:
            break
    if best is None:
        best = data[0]
    return float(best[4])  # close

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

def build_playbook(now_et: datetime, p: float, okx: dict, meta: dict) -> dict:
    # Simple band (kept lightweight) but trades are now 1R-to-level
    band = p * 0.015
    support = round(p - band, 2)
    resistance = round(p + band, 2)

    test_trade_id = f"BTC-{now_et:%Y-%m-%d}-0600-ET-TEST"

    # Entries near spot (same idea as before)
    long_entry = round(p * 1.003, 2)
    short_entry = round(p * 0.997, 2)

    # TP1 anchored to structure; stop sized so TP1 == 1R
    long_tp1 = resistance
    long_stop = round(2 * long_entry - long_tp1, 2)  # 1R stop
    long_risk = max(0.01, long_entry - long_stop)
    long_tp2 = round(long_entry + 2 * long_risk, 2)  # 2R extension

    short_tp1 = support
    short_stop = round(short_entry + (short_entry - short_tp1), 2)  # 1R stop
    short_risk = max(0.01, short_stop - short_entry)
    short_tp2 = round(short_entry - 2 * short_risk, 2)  # 2R extension

    return {
        "meta": meta,
        "run_timestamp_et": now_et.strftime("%Y-%m-%d %H:%M"),
        "price_time_et": now_et.strftime("%Y-%m-%d %H:%M"),
        "btc_spot_usd": round(p, 2),
        "derivatives_okx": okx,
        "levels": {"support": [support], "resistance": [resistance]},
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
                "trigger": f"15m close >= {round(p * 1.002, 2)}",
                "entry": long_entry,
                "stop": long_stop,
                "tps": [long_tp1, long_tp2],
            },
            "short": {
                "trigger": f"15m close <= {round(p * 0.998, 2)}",
                "entry": short_entry,
                "stop": short_stop,
                "tps": [short_tp1, short_tp2],
            },
        },
    }

def out_path_for(now_et: datetime) -> str:
    year = now_et.strftime("%Y")
    day = now_et.strftime("%Y-%m-%d")
    return os.path.join("journal", year, f"{day}.json")

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
        # Backfill/historical should never use live fallback
        strict_hist = True if strict_hist is False else strict_hist
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

    # Price
    if date_et:
        try:
            btc = fetch_btc_price_binance_at(now_et)
            price_source = "binance_vision_1m_close_at_0600_et"
        except Exception as e:
            if strict_hist:
                raise
            btc = fetch_btc_spot_usd_now()
            price_source = f"coinbase_fallback_due_to:{type(e).__name__}"
    else:
        btc = fetch_btc_spot_usd_now()
        price_source = "coinbase_spot_now"

    # Funding snapshot (current)
    try:
        okx = fetch_okx_funding_snapshot_now()
        funding_source = "okx_current"
    except Exception:
        okx = {"asof": "missing"}
        funding_source = "missing"

    meta = {
        "source": run_source,
        "price_source": price_source,
        "funding_source": funding_source,
    }

    playbook = build_playbook(now_et, btc, okx, meta)
    write_daily_json(now_et, playbook, overwrite=overwrite)

if __name__ == "__main__":
    main()
