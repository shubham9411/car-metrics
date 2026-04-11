/**
 * Car Metrics — Dashboard JS
 * Auto-refreshing dashboard with tab navigation.
 */

const REFRESH_MS = 3000;   // status refresh
const IMG_REFRESH_MS = 10000;  // image list refresh
let currentPage = 'dashboard';
let imgPage = 0;

// ─── Tab Navigation ─────────────────────────────

const el = id => document.getElementById(id) || { textContent: '', innerHTML: '', style: {} };

document.querySelectorAll('.nav-btn').forEach(btn => {
    btn.addEventListener('click', () => {
        document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
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
    if (page === 'trips') loadTrips();
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
        el('pressure').textContent = imu.pressure != null ? (imu.pressure / 100000).toFixed(3) + ' bar' : '--';
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

// ─── G-Force Chart (Chart.js) ──────────────────────────────

let gforceData = [];
let chartInstance = null;

async function loadGforceChart() {
    try {
        const res = await fetch('/api/gforce?limit=150');
        gforceData = await res.json();

        el('chartInfo').textContent = gforceData.length + ' points';
        initOrUpdateChart();
    } catch (e) {
        console.error("Failed to load chart", e);
    }
}

function initOrUpdateChart() {
    const canvas = el('gforceChart');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');

    // Convert DB timestamps & G values for Chart.js
    const labels = gforceData.map(d => {
        let date = new Date(d.ts * 1000);
        return date.toLocaleTimeString([], { minute: '2-digit', second: '2-digit' });
    });
    const datasets = gforceData.map(d => parseFloat(d.g));

    if (chartInstance) {
        chartInstance.data.labels = labels;
        chartInstance.data.datasets[0].data = datasets;
        chartInstance.update('none'); // Update without animation so it doesn't bounce constantly
        return;
    }

    // Gradient fill
    let gradient = ctx.createLinearGradient(0, 0, 0, 300);
    gradient.addColorStop(0, 'rgba(56, 189, 248, 0.4)');
    gradient.addColorStop(1, 'rgba(56, 189, 248, 0.0)');

    chartInstance = new Chart(ctx, {
        type: 'line',
        data: {
            labels: labels,
            datasets: [{
                label: 'G-Force',
                data: datasets,
                borderColor: '#38bdf8',
                backgroundColor: gradient,
                borderWidth: 2,
                pointRadius: 0,
                pointHitRadius: 10,
                fill: true,
                tension: 0.4  // smooth curves
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: false,
            plugins: {
                legend: { display: false },
                tooltip: {
                    mode: 'index',
                    intersect: false,
                    backgroundColor: 'rgba(17, 24, 39, 0.9)',
                    titleColor: '#fff',
                    bodyColor: '#38bdf8',
                    bodyFont: { family: "'JetBrains Mono', monospace", size: 14 }
                }
            },
            interaction: {
                mode: 'nearest',
                axis: 'x',
                intersect: false
            },
            scales: {
                x: {
                    grid: { display: false },
                    ticks: { maxTicksLimit: 8, color: '#94a3b8' }
                },
                y: {
                    grid: { color: 'rgba(255,255,255,0.05)' },
                    ticks: { color: '#94a3b8' },
                    min: 0,
                    suggestedMax: 3
                }
            }
        }
    });
}

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

el('loadMoreImages')?.addEventListener('click', () => { imgPage++; loadImages(false); });

function openModal(src) {
    el('modalImage').src = src;
    const modal = el('imageModal');
    modal.style.display = 'flex';
    modal.onclick = () => { modal.style.display = 'none'; };
}

// Fetch initial force-camera state
fetch('/api/settings/force_camera')
    .then(r => r.json())
    .then(d => { if (el('forceCameraToggle')) el('forceCameraToggle').checked = d.enabled; })
    .catch(e => console.error(e));

el('forceCameraToggle')?.addEventListener('change', async (e) => {
    try {
        await fetch('/api/settings/force_camera', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ enabled: e.target.checked })
        });
    } catch (err) {
        console.error('Failed to save camera setting');
    }
});

// ─── GPS Map & Table ────────────────────────────

let gpsMap = null;
let gpsMarker = null;
let gpsTrackLine = null;
let gpsMapInitialized = false;
let gpsBoundsSet = false;

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
        const res = await fetch('/api/routes');
        const tripsArray = await res.json();

        if (!gpsMap || !Array.isArray(tripsArray) || tripsArray.length === 0) return;

        // Draw multiple disjoint polylines representing historically isolated trips
        gpsTrackLine.setLatLngs(tripsArray);

        // Set the active marker onto the very last known point, if it exists
        const lastTrip = tripsArray[tripsArray.length - 1];
        if (lastTrip && lastTrip.length > 0) {
            const latest = lastTrip[lastTrip.length - 1];
            if (gpsMarker) {
                const latlng = L.latLng(latest[0], latest[1]);
                gpsMarker.setLatLng(latlng);
            }
            if (!gpsBoundsSet) {
                gpsMap.fitBounds(gpsTrackLine.getBounds(), { padding: [20, 20], maxZoom: 15 });
                gpsBoundsSet = true;
            }
        }
    } catch (e) {
        console.error("Map route load error:", e);
    }
}

async function loadGps() {
    // Init map on first visit to GPS tab
    setTimeout(() => {
        initMap();
        if (gpsMap) gpsMap.invalidateSize();
    }, 100);

    try {
        const res = await fetch('/api/gps?limit=50');
        const rows = await res.json();
        // Update map with latest position
        if (rows.length > 0 && rows[0].lat != null) {
            updateMapPosition(rows[0].lat, rows[0].lon);

            // Populate the global map widgets
            if (el('gpsLat')) el('gpsLat').textContent = rows[0].lat.toFixed(6) || '--';
            if (el('gpsLon')) el('gpsLon').textContent = rows[0].lon.toFixed(6) || '--';
            if (el('gpsSats')) el('gpsSats').textContent = rows[0].satellites || '--';
        }

        // Draw Fog of War overlay
        loadGpsTrack();
    } catch (e) {
        console.error("GPS load error:", e);
    }
}
// ─── Trips ──────────────────────────────────────

async function loadTrips() {
    try {
        const res = await fetch('/api/trips?limit=20');
        const rows = await res.json();
        const tbody = el('tripsTable');
        if (!tbody) return;

        tbody.innerHTML = '';

        if (rows.length === 0) {
            tbody.innerHTML = '<tr><td colspan="4" style="text-align:center; padding:30px; opacity:0.5;">No trips recorded yet.</td></tr>';
            return;
        }

        for (const trip of rows) {
            const startDate = new Date(trip.start_ts * 1000);
            const duration = trip.end_ts ? Math.round((trip.end_ts - trip.start_ts) / 60) : Math.round((Date.now() / 1000 - trip.start_ts) / 60);
            const statusLabel = trip.end_ts ? "Completed" : "<span style='color:var(--accent); font-weight:bold;'>Active</span>";

            // Format score to 1 decimal with color
            const rawScore = trip.driver_score ?? 100;
            const scoreColor = rawScore >= 95 ? "#34d399" : rawScore >= 80 ? "#fbbf24" : "#ef4444";

            const tr = document.createElement('tr');
            tr.style.borderBottom = "1px solid var(--border)";
            tr.innerHTML = `
                <td style="padding:12px; font-family:var(--font-data)">#${trip.id}</td>
                <td style="padding:12px;">${startDate.toLocaleString()}</td>
                <td style="padding:12px;">${duration} min · ${statusLabel}</td>
                <td style="padding:12px;">
                    <div style="font-weight:600; color:${scoreColor}">${rawScore.toFixed(1)} <span style="font-size:0.75rem; opacity:0.6; font-weight:normal">/100</span></div>
                </td>
            `;
            tbody.appendChild(tr);
        }
    } catch (e) {
        console.error("Trips load error:", e);
    }
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

// ─── Toggles & Overrides ────────────────────────

// Main tab polling loop
setInterval(() => {
    if (currentPage === 'imu') loadImu();
    if (currentPage === 'obd') loadObd();
    if (currentPage === 'gps') loadGps();
    if (currentPage === 'trips') loadTrips();
}, REFRESH_MS);

// Force Camera state
fetch('/api/settings/force_camera')
    .then(r => r.json())
    .then(d => { if (el('forceCameraToggle')) el('forceCameraToggle').checked = d.enabled; });

el('forceCameraToggle')?.addEventListener('change', () => {
    const enabled = el('forceCameraToggle').checked;
    fetch('/api/settings/force_camera', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled })
    });
});

// Simulate Data state
fetch('/api/settings/simulate_data')
    .then(r => r.json())
    .then(d => { if (el('simulateDataToggle')) el('simulateDataToggle').checked = d.enabled; });

el('simulateDataToggle')?.addEventListener('change', () => {
    const enabled = el('simulateDataToggle').checked;
    fetch('/api/settings/simulate_data', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled })
    });
});
