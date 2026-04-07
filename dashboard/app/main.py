"""
POI Claimer Dashboard — Flask backend

Time modes
----------
NIGHT  01:00 – 08:00  → night_keys selected, interval = NIGHT_INTERVAL_MINUTES (15)
DAY    08:00 – 01:00  → day_keys selected,   interval = DAY_INTERVAL_MINUTES   (30)

Each cycle spreads its claims randomly across SPREAD_SECONDS (180–300 s).
Claims that don't fit within the interval window are simply skipped that round.
"""

import json
import logging
import os
import random
import base64
import time
import threading
from uuid import uuid4
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask, jsonify, render_template, request
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.utils import secure_filename

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

ROWS_FILE  = Path(os.environ.get("ROWS_FILE",  "/data/rows.json"))
IMAGES_DIR = Path(os.environ.get("IMAGES_DIR", "/data/images"))
MAX_IMAGE_SIZE_MB = int(os.environ.get("MAX_IMAGE_SIZE_MB", "10"))

ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
ALLOWED_IMAGE_MIME_TYPES = {
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
}

# Night = 01:00–08:00, Day = 08:00–01:00
NIGHT_START = int(os.environ.get("NIGHT_START", "1"))   # hour (24h)
NIGHT_END   = int(os.environ.get("NIGHT_END",   "8"))   # hour (24h)

NIGHT_INTERVAL_MINUTES = int(os.environ.get("NIGHT_INTERVAL_MINUTES", "15"))
DAY_INTERVAL_MINUTES   = int(os.environ.get("DAY_INTERVAL_MINUTES",   "30"))

# Spread: claims are randomly distributed across this many seconds
SPREAD_MIN = int(os.environ.get("SPREAD_MIN", "180"))   # 3 min
SPREAD_MAX = int(os.environ.get("SPREAD_MAX", "300"))   # 5 min

# ── STATE ─────────────────────────────────────────────────────────────────────

# Two independent selection sets — night claims more aggressively
night_keys: set = set()
day_keys:   set = set()
state_lock = threading.Lock()

# Activity log
claim_log: list = []
MAX_LOG = 300

# Prevent overlapping cycles
cycle_running = False

# ── LOAD ROWS ─────────────────────────────────────────────────────────────────

def load_rows():
    with open(ROWS_FILE, encoding="utf-8") as f:
        return json.load(f)

ALL_ROWS = load_rows()
ROWS_BY_KEY = {r[1]: r for r in ALL_ROWS}

# ── TIME HELPERS ──────────────────────────────────────────────────────────────

def is_night() -> bool:
    hour = datetime.now().hour
    return NIGHT_START <= hour < NIGHT_END


def current_mode() -> str:
    return "night" if is_night() else "day"


def current_interval() -> int:
    return NIGHT_INTERVAL_MINUTES if is_night() else DAY_INTERVAL_MINUTES


def ts(offset_ms: int = 0) -> str:
    t = datetime.now(timezone.utc) + timedelta(milliseconds=offset_ms)
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"

# ── IMAGE HELPERS ─────────────────────────────────────────────────────────────

def pick_local_image(poi_name: str) -> str:
    """
    Try images/<poi_name>/night/ or images/<poi_name>/day/ depending on time.
    Falls back to the other subfolder, then to empty string.
    Returns a base64 data URI or "".
    """
    preferred = "night" if is_night() else "day"
    fallback  = "day"   if preferred == "night" else "night"

    for sub in (preferred, fallback):
        folder = IMAGES_DIR / poi_name / sub
        if not folder.exists():
            continue
        candidates = [
            p for p in folder.iterdir()
            if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".gif", ".webp"}
        ]
        if candidates:
            chosen = random.choice(candidates)
            data   = chosen.read_bytes()
            ext    = chosen.suffix.lower().lstrip(".")
            mime   = {"jpg": "image/jpeg", "jpeg": "image/jpeg",
                      "png": "image/png",  "gif": "image/gif",
                      "webp": "image/webp"}.get(ext, "image/jpeg")
            return f"data:{mime};base64,{base64.b64encode(data).decode()}"
    return ""


def resolve_image(row: list, poi_name: str) -> str:
    local = pick_local_image(poi_name)
    if local:
        return local
    return row[13] if len(row) > 13 else ""


def is_allowed_upload(filename: str, mimetype: str) -> bool:
    ext = Path(filename).suffix.lower()
    return ext in ALLOWED_IMAGE_EXTENSIONS and mimetype in ALLOWED_IMAGE_MIME_TYPES

