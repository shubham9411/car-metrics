"""
Car Metrics — BME680 Environmental Sensor Poller
Reads temperature, humidity, pressure, and gas resistance (VOC/IAQ)
via I2C using the Pimoroni bme680 library. Polls every ~5s.
"""

import asyncio
import logging
import math
import os
import random
import time
from collections import deque

import config
from storage import db

logger = logging.getLogger("pollers.bme680")


class BME680Poller:
    """Async poller for the BME680 environmental sensor."""

    def __init__(self):
        self._sensor = None
        self._running = False
        self._last_reading = None
        self._iaq_buffer = deque(maxlen=12)  # last 1 minute of readings
        self._gas_baseline = None
        self._start_time = time.time()
        self._burn_in_sec = 30  # Reduced to 30s for faster feedback

    @property
    def last_reading(self) -> dict | None:
        return self._last_reading

    def _init_hardware(self):
        """Initialize the BME680 sensor."""
        try:
            import bme680
            try:
                self._sensor = bme680.BME680(bme680.I2C_ADDR_PRIMARY)  # 0x76
            except (RuntimeError, IOError):
                self._sensor = bme680.BME680(bme680.I2C_ADDR_SECONDARY)  # 0x77

            # Configure oversampling and filter
            self._sensor.set_humidity_oversample(bme680.OS_2X)
            self._sensor.set_pressure_oversample(bme680.OS_4X)
            self._sensor.set_temperature_oversample(bme680.OS_8X)
            self._sensor.set_filter(bme680.FILTER_SIZE_3)

            # Gas heater config for IAQ
            self._sensor.set_gas_status(bme680.ENABLE_GAS_MEAS)
            self._sensor.set_gas_heater_temperature(320)
            self._sensor.set_gas_heater_duration(150)
            self._sensor.select_gas_heater_profile(0)

            logger.info("BME680 initialized at I2C 0x%02x", config.BME680_ADDR)
        except Exception as e:
            logger.error("BME680 init failed: %s", e)
            self._sensor = None

    def _compute_iaq(self, gas_resistance: float, humidity: float) -> float:
        """
        Compute a simple Indoor Air Quality (IAQ) score 0-500.
        Lower = better air quality.
        """
        # Gas baseline tracking (higher = cleaner air)
        if self._gas_baseline is None or gas_resistance > self._gas_baseline:
            self._gas_baseline = gas_resistance

        # No longer blocking with burn-in per user request

        # Gas contribution (75%)
        # Scale: 0-100% where 100% is at the baseline (cleanest air seen)
        # We assume 25% of the baseline is the "poor" threshold
        gas_lower_limit = self._gas_baseline * 0.25
        gas_range = self._gas_baseline - gas_lower_limit
        if gas_range > 0:
            gas_score = max(0, min(100, (gas_resistance - gas_lower_limit) / gas_range * 100.0))
        else:
            gas_score = 100.0

        # Humidity contribution (25% weight) — optimal is 40%
        hum_baseline = 40.0
        if humidity >= hum_baseline:
            hum_score = 100.0 - ((humidity - hum_baseline) / (100.0 - hum_baseline) * 100.0)
        else:
            hum_score = 100.0 - ((hum_baseline - humidity) / hum_baseline * 100.0)
        hum_score = max(0, min(100, hum_score))

        # Weighted composite (inverted: 0 = best, 500 = worst)
        composite = gas_score * 0.75 + hum_score * 0.25
        iaq = 500.0 * (1.0 - composite / 100.0)
        return round(max(0, min(500, iaq)), 1)

    async def run(self):
        """Async poll loop — reads BME680 every N seconds."""
        self._running = True
        sim_file = os.path.join(config.DATA_DIR, ".simulate_data")

        # Try real hardware init
        if not os.path.exists(sim_file):
            await asyncio.get_event_loop().run_in_executor(None, self._init_hardware)

        logger.info("BME680 poller started (interval=%ds)", config.BME680_POLL_SEC)

        while self._running:
            try:
                if os.path.exists(sim_file):
                    # ── Mock data ──
                    t = time.time()
                    reading = {
                        "ts": t,
                        "temperature": 24.0 + math.sin(t / 60) * 3.0 + random.uniform(-0.2, 0.2),
                        "humidity": 45.0 + math.sin(t / 90) * 10.0 + random.uniform(-0.5, 0.5),
                        "pressure": 1013.25 + math.sin(t / 120) * 2.0,
                        "gas_resistance": 40000 + math.sin(t / 45) * 15000 + random.uniform(-500, 500),
                    }
                    raw_iaq = self._compute_iaq(reading["gas_resistance"], reading["humidity"])
                    self._iaq_buffer.append(raw_iaq) if raw_iaq is not None else None
                    reading["iaq_score"] = round(sum(self._iaq_buffer) / len(self._iaq_buffer), 1) if self._iaq_buffer else None
                    reading["gas_baseline"] = self._gas_baseline or 50000.0
                    reading["is_mock"] = 1
                    
                    self._last_reading = reading
                    db.insert_env_reading(reading)
                    await asyncio.sleep(config.BME680_POLL_SEC)
                    continue

                # ── Real hardware ──
                if self._sensor is None:
                    await asyncio.get_event_loop().run_in_executor(None, self._init_hardware)
                    if self._sensor is None:
                        await asyncio.sleep(10)
                        continue

                if await asyncio.get_event_loop().run_in_executor(None, self._sensor.get_sensor_data):
                    data = self._sensor.data
                    gas_res = data.gas_resistance if data.heat_stable else None

                    reading = {
                        "ts": time.time(),
                        "temperature": round(data.temperature, 2),
                        "humidity": round(data.humidity, 2),
                        "pressure": round(data.pressure, 2),
                        "gas_resistance": round(gas_res, 0) if gas_res else None,
                    }
                    
                    if gas_res:
                        raw_iaq = self._compute_iaq(gas_res, data.humidity)
                        if raw_iaq is not None:
                            self._iaq_buffer.append(raw_iaq)
                        reading["iaq_score"] = round(sum(self._iaq_buffer) / len(self._iaq_buffer), 1) if self._iaq_buffer else None
                    else:
                        reading["iaq_score"] = None
                    
                    reading["gas_baseline"] = round(self._gas_baseline, 0) if self._gas_baseline else None
                    reading["is_mock"] = 0
                        
                    self._last_reading = reading
                    db.insert_env_reading(reading)

            except Exception as e:
                logger.error("BME680 read error: %s", e)

            await asyncio.sleep(config.BME680_POLL_SEC)

    def stop(self):
        """Stop polling."""
        self._running = False
        logger.info("BME680 poller stopped")
