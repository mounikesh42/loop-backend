/**
 * 3D mesh 3D Tiles — CB-UI production style (MinIO 3dmodel_3dtiles).
 * Trusts georeferencing in tileset.json; modelMatrix = IDENTITY; flat ellipsoid.
 */
(function (global) {
    'use strict';

    function modelCfg() {
        return global.SurveyConfig.get().model3d;
    }

    function alignCfg() {
        return global.SurveyConfig.get().alignment;
    }

    function shouldAlign(opts) {
        if (opts.alignToGround === false) return false;
        if (opts.alignToGround === true) return true;
        const method = (opts.alignMethod || alignCfg().method || 'trustTileset');
        return method !== 'trustTileset';
    }

    let tileset = null;
    let metadata = null;
    let appliedOffsetM = 0;

    async function fetchMetadata() {
        if (metadata) return metadata;
        try {
            const res = await fetch(modelCfg().metadataUrl);
            if (res.ok) metadata = await res.json();
        } catch (e) {
            console.warn('[Model3DTiles] metadata:', e);
        }
        return metadata;
    }

    function unload(viewer) {
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

    async function alignToGround(viewer, opts) {
        opts = opts || {};
        if (!tileset || !global.TilesetAlign) return 0;

        const a = alignCfg();
        const method = opts.alignMethod || a.method || 'trustTileset';

        if (method === 'demSample' || opts.sampleDemElevation === true) {
            return alignToDemTerrain(viewer, opts);
        }
        if (method === 'minElevation') {
            const result = await global.TilesetAlign.alignSubtractMinElevation(viewer, tileset, {
                alignment: a,
                surfaceLiftM: a.minElevationLiftM,
                tilesetUrl: modelCfg().tilesetUrl,
                logTag: 'Model3DTiles'
            });
            appliedOffsetM = result.offsetM;
            return result.offsetM;
        }
        return 0;
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
            if (opts.showTerrainMesh === true) {
                opts.survey.useDemTerrain();
                viewer.scene.globe.depthTestAgainstTerrain = true;
            } else {
                global.Cesium3DTilesCommon.ensureFlatTerrain(viewer);
            }
        }
        if (!tp || tp instanceof Cesium.EllipsoidTerrainProvider || !global.TilesetAlign) {
            return 0;
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
        const terrainCenter = sampled[0].height;
        if (!Cesium.defined(terrainCenter) || isNaN(terrainCenter)) return 0;

        const wasH = centerCarto.height;
        const bottomFactor = a.model3dBottomRadiusFactor != null
            ? a.model3dBottomRadiusFactor
            : a.bottomRadiusFactor;
        const targetCenterH =
            terrainCenter + radiusM * bottomFactor + a.terrainSurfaceLiftM;
        const offsetM = targetCenterH - wasH + a.manualOffsetM;
        if (Math.abs(offsetM) < 0.2) return 0;

        tileset.modelMatrix = Cesium.Matrix4.clone(Cesium.Matrix4.IDENTITY);
        global.TilesetAlign.applyVerticalOffsetMeters(tileset, offsetM);
        appliedOffsetM = offsetM;
        viewer.scene.requestRender();
        console.log('[Model3DTiles] DEM align: Δ' + offsetM.toFixed(2) + ' m');
        return offsetM;
    }

    function trustTilesetMatrix() {
        const Cesium = global.Cesium;
        tileset.modelMatrix = Cesium.Matrix4.clone(Cesium.Matrix4.IDENTITY);
        appliedOffsetM = 0;
        console.log('[Model3DTiles] modelMatrix=IDENTITY (trust tileset.json georeferencing)');
    }

    async function load(viewer, opts) {
        const Cesium = global.Cesium;
        const a = alignCfg();
        const url = modelCfg().tilesetUrl;
        opts = opts || {};

        unload(viewer);
        await fetchMetadata();

        await global.Cesium3DTilesCommon.validateTilesetUrl(url);

        global.Cesium3DTilesCommon.ensureFlatTerrain(viewer);

        tileset = new Cesium.Cesium3DTileset({
            url: url,
            skipLevelOfDetail: true,
            baseScreenSpaceError: 1024,
            skipScreenSpaceErrorFactor: 16,
            skipLevels: 1,
            immediatelyLoadDesiredLevelOfDetail: true,
            loadSiblings: true,
            maximumScreenSpaceError: a.model3dMaxScreenSpaceError || 16,
            dynamicScreenSpaceError: true,
            dynamicScreenSpaceErrorDensity: 0.002,
            dynamicScreenSpaceErrorFactor: 4.0
        });

        viewer.scene.primitives.add(tileset);
        tileset.tileFailed.addEventListener(function (_tile, error) {
            console.warn('[Model3DTiles] tile failed', error);
        });

        try {
            await tileset.readyPromise;
        } catch (e) {
            unload(viewer);
            throw e;
        }

        trustTilesetMatrix();

        if (shouldAlign(opts)) {
            await alignToGround(viewer, Object.assign({ survey: opts.survey }, opts));
        }

        viewer.scene.requestRender();
        console.log('[Model3DTiles] loaded (CB-UI style)', url);
        return tileset;
    }

    async function flyTo(viewer, duration) {
        if (!tileset || viewer.isDestroyed()) return;
        const Cesium = global.Cesium;
        const dur = duration != null ? duration : 1.2;
        const bs = tileset.boundingSphere;
        const range = Math.max(bs.radius * 2.2, 900);
        try {
            await viewer.flyTo(tileset, {
                duration: dur,
                offset: new Cesium.HeadingPitchRange(
                    0,
                    Cesium.Math.toRadians(-55),
                    range
                )
            });
        } catch (e) {
            console.warn('[Model3DTiles] flyTo:', e);
        }
    }

    global.Model3DTiles = {
        load: load,
        unload: unload,
        flyTo: flyTo,
        alignToGround: alignToGround,
        get tileset() { return tileset; },
        get metadata() { return metadata; },
        get appliedOffsetM() { return appliedOffsetM; }
    };
})(typeof window !== 'undefined' ? window : globalThis);
