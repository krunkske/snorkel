"""
Change the owner of every POI in rows.json to NEW_OWNER.

Image handling:
  - If a custom base64 image path is passed via --image, it is sent inline
    as a data URI (same format as a fresh capture).
  - Otherwise the existing image URL already in the row is reused as-is
    (i.e. the last capture photo that was on the server).
  - If the row has no image at all, the field is left empty.

Usage:
  python change_poi_owner.py                        # reuse existing images
  python change_poi_owner.py --image photo.png      # attach new image to all POIs
  python change_poi_owner.py --dry-run              # print rows without sending
"""

import requests
import json
import random
import sys
import base64
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ── CONFIGURE ─────────────────────────────────────────────────────────────────

NEW_OWNER  = "Chiro Oostham"
ROWS_FILE  = "rows.json"

# Grab a fresh syncToken from a new HAR when this expires (~90 days).
SYNC_TOKEN = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJhcHBJZCI6IjA0ZWQzMjI1LThiZGYtNDkzYS1iODRiLTRmY2RlNDU4ZWUwNyIsImFwcFZlcnNpb24iOiIxLjAwMDQyOSIsInVzZXJJZCI6Ii0xIiwiaWF0IjoxNzc1NTA3MTA5LCJleHAiOjE3ODMyODMxMDksImlzcyI6Imh0dHBzOi8vd3d3LmFwcHNoZWV0LmNvbSIsImF1ZCI6Imh0dHBzOi8vd3d3LmFwcHNoZWV0LmNvbSJ9"
    ".9Z3XiDHVAdv5lpuG8FwlB8WyWu_W2iOAFkC5slNHRns"
)

# ── FIXED ─────────────────────────────────────────────────────────────────────

APP_ID      = "04ed3225-8bdf-493a-b84b-4fcde458ee07"
APP_VERSION = "1.000429"
CLIENT_ID   = "2fdccef5-01f6-4877-b7d4-5e6f58696259"
BUILD       = "aaaaaaaaaaaaaaaaaaaa-1775242405640-9e8e0270"

# ── HELPERS ───────────────────────────────────────────────────────────────────

def ts(offset_ms=0):
    t = datetime.now(timezone.utc) + timedelta(milliseconds=offset_ms)
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"


def load_image_as_data_uri(path):
    """Read an image file and return it as a base64 data URI."""
    data = Path(path).read_bytes()
    ext  = Path(path).suffix.lower().lstrip(".")
    mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg",
            "png": "image/png",  "gif": "image/gif",
            "webp": "image/webp"}.get(ext, "image/jpeg")
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


def resolve_image(row, custom_image_uri):
    """
    Return the image value for column [13]:
      1. custom_image_uri if supplied (base64 data URI of a new photo)
      2. existing URL already in the row (last server-side capture photo)
      3. empty string if the row has no image
    """
    if custom_image_uri:
        return custom_image_uri
    return row[13] if len(row) > 13 else ""


def change_owner(row, new_owner, image_uri=None, dry_run=False):
    row    = list(row)
    row[4] = new_owner
    row[13] = resolve_image(row, image_uri)

    settings = {
        "_RowNumber": "0", "_EMAIL": "guest", "_NAME": "Guest",
        "_LOCATION": "", "Team": new_owner,
        "Option 1": "", "Option 2": "", "Country Option": "",
        "Language Option": "", "Option 5": "", "Option 6": "",
        "Option 7": "", "Option 8": "", "Option 9": "",
        "_THISUSER": "onlyvalue",
    }

    params = {
        "tzOffset":           "-120",
        "settings":           json.dumps(settings),
        "apiLevel":           "2",
        "isPreview":          "false",
        "checkCache":         "false",
        "locale":             "en-US",
        "location":           "null, null",
        "appTemplateVersion": APP_VERSION,
        "localVersion":       APP_VERSION,
        "timestamp":          ts(0),
        "requestStartTime":   ts(3),
        "lastSyncTime":       ts(-30000),
        "appStartTime":       ts(-60000),
        "dataStamp":          ts(0),
        "clientId":           CLIENT_ID,
        "build":              BUILD,
        "requestId":          str(random.randint(1_000_000, 99_999_999)),
        "syncToken":          SYNC_TOKEN,
    }

    headers = {
        "Content-Type":     "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "Origin":           "https://www.appsheet.com",
        "Referer":          f"https://www.appsheet.com/start/{APP_ID}",
    }

    body = {"row": row, "pii": [False] * len(row)}

    img_label = "new image" if image_uri else ("existing image" if row[13] else "no image")
    print(f"  [{row[0]:>3}] {row[2]:<40} owner→{new_owner}  ({img_label})")

    if dry_run:
        return None

    resp   = requests.post(
        f"https://www.appsheet.com/api/template/{APP_ID}/table/Locations/row/update",
        params=params, headers=headers, json=body, timeout=30,
    )
    result = resp.json()
    ok     = result.get("Success") and not result.get("ReturnedFromCache")
    status = "OK" if ok else f"WARN cache={result.get('ReturnedFromCache')} success={result.get('Success')}"
    print(f"       → {resp.status_code} {status}")
    return result


# ── MAIN ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    dry_run   = "--dry-run" in sys.argv
    image_uri = None

    if "--image" in sys.argv:
        idx = sys.argv.index("--image")
        try:
            image_path = sys.argv[idx + 1]
            image_uri  = load_image_as_data_uri(image_path)
            print(f"Custom image loaded: {image_path} ({len(image_uri) // 1024} KB as data URI)")
        except (IndexError, FileNotFoundError) as e:
            print(f"Error loading image: {e}")
            sys.exit(1)

    with open(ROWS_FILE, encoding="utf-8") as f:
        rows = json.load(f)

    print(f"{'[DRY RUN] ' if dry_run else ''}Updating {len(rows)} POIs -> '{NEW_OWNER}'")
    print("-" * 60)

    ok_count   = 0
    fail_count = 0

    for row in rows:
        result = change_owner(row, NEW_OWNER, image_uri=image_uri, dry_run=dry_run)
        if result is not None:
            if result.get("Success") and not result.get("ReturnedFromCache"):
                ok_count += 1
            else:
                fail_count += 1
            time.sleep(0.3)  # be gentle with the server

    if not dry_run:
        print("-" * 60)
        print(f"Done. {ok_count} succeeded, {fail_count} failed.")