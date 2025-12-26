from __future__ import annotations

import json
import os
from glob import glob
from collections import Counter
from typing import Any

ROOT = os.path.dirname(os.path.abspath(os.path.join(__file__, "..", "..")))
JOURNAL_DIR = os.path.join(ROOT, "journal")
OUT_MD = os.path.join(JOURNAL_DIR, "DASHBOARD.md")

BLOCKS = "▁▂▃▄▅▆▇█"

def read_json(path: str) -> dict | None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            x = json.load(f)
        return x if isinstance(x, dict) else None
    except Exception:
        return None

def get_path(d: dict, path: str, default=None):
    cur: Any = d
    for k in path.split("."):
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur

def pick_str(d: dict, keys: list[str]) -> str:
    for k in keys:
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""

def pick_num(d: dict, keys: list[str]):
    for k in keys:
        v = d.get(k)
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, str):
            try:
                return float(v)
            except Exception:
                pass
    return None

def classify_result(result: str) -> str:
    r = (result or "").lower()
    if any(x in r for x in ["skip", "skipped", "no trade"]):
        return "skipped"
    if any(x in r for x in ["stop", "stopped", "loss", "-1r", "-0."]):
        return "loss"
    if any(x in r for x in ["tp", "hit", "win", "+", "profit"]):
        return "win"
    if r.strip() == "":
        return "pending"
    return "pending"

def emoji_for_bucket(bucket: str) -> str:
    return {"win":"🟢", "loss":"🔴", "skipped":"⚪", "pending":"🟡"}.get(bucket, "🟡")

def sparkline(values: list[float | None]) -> tuple[str, float | None, float | None]:
    vs = [v for v in values if isinstance(v, (int, float))]
    if len(vs) < 2:
        return ("", None, None)
    mn, mx = min(vs), max(vs)
    if mx == mn:
        return (BLOCKS[0] * len(values), mn, mx)
    out = []
    for v in values:
        if not isinstance(v, (int, float)):
            out.append(" ")
            continue
        idx = int((v - mn) / (mx - mn) * (len(BLOCKS) - 1))
        out.append(BLOCKS[max(0, min(len(BLOCKS)-1, idx))])
    return ("".join(out), mn, mx)

def fmt(x) -> str:
    if x is None:
        return "—"
    if isinstance(x, float):
        return f"{x:.6f}".rstrip("0").rstrip(".")
    return str(x)

def extract_rows() -> list[dict]:
    files = sorted(glob(os.path.join(JOURNAL_DIR, "*", "*.json")))
    rows: list[dict] = []

    for path in files:
        data = read_json(path)
        if not isinstance(data, dict):
            continue

        date = os.path.basename(path).replace(".json", "")
        okx = data.get("derivatives_okx", {}) or {}
        t = data.get("paper_test_trade", {}) or {}
        long = (t.get("long") or {})
        short = (t.get("short") or {})

        # result / R can be stored in different keys depending on scorer version
        result = pick_str(data, ["daily_result", "result", "outcome", "paper_test_trade_result", "status"])
        if not result:
            review = data.get("paper_test_trade_review") or data.get("auto_score") or {}
            if isinstance(review, dict):
                result = pick_str(review, ["result", "outcome", "status"])
        if not result:
            result = "pending"

        R = pick_num(data, ["daily_R", "R", "paper_test_trade_R", "realized_R"])
        if R is None:
            review = data.get("paper_test_trade_review") or data.get("auto_score") or {}
            if isinstance(review, dict):
                R = pick_num(review, ["R", "realized_R", "score_R"])

        rows.append({
            "date": date,
            "run_timestamp_et": data.get("run_timestamp_et", ""),
            "btc_spot_usd": pick_num(data, ["btc_spot_usd"]) or data.get("btc_spot_usd", None),
            "funding": okx.get("fundingRate", None),
            "test_trade_id": t.get("test_trade_id", ""),
            "long_entry": long.get("entry", None),
            "long_stop": long.get("stop", None),
            "long_tps": long.get("tps", []),
            "short_entry": short.get("entry", None),
            "short_stop": short.get("stop", None),
            "short_tps": short.get("tps", []),
            "result": result,
            "bucket": classify_result(result),
            "R": R,
            "rel_json": os.path.relpath(path, ROOT).replace("\\", "/"),
        })

    rows.sort(key=lambda r: r["date"])
    return rows

