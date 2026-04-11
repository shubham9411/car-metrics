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
