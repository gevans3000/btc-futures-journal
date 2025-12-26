from __future__ import annotations

import json
import os
from glob import glob

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # repo root
JOURNAL_DIR = os.path.join(ROOT, "journal")

def read_json(path: str) -> dict | None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            x = json.load(f)
        return x if isinstance(x, dict) else None
    except Exception:
        return None

def fmt_float(x, nd=6) -> str:
    try:
        return f"{float(x):.{nd}f}"
    except Exception:
        return ""

def fmt_r(x) -> str:
    try:
        return f"{float(x):.2f}R"
    except Exception:
        return ""

def link_from_rel(relpath: str) -> str:
    rel = relpath.replace("\\", "/")
    return rel.split("journal/", 1)[-1] if "journal/" in rel else rel

def build_index(rows: list[dict]) -> str:
    lines = []
    lines.append("# BTC Futures Journal Index")
    lines.append("")
    lines.append("Auto-generated after each run.")
    lines.append("")
    lines.append("- **Latest:** [LATEST.md](LATEST.md) | [LATEST.json](LATEST.json)")
    lines.append("")
    lines.append("| Date | BTC Spot (USD) | OKX Funding | Result | R | File |")
    lines.append("|---|---:|---:|---|---:|---|")

    for r in rows:
        date = r.get("date", "")
        spot = r.get("btc_spot_usd", "")
        funding = r.get("fundingRate", "")

        result = r.get("result", "")
        rmult = r.get("r_multiple", "")

        link = link_from_rel(r.get("relpath", ""))
        lines.append(
            f"| {date} | {spot} | {funding} | {result} | {rmult} | [{os.path.basename(link)}]({link}) |"
        )

    lines.append("")
    return "\n".join(lines)

def build_latest_md(latest: dict, rel_json_path: str) -> str:
    okx = (latest or {}).get("derivatives_okx", {}) or {}
    spot = (latest or {}).get("btc_spot_usd", "")
    ts = (latest or {}).get("run_timestamp_et", "")
    funding = okx.get("fundingRate", "")
    premium = okx.get("premium", "")

    rel_link = link_from_rel(rel_json_path)

    lines = []
    lines.append("# Latest BTC Futures Playbook")
    lines.append("")
    lines.append(f"- **Run (ET):** {ts}")
    lines.append(f"- **BTC Spot (USD):** {spot}")
    lines.append(f"- **OKX fundingRate:** {funding}")
    lines.append(f"- **OKX premium:** {premium}")
    lines.append("")
    lines.append(f"Source JSON: [{os.path.basename(rel_link)}]({rel_link})")
    lines.append("")
    lines.append("History dashboard: [INDEX.md](INDEX.md)")
    lines.append("")
    return "\n".join(lines)

def summarize_result(d: dict) -> tuple[str, str]:
    pr = d.get("prior_day_review")
    if not isinstance(pr, dict):
        return ("", "")
    if pr.get("status") != "scored":
        # show useful statuses without clutter
        s = pr.get("outcome") or pr.get("status") or ""
        return (str(s), "")

    direction = (pr.get("direction") or "").upper()
    outcome = pr.get("outcome") or ""
    if direction and outcome:
        res = f"{direction} {outcome}"
    else:
        res = str(outcome or direction or "")

    r = pr.get("r_multiple")
    return (res, fmt_r(r))

def main() -> None:
    files = sorted(glob(os.path.join(JOURNAL_DIR, "*", "*.json")))
    rows = []

    for path in files:
        data = read_json(path)
        if not isinstance(data, dict):
            continue

        base = os.path.basename(path)
        date = base.replace(".json", "")

        okx = data.get("derivatives_okx", {}) or {}
        result, rmult = summarize_result(data)

        rows.append({
            "date": date,
            "btc_spot_usd": data.get("btc_spot_usd", ""),
            "fundingRate": fmt_float(okx.get("fundingRate")),
            "result": result,
            "r_multiple": rmult,
            "path": path,
            "relpath": os.path.relpath(path, ROOT),
        })

    rows.sort(key=lambda r: r["date"], reverse=True)

    # INDEX.md
    index_path = os.path.join(JOURNAL_DIR, "INDEX.md")
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(build_index(rows[:180]))
        f.write("\n")

    # LATEST.md + LATEST.json
    if rows:
        newest = rows[0]
        newest_data = read_json(newest["path"]) or {}

        latest_json_path = os.path.join(JOURNAL_DIR, "LATEST.json")
        with open(latest_json_path, "w", encoding="utf-8") as f:
            json.dump(newest_data, f, indent=2, sort_keys=True)
            f.write("\n")

        latest_md_path = os.path.join(JOURNAL_DIR, "LATEST.md")
        with open(latest_md_path, "w", encoding="utf-8") as f:
            f.write(build_latest_md(newest_data, newest["relpath"]))
            f.write("\n")

if __name__ == "__main__":
    main()
