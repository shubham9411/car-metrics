"""
Car Metrics — Web Dashboard Server
Lightweight Bottle-based dashboard for viewing sensor data and images.
Designed to run on Pi Zero with minimal resource usage.

Start manually:  python3 web/server.py
Or via systemd:  sudo systemctl start car-metrics-web
"""

import os
import sys

# Add parent dir to path so we can import config/storage
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import math
import threading
import time
import logging
from datetime import datetime, timedelta

from scripts import aggregate_env

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

@app.route("/live")
def live_hud():
    """Standalone Live HUD page (own file, own JS, no SPA trapping)."""
    return static_file("live.html", root=os.path.join(WEB_DIR, "templates"))


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
    env_count = conn.execute("SELECT COUNT(*) FROM env_readings").fetchone()[0]

    # Latest BME680 environmental reading
    env = conn.execute(
        "SELECT * FROM env_readings ORDER BY id DESC LIMIT 1"
    ).fetchone()

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
        "env": _row_to_dict(env) if env else None,
        "counts": {
            "imu_readings": imu_count,
            "gps_fixes": gps_count,
            "obd_readings": obd_count,
            "camera_frames": img_count,
            "events": event_count,
            "env_readings": env_count,
        },
        "uptime_sec": uptime_sec,
        "server_ts": time.time(),
        "ghost": None,
    }

    # Ghost Ride: Smart Prediction & PB Path Retrieval
    active_trip = conn.execute("SELECT * FROM trips WHERE end_ts IS NULL ORDER BY id DESC LIMIT 1").fetchone()
    if active_trip and active_trip['start_location_id']:
        duration = time.time() - active_trip['start_ts']
        now = datetime.fromtimestamp(time.time())
        is_weekday = now.weekday() < 5
        hour = now.hour

        routines = conn.execute("""
            SELECT r.*, l.lat as end_lat, l.lon as end_lon, l.name as end_name 
            FROM routines r 
            JOIN locations l ON r.end_location_id = l.id 
            WHERE r.start_location_id = ?
        """, (active_trip['start_location_id'],)).fetchall()
        
        if routines:
            scored_routines = []
            current_heading = result.get("gps", {}).get("course")
            start_loc = conn.execute("SELECT lat, lon FROM locations WHERE id=?", (active_trip['start_location_id'],)).fetchone()

            for r in routines:
                # Base score from trip count (frequency)
                score = r['trip_count'] * 1.0
                
                # Directional bias: If current heading matches bearing to destination
                if current_heading is not None and start_loc:
                    bearing = _calculate_bearing(start_loc['lat'], start_loc['lon'], r['end_lat'], r['end_lon'])
                    diff = abs(current_heading - bearing) % 360
                    if diff > 180: diff = 360 - diff
                    if diff < 45: # Broad direction check
                        score *= 2.0

                # Recency bias: Check when the last trip for this routine happened
                last_trip = conn.execute("SELECT start_ts FROM trips WHERE id=?", (r['pb_trip_id'],)).fetchone()
                if last_trip:
                    # Score multiplier based on recency (bonus for trips in last 7 days)
                    days_ago = (time.time() - last_trip['start_ts']) / 86400
                    if days_ago < 7:
                        score *= (2.0 - (days_ago / 7.0))
                
                scored_routines.append({"routine": r, "score": score})

            scored_routines.sort(key=lambda x: x['score'], reverse=True)
            top = scored_routines[0]

            # Fetch Ghost Path for the TOP routine
            ghost_path = []
            if top['routine']['pb_trip_id']:
                pb_tid = top['routine']['pb_trip_id']
                pb_trip = conn.execute("SELECT start_ts FROM trips WHERE id=?", (pb_tid,)).fetchone()
                if pb_trip:
                    pb_start = pb_trip['start_ts']
                    path_rows = conn.execute(
                        "SELECT lat, lon, ts FROM trip_routes WHERE trip_id = ? ORDER BY ts ASC",
                        (pb_tid,)
                    ).fetchall()
                    ghost_path = [[p['lat'], p['lon'], int(p['ts'] - pb_start)] for p in path_rows]

            # Also fetch MY current path to support mid-trip catch-up
            my_path = []
            if active_trip:
                my_rows = conn.execute(
                    "SELECT lat, lon FROM trip_routes WHERE trip_id = ? ORDER BY ts ASC",
                    (active_trip['id'],)
                ).fetchall()
                my_path = [[r['lat'], r['lon']] for r in my_rows]

            result["ghost"] = {
                "routines": [dict(r) for r in routines],
                "predicted_end_name": top['routine']['end_name'] if routines else None,
                "current_duration": duration,
                "ghost_path": ghost_path,
                "current_path": my_path,
            }

    # NOTE: No GPS/OBD spoofing here — gps.py and obd.py already generate
    # proper mock data into the DB when .simulate_data exists.
    # A server-side spoof would overwrite the DB values with incompatible
    # circular coordinates, breaking both ghost path and car trail rendering.

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
    """Get full drilldown data for a single trip: summary, enriched route, events, analytics."""
    conn = db.get_connection()
    response.content_type = "application/json"

    try:
        trip = conn.execute("SELECT * FROM trips WHERE id=?", (trip_id,)).fetchone()
        if not trip:
            response.status = 404
            return json.dumps({"error": "Trip not found"})

        trip_data = _row_to_dict(trip)

        # Route with speed/alt/course
        route_rows = conn.execute(
            "SELECT ts, lat, lon, speed, alt, course FROM trip_routes WHERE trip_id=? ORDER BY ts ASC",
            (trip_id,)
        ).fetchall()
        route = [_row_to_dict(r) for r in route_rows]

        # Real-time distance calculation (on-the-fly summation)
        distance_m = _calculate_route_distance(route)

        # Computed analytics from route
        speeds = [p["speed"] for p in route if p.get("speed") is not None]
        avg_speed = sum(speeds) / len(speeds) if speeds else 0
        max_speed = max(speeds) if speeds else 0

        # Events for this trip
        ev_rows = conn.execute(
            "SELECT * FROM events WHERE trip_id=? ORDER BY ts ASC",
            (trip_id,)
        ).fetchall()

        # Fallback: if no trip_id-linked events, try timestamp range (legacy data)
        if not ev_rows and trip_data.get("start_ts"):
            end_ts = trip_data.get("end_ts") or time.time()
            ev_rows = conn.execute(
                "SELECT * FROM events WHERE ts >= ? AND ts <= ? ORDER BY ts ASC",
                (trip_data["start_ts"], end_ts)
            ).fetchall()

        events = [_row_to_dict(r) for r in ev_rows]

        # Event breakdown and total penalty calculation
        # Penalties: brake:5, accel:3, turn:4, pothole:2, speeding:3, impact:8
        penalties = {"sudden_brake": 5, "sudden_accel": 3, "sharp_turn": 4, "pothole": 2, "high_impact": 8, "speeding": 3}
        total_penalty_points = 0
        event_breakdown = {}
        for ev in events:
            t = ev.get("event_type", "unknown")
            event_breakdown[t] = event_breakdown.get(t, 0) + 1
            total_penalty_points += penalties.get(t, 5)

        # Dynamic Weighted Score: 100 - (Penalties / (1 + Dist_KM / 5))
        dist_km = distance_m / 1000.0
        weighted_score = 100 - (total_penalty_points / (1 + dist_km / 5.0))
        weighted_score = max(0, min(100, round(weighted_score, 1)))

        # Update trip_data score for the response (doesn't mutate DB here)
        trip_data["score"] = weighted_score
        trip_data["distance"] = distance_m

        # Reverse geocode start/end
        start_addr = _reverse_geocode(trip_data.get("start_lat"), trip_data.get("start_lon"))
        end_addr = _reverse_geocode(trip_data.get("end_lat"), trip_data.get("end_lon"))

        return json.dumps({
            "trip": trip_data,
            "route": route,
            "events": events,
            "analytics": {
                "avg_speed": round(avg_speed, 1),
                "max_speed": round(max_speed, 1),
                "distance": round(distance_m),
                "event_breakdown": event_breakdown,
                "start_address": start_addr,
                "end_address": end_addr,
                "total_penalty": total_penalty_points,
            }
        })
    except Exception as e:
        logger.error(f"Error fetching trip {trip_id}: {e}")
        return json.dumps({"error": str(e)})


