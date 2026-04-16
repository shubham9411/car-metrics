"""
Car Metrics — GY-87 IMU Poller
Reads MPU6050 (accel/gyro), BMP180 (pressure/temp), HMC5883L (magnetometer)
via I2C using smbus2. Polls at 10Hz, batch-inserts to SQLite.
"""

import asyncio
import logging
import math
import os
import random
import struct
import time

import config

logger = logging.getLogger("pollers.imu")

# ─── MPU6050 Registers ───────────────────────────
_MPU_PWR_MGMT_1 = 0x6B
_MPU_ACCEL_XOUT_H = 0x3B  # 6 bytes: ax, ay, az (high+low each)
_MPU_GYRO_XOUT_H = 0x43   # 6 bytes: gx, gy, gz
_MPU_TEMP_OUT_H = 0x41     # 2 bytes
_MPU_INT_PIN_CFG = 0x37    # enable bypass for magnetometer
_MPU_ACCEL_SCALE = 16384.0  # ±2g default
_MPU_GYRO_SCALE = 131.0     # ±250°/s default

# ─── BMP180 Registers ────────────────────────────
_BMP_CTRL = 0xF4
_BMP_DATA = 0xF6
_BMP_TEMP_CMD = 0x2E
_BMP_PRES_CMD = 0x34  # OSS=0
_BMP_CAL_START = 0xAA  # 22 bytes of calibration data

# ─── HMC5883L Registers ──────────────────────────
_HMC_CFG_A = 0x00
_HMC_CFG_B = 0x01
_HMC_MODE = 0x02
_HMC_DATA = 0x03  # 6 bytes: x, z, y (note: z before y!)
_HMC_SCALE = 1090.0  # Gain = 1090 LSb/Gauss for ±1.3Ga
_HMC_ADDR = 0x1E

# ─── QMC5883L Registers (common on GY-87 clones) ─
_QMC_ADDR = 0x0D
_QMC_CTRL1 = 0x09
_QMC_CTRL2 = 0x0A
_QMC_SET_RESET = 0x0B
_QMC_DATA = 0x00  # 6 bytes: x_l, x_h, y_l, y_h, z_l, z_h
_QMC_SCALE = 12000.0  # LSb/Gauss at 8G range


