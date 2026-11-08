(() => {
    function closeNavigation() {
        document.body.classList.remove('navigation-open');
        document.getElementById('navigation-toggle')?.setAttribute('aria-expanded', 'false');
    }
    document.addEventListener('keydown', event => {
        if (event.key === 'Escape' && document.body.classList.contains('navigation-open')) {
            closeNavigation();
            document.getElementById('navigation-toggle')?.focus();
        }
    });
    document.addEventListener('click', event => {
        const toggle = event.target.closest('#navigation-toggle');
        if (toggle) {
            const open = document.body.classList.toggle('navigation-open');
            toggle.setAttribute('aria-expanded', String(open));
        } else if (!document.getElementById('app-sidebar-shell')?.contains(event.target)) {
            closeNavigation();
        }
    });
    matchMedia('(min-width: 1025px)').addEventListener('change', closeNavigation);

    const status = document.getElementById('request-status');
    document.addEventListener('htmx:beforeRequest', () => {
        status.textContent = 'Loading…';
        status.hidden = false;
    });
    document.addEventListener('htmx:afterRequest', event => {
        status.hidden = event.detail.successful;
        if (!event.detail.successful) status.textContent = 'The request failed. Check your connection and try again.';
    });
})();
