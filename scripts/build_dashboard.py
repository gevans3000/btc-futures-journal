from __future__ import annotations

import json
import os
from glob import glob

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JOURNAL_DIR = os.path.join(ROOT, "journal")
ASSETS_DIR = os.path.join(JOURNAL_DIR, "assets")

def read_json(path: str) -> dict | None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            x = json.load(f)
        return x if isinstance(x, dict) else None
    except Exception:
        return None

def write_text(path: str, s: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(s.rstrip() + "\n")

def svg_equity(values: list[float], width=1100, height=320, pad=40) -> str:
    # Simple, clean SVG line chart (works in GitHub Markdown).
    if not values:
        values = [0.0]
    xs = list(range(len(values)))
    lo = min(values)
    hi = max(values)
    if hi == lo:
        hi = lo + 1.0

    def sx(i): 
        return pad + (i / max(1, len(values)-1)) * (width - 2*pad)

    def sy(v):
        # invert y
        return pad + (1 - (v - lo) / (hi - lo)) * (height - 2*pad)

    pts = " ".join(f"{sx(i):.2f},{sy(v):.2f}" for i, v in enumerate(values))

    # background + subtle grid + line + label
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#0b1220"/>
      <stop offset="100%" stop-color="#111827"/>
    </linearGradient>
  </defs>
  <rect x="0" y="0" width="{width}" height="{height}" rx="18" fill="url(#bg)"/>
  <g opacity="0.18" stroke="#ffffff" stroke-width="1">
    <line x1="{pad}" y1="{pad}" x2="{width-pad}" y2="{pad}"/>
    <line x1="{pad}" y1="{height/2}" x2="{width-pad}" y2="{height/2}"/>
    <line x1="{pad}" y1="{height-pad}" x2="{width-pad}" y2="{height-pad}"/>
  </g>
  <polyline fill="none" stroke="#22c55e" stroke-width="3.2" points="{pts}"/>
  <text x="{pad}" y="{pad-12}" fill="#e5e7eb" font-family="ui-sans-serif, system-ui" font-size="16">Equity Curve (Cumulative R)</text>
  <text x="{pad}" y="{height-12}" fill="#9ca3af" font-family="ui-sans-serif, system-ui" font-size="12">Auto-updated by GitHub Actions</text>
</svg>'''

def main() -> None:
    files = sorted(glob(os.path.join(JOURNAL_DIR, "*", "*.json")))
    rows = []
    for path in files:
        d = read_json(path)
        if not d:
            continue
        date = os.path.basename(path).replace(".json", "")
        auto = d.get("paper_test_trade_autoscore") if isinstance(d.get("paper_test_trade_autoscore"), dict) else {}
        R = float(auto.get("R", 0.0)) if auto else 0.0
        outcome = (auto.get("outcome") if auto else "") or ""
        side = (auto.get("chosen_side") if auto else "") or ""
        rows.append({"date": date, "path": os.path.relpath(path, ROOT).replace("\\","/"), "R": R, "outcome": outcome, "side": side})

    rows.sort(key=lambda r: r["date"])  # oldest->newest
    Rs = [r["R"] for r in rows]
    eq = []
    s = 0.0
    for r in Rs:
        s += r
        eq.append(round(s, 4))

    os.makedirs(ASSETS_DIR, exist_ok=True)
    write_text(os.path.join(ASSETS_DIR, "equity.svg"), svg_equity(eq))

    # KPIs
    days = len(rows)
    wins = sum(1 for r in rows if r["R"] > 0)
    losses = sum(1 for r in rows if r["R"] < 0)
    flat = sum(1 for r in rows if r["R"] == 0)
    winrate = (wins / max(1, (wins+losses))) * 100.0
    totalR = eq[-1] if eq else 0.0
    avgR = (sum(Rs) / max(1, days))

    # last 30 (newest first)
    last = list(reversed(rows))[:30]

    lines = []
    lines.append("# BTC Journal Dashboard")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|---|---:|")
    lines.append(f"| Days tracked | {days} |")
    lines.append(f"| Wins / Losses / Flat | {wins} / {losses} / {flat} |")
    lines.append(f"| Win rate (ignores flat) | {winrate:.1f}% |")
    lines.append(f"| Total R | {totalR:.2f} |")
    lines.append(f"| Avg R / day | {avgR:.3f} |")
    lines.append("")
    lines.append("![Equity Curve](assets/equity.svg)")
    lines.append("")
    lines.append("## Last 30 days")
    lines.append("| Date | Side | Outcome | R | File |")
    lines.append("|---|---|---|---:|---|")
    for r in last:
        base = os.path.basename(r["path"])
        link = r["path"].split("journal/", 1)[-1]
        lines.append(f"| {r['date']} | {r['side'] or '-'} | {r['outcome'] or '-'} | {r['R']:.3f} | [{base}]({link}) |")
    lines.append("")

    write_text(os.path.join(JOURNAL_DIR, "DASHBOARD.md"), "\n".join(lines))

if __name__ == "__main__":
    main()
