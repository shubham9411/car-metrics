"""
Car Metrics — ST7789V 2" LCD Display Poller
Premium Digital Cockpit UI with Inter Typography.
"""

import asyncio
import logging
import time
import math
import os

import config
from storage import db

logger = logging.getLogger("pollers.display")

# ─── Display Settings ────────────────────────────────
WIDTH  = 320
HEIGHT = 240
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONT_BOLD = os.path.join(BASE_DIR, "assets", "fonts", "Inter-Bold.ttf")
FONT_MED  = os.path.join(BASE_DIR, "assets", "fonts", "Inter-Medium.ttf")

# ─── Premium Palette ─────────────────────────────────
BG      = "#020408"
CYAN    = "#06b6d4"
AMBER   = "#f59e0b"
ROSE    = "#f43f5e"
EMERALD = "#10b981"
SLATE   = "#475569"
WHITE   = "#f8fafc"
DIM     = "#1e293b"


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
        device = st7789(serial, width=WIDTH, height=HEIGHT, rotate=0)
        return device, Image, ImageDraw, ImageFont
    except Exception as e:
        logger.warning("Display init failed: %s", e)
        return None, None, None, None


# ─── Styling Helpers ─────────────────────────────────

_FCACHE = {}

def get_font(ImageFont, family, size):
    path = FONT_BOLD if family == "bold" else FONT_MED
    key = (path, size)
    if key not in _FCACHE:
        if os.path.exists(path):
            _FCACHE[key] = ImageFont.truetype(path, size)
        else:
            _FCACHE[key] = ImageFont.load_default()
    return _FCACHE[key]


