#!/bin/bash
# OBD2 Bluetooth Healing Script
# This script ensures the ELM327 Bluetooth device is bound to /dev/rfcomm0
# Used to maintain OBD connectivity if the system drops the serial mapping.

MAC_ADDR="AA:BB:CC:11:22:33"
DEVICE="/dev/rfcomm0"

echo "🩺 Starting OBD Self-Healing for $MAC_ADDR..."

while true; do
    if [ ! -e $DEVICE ]; then
        echo "⚠️ $DEVICE not found. Attempting to bind $MAC_ADDR..."
        
        # Try to release first just in case of stale lock
        sudo rfcomm release 0 2>/dev/null
        
        # Bind the device
        sudo rfcomm bind 0 $MAC_ADDR
        
        if [ $? -eq 0 ]; then
            echo "✅ Successfully bound $MAC_ADDR to $DEVICE"
            sudo chmod 666 $DEVICE
        else
            echo "❌ Failed to bind $MAC_ADDR. Is Bluetooth on?"
        fi
    else
        # Device exists, check if it's responsive (optional)
        # We'll just trust rfcomm for now and let the python poller handle data errors
        sleep 30
    fi
    
    sleep 10
done
