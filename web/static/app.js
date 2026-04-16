/**
 * Car Metrics — Dashboard JS
 * Auto-refreshing dashboard with tab navigation.
 */

const REFRESH_MS = 3000;   // status refresh
const IMG_REFRESH_MS = 10000;  // image list refresh
let currentPage = 'dashboard';
let imgPage = 0;
let envData = [];
let envChartInstance = null;
let lastEnvFetch = 0;

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
    if (el('pageTitle')) el('pageTitle').innerText = page.charAt(0).toUpperCase() + page.slice(1);

    if (page === 'overview') {
        // Nothing special for overview yet
    } else if (page === 'live') {
        if (!ghostOverviewMap) initGhostMap();
        setTimeout(() => {
            if (ghostOverviewMap) ghostOverviewMap.invalidateSize();
        }, 300);
    } else if (page === 'images') {
        loadImages(true);
    } else if (page === 'gps') {
        initMap();
        loadGpsTrack();
    } else if (page === 'events') {
        loadEvents();
    } else if (page === 'trips') {
        loadTrips();
    } else if (page === 'locations') {
        loadLocations();
    }
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

    // Cabin Environment (BME680)
    if (d.env) {
        const e = d.env;
        el('envTemp').innerHTML = e.temperature != null ? e.temperature.toFixed(1) + '<span class="widget-unit">°C</span>' : '--';
        el('envHumidity').innerHTML = e.humidity != null ? e.humidity.toFixed(1) + '<span class="widget-unit">%</span>' : '--';
        el('envPressure').innerHTML = e.pressure != null ? e.pressure.toFixed(1) + '<span class="widget-unit">hPa</span>' : '--';

        if (e.iaq_score != null) {
            const iaq = e.iaq_score;
            let label, color;
            if (iaq <= 50) { label = 'Good'; color = '#34d399'; }
            else if (iaq <= 100) { label = 'OK'; color = '#a3e635'; }
            else if (iaq <= 200) { label = 'Moderate'; color = '#facc15'; }
            else if (iaq <= 300) { label = 'Poor'; color = '#fb923c'; }
            else { label = 'Bad'; color = '#f43f5e'; }
            el('envIAQ').innerHTML = Math.round(iaq) + '<span class="widget-unit" style="color:' + color + '"> ' + label + '</span>';
            el('envIAQ').style.color = color;
        } else {
            el('envIAQ').innerHTML = '--<span class="widget-unit">IAQ</span>';
        }

        let statusText = d.counts.env_readings + ' readings';
        if (e.gas_baseline) statusText += ` | Baseline: ${Math.round(e.gas_baseline / 1000)}kΩ`;
        el('envStatus').textContent = statusText;
    }

    // Ghost Ride Mode
    const gp = el('ghostPanel');
    if (d.ghost && d.ghost.routines && d.ghost.routines.length > 0) {
        gp.style.display = 'block';

        // Use predicted destination name
        el('ghostDestName').innerText = d.ghost.predicted_end_name || "Recognized Routine";

        const cur = d.ghost.current_duration;
        const top = d.ghost.routines[0];
        const pb = top.pb_duration;

        el('ghostPB').innerText = formatDuration(pb);
        el('ghostCurrent').innerText = formatDuration(cur);

        // Mirror to Live HUD Page
        if (el('livePB')) el('livePB').innerText = formatDuration(pb);
        if (el('liveCurrent')) el('liveCurrent').innerText = formatDuration(cur);
        if (el('liveDestName')) el('liveDestName').innerText = d.ghost.predicted_end_name || "Recognized Routine";
        if (el('liveStatusBadge')) el('liveStatusBadge').style.display = 'inline-block';
        if (el('liveGhostDeltaPanel')) el('liveGhostDeltaPanel').style.display = 'flex';

        // --- TEMPORAL GHOST LOGIC ---
        let deltaSec = 0;
        let ghostPos = null;

        if (d.ghost.ghost_path && d.ghost.ghost_path.length > 0 && d.gps && d.gps.lat) {
            const myLat = d.gps.lat;
            const myLon = d.gps.lon;

            // 1. Find the point in Ghost Path closest to my CURRENT position
            let minVDist = Infinity;
            let closestGhostIdx = -1;
            d.ghost.ghost_path.forEach((p, i) => {
                const dv = Math.sqrt(Math.pow(p[0] - myLat, 2) + Math.pow(p[1] - myLon, 2));
                if (dv < minVDist) {
                    minVDist = dv;
                    closestGhostIdx = i;
                }
            });

            if (closestGhostIdx !== -1) {
                const ghostAtMySpot = d.ghost.ghost_path[closestGhostIdx];
                const ghostTimeAtSpot = ghostAtMySpot[2]; // offset_s
                deltaSec = ghostTimeAtSpot - cur;
            }

            // 2. Find where the Ghost is RIGHT NOW (at time 'cur')
            let closestTimeIdx = -1;
            let minTDiff = Infinity;
            d.ghost.ghost_path.forEach((p, i) => {
                const dt = Math.abs(p[2] - cur);
                if (dt < minTDiff) {
                    minTDiff = dt;
                    closestTimeIdx = i;
                }
            });
            if (closestTimeIdx !== -1) {
                // Apply visual offset so ghost is visible even if overlapping
                const GHOST_LAT_OFF = 0.00015;
                const GHOST_LON_OFF = 0.00015;
                ghostPos = [
                    d.ghost.ghost_path[closestTimeIdx][0] + GHOST_LAT_OFF,
                    d.ghost.ghost_path[closestTimeIdx][1] + GHOST_LON_OFF
                ];
            }
        }

        const deltaEl = el('ghostDelta');
        const liveDeltaEl = el('liveGhostDelta');
        const formattedDelta = (deltaSec >= 0 ? "+" : "-") + formatDuration(Math.abs(deltaSec));

        if (deltaEl) deltaEl.innerText = formattedDelta;
        if (liveDeltaEl) liveDeltaEl.innerText = formattedDelta;

        const isAhead = deltaSec >= 0;
        const deltaClass = isAhead ? 'ghost-value-large delta-faster' : 'ghost-value-large delta-slower';
        const labelText = isAhead ? "Ghost Lead" : "Ghost Ahead";

        if (deltaEl) {
            deltaEl.className = deltaClass;
            const lbl = deltaEl.parentElement.querySelector('.ghost-label');
            if (lbl) lbl.innerText = labelText;
        }
        if (liveDeltaEl) {
            liveDeltaEl.className = 'val ' + (isAhead ? 'delta-faster' : 'delta-slower');
            if (el('liveGhostLabel')) el('liveGhostLabel').innerText = labelText;
        }

        // --- DASHBOARD GHOST MAP GHOST-ONLY LAYERS ---
        if (ghostOverviewMap) {
            // Update Full Ghost Line (PB) with offset
            if (d.ghost.ghost_path && d.ghost.ghost_path.length > 0) {
                const GHOST_LAT_OFF = 0.00015;
                const GHOST_LON_OFF = 0.00015;
                if (ghostLinePB) ghostLinePB.setLatLngs(d.ghost.ghost_path.map(p => [p[0] + GHOST_LAT_OFF, p[1] + GHOST_LON_OFF]));
            }

            // Update Ghost Rival Marker
            if (ghostPos) {
                if (!ghostRivalMarker) {
                    const rivalIcon = L.divIcon({
                        className: '',
                        html: '<div style="width:16px;height:16px;background:#c084fc;border:3px solid #fff;border-radius:50%;box-shadow:0 0 10px #c084fc"></div>',
                        iconSize: [16, 16],
                        iconAnchor: [8, 8]
                    });
                    ghostRivalMarker = L.marker(ghostPos, { icon: rivalIcon, zIndexOffset: 500 }).addTo(ghostOverviewMap);
                } else {
                    ghostRivalMarker.setLatLng(ghostPos);
                }
            }
        }
    } else {
        if (gp) gp.style.display = 'none';
        if (el('liveStatusBadge')) el('liveStatusBadge').style.display = 'none';
        if (el('liveGhostDeltaPanel')) el('liveGhostDeltaPanel').style.display = 'none';
        if (el('liveDestName')) el('liveDestName').innerText = "Scanning...";

        if (ghostRivalMarker) {
            if (ghostOverviewMap) ghostOverviewMap.removeLayer(ghostRivalMarker);
            ghostRivalMarker = null;
        }
    }

    // --- GPS DATA COLLECTION (ALWAYS ON) ---
    let fullCurrentPath = [];
    if (d.gps && d.gps.lat) {
        if (d.ghost && d.ghost.current_path && d.ghost.current_path.length > 0) {
            fullCurrentPath = d.ghost.current_path;
        } else {
            fullCurrentPath = [[d.gps.lat, d.gps.lon]];
        }
        updateMapPosition(d.gps.lat, d.gps.lon, fullCurrentPath);
    }

    // ─── Mirror Speed/RPM (Moved UP to guarantee execution) ───
    if (d.obd) {
        if (d.obd.SPEED && el('liveSpeed')) el('liveSpeed').innerText = d.obd.SPEED.value;
        if (d.obd.RPM && el('liveRPM')) el('liveRPM').innerText = d.obd.RPM.value;
    }

    // --- HUD MAP UPDATES (FROM SCRATCH LOGIC) ---
    if (currentPage === 'live') {
        try {
            // Force init if missing
            if (!ghostOverviewMap) {
                initGhostMap();
            }

            // If map is alive, render the backend's source-of-truth immediately
            if (ghostOverviewMap && d.gps && d.ghost) {
                let fullCurrentPath = [];
                if (d.ghost.current_path && d.ghost.current_path.length > 0) {
                    fullCurrentPath = d.ghost.current_path;
                } else if (d.gps.lat) {
                    fullCurrentPath = [[d.gps.lat, d.gps.lon]];
                }

                if (ghostLineCurrent && fullCurrentPath.length > 0) {
                    ghostLineCurrent.setLatLngs(fullCurrentPath);
                }

                if (d.gps.lat) {
                    const curPt = [d.gps.lat, d.gps.lon];
                    if (!liveCarMarker) {
                        const carIcon = L.divIcon({
                            className: '',
                            html: '<div style="width:16px;height:16px;background:#38bdf8;border:3px solid #fff;border-radius:50%;box-shadow:0 0 10px #38bdf8"></div>',
                            iconSize: [16, 16],
                            iconAnchor: [8, 8]
                        });
                        liveCarMarker = L.marker(curPt, { icon: carIcon, zIndexOffset: 1000 }).addTo(ghostOverviewMap);
                    } else {
                        liveCarMarker.setLatLng(curPt);
                    }

                    ghostOverviewMap.invalidateSize();
                    ghostOverviewMap.setView(curPt);
                }
            }
        } catch (e) {
            console.error("Live HUD Map Error:", e);
            if (el('liveDestName')) el('liveDestName').innerText = "Map Err: " + e.message.substring(0, 15);
        }
    }
}

