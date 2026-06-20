/**
 * Single survey/site configuration — edit survey/site.json (+ data under survey/) for a new site.
 */
(function (global) {
    'use strict';

    const DEFAULT_CONFIG_URL = './survey/site.json';

    let config = null;
    let loadPromise = null;

    function expandString(str, vars) {
        if (typeof str !== 'string') return str;
        return str.replace(/\{(\w+)\}/g, function (_m, key) {
            return vars[key] != null ? String(vars[key]) : _m;
        });
    }

    function expandValue(val, vars) {
        if (typeof val === 'string') return expandString(val, vars);
        if (Array.isArray(val)) return val.map(function (v) { return expandValue(v, vars); });
        if (val && typeof val === 'object') {
            const out = {};
            Object.keys(val).forEach(function (k) {
                out[k] = expandValue(val[k], vars);
            });
            return out;
        }
        return val;
    }

    function resolveAnalyticsLayers(cfg) {
        const base = cfg.analytics.shapesDir || './survey/shapes/';
        const prefix = base.endsWith('/') ? base : base + '/';
        return cfg.analytics.layers.map(function (layer) {
            return {
                key: layer.key,
                label: layer.label,
                file: prefix + layer.file,
                color: layer.color
            };
        });
    }

    function normalizeConfig(raw) {
        const vars = { s3Base: raw.services.s3Base };
        const expanded = expandValue(raw, vars);
        expanded.analytics.layerSources = resolveAnalyticsLayers(expanded);
        return expanded;
    }

    async function load(url) {
        if (config) return config;
        if (loadPromise) return loadPromise;

        const configUrl = url || DEFAULT_CONFIG_URL;
        loadPromise = fetch(configUrl)
            .then(function (res) {
                if (!res.ok) throw new Error('Survey config ' + res.status + ': ' + configUrl);
                return res.json();
            })
            .then(function (raw) {
                config = normalizeConfig(raw);
                console.log('[SurveyConfig] loaded', config.site.id, 'from', configUrl);
                return config;
            })
            .catch(function (err) {
                loadPromise = null;
                throw err;
            });

        return loadPromise;
    }

    function get() {
        if (!config) {
            throw new Error('SurveyConfig.load() must run before using survey data');
        }
        return config;
    }

    function getScores() {
        return get().scores || {};
    }

    function scoreThresholds() {
        const t = getScores().thresholds || {};
        return {
            good: t.good != null ? t.good : 80,
            warn: t.warn != null ? t.warn : 65
        };
    }

    function stateFromScore(score) {
        if (score == null || isNaN(score)) return 'good';
        const t = scoreThresholds();
        if (score >= t.good) return 'good';
        if (score >= t.warn) return 'warn';
        return 'crit';
    }

    function applyScoreFields(target, entry) {
        if (!target || !entry) return target;
        if (entry.score != null) target.score = entry.score;
        if (entry.pastScore != null) target.pastScore = entry.pastScore;
        if (entry.sharpness !== undefined) target.sharpness = entry.sharpness;
        if (entry.state != null) {
            target.state = entry.state;
        } else if (entry.score != null) {
            target.state = stateFromScore(entry.score);
        }
        return target;
    }

    function getCaptureAnomalies() {
        const cap = getScores().capture || {};
        return Array.isArray(cap.anomalies) ? cap.anomalies : [];
    }

    function getSurveyPoints() {
        const pts = get().capture && get().capture.surveyPoints;
        return Array.isArray(pts) ? pts : [];
    }

    function getCaptureLayerScore(key) {
        const cap = getScores().capture || {};
        return cap[key] || null;
    }

    function getProcessingLayerScore(key) {
        const proc = getScores().processing || {};
        return proc[key] || null;
    }

    function getImageScoreEntry(imageId) {
        const images = (getScores().processing || {}).images || {};
        const overrides = images.overrides || {};
        if (imageId && overrides[imageId]) return overrides[imageId];
        return images.default || { state: 'good', sharpness: null };
    }

    function getAnalyticsScoreEntry(layerKey, instanceId) {
        const layer = (getScores().analytics || {})[layerKey] || {};
        return layer[instanceId] || null;
    }

    function applySiteLabels(root) {
        const cfg = get();
        const site = cfg.site;
        const doc = root || document;

        doc.querySelectorAll('[data-survey-title]').forEach(function (el) {
            el.textContent = site.name;
        });
        doc.querySelectorAll('[data-survey-subtitle]').forEach(function (el) {
            el.textContent = site.subtitle;
        });
        doc.querySelectorAll('[data-survey-brand]').forEach(function (el) {
            el.textContent = site.brand;
        });
        doc.querySelectorAll('[data-survey-id-line]').forEach(function (el) {
            const n = site.imageCount != null ? ' · ' + site.imageCount + ' IMAGES' : '';
            el.textContent = site.surveyIdLine + n;
        });

        if (site.pageTitleSuffix && doc.title && doc.title.indexOf('·') >= 0) {
            const parts = doc.title.split('·');
            doc.title = parts[0].trim() + ' · ' + site.pageTitleSuffix;
        }

        return cfg;
    }

    global.SurveyConfig = {
        DEFAULT_CONFIG_URL,
        load,
        get,
        applySiteLabels,
        getScores,
        scoreThresholds,
        stateFromScore,
        applyScoreFields,
        getCaptureAnomalies,
        getSurveyPoints,
        getCaptureLayerScore,
        getProcessingLayerScore,
        getImageScoreEntry,
        getAnalyticsScoreEntry
    };
})(typeof window !== 'undefined' ? window : globalThis);
