"""
Trip Manager
Monitors vehicle telemetry to autonomously detect driving sessions (Trips),
track GPS footprints (Fog of War mapping), compute driver scores at the edge,
and purge massive high-frequency data locally to protect SD Card storage.
"""

import asyncio
import logging
import math
import time

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
        
        self.score = 100
        self.idle_start_ts = None
        
        # We start looking immediately
        self.is_running = False

    async def run(self):
        self.is_running = True
        logger.info("TripManager started. Edge-Computing active.")
        
        # Purge loop timers
        last_purge_ts = 0

        while self.is_running:
            try:
                self._check_trip_state()
                self._update_route_footprint()
                
                # Cleanup raw vibration data every hour loosely
                now = time.time()
                if now - last_purge_ts > 3600:
                    self._purge_old_telemetry()
                    last_purge_ts = now
                
            except Exception as e:
                logger.error("TripManager error: %s", e)

            await asyncio.sleep(5.0)

    def _check_trip_state(self):
        """State machine for detecting when a car is parked or driving."""
        fix = self.gps.last_fix
        speed = fix["speed_knots"] * 1.852 if fix and fix.get("speed_knots") else 0.0
        
        # Fallback OBD RPM if GPS is missing
        rpm = self.obd.get_rpm() or 0.0
        
        is_moving = speed > 5.0 or rpm > 500

        if not self.active_trip_id:
            # Detect trip start
            if is_moving:
                self.start_trip(fix)
        else:
            # Detect trip end (5 mins idle)
            if not is_moving:
                if self.idle_start_ts is None:
                    self.idle_start_ts = time.time()
                elif time.time() - self.idle_start_ts > 300: # 5 minutes idle
                    self.end_trip(fix)
            else:
                self.idle_start_ts = None  # reset idle tracker if we move again

    def start_trip(self, fix):
        self.idle_start_ts = None
        self.score = 100
        
        lat = fix["lat"] if fix else None
        lon = fix["lon"] if fix else None
        
        self.start_lat = lat
        self.start_lon = lon
        self.last_route_lat = lat
        self.last_route_lon = lon

        # Insert new trip
        conn = db.get_connection()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO trips (start_ts, start_lat, start_lon, score) VALUES (?, ?, ?, ?)",
            (time.time(), lat, lon, self.score)
        )
        self.active_trip_id = cur.lastrowid
        conn.commit()
        logger.info(f"🚗 Trip [{self.active_trip_id}] STARTED.")

    def end_trip(self, fix):
        if not self.active_trip_id:
            return
            
        lat = fix["lat"] if fix else None
        lon = fix["lon"] if fix else None
        
        logger.info(f"🛑 Trip [{self.active_trip_id}] ENDED (Idle timeout). Score: {self.score}")

        conn = db.get_connection()
        conn.execute(
            "UPDATE trips SET end_ts=?, end_lat=?, end_lon=?, score=? WHERE id=?",
            (time.time(), lat, lon, self.score, self.active_trip_id)
        )
        conn.commit()
        
        self.active_trip_id = None
        self.idle_start_ts = None

    def _update_route_footprint(self):
        """Samples the route to build the 'Fog of War' map decoupled from 10Hz data."""
        if not self.active_trip_id:
            return
            
        fix = self.gps.last_fix
        if not fix or not fix.get("lat") or not fix.get("lon"):
            return
            
        lat = fix["lat"]
        lon = fix["lon"]
        
        # Haversine distance check roughly
        drop_pin = False
        if self.last_route_lat is None or self.last_route_lon is None:
            drop_pin = True
        else:
            # roughly 30 meter grid check
            d_lat = abs(lat - self.last_route_lat) * 111000
            d_lon = abs(lon - self.last_route_lon) * 111000 * math.cos(math.radians(lat))
            dist = math.sqrt(d_lat**2 + d_lon**2)
            if dist > 30.0:
                drop_pin = True
                
        if drop_pin:
            self.last_route_lat = lat
            self.last_route_lon = lon
            conn = db.get_connection()
            conn.execute(
                "INSERT INTO trip_routes (trip_id, ts, lat, lon) VALUES (?, ?, ?, ?)",
                (self.active_trip_id, time.time(), lat, lon)
            )
            conn.commit()

    def deduct_event_penalty(self, penalty):
        """Hook called when crash_detector/event_detector fires."""
        if self.active_trip_id:
            self.score = max(0, self.score - penalty)
            # Sync back to DB immediately
            conn = db.get_connection()
            conn.execute("UPDATE trips SET score=? WHERE id=?", (self.score, self.active_trip_id))
            conn.commit()

    def _purge_old_telemetry(self):
        """Edges discarding: Deletes massive 10Hz vibration tracks older than 48 hours."""
        conn = db.get_connection()
        cutoff = time.time() - (48 * 3600)
        
        cur = conn.cursor()
        cur.execute("DELETE FROM imu_readings WHERE ts < ?", (cutoff,))
        imu_d = cur.rowcount
        
        cur.execute("DELETE FROM obd_readings WHERE ts < ?", (cutoff,))
        obd_d = cur.rowcount
        
        conn.commit()
        
        if imu_d > 0 or obd_d > 0:
            logger.info(f"🧹 Smart Discard: Purged {imu_d} IMU rows, {obd_d} OBD rows older than 48h.")

    def stop(self):
        self.is_running = False
