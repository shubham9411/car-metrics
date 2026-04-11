"""
Car Metrics — Smart Event Detection
Monitors accelerometer axes to classify driving events:
  - Sudden Brake / Sudden Accel (longitudinal ax)
  - Sharp Turn (lateral ay)
  - Pothole (vertical az spike)
  - High Impact (balanced extreme)
"""

import logging
import math
import time

import config

logger = logging.getLogger("utils.crash_detect")


class CrashDetector:
    """Detects and classifies driving events from IMU readings."""

    def __init__(self, on_event=None):
        """
        on_event: callback(event_type: str, g_force: float, details: str)
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

        if g_total >= self._threshold:
            now = time.time()
            if now - self._last_trigger_ts > self._cooldown:
                self._last_trigger_ts = now

                # Classify by dominant axis
                event_type, details = self._classify(ax, ay, az, g_total)

                logger.warning(
                    "⚠️  %s detected: %.2fg (threshold: %.1fg)",
                    event_type, g_total, self._threshold,
                )
                if self._on_event:
                    self._on_event(event_type, g_total, details)

    @staticmethod
    def _classify(ax, ay, az, g_total):
        """Classify event based on which axis dominates the impact."""
        abs_ax = abs(ax)
        abs_ay = abs(ay)
        # az deviation from gravity (normal ~1.0g)
        abs_az_dev = abs(az - 1.0)

        dominant = max(abs_ax, abs_ay, abs_az_dev)

        if dominant == abs_ax:
            if ax < 0:
                return "sudden_brake", f"Longitudinal decel: {ax:.2f}g"
            else:
                return "sudden_accel", f"Longitudinal accel: {ax:.2f}g"
        elif dominant == abs_ay:
            direction = "left" if ay < 0 else "right"
            return "sharp_turn", f"Lateral {direction}: {ay:.2f}g"
        elif dominant == abs_az_dev:
            return "pothole", f"Vertical shock: {az:.2f}g (dev {abs_az_dev:.2f})"
        else:
            return "high_impact", f"Total: {g_total:.2f}g"
