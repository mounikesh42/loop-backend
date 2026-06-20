/**
 * Shared TiTiler orthomosaic + CTOD DEM loader for Cesium viewers.
 * URLs and rasters come from survey/site.json via SurveyConfig.
 */
(function (global) {
    'use strict';

    function cfg() {
        return global.SurveyConfig.get();
    }

    function orthoCog() {
        return cfg().rasters.orthoCog;
    }

    function demCog() {
        return cfg().rasters.demCog;
    }

    function terrainConfig() {
        return cfg().terrain;
    }

    let TITILER = '';
    let CTOD = '';

    function isLocalDevHost() {
        const h = global.location.hostname;
        return (
            global.location.protocol !== 'file:' &&
            (h === 'localhost' || h === '127.0.0.1' || h === '[::1]' || h === '::1')
        );
    }

    async function initServiceBases() {
        const c = cfg();
        TITILER = c.services.titiler;
        CTOD = c.services.ctod;
        if (!isLocalDevHost()) return;
        TITILER = global.location.origin + '/titiler-proxy';
        CTOD = global.location.origin + '/ctod-proxy';
        console.log('[SurveyOrthoDem] using local proxy', TITILER);
    }

    function rewriteServiceUrl(url) {
        if (!url) return url;
        const c = cfg();
        let u = String(url).replace(/^http:\/\//i, 'https://');
        const pairs = [
            [c.services.titiler, TITILER],
            ['http://titiler2.cbstack.online', TITILER],
            [c.services.ctod, CTOD],
            ['http://ctod2.cbstack.online', CTOD]
        ];
        for (const [from, to] of pairs) {
            if (u.startsWith(from)) return to + u.slice(from.length);
        }
        return u;
    }

    function readStatsMin(stats) {
        if (!stats || typeof stats !== 'object') return undefined;
        const b =
            stats['1'] || stats.b1 || stats.B1 ||
            Object.values(stats).find((v) => v && typeof v === 'object' && 'min' in v);
        return b && typeof b.min === 'number' ? b.min : undefined;
    }

    async function resolveNoDataForDem(cogUrl) {
        let noDataValue = 0;
        try {
            const info = await (await fetch(
                TITILER + '/cog/info?url=' + encodeURIComponent(cogUrl)
            )).json();
            const fromInfo =
                info.nodata_value ?? info.nodata ??
                (info.profile && info.profile.nodata) ??
                (info.bands && info.bands[0] && info.bands[0].nodata);
            if (fromInfo != null && isFinite(Number(fromInfo))) {
                noDataValue = Number(fromInfo);
            }
        } catch (e) {
            console.warn('cog/info:', e);
        }
        try {
            const statsData = await (await fetch(
                TITILER + '/cog/statistics?url=' + encodeURIComponent(cogUrl)
            )).json();
            const statsMin = readStatsMin(statsData);
            if (statsMin !== undefined && isFinite(statsMin)) {
                noDataValue = Math.floor(statsMin);
            }
        } catch (e) {
            console.warn('cog/statistics:', e);
        }
        return Math.floor(isFinite(noDataValue) ? noDataValue : 0);
    }

    function padBounds(west, south, east, north, ratio) {
        const Cesium = global.Cesium;
        const dLon = Math.max(east - west, 1e-9) * ratio + 0.0005;
        const dLat = Math.max(north - south, 1e-9) * ratio + 0.0005;
        return Cesium.Rectangle.fromDegrees(
            west - dLon, south - dLat, east + dLon, north + dLat
        );
    }

    function createViewer(containerId) {
        const Cesium = global.Cesium;
        Cesium.Ion.defaultAccessToken = '';
        const viewer = new Cesium.Viewer(containerId, {
            animation: false,
            timeline: false,
            baseLayerPicker: false,
            geocoder: false,
            homeButton: true,
            navigationHelpButton: false,
            sceneModePicker: false,
            imageryProvider: false,
            terrainProvider: new Cesium.EllipsoidTerrainProvider(),
            shouldAnimate: false,
            infoBox: false,
            selectionIndicator: false
        });
        viewer.imageryLayers.removeAll();
        viewer.scene.globe.baseColor = Cesium.Color.fromCssColorString('#1a2430');
        viewer.scene.globe.enableLighting = false;
        viewer.scene.globe.depthTestAgainstTerrain = true;
        return viewer;
    }

    /**
     * @param {Cesium.Viewer} viewer
     * @param {{ onOrthoStatus?: Function, onDemStatus?: Function }} hooks
     */
    async function loadSurveyLayers(viewer, hooks) {
        const Cesium = global.Cesium;
        const onOrtho = hooks.onOrthoStatus || function () {};
        const onDem = hooks.onDemStatus || function () {};

        const state = {
            satelliteImageryLayer: null,
            orthoImageryLayer: null,
            ctodTerrainProvider: null,
            surveyRectangle: null
        };

        function setTerrain(provider) {
            viewer.terrainProvider = provider;
            viewer.scene.terrainProvider = provider;
            viewer.scene.globe.depthTestAgainstTerrain =
                !(provider instanceof Cesium.EllipsoidTerrainProvider);
            viewer.scene.requestRender();
        }

        async function loadCtodTerrain(demUrl, noDataValue) {
            const enc = encodeURIComponent(demUrl);
            const terrainUrl =
                CTOD + '/tiles/dynamic?minZoom=' + terrainConfig().minZoom +
                '&maxZoom=' + terrainConfig().maxZoom + '&noData=' + noDataValue +
                '&cog=' + enc + '&skipCache=' + terrainConfig().skipCache +
                '&meshingMethod=' + terrainConfig().meshingMethod;

            const provider = new Cesium.CesiumTerrainProvider({
                url: terrainUrl,
                requestVertexNormals: true
            });
            provider.errorEvent.addEventListener(function (err) {
                console.error('Terrain error:', err);
                onDem('DEM: tile error — see console', true);
                setTerrain(new Cesium.EllipsoidTerrainProvider());
            });
            await provider.readyPromise;
            state.ctodTerrainProvider = provider;
            return provider;
        }

        async function loadTitilerOrtho() {
            const params = new URLSearchParams({
                url: orthoCog(),
                tile_format: 'png',
                minzoom: '10'
            });
            const tileJson = await (
                await fetch(TITILER + '/cog/WebMercatorQuad/tilejson.json?' + params)
            ).json();
            const bounds = tileJson.bounds;
            if (!bounds || bounds.length < 4) throw new Error('TileJSON missing bounds');

            const rectangle = Cesium.Rectangle.fromDegrees(
                bounds[0], bounds[1], bounds[2], bounds[3]
            );
            const imageryProvider = new Cesium.UrlTemplateImageryProvider({
                url: rewriteServiceUrl(tileJson.tiles[0]),
                minimumLevel: tileJson.minzoom ?? 0,
                maximumLevel: tileJson.maxzoom ?? 22,
                tileWidth: 256,
                tileHeight: 256,
                tilingScheme: new Cesium.WebMercatorTilingScheme({
                    ellipsoid: Cesium.Ellipsoid.WGS84
                }),
                rectangle
            });
            state.orthoImageryLayer = viewer.imageryLayers.addImageryProvider(imageryProvider);
            onOrtho('Orthomosaic: loaded (TiTiler)', false);
            return rectangle;
        }

        function addSatelliteBasemap() {
            const provider = new Cesium.UrlTemplateImageryProvider({
                url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
                maximumLevel: 19,
                credit: 'Esri World Imagery'
            });
            state.satelliteImageryLayer = viewer.imageryLayers.addImageryProvider(provider, 0);
        }

        await initServiceBases();
        addSatelliteBasemap();
        onOrtho('Orthomosaic: loading…', false);
        onDem('DEM: resolving noData…', false);

        const noData = await resolveNoDataForDem(demCog());
        let orthoRect = null;
        try {
            orthoRect = await loadTitilerOrtho();
        } catch (orthoErr) {
            console.error(orthoErr);
            onOrtho('Orthomosaic: failed — ' + (orthoErr.message || orthoErr), true);
        }

        if (orthoRect) {
            state.surveyRectangle = padBounds(
                Cesium.Math.toDegrees(orthoRect.west),
                Cesium.Math.toDegrees(orthoRect.south),
                Cesium.Math.toDegrees(orthoRect.east),
                Cesium.Math.toDegrees(orthoRect.north),
                0.06
            );
            viewer.camera.setView({ destination: state.surveyRectangle });
        }

        onDem('DEM: loading CTOD (noData=' + noData + ')…', false);
        try {
            await loadCtodTerrain(demCog(), noData);
            setTerrain(state.ctodTerrainProvider);
            onDem('DEM: loaded (CTOD terrain mesh)', false);
        } catch (err) {
            console.error(err);
            onDem('DEM: failed — ' + (err.message || err), true);
            setTerrain(new Cesium.EllipsoidTerrainProvider());
        }

        return {
            state,
            setTerrain,
            setSatelliteVisible(v) {
                if (state.satelliteImageryLayer) state.satelliteImageryLayer.show = v;
            },
            setOrthoVisible(v) {
                if (state.orthoImageryLayer) state.orthoImageryLayer.show = v;
            },
            setDemVisible(v) {
                if (v && state.ctodTerrainProvider) {
                    setTerrain(state.ctodTerrainProvider);
                    onDem('DEM: loaded (CTOD terrain mesh)', false);
                } else {
                    setTerrain(new Cesium.EllipsoidTerrainProvider());
                    onDem(
                        state.ctodTerrainProvider ? 'DEM: hidden (flat globe)' : 'DEM: not loaded',
                        false
                    );
                }
            },
            setOrthoAlpha(alpha) {
                if (state.orthoImageryLayer) state.orthoImageryLayer.alpha = alpha;
            }
        };
    }

    /**
     * Lazy survey context — satellite always; ortho/DEM loaded on demand (processing stage).
     */
    async function initSurveyContext(viewer) {
        const Cesium = global.Cesium;
        const state = {
            satelliteImageryLayer: null,
            demImageryLayer: null,
            orthoImageryLayer: null,
            ctodTerrainProvider: null,
            surveyRectangle: null,
            noDataValue: null,
            demRescale: null,
            orthoReady: false,
            demTerrainReady: false,
            demImageryReady: false
        };

        function setTerrain(provider) {
            viewer.terrainProvider = provider;
            viewer.scene.terrainProvider = provider;
            viewer.scene.globe.depthTestAgainstTerrain =
                !(provider instanceof Cesium.EllipsoidTerrainProvider);
            viewer.scene.requestRender();
        }

        function addSatelliteBasemap() {
            const provider = new Cesium.UrlTemplateImageryProvider({
                url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
                maximumLevel: 19,
                credit: 'Esri World Imagery'
            });
            state.satelliteImageryLayer = viewer.imageryLayers.addImageryProvider(provider, 0);
        }

        await initServiceBases();
        addSatelliteBasemap();
        state.noDataValue = await resolveNoDataForDem(demCog());

        function stackImageryOrder() {
            if (state.demImageryLayer) {
                viewer.imageryLayers.raiseToTop(state.demImageryLayer);
            }
            if (state.orthoImageryLayer) {
                viewer.imageryLayers.raiseToTop(state.orthoImageryLayer);
            }
            viewer.scene.requestRender();
        }

        async function getDemRescale() {
            if (state.demRescale) return state.demRescale;
            let min = 500;
            let max = 700;
            try {
                const statsData = await (
                    await fetch(TITILER + '/cog/statistics?url=' + encodeURIComponent(demCog()))
                ).json();
                const statsMin = readStatsMin(statsData);
                const band = statsData.b1 || Object.values(statsData).find(
                    (v) => v && typeof v === 'object' && 'max' in v
                );
                if (band && isFinite(band.min) && isFinite(band.max) && band.max > band.min) {
                    min = band.min;
                    max = band.max;
                } else if (statsMin !== undefined && isFinite(statsMin)) {
                    min = statsMin;
                    max = statsMin + 200;
                }
            } catch (e) {
                console.warn('[SurveyOrthoDem] DEM statistics:', e);
            }
            state.demRescale = [min, max];
            return state.demRescale;
        }

        async function ensureDemImagery() {
            if (state.demImageryReady) {
                stackImageryOrder();
                return true;
            }
            const [min, max] = await getDemRescale();
            const params = new URLSearchParams({
                url: demCog(),
                tile_format: 'png',
                rescale: min + ',' + max,
                colormap_name: 'terrain'
            });
            const tileJson = await (
                await fetch(TITILER + '/cog/WebMercatorQuad/tilejson.json?' + params)
            ).json();
            const bounds = tileJson.bounds;
            if (!bounds || bounds.length < 4) throw new Error('DEM TileJSON missing bounds');

            const imageryProvider = new Cesium.UrlTemplateImageryProvider({
                url: rewriteServiceUrl(tileJson.tiles[0]),
                minimumLevel: tileJson.minzoom ?? 0,
                maximumLevel: tileJson.maxzoom ?? 22,
                tileWidth: 256,
                tileHeight: 256,
                tilingScheme: new Cesium.WebMercatorTilingScheme({
                    ellipsoid: Cesium.Ellipsoid.WGS84
                }),
                rectangle: Cesium.Rectangle.fromDegrees(bounds[0], bounds[1], bounds[2], bounds[3])
            });
            state.demImageryLayer = viewer.imageryLayers.addImageryProvider(imageryProvider);
            state.demImageryLayer.alpha = 1;
            state.demImageryReady = true;
            if (!state.surveyRectangle) {
                state.surveyRectangle = padBounds(
                    bounds[0], bounds[1], bounds[2], bounds[3], terrainConfig().padBoundsRatio
                );
            }
            stackImageryOrder();
            return true;
        }

        async function ensureOrtho() {
            if (state.orthoReady) return true;
            const params = new URLSearchParams({
                url: orthoCog(),
                tile_format: 'png',
                minzoom: '10'
            });
            const tileJson = await (
                await fetch(TITILER + '/cog/WebMercatorQuad/tilejson.json?' + params)
            ).json();
            const bounds = tileJson.bounds;
            if (!bounds || bounds.length < 4) throw new Error('TileJSON missing bounds');
            const rectangle = Cesium.Rectangle.fromDegrees(
                bounds[0], bounds[1], bounds[2], bounds[3]
            );
            const imageryProvider = new Cesium.UrlTemplateImageryProvider({
                url: rewriteServiceUrl(tileJson.tiles[0]),
                minimumLevel: tileJson.minzoom ?? 0,
                maximumLevel: tileJson.maxzoom ?? 22,
                tileWidth: 256,
                tileHeight: 256,
                tilingScheme: new Cesium.WebMercatorTilingScheme({
                    ellipsoid: Cesium.Ellipsoid.WGS84
                }),
                rectangle
            });
            state.orthoImageryLayer = viewer.imageryLayers.addImageryProvider(imageryProvider);
            state.orthoReady = true;
            if (!state.surveyRectangle) {
                state.surveyRectangle = padBounds(
                    bounds[0], bounds[1], bounds[2], bounds[3], 0.06
                );
                viewer.camera.setView({ destination: state.surveyRectangle });
            }
            stackImageryOrder();
            return true;
        }

        async function ensureDemTerrain() {
            if (state.demTerrainReady) return true;
            const enc = encodeURIComponent(demCog());
            const noData = state.noDataValue ?? 0;
            const tc = terrainConfig();
            const terrainUrl =
                CTOD + '/tiles/dynamic?minZoom=' + tc.minZoom +
                '&maxZoom=' + tc.maxZoom + '&noData=' + noData +
                '&cog=' + enc + '&skipCache=' + tc.skipCache +
                '&meshingMethod=' + tc.meshingMethod;
            const provider = new Cesium.CesiumTerrainProvider({
                url: terrainUrl,
                requestVertexNormals: true
            });
            await provider.readyPromise;
            state.ctodTerrainProvider = provider;
            state.demTerrainReady = true;
            return true;
        }

        function useFlatGlobe() {
            setTerrain(new Cesium.EllipsoidTerrainProvider());
        }

        function useDemTerrain() {
            if (state.ctodTerrainProvider) setTerrain(state.ctodTerrainProvider);
        }

        function setSatelliteVisible(v) {
            if (state.satelliteImageryLayer) state.satelliteImageryLayer.show = v;
        }

        function setOrthoVisible(v) {
            if (state.orthoImageryLayer) state.orthoImageryLayer.show = v;
        }

        function setOrthoAlpha(a) {
            if (state.orthoImageryLayer) state.orthoImageryLayer.alpha = a;
        }

        function setDemImageryVisible(v) {
            if (state.demImageryLayer) state.demImageryLayer.show = v;
        }

        function setDemImageryAlpha(a) {
            if (state.demImageryLayer) state.demImageryLayer.alpha = a;
        }

        /** Toggle CTOD terrain mesh (flat globe when off). */
        async function setDemVisible(v) {
            if (v) {
                await ensureDemTerrain();
                useDemTerrain();
            } else {
                useFlatGlobe();
            }
        }

        /**
         * Stack (bottom → top): satellite → CTOD DEM terrain mesh → DEM imagery → ortho.
         * DEM terrain + imagery only when useTerrain / useDemImagery; ortho only when useOrtho.
         */
        async function applyElevationStack(opts) {
            const useTerrain = !!opts.useTerrain;
            const useDemImagery = !!opts.useDemImagery;
            const useOrtho = !!opts.useOrtho;

            if (!useTerrain && !useDemImagery && !useOrtho) {
                useFlatGlobe();
                setDemImageryVisible(false);
                setOrthoVisible(false);
                setSatelliteVisible(true);
                return;
            }

            if (useTerrain) {
                await ensureDemTerrain();
                useDemTerrain();
            } else {
                useFlatGlobe();
            }

            if (useDemImagery) {
                await ensureDemImagery();
                setDemImageryVisible(true);
                if (opts.demImageryAlpha != null && state.demImageryLayer) {
                    state.demImageryLayer.alpha = opts.demImageryAlpha;
                }
            } else {
                setDemImageryVisible(false);
            }

            if (useOrtho) {
                await ensureOrtho();
                setOrthoVisible(true);
                if (opts.orthoAlpha != null) setOrthoAlpha(opts.orthoAlpha);
            } else {
                setOrthoVisible(false);
            }

            stackImageryOrder();
            setSatelliteVisible(opts.satellite !== false);
        }

        return {
            state,
            setTerrain,
            ensureOrtho,
            ensureDemTerrain,
            ensureDemImagery,
            applyElevationStack,
            setSatelliteVisible,
            setDemVisible,
            setDemImageryVisible,
            setDemImageryAlpha,
            setOrthoVisible,
            setOrthoAlpha,
            useFlatGlobe,
            useDemTerrain,
            flyToSurvey() {
                if (state.surveyRectangle) {
                    viewer.camera.flyTo({ destination: state.surveyRectangle, duration: 0.6 });
                }
            },
            /** @deprecated use ensureDemTerrain */
            ensureDem: ensureDemTerrain
        };
    }

    global.SurveyOrthoDem = {
        get ORTHO_COG() { return orthoCog(); },
        get DEM_COG() { return demCog(); },
        createViewer,
        loadSurveyLayers,
        initSurveyContext
    };
})(typeof window !== 'undefined' ? window : globalThis);