function formatDuration(sec) {
    if (!sec && sec !== 0) return "--:--";
    const m = Math.floor(Math.abs(sec) / 60);
    const s = Math.floor(Math.abs(sec) % 60);
    return `${m}:${s.toString().padStart(2, '0')}`;
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

async function updateEnvHistory() {
    console.log("Fetching env history...");
    try {
        const res = await fetch('/api/env/history?limit=120');
        envData = await res.json();
        console.log(`Loaded ${envData.length} env points`);
        initOrUpdateEnvChart();
    } catch (e) {
        console.error("Failed to load env history", e);
    }
}

function initOrUpdateEnvChart() {
    const canvas = document.getElementById('envChart');
    if (!canvas) {
        console.error("CRITICAL: envChart canvas NOT found in DOM");
        return;
    }

    if (envData.length === 0) {
        console.warn("No env data points to render");
        el('envChartInfo').textContent = "No data";
        return;
    }

    const ctx = canvas.getContext('2d');
    const labels = envData.map(d => {
        let date = new Date(d.ts * 1000);
        return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    });

    console.log(`Starting env chart render with ${envData.length} pts`);

    try {
        if (envChartInstance) {
            envChartInstance.data.labels = labels;
            envChartInstance.data.datasets[0].data = envData.map(d => d.temp);
            envChartInstance.data.datasets[1].data = envData.map(d => d.hum);
            envChartInstance.data.datasets[2].data = envData.map(d => d.iaq);
            envChartInstance.update('none');
            el('envChartInfo').textContent = `${envData.length} pts`;
            return;
        }

        envChartInstance = new Chart(ctx, {
            type: 'line',
            data: {
                labels: labels,
                datasets: [
                    {
                        label: 'Temp (°C)',
                        data: envData.map(d => d.temp),
                        borderColor: '#f87171',
                        yAxisID: 'y',
                        tension: 0.3,
                        pointRadius: 0
                    },
                    {
                        label: 'Hum (%)',
                        data: envData.map(d => d.hum),
                        borderColor: '#38bdf8',
                        yAxisID: 'y',
                        tension: 0.3,
                        pointRadius: 0
                    },
                    {
                        label: 'IAQ',
                        data: envData.map(d => d.iaq),
                        borderColor: '#a3e635',
                        yAxisID: 'y2',
                        tension: 0.3,
                        pointRadius: 0,
                        backgroundColor: 'rgba(163, 230, 53, 0.05)',
                        fill: true
                    }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                animation: false,
                plugins: {
                    legend: { display: true, position: 'top', labels: { color: '#94a3b8', font: { size: 10 } } }
                },
                scales: {
                    x: { ticks: { color: '#64748b', maxTicksLimit: 6 } },
                    y: {
                        position: 'left',
                        ticks: { color: '#94a3b8' },
                        grid: { color: 'rgba(255,255,255,0.05)' }
                    },
                    y2: {
                        position: 'right',
                        min: 0,
                        suggestedMax: 200,
                        grid: { display: false },
                        ticks: { color: '#a3e635' }
                    }
                }
            }
        });
        el('envChartInfo').textContent = `${envData.length} pts`;
        console.log("Env chart initialized successfully");
    } catch (err) {
        console.error("FATAL: Chart rendering failed", err);
        el('envChartInfo').textContent = "Render Error";
    }
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
let fogLayer = null;
let ghostPolyline = null;
let ghostOverviewMap = null;
let ghostLinePB = null;
let ghostLineCurrent = null; // HUD map Current Path
let gpsLineCurrent = null;   // Global map Current Path
let ghostRivalMarker = null;
let liveCarMarker = null;
let ghostCurrentPoints = [];

/**
 * L.FogLayer: A custom canvas layer for Fog of War.
 * Reveals areas by erasing a semi-transparent mask along polylines.
 */
L.FogLayer = L.Layer.extend({
    initialize: function (paths, options) {
        this._paths = paths || [];
        L.setOptions(this, options);
    },
    onAdd: function (map) {
        this._map = map;
        this._canvas = L.DomUtil.create('canvas', 'leaflet-fog-layer');
        this._canvas.style.pointerEvents = 'none';
        map.getPanes().overlayPane.appendChild(this._canvas);
        map.on('move viewreset', this._update, this);
        this._update();
    },
    onRemove: function (map) {
        L.DomUtil.remove(this._canvas);
        map.off('move viewreset', this._update, this);
    },
    setPaths: function (paths) {
        this._paths = paths;
        this._update();
    },
    _update: function () {
        if (!this._map) return;
        const size = this._map.getSize();
        this._canvas.width = size.x;
        this._canvas.height = size.y;

        const pos = this._map.containerPointToLayerPoint([0, 0]);
        L.DomUtil.setPosition(this._canvas, pos);

        const ctx = this._canvas.getContext('2d');
        ctx.clearRect(0, 0, size.x, size.y);

        if (!fogEnabled) return;

        // Clear and fill fog
        ctx.fillStyle = 'rgba(0,0,0,0.65)';
        ctx.fillRect(0, 0, size.x, size.y);

        // Punch holes
        ctx.globalCompositeOperation = 'destination-out';
        ctx.strokeStyle = 'white';
        ctx.lineWidth = 40;
        ctx.lineCap = 'round';
        ctx.lineJoin = 'round';
        ctx.shadowBlur = 10;
        ctx.shadowColor = 'white';

        this._paths.forEach(path => {
            if (!path || path.length < 1) return;
            ctx.beginPath();
            let first = true;
            path.forEach(pt => {
                const cp = this._map.latLngToContainerPoint([pt[0], pt[1]]);
                if (first) { ctx.moveTo(cp.x, cp.y); first = false; }
                else { ctx.lineTo(cp.x, cp.y); }
            });
            ctx.stroke();
        });
    }
});

// End of G-Force Layer

// ─── Locations ───────────────────────────────────
async function loadLocations() {
    const grid = el('locationsGrid');
    if (!grid) return;

    try {
        const r = await fetch('/api/locations');
        const locs = await r.json();

        el('locationsEmpty').style.display = locs.length === 0 ? 'block' : 'none';
        grid.innerHTML = '';

        locs.forEach(l => {
            const card = document.createElement('div');
            card.className = 'data-item glass';
            card.style.display = 'flex';
            card.style.flexDirection = 'column';
            card.style.gap = '12px';
            card.style.padding = '20px';

            card.innerHTML = `
                <div style="display:flex; justify-content:space-between; align-items:center;">
                    <label style="margin:0">Location #${l.id}</label>
                    <span style="font-size:0.75rem; color:var(--text-dim)">${l.visit_count} visits</span>
                </div>
                <div style="display:flex; gap:8px;">
                    <input type="text" value="${l.name || ''}" placeholder="Name (e.g. Home)" 
                        style="background:rgba(0,0,0,0.3); border:1px solid var(--border); color:var(--text-pure); padding:8px 12px; border-radius:8px; flex-grow:1; font-family:var(--font-ui);">
                    <button class="nav-btn" style="padding:8px; border-radius:8px; background:var(--neon-blue); color:white;" onclick="renameLocation(${l.id}, this.previousElementSibling.value)">
                        <i class="ph ph-check"></i>
                    </button>
                </div>
                <div style="font-size:0.75rem; color:var(--text-dim); font-family:var(--font-data);">
                    ${l.lat.toFixed(4)}, ${l.lon.toFixed(4)}
                </div>
            `;
            grid.appendChild(card);
        });
    } catch (e) {
        console.error("Error loading locations:", e);
    }
}

async function renameLocation(id, name) {
    try {
        await fetch('/api/locations', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ id, name })
        });
        loadLocations();
        // and refresh dashboard in case it was showing this loc
        fetchStatus();
    } catch (e) {
    }
}

