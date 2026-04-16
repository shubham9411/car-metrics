"""
Car Metrics — Configuration
All tunables in one place. Override with environment variables.
"""

import os

# ──────────────────────────────────────────────
# Paths
# ──────────────────────────────────────────────
DATA_DIR = os.environ.get("CM_DATA_DIR", "/home/dietpi/car-metrics-data")
DB_PATH = os.path.join(DATA_DIR, "car_metrics.db")
IMAGE_DIR = os.path.join(DATA_DIR, "images")

# ──────────────────────────────────────────────
# IMU (GY-87) — I2C
# ──────────────────────────────────────────────
I2C_BUS = int(os.environ.get("CM_I2C_BUS", "1"))
MPU6050_ADDR = 0x68
BMP180_ADDR = 0x77
# HMC5883L accessed via MPU6050 bypass mode
HMC5883L_ADDR = 0x1E  # may be 0x0D if QMC5883L

IMU_POLL_HZ = int(os.environ.get("CM_IMU_HZ", "10"))
IMU_BATCH_SIZE = 10  # rows buffered before SQLite insert

# ──────────────────────────────────────────────
# BME680 (Environmental Sensor) — I2C
# ──────────────────────────────────────────────
BME680_ADDR = 0x76
BME680_POLL_SEC = int(os.environ.get("CM_BME680_POLL_SEC", "5"))

# ──────────────────────────────────────────────
# GPS (NEO-8M) — UART
# ──────────────────────────────────────────────
GPS_SERIAL_PORT = os.environ.get("CM_GPS_PORT", "/dev/serial0")
GPS_BAUD_RATE = int(os.environ.get("CM_GPS_BAUD", "9600"))
GPS_POLL_HZ = 1  # GPS modules output at 1Hz

# Fallback when GPS has no satellite fix (indoors etc.)
# Set your home/default location here
GPS_FALLBACK_LAT = float(os.environ.get("CM_GPS_FALLBACK_LAT", "0"))
GPS_FALLBACK_LON = float(os.environ.get("CM_GPS_FALLBACK_LON", "0"))
GPS_USE_IP_FALLBACK = os.environ.get("CM_GPS_IP_FALLBACK", "true").lower() == "true"

# ──────────────────────────────────────────────
# Camera (OV5647)
# ──────────────────────────────────────────────
CAMERA_INTERVAL_SEC = int(os.environ.get("CM_CAM_INTERVAL", "5"))
CAMERA_BURST_INTERVAL_SEC = 1
CAMERA_BURST_COUNT = 10
CAMERA_WIDTH = int(os.environ.get("CM_CAM_W", "640"))
CAMERA_HEIGHT = int(os.environ.get("CM_CAM_H", "480"))
CAMERA_JPEG_QUALITY = int(os.environ.get("CM_CAM_QUALITY", "60"))
CAMERA_MAX_LOCAL_IMAGES = int(os.environ.get("CM_CAM_MAX_LOCAL", "1000"))

# ──────────────────────────────────────────────
# OBD2 (ELM327 Bluetooth)
# ──────────────────────────────────────────────
OBD_PORT = os.environ.get("CM_OBD_PORT", "/dev/rfcomm0")
OBD_BAUD = int(os.environ.get("CM_OBD_BAUD", "38400"))
OBD_FAST = os.environ.get("CM_OBD_FAST", "true").lower() == "true"
# PIDs to watch (python-obd command names)
OBD_WATCHED_PIDS = [
    "RPM",
    "SPEED",
    "COOLANT_TEMP",
    "THROTTLE_POS",
    "ENGINE_LOAD",
    "FUEL_LEVEL",
    "INTAKE_TEMP",
    "RUN_TIME",
]

# ──────────────────────────────────────────────
# Crash Detection
# ──────────────────────────────────────────────
CRASH_G_THRESHOLD = float(os.environ.get("CM_CRASH_G", "2.5"))
CRASH_COOLDOWN_SEC = 30  # ignore repeated triggers for N seconds

# ──────────────────────────────────────────────
# Sync (pluggable — disabled by default)
# ──────────────────────────────────────────────
SYNC_ENABLED = os.environ.get("CM_SYNC_ENABLED", "false").lower() == "true"
SYNC_INTERVAL_SEC = int(os.environ.get("CM_SYNC_INTERVAL", "60"))
SUPABASE_URL = os.environ.get("CM_SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("CM_SUPABASE_KEY", "")
SUPABASE_BUCKET = os.environ.get("CM_SUPABASE_BUCKET", "car-images")

# ──────────────────────────────────────────────
# System
# ──────────────────────────────────────────────
LOG_LEVEL = os.environ.get("CM_LOG_LEVEL", "INFO")
HEARTBEAT_INTERVAL_SEC = 60
