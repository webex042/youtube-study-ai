/**
 * AI Study Lab — Main Application Logic
 *
 * Handles form submission, the API call to /api/study, dashboard rendering,
 * tab navigation and in-page search.
 *
 * Security posture for this file:
 *  - Every string rendered here came from a transcript or from an LLM. All of
 *    it is treated as untrusted and goes through SafeDOM, which only ever
 *    assigns text via textContent. There is no innerHTML in this file.
 *  - Video IDs are re-validated client-side before being placed in a URL.
 *  - No secrets, keys or tokens live in this file; it is served publicly.
 *  - Study data is held in a module-scoped variable for the lifetime of the
 *    page only. Nothing is written to localStorage or sessionStorage, so a
 *    transcript never outlives the tab.
 */

(function () {
    'use strict';

    const S = window.SafeDOM;

    // Held in memory only — deliberately not persisted to browser storage.
    let studyData = null;

    // ── Boot ────────────────────────────────────────────────────

    document.addEventListener('DOMContentLoaded', function () {
        const form = document.getElementById('study-form');
        if (form) form.addEventListener('submit', handleSubmit);

        const nav = document.getElementById('dash-nav');
        if (nav) nav.addEventListener('click', handleTabClick);

        const search = document.getElementById('search-input');
        if (search) search.addEventListener('input', debounce(handleSearch, 250));

        const searchClose = document.getElementById('search-close');
        if (searchClose) searchClose.addEventListener('click', closeSearch);

        const home = document.getElementById('brand-home');
        if (home) home.addEventListener('click', showLanding);

        // Escape closes the search panel — expected keyboard behaviour for an
        // overlay, and it keeps the panel reachable without a mouse.
        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape') closeSearch();
        });
    });

    // ── Submit ──────────────────────────────────────────────────

    async function handleSubmit(event) {
        event.preventDefault();

        const input = document.getElementById('youtube-url');
        const url = (input.value || '').trim();
        const loader = document.getElementById('loader');
        const btn = document.getElementById('analyze-btn');
        const btnText = btn ? btn.querySelector('.input-block__btn-text') : null;

        hideError();

        // A courtesy check only. The server repeats every one of these checks;
        // this exists so the common typo does not cost a round trip.
        if (!url) {
            showError('Please enter a YouTube URL.');
            input.focus();
            return;
        }
        if (url.length > 2048) {
            showError('That URL is too long.');
            return;
        }

        if (loader) loader.classList.add('loader--visible');
        if (btn) {
            btn.disabled = true;
            btn.setAttribute('aria-busy', 'true');
        }
        if (btnText) btnText.textContent = 'ANALYZING...';

        try {
            const response = await fetch('/api/study', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ url: url }),
            });

            // A non-JSON body means something upstream failed (a proxy error
            // page, for instance). Don't surface whatever it happened to say.
            let data = null;
            try {
                data = await response.json();
            } catch (err) {
                showError('Something went wrong. Please try again.');
                return;
            }

            if (!response.ok) {
                showError(safeMessage(data));
                return;
            }

            studyData = data;
            renderDashboard(data);
            showDashboard();
        } catch (err) {
            // Network-level failure. Nothing from the exception is shown.
            showError('Network error. Please check your connection and try again.');
        } finally {
            if (loader) loader.classList.remove('loader--visible');
            if (btn) {
                btn.disabled = false;
                btn.removeAttribute('aria-busy');
            }
            if (btnText) btnText.textContent = 'ANALYZE';
        }
    }

    /**
     * Pull a displayable message out of an error body.
     *
     * The server already returns only curated text, but this caps the length
     * and falls back to a fixed string so an unexpected shape can't put
     * arbitrary content on screen.
     */
    function safeMessage(data) {
        if (data && typeof data.error === 'string' && data.error) {
            return S.str(data.error, 300);
        }
        return 'Something went wrong. Please try again.';
    }

    // ── Page state ──────────────────────────────────────────────

    function showDashboard() {
        document.getElementById('landing-page').style.display = 'none';
        document.getElementById('dashboard').style.display = 'block';
        window.scrollTo({ top: 0, behavior: 'smooth' });
    }

    function showLanding() {
        document.getElementById('dashboard').style.display = 'none';
        document.getElementById('landing-page').style.display = 'block';
        window.scrollTo({ top: 0, behavior: 'smooth' });
    }

    let errorTimer = null;

    function showError(message) {
        const toast = document.getElementById('error-toast');
        const msg = document.getElementById('error-msg');
        if (!toast || !msg) return;
        msg.textContent = message;
        toast.classList.add('error-toast--visible');
        clearTimeout(errorTimer);
        errorTimer = setTimeout(hideError, 8000);
    }

    function hideError() {
        const toast = document.getElementById('error-toast');
        if (toast) toast.classList.remove('error-toast--visible');
    }

    // ── Tabs ────────────────────────────────────────────────────

    function handleTabClick(event) {
        const tab = event.target.closest('.dash-nav__tab');
        if (!tab) return;

        const name = tab.dataset.tab;
        // Only known tab names are accepted, so a tampered data-tab attribute
        // cannot be used to build an arbitrary selector.
        if (!/^[a-z-]{1,32}$/.test(name || '')) return;

        const panel = document.getElementById('tab-' + name);
        if (!panel) return;

        document.querySelectorAll('.dash-nav__tab').forEach(function (t) {
            t.classList.remove('dash-nav__tab--active');
            t.setAttribute('aria-selected', 'false');
        });
        tab.classList.add('dash-nav__tab--active');
        tab.setAttribute('aria-selected', 'true');

        document.querySelectorAll('.tab-content').forEach(function (c) {
            c.classList.remove('tab-content--active');
        });
        panel.classList.add('tab-content--active');
    }

    // ── Dashboard ───────────────────────────────────────────────

    function renderDashboard(data) {
        const topics = Array.isArray(data.topics) ? data.topics : [];

        setText('dash-lang', S.str(data.language_code || 'EN', 12).toUpperCase());
        setText('dash-type', data.is_generated ? 'AUTO-GENERATED' : 'MANUAL');
        setText('dash-topics-count', topics.length + ' TOPICS');
        setText('dash-title', 'LECTURE ANALYSIS');
        setText('dash-overview', S.str(data.overview, 4000));

        renderQuickSummary(Array.isArray(data.quick_summary) ? data.quick_summary : []);
        renderTopicTimeline(topics, data.video_id);
        renderMarkdownInto('detailed-summary', data.detailed_summary);
        renderMarkdownInto('notes-content', data.notes);
        renderMemoryGrid(Array.isArray(data.memory_points) ? data.memory_points : []);

        if (typeof window.initFlashcards === 'function') {
            window.initFlashcards(Array.isArray(data.flashcards) ? data.flashcards : []);
        }
        if (typeof window.initQuiz === 'function') {
            window.initQuiz(Array.isArray(data.quiz) ? data.quiz : []);
        }
        if (typeof window.initFlowchart === 'function') {
            window.initFlowchart(data.flowchart || {});
        }
    }

    function setText(id, value) {
        const node = document.getElementById(id);
        if (node) node.textContent = value == null ? '' : String(value);
    }

    /** Markdown is rendered to real nodes by SafeDOM, never to an HTML string. */
    function renderMarkdownInto(id, markdown) {
        const node = document.getElementById(id);
        if (!node) return;
        S.replace(node, S.renderMarkdown(typeof markdown === 'string' ? markdown : ''));
    }

    function renderQuickSummary(points) {
        const container = document.getElementById('quick-summary');
        if (!container) return;
        S.clear(container);

        points.forEach(function (point, i) {
            const li = S.el('li', 'summary-list__item');
            S.append(li, [
                S.el('span', 'summary-list__bullet', S.pad(i + 1, 2)),
                S.el('span', 'summary-list__text', S.str(point, 600)),
            ]);
            container.appendChild(li);
        });
    }

    function renderTopicTimeline(topics, videoId) {
        const container = document.getElementById('topic-timeline');
        if (!container) return;
        S.clear(container);

        topics.forEach(function (topic, i) {
            const seconds = Math.max(0, Math.floor(Number(topic.start_time) || 0));
            const stamp = S.str(topic.timestamp, 16) || S.formatTimestamp(seconds);

            const body = S.el('div', 'topic-item__body');
            S.append(body, [
                S.el('div', 'topic-item__title', S.str(topic.title, 300)),
                S.el('div', 'topic-item__desc', S.str(topic.text, 150)),
            ]);

            // youtubeUrl returns null for a malformed ID, and SafeDOM.link
            // degrades to a span, so the timestamp is still shown but is not
            // clickable. A bad ID never reaches an href.
            const item = S.el('div', 'topic-item');
            S.append(item, [
                S.el('div', 'topic-item__num', S.pad(i + 1, 2)),
                body,
                S.link(
                    S.youtubeUrl(videoId, seconds),
                    'topic-item__time',
                    stamp,
                    'Jump to ' + stamp + ' in the video'
                ),
            ]);
            container.appendChild(item);
        });
    }

    const CATEGORY_LABELS = {
        definition: '定義 — DEFINITIONS',
        formula: '公式 — FORMULAS',
        fact: '事実 — KEY FACTS',
        confusion: '注意 — COMMON CONFUSIONS',
        exam_point: '試験 — EXAM POINTS',
        general: '一般 — GENERAL',
    };

    // Importance arrives from the model. Mapping it through a known set keeps
    // it out of a class name, which would otherwise be attacker-influenced.
    const IMPORTANCE = { high: 'high', medium: 'medium', low: 'low' };

    function renderMemoryGrid(points) {
        const grid = document.getElementById('memory-grid');
        if (!grid) return;
        S.clear(grid);

        const groups = new Map();
        points.forEach(function (p) {
            const key = Object.prototype.hasOwnProperty.call(CATEGORY_LABELS, p.category)
                ? p.category
                : 'general';
            if (!groups.has(key)) groups.set(key, []);
            groups.get(key).push(p);
        });

        groups.forEach(function (items, key) {
            const section = S.el('div', 'memory-section');
            section.appendChild(S.el('div', 'memory-section__header', CATEGORY_LABELS[key]));

            const wrap = S.el('div', 'memory-cards');
            items.forEach(function (item, i) {
                const importance = IMPORTANCE[item.importance] || 'medium';
                const card = S.el('div', 'memory-card memory-card--' + importance);

                // Deterministic tilt rather than Math.random, so the layout is
                // stable across re-renders of the same content.
                const tilt = ((i % 5) - 2) * 0.7;
                card.style.transform = 'rotate(' + tilt.toFixed(2) + 'deg)';

                S.append(card, [
                    S.el('div', 'memory-card__importance', importance.toUpperCase()),
                    S.el('div', 'memory-card__concept', S.str(item.concept, 300)),
                    S.el('div', 'memory-card__hook', S.str(item.memory_hook, 800)),
                ]);
                wrap.appendChild(card);
            });

            section.appendChild(wrap);
            grid.appendChild(section);
        });
    }

    // ── Search ──────────────────────────────────────────────────

    const MAX_RESULTS = 15;

    function closeSearch() {
        const panel = document.getElementById('search-results');
        const input = document.getElementById('search-input');
        if (panel) panel.style.display = 'none';
        if (input) input.value = '';
    }

    function handleSearch(event) {
        const query = (event.target.value || '').trim().toLowerCase();
        const panel = document.getElementById('search-results');
        const list = document.getElementById('search-results-list');
        if (!panel || !list) return;

        if (query.length < 2 || !studyData) {
            panel.style.display = 'none';
            return;
        }

        const results = collectResults(query);
        S.clear(list);

        if (!results.length) {
            list.appendChild(
                S.el('div', 'search-result search-result--empty', 'No results found.')
            );
        } else {
            results.slice(0, MAX_RESULTS).forEach(function (r) {
                list.appendChild(buildResult(r));
            });
        }

        panel.style.display = 'block';
    }

    function collectResults(query) {
        const results = [];
        const has = function (value) {
            return typeof value === 'string' && value.toLowerCase().indexOf(query) !== -1;
        };

        (studyData.topics || []).forEach(function (t) {
            if (has(t.title) || has(t.text)) {
                results.push({ type: 'TOPIC', text: t.title, detail: t.text, tab: 'overview' });
            }
        });

        (studyData.flashcards || []).forEach(function (f) {
            if (has(f.question) || has(f.answer)) {
                results.push({ type: 'FLASHCARD', text: f.question, detail: f.answer, tab: 'flashcards' });
            }
        });

        (studyData.memory_points || []).forEach(function (m) {
            if (has(m.concept) || has(m.memory_hook)) {
                results.push({ type: 'MEMORY', text: m.concept, detail: m.memory_hook, tab: 'memory' });
            }
        });

        (studyData.quiz || []).forEach(function (q) {
            if (has(q.question)) {
                results.push({ type: 'QUIZ', text: q.question, detail: '', tab: 'quiz' });
            }
        });

        if (has(studyData.notes)) {
            results.push({ type: 'NOTES', text: 'Found in lecture notes', detail: '', tab: 'notes' });
        }

        const transcript = studyData.transcript_text || '';
        if (has(transcript)) {
            const idx = transcript.toLowerCase().indexOf(query);
            const snippet = transcript.slice(Math.max(0, idx - 40), idx + query.length + 60);
            results.push({
                type: 'TRANSCRIPT',
                text: 'Found in transcript',
                detail: '…' + snippet + '…',
                tab: 'overview',
            });
        }

        return results;
    }

    function buildResult(r) {
        const row = S.el('div', 'search-result');
        row.setAttribute('role', 'button');
        row.setAttribute('tabindex', '0');

        S.append(row, [
            S.el('span', 'search-result__type', S.str(r.type, 20)),
            S.el('span', 'search-result__text', S.str(r.text, 200)),
            S.el('span', 'search-result__detail', S.str(r.detail, 200)),
        ]);

        const go = function () {
            if (/^[a-z-]{1,32}$/.test(r.tab || '')) {
                const btn = document.querySelector('.dash-nav__tab[data-tab="' + r.tab + '"]');
                if (btn) btn.click();
            }
            closeSearch();
        };

        row.addEventListener('click', go);
        row.addEventListener('keydown', function (e) {
            if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                go();
            }
        });

        return row;
    }

    // ── Utilities ───────────────────────────────────────────────

    function debounce(fn, delay) {
        let timer = null;
        return function () {
            const args = arguments;
            const self = this;
            clearTimeout(timer);
            timer = setTimeout(function () {
                fn.apply(self, args);
            }, delay);
        };
    }

    // Exposed for the other modules and for the redesign's motion layer.
    window.StudyApp = {
        showLanding: showLanding,
        showDashboard: showDashboard,
        showError: showError,
        getData: function () {
            return studyData;
        },
    };
})();
