"""
Fetch the full AppSheet database dump (all tables).
Returns: Locations (90 rows), Captures (546 rows), Score (12 rows), Nested table (100 rows)
"""

import requests
import json
from datetime import datetime, timezone

SYNC_TOKEN = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJhcHBJZCI6IjA0ZWQzMjI1LThiZGYtNDkzYS1iODRiLTRmY2RlNDU4ZWUwNyIsImFwcFZlcnNpb24iOiIxLjAwMDQyOSIsInVzZXJJZCI6Ii0xIiwiaWF0IjoxNzc1NTA3MTA5LCJleHAiOjE3ODMyODMxMDksImlzcyI6Imh0dHBzOi8vd3d3LmFwcHNoZWV0LmNvbSIsImF1ZCI6Imh0dHBzOi8vd3d3LmFwcHNoZWV0LmNvbSJ9"
    ".9Z3XiDHVAdv5lpuG8FwlB8WyWu_W2iOAFkC5slNHRns"
)

APP_ID      = "04ed3225-8bdf-493a-b84b-4fcde458ee07"
APP_VERSION = "1.000429"
CLIENT_ID   = "2fdccef5-01f6-4877-b7d4-5e6f58696259"
BUILD       = "aaaaaaaaaaaaaaaaaaaa-1775242405640-9e8e0270"

def fetch_database():
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    body = {
        "settings": json.dumps({
            "_RowNumber": "0", "_EMAIL": "", "_NAME": "", "_LOCATION": "",
            "Team": "", "Option 1": "", "Option 2": "", "Country Option": "",
            "Language Option": "", "Option 5": "", "Option 6": "",
            "Option 7": "", "Option 8": "", "Option 9": "", "_THISUSER": "onlyvalue"
        }),
        "getAllTables": True,
        "syncsOnConsent": True,
        "syncUI": "Blocking",
        "initiatedBy": "AppStart",
        "isPreview": False,
        "apiLevel": 2,
        "supportsJsonDataSets": True,
        "tzOffset": -120,
        "locale": "en-US",
        "perTableParams": {
            "Locations":     {"time": "0001-01-01T00:00:00", "etag": ""},
            "Score":         {"time": "0001-01-01T00:00:00", "etag": ""},
            "Captures":      {"time": "0001-01-01T00:00:00", "etag": ""},
            "Nested table":  {"time": "0001-01-01T00:00:00", "etag": ""},
        },
        "lastSyncTime": now,
        "appStartTime": now,
        "dataStamp": now,
        "clientId": CLIENT_ID,
        "build": BUILD,
        "hasValidPlan": True,
        "userConsentedScopes": "data_input,device_identity,device_io,location,usage",
        "localVersion": APP_VERSION,
        "location": "0.000000, 0.000000",
        "syncToken": SYNC_TOKEN,
    }

    resp = requests.post(
        f"https://www.appsheet.com/api/template/{APP_ID}/",
        headers={
            "Content-Type": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": "https://www.appsheet.com",
            "Referer": f"https://www.appsheet.com/start/{APP_ID}",
        },
        json=body,
        timeout=30,
    )

    data = resp.json()

    if not data.get("Success"):
        print("Sync failed:", data.get("ErrorDescription"))
        return

    print(f"Owner email: {data['OwnerEmail']}")
    for ds in data["NestedDataSets"]:
        table = json.loads(ds["DataSet"])
        rows = table["data"]
        cols = table["columns"]
        print(f"\nTable: {ds['Name']} ({len(rows)} rows)")
        print(f"Columns: {cols}")
        for row in rows:
            print(row)

    # Save full raw response
    with open("db_dump.json", "w") as f:
        json.dump(data, f, indent=2)
    print("\nFull dump saved to db_dump.json")

    # Save Locations rows to rows.json
    for ds in data["NestedDataSets"]:
        if ds["Name"] == "Locations":
            rows = json.loads(ds["DataSet"])["data"]
            with open("rows.json", "w", encoding="utf-8") as f:
                json.dump(rows, f, indent=2, ensure_ascii=False)
            print(f"rows.json saved ({len(rows)} POIs)")

if __name__ == "__main__":
    fetch_database()