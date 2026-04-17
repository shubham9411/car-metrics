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
from pollers.bme680_poller import BME680Poller
from pollers.trip_manager import TripManager
from pollers.display_poller import DisplayPoller
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
        self.gps = GPSPoller()
        self.camera = CameraPoller()
        self.imu = IMUPoller(gps_poller=self.gps)
        self.obd = OBDPoller(gps_poller=self.gps)
        self.bme680 = BME680Poller()
        self.display = DisplayPoller(
            gps_poller=self.gps,
            obd_poller=self.obd,
            bme680_poller=self.bme680,
            imu_poller=self.imu,
        )
        self.trip_manager = TripManager(self.gps, self.obd)
        self.sync_engine = SyncEngine()
        self.crash_detector = CrashDetector(on_event=self._on_crash_event)
        self._shutdown_event = asyncio.Event()

    def _on_crash_event(self, event_type: str, g_force: float, details: str):
        """Called when crash detector fires — trigger camera burst + log classified event."""
        self.camera.trigger_burst()

        # Log event to DB with GPS position if available
        fix = self.gps.last_fix
        db.insert_event({
            "ts": time.time(),
            "event_type": event_type,
            "g_force": g_force,
            "lat": fix["lat"] if fix else None,
            "lon": fix["lon"] if fix else None,
            "details": details,
            "trip_id": self.trip_manager.active_trip_id,
        })
        
        # Variable penalty by type
        penalties = {"sudden_brake": 5, "sudden_accel": 3, "sharp_turn": 4, "pothole": 2, "high_impact": 8, "speeding": 3}
        self.trip_manager.deduct_event_penalty(penalties.get(event_type, 5))

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

    async def _watchdog(self, name: str, coro_func, *args, **kwargs):
        """Monitor and restart a component task if it crashes."""
        while not self._shutdown_event.is_set():
            try:
                # Some pollers might have a .run method
                if hasattr(coro_func, "run"):
                    await coro_func.run(*args, **kwargs)
                else:
                    await coro_func(*args, **kwargs)
                # Small delay to prevent tight-looping if task returns immediately
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                break
            except Exception as e:
                if not self._shutdown_event.is_set():
                    logger.error("⚙️ Component '%s' crashed: %s. Restarting in 10s...", name, e)
                    await asyncio.sleep(10)
                else:
                    break

    async def start(self):
        """Start all tasks in the event loop with supervision."""
        logger.info("=" * 50)
        logger.info("  Car Metrics — Starting up (Resilient Mode)")
        logger.info("  Data dir: %s", config.DATA_DIR)
        logger.info("=" * 50)
 
        # Ensure DB is initialized
        db.get_connection()
 
        # Optional: Auto-Calibration on first run
        if not os.path.exists(self.imu._offset_file):
            logger.info("📐 No IMU offsets found — performing first-run auto-tare...")
            await self.imu.calibrate_level(samples=20)

        # Define supervised tasks
        tasks = [
            asyncio.create_task(self._watchdog("IMU", self.imu.run, 
                on_reading=self.crash_detector.check,
                is_car_on_func=lambda: self.trip_manager.active_trip_id is not None
            )),
            asyncio.create_task(self._watchdog("GPS", self.gps.run)),
            asyncio.create_task(self._watchdog("Camera", self.camera.run)),
            asyncio.create_task(self._watchdog("OBD", self.obd.run)),
            asyncio.create_task(self._watchdog("BME680", self.bme680.run)),
            asyncio.create_task(self._watchdog("Display", self.display.run)),
            asyncio.create_task(self._watchdog("TripMgr", self.trip_manager.run)),
            asyncio.create_task(self._watchdog("Sync", self.sync_engine.run)),
            asyncio.create_task(self._watchdog("Heartbeat", self._heartbeat)),
            asyncio.create_task(self._watchdog("OBDCamLink", self._obd_camera_link)),
        ]

        # Wait for shutdown signal
        await self._shutdown_event.wait()

        # Cancel all tasks
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def shutdown(self):
        """Graceful shutdown."""
        self.imu.stop()
        self.gps.stop()
        self.camera.stop()
        self.obd.stop()
        self.bme680.stop()
        self.display.stop()
        self.trip_manager.stop()
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