// ─── Fog of War Toggle ────────────────────────────
let fogEnabled = localStorage.getItem('cm_fog_enabled') !== 'false';

function toggleFog() {
    fogEnabled = !fogEnabled;
    localStorage.setItem('cm_fog_enabled', fogEnabled);

    // Update UI
    const btn = el('fogToggleBtn');
    if (btn) {
        btn.innerHTML = fogEnabled ? '<i class="ph ph-eye-slash"></i> Clear Fog' : '<i class="ph ph-eye"></i> Show Fog';
        btn.classList.toggle('nav-btn-active', !fogEnabled);
    }

    if (fogLayer) {
        fogLayer._update();
    }
}

function initMap() {
    if (gpsMapInitialized) return;
    const mapEl = document.getElementById('gpsMap');
    if (!mapEl) return;

    gpsMap = L.map('gpsMap', {
        zoomControl: true,
        attributionControl: false,
    }).setView([20, 78], 5);

    L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
        maxZoom: 19, subdomains: 'abcd',
    }).addTo(gpsMap);

    // Historical Tracks (Blue)
    gpsTrackLine = L.polyline([], {
        color: '#3b82f6',
        weight: 2,
        opacity: 0.5
    }).addTo(gpsMap);

    // Initialize Fog of War
    fogLayer = new L.FogLayer([]).addTo(gpsMap);

    // Current Trip Trail (Global)
    gpsLineCurrent = L.polyline([], {
        color: '#38bdf8',
        weight: 4,
        opacity: 0.8,
        lineCap: 'round'
    }).addTo(gpsMap);

    // Initialize Ghost Line (PB path) - EMPTY for Global
    ghostPolyline = L.polyline([], {
        color: '#c084fc',
        weight: 2,
        opacity: 0.2
    }).addTo(gpsMap);

    const markerIcon = L.divIcon({
        className: '',
        html: '<div style="width:14px;height:14px;background:#3b82f6;border:3px solid #fff;border-radius:50%;box-shadow:0 0 10px #3b82f680"></div>',
        iconSize: [14, 14],
        iconAnchor: [7, 7]
    });
    gpsMarker = L.marker([0, 0], { icon: markerIcon }).addTo(gpsMap);
    gpsMapInitialized = true;
}

