"""
POI Claimer Dashboard — Flask backend
"""

import json
import logging
import os
import random
import base64
import time
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask, jsonify, render_template, request

# ── LOGGING ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

# ── CONFIG ────────────────────────────────────────────────────────────────────

SYNC_TOKEN = os.environ.get("SYNC_TOKEN", (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJhcHBJZCI6IjA0ZWQzMjI1LThiZGYtNDkzYS1iODRiLTRmY2RlNDU4ZWUwNyIsImFwcFZlcnNpb24iOiIxLjAwMDQyOSIsInVzZXJJZCI6Ii0xIiwiaWF0IjoxNzc1NTA3MTA5LCJleHAiOjE3ODMyODMxMDksImlzcyI6Imh0dHBzOi8vd3d3LmFwcHNoZWV0LmNvbSIsImF1ZCI6Imh0dHBzOi8vd3d3LmFwcHNoZWV0LmNvbSJ9"
    ".9Z3XiDHVAdv5lpuG8FwlB8WyWu_W2iOAFkC5slNHRns"
))

APP_ID      = os.environ.get("APP_ID",      "04ed3225-8bdf-493a-b84b-4fcde458ee07")
APP_VERSION = os.environ.get("APP_VERSION", "1.000429")
CLIENT_ID   = os.environ.get("CLIENT_ID",   "2fdccef5-01f6-4877-b7d4-5e6f58696259")
BUILD       = os.environ.get("BUILD",       "aaaaaaaaaaaaaaaaaaaa-1775242405640-9e8e0270")
NEW_OWNER   = os.environ.get("NEW_OWNER",   "Chiro Oostham")

ROWS_FILE   = Path(os.environ.get("ROWS_FILE",   "/data/rows.json"))
IMAGES_DIR  = Path(os.environ.get("IMAGES_DIR",  "/data/images"))

CLAIM_INTERVAL_MINUTES = int(os.environ.get("CLAIM_INTERVAL_MINUTES", "15"))
# Random extra delay between individual POI updates (seconds)
DELAY_MIN = float(os.environ.get("DELAY_MIN", "2"))
DELAY_MAX = float(os.environ.get("DELAY_MAX", "8"))

# ── STATE ─────────────────────────────────────────────────────────────────────

# selected_keys: set of row[1] (row key) values the user has ticked
selected_keys: set[str] = set()
state_lock = threading.Lock()

# Log of recent claim attempts shown in the dashboard
claim_log: list[dict] = []
MAX_LOG = 200

# ── LOAD ROWS ─────────────────────────────────────────────────────────────────

def load_rows() -> list[list]:
    with open(ROWS_FILE, encoding="utf-8") as f:
        return json.load(f)

ALL_ROWS: list[list] = load_rows()
# Build a lookup by row key for fast access
ROWS_BY_KEY: dict[str, list] = {r[1]: r for r in ALL_ROWS}

# ── IMAGE HELPERS ─────────────────────────────────────────────────────────────

def is_daytime() -> bool:
    hour = datetime.now().hour
    return 7 <= hour < 21


def pick_local_image(poi_name: str) -> str | None:
    """
    Look for images in images/<poi_name>/day/ or images/<poi_name>/night/.
    Returns a base64 data URI or None if no images found.
    """
    subfolder = "day" if is_daytime() else "night"
    folder = IMAGES_DIR / poi_name / subfolder

    if not folder.exists():
        # Fall back to the other time slot if the preferred one is empty
        other = "night" if subfolder == "day" else "day"
        folder = IMAGES_DIR / poi_name / other

    if not folder.exists():
        return None

    candidates = [
        p for p in folder.iterdir()
        if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".gif", ".webp"}
    ]
    if not candidates:
        return None

    chosen = random.choice(candidates)
    data   = chosen.read_bytes()
    ext    = chosen.suffix.lower().lstrip(".")
    mime   = {"jpg": "image/jpeg", "jpeg": "image/jpeg",
               "png": "image/png", "gif": "image/gif",
               "webp": "image/webp"}.get(ext, "image/jpeg")
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


def resolve_image(row: list, poi_name: str) -> str:
    """
    1. Try local images folder for this POI
    2. Fall back to existing URL in row[13] (last server image)
    3. Empty string
    """
    local = pick_local_image(poi_name)
    if local:
        return local
    return row[13] if len(row) > 13 else ""

# ── APPSHEET API ──────────────────────────────────────────────────────────────

def ts(offset_ms: int = 0) -> str:
    t = datetime.now(timezone.utc) + timedelta(milliseconds=offset_ms)
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"


