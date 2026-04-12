"""
Car Metrics — SQLite Storage Layer
Crash-safe via WAL mode. All sensor data lands here.
"""

import os
import sqlite3
import logging
import time

import config

logger = logging.getLogger("storage.db")

_conn = None


def _ensure_dirs():
    """Create data directories if they don't exist."""
    os.makedirs(config.DATA_DIR, exist_ok=True)
    os.makedirs(config.IMAGE_DIR, exist_ok=True)


def get_connection() -> sqlite3.Connection:
    """Get or create the singleton DB connection (thread-safe for reads)."""
    global _conn
    if _conn is None:
        _ensure_dirs()
        _conn = sqlite3.connect(config.DB_PATH, check_same_thread=False,
                                  isolation_level=None)  # autocommit for fresh cross-process reads
        # Crash-safe WAL mode
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA synchronous=NORMAL")
        # Smaller cache to save RAM
        _conn.execute("PRAGMA cache_size=-2000")  # 2MB
        _conn.row_factory = sqlite3.Row
        _init_schema(_conn)
        logger.info("Database opened: %s (WAL mode)", config.DB_PATH)
    return _conn


def _init_schema(conn: sqlite3.Connection):
    """Create tables if they don't exist."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS imu_readings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,
            ax REAL, ay REAL, az REAL,
            gx REAL, gy REAL, gz REAL,
            mx REAL, my REAL, mz REAL,
            pressure REAL,
            temp_c REAL,
            altitude REAL,
            synced INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS gps_fixes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,
            lat REAL,
            lon REAL,
            alt REAL,
            speed_knots REAL,
            course REAL,
            satellites INTEGER,
            fix_quality INTEGER,
            synced INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS obd_readings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,
            pid TEXT NOT NULL,
            value REAL,
            unit TEXT,
            synced INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS camera_frames (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,
            filename TEXT NOT NULL,
            event_triggered INTEGER DEFAULT 0,
            synced INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,
            event_type TEXT NOT NULL,
            g_force REAL,
            lat REAL,
            lon REAL,
            details TEXT,
            trip_id INTEGER,
            synced INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS trips (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            start_ts REAL,
            end_ts REAL,
            start_lat REAL,
            start_lon REAL,
            end_lat REAL,
            end_lon REAL,
            distance REAL DEFAULT 0.0,
            score INTEGER DEFAULT 100,
            is_mock INTEGER DEFAULT 0,
            synced INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS trip_routes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trip_id INTEGER NOT NULL,
            ts REAL NOT NULL,
            lat REAL,
            lon REAL,
            speed REAL,
            alt REAL,
            course REAL,
            synced INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS intersections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lat REAL NOT NULL,
            lon REAL NOT NULL,
            detection_type TEXT,
            first_seen_ts REAL,
            trip_count INTEGER DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS sync_cursor (
            table_name TEXT PRIMARY KEY,
            last_synced_id INTEGER DEFAULT 0
        );

        -- Indexes for sync queries
        CREATE INDEX IF NOT EXISTS idx_imu_synced ON imu_readings(synced);
        CREATE INDEX IF NOT EXISTS idx_gps_synced ON gps_fixes(synced);
        CREATE INDEX IF NOT EXISTS idx_obd_synced ON obd_readings(synced);
        CREATE INDEX IF NOT EXISTS idx_events_synced ON events(synced);

        CREATE TABLE IF NOT EXISTS locations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            lat REAL NOT NULL,
            lon REAL NOT NULL,
            visit_count INTEGER DEFAULT 1,
            last_visit_ts REAL
        );

        CREATE TABLE IF NOT EXISTS routines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            start_location_id INTEGER,
            end_location_id INTEGER,
            pb_duration REAL,
            avg_duration REAL,
            trip_count INTEGER DEFAULT 0,
            FOREIGN KEY(start_location_id) REFERENCES locations(id),
            FOREIGN KEY(end_location_id) REFERENCES locations(id)
        );
    """)

    # Safe schema migrations for existing local databases
    try:
        conn.execute("ALTER TABLE trips ADD COLUMN distance REAL DEFAULT 0.0")
    except sqlite3.OperationalError:
        pass

    try:
        conn.execute("ALTER TABLE trips ADD COLUMN score INTEGER DEFAULT 100")
    except sqlite3.OperationalError:
        pass

    # Phase 5 migrations: enrich trip_routes
    for col, default in [("speed", "NULL"), ("alt", "NULL"), ("course", "NULL")]:
        try:
            conn.execute(f"ALTER TABLE trip_routes ADD COLUMN {col} REAL DEFAULT {default}")
        except sqlite3.OperationalError:
            pass

    # Phase 6 migrations
    for tbl, col, default in [("trips", "is_mock", "0"), ("events", "trip_id", "NULL")]:
        try:
            conn.execute(f"ALTER TABLE {tbl} ADD COLUMN {col} INTEGER DEFAULT {default}")
        except sqlite3.OperationalError:
            pass

    # Phase 8 migrations: Ghost Ride
    for col in ["start_location_id", "end_location_id"]:
        try:
            conn.execute(f"ALTER TABLE trips ADD COLUMN {col} INTEGER")
        except sqlite3.OperationalError:
            pass

    try:
        conn.execute("ALTER TABLE routines ADD COLUMN pb_trip_id INTEGER")
    except sqlite3.OperationalError:
        pass

    conn.commit()