function initGhostMap() {
    const el = document.getElementById('ghostOverviewMap');
    if (!el || ghostOverviewMap) return;

    // Small hack to ensure container visibility
    if (el.clientWidth === 0 || el.clientHeight === 0) {
        console.warn("initGhostMap: Container has no size, delaying init.");
        return;
    }

    try {
        ghostOverviewMap = L.map('ghostOverviewMap', {
            zoomControl: true,
            attributionControl: false,
            dragging: true,
            scrollWheelZoom: true,
            touchZoom: true,
            doubleClickZoom: true
        }).setView([37.7749, -122.4194], 16);

        L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
            maxZoom: 19, subdomains: 'abcd',
        }).addTo(ghostOverviewMap);

        // Personal Best Path (Ghost)
        ghostLinePB = L.polyline([], {
            color: '#c084fc',
            weight: 6,
            opacity: 0.4,
            dashArray: '10, 15',
            lineCap: 'round'
        }).addTo(ghostOverviewMap);

        // Current Trip Path
        ghostLineCurrent = L.polyline([], {
            color: '#38bdf8',
            weight: 4,
            opacity: 1,
            lineCap: 'round'
        }).addTo(ghostOverviewMap);
    } catch (e) {
        console.error("Leaflet init error:", e);
    }
}

