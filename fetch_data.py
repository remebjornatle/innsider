#!/usr/bin/env python3
"""Fetch insider trade data from Oslo Børs and write docs/data.json.

Run by GitHub Actions daily; output is served as a static file via GitHub Pages.
"""

import json
import os
from datetime import datetime, timedelta, timezone

from insider_lib import build_dataset

DAYS = 90


def main():
    to_dt = datetime.now(timezone.utc)
    from_dt = to_dt - timedelta(days=DAYS)
    from_date = from_dt.strftime("%Y-%m-%d")
    to_date = to_dt.strftime("%Y-%m-%d")

    print(f"Fetching messages {from_date} → {to_date} …")
    result = build_dataset(from_date, to_date)
    result["generated_at"] = to_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    os.makedirs("docs", exist_ok=True)
    out_path = os.path.join("docs", "data.json")
    with open(out_path, "w") as f:
        json.dump(result, f, separators=(",", ":"))

    print(f"  {result['total_parsed']} trades parsed → {out_path}")


if __name__ == "__main__":
    main()
