"""
Change the owner (Team) of any POI in the Locations table.

Usage:
  1. Fill in SYNC_TOKEN with a fresh one from a HAR export.
  2. Fill in the ROW dict for the POI you want to change.
  3. Run: python change_poi_owner.py
"""
from time import sleep
from pathlib import Path

import requests
import json
from datetime import datetime, timezone

# ── CONFIGURE ─────────────────────────────────────────────────────────────────

# Grab a fresh syncToken from a new HAR when this expires (~90 days).
SYNC_TOKEN = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJhcHBJZCI6IjA0ZWQzMjI1LThiZGYtNDkzYS1iODRiLTRmY2RlNDU4ZWUwNyIsImFwcFZlcnNpb24iOiIxLjAwMDM5MCIsInVzZXJJZCI6Ii0xIiwiaWF0IjoxNzc1NDE0MjY1LCJleHAiOjE3ODMxOTAyNjUsImlzcyI6Imh0dHBzOi8vd3d3LmFwcHNoZWV0LmNvbSIsImF1ZCI6Imh0dHBzOi8vd3d3LmFwcHNoZWV0LmNvbSJ9"
    ".DFiFajLPbnFY3aKuUw0Utqjis2oi4FWrNLco6oDExcY"
)

# Copy the full row array from the HAR for the POI you want to change.
# Only [4] (owner) will be overwritten by change_owner().
ROW = [
    "27",                           # [0]  internal row number
    "JjCK-VggXC4yi_GmQns_81",      # [1]  unique row key
    "",                     # [2]  POI name
    "51.101310, 5.149437",          # [3]  coordinates
    "Chiro Genebos",                # [4]  current owner  ← will be overwritten
    "Ham",                          # [5]  municipality
    "38",                           # [6]  score / numeric field
    "🟩🟩🟩🟩🟩🟩⬜⬜⬜⬜",          # [7]  emoji progress bar
    "Aftand tot locatie: 0.034 km", # [8]  distance string
    "",                             # [9]
    "",                             # [10]
    "aLMBr9-Ya-4Eeo0HeVzkz5 , uuAZS83JSsd6GsfzBHHuLQ",  # [11] capture keys
    "Nog -44:38:23 tot het spel begint!",                 # [12] status string
]

NEW_OWNER = "Chiro Oostham"

# ── FIXED (don't change these) ────────────────────────────────────────────────

APP_ID      = "04ed3225-8bdf-493a-b84b-4fcde458ee07"
APP_VERSION = "1.000390"
CLIENT_ID   = "1d5acc58-0eb2-40e8-8a23-5d8328758830"
BUILD       = "aaaaaaaaaaaaaaaaaaaa-1775105475616-34910e9b"

# ── SEND ──────────────────────────────────────────────────────────────────────

def change_owner(row: list, new_owner: str = NEW_OWNER):
    row = list(row)
    row[4] = new_owner          # column 4 = Team / owner

    def ts(offset_ms=0):
        from datetime import timedelta
        t = datetime.now(timezone.utc) + timedelta(milliseconds=offset_ms)
        return t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"

    settings = {
        "_RowNumber": "0", "_EMAIL": "guest", "_NAME": "Guest",
        "_LOCATION": "", "Team": new_owner,
        "Option 1": "", "Option 2": "", "Country Option": "",
        "Language Option": "", "Option 5": "", "Option 6": "",
        "Option 7": "", "Option 8": "", "Option 9": "",
        "_THISUSER": "onlyvalue",
    }

    import random
    resp = requests.post(
        f"https://www.appsheet.com/api/template/{APP_ID}/table/Locations/row/update",
        params={
            "tzOffset": "-120",
            "settings": json.dumps(settings),
            "apiLevel": "2",
            "isPreview": "false",
            "checkCache": "false",          # was "true" — caused cached no-op
            "locale": "en-US",
            "location": "null, null",
            "appTemplateVersion": APP_VERSION,
            "localVersion": APP_VERSION,
            "timestamp": ts(0),
            "requestStartTime": ts(3),      # slightly after timestamp, like the browser
            "lastSyncTime": ts(-30000),     # ~30s ago, like a real sync interval
            "appStartTime": ts(-60000),
            "dataStamp": ts(0),
            "clientId": CLIENT_ID,
            "build": BUILD,
            "requestId": str(random.randint(1_000_000, 99_999_999)),  # unique each call
            "syncToken": SYNC_TOKEN,
        },
        headers={
            "Content-Type": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": "https://www.appsheet.com",
            "Referer": f"https://www.appsheet.com/start/{APP_ID}",
        },
        json={"row": row, "pii": [False] * len(row)},
        timeout=30,
    )

    print(f"Status: {resp.status_code}")
    try:
        print(json.dumps(resp.json(), indent=2))
    except Exception:
        print(resp.text)
    return resp


def load_rows_from_file(file_path: Path) -> list:
    try:
        with file_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"rows file not found: {file_path}")
        return []
    except json.JSONDecodeError as exc:
        print(f"rows file is not valid JSON: {exc}")
        return []

    if not isinstance(data, list):
        print("rows file must contain a top-level list")
        return []

    return data


if __name__ == "__main__":
    rows_path = Path(__file__).with_name("")

    while True:
        if False: #TODO NEVER EVER CHANGE TO TRUE!!!!!!!!!
            rows = load_rows_from_file(rows_path)
            for index, row in enumerate(rows):
                if not isinstance(row, list) or len(row) < 5:
                    print(f"Skipping invalid row at index {index}")
                    continue
                change_owner(row, NEW_OWNER)
        sleep(10)
