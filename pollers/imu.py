"""
Car Metrics — GY-87 IMU Poller
Reads MPU6050 (accel/gyro), BMP180 (pressure/temp), HMC5883L (magnetometer)
via I2C using smbus2. Polls at 10Hz, batch-inserts to SQLite.
"""

import asyncio
import logging
import math
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


class IMUPoller:
    """Async poller for the GY-87 10DOF sensor module."""

    def __init__(self):
        self._bus = None
        self._bmp_cal = None
        self._batch = []
        self._running = False

    def _init_hardware(self):
        """Initialize I2C bus and configure sensors."""
        from smbus2 import SMBus

        self._bus = SMBus(config.I2C_BUS)

        # ── MPU6050: wake up ──
        self._bus.write_byte_data(config.MPU6050_ADDR, _MPU_PWR_MGMT_1, 0x00)
        # Enable I2C bypass so we can talk to HMC5883L directly
        self._bus.write_byte_data(config.MPU6050_ADDR, _MPU_INT_PIN_CFG, 0x02)
        logger.info("MPU6050 initialized at 0x%02X", config.MPU6050_ADDR)

        # ── BMP180: read calibration data ──
        self._bmp_cal = self._read_bmp_calibration()
        logger.info("BMP180 initialized at 0x%02X", config.BMP180_ADDR)

        # ── HMC5883L: continuous measurement mode ──
        try:
            self._bus.write_byte_data(config.HMC5883L_ADDR, _HMC_CFG_A, 0x70)  # 8 avg, 15Hz, normal
            self._bus.write_byte_data(config.HMC5883L_ADDR, _HMC_CFG_B, 0x20)  # gain 1090
            self._bus.write_byte_data(config.HMC5883L_ADDR, _HMC_MODE, 0x00)   # continuous
            logger.info("HMC5883L initialized at 0x%02X", config.HMC5883L_ADDR)
        except OSError:
            logger.warning(
                "HMC5883L not found at 0x%02X — may be QMC5883L (0x0D). "
                "Magnetometer disabled.", config.HMC5883L_ADDR
            )

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
        """Read magnetometer from HMC5883L."""
        try:
            data = self._bus.read_i2c_block_data(config.HMC5883L_ADDR, _HMC_DATA, 6)
            # HMC5883L outputs: X_H, X_L, Z_H, Z_L, Y_H, Y_L
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

        # Read raw temperature
        bus.write_byte_data(addr, _BMP_CTRL, _BMP_TEMP_CMD)
        time.sleep(0.005)
        ut = self._read_word(addr, _BMP_DATA)

        # Read raw pressure (OSS=0)
        bus.write_byte_data(addr, _BMP_CTRL, _BMP_PRES_CMD)
        time.sleep(0.005)
        up = self._read_word(addr, _BMP_DATA)

        # Compensate temperature
        x1 = (ut - cal["AC6"]) * cal["AC5"] / 32768
        x2 = cal["MC"] * 2048 / (x1 + cal["MD"])
        b5 = x1 + x2
        temp_c = (b5 + 8) / 160

        # Compensate pressure
        b6 = b5 - 4000
        x1 = (cal["B2"] * (b6 * b6 / 4096)) / 2048
        x2 = cal["AC2"] * b6 / 2048
        x3 = x1 + x2
        b3 = (((cal["AC1"] * 4 + int(x3)) << 0) + 2) / 4
        x1 = cal["AC3"] * b6 / 8192
        x2 = (cal["B1"] * (b6 * b6 / 4096)) / 65536
        x3 = ((x1 + x2) + 2) / 4
        b4 = cal["AC4"] * (x3 + 32768) / 32768
        b7 = (up - b3) * 50000
        if b7 < 0x80000000:
            p = (b7 * 2) / b4
        else:
            p = (b7 / b4) * 2
        x1 = (p / 256) ** 2
        x1 = (x1 * 3038) / 65536
        x2 = (-7357 * p) / 65536
        pressure = p + (x1 + x2 + 3791) / 16  # Pa

        # Simple altitude from pressure (sea level = 101325 Pa)
        altitude = 44330 * (1 - (pressure / 101325) ** (1 / 5.255))

        return {"pressure": pressure, "temp_c": temp_c, "altitude": altitude}

    # ─── Main poll loop ───────────────────────────

    def read_once(self) -> dict:
        """Read all sensors once. Returns combined dict."""
        reading = {"ts": time.time()}
        reading.update(self._read_accel_gyro())
        reading.update(self._read_magnetometer())
        reading.update(self._read_bmp_temp_pressure())
        return reading

    async def run(self, on_reading=None):
        """
        Async poll loop at configured Hz.
        on_reading: optional callback(reading_dict) for crash detection etc.
        """
        self._init_hardware()
        self._running = True
        interval = 1.0 / config.IMU_POLL_HZ

        logger.info("IMU poller started at %dHz", config.IMU_POLL_HZ)

        from storage import db

        while self._running:
            try:
                t0 = time.monotonic()
                reading = self.read_once()
                self._batch.append(reading)

                # Callback for crash detection etc.
                if on_reading:
                    on_reading(reading)

                # Flush batch to SQLite
                if len(self._batch) >= config.IMU_BATCH_SIZE:
                    db.insert_imu_batch(self._batch)
                    self._batch.clear()

                # Sleep remaining interval
                elapsed = time.monotonic() - t0
                sleep_time = max(0, interval - elapsed)
                await asyncio.sleep(sleep_time)

            except Exception as e:
                logger.error("IMU read error: %s", e)
                await asyncio.sleep(1)

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