def _calculate_route_distance(route):
    """Sum haversine distance between all points in a route."""
    if len(route) < 2:
        return 0.0
    total = 0.0
    for i in range(len(route) - 1):
        p1 = route[i]
        p2 = route[i+1]
        # Skip if coordinates missing
        if p1["lat"] is None or p2["lat"] is None: continue
        
        # Simple Haversine approximation
        d_lat = abs(p1["lat"] - p2["lat"]) * 111139
        d_lon = abs(p1["lon"] - p2["lon"]) * 111139 * math.cos(math.radians(p1["lat"]))
        total += math.sqrt(d_lat**2 + d_lon**2)
    return total


# In-memory geocode cache (cleared on restart)
_geocode_cache = {}

def _reverse_geocode(lat, lon):
    """Best-effort reverse geocode via Nominatim. Returns address string or None."""
    if lat is None or lon is None:
        return None
    key = f"{lat:.4f},{lon:.4f}"
    if key in _geocode_cache:
        return _geocode_cache[key]
    try:
        import urllib.request
        url = f"https://nominatim.openstreetmap.org/reverse?lat={lat}&lon={lon}&format=json&zoom=16"
        req = urllib.request.Request(url, headers={"User-Agent": "car-metrics/1.0"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode())
            addr = data.get("display_name", "")
            # Shorten to locality level
            parts = addr.split(", ")
            short = ", ".join(parts[:3]) if len(parts) > 3 else addr
            _geocode_cache[key] = short
            return short
    except Exception:
        _geocode_cache[key] = None
        return None


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

@app.route("/api/env/history")
def api_env_history():
    """Get historical environmental readings with optional downsampling."""
    start = request.query.get("start")
    end = request.query.get("end")
    bucket = request.query.get("bucket")

    conn = db.get_connection()
    
    if start and end and bucket:
        # Aggregated query for historical ranges
        try:
            b_size = int(bucket)
            s_ts = float(start)
            e_ts = float(end)
            
            # Smart Switching: Use summary table for long ranges (bucket >= 1 hour)
            if b_size >= 3600:
                query = """
                    SELECT ts as bucket_ts, avg_temp, avg_hum, avg_iaq
                    FROM env_hourly_summary
                    WHERE ts >= ? AND ts <= ? AND is_mock = 0
                    ORDER BY ts ASC
                """
                rows = conn.execute(query, (s_ts, e_ts)).fetchall()
                points = []
                for r in rows:
                    points.append({
                        "ts": r["bucket_ts"],
                        "temp": round(r["avg_temp"], 2) if r["avg_temp"] else None,
                        "hum": round(r["avg_hum"], 1) if r["avg_hum"] else None,
                        "iaq": int(r["avg_iaq"]) if r["avg_iaq"] else None
                    })
                response.content_type = "application/json"
                return json.dumps(points)

            # High-resolution Bucket aggregation from raw data
            query = """
                SELECT 
                    (CAST(ts / ? AS INT) * ?) as bucket_ts,
                    AVG(temperature) as avg_temp,
                    AVG(humidity) as avg_hum,
                    AVG(iaq_score) as avg_iaq
                FROM env_readings 
                WHERE ts >= ? AND ts <= ? AND is_mock = 0
                GROUP BY bucket_ts
                ORDER BY bucket_ts ASC
            """
            rows = conn.execute(query, (b_size, b_size, s_ts, e_ts)).fetchall()
            
            points = []
            for r in rows:
                points.append({
                    "ts": r["bucket_ts"],
                    "temp": round(r["avg_temp"], 2) if r["avg_temp"] else None,
                    "hum": round(r["avg_hum"], 1) if r["avg_hum"] else None,
                    "iaq": int(r["avg_iaq"]) if r["avg_iaq"] else None
                })
            
            response.content_type = "application/json"
            return json.dumps(points)
        except Exception as e:
            logger.error("Failed aggregated env query: %s", e)
            # fall through to default

    # Default: last N points
    limit = min(int(request.query.get("limit", 120)), 1000)
    rows = conn.execute(
        "SELECT ts, temperature, humidity, iaq_score FROM env_readings WHERE is_mock = 0 ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()

    points = []
    for r in rows:
        points.append({
            "ts": r["ts"], 
            "temp": r["temperature"], 
            "hum": r["humidity"], 
            "iaq": r["iaq_score"]
        })

    response.content_type = "application/json"
    return json.dumps(points[::-1])  # oldest first for chart


@app.route("/api/env/stats")
def api_env_stats():
    """Get aggregated min/max/avg environmental stats for the last 24h."""
    now = time.time()
    day_ago = now - (24 * 3600)
    
    conn = db.get_connection()
    query = """
        SELECT 
            AVG(temperature) as avg_temp, MIN(temperature) as min_temp, MAX(temperature) as max_temp,
            AVG(humidity) as avg_hum, MIN(humidity) as min_hum, MAX(humidity) as max_hum,
            AVG(CASE WHEN iaq_score >= 1 THEN iaq_score END) as avg_iaq, 
            MIN(CASE WHEN iaq_score >= 1 THEN iaq_score END) as min_iaq, 
            MAX(iaq_score) as max_iaq
        FROM env_readings 
        WHERE ts >= ? AND is_mock = 0
    """
    res = conn.execute(query, (day_ago,)).fetchone()
    
    response.content_type = "application/json"
    if not res or res["avg_temp"] is None:
        return json.dumps(None)
        
    return json.dumps({
        "temp": {"avg": round(res["avg_temp"], 1), "min": round(res["min_temp"], 1), "max": round(res["max_temp"], 1)},
        "hum":  {"avg": round(res["avg_hum"], 1),  "min": round(res["min_hum"], 1),  "max": round(res["max_hum"], 1)},
        "iaq":  {"avg": int(res["avg_iaq"]),      "min": int(res["min_iaq"]),      "max": int(res["max_iaq"])}
    })
def api_locations():
    """Get or update discovered anchor point locations."""
    conn = db.get_connection()
    response.content_type = "application/json"
    
    if request.method == "POST":
        try:
            data = request.json or {}
            loc_id = data.get("id")
            name = data.get("name", "").strip()
            if not loc_id: return json.dumps({"status": "error", "message": "Missing ID"})
            
            conn.execute("UPDATE locations SET name = ? WHERE id = ?", (name, loc_id))
            conn.commit()
            return json.dumps({"status": "ok"})
        except Exception as e:
            return json.dumps({"status": "error", "message": str(e)})

    # GET
    rows = conn.execute("SELECT * FROM locations ORDER BY visit_count DESC").fetchall()
    return json.dumps([_row_to_dict(r) for r in rows])


def _calculate_bearing(lat1, lon1, lat2, lon2):
    """Calculate the compass bearing between two points."""
    y = math.sin(math.radians(lon2 - lon1)) * math.cos(math.radians(lat2))
    x = math.cos(math.radians(lat1)) * math.sin(math.radians(lat2)) - \
        math.sin(math.radians(lat1)) * math.cos(math.radians(lat2)) * \
        math.cos(math.radians(lon2 - lon1))
    bearing = math.degrees(math.atan2(y, x))
    return (bearing + 360) % 360


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


@app.route("/api/imu/calibrate", method="POST")
def api_imu_calibrate():
    """Signaling endpoint to trigger IMU auto-tare."""
    try:
        open(os.path.join(config.DATA_DIR, ".trigger_imu_calibrate"), "w").close()
        return json.dumps({"status": "ok", "message": "Calibration triggered. Stationary for 2s..."})
    except Exception as e:
        response.status = 500
        return json.dumps({"status": "error", "message": str(e)})


@app.route("/api/imu/reset", method="POST")
def api_imu_reset():
    """Signaling endpoint to reset IMU offsets."""
    try:
        open(os.path.join(config.DATA_DIR, ".trigger_imu_reset"), "w").close()
        return json.dumps({"status": "ok", "message": "Calibration reset triggered."})
    except Exception as e:
        response.status = 500
        return json.dumps({"status": "error", "message": str(e)})


SIM_DATA_FILE = os.path.join(config.DATA_DIR, ".simulate_data")

@app.route("/api/settings/simulate_data", method=["GET", "POST"])
def api_simulate_data():
    """Get or set the data simulation override toggle.
    Safety: blocks enable if OBD shows car is actually running."""
    if request.method == "POST":
        try:
            data = request.json or {}
            enabled = bool(data.get("enabled", False))

            if enabled:
                # Safety guard: check if the car is actually running via real OBD speed
                conn = db.get_connection()
                cutoff = time.time() - 30  # last 30 seconds
                row = conn.execute(
                    "SELECT value FROM obd_readings WHERE pid='SPEED' AND ts > ? ORDER BY ts DESC LIMIT 1",
                    (cutoff,)
                ).fetchone()
                if row and row["value"] and float(row["value"]) > 5.0:
                    response.status = 409
                    return json.dumps({
                        "status": "blocked",
                        "message": "Cannot enable simulation while car is running (OBD speed detected)."
                    })

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

def _agg_worker():
    """Background worker to run aggregation periodically."""
    while True:
        try:
            logger.info("Background Aggregator: Running...")
            aggregate_env.run_aggregation()
        except Exception as e:
            logger.error("Background Aggregator failed: %s", e)
        time.sleep(1800) # Run every 30 minutes

if __name__ == "__main__":
    # Start background aggregator
    threading.Thread(target=_agg_worker, daemon=True).start()
    
    print(f"Car Metrics Dashboard: http://{WEB_HOST}:{WEB_PORT}")
    app.run(host=WEB_HOST, port=WEB_PORT, debug=False, quiet=True)