def claim_poi(row: list) -> dict:
    """Send a Locations/row/update to claim a POI for NEW_OWNER."""
    row     = list(row)
    poi_name = row[2]
    row[4]  = NEW_OWNER
    row[13] = resolve_image(row, poi_name)

    img_source = "local" if row[13].startswith("data:") else ("server" if row[13] else "none")

    settings = {
        "_RowNumber": "0", "_EMAIL": "guest", "_NAME": "Guest",
        "_LOCATION": "", "Team": NEW_OWNER,
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

    try:
        resp   = requests.post(
            f"https://www.appsheet.com/api/template/{APP_ID}/table/Locations/row/update",
            params=params, headers=headers,
            json={"row": row, "pii": [False] * len(row)},
            timeout=30,
        )
        result = resp.json()
        ok     = result.get("Success") and not result.get("ReturnedFromCache")
        return {
            "poi":        poi_name,
            "key":        row[1],
            "status":     "ok" if ok else "warn",
            "http":       resp.status_code,
            "cached":     result.get("ReturnedFromCache"),
            "img_source": img_source,
            "ts":         datetime.now().strftime("%H:%M:%S"),
        }
    except Exception as e:
        return {
            "poi":        poi_name,
            "key":        row[1],
            "status":     "error",
            "http":       0,
            "cached":     False,
            "img_source": img_source,
            "error":      str(e),
            "ts":         datetime.now().strftime("%H:%M:%S"),
        }

# ── SCHEDULER JOB ─────────────────────────────────────────────────────────────

def claim_cycle():
    with state_lock:
        keys_to_claim = set(selected_keys)  # snapshot — never mutate during iteration

    if not keys_to_claim:
        log.info("Claim cycle: no POIs selected, skipping.")
        return

    log.info(f"Claim cycle starting: {len(keys_to_claim)} POIs selected.")

    # Shuffle order so it looks organic
    keys_list = list(keys_to_claim)
    random.shuffle(keys_list)

    for key in keys_list:
        # Re-check — user may have deselected mid-run
        with state_lock:
            if key not in selected_keys:
                log.info(f"  Skipping {key} (deselected mid-run)")
                continue

        row = ROWS_BY_KEY.get(key)
        if not row:
            log.warning(f"  Key {key} not found in rows data, skipping.")
            continue

        result = claim_poi(row)

        with state_lock:
            claim_log.insert(0, result)
            del claim_log[MAX_LOG:]

        status_str = result["status"].upper()
        log.info(f"  [{status_str}] {result['poi']} (img: {result['img_source']})")

        # Random delay between POIs
        delay = random.uniform(DELAY_MIN, DELAY_MAX)
        log.info(f"  Waiting {delay:.1f}s before next POI...")
        time.sleep(delay)

    log.info("Claim cycle complete.")


# ── FLASK APP ─────────────────────────────────────────────────────────────────

app = Flask(__name__)


@app.route("/")
def index():
    return render_template("index.html",
                           pois=ALL_ROWS,
                           new_owner=NEW_OWNER,
                           interval=CLAIM_INTERVAL_MINUTES)


@app.route("/api/pois")
def api_pois():
    with state_lock:
        sel = list(selected_keys)
    return jsonify({
        "pois": [
            {
                "key":      r[1],
                "name":     r[2],
                "coords":   r[3],
                "owner":    r[4],
                "gemeente": r[5],
                "progress": r[6],
                "bar":      r[7],
                "selected": r[1] in selected_keys,
            }
            for r in ALL_ROWS
        ],
        "selected": sel,
    })


@app.route("/api/select", methods=["POST"])
def api_select():
    data = request.json or {}
    key  = data.get("key")
    sel  = data.get("selected", True)

    if not key or key not in ROWS_BY_KEY:
        return jsonify({"error": "unknown key"}), 400

    with state_lock:
        if sel:
            selected_keys.add(key)
        else:
            selected_keys.discard(key)

    with state_lock:
        count = len(selected_keys)

    return jsonify({"key": key, "selected": sel, "total_selected": count})


@app.route("/api/select_all", methods=["POST"])
def api_select_all():
    data = request.json or {}
    keys = data.get("keys", [])
    sel  = data.get("selected", True)

    valid = [k for k in keys if k in ROWS_BY_KEY]
    with state_lock:
        if sel:
            selected_keys.update(valid)
        else:
            for k in valid:
                selected_keys.discard(k)
        count = len(selected_keys)

    return jsonify({"updated": len(valid), "total_selected": count})


@app.route("/api/claim_now", methods=["POST"])
def api_claim_now():
    """Trigger an immediate claim cycle in a background thread."""
    t = threading.Thread(target=claim_cycle, daemon=True)
    t.start()
    return jsonify({"started": True})


@app.route("/api/log")
def api_log():
    with state_lock:
        return jsonify({"log": list(claim_log)})


@app.route("/api/status")
def api_status():
    job = scheduler.get_job("claim_cycle")
    try:
        nrt = job.next_run_time if job else None
        next_run = nrt.strftime("%H:%M:%S") if nrt else "—"
    except AttributeError:
        next_run = "—"
    with state_lock:
        sel = len(selected_keys)
    return jsonify({
        "selected":    sel,
        "next_run":    next_run,
        "new_owner":   NEW_OWNER,
        "interval":    CLAIM_INTERVAL_MINUTES,
        "server_time": datetime.now().strftime("%H:%M:%S"),
        "daytime":     is_daytime(),
    })


# ── SCHEDULER ─────────────────────────────────────────────────────────────────

scheduler = BackgroundScheduler(timezone="Europe/Brussels")
scheduler.add_job(
    claim_cycle,
    trigger="interval",
    minutes=CLAIM_INTERVAL_MINUTES,
    id="claim_cycle",
    replace_existing=True,
)


if __name__ == "__main__":
    scheduler.start()
    log.info(f"Scheduler started — claiming every {CLAIM_INTERVAL_MINUTES} min for '{NEW_OWNER}'")
    app.run(host="0.0.0.0", port=5000, debug=False)
