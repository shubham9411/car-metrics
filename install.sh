#!/bin/bash
# ──────────────────────────────────────────────────
# Car Metrics — DietPi Setup Script
# Run once on a fresh Pi Zero WH with DietPi
# ──────────────────────────────────────────────────
set -e

echo "=== Car Metrics — DietPi Setup ==="

# Enable I2C (for GY-87)
echo "→ Enabling I2C..."
if ! grep -q "^dtparam=i2c_arm=on" /boot/config.txt 2>/dev/null && \
   ! grep -q "^dtparam=i2c_arm=on" /boot/dietpiEnv.txt 2>/dev/null; then
    echo "dtparam=i2c_arm=on" | sudo tee -a /boot/config.txt
    echo "  I2C enabled (reboot required)"
else
    echo "  I2C already enabled"
fi

# Disable serial console (GPS uses UART)
echo "→ Disabling serial console for GPS..."
sudo systemctl disable serial-getty@ttyS0.service 2>/dev/null || true
sudo systemctl stop serial-getty@ttyS0.service 2>/dev/null || true
# Remove console=serial0 from cmdline if present
if grep -q "console=serial0" /boot/cmdline.txt 2>/dev/null; then
    sudo sed -i 's/console=serial0,[0-9]* //' /boot/cmdline.txt
    echo "  Serial console disabled (reboot required)"
else
    echo "  Serial console already disabled"
fi

# Enable UART
if ! grep -q "^enable_uart=1" /boot/config.txt 2>/dev/null; then
    echo "enable_uart=1" | sudo tee -a /boot/config.txt
    echo "  UART enabled"
fi

# Enable camera
if ! grep -q "^start_x=1" /boot/config.txt 2>/dev/null; then
    echo "start_x=1" | sudo tee -a /boot/config.txt
    echo "gpu_mem=128" | sudo tee -a /boot/config.txt
    echo "  Camera enabled"
fi

# Install system dependencies
echo "→ Installing system packages..."
sudo apt-get update -qq
sudo apt-get install -y --no-install-recommends \
    python3-pip \
    python3-picamera2 \
    python3-libcamera \
    i2c-tools \
    bluetooth \
    bluez \
    python3-smbus

# Install Python dependencies
echo "→ Installing Python packages..."
pip3 install --break-system-packages -r requirements.txt 2>/dev/null || \
pip3 install -r requirements.txt

# Setup Bluetooth for OBD2 ELM327
echo "→ Setting up Bluetooth serial..."
# User needs to pair their ELM327 first:
# sudo bluetoothctl
# > scan on
# > pair XX:XX:XX:XX:XX:XX
# > trust XX:XX:XX:XX:XX:XX
# > quit
# Then bind: sudo rfcomm bind 0 XX:XX:XX:XX:XX:XX

# Add user to required groups
sudo usermod -aG i2c,dialout,video,bluetooth dietpi 2>/dev/null || true

# Create data directory
echo "→ Creating data directory..."
mkdir -p /home/dietpi/car-metrics-data/images

# Install systemd service
echo "→ Installing systemd service..."
sudo cp car-metrics.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable car-metrics

echo ""
echo "=== Setup Complete ==="
echo ""
echo "Next steps:"
echo "  1. Reboot: sudo reboot"
echo "  2. Verify I2C: sudo i2cdetect -y 1"
echo "     - Should see 0x68 (MPU6050) and 0x77 (BMP180)"
echo "  3. Pair Bluetooth ELM327:"
echo "     sudo bluetoothctl → scan on → pair XX:XX → trust XX:XX → quit"
echo "     sudo rfcomm bind 0 XX:XX:XX:XX:XX:XX"
echo "  4. Start: sudo systemctl start car-metrics"
echo "  5. Logs:  journalctl -u car-metrics -f"
