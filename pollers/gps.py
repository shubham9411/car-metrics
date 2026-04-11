"""
Car Metrics — GPS Poller
Reads NEO-8M GPS via UART using pyserial + pynmea2.
Parses GGA (position) and RMC (speed/course) sentences.
Falls back to IP geolocation or default coords when indoors.
"""

import asyncio
import json
import logging
import math
import os
import time
import urllib.request

import serial
import pynmea2

import config
from storage import db

logger = logging.getLogger("pollers.gps")


class GPSPoller:
    """Async GPS poller reading NMEA sentences from serial."""

    def __init__(self):
        self._serial = None
        self._running = False
        self._last_fix = None  # latest fix dict, shared with other modules
        self._has_satellite_fix = False
        self._fallback_fix = None

    @property
    def last_fix(self) -> dict | None:
        """Get the most recent GPS fix (for use by other modules)."""
        return self._last_fix

    def _init_serial(self):
        """Open serial port for GPS."""
        self._serial = serial.Serial(
            config.GPS_SERIAL_PORT,
            baudrate=config.GPS_BAUD_RATE,
            timeout=1.0,
        )
        logger.info(
            "GPS serial opened: %s @ %d baud",
            config.GPS_SERIAL_PORT,
            config.GPS_BAUD_RATE,
        )

    def _get_fallback_location(self) -> dict | None:
        """Get fallback location from IP geolocation or config defaults."""
        # Try IP geolocation first
        if config.GPS_USE_IP_FALLBACK:
            try:
                req = urllib.request.Request(
                    "http://ip-api.com/json/?fields=lat,lon,city,status",
                    headers={"User-Agent": "car-metrics/1.0"},
                )
                with urllib.request.urlopen(req, timeout=5) as resp:
                    data = json.loads(resp.read().decode())
                    if data.get("status") == "success":
                        fix = {
                            "ts": time.time(),
                            "lat": data["lat"],
                            "lon": data["lon"],
                            "alt": None,
                            "speed_knots": None,
                            "course": None,
                            "satellites": 0,
                            "fix_quality": 0,  # 0 = no satellite fix
                        }
                        logger.info(
                            "GPS fallback via IP: (%.4f, %.4f) — %s",
                            fix["lat"], fix["lon"], data.get("city", "?"),
                        )
                        return fix
            except Exception as e:
                logger.debug("IP geolocation failed: %s", e)

        # Fall back to config defaults
        if config.GPS_FALLBACK_LAT != 0 and config.GPS_FALLBACK_LON != 0:
            fix = {
                "ts": time.time(),
                "lat": config.GPS_FALLBACK_LAT,
                "lon": config.GPS_FALLBACK_LON,
                "alt": None,
                "speed_knots": None,
                "course": None,
                "satellites": 0,
                "fix_quality": 0,
            }
            logger.info(
                "GPS fallback via config: (%.4f, %.4f)",
                fix["lat"], fix["lon"],
            )
            return fix

        return None

    async def run(self):
        """Async poll loop — reads NMEA lines from serial."""
        self._init_serial()
        self._running = True

        # Get fallback location for when we're indoors
        self._fallback_fix = await asyncio.get_event_loop().run_in_executor(
            None, self._get_fallback_location
        )
        if self._fallback_fix and not self._has_satellite_fix:
            self._last_fix = self._fallback_fix
            db.insert_gps_fix(self._fallback_fix)

        logger.info("GPS poller started")

        sim_file = os.path.join(config.DATA_DIR, ".simulate_data")

        while self._running:
            try:
                if os.path.exists(sim_file):
                    # Emulate moving in a circle around San Francisco
                    t = time.time()
                    lat = 37.7749 + math.sin(t / 20) * 0.01
                    lon = -122.4194 + math.cos(t / 20) * 0.01
                    
                    fix = {
                        "ts": t, "lat": lat, "lon": lon, "alt": 15.0,
                        "speed_knots": 25.0 + math.sin(t / 10) * 15.0, "course": 90.0,
                        "satellites": 8, "fix_quality": 1
                    }
                    self._has_satellite_fix = True
                    self._last_fix = fix
                    db.insert_gps_fix(fix)
                    await asyncio.sleep(1.0)
                    continue

                # readline blocks briefly (timeout=1s), we run in executor
                line = await asyncio.get_event_loop().run_in_executor(
                    None, self._read_line
                )
                if not line:
                    continue

                fix = self._parse_line(line)
                if fix:
                    self._has_satellite_fix = True
                    self._last_fix = fix
                    db.insert_gps_fix(fix)

            except Exception as e:
                logger.error("GPS error: %s", e)
                await asyncio.sleep(2)

    def _read_line(self) -> str | None:
        """Read one NMEA line from serial (blocking, called in executor)."""
        try:
            raw = self._serial.readline()
            if raw:
                return raw.decode("ascii", errors="replace").strip()
        except (serial.SerialException, OSError) as e:
            logger.warning("GPS serial read error: %s", e)
        return None

    def _parse_line(self, line: str) -> dict | None:
        """Parse GGA or RMC sentence into a fix dict."""
        if not (line.startswith("$GPGGA") or line.startswith("$GNGGA") or
                line.startswith("$GPRMC") or line.startswith("$GNRMC")):
            return None

        try:
            msg = pynmea2.parse(line)
        except pynmea2.ParseError:
            return None

        if isinstance(msg, (pynmea2.types.talker.GGA,)):
            lat = msg.latitude if msg.latitude else None
            lon = msg.longitude if msg.longitude else None
            if lat is None or lon is None:
                return None
            return {
                "ts": time.time(),
                "lat": lat,
                "lon": lon,
                "alt": float(msg.altitude) if msg.altitude else None,
                "speed_knots": None,
                "course": None,
                "satellites": int(msg.num_sats) if msg.num_sats else 0,
                "fix_quality": int(msg.gps_qual) if msg.gps_qual else 0,
            }

        if isinstance(msg, (pynmea2.types.talker.RMC,)):
            lat = msg.latitude if msg.latitude else None
            lon = msg.longitude if msg.longitude else None
            if lat is None or lon is None:
                return None
            return {
                "ts": time.time(),
                "lat": lat,
                "lon": lon,
                "alt": None,
                "speed_knots": float(msg.spd_over_grnd) if msg.spd_over_grnd else None,
                "course": float(msg.true_course) if msg.true_course else None,
                "satellites": None,
                "fix_quality": None,
            }

        return None

    def stop(self):
        """Stop polling and close serial."""
        self._running = False
        if self._serial and self._serial.is_open:
            self._serial.close()
        logger.info("GPS poller stopped")
