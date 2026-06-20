/**
 * Drone log loader — GPX (timed) / KMZ (Mission Planner segments).
 * Used by drone_log_kmz_viewer.html and globe_polygons.html
 */
(function (global) {
    'use strict';

    const KMZ_CRUISE_SPEED_MPS = 10;
    const KMZ_LOITER_SPEED_MPS = 1.2;
    const KMZ_LOITER_MIN_DT_SEC = 2.5;
    const GROUND_AGL_M = 3;
    const TERRAIN_LIFT_M = 2;

    const DroneLog = {
        debug: [],
        lastMetrics: null
    };

    function log(msg, data) {
        DroneLog.debug.push({ t: Date.now(), msg, data });
        console.log('[DroneLog]', msg, data !== undefined ? data : '');
    }

    function parseTime(iso) {
        if (!iso) return null;
        const d = new Date(String(iso).trim());
        return isNaN(d.getTime()) ? null : d;
    }

    function parseGpx(text) {
        const pts = [];
        const re = /<trkpt lat="([^"]+)" lon="([^"]+)">(?:<ele>([^<]*)<\/ele>)?(?:<time>([^<]*)<\/time>)?/g;
        let m;
        while ((m = re.exec(text)) !== null) {
            const lat = parseFloat(m[1]);
            const lon = parseFloat(m[2]);
            if (!Number.isFinite(lat) || !Number.isFinite(lon)) continue;
            pts.push({
                lat,
                lon,
                alt: m[3] ? parseFloat(m[3]) : undefined,
                time: m[4] ? parseTime(m[4]) : null,
                segment: 'gpx'
            });
        }
        log('parseGpx', { points: pts.length, hasTime: pts.every((p) => p.time) });
        return pts;
    }

    function parseKmlCoordinates(coordText) {
        const pts = [];
        coordText.trim().split(/\s+/).forEach((tuple) => {
            const p = tuple.split(',').map(Number);
            if (p.length >= 2 && Number.isFinite(p[0]) && Number.isFinite(p[1])) {
                pts.push({ lon: p[0], lat: p[1], alt: p.length >= 3 ? p[2] : undefined, time: null });
            }
        });
        return pts;
    }

    function parseKmlSegments(text) {
        const doc = new DOMParser().parseFromString(text, 'application/xml');
        const segments = [];
        doc.querySelectorAll('Placemark').forEach((pm) => {
            const name = (pm.querySelector('name')?.textContent || 'segment').trim();
            const coordEl = pm.querySelector('LineString coordinates');
            if (!coordEl) return;
            const points = parseKmlCoordinates(coordEl.textContent);
            if (points.length) segments.push({ name, points });
        });
        log('parseKmlSegments', { segments: segments.length, names: segments.map((s) => s.name) });
        return segments;
    }

    function distanceM(a, b) {
        const c1 = global.Cesium
            ? global.Cesium.Cartesian3.fromDegrees(a.lon, a.lat, a.alt || 0)
            : null;
        const c2 = global.Cesium
            ? global.Cesium.Cartesian3.fromDegrees(b.lon, b.lat, b.alt || 0)
            : null;
        if (c1 && c2) return global.Cesium.Cartesian3.distance(c1, c2);
        const dLat = (b.lat - a.lat) * 111320;
        const dLon = (b.lon - a.lon) * 111320 * Math.cos((a.lat * Math.PI) / 180);
        return Math.sqrt(dLat * dLat + dLon * dLon);
    }

    function isSlowSegment(name) {
        return /loiter|althold|hold|takeoff|land/i.test(name || '');
    }

    function mergeSegmentsToTimedPoints(segments) {
        let tMs = Date.now();
        const all = [];
        let prev = null;
        segments.forEach((seg) => {
            const slow = isSlowSegment(seg.name);
            const speed = slow ? KMZ_LOITER_SPEED_MPS : KMZ_CRUISE_SPEED_MPS;
            seg.points.forEach((p) => {
                const pt = { lon: p.lon, lat: p.lat, alt: p.alt, segment: seg.name, time: null };
                if (prev) {
                    const dist = distanceM(prev, pt);
                    let dt = Math.max(dist / speed, 0.2);
                    if (slow && dist < 5) dt = Math.max(dt, KMZ_LOITER_MIN_DT_SEC);
                    tMs += dt * 1000;
                }
                pt.time = new Date(tMs);
                all.push(pt);
                prev = pt;
            });
        });
        log('mergeSegmentsToTimedPoints', { points: all.length });
        return all;
    }

    function parseKml(text) {
        const segments = parseKmlSegments(text);
        if (segments.length > 0) return mergeSegmentsToTimedPoints(segments);
        const pts = [];
        const doc = new DOMParser().parseFromString(text, 'application/xml');
        doc.querySelectorAll('LineString coordinates').forEach((el) => {
            parseKmlCoordinates(el.textContent).forEach((p) => pts.push({ ...p, segment: 'kml' }));
        });
        return pts;
    }

    async function loadKmzBuffer(arrayBuffer) {
        if (!global.JSZip) throw new Error('JSZip required for KMZ');
        const zip = await global.JSZip.loadAsync(arrayBuffer);
        const names = Object.keys(zip.files).filter((n) => /\.kml$/i.test(n) && !n.startsWith('__'));
        if (!names.length) throw new Error('KMZ has no .kml');
        names.sort();
        const text = await zip.file(names[0]).async('string');
        log('loadKmz', { kml: names[0] });
        return parseKml(text);
    }

    async function fetchText(url) {
        const res = await fetch(url);
        if (!res.ok) throw new Error('HTTP ' + res.status + ' ' + url);
        return res.text();
    }

    async function fetchArrayBuffer(url) {
        const res = await fetch(url);
        if (!res.ok) throw new Error('HTTP ' + res.status + ' ' + url);
        return res.arrayBuffer();
    }

    async function loadFromUrl(url) {
        DroneLog.debug = [];
        const ext = url.split('.').pop().toLowerCase().split('?')[0];
        log('loadFromUrl', { url, ext });
        if (ext === 'kmz') return loadKmzBuffer(await fetchArrayBuffer(url));
        if (ext === 'kml') return parseKml(await fetchText(url));
        if (ext === 'gpx') return parseGpx(await fetchText(url));
        throw new Error('Unsupported: ' + ext);
    }

    const NAV_WP_COMMANDS = new Set([16, 21, 22, 19]);

    function commandLabel(cmd) {
        if (cmd === 22) return 'TAKEOFF';
        if (cmd === 21) return 'LAND';
        if (cmd === 19) return 'LOITER';
        return 'WP';
    }

    /**
     * Mission Planner / QGroundControl waypoint file (QGC WPL 110).
     * @returns {Array<{seq:number,cmd:number,lat:number,lon:number,alt:number,label:string}>}
     */
    function parseQgcWaypoints(text) {
        const lines = String(text).trim().split(/\r?\n/);
        if (!lines.length || !lines[0].includes('QGC WPL')) {
            return [];
        }
        const wps = [];
        for (let i = 2; i < lines.length; i++) {
            const cols = lines[i].trim().split(/\t/);
            if (cols.length < 11) continue;
            const seq = parseInt(cols[0], 10);
            const cmd = parseInt(cols[3], 10);
            const lat = parseFloat(cols[8]);
            const lon = parseFloat(cols[9]);
            const alt = parseFloat(cols[10]);
            if (!Number.isFinite(lat) || !Number.isFinite(lon)) continue;
            if (Math.abs(lat) < 1e-6 && Math.abs(lon) < 1e-6) continue;
            if (!NAV_WP_COMMANDS.has(cmd)) continue;
            wps.push({
                seq,
                cmd,
                lat,
                lon,
                alt: Number.isFinite(alt) ? alt : undefined,
                relativeToGround: true,
                label: commandLabel(cmd) === 'WP' ? 'WP' + seq : commandLabel(cmd)
            });
        }
        log('parseQgcWaypoints', { count: wps.length });
        return wps;
    }

    function nearestTrackIndex(pts, lat, lon) {
        let bestIdx = 0;
        let bestD = Infinity;
        for (let i = 0; i < pts.length; i++) {
            const d = distanceM({ lat, lon }, pts[i]);
            if (d < bestD) {
                bestD = d;
                bestIdx = i;
            }
        }
        return bestIdx;
    }

    /** Nearest log sample on the flown path above ground (same heights as cyan flight polyline). */
    function nearestAirborneTrackIndex(pts, lat, lon, minAltM) {
        const minAlt = Number.isFinite(minAltM) ? minAltM : 450;
        let bestIdx = -1;
        let bestD = Infinity;
        for (let i = 0; i < pts.length; i++) {
            const p = pts[i];
            if (p.onGround) continue;
            if (!Number.isFinite(p.alt) || p.alt < minAlt) continue;
            const d = distanceM({ lat, lon }, p);
            if (d < bestD) {
                bestD = d;
                bestIdx = i;
            }
        }
        if (bestIdx >= 0) return bestIdx;
        for (let i = 0; i < pts.length; i++) {
            const p = pts[i];
            if (!Number.isFinite(p.alt) || p.alt < minAlt) continue;
            const d = distanceM({ lat, lon }, p);
            if (d < bestD) {
                bestD = d;
                bestIdx = i;
            }
        }
        return bestIdx >= 0 ? bestIdx : nearestTrackIndex(pts, lat, lon);
    }

    /**
     * KML "Flight Path" segments (absolute altitude ~ AMSL, same as Mission Planner log export).
     */
    function parseKmlFlightPathPoints(text) {
        const pts = [];
        const re = /<name>[^<]*Flight Path[^<]*<\/name>[\s\S]*?<altitudeMode>\s*([^<]+)\s*<\/altitudeMode>[\s\S]*?<coordinates>\s*([^<]+)\s*<\/coordinates>/gi;
        let m;
        while ((m = re.exec(String(text))) !== null) {
            const absolute = /absolute/i.test(m[1]);
            parseKmlCoordinates(m[2]).forEach((p) => {
                if (Number.isFinite(p.alt)) {
                    pts.push({ lat: p.lat, lon: p.lon, alt: p.alt, absolute });
                }
            });
        }
        log('parseKmlFlightPathPoints', { count: pts.length });
        return pts;
    }

    async function loadKmlFlightPathPoints(kmlUrl) {
        try {
            const text = await fetchText(kmlUrl);
            return parseKmlFlightPathPoints(text);
        } catch (e) {
            log('loadKmlFlightPathPoints failed', e.message);
            return [];
        }
    }

    function subsamplePoints(pts, maxCount) {
        if (!pts || pts.length <= maxCount) return pts || [];
        const step = Math.ceil(pts.length / maxCount);
        const out = [];
        for (let i = 0; i < pts.length; i += step) out.push(pts[i]);
        return out;
    }

    /**
     * Nearest KML flight / log altitude at lat/lon.
     * @param {{ minAltM?: number, maxDistM?: number }} [opts] — skip low (ground) samples unless endpoint
     */
    function nearestFlightAltitude(lat, lon, kmlFlightPts, logPts, opts) {
        const minAltM = opts && Number.isFinite(opts.minAltM) ? opts.minAltM : 0;
        const maxDistM = opts && Number.isFinite(opts.maxDistM) ? opts.maxDistM : 600;
        let bestAlt;
        let bestD = Infinity;
        const tryList = [];
        if (kmlFlightPts && kmlFlightPts.length) tryList.push(kmlFlightPts);
        if (logPts && logPts.length) tryList.push(logPts);
        tryList.forEach((arr) => {
            for (let i = 0; i < arr.length; i++) {
                const p = arr[i];
                if (!Number.isFinite(p.alt) || p.alt < minAltM) continue;
                const d = distanceM({ lat, lon }, p);
                if (d > maxDistM || d >= bestD) continue;
                bestD = d;
                bestAlt = p.alt;
            }
        });
        return bestAlt;
    }

    /**
     * Mission plan (WPL/KML Waypoints) + flying height from KML Flight Path / GPX log.
     */
    function mergeMissionWithFlightHeights(missionWps, logPts, kmlFlightPts, airborneAltM) {
        const flyMin = Number.isFinite(airborneAltM) ? airborneAltM : 450;
        const kmlFly = subsamplePoints(kmlFlightPts, 80000);
        return missionWps.map((mw) => {
            const isEndpoint = mw.cmd === 22 || mw.cmd === 21;
            const trackIdx = isEndpoint
                ? nearestTrackIndex(logPts, mw.lat, mw.lon)
                : nearestAirborneTrackIndex(logPts, mw.lat, mw.lon, flyMin);
            const trackPt = logPts[trackIdx];
            let flyAlt = trackPt && Number.isFinite(trackPt.alt) ? trackPt.alt : undefined;
            if (!isEndpoint && (!Number.isFinite(flyAlt) || trackPt.onGround || flyAlt < flyMin)) {
                const kmlAlt = nearestFlightAltitude(mw.lat, mw.lon, kmlFly, null, {
                    minAltM: flyMin,
                    maxDistM: 350
                });
                if (Number.isFinite(kmlAlt)) flyAlt = kmlAlt;
            }
            return Object.assign({}, mw, {
                flyAlt,
                trackIdx,
                lon: mw.lon,
                lat: mw.lat
            });
        });
    }

    async function loadMissionWaypoints(wplUrl) {
        try {
            const text = await fetchText(wplUrl);
            return parseQgcWaypoints(text);
        } catch (e) {
            log('loadMissionWaypoints failed', e.message);
            return [];
        }
    }

    /**
     * Mission Planner KMZ/KML "Waypoints" LineString (planned survey path).
     * Regex parse — full KML is multi-MB; DOMParser over all Placemarks is slow/unreliable.
     */
    function parseKmlMissionWaypoints(text) {
        const src = String(text);
        const blockRe = /<name>\s*Waypoints\s*<\/name>[\s\S]*?<LineString>[\s\S]*?<altitudeMode>\s*([^<]+)\s*<\/altitudeMode>[\s\S]*?<coordinates>\s*([^<]+)\s*<\/coordinates>/i;
        const block = src.match(blockRe);
        if (!block) {
            log('parseKmlMissionWaypoints', { count: 0, error: 'Waypoints block not found' });
            return [];
        }
        const altitudeMode = (block[1] || 'relativeToGround').trim();
        const relativeToGround = /relative/i.test(altitudeMode);
        const coords = parseKmlCoordinates(block[2]);
        const wps = coords.map((p, idx) => {
            let cmd = 16;
            let label = 'WP' + (idx + 1);
            if (idx === 0) {
                cmd = 22;
                label = 'TAKEOFF';
            } else if (idx === coords.length - 1) {
                cmd = 21;
                label = 'LAND';
            }
            return {
                seq: idx,
                cmd,
                lat: p.lat,
                lon: p.lon,
                alt: Number.isFinite(p.alt) ? p.alt : undefined,
                relativeToGround,
                label
            };
        });
        log('parseKmlMissionWaypoints', { count: wps.length, relativeToGround });
        return wps;
    }

    async function loadKmlMissionWaypoints(kmlUrl) {
        try {
            const text = await fetchText(kmlUrl);
            return parseKmlMissionWaypoints(text);
        } catch (e) {
            log('loadKmlMissionWaypoints failed', e.message);
            return [];
        }
    }

    /** Apply exact KML Waypoints altitudes (relativeToGround) onto WPL mission points by lat/lon. */
    function mergeMissionWaypointsWithKmlHeights(missionWps, kmlWps) {
        if (!kmlWps || !kmlWps.length) {
            return missionWps || [];
        }
        if (!missionWps || !missionWps.length) {
            return kmlWps;
        }
        const matchM = 3;
        let merged = 0;
        missionWps.forEach((mw) => {
            let best = null;
            let bestD = matchM;
            kmlWps.forEach((kw) => {
                const d = distanceM(mw, kw);
                if (d < bestD) {
                    bestD = d;
                    best = kw;
                }
            });
            if (best) {
                mw.alt = best.alt;
                mw.relativeToGround = best.relativeToGround;
                mw.altitudeMode = best.relativeToGround ? 'relativeToGround' : 'absolute';
                merged++;
            }
        });
        log('mergeMissionWaypointsWithKmlHeights', { mission: missionWps.length, kml: kmlWps.length, merged });
        return missionWps;
    }

    function parseParamFile(text) {
        const params = {};
        text.split('\n').forEach((line) => {
            const i = line.indexOf(',');
            if (i > 0) params[line.slice(0, i).trim()] = line.slice(i + 1).trim();
        });
        return params;
    }

  async function loadParamMetrics(url) {
        try {
            const text = await fetchText(url);
            const p = parseParamFile(text);
            return {
                camTrigDistM: parseFloat(p.CAM1_TRIGG_DIST) || 0,
                wpNavSpeedCms: parseFloat(p.WPNAV_SPEED) || 0,
                wpNavSpeedUpCms: parseFloat(p.WPNAV_SPEED_UP) || 0,
                fenceAltMaxM: parseFloat(p.FENCE_ALT_MAX) || 0,
                raw: p
            };
        } catch (e) {
            log('loadParamMetrics failed', e.message);
            return null;
        }
    }

    /**
     * Apply terrain heights; clamp low AGL points to ground for takeoff/land.
     * @param {object[]} pts
     * @param {Cesium.Cartographic[]} terrainCartos same order, .height filled
     */
    function applyTerrainAndTakeoff(pts, terrainCartos) {
        const out = [];
        let pathLenM = 0;
        let airborne = false;
        const home = pts[0];
        const homeTh = terrainCartos[0] && Number.isFinite(terrainCartos[0].height) ? terrainCartos[0].height : 0;

        for (let i = 0; i < pts.length; i++) {
            const p = { ...pts[i] };
            const th = terrainCartos[i] && Number.isFinite(terrainCartos[i].height)
                ? terrainCartos[i].height
                : 0;
            const absAlt = Number.isFinite(p.alt) ? p.alt : th + 50;
            let agl = absAlt - th;

            if (i > 0) {
                pathLenM += distanceM(pts[i - 1], p);
            }
            const distHome = distanceM(home, p);

            if (!airborne) {
                const stillOnPad = pathLenM < 120 && distHome < 40;
                const lowAgl = agl < 12;
                if (stillOnPad || lowAgl) {
                    p.alt = th + TERRAIN_LIFT_M;
                    p.onGround = true;
                    agl = p.alt - th;
                } else {
                    p.alt = absAlt;
                    p.onGround = false;
                    airborne = true;
                }
            } else if (agl < GROUND_AGL_M) {
                p.alt = th + TERRAIN_LIFT_M;
                p.onGround = true;
                agl = p.alt - th;
            } else {
                p.alt = absAlt;
                p.onGround = false;
            }

            p.terrainH = th;
            p.agl = agl;
            out.push(p);
        }
        const groundCount = out.filter((p) => p.onGround).length;
        log('applyTerrainAndTakeoff', {
            groundSamples: groundCount,
            total: out.length,
            homeTerrainM: Math.round(homeTh * 10) / 10
        });
        return out;
    }

    function computeMetrics(pts, sensorHFovDeg, sensorVFovDeg) {
        const agls = pts.map((p) => p.agl).filter((h) => Number.isFinite(h) && h > 5 && h < 200);
        agls.sort((a, b) => a - b);
        const medianAgl = agls.length ? agls[Math.floor(agls.length / 2)] : 50;
        const tanH = Math.tan((sensorHFovDeg * 0.5 * Math.PI) / 180);
        const tanV = Math.tan((sensorVFovDeg * 0.5 * Math.PI) / 180);
        const footprintAlongM = 2 * medianAgl * tanV;
        const footprintAcrossM = 2 * medianAgl * tanH;

        let photoSpacingM = footprintAlongM * 0.2;
        let frontOverlapPct = 80;
        if (pts[0].time && pts.length > 10) {
            const spacings = [];
            for (let i = 1; i < pts.length; i++) {
                const dt = (pts[i].time - pts[i - 1].time) / 1000;
                if (dt <= 0) continue;
                const d = distanceM(pts[i - 1], pts[i]);
                if (d > 0.5 && d < footprintAlongM) spacings.push(d);
            }
            if (spacings.length) {
                spacings.sort((a, b) => a - b);
                photoSpacingM = spacings[Math.floor(spacings.length / 2)];
                frontOverlapPct = Math.round((1 - photoSpacingM / footprintAlongM) * 100);
                frontOverlapPct = Math.min(95, Math.max(0, frontOverlapPct));
            }
        }

        const sideOverlapPct = 70;
        const passSpacingM = footprintAcrossM * (1 - sideOverlapPct / 100);
        const durationSec = pts[0].time && pts[pts.length - 1].time
            ? (pts[pts.length - 1].time - pts[0].time) / 1000
            : 0;

        const m = {
            pointCount: pts.length,
            medianAglM: Math.round(medianAgl * 10) / 10,
            minAglM: agls.length ? Math.round(agls[0] * 10) / 10 : 0,
            maxAglM: agls.length ? Math.round(agls[agls.length - 1] * 10) / 10 : 0,
            footprintAlongM: Math.round(footprintAlongM * 10) / 10,
            footprintAcrossM: Math.round(footprintAcrossM * 10) / 10,
            frontOverlapPct,
            sideOverlapPct,
            photoSpacingM: Math.round(photoSpacingM * 10) / 10,
            passSpacingM: Math.round(passSpacingM * 10) / 10,
            durationSec: Math.round(durationSec),
            sourceHasTimestamps: !!pts[0].time
        };
        DroneLog.lastMetrics = m;
        log('computeMetrics', m);
        return m;
    }

    global.DroneLog = DroneLog;
    global.DroneLog.parseGpx = parseGpx;
    global.DroneLog.parseKml = parseKml;
    global.DroneLog.loadFromUrl = loadFromUrl;
    global.DroneLog.loadKmzBuffer = loadKmzBuffer;
    global.DroneLog.loadParamMetrics = loadParamMetrics;
    global.DroneLog.applyTerrainAndTakeoff = applyTerrainAndTakeoff;
    global.DroneLog.computeMetrics = computeMetrics;
    global.DroneLog.distanceM = distanceM;
    global.DroneLog.parseQgcWaypoints = parseQgcWaypoints;
    global.DroneLog.loadMissionWaypoints = loadMissionWaypoints;
    global.DroneLog.parseKmlMissionWaypoints = parseKmlMissionWaypoints;
    global.DroneLog.loadKmlMissionWaypoints = loadKmlMissionWaypoints;
    global.DroneLog.mergeMissionWaypointsWithKmlHeights = mergeMissionWaypointsWithKmlHeights;
    global.DroneLog.parseKmlFlightPathPoints = parseKmlFlightPathPoints;
    global.DroneLog.loadKmlFlightPathPoints = loadKmlFlightPathPoints;
    global.DroneLog.nearestFlightAltitude = nearestFlightAltitude;
    global.DroneLog.mergeMissionWithFlightHeights = mergeMissionWithFlightHeights;
    global.DroneLog.subsamplePoints = subsamplePoints;
    global.DroneLog.nearestTrackIndex = nearestTrackIndex;
    global.DroneLog.nearestAirborneTrackIndex = nearestAirborneTrackIndex;
})(typeof window !== 'undefined' ? window : globalThis);
