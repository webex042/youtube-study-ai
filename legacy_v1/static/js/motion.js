/**
 * Motion Layer
 *
 * Parallax, scroll reveals, the load-in slash, drifting dust, magnetic
 * buttons and the wipe between dashboard tabs.
 *
 * This file exists as a separate script rather than an inline block because
 * the CSP is `script-src 'self'` with no 'unsafe-inline'. Nothing here may be
 * moved into the template.
 *
 * Security notes:
 *  - No content is rendered here. This module only reads geometry and writes
 *    transforms and class names, so no untrusted string reaches the DOM.
 *  - Class names written here are literals in this file, never derived from
 *    data.
 *  - Nothing is persisted. No storage APIs are used.
 *
 * Accessibility:
 *  - `prefers-reduced-motion: reduce` disables the slash, the dust, parallax
 *    and the magnetic effect. Reveals resolve immediately so no content is
 *    left hidden.
 *  - The un-revealed state is applied by adding `js-motion` to <html>, so a
 *    reader without JavaScript sees everything at full opacity.
 */

(function () {
    'use strict';

    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)');

    // Gate the hidden-until-revealed CSS on JS being alive. Set before paint
    // so there is no flash of visible-then-hidden content.
    if (!reduceMotion.matches) {
        document.documentElement.classList.add('js-motion');
    }

    document.addEventListener('DOMContentLoaded', function () {
        playSlash();
        setupReveals();
        setupParallax();
        setupDust();
        setupMagnets();
        setupTabWipe();
    });

    // ── The load-in slash ───────────────────────────────────────

    function playSlash() {
        const curtain = document.getElementById('slash-curtain');
        const hero = document.querySelector('.hero');

        if (!curtain) {
            if (hero) hero.classList.add('is-lit');
            return;
        }

        if (reduceMotion.matches) {
            curtain.classList.add('slash--done');
            if (hero) hero.classList.add('is-lit');
            return;
        }

        // One frame of delay so the transition has a start state to animate from.
        requestAnimationFrame(function () {
            curtain.classList.add('slash--cut');
            if (hero) hero.classList.add('is-lit');
        });

        // Take it out of the layer tree once it has played rather than leaving
        // a full-viewport fixed element compositing forever.
        window.setTimeout(function () {
            curtain.classList.add('slash--done');
        }, 1600);
    }

    // ── Scroll reveals ──────────────────────────────────────────

    function setupReveals() {
        const targets = document.querySelectorAll('[data-reveal]');
        if (!targets.length) return;

        // No IntersectionObserver, or motion is unwelcome: show everything.
        if (reduceMotion.matches || typeof IntersectionObserver !== 'function') {
            targets.forEach(function (node) { node.classList.add('is-revealed'); });
            return;
        }

        const observer = new IntersectionObserver(function (entries) {
            entries.forEach(function (entry) {
                if (!entry.isIntersecting) return;
                // Stagger within a group so a list arrives as a sequence
                // rather than all at once.
                const delay = Number(entry.target.dataset.revealOrder || 0) * 90;
                window.setTimeout(function () {
                    entry.target.classList.add('is-revealed');
                }, delay);
                observer.unobserve(entry.target);
            });
        }, { rootMargin: '0px 0px -12% 0px', threshold: 0.15 });

        // Assign the stagger index per parent, so each group counts from zero.
        const seen = new Map();
        targets.forEach(function (node) {
            const parent = node.parentNode;
            const n = seen.get(parent) || 0;
            node.dataset.revealOrder = String(n);
            seen.set(parent, n + 1);
            observer.observe(node);
        });
    }

    // ── Parallax ────────────────────────────────────────────────

    /**
     * Move the oversized kanji, the pattern layers and the grain at different
     * rates against the scroll.
     *
     * Reads are batched into a rAF callback so scrolling never triggers a
     * synchronous layout.
     */
    function setupParallax() {
        if (reduceMotion.matches) return;

        const layers = [
            { node: document.querySelector('.hero__kanji'), rate: -0.16 },
            { node: document.querySelector('.paper__seigaiha'), rate: 0.07 },
            { node: document.querySelector('.paper__asanoha'), rate: -0.05 },
            { node: document.querySelector('.paper__grain'), rate: 0.03 },
        ].filter(function (l) { return l.node; });

        if (!layers.length) return;

        let queued = false;

        function apply() {
            queued = false;
            const y = window.scrollY || window.pageYOffset || 0;
            layers.forEach(function (layer) {
                layer.node.style.transform = 'translate3d(0,' + (y * layer.rate).toFixed(1) + 'px,0)';
            });
        }

        window.addEventListener('scroll', function () {
            if (queued) return;
            queued = true;
            requestAnimationFrame(apply);
        }, { passive: true });

        apply();
    }

    // ── Drifting dust ───────────────────────────────────────────

    /**
     * A small field of slow motes. Count is fixed and low — this is
     * atmosphere, and it must not cost battery on a laptop.
     */
    function setupDust() {
        const field = document.getElementById('dust-field');
        if (!field || reduceMotion.matches) return;

        const COUNT = window.innerWidth < 700 ? 12 : 26;

        for (let i = 0; i < COUNT; i++) {
            const mote = document.createElement('div');
            mote.className = 'dust__mote';

            const duration = 16 + Math.random() * 22;
            mote.style.left = (Math.random() * 100).toFixed(2) + 'vw';
            mote.style.top = (Math.random() * 100).toFixed(2) + 'vh';
            mote.style.animation = 'drift ' + duration.toFixed(1) + 's linear ' +
                (-Math.random() * duration).toFixed(1) + 's infinite';
            mote.style.opacity = (0.1 + Math.random() * 0.25).toFixed(2);

            field.appendChild(mote);
        }

        // The keyframes are injected once rather than written per-mote. This is
        // a stylesheet rule, not script, so the CSP style-src allowance covers it.
        if (!document.getElementById('dust-keyframes')) {
            const style = document.createElement('style');
            style.id = 'dust-keyframes';
            style.textContent =
                '@keyframes drift {' +
                '0% { transform: translate3d(0, 0, 0); }' +
                '50% { transform: translate3d(14px, -46vh, 0); }' +
                '100% { transform: translate3d(-8px, -92vh, 0); }' +
                '}';
            document.head.appendChild(style);
        }
    }

    // ── Magnetic buttons ────────────────────────────────────────

    /**
     * Nudge a control a few pixels toward the cursor. Capped at 6px so it
     * reads as weight rather than as the element running away from the click.
     */
    function setupMagnets() {
        if (reduceMotion.matches) return;
        if (!window.matchMedia('(hover: hover) and (pointer: fine)').matches) return;

        const MAX = 6;

        document.querySelectorAll('[data-magnetic]').forEach(function (node) {
            node.addEventListener('mousemove', function (event) {
                const box = node.getBoundingClientRect();
                const dx = (event.clientX - (box.left + box.width / 2)) / (box.width / 2);
                const dy = (event.clientY - (box.top + box.height / 2)) / (box.height / 2);
                const cx = Math.max(-1, Math.min(1, dx)) * MAX;
                const cy = Math.max(-1, Math.min(1, dy)) * MAX;
                node.style.transform = 'translate(' + cx.toFixed(1) + 'px,' + cy.toFixed(1) + 'px)';
            });

            const release = function () { node.style.transform = ''; };
            node.addEventListener('mouseleave', release);
            // Clear the offset on activation too, so the pressed state is
            // not drawn at a stale position.
            node.addEventListener('blur', release);
            node.addEventListener('click', release);
        });
    }

    // ── Tab wipe ────────────────────────────────────────────────

    /**
     * Re-trigger the slash wipe on whichever panel became active.
     *
     * app.js owns tab state. This listens on the same container and reads the
     * result rather than duplicating the switching logic, so there is one
     * source of truth for which panel is shown.
     */
    function setupTabWipe() {
        const nav = document.getElementById('dash-nav');
        if (!nav || reduceMotion.matches) return;

        nav.addEventListener('click', function () {
            // Defer past app.js's own handler so the active class is settled.
            window.setTimeout(function () {
                const panel = document.querySelector('.tab-content--active');
                if (!panel) return;
                panel.classList.remove('tab-content--cut');
                // Force a reflow so removing and re-adding restarts the animation.
                void panel.offsetWidth;
                panel.classList.add('tab-content--cut');
            }, 0);
        });
    }

    // Note: the flashcard's Enter/Space handling is deliberately NOT bound
    // here. flashcards.js already listens at the document level for space and
    // enter while the cards panel is active, and it does not exclude the card
    // element itself — binding again would flip the card twice per keypress.
})();
