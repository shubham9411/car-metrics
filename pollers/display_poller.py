"""
Car Metrics — ST7789V 2" LCD Display Poller
Graphical rotating widgets: Cluster, G-Force, and Environment.
"""

import asyncio
import logging
import time
import math

import config
from storage import db

logger = logging.getLogger("pollers.display")

# ─── Display Settings ────────────────────────────────
# landscape view: 320 wide, 240 high
WIDTH  = 320
HEIGHT = 240

# ─── Colors ──────────────────────────────────────────
BG       = "#05070a"
CYAN     = "#38bdf8"
PURPLE   = "#c084fc"
GREEN    = "#34d399"
YELLOW   = "#fbbf24"
RED      = "#f43f5e"
MID      = "#1e293b"
DIM      = "#0f172a"
TEXT_DIM = "#64748b"
WHITE    = "#f8fafc"


def _try_import():
    try:
        from luma.core.interface.serial import spi
        from luma.lcd.device import st7789
        import RPi.GPIO as GPIO
        from PIL import Image, ImageDraw, ImageFont

        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        GPIO.setup(config.DISPLAY_BL_PIN, GPIO.OUT)
        GPIO.output(config.DISPLAY_BL_PIN, GPIO.HIGH)

        serial = spi(
            port=0,
            device=0,
            gpio_DC=config.DISPLAY_DC_PIN,
            gpio_RST=config.DISPLAY_RST_PIN,
            bus_speed_hz=24_000_000,
        )
        
        # Most 2" 320x240 modules use 0 offset, but if noise persists, 
        # we might need to experiment with x_offset/y_offset.
        device = st7789(
            serial,
            width=WIDTH,
            height=HEIGHT,
            rotate=0,     # We initialized as 320x240, so no rotation needed
        )
        logger.info("ST7789V display initialised (%dx%d)", WIDTH, HEIGHT)
        return device, Image, ImageDraw, ImageFont
    except Exception as e:
        logger.warning("Display init failed: %s", e)
        return None, None, None, None


# ─── Helpers ─────────────────────────────────────────

_FONTS = {}
def get_font(ImageFont, size, bold=False):
    key = (size, bold)
    if key not in _FONTS:
        try:
            # Try to use a better font if available, otherwise default
            _FONTS[key] = ImageFont.load_default(size=size)
        except Exception:
            _FONTS[key] = ImageFont.load_default()
    return _FONTS[key]


