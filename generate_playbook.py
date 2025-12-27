from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

ET = ZoneInfo("America/New_York")

def http_get_json(url: str, timeout: int = 25, params: dict | None = None) -> dict:
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
    # Grab a small 1m window around 06:00 ET and use the candle at/just before that timestamp.
    start = now_et - timedelta(minutes=6)
    end = now_et + timedelta(minutes=1)

    url = "https://api.binance.com/api/v3/klines"
    params = {
        "symbol": "BTCUSDT",
        "interval": "1m",
        "startTime": _ms(start.astimezone(timezone.utc)),
        "endTime": _ms(end.astimezone(timezone.utc)),
        "limit": 20,
    }
    data = http_get_json(url, params=params)
    if not isinstance(data, list) or not data:
        raise RuntimeError("Binance klines empty")

    target_ms = _ms(now_et.astimezone(timezone.utc))
    best = None
    for k in data:
        # kline: [openTime, open, high, low, close, ...]
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

def build_playbook(now_et: datetime, btc_spot: float, okx: dict) -> dict:
    p = btc_spot
    band = p * 0.015
    support = round(p - band, 2)
    resistance = round(p + band, 2)

    test_trade_id = f"BTC-{now_et:%Y-%m-%d}-0600-ET-TEST"

    return {
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
                "entry": round(p * 1.003, 2),
                "stop": round(support * 0.998, 2),
                "tps": [round(p * 1.01, 2), round(resistance, 2)],
            },
            "short": {
                "trigger": f"15m close <= {round(p * 0.998, 2)}",
                "entry": round(p * 0.997, 2),
                "stop": round(resistance * 1.002, 2),
                "tps": [round(p * 0.99, 2), round(p * 0.985, 2)],
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

    if date_et:
        y, m, d = [int(x) for x in date_et.split("-")]
        now_et = datetime(y, m, d, 6, 0, tzinfo=ET)
        force = True  # date override always allowed
    else:
        now_et = datetime.now(tz=ET)

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
        except Exception:
            btc = fetch_btc_spot_usd_now()
    else:
        btc = fetch_btc_spot_usd_now()

    # Funding (keep current snapshot for now; historical can be added later without changing the rest)
    try:
        okx = fetch_okx_funding_snapshot_now()
    except Exception:
        okx = {"asof": "missing"}

    playbook = build_playbook(now_et, btc, okx)
    write_daily_json(now_et, playbook, overwrite=overwrite)

if __name__ == "__main__":
    main()