# ── APPSHEET API ──────────────────────────────────────────────────────────────

def claim_poi(row: list) -> dict:
    row      = list(row)
    poi_name = row[2]
    row[4]   = NEW_OWNER
    row[13]  = resolve_image(row, poi_name)

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
            "mode":       current_mode(),
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
            "mode":       current_mode(),
            "error":      str(e),
            "ts":         datetime.now().strftime("%H:%M:%S"),
        }

# ── SCHEDULER JOB ─────────────────────────────────────────────────────────────

def claim_cycle():
    global cycle_running
    with state_lock:
        if cycle_running:
            log.info("Cycle already running, skipping this tick.")
            return
        cycle_running = True

        mode = current_mode()
        keys_to_claim = set(night_keys if mode == "night" else day_keys)

    if not keys_to_claim:
        log.info(f"Claim cycle ({mode}): no POIs in {mode} list, skipping.")
        with state_lock:
            cycle_running = False
        return

    # Spread claims randomly across SPREAD_MIN–SPREAD_MAX seconds
    spread_secs  = random.randint(SPREAD_MIN, SPREAD_MAX)
    interval_sec = current_interval() * 60
    # Cap spread to the interval so we never bleed into the next cycle
    spread_secs  = min(spread_secs, interval_sec - 10)

    keys_list = list(keys_to_claim)
    random.shuffle(keys_list)

    # Generate sorted random offsets within spread window
    offsets = sorted(random.uniform(0, spread_secs) for _ in keys_list)

    log.info(f"Claim cycle ({mode}): {len(keys_list)} POIs spread over {spread_secs}s")

    cycle_start = time.monotonic()

    with state_lock:
        claim_log.insert(0, {
            "poi":    f"— {mode.upper()} cycle started ({len(keys_list)} POIs, spread {spread_secs}s) —",
            "key":    "",
            "status": "info",
            "ts":     datetime.now().strftime("%H:%M:%S"),
            "mode":   mode,
        })
        del claim_log[MAX_LOG:]

    for i, key in enumerate(keys_list):
        # Wait until this POI's scheduled offset
        target_elapsed = offsets[i]
        now_elapsed    = time.monotonic() - cycle_start
        wait_for       = target_elapsed - now_elapsed
        if wait_for > 0:
            time.sleep(wait_for)

        # Re-check selection — user may have deselected mid-run
        with state_lock:
            active_set = night_keys if mode == "night" else day_keys
            if key not in active_set:
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

        log.info(f"  [{result['status'].upper()}] {result['poi']} (img:{result['img_source']})")

    log.info(f"Claim cycle ({mode}) complete. Total elapsed: {time.monotonic()-cycle_start:.1f}s")
    with state_lock:
        cycle_running = False


# ── FLASK APP ─────────────────────────────────────────────────────────────────

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_IMAGE_SIZE_MB * 1024 * 1024


@app.errorhandler(RequestEntityTooLarge)
def handle_request_too_large(_err):
    return jsonify({"error": f"file too large (max {MAX_IMAGE_SIZE_MB} MB)"}), 413


@app.route("/")
def index():
    return render_template("index.html",
                           pois=ALL_ROWS,
                           new_owner=NEW_OWNER,
                           night_interval=NIGHT_INTERVAL_MINUTES,
                           day_interval=DAY_INTERVAL_MINUTES,
                           night_start=NIGHT_START,
                           night_end=NIGHT_END)


@app.route("/api/pois")
def api_pois():
    with state_lock:
        nk = set(night_keys)
        dk = set(day_keys)
    return jsonify({
        "pois": [
            {
                "key":      r[1],
                "name":     r[2],
                "gemeente": r[5],
                "night":    r[1] in nk,
                "day":      r[1] in dk,
            }
            for r in ALL_ROWS
        ],
    })


@app.route("/api/select", methods=["POST"])
def api_select():
    data = request.json or {}
    key  = data.get("key")
    mode = data.get("mode", "night")   # "night" or "day"
    sel  = data.get("selected", True)

    if not key or key not in ROWS_BY_KEY:
        return jsonify({"error": "unknown key"}), 400

    with state_lock:
        target = night_keys if mode == "night" else day_keys
        if sel:
            target.add(key)
        else:
            target.discard(key)
        counts = {"night": len(night_keys), "day": len(day_keys)}

    return jsonify({"key": key, "mode": mode, "selected": sel, "counts": counts})