def build_md(rows: list[dict]) -> str:
    total = len(rows)
    latest = rows[-1] if rows else {}
    last30 = rows[-30:] if total >= 30 else rows[:]

    counts = Counter([r["bucket"] for r in rows])
    wins = counts.get("win", 0)
    losses = counts.get("loss", 0)
    skipped = counts.get("skipped", 0)
    pending = counts.get("pending", 0)

    winrate = (wins / max(1, (wins + losses))) * 100.0

    spot_vals = [r.get("btc_spot_usd") if isinstance(r.get("btc_spot_usd"), (int, float)) else None for r in last30]
    fund_vals = [r.get("funding") if isinstance(r.get("funding"), (int, float)) else None for r in last30]
    spot_s, spot_min, spot_max = sparkline(spot_vals)
    fund_s, fund_min, fund_max = sparkline(fund_vals)

    L = []
    L.append("# BTC Futures Journal — Dashboard")
    L.append("")
    L.append("> **Goal:** open this page and instantly see what’s being tested each day + results over time.")
    L.append("")
    L.append("## Quick links")
    L.append("- **Latest summary:** `journal/LATEST.md`")
    L.append("- **Full index:** `journal/INDEX.md`")
    L.append("- **Optional notes/outcomes:** comment in the “BTC Journal Inbox” issue (optional)")
    L.append("")

    # KPIs
    L.append("## Snapshot")
    L.append("")
    L.append("| Metric | Value |")
    L.append("|---|---:|")
    L.append(f"| Total days | {total} |")
    L.append(f"| Wins / Losses | {wins} / {losses} |")
    L.append(f"| Win rate (wins / (wins+losses)) | {winrate:.1f}% |")
    L.append(f"| Skipped | {skipped} |")
    L.append(f"| Pending | {pending} |")
    L.append(f"| Latest date | {latest.get('date','—')} |")
    L.append(f"| Latest BTC spot | {fmt(latest.get('btc_spot_usd'))} |")
    L.append(f"| Latest OKX funding | {fmt(latest.get('funding'))} |")
    L.append("")

    # Mermaid pie (GitHub renders Mermaid)
    L.append("## Results breakdown")
    L.append("")
    L.append("```mermaid")
    L.append("pie showData")
    L.append(f'  "wins" : {wins}')
    L.append(f'  "losses" : {losses}')
    L.append(f'  "skipped" : {skipped}')
    L.append(f'  "pending" : {pending}')
    L.append("```")
    L.append("")

    # Trends (sparklines always render)
    L.append("## Last 30 days trend (sparkline)")
    L.append("")
    L.append("| Metric | Trend | Min → Max |")
    L.append("|---|---|---:|")
    L.append(f"| BTC spot | `{spot_s}` | {fmt(spot_min)} → {fmt(spot_max)} |")
    L.append(f"| OKX funding | `{fund_s}` | {fmt(fund_min)} → {fmt(fund_max)} |")
    L.append("")

    # Latest test details
    L.append("## Latest test (exact levels)")
    L.append("")
    if latest:
        L.append(f"- **Test trade id:** `{latest.get('test_trade_id','')}`")
        L.append(f"- **Result:** {emoji_for_bucket(latest.get('bucket','pending'))} `{latest.get('result','pending')}`")
        if latest.get("R") is not None:
            L.append(f"- **R:** `{latest.get('R')}`")
        L.append("")
        L.append("| Side | Entry | Stop | Take profits |")
        L.append("|---|---:|---:|---|")
        L.append(f"| Long | {fmt(latest.get('long_entry'))} | {fmt(latest.get('long_stop'))} | {latest.get('long_tps', [])} |")
        L.append(f"| Short | {fmt(latest.get('short_entry'))} | {fmt(latest.get('short_stop'))} | {latest.get('short_tps', [])} |")
        L.append("")
        L.append(f"- JSON: `{latest.get('rel_json','')}`")
    else:
        L.append("_No journal files found yet._")
    L.append("")

    # History table (recent first)
    L.append("## Recent history (newest first)")
    L.append("")
    L.append("| Date | Result | R | Spot | Funding | JSON |")
    L.append("|---|---|---:|---:|---:|---|")
    for r in list(reversed(rows))[:30]:
        e = emoji_for_bucket(r["bucket"])
        json_link = r["rel_json"].replace("journal/", "")  # relative from /journal/DASHBOARD.md
        L.append(
            f"| {r['date']} | {e} `{r['result']}` | {fmt(r.get('R'))} | {fmt(r.get('btc_spot_usd'))} | {fmt(r.get('funding'))} | [{os.path.basename(r['rel_json'])}]({json_link}) |"
        )

    L.append("")
    L.append("---")
    L.append("### Notes")
    L.append("- This page is generated automatically by `scripts/build_dashboard.py` from `journal/YYYY/YYYY-MM-DD.json`.")
    L.append("- Optional manual notes/outcomes (from phone/desktop): comment in the Inbox issue; they’re stored under `journal_updates`.")
    L.append("")
    return "\n".join(L)

def main() -> None:
    os.makedirs(JOURNAL_DIR, exist_ok=True)
    rows = extract_rows()
    md = build_md(rows)
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write(md)
        f.write("\n")
    print(f"Wrote {os.path.relpath(OUT_MD, ROOT)}")

if __name__ == "__main__":
    main()
