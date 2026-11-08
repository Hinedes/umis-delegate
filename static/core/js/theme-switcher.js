/**
 * Theme Switcher - Dark Mode Toggle
 * Handles: color scheme (light/dark), animations
 * The theme name is read from the body data attribute.
 */

(function () {
    'use strict';

    // Cookie utilities
    function setCookie(name, value, days = 365) {
        const expires = new Date(Date.now() + days * 864e5).toUTCString();
        document.cookie = name + '=' + encodeURIComponent(value) + '; expires=' + expires + '; path=/; SameSite=Lax';
    }

    function getCookie(name) {
        return document.cookie.split('; ').reduce((r, v) => {
            const parts = v.split('=');
            return parts[0] === name ? decodeURIComponent(parts[1]) : r;
        }, '');
    }

    // Theme state
    const state = {
        theme: document.body.dataset.theme || 'modern',
        colorScheme: document.body.dataset.colorScheme || 'auto',
        animations: document.body.dataset.animations || 'full'
    };

    // Check if dark mode is preferred
    function isDarkPreferred() {
        return window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
    }

    // Resolve effective color scheme
    function getEffectiveColorScheme() {
        if (state.colorScheme === 'dark') return 'dark';
        if (state.colorScheme === 'light') return 'light';
        return 'light';
    }

    // Apply theme to body
    function applyTheme() {
        const TRANSITION_CLASS = 'theme-transitioning';
        const html = document.documentElement;
        html.classList.add(TRANSITION_CLASS);

        const effective = getEffectiveColorScheme();

        document.body.dataset.theme = state.theme;
        document.body.dataset.colorScheme = state.colorScheme;
        document.body.dataset.animations = state.animations;
        document.body.dataset.colorSchemeResolved = effective;
        document.body.dataset.dark = effective === 'dark' ? 'true' : 'false';
        html.classList.toggle('dark', effective === 'dark');
        html.dataset.colorScheme = state.colorScheme;
        html.dataset.colorSchemeResolved = effective;
        document.getElementById('sidebar-theme-toggle')?.setAttribute('aria-pressed', String(effective === 'dark'));

        setCookie('umis_color_scheme_resolved', effective);

        // Toggle sun/moon icons explicitly (CSS-only approach unreliable)
        const sunIcons = document.querySelectorAll('.sidebar-theme-btn .btn-icon-sun');
        const moonIcons = document.querySelectorAll('.sidebar-theme-btn .btn-icon-moon');
        sunIcons.forEach(el => { el.style.display = effective === 'dark' ? 'inline' : 'none'; });
        moonIcons.forEach(el => { el.style.display = effective === 'dark' ? 'none' : 'inline'; });

        setTimeout(function () {
            html.classList.remove(TRANSITION_CLASS);
        }, 250);
    }

    // Listen for system dark mode changes
    function watchSystemTheme() {
        if (window.matchMedia) {
            const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)');
            mediaQuery.addEventListener('change', function (e) {
                if (state.colorScheme === 'auto') {
                    applyTheme();
                }
            });
        }
    }

    // Initialize theme from cookies or body data attributes
    function init() {
        // First read the server-provided defaults from body data attributes
        const serverTheme = document.body.dataset.theme || 'modern';
        const serverScheme = document.body.dataset.colorScheme || 'auto';
        const serverAnimations = document.body.dataset.animations || 'full';

        state.theme = serverTheme;
        state.colorScheme = serverScheme;
        state.animations = serverAnimations;

        // Check for cookie overrides (color scheme, animations)
        const cookieScheme = getCookie('umis_color_scheme');
        const cookieAnimations = getCookie('umis_animations');

        if (cookieScheme && ['auto', 'light', 'dark'].includes(cookieScheme)) {
            state.colorScheme = cookieScheme;
        }
        if (cookieAnimations && ['full', 'reduced', 'none'].includes(cookieAnimations)) {
            state.animations = cookieAnimations;
        }

        // Apply initial theme
        applyTheme();

        // Bind sidebar theme button (sun/moon) - toggles light/dark
        document.addEventListener('click', function (event) {
            if (!event.target.closest('#sidebar-theme-toggle')) return;
            const effective = getEffectiveColorScheme();
            state.colorScheme = effective === 'dark' ? 'light' : 'dark';
            setCookie('umis_color_scheme', state.colorScheme);
            applyTheme();
        });

        // Watch system theme changes
        watchSystemTheme();

    }

    // Run on DOM ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    // Expose API for other scripts
    window.UMISTheme = {
        getState: () => ({ ...state }),
        setColorScheme: (scheme) => {
            if (['auto', 'light', 'dark'].includes(scheme)) {
                state.colorScheme = scheme;
                setCookie('umis_color_scheme', scheme);
                applyTheme();
            }
        },
        setAnimations: (mode) => {
            if (['full', 'reduced', 'none'].includes(mode)) {
                state.animations = mode;
                setCookie('umis_animations', mode);
                applyTheme();
            }
        }
    };
})();
