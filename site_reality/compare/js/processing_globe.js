/**
 * Processing stage — prototype UI + Cesium (layers wired incrementally).
 */
(function (global) {
    'use strict';

    const EYE_ON = '<svg width="14" height="14" viewBox="0 0 14 14" fill="none"><path d="M1 7C1 7 3 3 7 3C11 3 13 7 13 7C13 7 11 11 7 11C3 11 1 7 1 7Z" stroke="currentColor" stroke-width="1.2"/><circle cx="7" cy="7" r="2" stroke="currentColor" stroke-width="1.2"/></svg>';
    const EYE_OFF = '<svg width="14" height="14" viewBox="0 0 14 14" fill="none"><path d="M2 2L12 12M3 7C3 7 4 9 7 9M11 7C11 7 10 5 7 5M5 5L4 4M9 9L10 10" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/></svg>';

    const HEAVY_LAYERS = ['ortho', 'dsm', 'dtm', 'mesh', 'pointcloud'];
    const LAYER_NAMES = {
        ortho: 'Orthomosaic',
        dsm: 'DSM',
        dtm: 'DTM',
        mesh: '3D Model',
        pointcloud: 'Point Cloud',
        images: 'Geotagged Images'
    };

    const LAYER_STATUS = {
        ortho: { ready: true, note: 'DEM terrain + orthomosaic (no DEM imagery)' },
        dsm: { ready: true, note: 'DEM terrain + DEM imagery (no ortho)' },
        dtm: { ready: false, note: 'Plot pending (flat globe)' },
        mesh: { ready: true, note: 'CB-UI: 3dmodel_3dtiles, flat globe, trust tileset.json' },
        pointcloud: { ready: true, note: 'S3 pointcloud_3dtiles — original tiles, no offset' },
        images: { ready: true, note: 'S3 manifest + map points' }
    };

    const state = {
        visible: {
            ortho: false, dsm: false, dtm: false,
            mesh: false, pointcloud: false, images: false
        },
        activeHeavyLayer: null,
        focused: null,
        mode: 'default',
        imagePreviewOpen: false,
        imagePreviewIndex: 0
    };

    let viewer = null;
    let survey = null;
    let loadBanner = null;
    let imagesLoaded = false;

    function getImageList() {
        return global.GeotaggedImages ? GeotaggedImages.images : [];
    }

    function $(id) { return document.getElementById(id); }

    function setEye(el, on) {
        if (!el) return;
        el.classList.toggle('on', on);
        el.classList.toggle('off', !on);
        el.innerHTML = on ? EYE_ON : EYE_OFF;
    }

    function updateEmptyState() {
        const any = Object.values(state.visible).some(Boolean);
        const el = $('emptyState');
        if (el) {
            el.classList.toggle('show', !any);
            el.classList.toggle('hidden', !!any);
        }
        if (global.__surveyCapture && typeof global.__surveyCapture.updateViewportHint === 'function') {
            global.__surveyCapture.updateViewportHint();
        }
    }

    function updateActivePill() {
        const pill = $('activeLayerPill');
        const nameEl = $('activeLayerName');
        if (state.activeHeavyLayer) {
            nameEl.textContent = LAYER_NAMES[state.activeHeavyLayer].toUpperCase();
            pill.classList.add('show');
        } else if (state.visible.images) {
            nameEl.textContent = 'GEOTAGGED IMAGES';
            pill.classList.add('show');
        } else {
            pill.classList.remove('show');
        }
    }

    function showLoadBanner(msg) {
        loadBanner = loadBanner || $('loadBanner');
        if (!loadBanner) return;
        loadBanner.textContent = msg;
        loadBanner.classList.add('show');
    }

    function hideLoadBanner() {
        if (loadBanner) loadBanner.classList.remove('show');
    }

    /** Hide TiTiler ortho (capture page) and SurveyOrthoDem ortho. */
    function setAllOrthoVisible(visible) {
        if (survey && typeof survey.setOrthoVisible === 'function') {
            survey.setOrthoVisible(visible);
        }
        if (
            global.__surveyCapture
            && typeof global.__surveyCapture.setCaptureOrthoVisible === 'function'
        ) {
            global.__surveyCapture.setCaptureOrthoVisible(visible);
        }
        if (viewer && !viewer.isDestroyed()) {
            viewer.scene.requestRender();
        }
    }

    async function applyHeavyLayerVisual(layer) {
        if (!survey) {
            console.warn('[Processing] survey context not ready — wait for unified boot');
            showLoadBanner('Processing layers still loading…');
            return;
        }

        if (layer !== 'pointcloud' && global.PointCloudTiles) {
            PointCloudTiles.unload(viewer);
        }
        if (layer !== 'mesh' && global.Model3DTiles) {
            Model3DTiles.unload(viewer);
        }

        if (!layer) {
            showLoadBanner('Hiding elevation stack…');
            try {
                await survey.applyElevationStack({
                    useTerrain: false,
                    useDemImagery: false,
                    useOrtho: false
                });
            } catch (e) {
                console.error(e);
            }
            hideLoadBanner();
            return;
        }

        if (layer === 'ortho') {
            showLoadBanner('Loading DEM terrain + orthomosaic…');
            try {
                setAllOrthoVisible(false);
                await survey.applyElevationStack({
                    useTerrain: true,
                    useDemImagery: false,
                    useOrtho: true,
                    orthoAlpha: 1
                });
                hideLoadBanner();
            } catch (e) {
                console.error(e);
                showLoadBanner('Ortho stack failed — see console');
            }
            return;
        }

        if (layer === 'dsm') {
            showLoadBanner('Loading DEM terrain + DEM imagery…');
            try {
                await survey.applyElevationStack({
                    useTerrain: true,
                    useDemImagery: true,
                    useOrtho: false,
                    demImageryAlpha: 1
                });
                hideLoadBanner();
            } catch (e) {
                console.error(e);
                showLoadBanner('DEM/DSM stack failed — see console');
            }
            return;
        }

        if (layer === 'mesh') {
            showLoadBanner('Loading 3D model (CB-UI: flat globe, 3dmodel_3dtiles)…');
            try {
                setAllOrthoVisible(false);
                await survey.applyElevationStack({
                    useTerrain: false,
                    useDemImagery: false,
                    useOrtho: false,
                    satellite: true
                });
                await Model3DTiles.load(viewer, { survey: survey });
                await Model3DTiles.flyTo(viewer, 1.2);
                hideLoadBanner();
            } catch (e) {
                console.error(e);
                if (global.Model3DTiles) Model3DTiles.unload(viewer);
                showLoadBanner('3D model failed — check S3 CORS and console');
            }
            return;
        }

        if (layer === 'pointcloud') {
            if (global.PointCloudTiles) {
                PointCloudTiles.showLoadingOverlay();
            }
            try {
                setAllOrthoVisible(false);
                await survey.applyElevationStack({
                    useTerrain: false,
                    useDemImagery: false,
                    useOrtho: false,
                    satellite: false
                });
                if (survey.setSatelliteVisible) survey.setSatelliteVisible(false);
                viewer.scene.globe.baseColor = global.Cesium.Color.fromCssColorString('#0a0e14');
                viewer.scene.globe.showGroundAtmosphere = false;
                await PointCloudTiles.load(viewer, { survey: survey });
            } catch (e) {
                console.error(e);
                if (global.PointCloudTiles) {
                    PointCloudTiles.hideLoadingOverlay();
                    PointCloudTiles.unload(viewer);
                }
                showLoadBanner('Point cloud failed — check console');
                setTimeout(hideLoadBanner, 5000);
            }
            return;
        }

        showLoadBanner('Flat globe — ' + LAYER_NAMES[layer]);
        try {
            await survey.applyElevationStack({
                useTerrain: false,
                useDemImagery: false,
                useOrtho: false
            });
        } catch (e) {
            console.error(e);
        }
        showLoadBanner(LAYER_NAMES[layer] + ' — ' + LAYER_STATUS[layer].note);
        setTimeout(hideLoadBanner, 3200);
    }

    function setHeavyLayer(layer) {
        const prev = state.activeHeavyLayer;
        if (layer != null && prev === layer) return;

        if (prev) {
            state.visible[prev] = false;
            setEye(document.querySelector('[data-toggle="' + prev + '"]'), false);
        }

        state.activeHeavyLayer = layer;

        if (layer) {
            state.visible[layer] = true;
            setEye(document.querySelector('[data-toggle="' + layer + '"]'), true);
        }

        applyHeavyLayerVisual(layer).then(function () {
            if (state.visible.images) {
                return GeotaggedImages.clampToTerrain(viewer);
            }
        }).catch(console.error);
        updateEmptyState();
        updateActivePill();
        if (state.focused && HEAVY_LAYERS.includes(state.focused)) {
            renderRightPanel();
        }
        syncSrRail();
    }

    async function setImagesVisible(visible) {
        state.visible.images = visible;
        setEye(document.querySelector('[data-toggle="images"]'), visible);
        updateEmptyState();
        updateActivePill();

        if (!visible) {
            GeotaggedImages.setVisible(false);
            hideLoadBanner();
            return;
        }

        showLoadBanner('Loading geotagged manifest…');
        try {
            if (!imagesLoaded) {
                await GeotaggedImages.loadManifest();
                GeotaggedImages.plot(viewer, getImageList());
                imagesLoaded = true;
            }
            GeotaggedImages.setVisible(true);
            await GeotaggedImages.clampToTerrain(viewer);
            GeotaggedImages.flyToBounds(viewer);
            hideLoadBanner();
            if (state.focused === 'images') renderRightPanel();
        } catch (e) {
            console.error(e);
            showLoadBanner('Manifest failed — using local copy or check S3 CORS');
            state.visible.images = false;
            setEye(document.querySelector('[data-toggle="images"]'), false);
            updateEmptyState();
            updateActivePill();
        }
        syncSrRail();
    }

    function toggleLayer(obj) {
        if (HEAVY_LAYERS.includes(obj)) {
            setHeavyLayer(state.visible[obj] ? null : obj);
        } else if (obj === 'images') {
            setImagesVisible(!state.visible.images).catch(console.error);
        }
        syncSrRail();
    }

    function syncSrRail() {
        document.querySelectorAll('.sr-litem[data-proc-layer]').forEach(function (el) {
            var key = el.dataset.procLayer;
            var on = !!state.visible[key];
            if (key === 'dsm' && state.activeHeavyLayer === 'dsm') on = true;
            if (key === 'ortho' && state.activeHeavyLayer === 'ortho') on = true;
            if (key === 'dtm' && state.activeHeavyLayer === 'dtm') on = true;
            if (key === 'mesh' && state.activeHeavyLayer === 'mesh') on = true;
            if (key === 'pointcloud' && state.activeHeavyLayer === 'pointcloud') on = true;
            el.classList.toggle('on', on);
        });
    }

    function bindSrRail(selector) {
        var sel = selector || '.sr-litem[data-proc-layer]';
        document.querySelectorAll(sel).forEach(function (el) {
            if (el.dataset.procBound) return;
            el.dataset.procBound = '1';
            el.addEventListener('click', function (e) {
                e.stopPropagation();
                var key = el.dataset.procLayer;
                if (!key) return;
                function runToggle() {
                    if (!survey) {
                        console.warn('[Processing] toggle ignored — context not ready:', key);
                        return;
                    }
                    toggleLayer(key);
                }
                if (!survey && global.SurveyUnified && typeof global.SurveyUnified.ensureBoot === 'function') {
                    global.SurveyUnified.ensureBoot().then(runToggle).catch(console.error);
                    return;
                }
                runToggle();
            });
        });
        syncSrRail();
    }

    function siblingTabs(activeKey) {
        const items = [
            { key: 'images', label: 'Images' },
            { key: 'ortho', label: 'Ortho' },
            { key: 'dsm', label: 'DSM' },
            { key: 'dtm', label: 'DTM' },
            { key: 'mesh', label: '3D' },
            { key: 'pointcloud', label: 'PtCloud' }
        ];
        return '<div class="rp-siblings">' + items.map(function (it) {
            const active = it.key === activeKey;
            return '<button class="rp-sibling' + (active ? ' active' : '') + '"' +
                (active ? '' : ' data-target="' + it.key + '"') + '>' + it.label + '</button>';
        }).join('') + '</div>';
    }

    function procLayerScore(key, fallback) {
        if (global.SurveyConfig && typeof SurveyConfig.getProcessingLayerScore === 'function') {
            try {
                const entry = SurveyConfig.getProcessingLayerScore(key);
                if (entry && entry.score != null) return entry;
            } catch (e) { /* config not loaded */ }
        }
        return fallback || { score: '—', state: 'good' };
    }

    function scoreHeroBlock(score, subHtml) {
        const val = score.score != null ? score.score : '—';
        return '<div class="rp-score"><div class="rp-score-value">' + val + '</div><div class="rp-score-label">/ 100</div></div>' +
            '<div class="rp-score-sub">' + (subHtml || '') + '</div>';
    }

    function renderImagesPanel() {
        const list = getImageList();
        const rows = list.slice(0, 20).map(function (img, idx) {
            const sev = img.state || 'good';
            const sharp = img.sharpness != null ? img.sharpness : '—';
            return '<div class="image-browser-row" data-img-idx="' + idx + '">' +
                '<div class="image-browser-thumb ' + sev + '"></div>' +
                '<div class="image-browser-id">' + img.id + '</div>' +
                '<div class="image-browser-score">' + sharp + '</div>' +
                '<div class="image-browser-state"><span class="dot" style="background:' +
                (sev === 'crit' ? '#C86262' : sev === 'warn' ? '#D2AA4E' : '#7CB89A') + '"></span></div>' +
                '</div>';
        }).join('');
        return '<div class="rp-hero">' +
            '<div class="rp-stage-chip">PROCESSING · CAPTURE</div>' +
            '<div class="rp-name">Geotagged Images</div>' +
            '<div class="rp-score"><div class="rp-score-value">' + list.length + '</div><div class="rp-score-label">IMAGES</div></div>' +
            '<div class="rp-score-sub">Click a point on the globe or a row below</div></div>' +
            siblingTabs('images') +
            '<div class="rp-section"><div class="rp-section-label">Image browser</div>' +
            '<div class="image-browser">' +
            '<div class="image-browser-header"><span></span><span>ID</span><span>Sharp</span><span></span></div>' +
            rows + '</div></div>';
    }

    function renderOrthoPanel() {
        const sc = procLayerScore('ortho', { score: 88, warnCount: 2, critCount: 1 });
        let sub = '';
        if (sc.warnCount) sub += '<span class="warn-count">' + sc.warnCount + ' warn</span>';
        if (sc.critCount) sub += (sub ? ' · ' : '') + '<span class="crit-count">' + sc.critCount + ' critical</span>';
        return '<div class="rp-hero">' +
            '<div class="rp-stage-chip">PROCESSING · HEAVY LAYER</div>' +
            '<div class="rp-name">Orthomosaic</div>' +
            scoreHeroBlock(sc, sub) +
            siblingTabs('ortho') +
            '<div class="rp-section"><div class="rp-section-label">Key Metrics</div>' +
            '<div class="rp-kpi"><span class="rp-kpi-label">GSD</span><span><span class="rp-kpi-value">2.1</span><span class="rp-kpi-unit">cm / px</span></span></div>' +
            '<div class="rp-kpi"><span class="rp-kpi-label">Coverage</span><span><span class="rp-kpi-value">1.8</span><span class="rp-kpi-unit">km²</span></span></div>' +
            '<div class="rp-kpi"><span class="rp-kpi-label">Source</span><span class="rp-kpi-value" style="font-size:11px;font-family:IBM Plex Mono,monospace">TiTiler COG</span></div>' +
            '</div><div class="rp-section"><div class="rp-empty">Stack bottom→top: satellite · 3D DEM terrain · orthomosaic. DEM 2D imagery is not shown with ortho (use DSM for that).</div></div>';
    }

    function renderDsmPanel() {
        const sc = procLayerScore('dsm', { score: 82 });
        return '<div class="rp-hero">' +
            '<div class="rp-stage-chip">PROCESSING · HEAVY LAYER</div>' +
            '<div class="rp-name">DSM</div>' +
            scoreHeroBlock(sc, 'DEM terrain mesh + DEM imagery (ortho off)') +
            siblingTabs('dsm') +
            '<div class="rp-section"><div class="rp-section-label">Key Metrics</div>' +
            '<div class="rp-kpi"><span class="rp-kpi-label">Resolution</span><span><span class="rp-kpi-value">0.5</span><span class="rp-kpi-unit">m grid</span></span></div>' +
            '<div class="rp-kpi"><span class="rp-kpi-label">Elevation range</span><span><span class="rp-kpi-value">512 – 658</span><span class="rp-kpi-unit">m</span></span></div>' +
            '</div><div class="rp-section"><div class="rp-empty">Stack: CTOD 3D terrain (below) + TiTiler DEM colormap imagery. Orthomosaic hidden for DSM view.</div></div>';
    }

    function renderPointCloudPanel() {
        const meta = global.PointCloudTiles && PointCloudTiles.metadata;
        const job = meta && meta.job_id ? meta.job_id : '—';
        const src = meta && meta.input_file
            ? String(meta.input_file).replace(/^s3:\/\/[^/]+\//, '')
            : 'POINT_CLOUD_32644…las';
        const tilesUrl = global.PointCloudTiles && PointCloudTiles.tilesetUrl
            ? PointCloudTiles.tilesetUrl
            : (global.SurveyConfig ? SurveyConfig.get().pointCloud.tilesetUrl : '');
        const sc = procLayerScore('pointcloud', { score: 84 });
        return '<div class="rp-hero">' +
            '<div class="rp-stage-chip">PROCESSING · HEAVY LAYER</div>' +
            '<div class="rp-name">Point Cloud</div>' +
            scoreHeroBlock(sc, LAYER_STATUS.pointcloud.note) +
            siblingTabs('pointcloud') +
            '<div class="rp-section"><div class="rp-section-label">Source</div>' +
            '<div class="rp-kpi"><span class="rp-kpi-label">Input</span><span class="rp-kpi-value" style="font-size:10px;font-family:IBM Plex Mono,monospace">' + src + '</span></div>' +
            '<div class="rp-kpi"><span class="rp-kpi-label">Job</span><span class="rp-kpi-value" style="font-size:10px;font-family:IBM Plex Mono,monospace">' + job + '</span></div>' +
            '<div class="rp-kpi"><span class="rp-kpi-label">Format</span><span><span class="rp-kpi-value">3D Tiles</span><span class="rp-kpi-unit"> · .pnts</span></span></div>' +
            '</div><div class="rp-section"><div class="rp-empty">S3 pointcloud_3dtiles as-is (no vertical shift). Bucket must allow browser CORS.</div>' +
            (tilesUrl ? '<div class="rp-empty" style="margin-top:8px;word-break:break-all;font-size:10px;font-family:IBM Plex Mono,monospace">' + tilesUrl + '</div>' : '') +
            '</div>';
    }

    function renderMeshPanel() {
        const meta = global.Model3DTiles && Model3DTiles.metadata;
        const job = meta && meta.job_id ? meta.job_id : '—';
        const src = meta && meta.input_file
            ? String(meta.input_file).replace(/^s3:\/\/[^/]+\//, '')
            : '3D_MODEL_32644…zip';
        const tilesUrl = global.SurveyConfig ? SurveyConfig.get().model3d.tilesetUrl : '';
        const sc = procLayerScore('mesh', { score: 79 });
        return '<div class="rp-hero">' +
            '<div class="rp-stage-chip">PROCESSING · HEAVY LAYER</div>' +
            '<div class="rp-name">3D Model</div>' +
            scoreHeroBlock(sc, LAYER_STATUS.mesh.note) +
            siblingTabs('mesh') +
            '<div class="rp-section"><div class="rp-section-label">Source</div>' +
            '<div class="rp-kpi"><span class="rp-kpi-label">Input</span><span class="rp-kpi-value" style="font-size:10px;font-family:IBM Plex Mono,monospace">' + src + '</span></div>' +
            '<div class="rp-kpi"><span class="rp-kpi-label">Job</span><span class="rp-kpi-value" style="font-size:10px;font-family:IBM Plex Mono,monospace">' + job + '</span></div>' +
            '<div class="rp-kpi"><span class="rp-kpi-label">Format</span><span><span class="rp-kpi-value">3D Tiles</span><span class="rp-kpi-unit"> · .b3dm</span></span></div>' +
            '</div><div class="rp-section"><div class="rp-empty">Same as CB-UI Layers3DModel: flat ellipsoid, 3dmodel_3dtiles, trust tileset root transform. Optional alignment.method in site.json.</div>' +
            (tilesUrl ? '<div class="rp-empty" style="margin-top:8px;word-break:break-all;font-size:10px;font-family:IBM Plex Mono,monospace">' + tilesUrl + '</div>' : '') +
            '</div>';
    }

    function renderStubPanel(name, key, fallbackScore) {
        const sc = procLayerScore(key, { score: fallbackScore });
        return '<div class="rp-hero">' +
            '<div class="rp-stage-chip">PROCESSING · HEAVY LAYER</div>' +
            '<div class="rp-name">' + name + '</div>' +
            scoreHeroBlock(sc, LAYER_STATUS[key].note) +
            siblingTabs(key) +
            '<div class="rp-section"><div class="rp-empty">Cesium plot for this layer will be added next. Toggle prepares UI and panel.</div></div>';
    }

    const PANEL_RENDERERS = {
        images: renderImagesPanel,
        ortho: renderOrthoPanel,
        dsm: renderDsmPanel,
        dtm: function () { return renderStubPanel('DTM', 'dtm', '71'); },
        mesh: renderMeshPanel,
        pointcloud: renderPointCloudPanel
    };

    function renderRightPanel() {
        const rp = $('rpContent');
        if (!state.focused || !PANEL_RENDERERS[state.focused]) {
            rp.innerHTML = '';
            return;
        }
        rp.innerHTML = PANEL_RENDERERS[state.focused]();

        rp.querySelectorAll('.rp-sibling[data-target]').forEach(function (tab) {
            tab.addEventListener('click', function () {
                const target = tab.dataset.target;
                if (HEAVY_LAYERS.includes(target) && !state.visible[target]) {
                    setHeavyLayer(target);
                } else if (target === 'images' && !state.visible.images) {
                    setImagesVisible(true).catch(console.error);
                }
                focusObject(target);
            });
        });

        rp.querySelectorAll('.image-browser-row[data-img-idx]').forEach(function (row) {
            row.addEventListener('click', function () {
                openImagePreview(parseInt(row.dataset.imgIdx, 10));
            });
        });
    }

    function focusObject(obj) {
        document.querySelectorAll('.lp-item[data-obj]').forEach(function (el) {
            el.classList.toggle('focused', el.dataset.obj === obj);
        });
        state.focused = obj;
        $('rightPanel').style.display = '';
        const rp = $('rpContent');
        rp.classList.add('fading');
        setTimeout(function () {
            renderRightPanel();
            rp.classList.remove('fading');
        }, 120);
    }

    function closeRightPanel() {
        $('rightPanel').style.display = 'none';
        state.focused = null;
        document.querySelectorAll('.lp-item[data-obj]').forEach(function (el) {
            el.classList.remove('focused');
        });
    }

    function setPreviewImage(url) {
        const canvas = document.querySelector('.image-preview-canvas');
        if (!canvas) return;
        let el = canvas.querySelector('img.preview-photo');
        if (!el) {
            el = document.createElement('img');
            el.className = 'preview-photo';
            el.alt = 'Geotagged photo';
            el.style.cssText = 'width:100%;height:100%;object-fit:contain;display:block;position:relative;z-index:1;';
            canvas.appendChild(el);
        }
        el.src = url || '';
        el.onerror = function () {
            el.alt = 'Image failed to load (check S3 CORS)';
        };
    }

    function openImagePreview(idx) {
        const list = getImageList();
        const img = list[idx];
        if (!img) return;
        state.imagePreviewIndex = idx;
        state.imagePreviewOpen = true;
        $('previewId').textContent = img.id;
        $('previewTime').textContent = img.capturedAt || '—';
        const altLabel = img.alt_m != null ? img.alt_m + ' m (EXIF)' : '—';
        $('previewAlt').textContent = altLabel;
        $('previewSharp').textContent = img.sharpness != null ? String(img.sharpness) : '—';
        setPreviewImage(img.url);
        $('previewPrev').disabled = idx <= 0;
        $('previewNext').disabled = idx >= list.length - 1;
        $('imagePreviewOverlay').classList.add('show');
    }

    function closeImagePreview() {
        state.imagePreviewOpen = false;
        $('imagePreviewOverlay').classList.remove('show');
    }

    function updateImagePreview() {
        openImagePreview(state.imagePreviewIndex);
    }

    function wireUi() {
        document.querySelectorAll('.lp-eye[data-toggle]').forEach(function (el) {
            el.innerHTML = EYE_OFF;
            el.addEventListener('click', function (e) {
                e.stopPropagation();
                toggleLayer(el.dataset.toggle);
            });
        });

        document.querySelectorAll('.lp-item[data-obj]').forEach(function (item) {
            item.addEventListener('click', function (e) {
                if (e.target.closest('[data-toggle]')) return;
                const obj = item.dataset.obj;
                if (HEAVY_LAYERS.includes(obj) && !state.visible[obj]) {
                    setHeavyLayer(obj);
                } else if (obj === 'images' && !state.visible.images) {
                    setImagesVisible(true).catch(console.error);
                }
                focusObject(obj);
            });
        });

        const rpClose = $('rpClose');
        if (rpClose) rpClose.addEventListener('click', closeRightPanel);
        const previewClose = $('previewClose');
        if (previewClose) previewClose.addEventListener('click', closeImagePreview);
        const overlay = $('imagePreviewOverlay');
        if (overlay) {
            overlay.addEventListener('click', function (e) {
                if (e.target === overlay) closeImagePreview();
            });
        }
        const previewPrev = $('previewPrev');
        if (previewPrev) {
            previewPrev.addEventListener('click', function () {
                if (state.imagePreviewIndex > 0) {
                    state.imagePreviewIndex--;
                    updateImagePreview();
                }
            });
        }
        const previewNext = $('previewNext');
        if (previewNext) {
            previewNext.addEventListener('click', function () {
                const list = getImageList();
                if (state.imagePreviewIndex < list.length - 1) {
                    state.imagePreviewIndex++;
                    updateImagePreview();
                }
            });
        }
        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape' && state.imagePreviewOpen) closeImagePreview();
        });

        document.querySelectorAll('.tab').forEach(function (tab) {
            tab.addEventListener('click', function () {
                document.querySelectorAll('.tab').forEach(function (t) { t.classList.remove('active'); });
                tab.classList.add('active');
                state.mode = tab.dataset.tab;
                const sceneMode = $('sceneMode');
                if (sceneMode) sceneMode.dataset.mode = state.mode;
            });
        });

        const protoToggle = $('protoToggle');
        const protoNote = $('protoNote');
        if (protoToggle && protoNote) {
            protoToggle.addEventListener('click', function () {
                protoNote.classList.toggle('show');
            });
        }
    }

    async function init(viewerInstance) {
        viewer = viewerInstance;
        if (global.SurveyConfig) SurveyConfig.applySiteLabels();
        try {
            survey = await SurveyOrthoDem.initSurveyContext(viewer);
            global.__processingSurvey = survey;
        } catch (e) {
            console.error('[Processing] initSurveyContext failed:', e);
            showLoadBanner('Processing init failed — ' + (e && e.message ? e.message : e));
            throw e;
        }
        wireUi();
        bindSrRail('.sr-litem[data-proc-layer]');
        if (global.GeotaggedImages) {
            GeotaggedImages.bindPick(viewer, function (img, index) {
                if (!state.visible.images) setImagesVisible(true).catch(console.error);
                focusObject('images');
                openImagePreview(index);
            });
        }
        updateEmptyState();
        global.__processingReady = true;
        console.log('[Processing] survey context ready — toggle layers in left panel');
    }

    function getSurvey() {
        return survey;
    }

    global.ProcessingGlobe = {
        init, state, LAYER_NAMES, HEAVY_LAYERS,
        toggleLayer, syncSrRail, bindSrRail, getSurvey
    };
})(typeof window !== 'undefined' ? window : globalThis);
