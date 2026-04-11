"""
Car Metrics — Web Dashboard Server
Lightweight Bottle-based dashboard for viewing sensor data and images.
Designed to run on Pi Zero with minimal resource usage.

Start manually:  python3 web/server.py
Or via systemd:  sudo systemctl start car-metrics-web
"""

import json
import math
import os
import sys
import time
from datetime import datetime

# Add parent dir to path so we can import config/storage
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from storage import db

try:
    from bottle import Bottle, static_file, request, response, template, TEMPLATE_PATH
except ImportError:
    print("Bottle not installed. Run: pip3 install bottle")
    sys.exit(1)

app = Bottle()

WEB_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(WEB_DIR, "static")
TEMPLATE_PATH.insert(0, os.path.join(WEB_DIR, "templates"))


# ─── Pages ────────────────────────────────────────

@app.route("/")
def index():
    """Dashboard home page."""
    return static_file("index.html", root=os.path.join(WEB_DIR, "templates"))

@app.route("/old-dashboard.html")
def old_dashboard():
    """Dashboard old page."""
    return static_file("old-dashboard.html", root=os.path.join(WEB_DIR, "templates"))


# ─── API: Latest Status ──────────────────────────

@app.route("/api/status")
def api_status():
    """Get latest readings from all sensors."""
    conn = db.get_connection()
    response.content_type = "application/json"

    # Latest IMU reading
    imu = conn.execute(
        "SELECT * FROM imu_readings ORDER BY id DESC LIMIT 1"
    ).fetchone()

    # Latest GPS fix
    gps = conn.execute(
        "SELECT * FROM gps_fixes ORDER BY id DESC LIMIT 1"
    ).fetchone()

    # Latest OBD readings (one per PID)
    obd_rows = conn.execute("""
        SELECT pid, value, unit, ts FROM obd_readings
        WHERE id IN (SELECT MAX(id) FROM obd_readings GROUP BY pid)
    """).fetchall()

    # Counts
    imu_count = conn.execute("SELECT COUNT(*) FROM imu_readings").fetchone()[0]
    gps_count = conn.execute("SELECT COUNT(*) FROM gps_fixes").fetchone()[0]
    obd_count = conn.execute("SELECT COUNT(*) FROM obd_readings").fetchone()[0]
    img_count = conn.execute("SELECT COUNT(*) FROM camera_frames").fetchone()[0]
    event_count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]

    # System uptime
    try:
        with open("/proc/uptime", "r") as f:
            uptime_sec = float(f.read().split()[0])
    except Exception:
        uptime_sec = None

    result = {
        "imu": _row_to_dict(imu) if imu else None,
        "gps": _row_to_dict(gps) if gps else None,
        "obd": {r["pid"]: {"value": r["value"], "unit": r["unit"], "ts": r["ts"]} for r in obd_rows},
        "counts": {
            "imu_readings": imu_count,
            "gps_fixes": gps_count,
            "obd_readings": obd_count,
            "camera_frames": img_count,
            "events": event_count,
        },
        "uptime_sec": uptime_sec,
        "server_ts": time.time(),
    }

    # Spoof data if simulation mode is on
    if os.path.exists(os.path.join(config.DATA_DIR, ".simulate_data")):
        s_base = time.time() / 10
        result["gps"] = {
            "lat": 37.7749 + math.sin(s_base) * 0.01,
            "lon": -122.4194 + math.cos(s_base) * 0.01,
            "alt": 15 + math.sin(s_base * 2) * 5,
            "speed_knots": 30 + math.sin(s_base * 5) * 10,
            "course": (s_base * 50) % 360,
            "satellites": 8
        }
        result["obd"] = {
            "RPM": {"value": 2500 + int(math.sin(s_base * 4) * 1500), "unit": "rev/min"},
            "SPEED": {"value": 55 + int(math.sin(s_base * 5) * 20), "unit": "kph"},
            "ENGINE_LOAD": {"value": 45.0 + math.sin(s_base * 2) * 30, "unit": "%"},
            "COOLANT_TEMP": {"value": 85 + int(math.sin(s_base / 2) * 10), "unit": "degC"}
        }
        if result["imu"]:
            result["imu"]["pressure"] = 101325 + math.sin(s_base) * 500
            result["imu"]["ax"] = math.sin(s_base * 8) * 0.1
            result["imu"]["ay"] = math.cos(s_base * 8) * 0.1

    return json.dumps(result)


