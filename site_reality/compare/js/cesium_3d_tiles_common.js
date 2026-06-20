/**
 * Shared 3D Tiles helpers — matches CB-UI production (flat globe, trust tileset.json).
 */
(function (global) {
    'use strict';

    function ensureFlatTerrain(viewer) {
        const Cesium = global.Cesium;
        const flat = new Cesium.EllipsoidTerrainProvider();
        viewer.terrainProvider = flat;
        viewer.scene.terrainProvider = flat;
        viewer.scene.globe.depthTestAgainstTerrain = false;
        viewer.scene.requestRender();
    }

    async function validateTilesetUrl(url) {
        const res = await fetch(url);
        if (!res.ok) {
            throw new Error('tileset.json HTTP ' + res.status + ': ' + url);
        }
        const text = await res.text();
        const trimmed = text.trim();
        if (trimmed.startsWith('<') || trimmed.startsWith('<!')) {
            throw new Error('tileset.json returned HTML (check URL / CORS): ' + url);
        }
        JSON.parse(trimmed);
        return true;
    }

    async function headTilesetExists(url) {
        try {
            const res = await fetch(url, { method: 'HEAD' });
            return res.ok;
        } catch (e) {
            return false;
        }
    }

    global.Cesium3DTilesCommon = {
        ensureFlatTerrain: ensureFlatTerrain,
        validateTilesetUrl: validateTilesetUrl,
        headTilesetExists: headTilesetExists
    };
})(typeof window !== 'undefined' ? window : globalThis);
