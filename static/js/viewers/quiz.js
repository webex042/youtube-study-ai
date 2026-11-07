// Quiz: one question at a time, instant feedback with explanations, score,
// per-topic/difficulty performance summary, review of mistakes, retake.

import { api } from '../lib/api.js';
import { h, icon, iconBtn, download, safeFilename } from '../lib/dom.js';
import { notifyError } from '../lib/ui.js';
import { renderMarkdown } from '../lib/markdown.js';

export function render(host, art, ctx) {
  const all = art.content.questions || [];
  let difficulty = 'all';
  let questions = all;
  let answers = new Array(all.length).fill(null);
  let pos = 0;
  let finished = false;
  let showHint = false;

  ctx.toolbar.replaceChildren(
    iconBtn('restart_alt', 'Retake quiz', () => restart(false)),
    iconBtn('shuffle', 'Retake in random order', () => restart(true)),
    iconBtn('download', 'Download (Markdown with answers)', () => {
      const md = `# ${art.title}\n\n${all.map((q, i) => `**${i + 1}. ${q.question}**\n\n${q.options.map((o, j) => `- ${String.fromCharCode(65 + j)}. ${o}`).join('\n')}\n\n> Answer: ${String.fromCharCode(65 + q.answer)} — ${q.explanation}\n`).join('\n')}`;
      download(`${safeFilename(art.title)}.md`, md, 'text/markdown');
    }),
    h('span', { class: 'grow' }),
    ...(art.attempts && art.attempts.length ? [h('span', { class: 'muted', style: { fontSize: '12px' } },
      `Best: ${Math.max(...art.attempts.map((a) => Math.round((100 * a.score) / a.total)))}% · ${art.attempts.length} attempt${art.attempts.length > 1 ? 's' : ''}`)] : []));

  function restart(shuffle) {
    questions = difficulty === 'all' ? [...all] : all.filter((q) => q.difficulty === difficulty);
    if (shuffle) {
      for (let i = questions.length - 1; i > 0; i -= 1) {
        const j = Math.floor(Math.random() * (i + 1));
        [questions[i], questions[j]] = [questions[j], questions[i]];
      }
    }
    answers = new Array(questions.length).fill(null);
    pos = 0;
    finished = false;
    draw();
  }

  function choose(i) {
    if (answers[pos] !== null) return;
    answers[pos] = i;
    draw();
  }

  async function finish() {
    finished = true;
    const score = answers.reduce((n, a, i) => n + (a === questions[i].answer ? 1 : 0), 0);
    try { await api.post(`/api/artifacts/${art.id}/attempts`, { score, total: questions.length, answers: answers.map((a) => (a === null ? -1 : a)) }); } catch (e) { notifyError(e); }
    draw();
  }

  function difficultyBar() {
    const levels = ['all', 'easy', 'medium', 'hard'].filter((l) => l === 'all' || all.some((q) => q.difficulty === l));
    if (levels.length <= 2) return null;
    return h('div', { class: 'fc-filters', style: { marginBottom: '12px' } }, ...levels.map((l) => h('button', {
      class: `chip ${difficulty === l ? 'selected' : ''}`, type: 'button',
      onclick: () => { difficulty = l; restart(false); },
    }, l === 'all' ? `All (${all.length})` : `${l[0].toUpperCase()}${l.slice(1)} (${all.filter((q) => q.difficulty === l).length})`)));
  }

  function draw() {
    if (!questions.length) { host.replaceChildren(h('div', { class: 'viewer-progress' }, 'No questions at this difficulty.')); return; }
    if (finished) { host.replaceChildren(results()); return; }
    const q = questions[pos];
    const chosen = answers[pos];
    const dots = h('div', { class: 'quiz-dots' }, ...questions.map((qq, i) => h('i', {
      class: `${i === pos ? 'current' : ''} ${answers[i] === null ? '' : answers[i] === qq.answer ? 'ok' : 'bad'}`,
      title: `Question ${i + 1}`, onclick: () => { pos = i; showHint = false; draw(); },
    })));
    const opts = q.options.map((o, i) => {
      let cls = '';
      if (chosen !== null) {
        if (i === q.answer) cls = 'correct';
        else if (i === chosen) cls = 'wrong';
      }
      return h('button', { class: `quiz-opt ${cls}`, type: 'button', disabled: chosen !== null, onclick: () => choose(i) },
        h('span', { class: 'letter' }, String.fromCharCode(65 + i)), h('span', {}, renderMarkdown(o)));
    });
    const answered = answers.filter((a) => a !== null).length;
    const wrap = h('div', { class: 'quiz-wrap' },
      difficultyBar(),
      dots,
      h('div', { class: 'quiz-head' }, h('span', {}, `Question ${pos + 1} of ${questions.length}`),
        h('span', {}, q.topic ? `${q.topic} · ` : '', h('span', { class: `diff ${q.difficulty}` }, q.difficulty))),
      h('div', { class: 'quiz-q' }, renderMarkdown(q.question)),
      ...opts);
    if (chosen === null && q.hint) {
      wrap.append(showHint ? h('div', { class: 'quiz-explain' }, h('b', {}, 'Hint: '), q.hint)
        : h('button', { class: 'btn btn-text small', type: 'button', onclick: () => { showHint = true; draw(); } }, icon('lightbulb'), 'Hint'));
    }
    if (chosen !== null) {
      const right = chosen === q.answer;
      wrap.append(h('div', { class: 'quiz-explain' }, h('b', {}, right ? '✅ Correct. ' : `❌ Not quite — the answer is ${String.fromCharCode(65 + q.answer)}. `), renderMarkdown(q.explanation)));
    }
    wrap.append(h('div', { class: 'quiz-nav' },
      h('button', { class: 'btn btn-text', type: 'button', disabled: pos === 0, onclick: () => { pos -= 1; showHint = false; draw(); } }, icon('chevron_left'), 'Previous'),
      pos < questions.length - 1
        ? h('button', { class: 'btn btn-tonal', type: 'button', onclick: () => { pos += 1; showHint = false; draw(); } }, 'Next', icon('chevron_right'))
        : h('button', { class: 'btn btn-primary', type: 'button', onclick: finish }, icon('flag'), answered < questions.length ? `Finish (${answered}/${questions.length} answered)` : 'See results')));
    host.replaceChildren(wrap);
  }

  function results() {
    const score = answers.reduce((n, a, i) => n + (a === questions[i].answer ? 1 : 0), 0);
    const pct = Math.round((100 * score) / questions.length);
    const r = 64;
    const circ = 2 * Math.PI * r;
    const color = pct >= 80 ? '#4caf7d' : pct >= 50 ? '#e2b33c' : '#e06a5f';
    const ring = h('div', { class: 'score-ring' });
    ring.innerHTML = `<svg width="150" height="150" viewBox="0 0 150 150"><circle cx="75" cy="75" r="${r}" stroke="#2a2a2a" stroke-width="12" fill="none"/><circle cx="75" cy="75" r="${r}" stroke="${color}" stroke-width="12" fill="none" stroke-linecap="round" stroke-dasharray="${circ}" stroke-dashoffset="${circ * (1 - pct / 100)}"/></svg>`;
    ring.append(h('div', { class: 'val' }, `${pct}%`, h('small', {}, `${score} / ${questions.length}`)));
    const verdict = pct >= 90 ? 'Excellent! You have mastered this material.' : pct >= 70 ? 'Good work — a few areas to polish.'
      : pct >= 50 ? 'Getting there — review the explanations below.' : 'Keep going — revisit the notes and try again.';

    const groups = (key) => {
      const map = new Map();
      questions.forEach((q, i) => {
        const k = q[key] || 'General';
        const e = map.get(k) || { right: 0, total: 0 };
        e.total += 1;
        if (answers[i] === q.answer) e.right += 1;
        map.set(k, e);
      });
      return [...map.entries()];
    };
    const table = (title, rows) => h('table', { class: 'perf-table' },
      h('tr', {}, h('th', {}, title), h('th', {}, 'Score'), h('th', {})),
      ...rows.map(([k, e]) => h('tr', {}, h('td', {}, k), h('td', {}, `${e.right}/${e.total}`),
        h('td', {}, h('div', { class: 'bar-mini' }, h('div', { style: { width: `${(100 * e.right) / e.total}%`, background: e.right / e.total >= 0.7 ? '#2f8a55' : e.right / e.total >= 0.4 ? '#b38f2c' : '#a33a35' } }))))));
    const wrong = questions.map((q, i) => ({ q, i })).filter(({ q, i }) => answers[i] !== q.answer);
    const weakTopics = groups('topic').filter(([, e]) => e.right / e.total < 0.6).map(([k]) => k);

    return h('div', { class: 'quiz-wrap quiz-result' },
      ring,
      h('div', { style: { fontSize: '17px', marginBottom: '6px' } }, verdict),
      h('div', { class: 'section-title', style: { textAlign: 'left' } }, 'Performance by topic'), table('Topic', groups('topic')),
      h('div', { class: 'section-title', style: { textAlign: 'left' } }, 'By difficulty'), table('Difficulty', groups('difficulty')),
      h('div', { style: { display: 'flex', gap: '8px', justifyContent: 'center', flexWrap: 'wrap', margin: '14px 0' } },
        h('button', { class: 'btn btn-primary', type: 'button', onclick: () => restart(true) }, icon('restart_alt'), 'Retake'),
        weakTopics.length ? h('button', {
          class: 'btn btn-tonal', type: 'button',
          onclick: async () => { const { askInChat } = await import('../notebook/chat.js'); askInChat(`I struggled with these topics in my quiz: ${weakTopics.join(', ')}. Explain each one simply with an example, then give me 2 practice questions per topic.`); },
        }, icon('psychology'), 'Explain my weak topics') : null),
      wrong.length ? h('div', { class: 'review-list' }, h('div', { class: 'section-title' }, `Review your mistakes (${wrong.length})`),
        ...wrong.map(({ q, i }) => h('div', { class: 'review-item' },
          h('div', { class: 'q' }, `${i + 1}. ${q.question}`),
          answers[i] !== null ? h('div', { class: 'y' }, `Your answer: ${q.options[answers[i]]}`) : h('div', { class: 'y' }, 'Not answered'),
          h('div', { class: 'a' }, `Correct: ${q.options[q.answer]}`),
          h('div', { class: 'muted', style: { marginTop: '4px' } }, q.explanation)))) : h('p', { class: 'lead' }, 'No mistakes — perfect score! 🎉'));
  }

  draw();
  return null;
}