# ─── Insert helpers ───────────────────────────────

def insert_imu_batch(rows: list[dict]):
    """Insert a batch of IMU readings. Each dict has keys matching columns."""
    conn = get_connection()
    conn.executemany(
        """INSERT INTO imu_readings
           (ts, ax, ay, az, gx, gy, gz, mx, my, mz, pressure, temp_c, altitude)
           VALUES (:ts, :ax, :ay, :az, :gx, :gy, :gz, :mx, :my, :mz,
                   :pressure, :temp_c, :altitude)""",
        rows,
    )
    conn.commit()


def insert_gps_fix(fix: dict):
    """Insert a single GPS fix."""
    conn = get_connection()
    conn.execute(
        """INSERT INTO gps_fixes
           (ts, lat, lon, alt, speed_knots, course, satellites, fix_quality)
           VALUES (:ts, :lat, :lon, :alt, :speed_knots, :course,
                   :satellites, :fix_quality)""",
        fix,
    )
    conn.commit()


def insert_obd_reading(reading: dict):
    """Insert a single OBD reading."""
    conn = get_connection()
    conn.execute(
        """INSERT INTO obd_readings (ts, pid, value, unit)
           VALUES (:ts, :pid, :value, :unit)""",
        reading,
    )
    conn.commit()


def insert_camera_frame(ts: float, filename: str, event_triggered: bool = False):
    """Insert a camera frame record."""
    conn = get_connection()
    conn.execute(
        """INSERT INTO camera_frames (ts, filename, event_triggered)
           VALUES (?, ?, ?)""",
        (ts, filename, int(event_triggered)),
    )
    conn.commit()


def insert_event(event: dict):
    """Insert a crash/incident event."""
    conn = get_connection()
    conn.execute(
        """INSERT INTO events (ts, event_type, g_force, lat, lon, details, trip_id)
           VALUES (:ts, :event_type, :g_force, :lat, :lon, :details, :trip_id)""",
        event,
    )
    conn.commit()


# ─── Query helpers (for sync) ─────────────────────

def get_unsynced_rows(table: str, limit: int = 100) -> list[sqlite3.Row]:
    """Get rows not yet synced to remote."""
    conn = get_connection()
    cur = conn.execute(
        f"SELECT * FROM {table} WHERE synced = 0 ORDER BY id LIMIT ?",
        (limit,),
    )
    return cur.fetchall()


def mark_synced(table: str, row_ids: list[int]):
    """Mark rows as synced."""
    if not row_ids:
        return
    conn = get_connection()
    placeholders = ",".join("?" * len(row_ids))
    conn.execute(
        f"UPDATE {table} SET synced = 1 WHERE id IN ({placeholders})",
        row_ids,
    )
    conn.commit()


def close():
    """Clean shutdown."""
    global _conn
    if _conn:
        _conn.close()
        _conn = None
        logger.info("Database closed")


# ─── Spatial Helpers ──────────────────────────────

def get_nearby_location(lat: float, lon: float, radius_m: float = 100):
    """Find a location record within radius_m of a point (bounding box filter)."""
    conn = get_connection()
    deg_per_m = 1.0 / 111000.0
    margin = radius_m * deg_per_m
    cur = conn.execute("""
        SELECT * FROM locations 
        WHERE lat BETWEEN ? AND ? 
          AND lon BETWEEN ? AND ?
        LIMIT 1
    """, (lat - margin, lat + margin, lon - margin, lon + margin))
    return cur.fetchone()

def upsert_location(lat: float, lon: float, name: str = None) -> int:
    """Insert or increment visit count for a location."""
    conn = get_connection()
    loc = get_nearby_location(lat, lon)
    if loc:
        conn.execute("UPDATE locations SET visit_count = visit_count + 1, last_visit_ts = ? WHERE id = ?", (time.time(), loc['id']))
        return loc['id']
    else:
        cur = conn.execute("INSERT INTO locations (lat, lon, visit_count, last_visit_ts, name) VALUES (?, ?, 1, ?, ?)", (lat, lon, time.time(), name))
        return cur.lastrowid

def get_routine(start_loc_id: int, end_loc_id: int):
    conn = get_connection()
    cur = conn.execute("SELECT * FROM routines WHERE start_location_id = ? AND end_location_id = ?", (start_loc_id, end_loc_id))
    return cur.fetchone()

def upsert_routine(start_loc_id: int, end_loc_id: int, duration: float, trip_id: int):
    conn = get_connection()
    r = get_routine(start_loc_id, end_loc_id)
    if r:
        is_new_pb = r['pb_duration'] is None or duration < r['pb_duration']
        new_pb = min(r['pb_duration'] if r['pb_duration'] else duration, duration)
        new_pb_trip = trip_id if is_new_pb else r['pb_trip_id']
        new_avg = (r['avg_duration'] * r['trip_count'] + duration) / (r['trip_count'] + 1)
        conn.execute("UPDATE routines SET pb_duration = ?, avg_duration = ?, trip_count = trip_count + 1, pb_trip_id = ? WHERE id = ?", (new_pb, new_avg, new_pb_trip, r['id']))
    else:
        conn.execute("INSERT INTO routines (start_location_id, end_location_id, pb_duration, avg_duration, trip_count, pb_trip_id) VALUES (?, ?, ?, ?, 1, ?)", (start_loc_id, end_loc_id, duration, duration, trip_id))
    conn.commit()
