from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

ET = ZoneInfo("America/New_York")

def http_get_json(url: str, timeout: int = 20) -> dict:
    r = requests.get(url, timeout=timeout, headers={"User-Agent": "btc-journal-bot/1.0"})
    r.raise_for_status()
    return r.json()

def in_run_window(now_et: datetime) -> bool:
    # Write once/day around 06:00–06:10 ET
    start = now_et.replace(hour=6, minute=0, second=0, microsecond=0)
    end = start + timedelta(minutes=10)
    return start <= now_et < end

def fetch_btc_spot_usd() -> float:
    data = http_get_json("https://api.coinbase.com/v2/prices/BTC-USD/spot")
    return float(data["data"]["amount"])

def fetch_okx_funding_snapshot() -> dict:
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
    }

def build_playbook(now_et: datetime, btc_spot: float, okx: dict) -> dict:
    p = btc_spot
    band = p * 0.015
    support = round(p - band, 2)
    resistance = round(p + band, 2)

    test_trade_id = f"BTC-{now_et:%Y-%m-%d}-0600-ET-TEST"

    return {
        "run_timestamp_et": now_et.strftime("%Y-%m-%d %H:%M"),
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
        "prior_day_review": {
            "status": "not_implemented",
            "note": "Upgrade path: pull candles and auto-score whether triggers fired + MAE/MFE for yesterday.",
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

    # Preserve any existing journal_updates (e.g., comments logged before the playbook ran)
    if os.path.exists(out_path):
        playbook["journal_updates"] = load_existing_updates(out_path)

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(playbook, f, indent=2, sort_keys=True)
        f.write("\n")

    print(f"Wrote: {out_path}")
    return out_path, True

def resolve_now_et() -> datetime:
    target = (os.getenv("TARGET_DATE_ET") or "").strip()
    if not target:
        return datetime.now(tz=ET)

    try:
        d = datetime.strptime(target, "%Y-%m-%d").date()
    except ValueError:
        raise SystemExit(f"TARGET_DATE_ET must be YYYY-MM-DD, got: {target!r}")

    # Use 06:00 ET for deterministic file naming & consistency
    return datetime(d.year, d.month, d.day, 6, 0, tzinfo=ET)

def main() -> None:
    now_et = resolve_now_et()
    force = (os.getenv("FORCE_WRITE", "") or "").strip().lower() in {"1", "true", "yes", "y"}
    overwrite = (os.getenv("FORCE_OVERWRITE", "") or "").strip().lower() in {"1", "true", "yes", "y"}

    if (not force) and (not in_run_window(now_et)):
        print(f"Not in run window (ET): {now_et.isoformat()}")
        return

    out_path = out_path_for(now_et)
    if os.path.exists(out_path) and (not overwrite):
        print(f"Today already exists, no overwrite: {out_path}")
        return

    btc = fetch_btc_spot_usd()
    okx = fetch_okx_funding_snapshot()
    playbook = build_playbook(now_et, btc, okx)
    write_daily_json(now_et, playbook, overwrite=overwrite)

if __name__ == "__main__":
    main()
