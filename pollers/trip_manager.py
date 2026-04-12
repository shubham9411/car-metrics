"""
Trip Manager
Monitors vehicle telemetry to autonomously detect driving sessions (Trips),
track GPS footprints (Fog of War mapping), compute driver scores at the edge,
classify driving events, and purge massive high-frequency data locally.
"""

import asyncio
import logging
import math
import os
import time

import config
from storage import db

logger = logging.getLogger("trip_manager")
class TripManager:
    def __init__(self, gps_poller, obd_poller):
        self.gps = gps_poller
        self.obd = obd_poller
        self.active_trip_id = None
        self.start_lat = None
        self.start_lon = None
        self.last_route_lat = None
        self.last_route_lon = None
        self.last_route_course = None

        self.score = 100
        self.idle_start_ts = None
        self.total_distance = 0.0  # meters
        self._trip_start_ts = None
        self._start_location_id = None

        # Speeding detection state
        self._speeding_start_ts = None
        self._speed_threshold_kph = 80.0

        self.is_running = False

    async def run(self):
        self.is_running = True
        logger.info("TripManager started. Edge-Computing active.")

        # Cleanup orphaned trips from previous runs
        self._cleanup_orphaned_trips()

        last_purge_ts = 0

        while self.is_running:
            try:
                self._check_trip_state()
                self._update_route_footprint()
                self._check_speeding()

                # Cleanup old data every hour
                now = time.time()
                if now - last_purge_ts > 3600:
                    self._purge_old_telemetry()
                    last_purge_ts = now

            except Exception as e:
                logger.error("TripManager error: %s", e)

            await asyncio.sleep(5.0)

    def _cleanup_orphaned_trips(self):
        """Finds trips that were never closed (end_ts is NULL) and closes them
        using the timestamp of the last recorded route point.
        Also calculates distance from recorded points."""
        conn = db.get_connection()
        orphans = conn.execute("SELECT id, start_ts FROM trips WHERE end_ts IS NULL").fetchall()
        
        for row in orphans:
            trip_id = row["id"]
            if self.active_trip_id and trip_id == self.active_trip_id:
                continue

            # Get all route points to calculate total distance
            routes = conn.execute(
                "SELECT lat, lon, ts FROM trip_routes WHERE trip_id = ? ORDER BY ts ASC",
                (trip_id,)
            ).fetchall()
            
            end_ts = routes[-1]["ts"] if routes else row["start_ts"]
            
            # Sum distance
            dist = 0.0
            for i in range(len(routes) - 1):
                p1, p2 = routes[i], routes[i+1]
                if p1["lat"] is not None and p2["lat"] is not None:
                    d_lat = abs(p1["lat"] - p2["lat"]) * 111139
                    d_lon = abs(p1["lon"] - p2["lon"]) * 111139 * math.cos(math.radians(p1["lat"]))
                    dist += math.sqrt(d_lat**2 + d_lon**2)
            
            conn.execute(
                "UPDATE trips SET end_ts = ?, distance = ? WHERE id = ?", 
                (end_ts, dist, trip_id)
            )
            logger.info(f"🧹 Cleaned up orphaned Trip #{trip_id} (Dist: {dist:.0f}m, Closed at {end_ts})")
        
        conn.commit()

    def _update_live_score(self, penalty_deduction=0):
        """Recalculate the live weighted score: 100 - (Penalties / (1 + Dist_KM / 5))"""
        # We need to track the sum of raw penalties
        if not hasattr(self, '_total_penalties'):
            self._total_penalties = 100 - self.score # bootstrap from existing score
        
        self._total_penalties += penalty_deduction
        
        dist_km = self.total_distance / 1000.0
        # Formula: 100 - (Penalties / (1 + KM / 5))
        self.score = 100 - (self._total_penalties / (1 + dist_km / 5.0))
        self.score = max(0, min(100, round(self.score, 1)))

        if self.active_trip_id:
            conn = db.get_connection()
            conn.execute("UPDATE trips SET score=? WHERE id=?", (self.score, self.active_trip_id))
            conn.commit()

    def _check_trip_state(self):
        """State machine for detecting when a car is parked or driving."""
        fix = self.gps.last_fix
        speed = fix["speed_knots"] * 1.852 if fix and fix.get("speed_knots") else 0.0
        rpm = self.obd.get_rpm() or 0.0
        is_moving = speed > 5.0 or rpm > 500

        if not self.active_trip_id:
            if is_moving:
                self.start_trip(fix)
        else:
            # Deferred location identification if start fix was missing
            if self._start_location_id is None and fix and fix.get("lat"):
                self._start_location_id = db.upsert_location(fix["lat"], fix["lon"])
                if self._start_location_id:
                    conn = db.get_connection()
                    conn.execute("UPDATE trips SET start_location_id = ? WHERE id = ?", (self._start_location_id, self.active_trip_id))
                    conn.commit()
                    logger.info(f"📍 Start Location identified (deferred): ID {self._start_location_id}")

            if not is_moving:
                if self.idle_start_ts is None:
                    self.idle_start_ts = time.time()
                elif time.time() - self.idle_start_ts > 300:
                    self.end_trip(fix)
            else:
                self.idle_start_ts = None

    def _check_speeding(self):
        """Detect sustained speeding (> threshold for > 10 seconds)."""
        if not self.active_trip_id:
            return
        fix = self.gps.last_fix
        speed = (fix["speed_knots"] * 1.852) if fix and fix.get("speed_knots") else 0.0

        if speed > self._speed_threshold_kph:
            if self._speeding_start_ts is None:
                self._speeding_start_ts = time.time()
            elif time.time() - self._speeding_start_ts > 10:
                # Log speeding event (only once per sustained period)
                db.insert_event({
                    "ts": time.time(),
                    "event_type": "speeding",
                    "g_force": speed,  # store speed in g_force field for display
                    "lat": fix["lat"] if fix else None,
                    "lon": fix["lon"] if fix else None,
                    "details": f"Sustained {speed:.0f} kph for >{int(time.time() - self._speeding_start_ts)}s",
                    "trip_id": self.active_trip_id,
                })
                self.deduct_event_penalty(3)
                self._speeding_start_ts = None  # reset, will re-trigger if still speeding
        else:
            self._speeding_start_ts = None

    def start_trip(self, fix):
        self.idle_start_ts = None
        self.score = 100
        self._total_penalties = 0
        self.total_distance = 0.0
        self._speeding_start_ts = None

        lat = fix["lat"] if fix else None
        lon = fix["lon"] if fix else None

        self.start_lat = lat
        self.start_lon = lon
        self.last_route_lat = lat
        self.last_route_lon = lon
        self.last_route_course = None

        # Detect if mock mode is active
        sim_file = os.path.join(config.DATA_DIR, ".simulate_data")
        is_mock = 1 if os.path.exists(sim_file) else 0

        conn = db.get_connection()
        cur = conn.cursor()
        start_ts = time.time()
        
        # Anchor Point detection: Mark start of trip
        start_loc_id = None
        if lat is not None and lon is not None:
            start_loc_id = db.upsert_location(lat, lon)
            logger.info(f"📍 Start Location matched: ID {start_loc_id}")

        cur.execute(
            "INSERT INTO trips (start_ts, start_lat, start_lon, score, is_mock, start_location_id) VALUES (?, ?, ?, ?, ?, ?)",
            (start_ts, lat, lon, self.score, is_mock, start_loc_id)
        )
        self.active_trip_id = cur.lastrowid
        self._trip_start_ts = start_ts
        self._start_location_id = start_loc_id
        conn.commit()

        label = "🧪 Mock " if is_mock else "🚗 "
        logger.info(f"{label}Trip [{self.active_trip_id}] STARTED.")

    def end_trip(self, fix):
        if not self.active_trip_id:
            return

        lat = fix["lat"] if fix else None
        lon = fix["lon"] if fix else None

        logger.info(f"🛑 Trip [{self.active_trip_id}] ENDED (Idle timeout). Score: {self.score} | Dist: {self.total_distance:.0f}m")

        conn = db.get_connection()
        conn.execute(
            "UPDATE trips SET end_ts=?, end_lat=?, end_lon=?, score=?, distance=? WHERE id=?",
            (time.time(), lat, lon, self.score, self.total_distance, self.active_trip_id)
        )
        conn.commit()

        self.active_trip_id = None
        self.idle_start_ts = None
        self._speeding_start_ts = None
        
        # Anchor Point & Routine logic
        if lat is not None and lon is not None and self._start_location_id and self._trip_start_ts:
            end_loc_id = db.upsert_location(lat, lon)
            conn.execute("UPDATE trips SET end_location_id = ? WHERE id = ?", (end_loc_id, self.active_trip_id))
            conn.commit()
            
            if end_loc_id != self._start_location_id:
                duration = time.time() - self._trip_start_ts
                db.upsert_routine(self._start_location_id, end_loc_id, duration, self.active_trip_id)
                logger.info(f"🔄 Routine matched: {self._start_location_id} -> {end_loc_id} ({duration:.0f}s)")
        
        self._trip_start_ts = None
        self._start_location_id = None

    def _update_route_footprint(self):
        """Samples the route to build the 'Fog of War' map decoupled from 10Hz data.
        Now enriched with speed, altitude, and course for gradient map rendering.
        Also detects intersections via course-change heuristics and tracks distance."""
        if not self.active_trip_id:
            return

        fix = self.gps.last_fix
        if not fix or not fix.get("lat") or not fix.get("lon"):
            return

        lat = fix["lat"]
        lon = fix["lon"]
        speed_knots = fix.get("speed_knots") or 0.0
        speed_kph = speed_knots * 1.852
        alt = fix.get("alt")
        course = fix.get("course")

        drop_pin = False
        dist = 0.0
        if self.last_route_lat is None or self.last_route_lon is None:
            drop_pin = True
        else:
            d_lat = abs(lat - self.last_route_lat) * 111000
            d_lon = abs(lon - self.last_route_lon) * 111000 * math.cos(math.radians(lat))
            dist = math.sqrt(d_lat**2 + d_lon**2)
            if dist > 30.0:
                drop_pin = True

        if drop_pin:
            # Accumulate distance
            self.total_distance += dist
            
            # Recalculate score with new distance
            self._update_live_score(0)

            conn = db.get_connection()
            conn.execute(
                "INSERT INTO trip_routes (trip_id, ts, lat, lon, speed, alt, course) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (self.active_trip_id, time.time(), lat, lon, speed_kph, alt, course)
            )

            # Intersection detection: course-change heuristic
            if course is not None and self.last_route_course is not None:
                delta = abs(course - self.last_route_course)
                if delta > 180:
                    delta = 360 - delta
                if delta > 30:
                    self._record_intersection(lat, lon, "course_change")

            self.last_route_course = course
            self.last_route_lat = lat
            self.last_route_lon = lon
            conn.commit()

    def _record_intersection(self, lat, lon, detection_type):
        """Store a detected intersection. If we've seen this spot before (within ~20m),
        just bump the trip_count instead of creating a duplicate."""
        conn = db.get_connection()

        rows = conn.execute("SELECT id, trip_count FROM intersections").fetchall()

        for row in rows:
            ex = conn.execute("SELECT lat, lon FROM intersections WHERE id=?", (row["id"],)).fetchone()
            d_lat = abs(lat - ex["lat"]) * 111000
            d_lon = abs(lon - ex["lon"]) * 111000 * math.cos(math.radians(lat))
            dist = math.sqrt(d_lat**2 + d_lon**2)
            if dist < 20.0:
                conn.execute(
                    "UPDATE intersections SET trip_count = trip_count + 1 WHERE id=?",
                    (row["id"],)
                )
                return

        conn.execute(
            "INSERT INTO intersections (lat, lon, detection_type, first_seen_ts) VALUES (?, ?, ?, ?)",
            (lat, lon, detection_type, time.time())
        )

    def deduct_event_penalty(self, penalty):
        """Hook called when crash_detector/event_detector fires."""
        if self.active_trip_id:
            self._update_live_score(penalty)

    def _purge_old_telemetry(self):
        """Edges discarding: Deletes massive 10Hz vibration tracks older than 48 hours.
        Also auto-purges mock trips older than 1 hour."""
        conn = db.get_connection()
        cutoff = time.time() - (48 * 3600)
        
        cur = conn.cursor()
        cur.execute("DELETE FROM imu_readings WHERE ts < ?", (cutoff,))
        imu_d = cur.rowcount
        
        cur.execute("DELETE FROM obd_readings WHERE ts < ?", (cutoff,))
        obd_d = cur.rowcount

        # Auto-purge mock data older than 1 hour (EXCLUDING PB TRIPS)
        mock_cutoff = time.time() - 3600
        mock_trips = conn.execute("""
            SELECT id FROM trips 
            WHERE is_mock=1 
              AND start_ts < ? 
              AND id NOT IN (SELECT pb_trip_id FROM routines WHERE pb_trip_id IS NOT TRUE)
        """, (mock_cutoff,)).fetchall()

        mock_d = 0
        for mt in mock_trips:
            tid = mt["id"]
            conn.execute("DELETE FROM trip_routes WHERE trip_id=?", (tid,))
            conn.execute("DELETE FROM events WHERE trip_id=?", (tid,))
            conn.execute("DELETE FROM trips WHERE id=?", (tid,))
            mock_d += 1

        conn.commit()
        
        if imu_d > 0 or obd_d > 0:
            logger.info(f"🧹 Smart Discard: Purged {imu_d} IMU rows, {obd_d} OBD rows older than 48h.")
        if mock_d > 0:
            logger.info(f"🧹 Mock Purge: Deleted {mock_d} mock trips older than 1h.")

    def stop(self):
        self.is_running = False