class IMUPoller:
    """Async poller for the GY-87 10DOF sensor module."""

    def __init__(self, gps_poller=None):
        self._bus = None
        self._bmp_cal = None
        self._batch = []
        self._running = False
        self._mag_type = None  # 'hmc', 'qmc', or None
        self._mag_addr = None
        self.gps = gps_poller
        self.last_reading = {} 
        
        # State for IMU simulation
        self._last_sim_speed = 0.0
        self._last_sim_course = 0.0

    def _init_hardware(self):
        """Initialize I2C bus and configure sensors."""
        sim_file = os.path.join(config.DATA_DIR, ".simulate_data")
        if os.path.exists(sim_file):
            logger.info("IMU hardware bypassed for simulation")
            return

        from smbus2 import SMBus

        self._bus = SMBus(config.I2C_BUS)

        # ── MPU6050: wake up ──
        self._bus.write_byte_data(config.MPU6050_ADDR, _MPU_PWR_MGMT_1, 0x00)
        # Enable I2C bypass so we can talk to magnetometer directly
        # Set both BYPASS_EN and LATCH_INT_EN bits
        self._bus.write_byte_data(config.MPU6050_ADDR, _MPU_INT_PIN_CFG, 0x22)
        import time as _t; _t.sleep(0.1)  # let bypass settle
        logger.info("MPU6050 initialized at 0x%02X (bypass enabled)", config.MPU6050_ADDR)

        # ── BMP180: read calibration data ──
        self._bmp_cal = self._read_bmp_calibration()
        logger.info("BMP180 initialized at 0x%02X", config.BMP180_ADDR)

        # ── Magnetometer: detect QMC5883L or HMC5883L ──
        self._init_magnetometer()

    def _init_magnetometer(self):
        """Auto-detect and configure QMC5883L or HMC5883L."""
        # Try QMC5883L first (0x0D) — most common on GY-87 clones
        try:
            self._bus.read_byte_data(_QMC_ADDR, 0x0D)  # chip ID register
            # Configure: continuous mode, 200Hz, 8G range, 512 oversampling
            self._bus.write_byte_data(_QMC_ADDR, _QMC_SET_RESET, 0x01)
            self._bus.write_byte_data(_QMC_ADDR, _QMC_CTRL2, 0x40)  # soft reset
            import time as _t; _t.sleep(0.05)
            self._bus.write_byte_data(_QMC_ADDR, _QMC_SET_RESET, 0x01)
            # CTRL1: mode=continuous(01), ODR=200Hz(11), range=8G(01), OSR=512(00)
            self._bus.write_byte_data(_QMC_ADDR, _QMC_CTRL1, 0x0D)
            self._mag_type = 'qmc'
            self._mag_addr = _QMC_ADDR
            logger.info("QMC5883L detected at 0x%02X", _QMC_ADDR)
            return
        except OSError:
            pass

        # Try HMC5883L (0x1E)
        try:
            self._bus.write_byte_data(_HMC_ADDR, _HMC_CFG_A, 0x70)  # 8 avg, 15Hz
            self._bus.write_byte_data(_HMC_ADDR, _HMC_CFG_B, 0x20)  # gain 1090
            self._bus.write_byte_data(_HMC_ADDR, _HMC_MODE, 0x00)   # continuous
            self._mag_type = 'hmc'
            self._mag_addr = _HMC_ADDR
            logger.info("HMC5883L detected at 0x%02X", _HMC_ADDR)
            return
        except OSError:
            pass

        logger.warning("No magnetometer found at 0x0D or 0x1E — compass disabled")

    def _read_word(self, addr: int, reg: int) -> int:
        """Read a signed 16-bit word (big-endian) from I2C."""
        high = self._bus.read_byte_data(addr, reg)
        low = self._bus.read_byte_data(addr, reg + 1)
        val = (high << 8) | low
        return val - 65536 if val >= 32768 else val

    def _read_accel_gyro(self) -> dict:
        """Read accelerometer and gyroscope from MPU6050."""
        # Read 14 bytes: accel(6) + temp(2) + gyro(6)
        data = self._bus.read_i2c_block_data(config.MPU6050_ADDR, _MPU_ACCEL_XOUT_H, 14)
        ax = self._to_signed(data[0], data[1]) / _MPU_ACCEL_SCALE
        ay = self._to_signed(data[2], data[3]) / _MPU_ACCEL_SCALE
        az = self._to_signed(data[4], data[5]) / _MPU_ACCEL_SCALE
        gx = self._to_signed(data[8], data[9]) / _MPU_GYRO_SCALE
        gy = self._to_signed(data[10], data[11]) / _MPU_GYRO_SCALE
        gz = self._to_signed(data[12], data[13]) / _MPU_GYRO_SCALE
        return {"ax": ax, "ay": ay, "az": az, "gx": gx, "gy": gy, "gz": gz}

    @staticmethod
    def _to_signed(high: int, low: int) -> int:
        val = (high << 8) | low
        return val - 65536 if val >= 32768 else val

    def _read_magnetometer(self) -> dict:
        """Read magnetometer — auto-selects QMC or HMC protocol."""
        if self._mag_type is None:
            return {"mx": None, "my": None, "mz": None}

        try:
            if self._mag_type == 'qmc':
                # QMC5883L: X_L, X_H, Y_L, Y_H, Z_L, Z_H (little-endian!)
                data = self._bus.read_i2c_block_data(_QMC_ADDR, _QMC_DATA, 6)
                mx = self._to_signed(data[1], data[0]) / _QMC_SCALE
                my = self._to_signed(data[3], data[2]) / _QMC_SCALE
                mz = self._to_signed(data[5], data[4]) / _QMC_SCALE
            else:
                # HMC5883L: X_H, X_L, Z_H, Z_L, Y_H, Y_L (big-endian)
                data = self._bus.read_i2c_block_data(_HMC_ADDR, _HMC_DATA, 6)
                mx = self._to_signed(data[0], data[1]) / _HMC_SCALE
                mz = self._to_signed(data[2], data[3]) / _HMC_SCALE
                my = self._to_signed(data[4], data[5]) / _HMC_SCALE
            return {"mx": mx, "my": my, "mz": mz}
        except OSError:
            return {"mx": None, "my": None, "mz": None}

    # ─── BMP180 ───────────────────────────────────

    def _read_bmp_calibration(self) -> dict:
        """Read BMP180 factory calibration coefficients."""
        raw = self._bus.read_i2c_block_data(config.BMP180_ADDR, _BMP_CAL_START, 22)
        cal = struct.unpack(">hhhHHHhhhhh", bytes(raw))
        return {
            "AC1": cal[0], "AC2": cal[1], "AC3": cal[2],
            "AC4": cal[3], "AC5": cal[4], "AC6": cal[5],
            "B1": cal[6], "B2": cal[7],
            "MB": cal[8], "MC": cal[9], "MD": cal[10],
        }

    def _read_bmp_temp_pressure(self) -> dict:
        """Read temperature and pressure from BMP180 (blocking, ~10ms)."""
        bus = self._bus
        addr = config.BMP180_ADDR
        cal = self._bmp_cal
        oss = 0  # oversampling setting

        # Read raw temperature
        bus.write_byte_data(addr, _BMP_CTRL, _BMP_TEMP_CMD)
        time.sleep(0.005)
        ut = self._read_word(addr, _BMP_DATA)

        # Read raw pressure (OSS=0)
        bus.write_byte_data(addr, _BMP_CTRL, _BMP_PRES_CMD + (oss << 6))
        time.sleep(0.005)
        msb = bus.read_byte_data(addr, _BMP_DATA)
        lsb = bus.read_byte_data(addr, _BMP_DATA + 1)
        xlsb = bus.read_byte_data(addr, _BMP_DATA + 2)
        up = ((msb << 16) + (lsb << 8) + xlsb) >> (8 - oss)

        # === Temperature compensation (integer math per datasheet) ===
        x1 = ((ut - cal["AC6"]) * cal["AC5"]) >> 15
        denom = x1 + cal["MD"]
        if denom == 0:
            return {"pressure": None, "temp_c": None, "altitude": None}
        x2 = (cal["MC"] << 11) // denom
        b5 = x1 + x2
        temp_c = ((b5 + 8) >> 4) / 10.0

        # === Pressure compensation (integer math per datasheet) ===
        b6 = b5 - 4000
        x1 = (cal["B2"] * ((b6 * b6) >> 12)) >> 11
        x2 = (cal["AC2"] * b6) >> 11
        x3 = x1 + x2
        b3 = (((cal["AC1"] * 4 + x3) << oss) + 2) // 4
        x1 = (cal["AC3"] * b6) >> 13
        x2 = (cal["B1"] * ((b6 * b6) >> 12)) >> 16
        x3 = ((x1 + x2) + 2) >> 2
        b4 = (cal["AC4"] * (x3 + 32768)) >> 15
        if b4 == 0:
            return {"pressure": None, "temp_c": temp_c, "altitude": None}
        b7 = (up - b3) * (50000 >> oss)
        if b7 < 0x80000000:
            p = (b7 * 2) // b4
        else:
            p = (b7 // b4) * 2
        x1 = (p >> 8) * (p >> 8)
        x1 = (x1 * 3038) >> 16
        x2 = (-7357 * p) >> 16
        pressure = p + ((x1 + x2 + 3791) >> 4)

        # Altitude from pressure (with safety check)
        if pressure > 0:
            ratio = pressure / 101325.0
            altitude = 44330.0 * (1.0 - (ratio ** (1.0 / 5.255)))
        else:
            altitude = None

        return {"pressure": float(pressure), "temp_c": temp_c, "altitude": altitude}

    # ─── Main poll loop ───────────────────────────

    def read_once(self) -> dict:
        """Read all sensors once. Returns combined dict."""
        sim_file = os.path.join(config.DATA_DIR, ".simulate_data")
        if os.path.exists(sim_file) and self.gps:
            # ... (simulation code) ...
            t = time.time()
            fix = self.gps.last_fix
            speed = (fix["speed_knots"] * 1.852) if (fix and fix.get("speed_knots") is not None) else 0.0
            course = fix["course"] if (fix and fix.get("course") is not None) else 0.0
            alt = fix["alt"] if (fix and fix.get("alt") is not None) else 15.0
            
            accel_val = (speed - self._last_sim_speed) * 0.5
            ax = -accel_val + random.uniform(-0.02, 0.02)
            
            course_delta = course - self._last_sim_course
            if course_delta > 180: course_delta -= 360
            if course_delta < -180: course_delta += 360
            ay = (course_delta * 0.1) * (speed / 50.0) + random.uniform(-0.02, 0.02)
            
            az = 1.0 + random.uniform(-0.05, 0.05)
            if random.random() < 0.02: 
                az += random.uniform(0.5, 1.2)
            
            self._last_sim_speed = speed
            self._last_sim_course = course
            
            return {
                "ts": t,
                "ax": ax, "ay": ay, "az": az,
                "gx": random.uniform(-1, 1), "gy": random.uniform(-1, 1), "gz": random.uniform(-1, 1),
                "mx": math.cos(math.radians(course)), "my": math.sin(math.radians(course)), "mz": 0.0,
                "pressure": 101325.0 - (alt * 12.0),
                "temp_c": 22.0 + random.uniform(-0.5, 0.5),
                "altitude": alt
            }

        # Real hardware path
        if self._bus is None:
            # Re-attempt hardware init if we were previously in simulation
            self._init_hardware()
            if self._bus is None:
                raise RuntimeError("IMU hardware not initialized and simulation disabled")

        reading = {"ts": time.time()}
        reading.update(self._read_accel_gyro())
        reading.update(self._read_magnetometer())
        reading.update(self._read_bmp_temp_pressure())
        return reading

    async def run(self, on_reading=None, is_car_on_func=None):
        """
        Async poll loop at configured Hz.
        on_reading: optional callback(reading_dict) for crash detection etc.
        is_car_on_func: callback returning bool to guard DB insertion.
        """
        self._init_hardware()
        self._running = True
        interval = 1.0 / config.IMU_POLL_HZ

        logger.info("IMU poller started at %dHz", config.IMU_POLL_HZ)

        from storage import db

        last_heartbeat = 0
        while self._running:
            try:
                t0 = time.monotonic()
                reading = self.read_once()
                self.last_reading = reading
                
                # Callback for crash detection (always runs for safety/triggering)
                if on_reading:
                    on_reading(reading)

                # Car on logic
                sim_file = os.path.join(config.DATA_DIR, ".simulate_data")
                car_on = is_car_on_func() if is_car_on_func else True
                
                # Log to DB if:
                # 1. Car is on (high-frequency 10Hz)
                # 2. Simulation is on (high-frequency 10Hz)
                # 3. Heartbeat interval: once every 1 second when idle
                is_sim = os.path.exists(sim_file)
                now = time.time()
                is_heartbeat = (now - last_heartbeat >= 1.0)

                if car_on or is_sim or is_heartbeat:
                    self._batch.append(reading)
                    if is_heartbeat:
                        last_heartbeat = now
                    
                    if len(self._batch) >= config.IMU_BATCH_SIZE or is_heartbeat:
                        db.insert_imu_batch(self._batch)
                        self._batch.clear()
                else:
                    self._batch.clear()

                # Sleep remaining interval
                elapsed = time.monotonic() - t0
                sleep_time = max(0, interval - elapsed)
                await asyncio.sleep(sleep_time)

            except Exception as e:
                logger.error("IMU read error: %s", e)
                await asyncio.sleep(2)

    def stop(self):
        """Stop polling and flush remaining batch."""
        self._running = False
        if self._batch:
            from storage import db
            db.insert_imu_batch(self._batch)
            self._batch.clear()
        if self._bus:
            self._bus.close()
        logger.info("IMU poller stopped")