@app.route("/api/select_all", methods=["POST"])
def api_select_all():
    data = request.json or {}
    keys = data.get("keys", [])
    mode = data.get("mode", "night")
    sel  = data.get("selected", True)

    valid = [k for k in keys if k in ROWS_BY_KEY]
    with state_lock:
        target = night_keys if mode == "night" else day_keys
        if sel:
            target.update(valid)
        else:
            for k in valid:
                target.discard(k)
        counts = {"night": len(night_keys), "day": len(day_keys)}

    return jsonify({"updated": len(valid), "counts": counts})


@app.route("/api/upload_image", methods=["POST"])
def api_upload_image():
    key = (request.form.get("key") or "").strip()
    mode = (request.form.get("mode") or "").strip().lower()
    file = request.files.get("image")

    if key not in ROWS_BY_KEY:
        return jsonify({"error": "unknown key"}), 400
    if mode not in {"day", "night"}:
        return jsonify({"error": "mode must be day or night"}), 400
    if not file or not file.filename:
        return jsonify({"error": "image file is required"}), 400

    filename = secure_filename(file.filename)
    mimetype = (file.mimetype or "").lower()
    if not filename or not is_allowed_upload(filename, mimetype):
        return jsonify({"error": "only jpg, png, gif, and webp images are allowed"}), 400

    row = ROWS_BY_KEY[key]
    poi_name = str(row[2]).strip()
    poi_dir = IMAGES_DIR / poi_name / mode
    images_root = IMAGES_DIR.resolve()
    target_dir = poi_dir.resolve()

    # Ensure uploads cannot escape the configured images root.
    if images_root not in target_dir.parents and target_dir != images_root:
        return jsonify({"error": "invalid target path"}), 400

    target_dir.mkdir(parents=True, exist_ok=True)

    ext = Path(filename).suffix.lower()
    out_name = f"upload-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:8]}{ext}"
    out_path = target_dir / out_name
    file.save(out_path)

    return jsonify({
        "ok": True,
        "key": key,
        "poi": poi_name,
        "mode": mode,
        "file": out_name,
    })


@app.route("/api/claim_now", methods=["POST"])
def api_claim_now():
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
        nrt      = job.next_run_time if job else None
        next_run = nrt.strftime("%H:%M:%S") if nrt else "—"
    except AttributeError:
        next_run = "—"

    with state_lock:
        nk  = len(night_keys)
        dk  = len(day_keys)
        run = cycle_running

    mode = current_mode()
    return jsonify({
        "mode":           mode,
        "night_selected": nk,
        "day_selected":   dk,
        "next_run":       next_run,
        "new_owner":      NEW_OWNER,
        "interval":       current_interval(),
        "night_interval": NIGHT_INTERVAL_MINUTES,
        "day_interval":   DAY_INTERVAL_MINUTES,
        "server_time":    datetime.now().strftime("%H:%M:%S"),
        "running":        run,
        "spread_min":     SPREAD_MIN,
        "spread_max":     SPREAD_MAX,
    })


# ── SCHEDULER ─────────────────────────────────────────────────────────────────
# Single job fires every minute and internally checks which interval applies.
# This avoids rescheduling complexity when mode switches at runtime.

_last_fired: dict = {"night": None, "day": None}

def smart_tick():
    """Called every minute. Fires a cycle when the interval for current mode has elapsed."""
    global _last_fired
    mode     = current_mode()
    interval = current_interval() * 60   # seconds
    now      = time.monotonic()
    last     = _last_fired.get(mode)

    if last is None or (now - last) >= interval:
        _last_fired[mode] = now
        log.info(f"smart_tick: triggering {mode} cycle")
        t = threading.Thread(target=claim_cycle, daemon=True)
        t.start()
    else:
        remaining = interval - (now - last)
        log.debug(f"smart_tick: {mode} — {remaining:.0f}s until next cycle")


scheduler = BackgroundScheduler(timezone="Europe/Brussels")
scheduler.add_job(
    smart_tick,
    trigger="interval",
    minutes=1,
    id="smart_tick",
    replace_existing=True,
)


if __name__ == "__main__":
    scheduler.start()
    log.info(f"Scheduler started — night every {NIGHT_INTERVAL_MINUTES}min, day every {DAY_INTERVAL_MINUTES}min")
    app.run(host="0.0.0.0", port=5000, debug=False)
