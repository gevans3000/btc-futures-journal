from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
import subprocess

ET = ZoneInfo("America/New_York")

def run(cmd: list[str]) -> None:
    subprocess.check_call(cmd)

def today_path(repo_root: Path) -> Path:
    now_et = datetime.now(tz=ET)
    year = now_et.strftime("%Y")
    day = now_et.strftime("%Y-%m-%d")
    return repo_root / "journal" / year / f"{day}.json"

def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    msg = " ".join(sys.argv[1:]).strip()
    if not msg:
        print("Usage: python scripts/update.py <update text>")
        print('Example: python scripts/update.py "TP1 hit, moved stop to BE"')
        sys.exit(2)

    path = today_path(repo_root)
    if not path.exists():
        raise FileNotFoundError(f"Today's journal file not found: {path}")

    data = json.loads(path.read_text(encoding="utf-8"))

    now_et = datetime.now(tz=ET).strftime("%Y-%m-%d %H:%M:%S ET")
    data.setdefault("execution_log", [])
    data["execution_log"].append({"ts": now_et, "note": msg})

    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Updated: {path}")

    # commit + push
    run(["git", "add", str(path)])
    # allow empty (no-op) update prevention not needed; every append changes file
    run(["git", "commit", "-m", f"Journal update: {now_et}"])
    run(["git", "push"])

if __name__ == "__main__":
    main()
