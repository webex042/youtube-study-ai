/**
 * Quiz Module
 *
 * Renders multiple-choice questions, scores answers and supports a retry.
 *
 * Both the question text and the options come from the LLM, so every string
 * here is rendered through SafeDOM (textContent only). Element references are
 * kept in JavaScript rather than looked up by an interpolated id, so no
 * model-supplied value ever becomes part of a selector.
 */

(function () {
    'use strict';

    const S = window.SafeDOM;

    let questions = [];
    let selections = [];   // selections[qIndex] = option index, or -1
    let rendered = [];     // { root, options[], explanation }
    let submitted = false;
    let retryBound = false;

    function initQuiz(list) {
        // Keep only well-formed questions. The server validates these too; this
        // is the client refusing to render something it cannot score.
        questions = (Array.isArray(list) ? list : []).filter(function (q) {
            return q
                && typeof q.question === 'string'
                && Array.isArray(q.options)
                && q.options.length >= 2
                && Number.isInteger(q.correct_answer)
                && q.correct_answer >= 0
                && q.correct_answer < q.options.length;
        });

        selections = questions.map(function () { return -1; });
        submitted = false;

        const score = document.getElementById('quiz-score');
        if (score) score.style.display = 'none';

        // Bound once — initQuiz runs again on every new analysis, and rebinding
        // would stack duplicate handlers.
        const retry = document.getElementById('quiz-retry');
        if (retry && !retryBound) {
            retry.addEventListener('click', retryQuiz);
            retryBound = true;
        }

        renderQuiz();
    }

    function renderQuiz() {
        const container = document.getElementById('quiz-container');
        if (!container) return;

        S.clear(container);
        rendered = [];

        if (!questions.length) {
            container.appendChild(S.el('div', 'quiz-empty', 'No quiz questions available.'));
            return;
        }

        questions.forEach(function (q, qIndex) {
            const root = S.el('div', 'quiz-question');

            const header = S.el('div', 'quiz-question__header');
            S.append(header, [
                S.el('span', 'quiz-question__num', S.pad(qIndex + 1, 2)),
                S.el('span', 'quiz-question__text', S.str(q.question, 1000)),
            ]);
            root.appendChild(header);

            const optionsWrap = S.el('div', 'quiz-options');
            optionsWrap.setAttribute('role', 'group');
            const optionEls = [];

            q.options.forEach(function (opt, oIndex) {
                const btn = S.el('button', 'quiz-option');
                btn.type = 'button';
                btn.setAttribute('aria-pressed', 'false');
                S.append(btn, [
                    S.el('span', 'quiz-option__letter', String.fromCharCode(65 + oIndex)),
                    S.el('span', 'quiz-option__text', S.str(opt, 600)),
                ]);
                btn.addEventListener('click', function () {
                    selectAnswer(qIndex, oIndex);
                });
                optionsWrap.appendChild(btn);
                optionEls.push(btn);
            });

            root.appendChild(optionsWrap);

            const explanation = S.el('div', 'quiz-explanation');
            explanation.style.display = 'none';
            root.appendChild(explanation);

            container.appendChild(root);
            rendered.push({ root: root, options: optionEls, explanation: explanation });
        });

        const submitWrap = S.el('div', 'quiz-submit-wrap');
        const submitBtn = S.el('button', 'quiz-submit', 'SUBMIT QUIZ →');
        submitBtn.type = 'button';
        submitBtn.addEventListener('click', submitQuiz);
        submitWrap.appendChild(submitBtn);
        container.appendChild(submitWrap);
    }

    function selectAnswer(qIndex, oIndex) {
        if (submitted) return;
        const entry = rendered[qIndex];
        if (!entry) return;

        entry.options.forEach(function (btn) {
            btn.classList.remove('quiz-option--selected');
            btn.setAttribute('aria-pressed', 'false');
        });

        const chosen = entry.options[oIndex];
        if (chosen) {
            chosen.classList.add('quiz-option--selected');
            chosen.setAttribute('aria-pressed', 'true');
        }
        selections[qIndex] = oIndex;
    }

    function submitQuiz() {
        if (submitted || !questions.length) return;
        submitted = true;

        let correct = 0;

        questions.forEach(function (q, qIndex) {
            const entry = rendered[qIndex];
            if (!entry) return;

            const chosen = selections[qIndex];
            const answer = q.correct_answer;

            if (entry.options[answer]) {
                entry.options[answer].classList.add('quiz-option--correct');
            }
            if (chosen >= 0 && chosen !== answer && entry.options[chosen]) {
                entry.options[chosen].classList.add('quiz-option--wrong');
            }

            if (chosen === answer) {
                correct++;
                entry.root.classList.add('quiz-question--correct');
            } else {
                entry.root.classList.add('quiz-question--wrong');
            }

            if (typeof q.explanation === 'string' && q.explanation.trim()) {
                entry.explanation.textContent = S.str(q.explanation, 1200);
                entry.explanation.style.display = 'block';
            }

            entry.options.forEach(function (btn) {
                btn.disabled = true;
            });
        });

        const score = document.getElementById('quiz-score');
        const value = document.getElementById('quiz-score-value');
        if (value) value.textContent = correct + '/' + questions.length;
        if (score) {
            score.style.display = 'block';
            score.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }

        const submitBtn = document.querySelector('.quiz-submit');
        if (submitBtn) submitBtn.style.display = 'none';
    }

    function retryQuiz() {
        selections = questions.map(function () { return -1; });
        submitted = false;

        const score = document.getElementById('quiz-score');
        if (score) score.style.display = 'none';

        renderQuiz();

        const container = document.getElementById('quiz-container');
        if (container) container.scrollIntoView({ behavior: 'smooth' });
    }

    window.initQuiz = initQuiz;
})();