function updateMapPosition(lat, lon, currentPathArray = null) {
    if (!gpsMap || lat == null || lon == null) return;
    const latlng = L.latLng(lat, lon);
    gpsMarker.setLatLng(latlng);
    gpsMap.setView(latlng, Math.max(gpsMap.getZoom(), 14));

    // Update live trail on Global Map using the provided backend path
    if (gpsLineCurrent && currentPathArray && currentPathArray.length > 0) {
        gpsLineCurrent.setLatLngs(currentPathArray);
    }

    // Live fog reveal: Add current point to the latest trip in fogLayer
    if (fogLayer && fogLayer._paths && fogLayer._paths.length > 0) {
        const lastTrip = fogLayer._paths[fogLayer._paths.length - 1];
        lastTrip.push([lat, lon]);
        fogLayer._update();
    }
}

async function loadGpsTrack() {
    try {
        const res = await fetch('/api/routes');
        const tripsArray = await res.json();

        if (!gpsMap || !Array.isArray(tripsArray) || tripsArray.length === 0) return;

        // Draw multiple disjoint polylines representing historically isolated trips
        gpsTrackLine.setLatLngs(tripsArray);

        // Update Fog of War paths
        if (fogLayer) fogLayer.setPaths(tripsArray);

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

            const rawScore = trip.score ?? 100;
            const scoreColor = rawScore >= 95 ? "#34d399" : rawScore >= 80 ? "#fbbf24" : "#ef4444";

            const tr = document.createElement('tr');
            tr.style.borderBottom = "1px solid var(--border)";
            tr.style.cursor = "pointer";
            tr.style.transition = "background 0.2s";
            tr.onmouseover = () => tr.style.background = "rgba(255,255,255,0.03)";
            tr.onmouseout = () => tr.style.background = "";
            tr.onclick = () => openTripDrilldown(trip.id);
            tr.innerHTML = `
                <td style="padding:12px; font-family:var(--font-data)">#${trip.id}</td>
                <td style="padding:12px;">${startDate.toLocaleString()}</td>
                <td style="padding:12px;">${duration} min · ${statusLabel}</td>
                <td style="padding:12px;">
                    <div style="font-weight:600; color:${scoreColor}">${typeof rawScore === 'number' ? rawScore.toFixed(1) : rawScore} <span style="font-size:0.75rem; opacity:0.6; font-weight:normal">/100</span></div>
                </td>
            `;
            tbody.appendChild(tr);
        }
    } catch (e) {
        console.error("Trips load error:", e);
    }
}

