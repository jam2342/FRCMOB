from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from app.services.ratings.component_discovery import discover  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="Print component/RP suggestions from one cached event; no writes or network")
    parser.add_argument("--event",required=True)
    parser.add_argument("--cache",required=True,type=Path)
    args = parser.parse_args()
    key = args.event.lower()
    if not re.fullmatch(r"\d{4}[a-z0-9]+",key):
        parser.error("invalid event key")
    path = args.cache/key[:4]/f"{key}.json"
    payload = json.loads(path.read_text())
    if payload.get("event",{}).get("key","").lower() != key:
        parser.error("cached event does not match requested key")
    print(json.dumps(discover(payload),indent=2,allow_nan=False))


if __name__ == "__main__":
    main()
