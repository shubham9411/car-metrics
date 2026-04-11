"""
Car Metrics — GPS Poller
Reads NEO-8M GPS via UART using pyserial + pynmea2.
Parses GGA (position) and RMC (speed/course) sentences.
"""

import asyncio
import logging
import time

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

    async def run(self):
        """Async poll loop — reads NMEA lines from serial."""
        self._init_serial()
        self._running = True

        logger.info("GPS poller started")

        while self._running:
            try:
                # readline blocks briefly (timeout=1s), we run in executor
                line = await asyncio.get_event_loop().run_in_executor(
                    None, self._read_line
                )
                if not line:
                    continue

                fix = self._parse_line(line)
                if fix:
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
