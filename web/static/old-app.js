/**
 * Car Metrics — Dashboard JS
 * Auto-refreshing dashboard with tab navigation.
 */

const REFRESH_MS = 3000;   // status refresh
const IMG_REFRESH_MS = 10000;  // image list refresh
let currentPage = 'dashboard';
let imgPage = 0;

// ─── Tab Navigation ─────────────────────────────

document.querySelectorAll('.nav button').forEach(btn => {
    btn.addEventListener('click', () => {
        document.querySelectorAll('.nav button').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        currentPage = btn.dataset.page;
        document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
        document.getElementById('page-' + currentPage).classList.add('active');
        onPageSwitch(currentPage);
    });
});

function onPageSwitch(page) {
    if (page === 'images') loadImages(true);
    if (page === 'gps') loadGps();
    if (page === 'events') loadEvents();
}

// ─── Status Polling ─────────────────────────────

async function fetchStatus() {
    try {
        const res = await fetch('/api/status');
        const d = await res.json();
        updateDashboard(d);
        document.getElementById('statusDot').className = 'status-dot';
    } catch (e) {
        document.getElementById('statusDot').className = 'status-dot offline';
    }
}

function updateDashboard(d) {
    // Header stats
    el('hdrImuCount').textContent = fmt(d.counts.imu_readings);
    el('hdrGpsCount').textContent = fmt(d.counts.gps_fixes);
    el('hdrImgCount').textContent = fmt(d.counts.camera_frames);

    // Uptime
    if (d.uptime_sec) {
        const h = Math.floor(d.uptime_sec / 3600);
        const m = Math.floor((d.uptime_sec % 3600) / 60);
        el('uptime').textContent = `${h}h ${m}m`;
    }

    // IMU data
    if (d.imu) {
        const imu = d.imu;
        const g = Math.sqrt(imu.ax ** 2 + imu.ay ** 2 + imu.az ** 2);
        el('gForce').textContent = g.toFixed(2) + 'g';
        el('ax').textContent = num(imu.ax);
        el('ay').textContent = num(imu.ay);
        el('az').textContent = num(imu.az);
        el('gx').textContent = num(imu.gx);
        el('gy').textContent = num(imu.gy);
        el('gz').textContent = num(imu.gz);
        el('mx').textContent = imu.mx != null ? num(imu.mx) : '--';
        el('my').textContent = imu.my != null ? num(imu.my) : '--';
        el('mz').textContent = imu.mz != null ? num(imu.mz) : '--';
        el('pressure').textContent = imu.pressure != null ? Math.round(imu.pressure) + ' Pa' : '--';
        el('temp').innerHTML = imu.temp_c != null ? num(imu.temp_c) + '<span class="unit">°C</span>' : '--';
        el('altitude').innerHTML = imu.altitude != null ? Math.round(imu.altitude) + '<span class="unit">m</span>' : '--';

        // Heading — prefer GPS course (reliable in car), fallback magnetometer
        if (imu.mx != null && imu.my != null) {
            let heading = Math.atan2(imu.my, imu.mx) * (180 / Math.PI);
            if (heading < 0) heading += 360;
            el('heading').innerHTML = Math.round(heading) + '<span class="unit">° mag</span>';
        }
    }

    // GPS data
    if (d.gps) {
        const gps = d.gps;
        const speedKmh = gps.speed_knots != null ? (gps.speed_knots * 1.852).toFixed(1) : '--';
        el('speed').innerHTML = speedKmh + '<span class="unit">km/h</span>';
        el('gpsLat').textContent = gps.lat != null ? gps.lat.toFixed(6) : '--';
        el('gpsLon').textContent = gps.lon != null ? gps.lon.toFixed(6) : '--';
        el('gpsAlt').textContent = gps.alt != null ? Math.round(gps.alt) + ' m' : '--';
        el('gpsSpeed').textContent = speedKmh + ' km/h';
        el('gpsCourse').textContent = gps.course != null ? gps.course.toFixed(1) + '°' : '--';
        el('gpsSats').textContent = gps.satellites != null ? gps.satellites : '--';

        // GPS course overrides magnetometer heading when available
        if (gps.course != null) {
            el('heading').innerHTML = Math.round(gps.course) + '<span class="unit">° gps</span>';
        }
    }

    // OBD data
    if (d.obd && Object.keys(d.obd).length > 0) {
        const grid = el('obdGrid');
        el('obdEmpty').style.display = 'none';
        grid.innerHTML = '';
        for (const [pid, val] of Object.entries(d.obd)) {
            const item = document.createElement('div');
            item.className = 'data-item';
            item.innerHTML = `<div class="label">${pid.replace(/_/g, ' ')}</div><div class="value">${num(val.value)}<span class="unit" style="font-size:0.7rem;color:var(--text-muted)"> ${val.unit || ''}</span></div>`;
            grid.appendChild(item);
        }
    }

    // Events count
    el('eventCount').textContent = d.counts.events;
}