def _text_centered(draw, ImageFont, cx, cy, text, size, fill, family="med", glow=False):
    fnt = get_font(ImageFont, family, size)
    try:
        bbox = fnt.getbbox(text)
        w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    except:
        w, h = len(text) * (size // 2), size

    if glow:
        # Simple bloom effect
        for off in [(1,0),(-1,0),(0,1),(0,-1)]:
            draw.text((cx - w//2 + off[0], cy - h//2 + off[1]), text, fill=fill, font=fnt, opacity=100)
    
    draw.text((cx - w//2, cy - h//2), text, fill=fill, font=fnt)


def _draw_status_bar(draw, ImageFont, data):
    draw.rectangle([(0, HEIGHT-35), (WIDTH, HEIGHT)], fill="#0a0f1a")
    draw.line([(0, HEIGHT-35), (WIDTH, HEIGHT-35)], fill=DIM, width=1)
    
    # Time (IST)
    ist = timezone(timedelta(hours=5, minutes=30))
    time_str = datetime.now(ist).strftime("%H:%M")
    _text_centered(draw, ImageFont, WIDTH-40, HEIGHT-17, time_str, 14, WHITE)
    
    # GPS Fix
    gps = data.get("gps", {})
    fix = gps.get("fix_quality", 0) > 0
    sats = gps.get("satellites", 0)
    fix_col = EMERALD if fix else ROSE
    draw.ellipse([10, HEIGHT-22, 18, HEIGHT-14], fill=fix_col)
    draw.text((25, HEIGHT-25), f"{sats} SATS" if fix else "SEARCHING...", fill=SLATE, font=get_font(ImageFont, "med", 12))


# ─── Modern Page Renderers ───────────────────────────

def _render_cockpit_main(draw, ImageFont, data):
    """Luxury Driving HUD."""
    gps = data.get("gps") or {}
    env = data.get("env") or {}
    
    # 1. LARGE CENTER SPEED
    speed_knots = gps.get("speed_knots")
    s_val = (speed_knots * 1.852) if speed_knots else 0
    
    # Glow Arc
    bbox = [60, 40, 260, 240]
    draw.arc(bbox, 140, 400, fill=DIM, width=4)
    pct = min(1, s_val / 140)
    if pct > 0:
        draw.arc(bbox, 140, 140 + int(260 * pct), fill=CYAN, width=6)
    
    spd_str = f"{int(s_val)}" if speed_knots else "--"
    _text_centered(draw, ImageFont, WIDTH//2, 110, spd_str, 84, WHITE, family="bold")
    _text_centered(draw, ImageFont, WIDTH//2, 165, "KM/H", 16, CYAN)

    # 2. LEFT FLANK: IAQ
    iaq = env.get("iaq_score")
    i_col = EMERALD if (iaq and iaq <= 50) else (AMBER if (iaq and iaq <= 150) else ROSE)
    _text_centered(draw, ImageFont, 50, 80, "AIR", 12, SLATE)
    _text_centered(draw, ImageFont, 50, 105, str(int(iaq)) if iaq else "--", 28, i_col, family="bold")

    # 3. RIGHT FLANK: TEMP
    temp = env.get("temperature")
    _text_centered(draw, ImageFont, 270, 80, "CABIN", 12, SLATE)
    _text_centered(draw, ImageFont, 270, 105, f"{int(temp)}°C" if temp else "--°", 28, WHITE, family="bold")

    _draw_status_bar(draw, ImageFont, data)


def _render_cockpit_imu(draw, ImageFont, data):
    """High-Res G-Force Tracker."""
    imu = data.get("imu") or {}
    ax, ay = imu.get("ax", 0), imu.get("ay", 0)
    
    # Grid
    cx, cy = WIDTH//2, HEIGHT//2 - 10
    for r in [40, 80]:
        draw.ellipse([cx-r, cy-r, cx+r, cy+r], outline=DIM, width=1)
    draw.line([cx-90, cy, cx+90, cy], fill=DIM)
    draw.line([cx, cy-90, cx, cy+90], fill=DIM)
    
    # Dot with tail
    mx, my = cx + int(ay * 80), cy + int(ax * 80)
    draw.ellipse([mx-6, my-6, mx+6, my+6], fill=ROSE)
    
    _text_centered(draw, ImageFont, WIDTH//2, 20, "LATERAL DYNAMICS", 14, ROSE, family="bold")
    
    g_total = math.sqrt(ax**2 + ay**2)
    _text_centered(draw, ImageFont, WIDTH//2, HEIGHT-60, f"{g_total:.2f} G", 24, WHITE, family="bold")
    
    _draw_status_bar(draw, ImageFont, data)


def _render_cockpit_engine(draw, ImageFont, data):
    """Audi-style Engine View."""
    obd = data.get("obd") or {}
    rpm = obd.get("RPM", {}).get("value")
    
    _text_centered(draw, ImageFont, WIDTH//2, 25, "POWER UNIT", 14, AMBER, family="bold")
    
    if rpm is None:
        _text_centered(draw, ImageFont, WIDTH//2, HEIGHT//2, "LINKING OBD...", 16, SLATE)
    else:
        # Precision Bar
        bar_x, bar_y = 40, 80
        bar_w, bar_h = 240, 40
        draw.rectangle([bar_x, bar_y, bar_x+bar_w, bar_y+bar_h], fill=DIM)
        pct = min(1, rpm / 7500)
        draw.rectangle([bar_x, bar_y, bar_x + int(bar_w*pct), bar_y+bar_h], fill=AMBER if pct < 0.85 else ROSE)
        
        _text_centered(draw, ImageFont, WIDTH//2, bar_y+20, f"{int(rpm)} RPM", 20, WHITE, family="bold")

    # Metrics
    volt = obd.get("CONTROL_MODULE_VOLTAGE", {}).get("value")
    _text_centered(draw, ImageFont, 80, 160, f"{volt:.1f}V" if volt else "--V", 22, WHITE, family="bold")
    _text_centered(draw, ImageFont, 80, 185, "VOLTAGE", 11, SLATE)
    
    cool = obd.get("COOLANT_TEMP", {}).get("value")
    _text_centered(draw, ImageFont, 240, 160, f"{int(cool)}°C" if cool else "--°C", 22, WHITE, family="bold")
    _text_centered(draw, ImageFont, 240, 185, "COOLANT", 11, SLATE)

    _draw_status_bar(draw, ImageFont, data)


def _render_cockpit_env(draw, ImageFont, data):
    """Ambient Environment View (Active when OBD off)."""
    env = data.get("env") or {}
    gps = data.get("gps") or {}
    
    _text_centered(draw, ImageFont, WIDTH//2, 25, "AMBIENT ENVIRONMENT", 14, EMERALD, family="bold")

    # 1. CENTER: AIR QUALITY GAUGE
    iaq = env.get("iaq_score")
    i_col = EMERALD if (iaq and iaq <= 50) else (AMBER if (iaq and iaq <= 150) else ROSE)
    
    bbox = [100, 60, 220, 180]
    draw.arc(bbox, 135, 405, fill=DIM, width=4)
    if iaq:
        pct = min(1, iaq / 300)
        draw.arc(bbox, 135, 135 + int(270 * pct), fill=i_col, width=6)
    
    _text_centered(draw, ImageFont, WIDTH//2, 110, str(int(iaq)) if iaq else "--", 48, i_col, family="bold")
    _text_centered(draw, ImageFont, WIDTH//2, 150, "IAQ INDEX", 12, SLATE)

    # 2. TOP LEF: TEMPERATURE
    temp = env.get("temperature")
    _text_centered(draw, ImageFont, 60, 70, f"{temp:.1f}°" if temp else "--°", 22, WHITE, family="bold")
    _text_centered(draw, ImageFont, 60, 90, "TEMP", 10, SLATE)

    # 3. TOP RIGHT: HUMIDITY
    humi = env.get("humidity")
    _text_centered(draw, ImageFont, 260, 70, f"{int(humi)}%" if humi else "--%", 22, CYAN, family="bold")
    _text_centered(draw, ImageFont, 260, 90, "HUMIDITY", 10, SLATE)

    # 4. BOTTOM LEFT: PRESSURE
    pres = env.get("pressure")
    p_str = f"{int(pres/100)}" if pres else "---"
    _text_centered(draw, ImageFont, 60, 160, p_str, 22, AMBER, family="bold")
    _text_centered(draw, ImageFont, 60, 180, "HPA", 10, SLATE)

    # 5. BOTTOM RIGHT: ELEVATION
    alt = gps.get("alt")
    _text_centered(draw, ImageFont, 260, 160, f"{int(alt)}m" if alt else "--m", 22, PURPLE if 'PURPLE' in globals() else CYAN, family="bold")
    _text_centered(draw, ImageFont, 260, 180, "ALTITUDE", 10, SLATE)

    _draw_status_bar(draw, ImageFont, data)


PAGES = [_render_cockpit_main, _render_cockpit_imu, _render_cockpit_engine, _render_cockpit_env]


class DisplayPoller:
    def __init__(self, gps_poller=None, obd_poller=None, bme680_poller=None, imu_poller=None):
        self._running = False
        self._gps, self._obd, self._bme, self._imu = gps_poller, obd_poller, bme680_poller, imu_poller
        self._page = 0
        self._device, self._Image, self._ImageDraw, self._ImageFont = None, None, None, None

    def _collect_data(self):
        d = {}
        if self._gps and self._gps.last_fix: d["gps"] = self._gps.last_fix
        if self._obd: d["obd"] = self._obd.latest
        if self._bme and self._bme.last_reading: d["env"] = self._bme.last_reading
        if self._imu: d["imu"] = self._imu.last_reading if hasattr(self._imu, "last_reading") else {}
        return d

    async def run(self):
        self._running = True
        self._device, self._Image, self._ImageDraw, self._ImageFont = await asyncio.get_event_loop().run_in_executor(None, _try_import)
        if not self._device: return

        while self._running:
            try:
                data = self._collect_data()
                obd_active = data.get("obd", {}).get("RPM") is not None
                
                # Determine active pages based on OBD connection
                if obd_active:
                    # Driving Mode: Show Speedo + G-Force + Engine
                    active = [PAGES[0], PAGES[1], PAGES[2]]
                else:
                    # Ambient Mode: Show Env + G-Force (if IMU active)
                    active = [PAGES[3]] # Start with Environment UI
                    if data.get("imu", {}).get("ax") is not None:
                        active.append(PAGES[1])
                
                render_fn = active[self._page % len(active)]
                img = self._Image.new("RGB", (WIDTH, HEIGHT), BG)
                render_fn(self._ImageDraw.Draw(img), self._ImageFont, data)
                self._device.display(img)
                self._page += 1
            except Exception as e:
                logger.error("Display UI error: %s", e)
            await asyncio.sleep(config.DISPLAY_PAGE_SEC)

    def stop(self):
        self._running = False
        if self._device: self._device.cleanup()
