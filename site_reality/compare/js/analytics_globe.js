/**
 * Analytics stage — Cesium globe + shapes/*.geojson + prototype UI
 */
(function (global) {
    'use strict';

    function layerSources() {
        return global.SurveyConfig.get().analytics.layerSources;
    }

    const EYE_ON = '<svg width="14" height="14" viewBox="0 0 14 14" fill="none"><path d="M1 7C1 7 3 3 7 3C11 3 13 7 13 7C13 7 11 11 7 11C3 11 1 7 1 7Z" stroke="currentColor" stroke-width="1.2"/><circle cx="7" cy="7" r="2" stroke="currentColor" stroke-width="1.2"/></svg>';
    const EYE_OFF = '<svg width="14" height="14" viewBox="0 0 14 14" fill="none"><path d="M2 2L12 12M3 7C3 7 4 9 7 9M11 7C11 7 10 5 7 5M5 5L4 4M9 9L10 10" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/></svg>';
    const EYE_MIXED = '<svg width="14" height="14" viewBox="0 0 14 14" fill="none"><path d="M1 7C1 7 3 3 7 3C11 3 13 7 13 7C13 7 11 11 7 11C3 11 1 7 1 7Z" stroke="currentColor" stroke-width="1.2"/><path d="M5 7L9 7" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>';
    const EYE_SMALL_ON = '<svg width="12" height="12" viewBox="0 0 12 12" fill="none"><path d="M1 6C1 6 2.5 2.5 6 2.5C9.5 2.5 11 6 11 6C11 6 9.5 9.5 6 9.5C2.5 9.5 1 6 1 6Z" stroke="currentColor" stroke-width="1"/><circle cx="6" cy="6" r="1.5" stroke="currentColor" stroke-width="1"/></svg>';
    const EYE_SMALL_OFF = '<svg width="12" height="12" viewBox="0 0 12 12" fill="none"><path d="M2 2L10 10M2.5 6C2.5 6 3.5 7.5 6 7.5M9.5 6C9.5 6 8.5 4.5 6 4.5" stroke="currentColor" stroke-width="1" stroke-linecap="round"/></svg>';

    const state = {
        mode: 'default',
        expanded: {},
        visible: {},
        focused: null,
        nudgeDismissed: false
    };

    const catalog = { stockpiles: [], pits: [], wastedumps: [], cfzones: [] };
    const cesiumEntities = {};
    let viewer = null;
    let surveyRectangle = null;

    function stateColor(s) {
        if (s === 'good') return '#7CB89A';
        if (s === 'warn') return '#D2AA4E';
        return '#C86262';
    }

    function normalizeFeature(f, index, layerKey, layerColor) {
        const p = f.properties || {};
        const id = p.id || layerKey.toUpperCase() + (index + 1);
        const category = p.category || layerKey;
        const item = {
            id,
            name: p.name || id,
            category,
            layerKey,
            layerColor,
            material: p.material,
            volume_m3: p.volume_m3,
            weight_t: p.weight_t,
            densityTier: p.densityTier,
            density_t_m3: p.density_t_m3,
            depth_m: p.depth_m,
            slope_deg: p.slope_deg,
            height_m: p.height_m,
            stability: p.stability,
            netCutFill_m3: p.netCutFill_m3,
            cutVolume_m3: p.cutVolume_m3,
            fillVolume_m3: p.fillVolume_m3,
            purpose: p.purpose,
            state: 'good',
            score: null,
            pastScore: null,
            confidence: p.confidence,
            anomalies: Array.isArray(p.anomalies) ? p.anomalies : [],
            feature: f
        };
        if (global.SurveyConfig && typeof SurveyConfig.getAnalyticsScoreEntry === 'function') {
            try {
                const entry = SurveyConfig.getAnalyticsScoreEntry(layerKey, id);
                if (entry) SurveyConfig.applyScoreFields(item, entry);
            } catch (e) { /* config not loaded yet */ }
        }
        if (item.score == null && p.score != null) item.score = p.score;
        if (item.pastScore == null && p.pastScore != null) item.pastScore = p.pastScore;
        if (item.state === 'good' && p.state) item.state = p.state;
        return item;
    }

    async function loadCatalog() {
        for (const src of layerSources()) {
            const res = await fetch(src.file);
            if (!res.ok) throw new Error('Failed ' + src.file);
            const gj = await res.json();
            catalog[src.key] = (gj.features || []).map((f, i) =>
                normalizeFeature(f, i, src.key, src.color)
            );
            state.visible[src.key] = {};
            state.expanded[src.key] = false;
            catalog[src.key].forEach((item) => {
                state.visible[src.key][item.id] = false;
            });
        }
        console.log('[Analytics] loaded shapes', Object.fromEntries(
            layerSources().map((s) => [s.key, catalog[s.key].length])
        ));
    }

    /** Prototype-aligned fill/stroke (renderPolygons / renderCfzones). */
    function polygonStyle(item) {
        const focused = state.focused && state.focused.id === item.id;
        let fillHex = item.layerColor;
        let strokeHex = item.layerColor;
        let fillAlpha = 0.18;
        let strokeAlpha = 0.65;
        let strokeWidth = focused ? 2.5 : 1.5;
        let dashed = false;

        if (item.category === 'cfzones') {
            const net = item.netCutFill_m3 || 0;
            const intensity = Math.min(Math.abs(net) / 3000, 1);
            dashed = true;
            strokeWidth = focused ? 2.2 : 1.4;
            if (state.mode === 'score') {
                fillHex = strokeHex = stateColor(item.state);
                fillAlpha = 0.30;
                strokeAlpha = 0.75;
            } else if (net < 0) {
                const r = Math.round(74 - intensity * 20);
                const g = Math.round(112 - intensity * 20);
                const b = Math.round(136 + intensity * 12);
                fillHex = strokeHex = 'rgb(' + r + ',' + g + ',' + b + ')';
                strokeHex = '#6BA8C5';
                fillAlpha = 0.30;
                strokeAlpha = 0.75;
            } else {
                const r = Math.round(168 + intensity * 30);
                const g = Math.round(106 - intensity * 22);
                const b = Math.round(75 - intensity * 22);
                fillHex = 'rgb(' + r + ',' + g + ',' + b + ')';
                strokeHex = '#C88A6B';
                fillAlpha = 0.30;
                strokeAlpha = 0.75;
            }
        } else if (state.mode === 'score') {
            fillHex = strokeHex = stateColor(item.state);
            fillAlpha = 0.25;
            strokeAlpha = 0.65;
        }

        if (state.mode === 'anomalies' && item.state === 'good') {
            fillAlpha *= 0.35;
            strokeAlpha *= 0.35;
        }

        return { fillHex, strokeHex, fillAlpha, strokeAlpha, strokeWidth, dashed };
    }

    function colorMaterial(hexOrRgb, alpha) {
        if (String(hexOrRgb).indexOf('rgb') === 0) {
            return Cesium.Color.fromAlpha(
                Cesium.Color.fromCssColorString(hexOrRgb.replace(/ /g, '')),
                alpha
            );
        }
        return Cesium.Color.fromCssColorString(hexOrRgb).withAlpha(alpha);
    }

    function outlineMaterial(item) {
        const s = polygonStyle(item);
        const color = colorMaterial(s.strokeHex, s.strokeAlpha);
        if (s.dashed) {
            return new Cesium.PolylineDashMaterialProperty({ color: color, dashLength: 14 });
        }
        return color;
    }

    function volumeMetric(item) {
        if (item.category === 'cfzones') return Math.abs(item.netCutFill_m3 || 0);
        return item.volume_m3 || 0;
    }

    function labelTextFor(item) {
        if (item.category === 'cfzones') {
            const net = item.netCutFill_m3 || 0;
            const sign = net < 0 ? '−' : '+';
            return sign + Math.abs(net).toLocaleString() + '\nM³ NET';
        }
        return (item.volume_m3 || 0).toLocaleString() + '\nM³';
    }

    function labelFillColor(item) {
        if (state.mode === 'score') {
            return Cesium.Color.fromCssColorString(stateColor(item.state));
        }
        if (item.category === 'cfzones') {
            const net = item.netCutFill_m3 || 0;
            return Cesium.Color.fromCssColorString(net < 0 ? '#9EC4D8' : '#E5B299');
        }
        return Cesium.Color.fromCssColorString('#EBF2F8');
    }

    function shrinkRing(positions, scale) {
        const center = new Cesium.Cartesian3();
        positions.forEach((p) => Cesium.Cartesian3.add(center, p, center));
        Cesium.Cartesian3.divideByScalar(center, positions.length, center);
        return positions.map((p) => {
            const d = Cesium.Cartesian3.subtract(p, center, new Cesium.Cartesian3());
            Cesium.Cartesian3.multiplyByScalar(d, scale, d);
            return Cesium.Cartesian3.add(center, d, new Cesium.Cartesian3());
        });
    }

    function offsetNorth(cartesian, meters) {
        const carto = Cesium.Cartographic.fromCartesian(cartesian);
        const earthRadius = 6378137;
        const lat = carto.latitude + meters / earthRadius;
        return Cesium.Cartesian3.fromRadians(carto.longitude, lat, carto.height);
    }

    function addPolygonEntities() {
        Object.keys(cesiumEntities).forEach((id) => {
            const bag = cesiumEntities[id];
            if (!bag) return;
            ['fill', 'outline', 'toe', 'marker', 'leader', 'label', 'dot'].forEach((k) => {
                if (bag[k]) viewer.entities.remove(bag[k]);
            });
        });
        Object.keys(cesiumEntities).forEach((k) => { delete cesiumEntities[k]; });

        layerSources().forEach((src) => {
            catalog[src.key].forEach((item) => {
                const positions = ringsToPositions(item.feature.geometry);
                if (!positions.length) return;
                const closed = positions.concat(positions[0]);
                const centroid = centroidDegrees(item.feature.geometry);
                const style = polygonStyle(item);
                const props = itemToProps(item);

                const fill = viewer.entities.add({
                    name: item.name,
                    polygon: {
                        hierarchy: new Cesium.PolygonHierarchy(positions),
                        material: colorMaterial(style.fillHex, style.fillAlpha),
                        outline: false,
                        perPositionHeight: false,
                        heightReference: Cesium.HeightReference.CLAMP_TO_GROUND,
                        classificationType: Cesium.ClassificationType.BOTH
                    },
                    properties: props
                });

                const outline = viewer.entities.add({
                    polyline: {
                        positions: closed,
                        width: style.strokeWidth,
                        material: outlineMaterial(item),
                        clampToGround: true,
                        arcType: Cesium.ArcType.GEODESIC
                    },
                    properties: props
                });

                let toe = null;
                if (item.layerKey === 'pits') {
                    const toePts = shrinkRing(positions, 0.55).concat(shrinkRing(positions, 0.55)[0]);
                    toe = viewer.entities.add({
                        polyline: {
                            positions: toePts,
                            width: 0.8,
                            material: new Cesium.PolylineDashMaterialProperty({
                                color: colorMaterial(item.layerColor, 0.35),
                                dashLength: 8
                            }),
                            clampToGround: true,
                            arcType: Cesium.ArcType.GEODESIC
                        },
                        properties: props
                    });
                }

                const markerColor = item.state === 'crit' ? '#C86262' : '#D2AA4E';
                const marker = viewer.entities.add({
                    position: centroid,
                    point: {
                        pixelSize: item.state === 'good' ? 0 : 10,
                        color: Cesium.Color.fromCssColorString(markerColor),
                        outlineColor: Cesium.Color.WHITE,
                        outlineWidth: 2,
                        disableDepthTestDistance: Number.POSITIVE_INFINITY,
                        heightReference: Cesium.HeightReference.CLAMP_TO_GROUND
                    },
                    properties: props
                });

                const labelAnchor = offsetNorth(centroid, 42);
                const leader = viewer.entities.add({
                    polyline: {
                        positions: [centroid, labelAnchor],
                        width: 0.5,
                        material: Cesium.Color.fromCssColorString('rgba(200,215,228,0.30)'),
                        clampToGround: true
                    },
                    properties: props
                });

                const label = viewer.entities.add({
                    position: labelAnchor,
                    label: {
                        text: labelTextFor(item),
                        font: 'bold 13px Barlow, sans-serif',
                        fillColor: labelFillColor(item),
                        outlineColor: Cesium.Color.BLACK,
                        outlineWidth: 2,
                        style: Cesium.LabelStyle.FILL_AND_OUTLINE,
                        verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
                        horizontalOrigin: Cesium.HorizontalOrigin.CENTER,
                        heightReference: Cesium.HeightReference.CLAMP_TO_GROUND,
                        disableDepthTestDistance: Number.POSITIVE_INFINITY,
                        showBackground: true,
                        backgroundColor: Cesium.Color.fromCssColorString('rgba(2,3,8,0.85)'),
                        backgroundPadding: new Cesium.Cartesian2(8, 6)
                    },
                    properties: props
                });

                const dot = viewer.entities.add({
                    position: centroid,
                    point: {
                        pixelSize: 5,
                        color: Cesium.Color.fromCssColorString('rgba(235,242,248,0.45)'),
                        outlineWidth: 0,
                        disableDepthTestDistance: Number.POSITIVE_INFINITY,
                        heightReference: Cesium.HeightReference.CLAMP_TO_GROUND
                    },
                    properties: props
                });

                cesiumEntities[item.id] = { fill, outline, toe, marker, leader, label, dot };
                Object.values(cesiumEntities[item.id]).forEach((e) => { if (e) e.show = false; });
            });
        });
        renderAll();
    }

    /** Items that get headline labels (prototype renderLabels top-5 + focused). */
    function labelPrioritySet() {
        const allItems = [];
        layerSources().forEach((src) => {
            catalog[src.key].forEach((item) => {
                if (state.visible[src.key][item.id]) {
                    allItems.push({ item, kind: src.key, volume: volumeMetric(item) });
                }
            });
        });
        if (!allItems.length) return new Set();

        let toLabel;
        if (state.focused && state.focused.id) {
            const focused = allItems.find(
                (x) => x.kind === state.focused.kind && x.item.id === state.focused.id
            );
            const others = allItems
                .filter((x) => !(x.kind === state.focused.kind && x.item.id === state.focused.id))
                .sort((a, b) => b.volume - a.volume)
                .slice(0, 4);
            toLabel = focused ? [focused, ...others] : allItems.sort((a, b) => b.volume - a.volume).slice(0, 5);
        } else {
            toLabel = [...allItems].sort((a, b) => b.volume - a.volume).slice(0, 5);
        }
        return new Set(toLabel.map((x) => x.kind + ':' + x.item.id));
    }

    function updateLabelLayout() {
        const labeled = labelPrioritySet();
        layerSources().forEach((src) => {
            catalog[src.key].forEach((item) => {
                const bag = cesiumEntities[item.id];
                if (!bag) return;
                const key = src.key + ':' + item.id;
                const hasHeadline = labeled.has(key);
                const visible = !!state.visible[src.key][item.id];
                if (bag.label) {
                    bag.label.show = visible && hasHeadline;
                    if (bag.label.label) {
                        bag.label.label.text = labelTextFor(item);
                        const labelAlpha = (state.mode === 'anomalies' && item.state === 'good') ? 0.5 : 1;
                        bag.label.label.fillColor = labelFillColor(item).withAlpha(labelAlpha);
                    }
                }
                if (bag.leader) bag.leader.show = visible && hasHeadline;
                if (bag.dot) bag.dot.show = visible && !hasHeadline;
            });
        });
    }

    function renderAll() {
        refreshPolygonStyles();
        updateLabelLayout();
        updateEntityVisibility();
    }

    function itemToProps(item) {
        return new Cesium.PropertyBag({
            analyticsId: item.id,
            layerKey: item.layerKey,
            name: item.name
        });
    }

    function outerRing(geometry) {
        if (!geometry || !geometry.coordinates) return [];
        if (geometry.type === 'Polygon') return geometry.coordinates[0];
        if (geometry.type === 'MultiPolygon') return geometry.coordinates[0][0];
        return [];
    }

    function ringsToPositions(geometry) {
        return outerRing(geometry).map((c) => Cesium.Cartesian3.fromDegrees(c[0], c[1]));
    }

    function centroidDegrees(geometry) {
        const coords = outerRing(geometry);
        let lon = 0;
        let lat = 0;
        const n = coords.length - 1;
        for (let i = 0; i < n; i++) {
            lon += coords[i][0];
            lat += coords[i][1];
        }
        return Cesium.Cartesian3.fromDegrees(lon / n, lat / n);
    }

    function updateEntityVisibility() {
        layerSources().forEach((src) => {
            catalog[src.key].forEach((item) => {
                const show = !!state.visible[src.key][item.id];
                const bag = cesiumEntities[item.id];
                if (!bag) return;
                const showMarker = show && item.state !== 'good';
                if (bag.fill) bag.fill.show = show;
                if (bag.outline) bag.outline.show = show;
                if (bag.toe) bag.toe.show = show;
                if (bag.marker) {
                    bag.marker.show = showMarker;
                    if (bag.marker.point) {
                        bag.marker.point.pixelSize = showMarker ? 10 : 0;
                    }
                }
            });
        });
        updateLabelLayout();
        updateEmptyState();
    }

    function updateEmptyState() {
        const any = layerSources().some((src) =>
            catalog[src.key].some((item) => state.visible[src.key][item.id])
        );
        const el = document.getElementById('emptyState');
        if (el) {
            el.classList.toggle('show', !any);
            el.classList.toggle('hidden', !!any);
        }
        if (global.__surveyCapture && typeof global.__surveyCapture.updateViewportHint === 'function') {
            global.__surveyCapture.updateViewportHint();
        }
    }

    function analyticsRailRoot() {
        return document.getElementById('analyticsRail')
            || document.getElementById('analyticsSection');
    }

    function buildLeftPanel() {
        const section = analyticsRailRoot();
        if (!section) return;
        section.querySelectorAll('[data-parent]').forEach((el) => el.remove());
        section.querySelectorAll('[data-children]').forEach((el) => el.remove());

        layerSources().forEach((src) => {
            const items = catalog[src.key];
            const worst = items.reduce((w, it) => {
                if (it.state === 'crit') return 'crit';
                if (it.state === 'warn' && w !== 'crit') return 'warn';
                return w;
            }, 'good');

            const parent = document.createElement('div');
            parent.className = 'lp-item';
            parent.dataset.parent = src.key;
            parent.innerHTML =
                '<div class="lp-chevron" data-chevron="' + src.key + '">' +
                '<svg width="10" height="10" viewBox="0 0 10 10" fill="none">' +
                '<path d="M3 2L7 5L3 8" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/></svg></div>' +
                '<div class="lp-eye off" data-toggle-parent="' + src.key + '"></div>' +
                '<span class="lp-name">' + src.label + '</span>' +
                '<span class="lp-count-badge">' + items.length + '</span>' +
                '<span class="lp-state-dot ' + worst + '"></span>';

            const children = document.createElement('div');
            children.className = 'lp-children';
            children.dataset.children = src.key;
            children.style.display = 'none';

            items.forEach((item) => {
                const child = document.createElement('div');
                child.className = 'lp-child';
                child.dataset.parent = src.key;
                child.dataset.id = item.id;
                child.innerHTML =
                    '<div class="lp-child-eye off" data-toggle-child="' + item.id + '">' + EYE_SMALL_OFF + '</div>' +
                    '<span class="lp-child-name">' + item.name + '</span>' +
                    '<span class="lp-state-dot ' + item.state + '"></span>';
                children.appendChild(child);
            });

            section.appendChild(parent);
            section.appendChild(children);
        });

        document.querySelectorAll('.lp-eye[data-toggle-parent]').forEach((el) => {
            el.innerHTML = EYE_OFF;
        });
        wireLeftPanel();
    }

    function wireLeftPanel() {
        document.querySelectorAll('[data-chevron]').forEach((el) => {
            el.addEventListener('click', (e) => {
                e.stopPropagation();
                const key = el.dataset.chevron;
                state.expanded[key] = !state.expanded[key];
                const box = document.querySelector('[data-children="' + key + '"]');
                const chev = document.querySelector('[data-chevron="' + key + '"]');
                if (box) box.style.display = state.expanded[key] ? '' : 'none';
                if (chev) chev.classList.toggle('expanded', state.expanded[key]);
            });
        });

        document.querySelectorAll('[data-parent]').forEach((el) => {
            el.addEventListener('click', (e) => {
                if (e.target.closest('[data-chevron],[data-toggle-parent]')) return;
                focusParent(el.dataset.parent);
            });
        });

        document.querySelectorAll('[data-toggle-parent]').forEach((el) => {
            el.addEventListener('click', (e) => {
                e.stopPropagation();
                const key = el.dataset.toggleParent;
                const items = catalog[key];
                const allOn = items.every((it) => state.visible[key][it.id]);
                setParentVisible(key, !allOn);
            });
        });

        document.querySelectorAll('.lp-child').forEach((el) => {
            el.addEventListener('click', (e) => {
                if (e.target.closest('[data-toggle-child]')) return;
                const id = el.dataset.id;
                const parent = el.dataset.parent;
                if (!state.visible[parent][id]) setChildVisible(parent, id, true);
                focusChild(parent, id);
            });
        });

        document.querySelectorAll('[data-toggle-child]').forEach((el) => {
            el.addEventListener('click', (e) => {
                e.stopPropagation();
                const id = el.dataset.toggleChild;
                const parent = el.closest('.lp-child').dataset.parent;
                setChildVisible(parent, id, !state.visible[parent][id]);
            });
        });
    }

    function setChildVisible(parent, id, visible) {
        state.visible[parent][id] = visible;
        updateParentEye(parent);
        const eye = document.querySelector('[data-toggle-child="' + id + '"]');
        if (eye) {
            eye.className = 'lp-child-eye ' + (visible ? 'on' : 'off');
            eye.innerHTML = visible ? EYE_SMALL_ON : EYE_SMALL_OFF;
        }
        renderAll();
    }

    function refreshPolygonStyles() {
        layerSources().forEach((src) => {
            catalog[src.key].forEach((item) => {
                const bag = cesiumEntities[item.id];
                if (!bag) return;
                const style = polygonStyle(item);
                if (bag.fill && bag.fill.polygon) {
                    bag.fill.polygon.material = colorMaterial(style.fillHex, style.fillAlpha);
                }
                if (bag.outline && bag.outline.polyline) {
                    bag.outline.polyline.material = outlineMaterial(item);
                    bag.outline.polyline.width = style.strokeWidth;
                }
            });
        });
    }

    function setParentVisible(parent, visible) {
        catalog[parent].forEach((it) => setChildVisible(parent, it.id, visible));
    }

    function updateParentEye(parent) {
        const items = catalog[parent];
        const n = items.filter((it) => state.visible[parent][it.id]).length;
        const eye = document.querySelector('[data-toggle-parent="' + parent + '"]');
        if (!eye) return;
        if (n === 0) {
            eye.className = 'lp-eye off';
            eye.innerHTML = EYE_OFF;
        } else if (n === items.length) {
            eye.className = 'lp-eye on';
            eye.innerHTML = EYE_ON;
        } else {
            eye.className = 'lp-eye mixed';
            eye.innerHTML = EYE_MIXED;
        }
    }

    function focusParent(kind) {
        state.focused = { kind, id: null };
        updateFocusUi();
        renderRightPanel();
        renderAll();
    }

    function focusChild(kind, id) {
        state.focused = { kind, id };
        if (!state.expanded[kind]) {
            state.expanded[kind] = true;
            const box = document.querySelector('[data-children="' + kind + '"]');
            const chev = document.querySelector('[data-chevron="' + kind + '"]');
            if (box) box.style.display = '';
            if (chev) chev.classList.add('expanded');
        }
        updateFocusUi();
        renderRightPanel();
        renderAll();
        flyToItem(id);
    }

    function flyToItem(id) {
        const bag = cesiumEntities[id];
        if (!bag || !bag.fill) return;
        viewer.flyTo(bag.fill, { duration: 0.8, offset: new Cesium.HeadingPitchRange(0, -0.55, 280) });
    }

    function updateFocusUi() {
        document.querySelectorAll('.lp-item,.lp-child').forEach((el) => el.classList.remove('focused'));
        if (!state.focused) return;
        if (state.focused.id) {
            const c = document.querySelector('.lp-child[data-id="' + state.focused.id + '"]');
            if (c) c.classList.add('focused');
        } else {
            const p = document.querySelector('[data-parent="' + state.focused.kind + '"]');
            if (p) p.classList.add('focused');
        }
        document.getElementById('rightPanel').style.display = '';
    }

    function closeRightPanel() {
        state.focused = null;
        document.getElementById('rightPanel').style.display = 'none';
        document.querySelectorAll('.lp-item,.lp-child').forEach((el) => el.classList.remove('focused'));
        renderAll();
    }

    function findItem(kind, id) {
        return catalog[kind].find((it) => it.id === id);
    }

    function renderRightPanel() {
        const rp = document.getElementById('rpContent');
        if (!state.focused) {
            rp.innerHTML = '';
            return;
        }
        if (state.focused.id) {
            rp.innerHTML = renderChildPanel(state.focused.kind, state.focused.id);
        } else {
            rp.innerHTML = renderAggregatePanel(state.focused.kind);
        }
        rp.querySelectorAll('.roster-row[data-id]').forEach((row) => {
            row.addEventListener('click', () => {
                const parent = row.dataset.parent;
                const id = row.dataset.id;
                if (!state.visible[parent][id]) setChildVisible(parent, id, true);
                focusChild(parent, id);
            });
        });
    }

    function renderAggregatePanel(kind) {
        const src = layerSources().find((s) => s.key === kind);
        const items = catalog[kind];
        const avgScore = Math.round(items.reduce((a, it) => a + (it.score || 0), 0) / items.length);
        const pastAvg = Math.round(items.reduce((a, it) => a + (it.pastScore || 0), 0) / items.length);
        const crit = items.filter((it) => it.state === 'crit').length;
        const warn = items.filter((it) => it.state === 'warn').length;
        let rows = items.map((it) =>
            '<div class="roster-row" data-parent="' + kind + '" data-id="' + it.id + '">' +
            '<span class="roster-id">' + it.name + '<span class="material-tag">' + (it.material || '—') + '</span></span>' +
            '<span class="roster-volume">' + (it.volume_m3 != null ? it.volume_m3.toLocaleString() : (it.netCutFill_m3 != null ? (it.netCutFill_m3 < 0 ? '−' : '+') + Math.abs(it.netCutFill_m3).toLocaleString() : '—')) + '</span>' +
            '<span class="roster-state"><span class="dot" style="background:' + stateColor(it.state) + '"></span></span>' +
            '</div>'
        ).join('');
        return (
            '<div class="rp-hero">' +
            '<span class="rp-stage-chip">ANALYTICS</span>' +
            '<div class="rp-name">' + src.label + '</div>' +
            '<div class="rp-score"><span class="rp-score-value" style="color:' + stateColor(avgScore >= 80 ? 'good' : avgScore >= 65 ? 'warn' : 'crit') + '">' + avgScore + '</span>' +
            '<span class="rp-score-label">SITE SCORE</span></div>' +
            '<div class="rp-score-sub">Prior survey <strong>' + pastAvg + '</strong> · ' +
            (crit ? '<span class="crit-count">' + crit + ' critical</span>' : '') +
            (warn ? '<span class="warn-count">' + warn + ' warn</span>' : '') +
            (!crit && !warn ? 'All passing' : '') + '</div></div>' +
            '<div class="rp-section"><div class="rp-section-label">Instances</div>' +
            '<div class="roster"><div class="roster-header"><span>ID</span><span>VOL / NET</span><span>STATE</span></div>' +
            rows + '</div></div>'
        );
    }

    function renderChildPanel(kind, id) {
        const item = findItem(kind, id);
        if (!item) return '';
        const scoreDelta = (item.score != null && item.pastScore != null) ? item.score - item.pastScore : null;
        const deltaStr = scoreDelta == null ? '' : (scoreDelta >= 0 ? '+' : '') + scoreDelta + ' vs prior';
        let kpis = '';
        if (kind === 'stockpiles') {
            kpis += kpiRow('Volume', item.volume_m3 != null ? item.volume_m3.toLocaleString() : '—', 'm³');
            kpis += kpiRow('Weight', item.weight_t != null ? item.weight_t.toLocaleString() : 'Omitted', 't', item.weight_t == null);
            kpis += kpiRow('Material', item.material || 'Not set', '', false);
            kpis += kpiRow('Density tier', item.densityTier != null ? 'Tier ' + item.densityTier : '—', '');
        } else if (kind === 'pits') {
            kpis += kpiRow('Volume excavated', item.volume_m3.toLocaleString(), 'm³');
            kpis += kpiRow('Depth', item.depth_m, 'm');
            kpis += kpiRow('Slope', item.slope_deg + '°', '', item.state !== 'good');
        } else if (kind === 'wastedumps') {
            kpis += kpiRow('Volume', item.volume_m3.toLocaleString(), 'm³');
            kpis += kpiRow('Height', item.height_m, 'm');
            kpis += kpiRow('Stability', item.stability, '', item.state !== 'good');
        } else {
            kpis += kpiRow('Net cut/fill', (item.netCutFill_m3 < 0 ? '−' : '+') + Math.abs(item.netCutFill_m3).toLocaleString(), 'm³', item.state !== 'good');
            kpis += kpiRow('Cut', item.cutVolume_m3.toLocaleString(), 'm³');
            kpis += kpiRow('Fill', item.fillVolume_m3.toLocaleString(), 'm³');
        }
        const anomHtml = item.anomalies.length
            ? item.anomalies.map((a) =>
                '<div class="rp-indicator"><span class="rp-indicator-dot ' + a.severity + '"></span>' +
                '<div class="rp-indicator-body"><div class="rp-indicator-title">' + a.type + '</div>' +
                '<div class="rp-indicator-meta">' + a.detail + '</div></div></div>'
            ).join('')
            : '<div class="rp-empty">No anomalies flagged.</div>';
        return (
            '<div class="rp-hero">' +
            '<span class="rp-stage-chip">ANALYTICS · ' + kind.toUpperCase() + '</span>' +
            '<div class="rp-name">' + item.name + '</div>' +
            '<div class="rp-score"><span class="rp-score-value" style="color:' + stateColor(item.state) + '">' + (item.score || '—') + '</span>' +
            '<span class="rp-score-label">INSTANCE SCORE</span></div>' +
            '<div class="rp-score-sub">Prior score <strong>' + (item.pastScore || '—') + '</strong>' +
            (deltaStr ? ' · ' + deltaStr : '') + '</div></div>' +
            '<div class="rp-section"><div class="rp-section-label">KPIs</div>' + kpis + '</div>' +
            '<div class="rp-section"><div class="rp-section-label">Anomalies <span class="rp-needs-count ' +
            (item.anomalies.length ? (item.state === 'crit' ? '' : 'warn-only') : 'zero') + '">' + item.anomalies.length + '</span></div>' +
            anomHtml + '</div>'
        );
    }

    function kpiRow(label, value, unit, warn) {
        const cls = warn ? ' warn-text' : '';
        return '<div class="rp-kpi"><span class="rp-kpi-label">' + label + '</span>' +
            '<span><span class="rp-kpi-value' + cls + '">' + value + '</span>' +
            (unit ? '<span class="rp-kpi-unit">' + unit + '</span>' : '') + '</span></div>';
    }

    function setMode(mode) {
        state.mode = mode;
        const el = document.getElementById('sceneMode');
        if (el) el.dataset.mode = mode;
        renderAll();
    }

    function initUi() {
        document.querySelectorAll('.tab').forEach((tab) => {
            tab.addEventListener('click', () => {
                document.querySelectorAll('.tab').forEach((t) => t.classList.remove('active'));
                tab.classList.add('active');
                setMode(tab.dataset.tab);
            });
        });
        const rpClose = document.getElementById('rpClose');
        if (rpClose) rpClose.addEventListener('click', closeRightPanel);
        const protoToggle = document.getElementById('protoToggle');
        const protoNote = document.getElementById('protoNote');
        if (protoToggle && protoNote) {
            protoToggle.addEventListener('click', () => protoNote.classList.toggle('show'));
        }
        const nudgeDismiss = document.getElementById('nudgeDismiss');
        const contextNudge = document.getElementById('contextNudge');
        if (nudgeDismiss && contextNudge) {
            nudgeDismiss.addEventListener('click', () => {
                state.nudgeDismissed = true;
                contextNudge.classList.remove('show');
            });
        }
        const layerSat = document.getElementById('layerSatellite');
        const layerOrtho = document.getElementById('layerOrtho');
        const layerDem = document.getElementById('layerDem');
        if (layerSat && global.__processingSurvey) {
            layerSat.addEventListener('change', (e) => {
                global.__processingSurvey.setSatelliteVisible(e.target.checked);
            });
        }
        if (layerOrtho && global.__processingSurvey) {
            layerOrtho.addEventListener('change', (e) => {
                global.__processingSurvey.setOrthoVisible(e.target.checked);
            });
        }
        if (layerDem && global.__processingSurvey) {
            layerDem.addEventListener('change', (e) => {
                global.__processingSurvey.setDemVisible(e.target.checked);
            });
        }
    }

    async function init(viewerInstance, surveyRect) {
        viewer = viewerInstance;
        surveyRectangle = surveyRect;
        await loadCatalog();
        buildLeftPanel();
        addPolygonEntities();
        initUi();
        const first = layerSources()[0];
        if (first && catalog[first.key] && catalog[first.key].length) {
            setParentVisible(first.key, true);
        }
        if (surveyRectangle) {
            viewer.camera.setView({ destination: surveyRectangle });
        }
    }

    function focusById(layerKey, id) {
        if (!state.visible[layerKey][id]) setChildVisible(layerKey, id, true);
        focusChild(layerKey, id);
    }

    function getProp(entity, key) {
        if (!entity || !entity.properties) return undefined;
        const p = entity.properties[key];
        if (!p) return undefined;
        if (typeof p.getValue === 'function') {
            return p.getValue(Cesium.JulianDate.now());
        }
        return p;
    }

    global.AnalyticsGlobe = { init, catalog, state, focusById, getProp, setMode, renderAll };
})(typeof window !== 'undefined' ? window : globalThis);
