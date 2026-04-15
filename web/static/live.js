// ─── Live HUD — Standalone Page ─────────────────────────────────
// Strategy:
//  1. On first poll, seed currentPathPts from server's DB-backed current_path
//  2. Every subsequent poll, append the API GPS point (which is the spoofed
//     position that matches the moving car marker)
// This avoids the coordinate mismatch between mock-spoofed API GPS and
// the Hyderabad real-GPS stored in trip_routes during simulation.

let hudMap = null;
let hudCarMarker = null;
let hudGhostMarker = null;
let hudCurrentLine = null;
let hudGhostLine = null;

let currentPathPts = [];
let lastLat = null;
let lastLon = null;
let seededFromServer = false;

const TILE_URL = 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png';
const TILE_OPTS = { maxZoom: 19, subdomains: 'abcd', attribution: '' };

// ─── Map init ────────────────────────────────────────────────────
function initHudMap(lat, lon) {
    if (hudMap) return;
    hudMap = L.map('hudMap', { zoomControl: true, attributionControl: false })
        .setView([lat, lon], 15);
    L.tileLayer(TILE_URL, TILE_OPTS).addTo(hudMap);
    hudCurrentLine = L.polyline([], { color: '#38bdf8', weight: 5, opacity: 0.9 }).addTo(hudMap);
    hudGhostLine = L.polyline([], { color: '#c084fc', weight: 3, opacity: 0.65, dashArray: '8 6' }).addTo(hudMap);
    setTimeout(() => hudMap && hudMap.invalidateSize(), 100);
}

// ─── Icons ───────────────────────────────────────────────────────
function mkIcon(color) {
    return L.divIcon({
        className: '',
        html: `<div style="width:18px;height:18px;background:${color};border:3px solid #fff;border-radius:50%;box-shadow:0 0 14px ${color}"></div>`,
        iconSize: [18, 18], iconAnchor: [9, 9]
    });
}

// ─── Helpers ─────────────────────────────────────────────────────
function fmtTime(sec) {
    if (sec == null) return '--:--';
    const m = Math.floor(Math.abs(sec) / 60);
    const s = Math.floor(Math.abs(sec) % 60);
    return `${m}:${s.toString().padStart(2, '0')}`;
}
function el(id) { return document.getElementById(id); }

// ─── Poll ────────────────────────────────────────────────────────
async function poll() {
    let d;
    try {
        const r = await fetch('/api/status');
        d = await r.json();
    } catch (e) {
        console.error('HUD fetch error:', e);
        return;
    }

    // ── No recognised route / no active trip ──
    if (!d.ghost || !d.ghost.routines || d.ghost.routines.length === 0) {
        el('noTrip').style.display = 'flex';
        el('tripContent').style.display = 'none';
        return;
    }
    el('noTrip').style.display = 'none';
    el('tripContent').style.display = 'flex';

    const lat = d.gps && d.gps.lat;
    const lon = d.gps && d.gps.lon;

    if (lat && lon) {
        if (!hudMap) initHudMap(lat, lon);

        // ── First load: seed from server DB path then switch to live appending ──
        if (!seededFromServer) {
            const serverPath = d.ghost.current_path;
            if (serverPath && serverPath.length > 0) {
                currentPathPts = serverPath.slice();
            }
            seededFromServer = true;
            lastLat = lat;
            lastLon = lon;
        }

        // ── Append every new GPS point ──
        if (lat !== lastLat || lon !== lastLon) {
            currentPathPts.push([lat, lon]);
            if (currentPathPts.length > 3000) currentPathPts.shift();
            lastLat = lat;
            lastLon = lon;
        }

        // ── Render blue trail ──
        if (hudCurrentLine && currentPathPts.length > 0) {
            hudCurrentLine.setLatLngs(currentPathPts);
        }

        // ── Ghost PB path (static — from server time-stamped array) ──
        const ghostPath = d.ghost.ghost_path || [];
        if (ghostPath.length > 0 && hudGhostLine.getLatLngs().length === 0) {
            hudGhostLine.setLatLngs(ghostPath.map(p => [p[0], p[1]]));
        }

        // ── Live car marker ──
        const curPt = [lat, lon];
        if (!hudCarMarker) {
            hudCarMarker = L.marker(curPt, { icon: mkIcon('#38bdf8'), zIndexOffset: 1000 }).addTo(hudMap);
        } else {
            hudCarMarker.setLatLng(curPt);
        }
        hudMap.setView(curPt);

        // ── Ghost rival marker (at same elapsed time) ──
        const elapsed = d.ghost.current_duration || 0;
        if (ghostPath.length > 0 && elapsed > 0) {
            let bestIdx = 0, bestDelta = Infinity;
            ghostPath.forEach((p, i) => {
                const diff = Math.abs((p[2] || 0) - elapsed);
                if (diff < bestDelta) { bestDelta = diff; bestIdx = i; }
            });
            const gp = ghostPath[bestIdx];
            if (!hudGhostMarker) {
                hudGhostMarker = L.marker([gp[0], gp[1]], { icon: mkIcon('#c084fc'), zIndexOffset: 999 }).addTo(hudMap);
            } else {
                hudGhostMarker.setLatLng([gp[0], gp[1]]);
            }
        }
    }

    // ── Gauges ──
    if (d.obd && d.obd.SPEED) {
        el('hudSpeed').innerText = d.obd.SPEED.value;
    } else if (d.gps && d.gps.speed_knots != null) {
        el('hudSpeed').innerText = (d.gps.speed_knots * 1.852).toFixed(0);
    }
    if (d.obd && d.obd.RPM) el('hudRPM').innerText = d.obd.RPM.value;

    // ── Ghost race panel ──
    const top = d.ghost.routines[0];
    const curDur = d.ghost.current_duration;
    const pb = top.pb_duration;

    el('hudDest').innerText = d.ghost.predicted_end_name || 'En Route';
    el('hudPB').innerText = fmtTime(pb);
    el('hudCur').innerText = fmtTime(curDur);

    if (pb != null && curDur != null) {
        const delta = curDur - pb;
        el('hudDelta').innerText = (delta <= 0 ? '+' : '-') + fmtTime(Math.abs(delta));
        el('hudDelta').style.color = delta <= 0 ? '#34d399' : '#f43f5e';
        el('hudDeltaLabel').innerText = delta <= 0 ? 'Ahead of Ghost' : 'Behind Ghost';
    }
}

// ─── Boot ────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    poll();
    setInterval(poll, 2000);
});