// ─── Trip Drilldown ─────────────────────────────

let tripDrillMap = null;
let tripDrillChart = null;

function closeTripDrilldown() {
    document.getElementById('tripDrilldown').style.display = 'none';
    if (tripDrillMap) { tripDrillMap.remove(); tripDrillMap = null; }
    if (tripDrillChart) { tripDrillChart.destroy(); tripDrillChart = null; }
}

function speedToColor(speed) {
    if (speed == null) return '#6366f1';
    if (speed > 35) return '#34d399';
    if (speed > 10) return '#fbbf24';
    return '#ef4444';
}

async function openTripDrilldown(tripId) {
    document.getElementById('tripDrilldown').style.display = 'block';
    el('tripDrillTitle').textContent = `Trip #${tripId}`;
    el('tripDrillSummary').textContent = 'Loading...';

    try {
        const res = await fetch(`/api/trips/${tripId}`);
        const data = await res.json();
        if (data.error) { el('tripDrillSummary').textContent = data.error; return; }

        const { trip, route, events, analytics } = data;

        // Summary stats
        const startDate = new Date(trip.start_ts * 1000);
        const duration = trip.end_ts ? Math.round((trip.end_ts - trip.start_ts) / 60) : Math.round((Date.now() / 1000 - trip.start_ts) / 60);
        const score = trip.score ?? 100;
        const scoreColor = score >= 95 ? '#34d399' : score >= 80 ? '#fbbf24' : '#ef4444';

        const totalPenalty = analytics.total_penalty || 0;
        const penaltyInfo = totalPenalty > 0 ? `<div style="font-size:0.75rem; opacity:0.7; margin-top:4px;">-${totalPenalty} raw pts</div>` : '';

        el('tripDrillSummary').textContent = `${startDate.toLocaleDateString()} · ${startDate.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })} · ${trip.end_ts ? 'Completed' : 'Active'}`;
        el('tripDrillScore').innerHTML = `<span style="color:${scoreColor}">${score}</span>${penaltyInfo}`;
        el('tripDrillDuration').textContent = `${duration} min`;
        el('tripDrillAvgSpeed').textContent = `${analytics.avg_speed} kph`;
        el('tripDrillMaxSpeed').textContent = `${analytics.max_speed} kph`;
        el('tripDrillDistance').textContent = analytics.distance > 1000 ? `${(analytics.distance / 1000).toFixed(1)} km` : `${analytics.distance} m`;

        // Address bar
        el('tripDrillStartAddr').textContent = analytics.start_address || 'Unknown';
        el('tripDrillEndAddr').textContent = analytics.end_address || (trip.end_ts ? 'Unknown' : 'In progress...');

        // Event breakdown pills
        const pillsContainer = el('tripDrillEventPills');
        const breakdown = analytics.event_breakdown || {};
        const eventMeta = {
            speeding: { label: 'Speeding', color: '#f59e0b', icon: '⚡', penalty: 3 },
            sudden_brake: { label: 'Sudden Brake', color: '#ef4444', icon: '🛑', penalty: 5 },
            sudden_accel: { label: 'Sudden Accel', color: '#f97316', icon: '🚀', penalty: 3 },
            sharp_turn: { label: 'Sharp Turn', color: '#a855f7', icon: '🔄', penalty: 4 },
            pothole: { label: 'Pothole', color: '#6366f1', icon: '🕳️', penalty: 2 },
            high_impact: { label: 'High Impact', color: '#dc2626', icon: '💥', penalty: 8 },
        };

        // Always show all event types even if 0
        const allTypes = ['speeding', 'sudden_brake', 'sudden_accel', 'sharp_turn', 'pothole'];
        let pillsHtml = '';
        for (const t of allTypes) {
            const count = breakdown[t] || 0;
            const meta = eventMeta[t] || { label: t, color: '#888', icon: '⚠', penalty: 5 };
            const opacity = count > 0 ? '1' : '0.4';
            const totalPenalty = count * meta.penalty;
            const penaltyText = count > 0 ? ` <span style="opacity:0.7; font-size:0.75rem; margin-left:4px;">(-${totalPenalty})</span>` : '';
            pillsHtml += `<span style="display:inline-flex; align-items:center; gap:6px; padding:6px 14px; border-radius:20px; background:${meta.color}20; border:1px solid ${meta.color}40; font-size:0.82rem; color:${meta.color}; opacity:${opacity}; font-weight:500;">${meta.icon} ${count}× ${meta.label}${penaltyText}</span>`;
        }
        pillsContainer.innerHTML = pillsHtml || '<span style="color:var(--text-dim); font-size:0.85rem;">No events</span>';

        // Map: gradient polyline
        if (tripDrillMap) { tripDrillMap.remove(); tripDrillMap = null; }

        setTimeout(() => {
            tripDrillMap = L.map('tripDrillMap', { zoomControl: true, attributionControl: false }).setView([20, 78], 5);
            L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', { maxZoom: 19, subdomains: 'abcd' }).addTo(tripDrillMap);

            if (route.length > 1) {
                for (let i = 0; i < route.length - 1; i++) {
                    const p1 = route[i], p2 = route[i + 1];
                    L.polyline([[p1.lat, p1.lon], [p2.lat, p2.lon]], { color: speedToColor(p2.speed), weight: 4, opacity: 0.9 }).addTo(tripDrillMap);
                }
                tripDrillMap.fitBounds(route.map(p => [p.lat, p.lon]), { padding: [30, 30], maxZoom: 16 });

                // Start / End markers
                const mkIcon = (bg) => L.divIcon({ className: '', html: `<div style="width:12px;height:12px;background:${bg};border:2px solid #fff;border-radius:50%;box-shadow:0 0 8px ${bg}80"></div>`, iconSize: [12, 12], iconAnchor: [6, 6] });
                L.marker([route[0].lat, route[0].lon], { icon: mkIcon('#34d399') }).addTo(tripDrillMap).bindTooltip('Start');
                L.marker([route[route.length - 1].lat, route[route.length - 1].lon], { icon: mkIcon('#ef4444') }).addTo(tripDrillMap).bindTooltip('End');

                // Classified event markers
                for (const ev of events) {
                    if (ev.lat && ev.lon) {
                        const meta = eventMeta[ev.event_type] || { label: ev.event_type, color: '#ef4444', icon: '⚠' };
                        const evIcon = L.divIcon({
                            className: '',
                            html: `<div style="width:22px;height:22px;background:${meta.color};border:2px solid #fff;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:11px;box-shadow:0 0 12px ${meta.color}80">${meta.icon}</div>`,
                            iconSize: [22, 22], iconAnchor: [11, 11]
                        });
                        const tooltip = ev.event_type === 'speeding'
                            ? `${meta.label}: ${(ev.g_force || 0).toFixed(0)} kph`
                            : `${meta.label}: ${(ev.g_force || 0).toFixed(1)}g`;
                        L.marker([ev.lat, ev.lon], { icon: evIcon }).addTo(tripDrillMap).bindTooltip(tooltip);
                    }
                }

                // Peak/Valley elevation markers
                const withAlt = route.filter(p => p.alt != null);
                if (withAlt.length > 0) {
                    const peak = withAlt.reduce((a, b) => a.alt > b.alt ? a : b);
                    const valley = withAlt.reduce((a, b) => a.alt < b.alt ? a : b);
                    const mkAltIcon = (ch, bg) => L.divIcon({ className: '', html: `<div style="width:18px;height:18px;background:${bg};border:2px solid #fff;border-radius:4px;display:flex;align-items:center;justify-content:center;font-size:10px;color:#fff">${ch}</div>`, iconSize: [18, 18], iconAnchor: [9, 9] });
                    L.marker([peak.lat, peak.lon], { icon: mkAltIcon('▲', '#6366f1') }).addTo(tripDrillMap).bindTooltip(`Peak: ${peak.alt.toFixed(0)}m`);
                    L.marker([valley.lat, valley.lon], { icon: mkAltIcon('▼', '#818cf8') }).addTo(tripDrillMap).bindTooltip(`Low: ${valley.alt.toFixed(0)}m`);
                }
            }
            tripDrillMap.invalidateSize();
        }, 150);

        // Chart: Speed + Elevation dual-axis
        if (tripDrillChart) { tripDrillChart.destroy(); tripDrillChart = null; }
        const labels = route.map(p => new Date(p.ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }));
        const ctx = document.getElementById('tripDrillChart').getContext('2d');
        tripDrillChart = new Chart(ctx, {
            type: 'line',
            data: {
                labels,
                datasets: [
                    { label: 'Speed (kph)', data: route.map(p => p.speed ?? 0), borderColor: '#3b82f6', backgroundColor: 'rgba(59,130,246,0.1)', borderWidth: 2, tension: 0.3, fill: false, yAxisID: 'y', pointRadius: 0 },
                    { label: 'Elevation (m)', data: route.map(p => p.alt ?? 0), borderColor: 'rgba(99,102,241,0.5)', backgroundColor: 'rgba(99,102,241,0.15)', borderWidth: 1, tension: 0.3, fill: true, yAxisID: 'y1', pointRadius: 0 },
                ],
            },
            options: {
                responsive: true, maintainAspectRatio: false,
                interaction: { mode: 'index', intersect: false },
                plugins: { legend: { labels: { color: '#aaa', font: { size: 11 } } } },
                scales: {
                    x: { ticks: { color: '#666', maxTicksLimit: 10 }, grid: { color: 'rgba(255,255,255,0.05)' } },
                    y: { position: 'left', title: { display: true, text: 'Speed (kph)', color: '#3b82f6' }, ticks: { color: '#3b82f6' }, grid: { color: 'rgba(255,255,255,0.05)' } },
                    y1: { position: 'right', title: { display: true, text: 'Elevation (m)', color: '#6366f1' }, ticks: { color: '#6366f1' }, grid: { drawOnChartArea: false } },
                },
            },
        });
    } catch (e) {
        console.error("Trip drilldown error:", e);
        el('tripDrillSummary').textContent = 'Error loading trip data.';
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

// Refresh environmental history every 30s
updateEnvHistory();
setInterval(() => {
    if (currentPage === 'overview' || currentPage === 'dashboard') updateEnvHistory();
}, 30000);

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

el('simulateDataToggle')?.addEventListener('change', async () => {
    const enabled = el('simulateDataToggle').checked;
    try {
        const res = await fetch('/api/settings/simulate_data', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ enabled })
        });
        const data = await res.json();
        if (data.status === 'blocked') {
            alert('🚗 ' + data.message);
            el('simulateDataToggle').checked = false; // revert
        }
    } catch (e) {
        console.error('Simulate toggle error:', e);
    }
});
