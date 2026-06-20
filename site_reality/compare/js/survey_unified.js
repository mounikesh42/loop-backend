/**
 * Unified survey viewer — wires Capture rail + Processing + Analytics on one globe.
 */
(function (global) {
    'use strict';

    var bootPromise = null;

    function loadScript(src) {
        return new Promise(function (resolve, reject) {
            if (global.document.querySelector('script[src="' + src + '"]')) {
                resolve();
                return;
            }
            var s = document.createElement('script');
            s.src = src;
            s.onload = resolve;
            s.onerror = function () { reject(new Error('Failed to load ' + src)); };
            document.head.appendChild(s);
        });
    }

    async function loadDeps() {
        var scripts = [
            './js/survey_config.js',
            './js/survey_ortho_dem.js',
            './js/geotagged_images.js',
            './js/cesium_3d_tiles_common.js',
            './js/tileset_align.js',
            './js/pointcloud_tiles.js',
            './js/model_3d_tiles.js',
            './js/processing_globe.js',
            './js/analytics_globe.js'
        ];
        for (var i = 0; i < scripts.length; i++) {
            await loadScript(scripts[i]);
        }
    }

    function surveyRectangleFromConfig() {
        var b = global.SurveyConfig.get().bounds.wgs84;
        var Cesium = global.Cesium;
        return Cesium.Rectangle.fromDegrees(b[0], b[1], b[2], b[3]);
    }

    function showBootBanner(msg, isError) {
        var el = document.getElementById('loadBanner');
        if (!el) return;
        el.textContent = msg;
        el.classList.add('show');
        if (isError) el.style.borderColor = 'rgba(200, 80, 80, 0.6)';
    }

    async function boot() {
        var cap = global.__surveyCapture;
        if (!cap || !cap.viewer) {
            throw new Error('Capture viewer not ready');
        }
        if (global.__surveyUnifiedBooted) return;

        await loadDeps();
        if (typeof SurveyConfig.load === 'function') {
            await SurveyConfig.load();
            SurveyConfig.applySiteLabels();
        }

        global.__skipInlineStockpiles = true;

        try {
            await ProcessingGlobe.init(cap.viewer);
            global.__processingReady = true;
            console.log('[Survey] Processing layers ready');
        } catch (procErr) {
            console.error('[Survey] Processing boot failed:', procErr);
            showBootBanner(
                'Processing failed: ' + (procErr && procErr.message ? procErr.message : procErr),
                true
            );
        }

        try {
            var rect = surveyRectangleFromConfig();
            await AnalyticsGlobe.init(cap.viewer, rect);
            console.log('[Survey] Analytics layers ready');
        } catch (anErr) {
            console.error('[Survey] Analytics boot failed:', anErr);
        }

        global.__surveyUnifiedBooted = true;
        if (global.__processingReady) {
            var banner = document.getElementById('loadBanner');
            if (banner) banner.classList.remove('show');
        }
        if (cap.updateViewportHint) cap.updateViewportHint();
    }

    function ensureBoot() {
        if (!bootPromise) {
            bootPromise = boot().catch(function (e) {
                bootPromise = null;
                throw e;
            });
        }
        return bootPromise;
    }

    function bindProcessingRailGuard() {
        document.querySelectorAll('.sr-litem[data-proc-layer]').forEach(function (el) {
            if (el.dataset.procGuard) return;
            el.dataset.procGuard = '1';
            el.addEventListener('click', function () {
                if (!global.__processingReady) {
                    showBootBanner('Loading processing layers…');
                    ensureBoot().catch(function (e) {
                        showBootBanner('Processing: ' + (e && e.message ? e.message : e), true);
                    });
                }
                if (global.__surveyCapture && global.__surveyCapture.updateViewportHint) {
                    global.__surveyCapture.updateViewportHint();
                }
            });
        });
    }

    function onReady() {
        bindProcessingRailGuard();
        if (global.__surveyCapture && global.__surveyCapture.viewer) {
            ensureBoot();
            return;
        }
        global.addEventListener('surveyCaptureReady', function () {
            ensureBoot();
        }, { once: true });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', onReady);
    } else {
        onReady();
    }

    global.SurveyUnified = { ensureBoot: ensureBoot };
})(typeof window !== 'undefined' ? window : globalThis);