# ─── API: Sensor Data ────────────────────────────

@app.route("/api/imu")
def api_imu():
    """Get recent IMU readings."""
    limit = min(int(request.query.get("limit", 100)), 500)
    conn = db.get_connection()
    rows = conn.execute(
        "SELECT * FROM imu_readings ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    response.content_type = "application/json"
    return json.dumps([_row_to_dict(r) for r in rows])


@app.route("/api/gps")
def api_gps():
    """Get recent GPS fixes."""
    limit = min(int(request.query.get("limit", 100)), 500)
    conn = db.get_connection()
    rows = conn.execute(
        "SELECT * FROM gps_fixes ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    response.content_type = "application/json"
    return json.dumps([_row_to_dict(r) for r in rows])


@app.route("/api/obd")
def api_obd():
    """Get recent OBD readings."""
    limit = min(int(request.query.get("limit", 200)), 500)
    conn = db.get_connection()
    rows = conn.execute(
        "SELECT * FROM obd_readings ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    response.content_type = "application/json"
    return json.dumps([_row_to_dict(r) for r in rows])


@app.route("/api/events")
def api_events():
    """Get recent events (crashes, high-G)."""
    limit = min(int(request.query.get("limit", 50)), 200)
    conn = db.get_connection()
    rows = conn.execute(
        "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    response.content_type = "application/json"
    return json.dumps([_row_to_dict(r) for r in rows])


@app.route("/api/trips")
def api_trips():
    """Get summarized trip sessions."""
    limit = min(int(request.query.get("limit", 20)), 100)
    conn = db.get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM trips ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        response.content_type = "application/json"
        return json.dumps([_row_to_dict(r) for r in rows])
    except Exception as e:
        logger.error(f"Error fetching trips: {e}")
        response.content_type = "application/json"
        return "[]"


@app.route("/api/routes")
def api_routes():
    """Get downsampled route tracks for Fog of War map mapping."""
    conn = db.get_connection()
    try:
        rows = conn.execute(
            "SELECT trip_id, lat, lon FROM trip_routes ORDER BY ts ASC"
        ).fetchall()

        trips = {}
        for r in rows:
            tid = r["trip_id"]
            if tid not in trips:
                trips[tid] = []
            trips[tid].append([r["lat"], r["lon"]])

        response.content_type = "application/json"
        return json.dumps(list(trips.values()))
    except Exception as e:
        logger.error(f"Error fetching routes: {e}")
        response.content_type = "application/json"
        return "[]"


@app.route("/api/trips/<trip_id:int>")
def api_trip_detail(trip_id):
    """Get full drilldown data for a single trip: summary, enriched route, events."""
    conn = db.get_connection()
    response.content_type = "application/json"

    try:
        # Trip summary
        trip = conn.execute("SELECT * FROM trips WHERE id=?", (trip_id,)).fetchone()
        if not trip:
            response.status = 404
            return json.dumps({"error": "Trip not found"})

        trip_data = _row_to_dict(trip)

        # Route with speed/alt/course for gradient maps
        route_rows = conn.execute(
            "SELECT ts, lat, lon, speed, alt, course FROM trip_routes WHERE trip_id=? ORDER BY ts ASC",
            (trip_id,)
        ).fetchall()
        route = [_row_to_dict(r) for r in route_rows]

        # Events that happened during this trip's timeframe
        events = []
        if trip_data.get("start_ts"):
            end_ts = trip_data.get("end_ts") or time.time()
            ev_rows = conn.execute(
                "SELECT * FROM events WHERE ts >= ? AND ts <= ? ORDER BY ts ASC",
                (trip_data["start_ts"], end_ts)
            ).fetchall()
            events = [_row_to_dict(r) for r in ev_rows]

        return json.dumps({
            "trip": trip_data,
            "route": route,
            "events": events,
        })
    except Exception as e:
        logger.error(f"Error fetching trip {trip_id}: {e}")
        return json.dumps({"error": str(e)})


@app.route("/api/intersections")
def api_intersections():
    """Get all detected intersections (for Global Map overlay)."""
    conn = db.get_connection()
    response.content_type = "application/json"
    try:
        rows = conn.execute(
            "SELECT * FROM intersections ORDER BY trip_count DESC"
        ).fetchall()
        return json.dumps([_row_to_dict(r) for r in rows])
    except Exception:
        return "[]"


# ─── API: Images ──────────────────────────────────

@app.route("/api/images")
def api_images():
    """List available images with metadata."""
    limit = min(int(request.query.get("limit", 50)), 200)
    page = max(int(request.query.get("page", 0)), 0)
    offset = page * limit

    conn = db.get_connection()
    rows = conn.execute(
        "SELECT * FROM camera_frames ORDER BY id DESC LIMIT ? OFFSET ?",
        (limit, offset),
    ).fetchall()

    total = conn.execute("SELECT COUNT(*) FROM camera_frames").fetchone()[0]

    response.content_type = "application/json"
    return json.dumps({
        "images": [_row_to_dict(r) for r in rows],
        "total": total,
        "page": page,
        "limit": limit,
    })


@app.route("/images/<filename>")
def serve_image(filename):
    """Serve an image file from the image directory."""
    return static_file(filename, root=config.IMAGE_DIR, mimetype="image/jpeg")


# ─── API: G-force history (for chart) ────────────

@app.route("/api/gforce")
def api_gforce():
    """Get recent G-force magnitudes for charting."""
    limit = min(int(request.query.get("limit", 300)), 1000)
    conn = db.get_connection()
    rows = conn.execute(
        "SELECT ts, ax, ay, az FROM imu_readings ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()

    points = []
    for r in rows:
        g = math.sqrt(r["ax"] ** 2 + r["ay"] ** 2 + r["az"] ** 2)
        points.append({"ts": r["ts"], "g": round(g, 3)})

    response.content_type = "application/json"
    return json.dumps(points[::-1])  # oldest first for chart


# ─── API: Settings (Camera override) ──────────────

FORCE_CAM_FILE = os.path.join(config.DATA_DIR, ".force_camera")

@app.route("/api/settings/force_camera", method=["GET", "POST"])
def api_force_camera():
    """Get or set the manual camera override toggle."""
    if request.method == "POST":
        try:
            data = request.json or {}
            enabled = bool(data.get("enabled", False))
            if enabled:
                open(FORCE_CAM_FILE, "w").close()
            else:
                if os.path.exists(FORCE_CAM_FILE):
                    os.remove(FORCE_CAM_FILE)
            return json.dumps({"status": "ok", "enabled": enabled})
        except Exception as e:
            response.status = 500
            return json.dumps({"status": "error", "message": str(e)})

    # GET
    response.content_type = "application/json"
    enabled = os.path.exists(FORCE_CAM_FILE)
    return json.dumps({"enabled": enabled})


SIM_DATA_FILE = os.path.join(config.DATA_DIR, ".simulate_data")

@app.route("/api/settings/simulate_data", method=["GET", "POST"])
def api_simulate_data():
    """Get or set the data simulation override toggle."""
    if request.method == "POST":
        try:
            data = request.json or {}
            enabled = bool(data.get("enabled", False))
            if enabled:
                open(SIM_DATA_FILE, "w").close()
            else:
                if os.path.exists(SIM_DATA_FILE):
                    os.remove(SIM_DATA_FILE)
            return json.dumps({"status": "ok", "enabled": enabled})
        except Exception as e:
            response.status = 500
            return json.dumps({"status": "error", "message": str(e)})

    # GET
    response.content_type = "application/json"
    enabled = os.path.exists(SIM_DATA_FILE)
    return json.dumps({"enabled": enabled})


# ─── Static files ─────────────────────────────────

@app.route("/static/<filepath:path>")
def serve_static(filepath):
    return static_file(filepath, root=STATIC_DIR)


# ─── Helpers ──────────────────────────────────────

def _row_to_dict(row) -> dict:
    if row is None:
        return {}
    return {k: row[k] for k in row.keys()}


# ─── Entrypoint ───────────────────────────────────

WEB_HOST = os.environ.get("CM_WEB_HOST", "0.0.0.0")
WEB_PORT = int(os.environ.get("CM_WEB_PORT", "8080"))

if __name__ == "__main__":
    print(f"Car Metrics Dashboard: http://{WEB_HOST}:{WEB_PORT}")
    app.run(host=WEB_HOST, port=WEB_PORT, debug=False, quiet=True)
