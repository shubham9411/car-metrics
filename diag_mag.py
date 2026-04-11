#!/usr/bin/env python3
"""Deep diagnostic: properly enable MPU6050 bypass for magnetometer access."""
from smbus2 import SMBus
import time

bus = SMBus(1)

print("=== MPU6050 Bypass Diagnostic ===\n")

# Step 1: Read current register state
print("1. Current MPU6050 state:")
who_am_i = bus.read_byte_data(0x68, 0x75)
print(f"   WHO_AM_I (0x75) = 0x{who_am_i:02X} (expect 0x68)")
pwr = bus.read_byte_data(0x68, 0x6B)
print(f"   PWR_MGMT_1 (0x6B) = 0x{pwr:02X}")
user_ctrl = bus.read_byte_data(0x68, 0x6A)
print(f"   USER_CTRL (0x6A) = 0x{user_ctrl:02X} (bit5=I2C_MST_EN)")
int_pin = bus.read_byte_data(0x68, 0x37)
print(f"   INT_PIN_CFG (0x37) = 0x{int_pin:02X} (bit1=BYPASS_EN)")

# Step 2: Wake MPU6050
print("\n2. Waking MPU6050...")
bus.write_byte_data(0x68, 0x6B, 0x00)
time.sleep(0.1)

# Step 3: DISABLE I2C master mode (critical for bypass!)
print("3. Disabling I2C master mode (USER_CTRL bit5 = 0)...")
user_ctrl = bus.read_byte_data(0x68, 0x6A)
bus.write_byte_data(0x68, 0x6A, user_ctrl & ~0x20)  # clear bit 5
time.sleep(0.1)

# Step 4: Enable bypass
print("4. Enabling I2C bypass (INT_PIN_CFG = 0x02)...")
bus.write_byte_data(0x68, 0x37, 0x02)
time.sleep(0.2)

# Verify
user_ctrl = bus.read_byte_data(0x68, 0x6A)
int_pin = bus.read_byte_data(0x68, 0x37)
print(f"   USER_CTRL now = 0x{user_ctrl:02X} (bit5 should be 0)")
print(f"   INT_PIN_CFG now = 0x{int_pin:02X} (bit1 should be 1)")

# Step 5: Full I2C scan
print("\n5. Scanning I2C bus:")
found = []
for addr in range(0x03, 0x78):
    try:
        bus.read_byte(addr)
        found.append(addr)
    except OSError:
        pass

known = {
    0x0C: "AK8963 (MPU9250 mag)", 0x0D: "QMC5883L",
    0x1E: "HMC5883L", 0x2C: "???",
    0x68: "MPU6050", 0x77: "BMP180",
}
for addr in found:
    name = known.get(addr, "unknown")
    print(f"   0x{addr:02X} — {name}")

# Step 6: Probe each non-MPU/BMP device deeply
for addr in found:
    if addr in (0x68, 0x77):
        continue
    print(f"\n6. Deep probe 0x{addr:02X}:")
    try:
        # Read all registers 0x00-0x20
        for reg in range(0x21):
            try:
                val = bus.read_byte_data(addr, reg)
                if val != 0:
                    print(f"   Reg 0x{reg:02X} = 0x{val:02X} ({val})")
            except OSError:
                pass
        # Also check common ID registers
        for reg in [0x75, 0x7E, 0x7F]:
            try:
                val = bus.read_byte_data(addr, reg)
                if val != 0:
                    print(f"   Reg 0x{reg:02X} = 0x{val:02X} ({val})")
            except OSError:
                pass
    except Exception as e:
        print(f"   Error: {e}")

# Step 7: Try reading magnetometer data from MPU6050's internal aux bus
print("\n7. Checking MPU6050 EXT_SENS_DATA (internal aux I2C reads):")
for i in range(6):
    reg = 0x49 + i
    val = bus.read_byte_data(0x68, reg)
    print(f"   EXT_SENS_DATA_{i} (0x{reg:02X}) = 0x{val:02X}")

bus.close()
print("\nDone.")
