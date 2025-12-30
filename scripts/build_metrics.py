from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from glob import glob

def _load_json(p: str) -> dict:
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)

def _safe_float(x, default=0.0) -> float:
    try:
        return float(x)
    except Exception:
        return default

def _list_day_files() -> list[str]:
    files = glob(os.path.join("journal", "*", "*.json"))
    # exclude generated pointers
    files = [p for p in files if os.path.basename(p) not in {"LATEST.json"}]
    # only YYYY-MM-DD.json
    out = []
    for p in files:
        name = os.path.basename(p)
        if len(name) == 15 and name[4] == "-" and name[7] == "-" and name.endswith(".json"):
            out.append(p)
    out.sort()
    return out

def build(days: int = 30) -> tuple[dict, str]:
    files = _list_day_files()
    if not files:
        stats = {"status": "no_files"}
        return stats, "# Metrics\n\nNo journal day files found.\n"

    tail = files[-days:]
    rows = []
    for p in tail:
        o = _load_json(p)
        r = (o.get("paper_test_trade_review") or {})
        rows.append({
            "date": r.get("date_et") or os.path.splitext(os.path.basename(p))[0],
            "triggered": r.get("triggered"),
            "filled": bool(r.get("filled")) if r.get("filled") is not None else False,
            "exit": r.get("exit"),
            "R": _safe_float(r.get("R"), 0.0),
        })

    total_R = sum(x["R"] for x in rows)
    avg_R_day = total_R / max(1, len(rows))

    trade_rows = [x for x in rows if x.get("filled") is True]
    no_trade_rows = [x for x in rows if x.get("exit") == "no_trigger"]

    wins = [x for x in trade_rows if x["R"] > 0]
    losses = [x for x in trade_rows if x["R"] < 0]

    def grp(key: str) -> dict[str, int]:
        d: dict[str, int] = {}
        for x in rows:
            k = str(x.get(key))
            d[k] = d.get(k, 0) + 1
        return dict(sorted(d.items(), key=lambda kv: (-kv[1], kv[0])))

    stats = {
        "window_days": len(rows),
        "total_R": round(total_R, 3),
        "avg_R_per_day": round(avg_R_day, 3),
        "trade_days": len(trade_rows),
        "no_trade_days": len(no_trade_rows),
        "win_trades": len(wins),
        "loss_trades": len(losses),
        "win_rate_on_trades": round((len(wins) / max(1, len(trade_rows))) * 100, 1),
        "expectancy_R_per_trade": round((sum(x["R"] for x in trade_rows) / max(1, len(trade_rows))), 3),
        "exit_breakdown": grp("exit"),
        "side_breakdown": grp("triggered"),
        "asof_utc": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    # Markdown
    lines = []
    lines.append("# Metrics (Auto)\n")
    lines.append(f"- Window: last **{stats['window_days']}** days\n")
    lines.append(f"- Total: **{stats['total_R']}R** | Avg/day: **{stats['avg_R_per_day']}R**\n")
    lines.append(f"- Trade days: **{stats['trade_days']}** | No-trade days: **{stats['no_trade_days']}**\n")
    lines.append(f"- Win rate (trades): **{stats['win_rate_on_trades']}%** | Expectancy: **{stats['expectancy_R_per_trade']}R/trade**\n")

    def md_table(title: str, d: dict[str, int]):
        lines.append(f"\n## {title}\n")
        lines.append("| Item | Count |\n|---|---:|\n")
        for k, v in d.items():
            lines.append(f"| {k} | {v} |\n")

    md_table("Exit breakdown", stats["exit_breakdown"])
    md_table("Triggered side breakdown", stats["side_breakdown"])

    lines.append("\n## Last days\n")
    lines.append("| Date | Side | Filled | Exit | R |\n|---|---|---|---|---:|\n")
    for x in rows[-14:]:
        lines.append(f"| {x['date']} | {x['triggered']} | {x['filled']} | {x['exit']} | {x['R']} |\n")

    return stats, "".join(lines)

def main():
    days = int((os.getenv("METRICS_DAYS") or "30").strip())
    stats, md = build(days=days)
    os.makedirs("journal", exist_ok=True)
    with open("journal/METRICS.json", "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2, sort_keys=True)
        f.write("\n")
    with open("journal/METRICS.md", "w", encoding="utf-8") as f:
        f.write(md)
    print("Wrote journal/METRICS.md and journal/METRICS.json")

if __name__ == "__main__":
    main()
