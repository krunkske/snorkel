"""
POI Claimer — Game Management Dashboard
Flask backend
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

SYNC_TOKEN  = os.environ.get("SYNC_TOKEN", (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJhcHBJZCI6IjA0ZWQzMjI1LThiZGYtNDkzYS1iODRiLTRmY2RlNDU4ZWUwNyIsImFwcFZlcnNpb24iOiIxLjAwMDQyOSIsInVzZXJJZCI6Ii0xIiwiaWF0IjoxNzc1NTA3MTA5LCJleHAiOjE3ODMyODMxMDksImlzcyI6Imh0dHBzOi8vd3d3LmFwcHNoZWV0LmNvbSIsImF1ZCI6Imh0dHBzOi8vd3d3LmFwcHNoZWV0LmNvbSJ9"
    ".9Z3XiDHVAdv5lpuG8FwlB8WyWu_W2iOAFkC5slNHRns"
))
APP_ID      = os.environ.get("APP_ID",      "04ed3225-8bdf-493a-b84b-4fcde458ee07")
APP_VERSION = os.environ.get("APP_VERSION", "1.000429")
CLIENT_ID   = os.environ.get("CLIENT_ID",   "2fdccef5-01f6-4877-b7d4-5e6f58696259")
BUILD       = os.environ.get("BUILD",       "aaaaaaaaaaaaaaaaaaaa-1775242405640-9e8e0270")
NEW_OWNER   = os.environ.get("NEW_OWNER",   "Chiro Oostham")

IMAGES_DIR  = Path(os.environ.get("IMAGES_DIR", "/data/images"))
IMAGES_DIR.mkdir(parents=True, exist_ok=True)

# ── STATE (in-memory, persisted to state.json) ────────────────────────────────

STATE_FILE = Path(os.environ.get("STATE_FILE", "/data/state.json"))

def default_state():
    return {
        "night_keys":    [],   # row keys selected for night
        "day_keys":      [],   # row keys selected for day
        "loop_enabled":  False,
        "loop_interval": 4,    # minutes between polls
        "use_local_images": True,
        "night_start":   1,    # hour 0-23
        "night_end":     8,    # hour 0-23
    }

def load_state():
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE) as f:
                saved = json.load(f)
            base = default_state()
            base.update(saved)
            return base
        except Exception:
            pass
    return default_state()

def save_state():
    with state_lock:
        snapshot = {
            "night_keys":       list(night_keys),
            "day_keys":         list(day_keys),
            "loop_enabled":     loop_enabled,
            "loop_interval":    loop_interval,
            "use_local_images": use_local_images,
            "night_start":      night_start,
            "night_end":        night_end,
        }
    with open(STATE_FILE, "w") as f:
        json.dump(snapshot, f, indent=2)

_state = load_state()

state_lock      = threading.Lock()
night_keys      = set(_state["night_keys"])
day_keys        = set(_state["day_keys"])
loop_enabled    = _state["loop_enabled"]
loop_interval   = _state["loop_interval"]   # minutes
use_local_images = _state["use_local_images"]
night_start     = _state["night_start"]
night_end       = _state["night_end"]

# Activity log
activity_log = []
MAX_LOG      = 300
log_lock     = threading.Lock()

# Cached live POI data (from last sync)
poi_cache     = {}   # key -> row dict
poi_cache_ts  = None
cache_lock    = threading.Lock()

# Prevent overlapping loops
loop_running = False
loop_lock    = threading.Lock()

# ── TIME HELPERS ──────────────────────────────────────────────────────────────

def is_night() -> bool:
    h = datetime.now().hour
    return night_start <= h < night_end

def current_mode() -> str:
    return "night" if is_night() else "day"

def active_keys() -> set:
    with state_lock:
        return set(night_keys if is_night() else day_keys)

def ts_str(offset_ms: int = 0) -> str:
    t = datetime.now(timezone.utc) + timedelta(milliseconds=offset_ms)
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"

def log_event(entry: dict):
    entry["ts"] = datetime.now().strftime("%H:%M:%S")
    with log_lock:
        activity_log.insert(0, entry)
        del activity_log[MAX_LOG:]

# ── IMAGE HELPERS ─────────────────────────────────────────────────────────────

def pick_local_image(poi_name: str) -> str:
    """Return base64 data URI from images/<poi_name>/(day|night)/ or ''."""
    sub  = "night" if is_night() else "day"
    other = "day" if sub == "night" else "night"
    for folder_name in (sub, other):
        folder = IMAGES_DIR / poi_name / folder_name
        if not folder.exists():
            continue
        imgs = [p for p in folder.iterdir()
                if p.suffix.lower() in {".jpg",".jpeg",".png",".gif",".webp"}]
        if imgs:
            chosen = random.choice(imgs)
            raw    = chosen.read_bytes()
            ext    = chosen.suffix.lower().lstrip(".")
            mime   = {"jpg":"image/jpeg","jpeg":"image/jpeg","png":"image/png",
                      "gif":"image/gif","webp":"image/webp"}.get(ext,"image/jpeg")
            return f"data:{mime};base64,{base64.b64encode(raw).decode()}"
    return ""

def resolve_image(row: dict) -> str:
    """Pick image based on use_local_images toggle."""
    with state_lock:
        local_pref = use_local_images
    if local_pref:
        local = pick_local_image(row["name"])
        if local:
            return local
    # Fall back to server image
    return row.get("image", "")

def list_images(poi_name: str) -> dict:
    """Return {day: [filenames], night: [filenames]} for a POI."""
    result = {"day": [], "night": []}
    for slot in ("day", "night"):
        folder = IMAGES_DIR / poi_name / slot
        if folder.exists():
            result[slot] = sorted(
                p.name for p in folder.iterdir()
                if p.suffix.lower() in {".jpg",".jpeg",".png",".gif",".webp"}
            )
    return result

# ── APPSHEET API ──────────────────────────────────────────────────────────────

def appsheet_sync() -> list[dict] | None:
    """
    Fetch all Locations from AppSheet.
    Returns list of dicts or None on failure.
    """
    now = ts_str()
    body = {
        "settings": json.dumps({
            "_RowNumber":"0","_EMAIL":"","_NAME":"","_LOCATION":"",
            "Team":"","Option 1":"","Option 2":"","Country Option":"",
            "Language Option":"","Option 5":"","Option 6":"","Option 7":"",
            "Option 8":"","Option 9":"","_THISUSER":"onlyvalue"
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
            "Locations":    {"time": "0001-01-01T00:00:00", "etag": ""},
            "Score":        {"time": "0001-01-01T00:00:00", "etag": ""},
            "Captures":     {"time": "0001-01-01T00:00:00", "etag": ""},
            "Nested table": {"time": "0001-01-01T00:00:00", "etag": ""},
        },
        "lastSyncTime":  now,
        "appStartTime":  now,
        "dataStamp":     now,
        "clientId":      CLIENT_ID,
        "build":         BUILD,
        "hasValidPlan":  True,
        "userConsentedScopes": "data_input,device_identity,device_io,location,usage",
        "localVersion":  APP_VERSION,
        "location":      "0.000000, 0.000000",
        "syncToken":     SYNC_TOKEN,
    }
    try:
        resp = requests.post(
            f"https://www.appsheet.com/api/template/{APP_ID}/",
            headers={
                "Content-Type":     "application/json",
                "X-Requested-With": "XMLHttpRequest",
                "Origin":           "https://www.appsheet.com",
                "Referer":          f"https://www.appsheet.com/start/{APP_ID}",
            },
            json=body,
            timeout=30,
        )
        data = resp.json()
        if not data.get("Success"):
            log.error(f"Sync failed: {data.get('ErrorDescription')}")
            return None

        # Parse Locations table
        pois = []
        for ds in data.get("NestedDataSets", []):
            if ds["Name"] == "Locations":
                table = json.loads(ds["DataSet"])
                cols  = table["columns"]
                # Build index map
                idx = {c: i for i, c in enumerate(cols)}
                for row in table["data"]:
                    def g(col, default=""):
                        i = idx.get(col)
                        return row[i] if i is not None and i < len(row) else default
                    pois.append({
                        "row_num":  g("_RowNumber"),
                        "key":      g("Row ID"),
                        "name":     g("Names"),
                        "coords":   g("Location"),
                        "owner":    g("Owner"),
                        "gemeente": g("Gemeente"),
                        "progress": g("Progress"),
                        "bar":      g("Progressbar"),
                        "image":    g("Image"),
                        # keep full raw row for update call
                        "_raw":     row,
                        "_cols":    cols,
                    })
                break
        return pois
    except Exception as e:
        log.error(f"Sync exception: {e}")
        return None


def appsheet_claim(poi: dict) -> dict:
    """
    Claim a single POI for NEW_OWNER.
    poi must have _raw and _cols from appsheet_sync().
    """
    raw  = list(poi["_raw"])
    cols = poi["_cols"]
    idx  = {c: i for i, c in enumerate(cols)}

    # Set owner
    if "Owner" in idx:
        raw[idx["Owner"]] = NEW_OWNER

    # Set image (last column = Image based on our earlier analysis)
    img = resolve_image(poi)
    if "Image" in idx:
        raw[idx["Image"]] = img

    img_source = "local" if img.startswith("data:") else ("server" if img else "none")

    settings = {
        "_RowNumber":"0","_EMAIL":"guest","_NAME":"Guest","_LOCATION":"",
        "Team": NEW_OWNER,
        "Option 1":"","Option 2":"","Country Option":"","Language Option":"",
        "Option 5":"","Option 6":"","Option 7":"","Option 8":"","Option 9":"",
        "_THISUSER":"onlyvalue",
    }

    def mk_ts(offset_ms=0):
        t = datetime.now(timezone.utc) + timedelta(milliseconds=offset_ms)
        return t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"

    try:
        resp = requests.post(
            f"https://www.appsheet.com/api/template/{APP_ID}/table/Locations/row/update",
            params={
                "tzOffset":           "-120",
                "settings":           json.dumps(settings),
                "apiLevel":           "2",
                "isPreview":          "false",
                "checkCache":         "false",
                "locale":             "en-US",
                "location":           "null, null",
                "appTemplateVersion": APP_VERSION,
                "localVersion":       APP_VERSION,
                "timestamp":          mk_ts(0),
                "requestStartTime":   mk_ts(3),
                "lastSyncTime":       mk_ts(-30000),
                "appStartTime":       mk_ts(-60000),
                "dataStamp":          mk_ts(0),
                "clientId":           CLIENT_ID,
                "build":              BUILD,
                "requestId":          str(random.randint(1_000_000, 99_999_999)),
                "syncToken":          SYNC_TOKEN,
            },
            headers={
                "Content-Type":     "application/json",
                "X-Requested-With": "XMLHttpRequest",
                "Origin":           "https://www.appsheet.com",
                "Referer":          f"https://www.appsheet.com/start/{APP_ID}",
            },
            json={"row": raw, "pii": [False] * len(raw)},
            timeout=30,
        )
        result = resp.json()
        ok = result.get("Success") and not result.get("ReturnedFromCache")
        return {"ok": ok, "http": resp.status_code,
                "cached": result.get("ReturnedFromCache"),
                "img_source": img_source}
    except Exception as e:
        return {"ok": False, "error": str(e), "img_source": img_source}

# ── MAIN LOOP ─────────────────────────────────────────────────────────────────

def run_loop(force_all: bool = False):
    """
    Core logic:
    1. Sync live POI data from AppSheet
    2. Check which selected POIs have the wrong owner
    3. If force_all: claim all of them immediately
       Otherwise:    claim exactly 1 and stop
    """
    global loop_running
    with loop_lock:
        if loop_running and not force_all:
            log.info("Loop already running, skipping tick.")
            return
        loop_running = True

    try:
        mode = current_mode()
        keys = active_keys() if not force_all else (
            with_lock(lambda: night_keys | day_keys)
        )

        log_event({"type": "poll", "msg": f"Syncing live data ({mode} mode)…", "mode": mode})
        log.info(f"run_loop: syncing ({mode}, force_all={force_all})")

        pois = appsheet_sync()
        if pois is None:
            log_event({"type": "error", "msg": "Sync failed — check credentials/network."})
            return

        # Update cache
        with cache_lock:
            global poi_cache, poi_cache_ts
            poi_cache    = {p["key"]: p for p in pois}
            poi_cache_ts = datetime.now().strftime("%H:%M:%S")

        # Find POIs that need claiming
        to_claim = []
        for poi in pois:
            key = poi["key"]
            if key not in keys:
                continue
            if poi["owner"] != NEW_OWNER:
                to_claim.append(poi)

        if not to_claim:
            log_event({"type": "ok", "msg": f"All {len(keys)} selected POIs already owned by {NEW_OWNER}.", "mode": mode})
            log.info("run_loop: nothing to claim.")
            return

        log_event({
            "type": "info",
            "msg":  f"{len(to_claim)} POI(s) need reclaiming.",
            "mode": mode,
        })

        targets = to_claim if force_all else [random.choice(to_claim)]

        for poi in targets:
            log.info(f"  Claiming: {poi['name']} (currently owned by {poi['owner']})")
            result = appsheet_claim(poi)

            log_event({
                "type":      "claim_ok" if result["ok"] else "claim_warn",
                "poi":       poi["name"],
                "prev_owner": poi["owner"],
                "img":       result.get("img_source", "?"),
                "mode":      mode,
                "msg":       f"{'✓' if result['ok'] else '⚠'} {poi['name']} ← {poi['owner']}",
            })

            if force_all:
                time.sleep(0.5)   # tiny pause between bulk claims

        log.info(f"run_loop: done. Claimed {len(targets)} POI(s).")

    finally:
        with loop_lock:
            loop_running = False


def with_lock(fn):
    with state_lock:
        return fn()

# ── SCHEDULER ─────────────────────────────────────────────────────────────────

scheduler = BackgroundScheduler(timezone="Europe/Brussels")

def scheduler_tick():
    with state_lock:
        enabled  = loop_enabled
        interval = loop_interval
    if not enabled:
        return
    # Check if interval has elapsed since last run
    with loop_lock:
        running = loop_running
    if running:
        return
    threading.Thread(target=run_loop, daemon=True).start()

# Tick every minute; actual interval enforced inside run_loop via APScheduler
# We reschedule dynamically when interval changes
def reschedule(minutes: int):
    if scheduler.get_job("main_loop"):
        scheduler.remove_job("main_loop")
    scheduler.add_job(
        scheduler_tick,
        trigger="interval",
        minutes=minutes,
        id="main_loop",
        replace_existing=True,
    )

# ── FLASK APP ─────────────────────────────────────────────────────────────────

app = Flask(__name__)

@app.route("/")
def index():
    return render_template("index.html", new_owner=NEW_OWNER)


# ── POI DATA ──────────────────────────────────────────────────────────────────

@app.route("/api/sync")
def api_sync():
    """Manually trigger a live sync and return fresh POI data."""
    pois = appsheet_sync()
    if pois is None:
        return jsonify({"error": "Sync failed"}), 502

    with cache_lock:
        global poi_cache, poi_cache_ts
        poi_cache    = {p["key"]: p for p in pois}
        poi_cache_ts = datetime.now().strftime("%H:%M:%S")

    with state_lock:
        nk = set(night_keys)
        dk = set(day_keys)

    return jsonify({
        "ts":   poi_cache_ts,
        "pois": [
            {
                "key":      p["key"],
                "name":     p["name"],
                "gemeente": p["gemeente"],
                "owner":    p["owner"],
                "progress": p["progress"],
                "bar":      p["bar"],
                "ours":     p["owner"] == NEW_OWNER,
                "night":    p["key"] in nk,
                "day":      p["key"] in dk,
                "images":   list_images(p["name"]),
            }
            for p in pois
        ],
    })


@app.route("/api/cache")
def api_cache():
    """Return cached POI data (no network call)."""
    with cache_lock:
        pois = list(poi_cache.values())
        ts   = poi_cache_ts
    with state_lock:
        nk = set(night_keys)
        dk = set(day_keys)

    if not pois:
        return jsonify({"ts": None, "pois": []})

    return jsonify({
        "ts":   ts,
        "pois": [
            {
                "key":      p["key"],
                "name":     p["name"],
                "gemeente": p["gemeente"],
                "owner":    p["owner"],
                "progress": p["progress"],
                "bar":      p["bar"],
                "ours":     p["owner"] == NEW_OWNER,
                "night":    p["key"] in nk,
                "day":      p["key"] in dk,
                "images":   list_images(p["name"]),
            }
            for p in pois
        ],
    })


# ── SELECTION ─────────────────────────────────────────────────────────────────

@app.route("/api/select", methods=["POST"])
def api_select():
    data = request.json or {}
    key  = data.get("key", "")
    mode = data.get("mode", "night")
    sel  = bool(data.get("selected", True))

    with cache_lock:
        if key not in poi_cache:
            return jsonify({"error": "unknown key"}), 400

    with state_lock:
        target = night_keys if mode == "night" else day_keys
        if sel:
            target.add(key)
        else:
            target.discard(key)
        counts = {"night": len(night_keys), "day": len(day_keys)}

    save_state()
    return jsonify({"key": key, "mode": mode, "selected": sel, "counts": counts})


@app.route("/api/select_all", methods=["POST"])
def api_select_all():
    data  = request.json or {}
    keys  = data.get("keys", [])
    mode  = data.get("mode", "night")
    sel   = bool(data.get("selected", True))

    with cache_lock:
        valid = [k for k in keys if k in poi_cache]

    with state_lock:
        target = night_keys if mode == "night" else day_keys
        if sel:
            target.update(valid)
        else:
            for k in valid:
                target.discard(k)
        counts = {"night": len(night_keys), "day": len(day_keys)}

    save_state()
    return jsonify({"updated": len(valid), "counts": counts})


# ── LOOP CONTROL ──────────────────────────────────────────────────────────────

@app.route("/api/loop", methods=["GET"])
def api_loop_get():
    with state_lock:
        return jsonify({
            "enabled":        loop_enabled,
            "interval":       loop_interval,
            "use_local_images": use_local_images,
            "night_start":    night_start,
            "night_end":      night_end,
            "mode":           current_mode(),
            "running":        loop_running,
        })


@app.route("/api/loop", methods=["POST"])
def api_loop_set():
    global loop_enabled, loop_interval, use_local_images, night_start, night_end
    data = request.json or {}

    with state_lock:
        if "enabled" in data:
            loop_enabled = bool(data["enabled"])
        if "interval" in data:
            loop_interval = max(1, int(data["interval"]))
            reschedule(loop_interval)
        if "use_local_images" in data:
            use_local_images = bool(data["use_local_images"])
        if "night_start" in data:
            night_start = int(data["night_start"])
        if "night_end" in data:
            night_end = int(data["night_end"])

        result = {
            "enabled":        loop_enabled,
            "interval":       loop_interval,
            "use_local_images": use_local_images,
            "night_start":    night_start,
            "night_end":      night_end,
        }

    save_state()
    return jsonify(result)


@app.route("/api/claim_now", methods=["POST"])
def api_claim_now():
    """Trigger one loop tick immediately."""
    threading.Thread(target=run_loop, kwargs={"force_all": False}, daemon=True).start()
    return jsonify({"started": True})


@app.route("/api/claim_all", methods=["POST"])
def api_claim_all():
    """Claim ALL selected POIs at once with no delay."""
    threading.Thread(target=run_loop, kwargs={"force_all": True}, daemon=True).start()
    return jsonify({"started": True})


# ── ACTIVITY LOG ──────────────────────────────────────────────────────────────

@app.route("/api/log")
def api_log():
    with log_lock:
        return jsonify({"log": list(activity_log)})


# ── IMAGE UPLOAD ──────────────────────────────────────────────────────────────

ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

@app.route("/api/images/<poi_key>/<slot>", methods=["POST"])
def upload_image(poi_key: str, slot: str):
    """Upload an image for a POI. slot = 'day' or 'night'."""
    if slot not in ("day", "night"):
        return jsonify({"error": "slot must be day or night"}), 400

    with cache_lock:
        poi = poi_cache.get(poi_key)
    if not poi:
        return jsonify({"error": "unknown poi key"}), 400

    if "file" not in request.files:
        return jsonify({"error": "no file"}), 400

    f    = request.files["file"]
    ext  = Path(f.filename).suffix.lower() if f.filename else ""
    if ext not in ALLOWED_EXT:
        return jsonify({"error": f"unsupported type {ext}"}), 400

    folder = IMAGES_DIR / poi["name"] / slot
    folder.mkdir(parents=True, exist_ok=True)

    # Unique filename
    fname = f"{int(time.time())}_{random.randint(1000,9999)}{ext}"
    dest  = folder / fname
    f.save(dest)

    log.info(f"Uploaded image for {poi['name']}/{slot}: {fname}")
    return jsonify({"file": fname, "slot": slot, "poi": poi["name"]})


@app.route("/api/images/<poi_key>/<slot>/<filename>", methods=["DELETE"])
def delete_image(poi_key: str, slot: str, filename: str):
    """Delete a specific image."""
    with cache_lock:
        poi = poi_cache.get(poi_key)
    if not poi:
        return jsonify({"error": "unknown poi"}), 400

    target = IMAGES_DIR / poi["name"] / slot / filename
    if target.exists() and target.parent.resolve().is_relative_to(IMAGES_DIR.resolve()):
        target.unlink()
        return jsonify({"deleted": filename})
    return jsonify({"error": "not found"}), 404


@app.route("/api/status")
def api_status():
    with state_lock:
        return jsonify({
            "mode":             current_mode(),
            "night_start":      night_start,
            "night_end":        night_end,
            "server_time":      datetime.now().strftime("%H:%M:%S"),
            "new_owner":        NEW_OWNER,
            "night_selected":   len(night_keys),
            "day_selected":     len(day_keys),
            "loop_enabled":     loop_enabled,
            "loop_interval":    loop_interval,
            "use_local_images": use_local_images,
            "running":          loop_running,
        })


# ── STARTUP ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    with state_lock:
        interval = loop_interval
    reschedule(interval)
    scheduler.start()
    log.info(f"Started. Owner='{NEW_OWNER}' interval={interval}min")
    app.run(host="0.0.0.0", port=5000, debug=False)