// ─── G-Force Chart ──────────────────────────────

let gforceData = [];

async function loadGforceChart() {
    try {
        const res = await fetch('/api/gforce?limit=300');
        gforceData = await res.json();
        drawChart();
        el('chartInfo').textContent = gforceData.length + ' points';
    } catch (e) { }
}

function drawChart() {
    const canvas = el('gforceChart');
    const ctx = canvas.getContext('2d');
    const rect = canvas.parentElement.getBoundingClientRect();
    canvas.width = rect.width * (window.devicePixelRatio || 1);
    canvas.height = rect.height * (window.devicePixelRatio || 1);
    ctx.scale(window.devicePixelRatio || 1, window.devicePixelRatio || 1);
    const w = rect.width, h = rect.height;

    ctx.clearRect(0, 0, w, h);

    if (gforceData.length < 2) return;

    const vals = gforceData.map(p => p.g);
    const maxG = Math.max(...vals, 2);
    const minG = Math.min(...vals, 0);
    const range = maxG - minG || 1;

    // Threshold line
    const threshY = h - ((2.5 - minG) / range) * (h - 20) - 10;
    ctx.strokeStyle = '#ef444460';
    ctx.lineWidth = 1;
    ctx.setLineDash([4, 4]);
    ctx.beginPath();
    ctx.moveTo(0, threshY);
    ctx.lineTo(w, threshY);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = '#ef444480';
    ctx.font = '10px Inter';
    ctx.fillText('2.5g threshold', 4, threshY - 4);

    // Chart line
    const gradient = ctx.createLinearGradient(0, 0, 0, h);
    gradient.addColorStop(0, '#3b82f6');
    gradient.addColorStop(1, '#8b5cf6');

    ctx.strokeStyle = gradient;
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    for (let i = 0; i < vals.length; i++) {
        const x = (i / (vals.length - 1)) * w;
        const y = h - ((vals[i] - minG) / range) * (h - 20) - 10;
        i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    }
    ctx.stroke();

    // Fill under line
    const fillGrad = ctx.createLinearGradient(0, 0, 0, h);
    fillGrad.addColorStop(0, '#3b82f620');
    fillGrad.addColorStop(1, '#3b82f602');
    ctx.lineTo(w, h);
    ctx.lineTo(0, h);
    ctx.closePath();
    ctx.fillStyle = fillGrad;
    ctx.fill();
}

window.addEventListener('resize', drawChart);

// ─── Images ─────────────────────────────────────

async function loadImages(reset = false) {
    if (reset) imgPage = 0;
    try {
        const res = await fetch(`/api/images?limit=30&page=${imgPage}`);
        const d = await res.json();
        const gallery = el('imageGallery');
        if (reset) gallery.innerHTML = '';

        if (d.images.length === 0 && imgPage === 0) {
            el('imgEmpty').style.display = '';
            el('loadMoreImages').style.display = 'none';
            return;
        }
        el('imgEmpty').style.display = 'none';
        el('imgTotal').textContent = `${d.total} total`;

        for (const img of d.images) {
            const item = document.createElement('div');
            item.className = 'gallery-item';
            const ts = new Date(img.ts * 1000);
            item.innerHTML = `
                <img src="/images/${img.filename}" alt="${img.filename}" loading="lazy" onclick="openModal('/images/${img.filename}')">
                <div class="meta">
                    <span>${ts.toLocaleString()}</span>
                    ${img.event_triggered ? '<span class="event-badge">EVENT</span>' : ''}
                </div>`;
            gallery.appendChild(item);
        }

        el('loadMoreImages').style.display = (imgPage + 1) * 30 < d.total ? '' : 'none';
    } catch (e) { }
}

el('loadMoreImages').addEventListener('click', () => { imgPage++; loadImages(false); });

function openModal(src) {
    el('modalImage').src = src;
    el('imageModal').classList.add('active');
}

// ─── GPS Map & Table ────────────────────────────

let gpsMap = null;
let gpsMarker = null;
let gpsTrackLine = null;
let gpsMapInitialized = false;

