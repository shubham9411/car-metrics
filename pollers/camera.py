"""
Car Metrics — Camera Poller
Captures JPEG stills from OV5647 via picamera2.
Normal mode: every 5 seconds. Burst mode: 1/sec for 10 frames on event trigger.
"""

import asyncio
import logging
import os
import time
from datetime import datetime

import config
from storage import db

logger = logging.getLogger("pollers.camera")


class CameraPoller:
    """Async camera capture with burst-on-event support."""

    def __init__(self):
        self._picam = None
        self._running = False
        self._burst_requested = False

    def _init_camera(self):
        """Initialize picamera2 (system-installed)."""
        try:
            from picamera2 import Picamera2

            self._picam = Picamera2()
            cam_config = self._picam.create_still_configuration(
                main={"size": (config.CAMERA_WIDTH, config.CAMERA_HEIGHT)},
                buffer_count=2,
            )
            self._picam.configure(cam_config)
            self._picam.start()
            logger.info(
                "Camera initialized: %dx%d @ JPEG q%d",
                config.CAMERA_WIDTH,
                config.CAMERA_HEIGHT,
                config.CAMERA_JPEG_QUALITY,
            )
        except ImportError:
            logger.warning("picamera2 not available — camera disabled")
            self._picam = None
        except Exception as e:
            logger.error("Camera init failed: %s", e)
            self._picam = None

    def trigger_burst(self):
        """Called by crash detector to request burst capture."""
        self._burst_requested = True
        logger.info("Camera burst triggered")

    async def run(self):
        """Async capture loop."""
        self._init_camera()
        if not self._picam:
            logger.warning("Camera poller not started — no camera available")
            return

        self._running = True
        logger.info("Camera poller started (interval=%ds)", config.CAMERA_INTERVAL_SEC)

        while self._running:
            try:
                if self._burst_requested:
                    await self._do_burst()
                    self._burst_requested = False
                else:
                    self._capture_frame(event_triggered=False)
                    await asyncio.sleep(config.CAMERA_INTERVAL_SEC)
            except Exception as e:
                logger.error("Camera capture error: %s", e)
                await asyncio.sleep(5)

    async def _do_burst(self):
        """Capture burst frames (1/sec for N frames)."""
        logger.info("Burst capture: %d frames", config.CAMERA_BURST_COUNT)
        for i in range(config.CAMERA_BURST_COUNT):
            self._capture_frame(event_triggered=True)
            if i < config.CAMERA_BURST_COUNT - 1:
                await asyncio.sleep(config.CAMERA_BURST_INTERVAL_SEC)

    def _capture_frame(self, event_triggered: bool = False):
        """Capture a single JPEG frame and save atomically."""
        ts = time.time()
        dt = datetime.fromtimestamp(ts)
        filename = dt.strftime("%Y%m%d_%H%M%S") + f"_{int(ts*1000)%1000:03d}.jpg"
        filepath = os.path.join(config.IMAGE_DIR, filename)
        tmp_path = filepath + ".tmp"

        try:
            # Capture to temp file, then atomic rename
            self._picam.capture_file(
                tmp_path,
                format="jpeg",
                quality=config.CAMERA_JPEG_QUALITY,
            )
            os.rename(tmp_path, filepath)
            db.insert_camera_frame(ts, filename, event_triggered)
            logger.debug("Captured: %s (event=%s)", filename, event_triggered)
        except Exception as e:
            logger.error("Frame capture failed: %s", e)
            # Clean up temp file
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

        # Rotate old images
        self._rotate_images()

    def _rotate_images(self):
        """Delete oldest images if over the local limit."""
        try:
            files = sorted(os.listdir(config.IMAGE_DIR))
            jpg_files = [f for f in files if f.endswith(".jpg")]
            excess = len(jpg_files) - config.CAMERA_MAX_LOCAL_IMAGES
            if excess > 0:
                for f in jpg_files[:excess]:
                    os.remove(os.path.join(config.IMAGE_DIR, f))
                logger.debug("Rotated %d old images", excess)
        except OSError as e:
            logger.warning("Image rotation error: %s", e)

    def stop(self):
        """Stop camera and release resources."""
        self._running = False
        if self._picam:
            try:
                self._picam.stop()
                self._picam.close()
            except Exception:
                pass
        logger.info("Camera poller stopped")