def _text_centered(draw, ImageFont, cx, cy, text, size, fill, bold=False):
    fnt = get_font(ImageFont, size, bold)
    try:
        bbox = fnt.getbbox(text)
        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]
    except Exception:
        w = len(text) * (size // 2)
        h = size
    draw.text((cx - w//2, cy - h//2), text, fill=fill, font=fnt)


def _draw_header(draw, ImageFont, title, color):
    draw.rectangle([(0, 0), (WIDTH, 35)], fill=DIM)
    draw.line([(0, 35), (WIDTH, 35)], fill=color, width=2)
    _text_centered(draw, ImageFont, WIDTH//2, 17, title, 16, WHITE, True)


# ─── Renderers ───────────────────────────────────────

def _render_summary_page(draw, ImageFont, data):
    """Page 1: Main Cluster (Speed, AQI, Time)."""
    _draw_header(draw, ImageFont, "S-TYPE TELEMETRY", CYAN)

    gps = data.get("gps") or {}
    env = data.get("env") or {}

    # Large Speed (KM/H)
    speed_knots = gps.get("speed_knots")
    s_val = (speed_knots * 1.852) if speed_knots else 0
    spd_str = f"{int(s_val)}" if speed_knots else "--"
    _text_centered(draw, ImageFont, WIDTH//2, 100, spd_str, 80, WHITE, True)
    _text_centered(draw, ImageFont, WIDTH//2, 150, "KM/H", 14, CYAN)

    # Left: AQI
    iaq = env.get("iaq_score")
    i_str = str(int(iaq)) if iaq is not None else "--"
    i_col = GREEN if (iaq and iaq <= 50) else (YELLOW if (iaq and iaq <= 150) else RED)
    _text_centered(draw, ImageFont, 60, 100, i_str, 30, i_col, True)
    _text_centered(draw, ImageFont, 60, 130, "IAQ", 12, TEXT_DIM)

    # Right: Satellites
    sats = gps.get("satellites", 0)
    _text_centered(draw, ImageFont, 260, 100, str(sats), 30, YELLOW if sats > 0 else RED, True)
    _text_centered(draw, ImageFont, 260, 130, "SATS", 12, TEXT_DIM)

    # Bottom Details
    temp = env.get("temperature")
    t_str = f"{temp:.1f}°C" if temp else "--°C"
    _text_centered(draw, ImageFont, 60, 190, t_str, 18, CYAN)
    
    _text_centered(draw, ImageFont, WIDTH//2, 190, time.strftime("%H:%M:%S"), 22, WHITE)
    
    alt = gps.get("alt")
    a_str = f"{int(alt)}m" if alt else "--m"
    _text_centered(draw, ImageFont, 260, 190, a_str, 18, PURPLE)


def _render_imu_page(draw, ImageFont, data):
    """Page 2: IMU / G-Force Visualizer."""
    _draw_header(draw, ImageFont, "G-FORCE ANALYTICS", PURPLE)
    
    imu = data.get("imu") or {}
    # Draw a 2D G-Force crosshair
    cx, cy = WIDTH//2, HEIGHT//2 + 10
    size = 70
    
    # Background Grid
    draw.ellipse([cx-size, cy-size, cx+size, cy+size], outline=DIM, width=1)
    draw.ellipse([cx-size//2, cy-size//2, cx+size//2, cy+size//2], outline=DIM, width=1)
    draw.line([cx-size-10, cy, cx+size+10, cy], fill=DIM, width=1)
    draw.line([cx, cy-size-10, cx, cy+size+10], fill=DIM, width=1)

    # Current G-point
    # Assuming ax, ay are in Gs
    ax = imu.get("ax", 0)
    ay = imu.get("ay", 0)
    
    # Scale: 1G = size pixels
    mx = cx + int(ay * size)  # Side Gs
    my = cy + int(ax * size)  # Longitudinal Gs (approx)
    
    # Keep in bounds
    mx = max(cx-size, min(cx+size, mx))
    my = max(cy-size, min(cy+size, my))
    
    # Draw Dot
    draw.ellipse([mx-5, my-5, mx+5, my+5], fill=PURPLE)
    
    # Labels
    _text_centered(draw, ImageFont, cx, cy-size-20, "FORWARD", 10, TEXT_DIM)
    _text_centered(draw, ImageFont, cx-size-30, cy, "LEFT", 10, TEXT_DIM)
    _text_centered(draw, ImageFont, cx+size+30, cy, "RIGHT", 10, TEXT_DIM)
    
    g_total = math.sqrt(ax**2 + ay**2)
    _text_centered(draw, ImageFont, WIDTH//2, HEIGHT-20, f"PEAK: {g_total:.2f}G", 14, WHITE)


def _render_obd_page(draw, ImageFont, data):
    """Page 3: OBD / Engine (RPM + Coolant)."""
    _draw_header(draw, ImageFont, "ENGINE DIAGNOSTICS", YELLOW)
    
    obd = data.get("obd") or {}
    rpm = obd.get("RPM", {}).get("value")
    
    if rpm is None:
        _text_centered(draw, ImageFont, WIDTH//2, HEIGHT//2, "WAITING FOR OBD...", 20, RED)
        return

    # RPM Gauge Bar
    y_bar = 90
    bar_w = 260
    draw.rectangle([30, y_bar, 30+bar_w, y_bar+30], outline=MID, width=2)
    
    pct = min(1, rpm / 7000)
    col = GREEN if pct < 0.7 else (YELLOW if pct < 0.9 else RED)
    draw.rectangle([32, y_bar+2, 32 + int((bar_w-4)*pct), y_bar+28], fill=col)
    
    _text_centered(draw, ImageFont, WIDTH//2, y_bar+15, f"{int(rpm)} RPM", 18, WHITE, True)
    
    # Coolant & Voltage
    coolant = obd.get("COOLANT_TEMP", {}).get("value")
    c_str = f"{int(coolant)}°C" if coolant else "--"
    _text_centered(draw, ImageFont, 80, 160, c_str, 24, YELLOW, True)
    _text_centered(draw, ImageFont, 80, 185, "COOLANT", 12, TEXT_DIM)
    
    volt = obd.get("CONTROL_MODULE_VOLTAGE", {}).get("value")
    v_str = f"{volt:.1f}V" if volt else "--"
    _text_centered(draw, ImageFont, 240, 160, v_str, 24, CYAN, True)
    _text_centered(draw, ImageFont, 240, 185, "VOLTAGE", 12, TEXT_DIM)


PAGES = [_render_summary_page, _render_imu_page, _render_obd_page]

# ─── Poller ──────────────────────────────────────────

class DisplayPoller:
    """Async poller for ST7789V LCD with rotating pages."""

    def __init__(self, gps_poller=None, obd_poller=None, bme680_poller=None, imu_poller=None):
        self._running = False
        self._gps = gps_poller
        self._obd = obd_poller
        self._bme = bme680_poller
        self._imu = imu_poller
        self._page = 0
        self._device = None
        self._Image = None
        self._ImageDraw = None
        self._ImageFont = None

    def _collect_data(self) -> dict:
        data = {}
        if self._gps and self._gps.last_fix:
            data["gps"] = self._gps.last_fix
        if self._obd:
            data["obd"] = self._obd.latest
        if self._bme and self._bme.last_reading:
            data["env"] = self._bme.last_reading
        if self._imu:
            # We need the last IMU reading from memory
            data["imu"] = self._imu.last_reading if hasattr(self._imu, "last_reading") else {}
        return data

    async def run(self):
        self._running = True
        self._device, self._Image, self._ImageDraw, self._ImageFont = await asyncio.get_event_loop().run_in_executor(
            None, _try_import
        )

        if not self._device:
            while self._running:
                await asyncio.sleep(60)
            return

        logger.info("Display Poller Started — %d Pages, %ds Refresh", len(PAGES), config.DISPLAY_PAGE_SEC)

        while self._running:
            try:
                data = self._collect_data()
                
                # Dynamically filter pages based on data availability
                active_pages = [PAGES[0]] # Always show summary
                
                # Check for IMU data
                if data.get("imu") and (data["imu"].get("ax") is not None):
                    active_pages.append(PAGES[1])
                
                # Check for OBD data
                obd_data = data.get("obd", {})
                if obd_data and "RPM" in obd_data:
                    active_pages.append(PAGES[2])
                
                # Render current page from active list
                render_fn = active_pages[self._page % len(active_pages)]
                
                # Create canvas
                img = self._Image.new("RGB", (WIDTH, HEIGHT), BG)
                draw = self._ImageDraw.Draw(img)
                
                render_fn(draw, self._ImageFont, data)
                self._device.display(img)
                
                self._page += 1

            except Exception as e:
                logger.error("Display render error: %s", e)

            await asyncio.sleep(config.DISPLAY_PAGE_SEC)

    def stop(self):
        self._running = False
        try:
            if self._device:
                self._device.cleanup()
        except Exception:
            pass
