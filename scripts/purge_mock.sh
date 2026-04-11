#!/bin/bash
# Car Metrics — Purge Mock Data Utility (Self-Contained)

python3 <<EOF
import sqlite3
import os

DB_PATH = '/home/dietpi/car-metrics-data/car_metrics.db'
IMAGE_DIR = '/home/dietpi/car-metrics-data/images'

def purge():
    if not os.path.exists(DB_PATH):
        print("Error: Database not found")
        return
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    try:
        mock_trips = cursor.execute("SELECT id, start_ts, end_ts FROM trips WHERE is_mock = 1").fetchall()
        if not mock_trips:
            print("No mock trips found.")
            return
        trip_ids = [t['id'] for t in mock_trips]
        print(f"Purging {len(trip_ids)} mock trips: {trip_ids}")
        cursor.execute(f"DELETE FROM trip_routes WHERE trip_id IN ({','.join(['?']*len(trip_ids))})", trip_ids)
        cursor.execute(f"DELETE FROM events WHERE trip_id IN ({','.join(['?']*len(trip_ids))})", trip_ids)
        images_deleted = 0
        for trip in mock_trips:
            start, end = trip['start_ts'], trip['end_ts'] or (trip['start_ts'] + 3600)
            frames = cursor.execute("SELECT id, filename FROM camera_frames WHERE ts >= ? AND ts <= ?", (start, end)).fetchall()
            for frame in frames:
                img_path = os.path.join(IMAGE_DIR, frame['filename'])
                if os.path.exists(img_path): os.remove(img_path)
                images_deleted += 1
                cursor.execute("DELETE FROM camera_frames WHERE id = ?", (frame['id'],))
        print(f"Deleted {images_deleted} images.")
        cursor.execute("DELETE FROM trips WHERE is_mock = 1")
        conn.commit()
        print("Vacuuming...")
        conn.close()
        sqlite3.connect(DB_PATH).execute("VACUUM")
        print("✅ Purge complete.")
    except Exception as e:
        print(f"Error: {e}")
        conn.close()

purge()
EOF
