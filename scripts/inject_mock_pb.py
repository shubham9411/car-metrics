#!/usr/bin/env python3
import os
import sys
import time
import math
import sqlite3

# Add parent dir to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

def inject():
    db_path = config.DB_PATH
    print(f"Injecting mock PB data into {db_path}...")
    
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    
    # 1. Waypoints from gps.py
    MOCK_ROUTE = [
        (37.7751, -122.4193), (37.7751, -122.4175), (37.7745, -122.4175),
        (37.7730, -122.4175), (37.7720, -122.4175), (37.7720, -122.4200),
        (37.7720, -122.4225), (37.7720, -122.4250), (37.7735, -122.4263),
        (37.7751, -122.4263), (37.7765, -122.4263), (37.7765, -122.4240),
        (37.7765, -122.4215), (37.7765, -122.4193), (37.7751, -122.4193),
    ]
    
    SPEED_KMH = 35.0
    METERS_PER_SEC = SPEED_KMH / 3.6
    
    # 2. Setup Locations
    # Start (Civic Center)
    cur = conn.execute("SELECT id FROM locations WHERE name = ?", ("Home",))
    row = cur.fetchone()
    if row:
        home_id = row['id']
    else:
        cur = conn.execute("INSERT INTO locations (name, lat, lon) VALUES (?, ?, ?)", ("Home", MOCK_ROUTE[0][0], MOCK_ROUTE[0][1]))
        home_id = cur.lastrowid
        
    # End (Civic Center - it's a loop)
    # Actually let's just use the same location or a close one
    cur = conn.execute("SELECT id FROM locations WHERE name = ?", ("Work",))
    row = cur.fetchone()
    if row:
        work_id = row['id']
    else:
        # Use the same coordinates but a different name for routine matching
        cur = conn.execute("INSERT INTO locations (name, lat, lon) VALUES (?, ?, ?)", ("Work", MOCK_ROUTE[-1][0], MOCK_ROUTE[-1][1]))
        work_id = cur.lastrowid

    # 3. Create Trip
    start_ts = time.time() - 3600 # 1 hour ago
    cur = conn.execute(
        "INSERT INTO trips (start_ts, start_lat, start_lon, end_lat, end_lon, is_mock, start_location_id, end_location_id, score) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (start_ts, MOCK_ROUTE[0][0], MOCK_ROUTE[0][1], MOCK_ROUTE[-1][0], MOCK_ROUTE[-1][1], 1, home_id, work_id, 100)
    )
    trip_id = cur.lastrowid
    
    # 4. Populate Route
    pts = []
    current_ts = start_ts
    last_lat, last_lon = MOCK_ROUTE[0]
    
    # Add start point
    pts.append((trip_id, current_ts, last_lat, last_lon, SPEED_KMH, 15.0, 0.0))
    
    for lat, lon in MOCK_ROUTE[1:]:
        # Calculate distance
        dlat = (lat - last_lat) * 111000.0
        dlon = (lon - last_lon) * 111000.0 * math.cos(math.radians(last_lat))
        dist = math.hypot(dlat, dlon)
        
        # Calculate time step
        duration = dist / METERS_PER_SEC
        
        # Interpolate points if distance is large (every ~5 meters)
        steps = max(1, int(dist / 5.0))
        for s in range(1, steps + 1):
            frac = s / steps
            i_lat = last_lat + (lat - last_lat) * frac
            i_lon = last_lon + (lon - last_lon) * frac
            i_ts = current_ts + (duration * frac)
            course = math.degrees(math.atan2(dlon, dlat)) % 360
            pts.append((trip_id, i_ts, i_lat, i_lon, SPEED_KMH, 15.0, course))
            
        current_ts += duration
        last_lat, last_lon = lat, lon

    conn.executemany(
        "INSERT INTO trip_routes (trip_id, ts, lat, lon, speed, alt, course) VALUES (?, ?, ?, ?, ?, ?, ?)",
        pts
    )
    
    # Update trip end_ts and distance
    end_ts = current_ts
    total_dist = sum(math.hypot((pts[i][2]-pts[i-1][2])*111000, (pts[i][3]-pts[i-1][3])*111000*math.cos(math.radians(pts[i][2]))) for i in range(1, len(pts)))
    conn.execute("UPDATE trips SET end_ts = ?, distance = ? WHERE id = ?", (end_ts, total_dist, trip_id))
    
    # 5. Create/Update Routine
    duration = end_ts - start_ts
    cur = conn.execute("SELECT id FROM routines WHERE start_location_id = ? AND end_location_id = ?", (home_id, work_id))
    r = cur.fetchone()
    if r:
        conn.execute(
            "UPDATE routines SET pb_duration = ?, avg_duration = ?, trip_count = trip_count + 1, pb_trip_id = ? WHERE id = ?",
            (duration, duration, trip_id, r['id'])
        )
    else:
        conn.execute(
            "INSERT INTO routines (start_location_id, end_location_id, pb_duration, avg_duration, trip_count, pb_trip_id) VALUES (?, ?, ?, ?, 1, ?)",
            (home_id, work_id, duration, duration, trip_id)
        )
        
    conn.commit()
    conn.close()
    print(f"✅ Injected Trip #{trip_id} as PB for {duration:.0f}s routine.")

if __name__ == "__main__":
    inject()
