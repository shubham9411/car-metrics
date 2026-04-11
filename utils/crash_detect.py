"""
Car Metrics — Crash / Event Detection
Monitors accelerometer G-force magnitude and triggers alerts.
"""

import logging
import math
import time

import config

logger = logging.getLogger("utils.crash_detect")


class CrashDetector:
    """Detects high-G events from IMU readings."""

    def __init__(self, on_event=None):
        """
        on_event: callback(g_force: float) called when threshold exceeded.
                  Used to trigger camera burst, log event, etc.
        """
        self._on_event = on_event
        self._last_trigger_ts = 0
        self._threshold = config.CRASH_G_THRESHOLD
        self._cooldown = config.CRASH_COOLDOWN_SEC

    def check(self, reading: dict):
        """
        Called by IMU poller on each reading.
        reading must have ax, ay, az in g-units.
        """
        ax = reading.get("ax", 0)
        ay = reading.get("ay", 0)
        az = reading.get("az", 0)

        # Total G-force magnitude (at rest: ~1.0g from gravity)
        g_total = math.sqrt(ax * ax + ay * ay + az * az)

        # We care about deviation from normal gravity (~1g)
        # So effective impact G = |g_total - 1.0| for simple detection
        # Or just use raw total if threshold accounts for gravity
        if g_total >= self._threshold:
            now = time.time()
            if now - self._last_trigger_ts > self._cooldown:
                self._last_trigger_ts = now
                logger.warning(
                    "⚠️  High-G event detected: %.2fg (threshold: %.1fg)",
                    g_total,
                    self._threshold,
                )
                if self._on_event:
                    self._on_event(g_total)
