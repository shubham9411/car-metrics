import os
import sys
from picamera2 import Picamera2
import time

custom_tuning = "/home/dietpi/car-metrics-data/ov5647_custom.json"
picam = Picamera2(tuning=custom_tuning)

cam_config = picam.create_still_configuration(
    main={"size": (640, 480), "format": "RGB888"},
)
picam.configure(cam_config)

picam.set_controls({
    "AwbEnable": True,
    "AwbMode": 1,  # Try different values, 0=Auto, 1=Incandescent, etc. Maybe greyworld?
})

picam.start()
time.sleep(2)  # settle

# Test Greyworld
try:
    from libcamera import controls
    picam.set_controls({"AwbMode": controls.AwbModeEnum.Greyworld})
    time.sleep(1)
except Exception as e:
    print("Could not set greyworld:", e)

# Also test disabling AWB and setting color gains manually
try:
    picam.set_controls({"AwbEnable": False, "ColourGains": (1.5, 1.5)})
    time.sleep(1)
    picam.capture_file("/home/dietpi/car-metrics-data/images/test_gains.jpg")
except:
    pass

picam.stop()
