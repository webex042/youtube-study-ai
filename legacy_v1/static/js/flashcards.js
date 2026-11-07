/**
 * Flashcards Module
 *
 * Card flipping, prev/next navigation, a counter and a topic filter.
 *
 * Card text comes from the LLM and is written with textContent throughout.
 * The difficulty value is mapped through a known set before it becomes part of
 * a class name, so a model-supplied string cannot inject a class.
 *
 * Progress is intentionally not persisted: keeping it in memory means study
 * content never lands in browser storage.
 */

(function () {
    'use strict';

    const S = window.SafeDOM;

    let cards = [];
    let filtered = [];
    let index = 0;
    let flipped = false;
    let bound = false;

    const DIFFICULTY = { easy: 'easy', medium: 'medium', hard: 'hard' };

    function initFlashcards(list) {
        cards = (Array.isArray(list) ? list : []).filter(function (c) {
            return c && typeof c.question === 'string' && typeof c.answer === 'string';
        });
        filtered = cards.slice();
        index = 0;
        flipped = false;

        buildTopicFilter();

        // Bind once. initFlashcards runs again for every new analysis, and
        // rebinding would fire prevCard/nextCard multiple times per click.
        if (!bound) {
            bindControl('flash-prev', prevCard);
            bindControl('flash-next', nextCard);
            bindControl('flash-flip', flipCard);
            bindControl('flashcard', flipCard);
            document.addEventListener('keydown', handleKey);
            bound = true;
        }

        renderCard();
    }

    function bindControl(id, handler) {
        const node = document.getElementById(id);
        if (node) node.addEventListener('click', handler);
    }

    function handleKey(event) {
        const tab = document.getElementById('tab-flashcards');
        if (!tab || !tab.classList.contains('tab-content--active')) return;

        // Don't hijack typing in the search box or any other field.
        const active = document.activeElement;
        if (active && /^(INPUT|TEXTAREA|SELECT)$/.test(active.tagName)) return;

        if (event.key === 'ArrowLeft') { event.preventDefault(); prevCard(); }
        else if (event.key === 'ArrowRight') { event.preventDefault(); nextCard(); }
        else if (event.key === ' ' || event.key === 'Enter') { event.preventDefault(); flipCard(); }
    }

    function buildTopicFilter() {
        const container = document.getElementById('flash-filter');
        if (!container) return;
        S.clear(container);

        const topics = [];
        cards.forEach(function (c) {
            if (typeof c.topic === 'string' && c.topic.trim() && topics.indexOf(c.topic) === -1) {
                topics.push(c.topic);
            }
        });
        if (topics.length <= 1) return;

        const buttons = [];

        function makeButton(label, topic) {
            const btn = S.el('button', 'flash-filter__btn', S.str(label, 60).toUpperCase());
            btn.type = 'button';
            btn.setAttribute('aria-pressed', 'false');
            btn.addEventListener('click', function () {
                buttons.forEach(function (b) {
                    b.classList.remove('flash-filter__btn--active');
                    b.setAttribute('aria-pressed', 'false');
                });
                btn.classList.add('flash-filter__btn--active');
                btn.setAttribute('aria-pressed', 'true');
                filterByTopic(topic);
            });
            buttons.push(btn);
            container.appendChild(btn);
            return btn;
        }

        const all = makeButton('ALL', null);
        all.classList.add('flash-filter__btn--active');
        all.setAttribute('aria-pressed', 'true');

        // Cap the number of filter chips so a model that invents a topic per
        // card cannot flood the control strip.
        topics.slice(0, 24).forEach(function (topic) {
            makeButton(topic, topic);
        });
    }

    function filterByTopic(topic) {
        filtered = topic === null
            ? cards.slice()
            : cards.filter(function (c) { return c.topic === topic; });
        index = 0;
        flipped = false;
        renderCard();
    }

    function renderCard() {
        const inner = document.getElementById('flashcard-inner');
        if (inner) inner.classList.remove('flashcard__inner--flipped');
        flipped = false;

        const question = document.getElementById('flash-question');
        const answer = document.getElementById('flash-answer');
        const difficulty = document.getElementById('flash-difficulty');
        const topic = document.getElementById('flash-topic');
        const counter = document.getElementById('flash-current');
        const total = document.getElementById('flash-total');

        if (!filtered.length) {
            if (counter) counter.textContent = '00';
            if (total) total.textContent = '00';
            if (question) question.textContent = 'No flashcards available';
            if (answer) answer.textContent = '';
            if (difficulty) { difficulty.textContent = ''; difficulty.className = 'flashcard__difficulty'; }
            if (topic) topic.textContent = '';
            return;
        }

        const card = filtered[index];
        const level = DIFFICULTY[card.difficulty] || 'medium';

        if (counter) counter.textContent = S.pad(index + 1, 2);
        if (total) total.textContent = S.pad(filtered.length, 2);
        if (question) question.textContent = S.str(card.question, 1000);
        if (answer) answer.textContent = S.str(card.answer, 2000);
        if (topic) topic.textContent = S.str(card.topic || 'GENERAL', 60).toUpperCase();
        if (difficulty) {
            difficulty.textContent = level.toUpperCase();
            difficulty.className = 'flashcard__difficulty flashcard__difficulty--' + level;
        }
    }

    function flipCard() {
        const inner = document.getElementById('flashcard-inner');
        if (!inner || !filtered.length) return;
        flipped = !flipped;
        inner.classList.toggle('flashcard__inner--flipped', flipped);
    }

    function nextCard() {
        if (!filtered.length) return;
        index = (index + 1) % filtered.length;
        renderCard();
    }

    function prevCard() {
        if (!filtered.length) return;
        index = (index - 1 + filtered.length) % filtered.length;
        renderCard();
    }

    window.initFlashcards = initFlashcards;
})();
