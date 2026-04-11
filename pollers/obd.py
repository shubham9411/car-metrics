"""
Car Metrics — OBD2 Poller
Reads car ECU data via Bluetooth ELM327 using python-obd in async mode.
"""

import asyncio
import logging
import math
import os
import random
import time

import config
from storage import db

logger = logging.getLogger("pollers.obd")


class OBDPoller:
    """Async OBD2 poller using python-obd's Async class."""

    def __init__(self, gps_poller=None):
        self._connection = None
        self._running = False
        self._latest_values = {}  # pid -> {value, unit, ts}
        self.gps = gps_poller

    @property
    def latest(self) -> dict:
        """Latest OBD values for dashboard use."""
        return dict(self._latest_values)

    def get_rpm(self) -> float:
        # Faked RPM injected directly into logic if mock enabled
        sim_file = os.path.join(config.DATA_DIR, ".simulate_data")
        if os.path.exists(sim_file) and self.gps:
            fix = self.gps.last_fix
            speed = fix["speed_knots"] * 1.852 if fix else 0.0
            # Base RPM on speed + some noise
            base_rpm = 800.0 + (speed * 60) # Simple linear relation for simulation
            return base_rpm + random.uniform(-50, 50)
            
        val = self._latest_values.get("RPM")
        return val["value"] if val else 0.0

    def _init_connection(self):
        """Connect to ELM327 via Bluetooth serial."""
        try:
            import obd

            logger.info("Connecting to OBD2 at %s ...", config.OBD_PORT)
            self._connection = obd.Async(
                portstr=config.OBD_PORT,
                baudrate=config.OBD_BAUD,
                fast=config.OBD_FAST,
                timeout=10,
            )

            if self._connection.is_connected():
                logger.info(
                    "OBD2 connected: %s | Protocol: %s",
                    self._connection.port_name(),
                    self._connection.protocol_name(),
                )
                self._watch_pids()
            else:
                logger.warning("OBD2 connection failed — will retry")
                self._connection = None

        except ImportError:
            logger.warning("python-obd not available — OBD disabled")
            self._connection = None
        except Exception as e:
            logger.error("OBD2 init error: %s", e)
            self._connection = None

    def _watch_pids(self):
        """Register watched PIDs with async callbacks."""
        import obd

        for pid_name in config.OBD_WATCHED_PIDS:
            cmd = obd.commands.get(pid_name)
            if cmd is None:
                logger.warning("Unknown OBD PID: %s — skipping", pid_name)
                continue

            if self._connection.supports(cmd):
                self._connection.watch(cmd, callback=self._make_callback(pid_name))
                logger.debug("Watching PID: %s", pid_name)
            else:
                logger.info("Vehicle does not support PID: %s", pid_name)

    def _make_callback(self, pid_name: str):
        """Create a callback closure for a specific PID."""

        def callback(response):
            if response.is_null():
                return
            ts = time.time()
            value = response.value.magnitude if hasattr(response.value, "magnitude") else float(response.value)
            unit = str(response.value.units) if hasattr(response.value, "units") else ""

            # Cache latest value
            self._latest_values[pid_name] = {"value": value, "unit": unit, "ts": ts}

            # Store in DB
            db.insert_obd_reading({
                "ts": ts,
                "pid": pid_name,
                "value": value,
                "unit": unit,
            })

        return callback

    async def run(self):
        """Start the OBD async connection and keep it alive."""
        self._running = True

        while self._running:
            if self._connection is None:
                self._init_connection()

            if self._connection and self._connection.is_connected():
                self._connection.start()
                logger.info("OBD2 async polling started")

                # Keep alive — check connection periodically
                while self._running and self._connection.is_connected():
                    await asyncio.sleep(5)

                # Connection lost
                logger.warning("OBD2 connection lost — will retry in 10s")
                try:
                    self._connection.stop()
                    self._connection.close()
                except Exception:
                    pass
                self._connection = None

            # Retry delay
            if self._running:
                await asyncio.sleep(10)

    def stop(self):
        """Stop OBD polling and close connection."""
        self._running = False
        if self._connection:
            try:
                self._connection.stop()
                self._connection.close()
            except Exception:
                pass
        logger.info("OBD poller stopped")