function initMap() {
    if (gpsMapInitialized) return;
    const mapEl = document.getElementById('gpsMap');
    if (!mapEl) return;

    gpsMap = L.map('gpsMap', {
        zoomControl: true,
        attributionControl: false,
    }).setView([20, 78], 5); // Default: India center

    // Dark-themed tiles (CartoDB Dark Matter — no key needed)
    L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
        maxZoom: 19,
        subdomains: 'abcd',
    }).addTo(gpsMap);

    // Custom marker icon (blue dot)
    const markerIcon = L.divIcon({
        className: '',
        html: '<div style="width:14px;height:14px;background:#3b82f6;border:3px solid #fff;border-radius:50%;box-shadow:0 0 10px #3b82f680"></div>',
        iconSize: [14, 14],
        iconAnchor: [7, 7],
    });

    gpsMarker = L.marker([0, 0], { icon: markerIcon }).addTo(gpsMap);
    gpsTrackLine = L.polyline([], {
        color: '#3b82f6',
        weight: 3,
        opacity: 0.7,
    }).addTo(gpsMap);

    gpsMapInitialized = true;

    // Load initial data
    loadGpsTrack();
}

function updateMapPosition(lat, lon) {
    if (!gpsMap || lat == null || lon == null) return;
    const latlng = L.latLng(lat, lon);
    gpsMarker.setLatLng(latlng);
    gpsMap.setView(latlng, Math.max(gpsMap.getZoom(), 14));
    el('mapInfo').textContent = `${lat.toFixed(5)}, ${lon.toFixed(5)}`;
}

async function loadGpsTrack() {
    try {
        const res = await fetch('/api/gps?limit=500');
        const rows = await res.json();
        if (!gpsMap || rows.length === 0) return;

        // Build track from points (oldest first)
        const trackPoints = rows
            .filter(r => r.lat != null && r.lon != null)
            .reverse()
            .map(r => [r.lat, r.lon]);

        if (trackPoints.length > 0) {
            gpsTrackLine.setLatLgs ? gpsTrackLine.setLatLngs(trackPoints) : gpsTrackLine.setLatLngs(trackPoints);
            // Center on latest point
            const latest = trackPoints[trackPoints.length - 1];
            updateMapPosition(latest[0], latest[1]);
        }
    } catch (e) { }
}

async function loadGps() {
    // Init map on first visit to GPS tab
    setTimeout(initMap, 100);

    try {
        const res = await fetch('/api/gps?limit=50');
        const rows = await res.json();
        const tbody = el('gpsTable');
        tbody.innerHTML = '';

        // Update map with latest position
        if (rows.length > 0 && rows[0].lat != null) {
            updateMapPosition(rows[0].lat, rows[0].lon);
        }

        // Build track line from all points
        const trackPoints = rows
            .filter(r => r.lat != null && r.lon != null)
            .reverse()
            .map(r => [r.lat, r.lon]);
        if (gpsTrackLine && trackPoints.length > 0) {
            gpsTrackLine.setLatLngs(trackPoints);
        }

        for (const r of rows) {
            const ts = new Date(r.ts * 1000);
            const tr = document.createElement('tr');
            tr.innerHTML = `<td>${ts.toLocaleTimeString()}</td><td>${r.lat?.toFixed(6) ?? '--'}</td><td>${r.lon?.toFixed(6) ?? '--'}</td><td>${r.alt != null ? Math.round(r.alt) : '--'}</td><td>${r.speed_knots?.toFixed(1) ?? '--'}</td><td>${r.satellites ?? '--'}</td>`;
            tbody.appendChild(tr);
        }
    } catch (e) { }
}

// ─── Events ─────────────────────────────────────

async function loadEvents() {
    try {
        const res = await fetch('/api/events?limit=50');
        const rows = await res.json();
        const list = el('eventsList');
        list.innerHTML = '';

        if (rows.length === 0) {
            el('eventsEmpty').style.display = '';
            return;
        }
        el('eventsEmpty').style.display = 'none';

        for (const ev of rows) {
            const ts = new Date(ev.ts * 1000);
            const li = document.createElement('li');
            li.className = 'event-item';
            li.innerHTML = `
                <div class="event-icon high-g">⚠️</div>
                <div class="event-info">
                    <div class="event-title">${ev.event_type} — ${ev.g_force?.toFixed(2) ?? '?'}g</div>
                    <div class="event-detail">${ts.toLocaleString()} ${ev.lat ? `· (${ev.lat.toFixed(5)}, ${ev.lon.toFixed(5)})` : ''}</div>
                </div>`;
            list.appendChild(li);
        }
    } catch (e) { }
}

// ─── Helpers ────────────────────────────────────

function el(id) { return document.getElementById(id); }
function num(v) { return v != null ? (typeof v === 'number' ? v.toFixed(2) : v) : '--'; }
function fmt(n) {
    if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M';
    if (n >= 1000) return (n / 1000).toFixed(1) + 'K';
    return n;
}

// ─── Init & Polling ─────────────────────────────

fetchStatus();
loadGforceChart();

setInterval(fetchStatus, REFRESH_MS);
setInterval(() => {
    if (currentPage === 'dashboard') loadGforceChart();
}, 10000);
setInterval(() => {
    if (currentPage === 'images') loadImages(true);
}, IMG_REFRESH_MS);
