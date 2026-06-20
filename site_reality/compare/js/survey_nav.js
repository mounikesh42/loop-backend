/**
 * Shared survey header — site brand only (layers live in the left rail).
 */
(function () {
    'use strict';

    function currentStageId() {
        var fromBody = document.body && document.body.getAttribute('data-survey-stage');
        if (fromBody) return fromBody;
        var path = (window.location.pathname || '').toLowerCase();
        if (path.indexOf('globe_polygons') >= 0) return 'survey';
        if (path.indexOf('globe_processing') >= 0) return 'processing';
        if (path.indexOf('globe_analytics') >= 0) return 'analytics';
        if (path.indexOf('globe_dem_ortho') >= 0) return 'orthodem';
        if (path.endsWith('/') || path.indexOf('index.html') >= 0) return 'home';
        return '';
    }

    function siteLabel() {
        try {
            if (window.SurveyConfig && typeof SurveyConfig.get === 'function') {
                return SurveyConfig.get().site.name;
            }
        } catch (e) { /* not loaded yet */ }
        return '';
    }

    function mount() {
        if (document.getElementById('surveyStageNav')) return;

        var active = currentStageId();
        document.body.classList.add('survey-has-nav');

        var nav = document.createElement('nav');
        nav.id = 'surveyStageNav';
        nav.setAttribute('aria-label', 'Survey');

        var brand = document.createElement('a');
        brand.className = 'nav-brand';
        brand.href = './index.html';
        brand.textContent = 'MINE 7';
        nav.appendChild(brand);

        var links = document.createElement('div');
        links.className = 'nav-links';

        var viewer = document.createElement('a');
        viewer.className = 'nav-link' + (active === 'survey' ? ' active' : '');
        viewer.href = './globe_polygons.html';
        viewer.textContent = 'Survey Viewer';
        if (active === 'survey') {
            viewer.setAttribute('aria-current', 'page');
        }
        links.appendChild(viewer);

        var ortho = document.createElement('a');
        ortho.className = 'nav-link' + (active === 'orthodem' ? ' active' : '');
        ortho.href = './globe_dem_ortho.html';
        ortho.textContent = 'Ortho + DEM';
        if (active === 'orthodem') {
            ortho.setAttribute('aria-current', 'page');
        }
        links.appendChild(ortho);

        nav.appendChild(links);

        var site = document.createElement('span');
        site.className = 'nav-site';
        site.setAttribute('data-survey-nav-site', '');
        site.textContent = siteLabel() || 'Hyderabad Mine 7';
        nav.appendChild(site);

        document.body.insertBefore(nav, document.body.firstChild);

        if (window.SurveyConfig && typeof SurveyConfig.load === 'function') {
            SurveyConfig.load().then(function () {
                var el = document.querySelector('[data-survey-nav-site]');
                if (el) el.textContent = SurveyConfig.get().site.name;
            }).catch(function () { /* ignore */ });
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', mount);
    } else {
        mount();
    }
})();
