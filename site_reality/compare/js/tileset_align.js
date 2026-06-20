/**
 * Ground align 3D Tiles — subtract layer minimum elevation (no DEM sampling).
 */
(function (global) {
    'use strict';

    const tilesetJsonCache = {};

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

    /** 3D Tiles box → 8 corners in local space. */
    function cornersFromTilesBox(box) {
        const cx = box[0];
        const cy = box[1];
        const cz = box[2];
        const hx = [box[3], box[4], box[5]];
        const hy = [box[6], box[7], box[8]];
        const hz = [box[9], box[10], box[11]];
        const out = [];
        for (let sx = -1; sx <= 1; sx += 2) {
            for (let sy = -1; sy <= 1; sy += 2) {
                for (let sz = -1; sz <= 1; sz += 2) {
                    out.push([
                        cx + sx * hx[0] + sy * hy[0] + sz * hz[0],
                        cy + sx * hx[1] + sy * hy[1] + sz * hz[1],
                        cz + sx * hx[2] + sy * hy[2] + sz * hz[2]
                    ]);
                }
            }
        }
        return out;
    }

    function collectBoxes(node, boxes) {
        if (!node) return;
        if (node.boundingVolume && node.boundingVolume.box) {
            boxes.push(node.boundingVolume.box);
        }
        (node.children || []).forEach(function (ch) {
            collectBoxes(ch, boxes);
        });
    }

    function minHeightFromTilesetJson(json) {
        const Cesium = global.Cesium;
        if (!json || !json.root) return undefined;

        const transform = json.root.transform
            ? Cesium.Matrix4.fromArray(json.root.transform)
            : Cesium.Matrix4.IDENTITY;

        const boxes = [];
        collectBoxes(json.root, boxes);
        if (!boxes.length) return undefined;

        const scratch = new Cesium.Cartesian3();
        let minH = Infinity;

        boxes.forEach(function (box) {
            cornersFromTilesBox(box).forEach(function (corner) {
                Cesium.Matrix4.multiplyByPoint(
                    transform,
                    new Cesium.Cartesian3(corner[0], corner[1], corner[2]),
                    scratch
                );
                const h = Cesium.Cartographic.fromCartesian(scratch).height;
                if (h < minH) minH = h;
            });
        });

        return isFinite(minH) ? minH : undefined;
    }

    async function fetchTilesetJson(url) {
        if (tilesetJsonCache[url]) return tilesetJsonCache[url];
        const res = await fetch(url);
        if (!res.ok) throw new Error('tileset.json ' + res.status);
        const json = await res.json();
        tilesetJsonCache[url] = json;
        return json;
    }

    async function minHeightFromTilesetUrl(url) {
        const json = await fetchTilesetJson(url);
        return minHeightFromTilesetJson(json);
    }

    /** Lowest point from loaded tileset bounding sphere (world). */
    function minHeightFromBoundingSphere(tileset) {
        const Cesium = global.Cesium;
        const bs = tileset.boundingSphere;
        const normal = Cesium.Ellipsoid.WGS84.geodeticSurfaceNormal(
            bs.center,
            new Cesium.Cartesian3()
        );
        const along = Cesium.Cartesian3.multiplyByScalar(
            normal,
            bs.radius,
            new Cesium.Cartesian3()
        );
        const bottom = Cesium.Cartesian3.subtract(bs.center, along, new Cesium.Cartesian3());
        return Cesium.Cartographic.fromCartesian(bottom).height;
    }

    async function resolveMinElevationMeters(tileset, tilesetUrl) {
        const fromSphere = minHeightFromBoundingSphere(tileset);
        let fromJson;

        if (tilesetUrl) {
            try {
                fromJson = await minHeightFromTilesetUrl(tilesetUrl);
            } catch (e) {
                console.warn('[TilesetAlign] tileset.json min height:', e);
            }
        }

        let minElev = fromSphere;
        if (CesiumDefined(fromJson)) {
            minElev = CesiumDefined(fromSphere) ? Math.min(fromSphere, fromJson) : fromJson;
        }

        return { minElev: minElev, fromJson: fromJson, fromSphere: fromSphere };
    }

    function CesiumDefined(v) {
        return v !== undefined && v !== null && isFinite(v);
    }

    /**
     * Reset model matrix and shift so minimum elevation → ellipsoid h≈0.
     */
    async function alignSubtractMinElevation(viewer, tileset, options) {
        const Cesium = global.Cesium;
        options = options || {};
        const a = options.alignment || {};
        const lift = options.surfaceLiftM != null
            ? options.surfaceLiftM
            : (a.minElevationLiftM != null ? a.minElevationLiftM : 0);
        const manual = a.manualOffsetM || 0;

        const resolved = await resolveMinElevationMeters(tileset, options.tilesetUrl);
        const minElev = resolved.minElev;
        const offsetM = -minElev + lift + manual;

        tileset.modelMatrix = Cesium.Matrix4.clone(Cesium.Matrix4.IDENTITY);

        if (Math.abs(offsetM) >= 0.05) {
            applyVerticalOffsetMeters(tileset, offsetM);
        }

        if (viewer && !viewer.isDestroyed()) {
            viewer.scene.requestRender();
        }

        const tag = options.logTag || 'TilesetAlign';
        console.log(
            '[' + tag + '] min elev ' + minElev.toFixed(2) + ' m (json=' +
            (resolved.fromJson != null ? resolved.fromJson.toFixed(2) : '—') +
            ', sphere=' + resolved.fromSphere.toFixed(2) + ') → offset ' + offsetM.toFixed(2) + ' m'
        );

        return { offsetM: offsetM, minElev: minElev };
    }

    /** Re-align once more after tiles refine (bounding volume may tighten). */
    function watchAndRealign(viewer, tileset, options) {
        if (!tileset) return;
        setTimeout(function () {
            if (!tileset || tileset.isDestroyed()) return;
            alignSubtractMinElevation(viewer, tileset, options).catch(function (e) {
                console.warn('[TilesetAlign] re-align:', e);
            });
        }, 2500);
    }

    global.TilesetAlign = {
        applyVerticalOffsetMeters: applyVerticalOffsetMeters,
        minHeightFromBoundingSphere: minHeightFromBoundingSphere,
        minHeightFromTilesetUrl: minHeightFromTilesetUrl,
        alignSubtractMinElevation: alignSubtractMinElevation,
        watchAndRealign: watchAndRealign
    };
})(typeof window !== 'undefined' ? window : globalThis);
