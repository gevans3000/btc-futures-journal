from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

def parse_date(s: str) -> datetime:
    y, m, d = [int(x) for x in s.split("-")]
    return datetime(y, m, d, tzinfo=ET)

def daterange(start: datetime, end: datetime):
    cur = start
    while cur <= end:
        yield cur
        cur = cur + timedelta(days=1)

def run_py(cmd: list[str], extra_env: dict[str, str] | None = None):
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    r = subprocess.run([sys.executable] + cmd, env=env)
    if r.returncode != 0:
        raise SystemExit(r.returncode)

def main():
    start_s = (os.getenv("START_DATE_ET") or "").strip()
    end_s = (os.getenv("END_DATE_ET") or "").strip()
    mode = (os.getenv("MODE") or "generate_and_score").strip().lower()
    overwrite = (os.getenv("FORCE_OVERWRITE") or "0").strip()

    if not start_s or not end_s:
        raise SystemExit("START_DATE_ET and END_DATE_ET required (YYYY-MM-DD).")

    start = parse_date(start_s)
    end = parse_date(end_s)

    # 1) generate (optional)
    if mode != "score_only":
        for d in daterange(start, end):
            date_et = d.strftime("%Y-%m-%d")
            run_py(
                ["generate_playbook.py"],
                {
                    "DATE_ET": date_et,
                    "FORCE_WRITE": "1",
                    "FORCE_OVERWRITE": overwrite,
                    "STRICT_HISTORICAL": "1",
                },
            )

    # 2) score each day
    for d in daterange(start, end):
        date_et = d.strftime("%Y-%m-%d")
        run_py(["scripts/score_day.py"], {"DATE_ET": date_et})

    # 3) rebuild markdown views
    run_py(["scripts/build_dashboard.py"])
    run_py(["scripts/build_index.py"])\n    run_py(["scripts/build_metrics.py"])\nif __name__ == "__main__":
    main()

