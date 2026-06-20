/**
 * Load geotagged image manifest and plot Cesium points (clamped to terrain).
 */
(function (global) {
    'use strict';

    function geoCfg() {
        return global.SurveyConfig.get().geotagged;
    }

    function alignCfg() {
        return global.SurveyConfig.get().alignment;
    }

    const STATE_COLORS = {
        good: '#7CB89A',
        warn: '#D2AA4E',
        crit: '#C86262'
    };

    const SAMPLE_BATCH = 80;

    let dataSource = null;
    let manifest = null;
    let images = [];
    let pickHandler = null;
    let onPickCallback = null;
    let entityByIndex = [];

    function resolveImageUrl(img, m) {
        if (img.url) return img.url;
        const base = (m && m.s3BaseUrl) || geoCfg().s3ImageBase;
        const fn = img.filename || (img.id + '.JPG');
        return base + (base.endsWith('/') ? '' : '/') + fn;
    }

    function applyImageScores(list) {
        if (!global.SurveyConfig || typeof SurveyConfig.getImageScoreEntry !== 'function') {
            return;
        }
        list.forEach(function (img) {
            try {
                const entry = SurveyConfig.getImageScoreEntry(img.id);
                SurveyConfig.applyScoreFields(img, entry);
            } catch (e) { /* config not loaded */ }
        });
    }

    function normalizeManifest(raw) {
        const m = raw || {};
        const list = m.images || [];
        list.forEach(function (img) {
            img.url = resolveImageUrl(img, m);
        });
        applyImageScores(list);
        return {
            meta: m,
            images: list,
            bounds: m.bounds
        };
    }

    async function loadManifest(urls) {
        const list = urls || geoCfg().manifestUrls;
        let lastErr = null;
        for (let i = 0; i < list.length; i++) {
            try {
                const res = await fetch(list[i]);
                if (!res.ok) throw new Error(res.status + ' ' + res.statusText);
                const raw = await res.json();
                const norm = normalizeManifest(raw);
                manifest = norm.meta;
                images = norm.images;
                console.log('[GeotaggedImages] loaded', images.length, 'from', list[i]);
                return norm;
            } catch (e) {
                lastErr = e;
                console.warn('[GeotaggedImages] failed', list[i], e);
            }
        }
        throw lastErr || new Error('No manifest loaded');
    }

    function stateColor(state) {
        return STATE_COLORS[state] || STATE_COLORS.good;
    }

    function hasMeshTerrain(viewer) {
        const Cesium = global.Cesium;
        const tp = viewer.scene.globe.terrainProvider;
        return tp && !(tp instanceof Cesium.EllipsoidTerrainProvider);
    }

    function getProp(entity, key) {
        if (!entity || !entity.properties) return undefined;
        const p = entity.properties[key];
        if (p == null) return undefined;
        if (typeof p.getValue === 'function') {
            return p.getValue(global.Cesium.JulianDate.now());
        }
        return p;
    }

    function entityFromPickedObjects(pickedList) {
        if (!pickedList || !pickedList.length) return null;
        for (let i = 0; i < pickedList.length; i++) {
            const obj = pickedList[i];
            if (!obj || !obj.id) continue;
            const ent = obj.id;
            if (getProp(ent, 'geotagIndex') != null) return ent;
            if (ent.id && String(ent.id).indexOf('geotag-') === 0) return ent;
        }
        return null;
    }

    function plot(viewer, imageList) {
        const Cesium = global.Cesium;
        clearDataOnly(viewer);
        dataSource = new Cesium.CustomDataSource('geotagged-images');
        entityByIndex = [];

        imageList.forEach(function (img, index) {
            const color = Cesium.Color.fromCssColorString(stateColor(img.state));
            const entity = dataSource.entities.add({
                id: 'geotag-' + img.id,
                position: Cesium.Cartesian3.fromDegrees(img.lon, img.lat),
                point: {
                    pixelSize: 8,
                    color: color,
                    outlineColor: Cesium.Color.fromCssColorString('#020308'),
                    outlineWidth: 2,
                    disableDepthTestDistance: Number.POSITIVE_INFINITY,
                    heightReference: Cesium.HeightReference.CLAMP_TO_GROUND
                },
                properties: new Cesium.PropertyBag({
                    geotagIndex: index,
                    geotagId: img.id,
                    imageUrl: img.url,
                    exifAlt_m: img.alt_m != null ? img.alt_m : undefined,
                    terrainHeight_m: undefined
                })
            });
            entityByIndex[index] = entity;
        });

        viewer.dataSources.add(dataSource);
        viewer.scene.requestRender();
        return dataSource;
    }

    /**
     * Sample DEM/CTOD terrain heights and place pins on the surface.
     * Falls back to CLAMP_TO_GROUND when terrain is flat or sampling fails.
     */
    async function clampToTerrain(viewer) {
        const Cesium = global.Cesium;
        if (!dataSource || !images.length) return;

        if (!hasMeshTerrain(viewer)) {
            images.forEach(function (img, index) {
                const entity = entityByIndex[index];
                if (!entity) return;
                entity.position = Cesium.Cartesian3.fromDegrees(img.lon, img.lat);
                entity.point.heightReference = Cesium.HeightReference.CLAMP_TO_GROUND;
            });
            viewer.scene.requestRender();
            console.log('[GeotaggedImages] clamp: flat globe — CLAMP_TO_GROUND');
            return;
        }

        const terrain = viewer.scene.globe.terrainProvider;
        let updated = 0;

        for (let start = 0; start < images.length; start += SAMPLE_BATCH) {
            const slice = images.slice(start, start + SAMPLE_BATCH);
            const cartos = slice.map(function (img) {
                return Cesium.Cartographic.fromDegrees(img.lon, img.lat);
            });

            try {
                const sampled = await Cesium.sampleTerrainMostDetailed(terrain, cartos);
                sampled.forEach(function (c, j) {
                    const index = start + j;
                    const entity = entityByIndex[index];
                    const img = images[index];
                    if (!entity || !img) return;

                    let h = Cesium.defined(c.height) && !isNaN(c.height) ? c.height : 0;
                    entity.position = Cesium.Cartesian3.fromRadians(
                        c.longitude,
                        c.latitude,
                        h + alignCfg().geotagTerrainOffsetM
                    );
                    entity.point.heightReference = Cesium.HeightReference.NONE;

                    updated++;
                });
            } catch (e) {
                console.warn('[GeotaggedImages] terrain sample batch failed', start, e);
                slice.forEach(function (img, j) {
                    const index = start + j;
                    const entity = entityByIndex[index];
                    if (!entity) return;
                    entity.position = Cesium.Cartesian3.fromDegrees(img.lon, img.lat);
                    entity.point.heightReference = Cesium.HeightReference.CLAMP_TO_GROUND;
                });
            }
        }

        viewer.scene.requestRender();
        console.log('[GeotaggedImages] clamped', updated, 'points to terrain mesh');
    }

    function clearDataOnly(viewer) {
        if (dataSource && viewer) {
            viewer.dataSources.remove(dataSource, true);
        }
        dataSource = null;
        entityByIndex = [];
    }

    function clear(viewer) {
        clearDataOnly(viewer);
        if (pickHandler) {
            pickHandler.destroy();
            pickHandler = null;
        }
        onPickCallback = null;
    }

    function setVisible(show) {
        if (dataSource) dataSource.show = show;
    }

    function flyToBounds(viewer) {
        const Cesium = global.Cesium;
        if (!manifest || !manifest.bounds || manifest.bounds.length < 4) return;
        const b = manifest.bounds;
        const rect = Cesium.Rectangle.fromDegrees(b[0], b[1], b[2], b[3]);
        viewer.camera.flyTo({ destination: rect, duration: 0.8 });
    }

    function bindPick(viewer, onPick) {
        const Cesium = global.Cesium;
        onPickCallback = onPick;
        if (pickHandler) pickHandler.destroy();
        pickHandler = new Cesium.ScreenSpaceEventHandler(viewer.scene.canvas);
        pickHandler.setInputAction(function (movement) {
            if (!isLayerVisible()) return;
            const scene = viewer.scene;
            let entity = null;
            const picked = scene.pick(movement.position);
            if (Cesium.defined(picked)) {
                entity = entityFromPickedObjects([picked]);
            }
            if (!entity) {
                entity = entityFromPickedObjects(scene.drillPick(movement.position, 16));
            }
            if (!entity) return;

            let index = getProp(entity, 'geotagIndex');
            if (index == null && entity.id) {
                const idStr = String(entity.id).replace(/^geotag-/, '');
                index = images.findIndex(function (im) { return im.id === idStr; });
            }
            if (index == null || index < 0 || index >= images.length) return;
            onPickCallback(images[index], index);
        }, Cesium.ScreenSpaceEventType.LEFT_CLICK);
    }

    function isLayerVisible() {
        return dataSource && dataSource.show !== false;
    }

    global.GeotaggedImages = {
        loadManifest,
        plot,
        clampToTerrain,
        clear,
        setVisible,
        flyToBounds,
        bindPick,
        get images() { return images; },
        get manifest() { return manifest; }
    };
})(typeof window !== 'undefined' ? window : globalThis);
