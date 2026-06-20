/**
 * 3D Tiles point cloud — S3 pointcloud_3dtiles (survey/site.json).
 * Original plot: no vertical offset unless alignToGround is true.
 */
(function (global) {
    'use strict';

    function pcCfg() {
        return global.SurveyConfig.get().pointCloud;
    }

    function alignCfg() {
        return global.SurveyConfig.get().alignment;
    }

    let tileset = null;
    let metadata = null;
    let appliedOffsetM = 0;
    let activeTilesetUrl = null;
    let activeSource = null;
    let refineTimer = null;
    let activeViewer = null;
    let loadOverlayTimer = null;
    let loadProgressHandler = null;
    let loadProgressDetach = null;
    let preloadLoadStart = null;

    function getPreloadElapsedMs() {
        if (!preloadLoadStart) return 0;
        return Date.now() - preloadLoadStart;
    }

    function beginPreloadSession() {
        preloadLoadStart = Date.now();
    }

    function endPreloadSession() {
        preloadLoadStart = null;
    }

    function getTilesetLoadProgressEvent(tilesetInstance) {
        if (!tilesetInstance) return null;
        return tilesetInstance.loadProgress || tilesetInstance.tileLoadProgressEvent || null;
    }

    function clearLoadProgressListener() {
        if (loadProgressDetach) {
            loadProgressDetach();
            loadProgressDetach = null;
        }
        loadProgressHandler = null;
    }

    function bindLoadProgressListener(tilesetInstance, handler) {
        clearLoadProgressListener();
        const ev = getTilesetLoadProgressEvent(tilesetInstance);
        if (!ev || typeof ev.addEventListener !== 'function') {
            return false;
        }
        ev.addEventListener(handler);
        loadProgressHandler = handler;
        loadProgressDetach = function () {
            if (ev && typeof ev.removeEventListener === 'function') {
                ev.removeEventListener(handler);
            }
        };
        return true;
    }

    /**
     * Point cloud LOD presets (set pointCloud.loadProfile in survey/site.json):
     *   fast → medium-fast → balanced → medium-quality → quality
     */
    const LOAD_PROFILES = {
        fast: {
            label: 'Fast (low quality)',
            sse: 48,
            skipLevelOfDetail: true,
            baseScreenSpaceError: 2048,
            skipScreenSpaceErrorFactor: 32,
            skipLevels: 2,
            loadSiblings: false,
            immediatelyLoadDesiredLevelOfDetail: false,
            dynamicScreenSpaceError: true,
            dynamicScreenSpaceErrorFactor: 8,
            foveatedScreenSpaceError: true,
            foveatedConeSize: 0.22,
            cullRequestsWhileMoving: true,
            cullWithChildrenBounds: true,
            preferLeaves: false,
            preloadWhenHidden: false,
            eyeDomeLighting: false
        },
        'medium-fast': {
            label: 'Medium fast',
            sse: 12,
            refineSse: 6,
            refineAfterMs: 2200,
            skipLevelOfDetail: true,
            loadSiblings: true,
            immediatelyLoadDesiredLevelOfDetail: true,
            dynamicScreenSpaceError: true,
            foveatedScreenSpaceError: true,
            cullRequestsWhileMoving: true
        },
        balanced: {
            label: 'Balanced',
            sse: 10,
            refineSse: 4,
            refineAfterMs: 2800,
            skipLevelOfDetail: false,
            loadSiblings: true,
            immediatelyLoadDesiredLevelOfDetail: true,
            dynamicScreenSpaceError: true,
            foveatedScreenSpaceError: true,
            cullRequestsWhileMoving: false
        },
        'medium-quality': {
            label: 'Medium quality',
            sse: 6,
            refineSse: 3,
            refineAfterMs: 3500,
            skipLevelOfDetail: false,
            loadSiblings: true,
            immediatelyLoadDesiredLevelOfDetail: true,
            dynamicScreenSpaceError: false,
            foveatedScreenSpaceError: true,
            cullRequestsWhileMoving: false
        },
        quality: {
            label: 'Quality',
            sse: 2,
            skipLevelOfDetail: false,
            loadSiblings: true,
            immediatelyLoadDesiredLevelOfDetail: true,
            dynamicScreenSpaceError: false,
            foveatedScreenSpaceError: false,
            cullRequestsWhileMoving: false,
            preloadWhenHidden: true,
            preferLeaves: true,
            eyeDomeLighting: true
        }
    };

    function formatElapsed(ms) {
        const totalSec = Math.max(0, Math.floor(ms / 1000));
        const m = Math.floor(totalSec / 60);
        const s = totalSec % 60;
        return m + ':' + (s < 10 ? '0' : '') + s;
    }

    function $(id) {
        return global.document ? global.document.getElementById(id) : null;
    }

    function setPreloadMask(on) {
        const container = $('cesiumContainer');
        const viewport = container && container.closest
            ? container.closest('.viewport')
            : null;
        if (container) {
            container.classList.toggle('pc-preload-masked', !!on);
        }
        if (viewport) {
            viewport.classList.toggle('pc-preload-active', !!on);
        }
    }

    function showLoadingOverlay() {
        beginPreloadSession();
        setPreloadMask(true);
        const el = $('pointCloudLoadingOverlay');
        if (!el) return;
        el.classList.add('show');
        el.setAttribute('aria-hidden', 'false');
        updateLoadingOverlay({ elapsedMs: 0, pendingRequests: null, tilesLoaded: false });
        if (loadOverlayTimer) clearInterval(loadOverlayTimer);
        loadOverlayTimer = setInterval(function () {
            const timeEl = $('pcLoadTime');
            if (timeEl) {
                timeEl.textContent = formatElapsed(getPreloadElapsedMs());
            }
        }, 250);
    }

    function hideLoadingOverlay() {
        setPreloadMask(false);
        endPreloadSession();
        const el = $('pointCloudLoadingOverlay');
        if (el) {
            el.classList.remove('show');
            el.setAttribute('aria-hidden', 'true');
        }
        if (loadOverlayTimer) {
            clearInterval(loadOverlayTimer);
            loadOverlayTimer = null;
        }
    }

    function updateLoadingOverlay(info) {
        info = info || {};
        const timeEl = $('pcLoadTime');
        const subEl = $('pcLoadSub');
        const barEl = $('pcLoadBarFill');
        if (timeEl) {
            const ms = info.elapsedMs != null ? info.elapsedMs : getPreloadElapsedMs();
            timeEl.textContent = formatElapsed(ms);
        }
        if (subEl) {
            if (info.statusLabel) {
                subEl.textContent = info.statusLabel;
            } else if (info.done) {
                subEl.textContent = 'Ready — opening view';
            } else if (info.timedOut) {
                subEl.textContent = 'Opening view (some tiles may still refine)';
            } else if (
                (info.pendingRequests != null && info.pendingRequests > 0)
                || (info.processing != null && info.processing > 0)
            ) {
                const parts = [];
                if (info.pendingRequests > 0) {
                    parts.push(info.pendingRequests + ' downloading');
                }
                if (info.processing > 0) {
                    parts.push(info.processing + ' processing');
                }
                subEl.textContent = 'Loading tiles… ' + parts.join(', ');
            } else if (info.tilesLoaded) {
                subEl.textContent = 'Finalizing full-quality tiles…';
            } else {
                subEl.textContent = 'Preparing full-quality tiles…';
            }
        }
        if (barEl) {
            if (info.done) {
                barEl.classList.remove('indeterminate');
                barEl.style.width = '100%';
            } else if (info.pct != null && info.pct > 0) {
                barEl.classList.remove('indeterminate');
                barEl.style.width = Math.min(98, Math.round(info.pct * 100)) + '%';
            } else if (!barEl.classList.contains('indeterminate')) {
                barEl.classList.add('indeterminate');
                barEl.style.width = '';
            }
        }
    }

    function shouldPreloadBeforeShow(opts) {
        if (opts && opts.preloadBeforeShow === false) return false;
        const cfg = pcCfg();
        return cfg.preloadBeforeShow !== false;
    }

    function finalQualitySse(profile) {
        if (profile.refineSse != null) return profile.refineSse;
        if (profile.sse != null) return profile.sse;
        return 2;
    }

    function applyFinalQualitySettings(tileset, profile) {
        tileset.maximumScreenSpaceError = finalQualitySse(profile);
        tileset.skipLevelOfDetail = false;
        tileset.loadSiblings = true;
        tileset.immediatelyLoadDesiredLevelOfDetail = true;
        tileset.dynamicScreenSpaceError = false;
        tileset.foveatedScreenSpaceError = false;
        tileset.cullRequestsWhileMoving = false;
        tileset.preloadWhenHidden = true;
        tileset.preferLeaves = true;
    }

    function getPreloadZoomMultipliers(pc) {
        const levels = pc && pc.preloadZoomLevels;
        if (Array.isArray(levels) && levels.length > 0) {
            return levels.slice().sort(function (a, b) { return b - a; });
        }
        return [2.4, 1.75, 1.25, 0.95, 0.7, 0.5, 0.32];
    }

    function aimCameraAtRange(viewer, tileset, rangeMultiplier) {
        const Cesium = global.Cesium;
        const bs = tileset.boundingSphere;
        const range = Math.max(bs.radius * rangeMultiplier, 35);
        viewer.camera.lookAt(
            bs.center,
            new Cesium.HeadingPitchRange(
                0,
                Cesium.Math.toRadians(-90),
                range
            )
        );
        viewer.camera.lookAtTransform(Cesium.Matrix4.IDENTITY);
    }

    function aimCameraForPreload(viewer, tileset) {
        aimCameraAtRange(viewer, tileset, 2.4);
    }

    async function runMultiZoomPreload(viewer, tileset, profile, loadStart, pc) {
        applyFinalQualitySettings(tileset, profile);
        const multipliers = getPreloadZoomMultipliers(pc);
        const total = multipliers.length;
        const baseSse = finalQualitySse(profile);
        let lastResult = null;
        let i;

        for (i = 0; i < total; i++) {
            const mult = multipliers[i];
            const step = i + 1;
            aimCameraAtRange(viewer, tileset, mult);
            if (mult <= 0.55) {
                tileset.maximumScreenSpaceError = Math.max(0.5, baseSse * 0.65);
            } else if (mult <= 0.95) {
                tileset.maximumScreenSpaceError = Math.max(1, baseSse * 0.85);
            } else {
                tileset.maximumScreenSpaceError = baseSse;
            }

            updateLoadingOverlay({
                statusLabel: 'Loading detail · step ' + step + ' / ' + total,
                pct: Math.round((step / total) * 88)
            });

            lastResult = await waitForQualityPreload(viewer, tileset, profile, null, {
                skipCameraAim: true,
                loadStartTime: loadStart,
                minWaitMs: i === 0
                    ? (pc.preloadMinWaitMs != null ? pc.preloadMinWaitMs : 4000)
                    : (pc.preloadZoomMinWaitMs != null ? pc.preloadZoomMinWaitMs : 2000),
                stableMs: i === total - 1 ? 2600 : 1800,
                maxWaitMs: pc.preloadMaxWaitMs != null ? pc.preloadMaxWaitMs : 600000,
                statusLabel: 'Loading detail · step ' + step + ' / ' + total
            });
        }

        updateLoadingOverlay({
            statusLabel: 'Preparing default view…',
            pct: 92
        });
        aimCameraAtRange(viewer, tileset, 1.5);
        tileset.maximumScreenSpaceError = baseSse;
        lastResult = await waitForQualityPreload(viewer, tileset, profile, null, {
            skipCameraAim: true,
            loadStartTime: loadStart,
            minWaitMs: pc.preloadZoomMinWaitMs != null ? pc.preloadZoomMinWaitMs : 2000,
            stableMs: 2200,
            maxWaitMs: pc.preloadMaxWaitMs != null ? pc.preloadMaxWaitMs : 600000,
            statusLabel: 'Preparing default view…'
        });

        return {
            elapsedMs: Date.now() - loadStart,
            complete: !!(lastResult && lastResult.complete),
            timedOut: !!(lastResult && lastResult.timedOut),
            zoomSteps: total
        };
    }

    function waitForQualityPreload(viewer, tileset, profile, onProgress, waitOpts) {
        waitOpts = waitOpts || {};
        const cfg = pcCfg();
        const maxWaitMs = waitOpts.maxWaitMs != null
            ? waitOpts.maxWaitMs
            : (cfg.preloadMaxWaitMs != null ? cfg.preloadMaxWaitMs : 600000);
        const stableMs = waitOpts.stableMs != null ? waitOpts.stableMs : 2200;
        const minWaitMs = waitOpts.minWaitMs != null
            ? waitOpts.minWaitMs
            : (cfg.preloadMinWaitMs != null ? cfg.preloadMinWaitMs : 4000);
        const statusLabel = waitOpts.statusLabel || null;
        const start = Date.now();
        let maxPending = 0;
        let stableSince = 0;
        let lastPendingCount = 0;
        let lastProcessingCount = 0;
        let sawNetworkActivity = false;
        let tilesTouched = 0;

        if (!waitOpts.skipCameraAim) {
            applyFinalQualitySettings(tileset, profile);
            aimCameraForPreload(viewer, tileset);
        }

        return new Promise(function (resolve) {
            function emit(extra) {
                extra = extra || {};
                const pending = extra.pendingRequests != null
                    ? extra.pendingRequests
                    : lastPendingCount;
                const processing = extra.processing != null
                    ? extra.processing
                    : lastProcessingCount;
                if (pending > maxPending) maxPending = pending;
                const pct = maxPending > 0
                    ? Math.max(0.05, 1 - pending / maxPending)
                    : null;
                const totalStart = waitOpts.loadStartTime || preloadLoadStart;
                const payload = {
                    elapsedMs: totalStart ? Date.now() - totalStart : Date.now() - start,
                    pendingRequests: pending,
                    processing: processing,
                    tilesLoaded: tileset && tileset.tilesLoaded,
                    pct: pct,
                    statusLabel: statusLabel
                };
                if (onProgress) onProgress(payload);
                updateLoadingOverlay(payload);
            }

            function finish(result) {
                clearLoadProgressListener();
                if (tileLoadDetach) tileLoadDetach();
                emit({
                    pendingRequests: 0,
                    processing: 0,
                    done: !!result.complete
                });
                resolve(result);
            }

            function isQueueIdle() {
                return lastPendingCount === 0 && lastProcessingCount === 0;
            }

            function canComplete(elapsed) {
                if (elapsed < minWaitMs) return false;
                if (tileset.tilesLoaded !== true || !isQueueIdle()) return false;
                if (sawNetworkActivity || tilesTouched >= 1) return true;
                return elapsed >= minWaitMs * 2;
            }

            let tileLoadDetach = null;
            if (tileset.tileLoad && typeof tileset.tileLoad.addEventListener === 'function') {
                const onTileLoad = function () {
                    tilesTouched += 1;
                    sawNetworkActivity = true;
                };
                tileset.tileLoad.addEventListener(onTileLoad);
                tileLoadDetach = function () {
                    tileset.tileLoad.removeEventListener(onTileLoad);
                };
            }

            const onLoadProgress = function (pending, processing) {
                lastPendingCount = pending;
                lastProcessingCount = processing;
                if (pending > 0 || processing > 0) {
                    sawNetworkActivity = true;
                }
                emit({
                    pendingRequests: pending,
                    processing: processing,
                    tilesLoaded: tileset.tilesLoaded
                });
            };

            if (!bindLoadProgressListener(tileset, onLoadProgress)) {
                console.warn('[PointCloudTiles] loadProgress event unavailable — using poll only');
            }

            function tick() {
                if (!tileset || tileset.isDestroyed() || !viewer || viewer.isDestroyed()) {
                    finish({ elapsedMs: Date.now() - start, cancelled: true });
                    return;
                }

                viewer.scene.requestRender();

                const elapsed = Date.now() - start;

                if (canComplete(elapsed)) {
                    if (!stableSince) stableSince = Date.now();
                    if (Date.now() - stableSince >= stableMs) {
                        finish({ elapsedMs: elapsed, complete: true });
                        return;
                    }
                } else {
                    stableSince = 0;
                }

                if (elapsed >= maxWaitMs) {
                    finish({ elapsedMs: elapsed, timedOut: true });
                    return;
                }

                emit({
                    pendingRequests: lastPendingCount,
                    processing: lastProcessingCount,
                    tilesLoaded: tileset.tilesLoaded
                });
                global.requestAnimationFrame(tick);
            }

            emit({ pendingRequests: 0, processing: 0, tilesLoaded: false });
            tick();
        });
    }

    function isLocalDevHost() {
        const h = global.location.hostname;
        return (
            global.location.protocol !== 'file:' &&
            (h === 'localhost' || h === '127.0.0.1' || h === '[::1]' || h === '::1')
        );
    }

    function resolveLocalUrl(relativePath) {
        return new URL(relativePath, global.location.href).href;
    }

    function s3ProxyUrl(s3AbsoluteUrl) {
        if (!isLocalDevHost() || !global.SurveyConfig) return s3AbsoluteUrl;
        const base = global.SurveyConfig.get().services.s3Base;
        if (!s3AbsoluteUrl || !String(s3AbsoluteUrl).startsWith(base)) return s3AbsoluteUrl;
        return global.location.origin + '/s3-proxy' + s3AbsoluteUrl.slice(base.length);
    }

    function loadProfile() {
        const cfg = pcCfg();
        const name = cfg.loadProfile || (cfg.preloadBeforeShow !== false ? 'quality' : 'balanced');
        if (!LOAD_PROFILES[name]) {
            console.warn(
                '[PointCloudTiles] unknown loadProfile "' + name
                + '" — using quality. Options:',
                Object.keys(LOAD_PROFILES).join(', ')
            );
            return LOAD_PROFILES.quality;
        }
        return LOAD_PROFILES[name];
    }

    async function probeTilesetJson(url) {
        try {
            const res = await fetch(url, { cache: 'no-store' });
            if (!res.ok) return false;
            JSON.parse(await res.text());
            return true;
        } catch (e) {
            return false;
        }
    }

    async function resolveTilesetUrl() {
        const cfg = pcCfg();
        const mode = cfg.source || 'auto';
        const localUrl = resolveLocalUrl(cfg.localTilesetUrl || './pointcloud_3dtiles/tileset.json');
        const s3Url = cfg.tilesetUrl;

        if (mode === 'local' || mode === 'auto') {
            if (await probeTilesetJson(localUrl)) {
                activeTilesetUrl = localUrl;
                activeSource = 'local';
                console.log('[PointCloudTiles] local tileset (fast):', localUrl);
                return localUrl;
            }
            if (mode === 'local') {
                throw new Error('Local point cloud not found: ' + localUrl);
            }
        }

        if (!s3Url) {
            throw new Error('pointCloud.tilesetUrl missing in survey/site.json');
        }

        const proxied = s3ProxyUrl(s3Url);
        if (await probeTilesetJson(proxied)) {
            activeTilesetUrl = proxied;
            activeSource = proxied === s3Url ? 's3' : 's3-proxy';
            console.log('[PointCloudTiles] tileset [' + activeSource + ']:', proxied);
            return proxied;
        }

        if (await probeTilesetJson(s3Url)) {
            activeTilesetUrl = s3Url;
            activeSource = 's3';
            console.log('[PointCloudTiles] S3 tileset (direct):', s3Url);
            return s3Url;
        }

        throw new Error(
            'Point cloud not reachable. Run: python dev_server.py 8765 — local path: '
            + localUrl
        );
    }

    function scheduleProgressiveRefine(viewer, profile) {
        if (refineTimer) {
            clearTimeout(refineTimer);
            refineTimer = null;
        }
        if (!tileset || !profile.refineAfterMs || profile.refineSse == null) return;
        refineTimer = setTimeout(function () {
            if (!tileset || tileset.isDestroyed()) return;
            tileset.maximumScreenSpaceError = profile.refineSse;
            if (viewer && !viewer.isDestroyed()) {
                viewer.scene.requestRender();
            }
            console.log('[PointCloudTiles] progressive refine SSE →', profile.refineSse);
        }, profile.refineAfterMs);
    }

    async function fetchMetadata() {
        if (metadata) return metadata;
        const cfg = pcCfg();
        if (!cfg.metadataUrl || cfg.fetchMetadata === false) return null;
        try {
            const res = await fetch(cfg.metadataUrl, { cache: 'no-store' });
            if (res.ok) {
                metadata = await res.json();
            }
        } catch (e) {
            /* optional — local tilesets often have no metadata.json on S3 */
        }
        return metadata;
    }

    function unload(viewer, opts) {
        opts = opts || {};
        if (!opts.keepLoadingOverlay) {
            hideLoadingOverlay();
        }
        if (refineTimer) {
            clearTimeout(refineTimer);
            refineTimer = null;
        }
        clearLoadProgressListener();
        activeViewer = null;
        activeTilesetUrl = null;
        activeSource = null;
        if (!tileset) return;
        if (viewer && !viewer.isDestroyed()) {
            viewer.scene.primitives.remove(tileset);
        }
        if (!tileset.isDestroyed()) {
            tileset.destroy();
        }
        tileset = null;
        appliedOffsetM = 0;
        if (viewer && !viewer.isDestroyed()) {
            viewer.scene.requestRender();
        }
    }

    function applyVerticalOffsetMeters(tilesetInstance, metersUp) {
        const Cesium = global.Cesium;
        const center = tilesetInstance.boundingSphere.center;
        const normal = Cesium.Ellipsoid.WGS84.geodeticSurfaceNormal(
            center,
            new Cesium.Cartesian3()
        );
        const delta = Cesium.Cartesian3.multiplyByScalar(
            normal,
            metersUp,
            new Cesium.Cartesian3()
        );
        const translation = Cesium.Matrix4.fromTranslation(delta);
        tilesetInstance.modelMatrix = Cesium.Matrix4.multiply(
            translation,
            tilesetInstance.modelMatrix,
            new Cesium.Matrix4()
        );
    }

    function alignToEllipsoidShell(viewer) {
        const Cesium = global.Cesium;
        const bs = tileset.boundingSphere;
        const centerCarto = Cesium.Cartographic.fromCartesian(bs.center);
        const a = alignCfg();
        const offsetM = bs.radius - centerCarto.height + (a.manualOffsetM || 0);
        if (Math.abs(offsetM) < 0.2) return 0;
        applyVerticalOffsetMeters(tileset, offsetM);
        appliedOffsetM = offsetM;
        viewer.scene.requestRender();
        console.log('[PointCloudTiles] flat-globe align: Δ' + offsetM.toFixed(2) + ' m');
        return offsetM;
    }

    async function alignToDemTerrain(viewer, opts) {
        const Cesium = global.Cesium;
        const a = alignCfg();
        opts = opts || {};
        let tp = opts.terrainProvider;

        if (
            (!tp || tp instanceof Cesium.EllipsoidTerrainProvider) &&
            opts.survey &&
            opts.survey.ensureDemTerrain
        ) {
            await opts.survey.ensureDemTerrain();
            tp = opts.survey.state.ctodTerrainProvider;
            if (opts.useTerrainMesh === true) {
                opts.survey.useDemTerrain();
                viewer.scene.globe.depthTestAgainstTerrain = true;
            } else if (opts.survey.useFlatGlobe) {
                opts.survey.useFlatGlobe();
                viewer.scene.globe.depthTestAgainstTerrain = false;
            }
        }

        if (!tp || tp instanceof Cesium.EllipsoidTerrainProvider) {
            return alignToEllipsoidShell(viewer);
        }

        const bs = tileset.boundingSphere;
        const centerCarto = Cesium.Cartographic.fromCartesian(bs.center);
        const lon = Cesium.Math.toDegrees(centerCarto.longitude);
        const lat = Cesium.Math.toDegrees(centerCarto.latitude);
        const radiusM = bs.radius;
        const dLon = radiusM / (111320 * Math.max(Math.cos(centerCarto.latitude), 0.2));
        const dLat = radiusM / 110540;

        const cartos = [
            [0, 0], [dLon, 0], [-dLon, 0], [0, dLat], [0, -dLat]
        ].map(function (g) {
            return Cesium.Cartographic.fromDegrees(lon + g[0], lat + g[1]);
        });

        const sampled = await Cesium.sampleTerrainMostDetailed(tp, cartos);
        let terrainMin = sampled[0].height;
        sampled.forEach(function (c) {
            if (Cesium.defined(c.height) && !isNaN(c.height) && c.height < terrainMin) {
                terrainMin = c.height;
            }
        });

        if (!Cesium.defined(terrainMin) || isNaN(terrainMin)) {
            return alignToEllipsoidShell(viewer);
        }

        const centerTerrain = sampled[0].height;
        const offsetCenter = centerTerrain - centerCarto.height;
        const offsetBottom = terrainMin + radiusM - centerCarto.height;
        let offsetM = offsetCenter;
        if (centerCarto.height > centerTerrain + 2) {
            offsetM = Math.min(offsetCenter, offsetBottom);
        }
        offsetM += a.manualOffsetM || 0;

        if (Math.abs(offsetM) < 0.2) return 0;

        applyVerticalOffsetMeters(tileset, offsetM);
        appliedOffsetM = offsetM;
        viewer.scene.requestRender();
        console.log(
            '[PointCloudTiles] aligned to ground: Δ' + offsetM.toFixed(2) +
            ' m (terrain ~' + terrainMin.toFixed(1) + ' m, was ' + centerCarto.height.toFixed(1) + ' m)'
        );
        return offsetM;
    }

    async function alignToGround(viewer, opts) {
        opts = opts || {};
        if (!tileset) return 0;
        return alignToDemTerrain(viewer, opts);
    }

    async function load(viewer, opts) {
        const Cesium = global.Cesium;
        opts = opts || {};
        const preload = shouldPreloadBeforeShow(opts);

        unload(viewer, { keepLoadingOverlay: preload });

        if (preload) {
            showLoadingOverlay();
        }

        const tilesetUrl = await resolveTilesetUrl();

        activeViewer = viewer;
        const pc = pcCfg();
        const profile = loadProfile();
        const pointSize = pc.pointSize != null ? pc.pointSize : 1.0;
        const maxAttenuation = pc.maximumAttenuation != null ? pc.maximumAttenuation : 4;
        const baseResolution = pc.baseResolution != null ? pc.baseResolution : 0.08;
        const sse = pc.maximumScreenSpaceError != null
            ? pc.maximumScreenSpaceError
            : (profile.sse != null ? profile.sse : 48);

        if (viewer.scene.pointCloudEyeDomeLighting) {
            viewer.scene.pointCloudEyeDomeLighting.enabled = profile.eyeDomeLighting === true;
        }

        const tilesetOpts = {
            url: tilesetUrl,
            maximumScreenSpaceError: sse,
            skipLevelOfDetail: !!profile.skipLevelOfDetail,
            immediatelyLoadDesiredLevelOfDetail: !!profile.immediatelyLoadDesiredLevelOfDetail,
            loadSiblings: profile.loadSiblings === true,
            dynamicScreenSpaceError: profile.dynamicScreenSpaceError !== false,
            dynamicScreenSpaceErrorDensity: profile.dynamicScreenSpaceErrorDensity != null
                ? profile.dynamicScreenSpaceErrorDensity : 0.004,
            dynamicScreenSpaceErrorFactor: profile.dynamicScreenSpaceErrorFactor != null
                ? profile.dynamicScreenSpaceErrorFactor : 4.0,
            cullRequestsWhileMoving: profile.cullRequestsWhileMoving !== false,
            cullWithChildrenBounds: profile.cullWithChildrenBounds === true,
            foveatedScreenSpaceError: profile.foveatedScreenSpaceError !== false,
            foveatedConeSize: profile.foveatedConeSize != null ? profile.foveatedConeSize : 0.12,
            cacheBytes: preload ? 536870912 : 268435456,
            preloadWhenHidden: preload || profile.preloadWhenHidden === true,
            preferLeaves: preload || profile.preferLeaves === true,
            pointCloudShading: {
                attenuation: profile.skipLevelOfDetail !== true,
                geometricErrorScale: profile.skipLevelOfDetail ? 0.5 : 0.85,
                maximumAttenuation: maxAttenuation,
                baseResolution: baseResolution
            }
        };
        if (profile.baseScreenSpaceError != null) {
            tilesetOpts.baseScreenSpaceError = profile.baseScreenSpaceError;
        }
        if (profile.skipScreenSpaceErrorFactor != null) {
            tilesetOpts.skipScreenSpaceErrorFactor = profile.skipScreenSpaceErrorFactor;
        }
        if (profile.skipLevels != null) {
            tilesetOpts.skipLevels = profile.skipLevels;
        }

        tileset = new Cesium.Cesium3DTileset(tilesetOpts);

        tileset.show = !preload;
        viewer.scene.primitives.add(tileset);
        fetchMetadata();
        tileset.tileFailed.addEventListener(function (_tile, error) {
            console.warn('[PointCloudTiles] tile failed', error);
        });

        try {
            await tileset.readyPromise;
        } catch (e) {
            hideLoadingOverlay();
            unload(viewer);
            throw e;
        }

        tileset.modelMatrix = Cesium.Matrix4.clone(Cesium.Matrix4.IDENTITY);
        appliedOffsetM = 0;

        tileset.style = new Cesium.Cesium3DTileStyle({
            pointSize: String(pointSize)
        });

        const wantAlign =
            opts.alignToGround === true || pcCfg().alignToGround === true;

        if (wantAlign) {
            await alignToGround(viewer, {
                survey: opts.survey,
                useTerrainMesh: opts.useTerrainMesh === true
            });
        } else {
            console.log('[PointCloudTiles] no vertical offset — original tile georeferencing');
        }

        let preloadResult = null;
        if (preload) {
            if (!preloadLoadStart) beginPreloadSession();
            const loadStart = preloadLoadStart || Date.now();
            tileset.show = false;
            if (tileset.initialTilesLoaded && typeof tileset.initialTilesLoaded.then === 'function') {
                try {
                    await tileset.initialTilesLoaded;
                } catch (e) { /* continue */ }
            }

            preloadResult = await runMultiZoomPreload(viewer, tileset, profile, loadStart, pc);
            await flyTo(viewer, 0.01);

            tileset.show = true;
            if (viewer && !viewer.isDestroyed()) {
                viewer.scene.requestRender();
            }
            updateLoadingOverlay({
                elapsedMs: getPreloadElapsedMs(),
                done: true,
                pct: 1,
                statusLabel: 'Ready — total time ' + formatElapsed(getPreloadElapsedMs())
            });
            await new Promise(function (r) { setTimeout(r, 350); });
            hideLoadingOverlay();
        } else {
            scheduleProgressiveRefine(viewer, profile);
        }

        viewer.scene.requestRender();
        const profName = pc.loadProfile || 'quality';
        const profLabel = profile.label || profName;
        const finalSse = preload ? finalQualitySse(profile) : sse;
        console.log(
            '[PointCloudTiles] loaded [' + activeSource + '] profile=' + profName
            + ' (' + profLabel + ') SSE=' + finalSse
            + (preloadResult
                ? ' preload=' + formatElapsed(preloadResult.elapsedMs)
                + (preloadResult.timedOut ? ' (timeout)' : '')
                : ''),
            activeTilesetUrl
        );
        return tileset;
    }

    async function flyTo(viewer, duration) {
        if (!tileset || viewer.isDestroyed()) return;
        const Cesium = global.Cesium;
        const dur = duration != null ? duration : 1.2;
        const bs = tileset.boundingSphere;
        const range = Math.max(bs.radius * 1.5, 80);
        try {
            await viewer.flyTo(tileset, {
                duration: dur,
                offset: new Cesium.HeadingPitchRange(
                    0,
                    Cesium.Math.toRadians(-90),
                    range
                )
            });
        } catch (e) {
            console.warn('[PointCloudTiles] flyTo:', e);
        }
    }

    global.PointCloudTiles = {
        LOAD_PROFILES,
        get activeSource() { return activeSource; },
        load: load,
        unload: unload,
        flyTo: flyTo,
        alignToGround: alignToGround,
        resolveTilesetUrl: resolveTilesetUrl,
        showLoadingOverlay: showLoadingOverlay,
        hideLoadingOverlay: hideLoadingOverlay,
        updateLoadingOverlay: updateLoadingOverlay,
        get tileset() { return tileset; },
        get metadata() { return metadata; },
        get tilesetUrl() { return activeTilesetUrl; },
        get source() { return activeSource; },
        get appliedOffsetM() { return appliedOffsetM; }
    };
})(typeof window !== 'undefined' ? window : globalThis);
