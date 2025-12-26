from __future__ import annotations

import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

def _load(path: str) -> dict | None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            x = json.load(f)
        return x if isinstance(x, dict) else None
    except Exception:
        return None

def _save(path: str, obj: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, sort_keys=True)
        f.write("\n")

def today_path(now_et: datetime) -> str:
    year = now_et.strftime("%Y")
    day = now_et.strftime("%Y-%m-%d")
    return os.path.join("journal", year, f"{day}.json")

def main() -> int:
    now = datetime.now(tz=ET)
    text = (os.getenv("UPDATE_TEXT") or "").strip()
    if not text:
        print("No UPDATE_TEXT provided.")
        return 2

    out_path = today_path(now)
    data = _load(out_path) or {
        "run_timestamp_et": now.strftime("%Y-%m-%d %H:%M"),
        "status": "preplaybook",
        "journal_updates": [],
    }

    updates = data.get("journal_updates")
    if not isinstance(updates, list):
        updates = []
    upd = {
        "ts_et": now.strftime("%Y-%m-%d %H:%M:%S"),
        "actor": os.getenv("GITHUB_ACTOR", "") or os.getenv("ACTOR", ""),
        "source": os.getenv("UPDATE_SOURCE", "workflow_dispatch"),
        "text": text,
    }

    issue = (os.getenv("ISSUE_NUMBER") or "").strip()
    if issue:
        upd["issue_number"] = int(issue) if issue.isdigit() else issue

    url = (os.getenv("COMMENT_URL") or "").strip()
    if url:
        upd["comment_url"] = url

    updates.append(upd)
    data["journal_updates"] = updates

    _save(out_path, data)
    print(f"Updated: {out_path}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
