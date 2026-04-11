"""
Car Metrics — Main Orchestrator
Single asyncio event loop running all sensor pollers + sync.
Designed for Raspberry Pi Zero (single-core, 512MB RAM, DietPi).
"""

import asyncio
import logging
import os
import signal
import sys
import time

import config
from storage import db
from storage.sync import SyncEngine
from pollers.imu import IMUPoller
from pollers.gps import GPSPoller
from pollers.camera import CameraPoller
from pollers.obd import OBDPoller
from utils.crash_detect import CrashDetector

# ─── Logging setup ────────────────────────────────
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("main")


class CarMetrics:
    """Main application — orchestrates all components."""

    def __init__(self):
        self.camera = CameraPoller()
        self.imu = IMUPoller()
        self.gps = GPSPoller()
        self.obd = OBDPoller()
        self.sync_engine = SyncEngine()
        self.crash_detector = CrashDetector(on_event=self._on_crash_event)
        self._shutdown_event = asyncio.Event()

    def _on_crash_event(self, g_force: float):
        """Called when crash detector fires — trigger camera burst + log event."""
        self.camera.trigger_burst()

        # Log event to DB with GPS position if available
        fix = self.gps.last_fix
        db.insert_event({
            "ts": time.time(),
            "event_type": "high_g",
            "g_force": g_force,
            "lat": fix["lat"] if fix else None,
            "lon": fix["lon"] if fix else None,
            "details": f"G-force: {g_force:.2f}g",
        })

    async def _heartbeat(self):
        """Periodic status log — CPU/RAM usage for diagnostics."""
        while not self._shutdown_event.is_set():
            try:
                # Read Pi system stats from /proc
                with open("/proc/loadavg", "r") as f:
                    loadavg = f.read().strip().split()[0]
                with open("/proc/meminfo", "r") as f:
                    meminfo = f.read()
                    mem_total = mem_avail = 0
                    for line in meminfo.splitlines():
                        if line.startswith("MemTotal:"):
                            mem_total = int(line.split()[1]) // 1024
                        elif line.startswith("MemAvailable:"):
                            mem_avail = int(line.split()[1]) // 1024

                gps_fix = self.gps.last_fix
                gps_status = (
                    f"({gps_fix['lat']:.5f}, {gps_fix['lon']:.5f})"
                    if gps_fix and gps_fix.get("lat")
                    else "no fix"
                )

                logger.info(
                    "💓 Load: %s | RAM: %dMB/%dMB | GPS: %s",
                    loadavg,
                    mem_total - mem_avail,
                    mem_total,
                    gps_status,
                )
            except Exception:
                logger.info("💓 Heartbeat (system stats unavailable on this OS)")

            await asyncio.sleep(config.HEARTBEAT_INTERVAL_SEC)

    async def _obd_camera_link(self):
        """Keep camera.obd_connected in sync with OBD state."""
        while not self._shutdown_event.is_set():
            connected = (
                self.obd._connection is not None
                and self.obd._connection.is_connected()
            )
            self.camera.obd_connected = connected
            await asyncio.sleep(5)

    async def start(self):
        """Start all tasks in the event loop."""
        logger.info("=" * 50)
        logger.info("  Car Metrics — Starting up")
        logger.info("  Data dir: %s", config.DATA_DIR)
        logger.info("=" * 50)

        # Ensure DB is initialized
        db.get_connection()

        # Create all tasks
        tasks = [
            asyncio.create_task(self.imu.run(on_reading=self.crash_detector.check)),
            asyncio.create_task(self.gps.run()),
            asyncio.create_task(self.camera.run()),
            asyncio.create_task(self.obd.run()),
            asyncio.create_task(self.sync_engine.run()),
            asyncio.create_task(self._heartbeat()),
            asyncio.create_task(self._obd_camera_link()),
        ]

        # Wait for shutdown signal
        await self._shutdown_event.wait()

        # Cancel all tasks
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def shutdown(self):
        """Graceful shutdown."""
        logger.info("Shutting down...")
        self.imu.stop()
        self.gps.stop()
        self.camera.stop()
        self.obd.stop()
        self.sync_engine.stop()
        db.close()
        self._shutdown_event.set()
        logger.info("Shutdown complete")


def main():
    app = CarMetrics()

    # Handle signals for clean shutdown
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, app.shutdown)

    try:
        loop.run_until_complete(app.start())
    except KeyboardInterrupt:
        app.shutdown()
    finally:
        loop.close()


if __name__ == "__main__":
    main()
