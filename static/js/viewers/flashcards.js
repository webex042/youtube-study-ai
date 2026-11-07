// Flashcards: flip cards, prev/next, difficulty filter, shuffle (random mode),
// "Got it" / "Needs revision" tracking saved to the server.

import { api } from '../lib/api.js';
import { h, icon, iconBtn, download, safeFilename } from '../lib/dom.js';
import { notifyError, openMenu, toast } from '../lib/ui.js';
import { renderMarkdown } from '../lib/markdown.js';

export function render(host, art, ctx) {
  const cards = (art.content.cards || []).map((c, i) => ({ ...c, index: i }));
  const reviews = { ...(art.reviews || {}) };
  let filter = 'all';
  let shuffled = false;
  let order = cards.map((c) => c.index);
  let pos = 0;
  let flipped = false;
  let showHint = false;

  const visible = () => order.filter((i) => {
    const c = cards[i];
    if (filter === 'review') return reviews[i] === 'review';
    if (filter === 'new') return !reviews[i];
    if (['easy', 'medium', 'hard'].includes(filter)) return c.difficulty === filter;
    return true;
  });

  const toolbar = ctx.toolbar;
  toolbar.replaceChildren(
    iconBtn('shuffle', 'Random mode (shuffle)', () => { shuffled = !shuffled; reorder(); }),
    iconBtn('restart_alt', 'Reset progress', resetProgress),
    h('button', { class: 'icon-btn', type: 'button', 'aria-label': 'Export', 'data-tip': 'Export', onclick: (e) => exportMenu(e.currentTarget) }, icon('download')),
    h('span', { class: 'grow' }));

  function reorder() {
    order = cards.map((c) => c.index);
    if (shuffled) {
      for (let i = order.length - 1; i > 0; i -= 1) {
        const j = Math.floor(Math.random() * (i + 1));
        [order[i], order[j]] = [order[j], order[i]];
      }
      toast('Random mode on — cards shuffled');
    }
    pos = 0;
    flipped = false;
    draw();
    toolbar.firstChild.classList.toggle('active', shuffled);
  }

  async function mark(status) {
    const list = visible();
    if (!list.length) return;
    const idx = list[pos];
    const next = reviews[idx] === status ? 'clear' : status;
    try {
      await api.post(`/api/artifacts/${art.id}/cards/${idx}`, { status: next });
      if (next === 'clear') delete reviews[idx]; else reviews[idx] = next;
    } catch (e) { notifyError(e); return; }
    if (next !== 'clear') go(1, true); else draw();
  }

  async function resetProgress() {
    try {
      await Promise.all(Object.keys(reviews).map((i) => api.post(`/api/artifacts/${art.id}/cards/${i}`, { status: 'clear' })));
      Object.keys(reviews).forEach((k) => delete reviews[k]);
      draw();
      toast('Progress reset');
    } catch (e) { notifyError(e); }
  }

  function go(dir, fromMark = false) {
    const list = visible();
    if (!list.length) return;
    if (fromMark && filter !== 'all' && !list.includes(list[pos])) { draw(); return; }
    pos = Math.min(Math.max(0, pos + dir), list.length - 1);
    if (fromMark && dir > 0 && pos === list.length - 1 && list.length > 1 && filter === 'all') { /* stay on last */ }
    flipped = false;
    showHint = false;
    draw();
  }

  function exportMenu(anchor) {
    openMenu(anchor, [
      { icon: 'table_view', label: 'CSV (Anki / Quizlet import)', run: () => {
        const esc = (s) => `"${String(s).replace(/"/g, '""')}"`;
        download(`${safeFilename(art.title)}.csv`, cards.map((c) => `${esc(c.front)},${esc(c.back)}`).join('\n'), 'text/csv');
      } },
      { icon: 'description', label: 'Markdown', run: () => {
        download(`${safeFilename(art.title)}.md`, `# ${art.title}\n\n${cards.map((c, i) => `**${i + 1}. ${c.front}**\n\n${c.back}\n`).join('\n')}`, 'text/markdown');
      } },
    ]);
  }

  function filterChips() {
    const counts = {
      all: cards.length,
      review: cards.filter((c) => reviews[c.index] === 'review').length,
      new: cards.filter((c) => !reviews[c.index]).length,
    };
    const opts = [['all', `All (${counts.all})`], ['new', `Not marked (${counts.new})`], ['review', `Needs revision (${counts.review})`],
      ['easy', 'Easy'], ['medium', 'Medium'], ['hard', 'Hard']];
    return h('div', { class: 'fc-filters' }, ...opts.map(([v, label]) => h('button', {
      class: `chip ${filter === v ? 'selected' : ''}`, type: 'button', onclick: () => { filter = v; pos = 0; flipped = false; draw(); },
    }, label)));
  }

  function draw() {
    const list = visible();
    const understood = cards.filter((c) => reviews[c.index] === 'understood').length;
    const review = cards.filter((c) => reviews[c.index] === 'review').length;
    const head = h('div', { class: 'fc-top' }, filterChips());
    if (!list.length) {
      host.replaceChildren(h('div', { class: 'fc-wrap' }, head, h('div', { class: 'viewer-progress' }, icon('celebration', 'lg'),
        h('div', { class: 'big' }, filter === 'review' ? 'No cards need revision. Nice work!' : 'No cards in this filter.'))));
      return;
    }
    pos = Math.min(pos, list.length - 1);
    const card = cards[list[pos]];
    const status = reviews[card.index];
    const front = h('div', { class: 'fc-face fc-front' },
      h('div', { class: 'fc-label' }, h('span', {}, card.topic || 'Question'), h('span', { class: `diff ${card.difficulty}` }, card.difficulty)),
      h('div', { class: 'fc-text' }, renderMarkdown(card.front)),
      card.hint ? h('div', { class: 'fc-hint' }, showHint ? `Hint: ${card.hint}` : h('button', {
        class: 'btn btn-text small', type: 'button', onclick: (e) => { e.stopPropagation(); showHint = true; draw(); },
      }, icon('lightbulb'), 'Show hint')) : h('div', { class: 'fc-hint' }, 'Click or press Space to flip'));
    const back = h('div', { class: 'fc-face fc-back' },
      h('div', { class: 'fc-label' }, h('span', {}, 'Answer'), status ? h('span', {}, status === 'understood' ? '✓ Got it' : '↻ Needs revision') : null),
      h('div', { class: 'fc-text' }, renderMarkdown(card.back)),
      h('div', { class: 'fc-hint' }, h('button', {
        class: 'btn btn-text small', type: 'button',
        onclick: async (e) => { e.stopPropagation(); const { askInChat } = await import('../notebook/chat.js'); askInChat(`Explain this flashcard in more depth with an example.\nQ: ${card.front}\nA: ${card.back}`); },
      }, icon('psychology'), 'Explain more')));
    const cardEl = h('div', { class: `fc-card ${flipped ? 'flipped' : ''}`, role: 'button', tabindex: '0', 'aria-label': flipped ? 'Answer side — click to flip' : 'Question side — click to flip',
      onclick: () => { flipped = !flipped; cardEl.classList.toggle('flipped', flipped); } },
    h('div', { class: 'fc-inner' }, front, back));
    host.replaceChildren(h('div', { class: 'fc-wrap' },
      head,
      h('div', { class: 'fc-progress' }, h('div', { style: { width: `${Math.round((100 * understood) / cards.length)}%` } })),
      h('div', { class: 'fc-status' }, h('span', {}, `Card ${pos + 1} of ${list.length}`), h('span', { class: 'ok' }, `✓ ${understood} understood`), h('span', { class: 'rev' }, `↻ ${review} to revise`)),
      cardEl,
      h('div', { class: 'fc-controls' },
        iconBtn('chevron_left', 'Previous (←)', () => go(-1)),
        h('button', { class: `btn ${status === 'review' ? 'btn-primary' : 'btn-tonal'}`, type: 'button', onclick: () => mark('review') }, icon('replay'), 'Needs revision'),
        h('button', { class: `btn ${status === 'understood' ? 'btn-primary' : 'btn-tonal'}`, type: 'button', onclick: () => mark('understood') }, icon('check'), 'Got it'),
        iconBtn('chevron_right', 'Next (→)', () => go(1))),
      h('div', { class: 'fc-hint' }, 'Keyboard: ← → to move · Space to flip · 1 needs revision · 2 got it')));
  }

  const onKey = (e) => {
    if (!host.isConnected || e.target.closest('input, textarea')) return;
    if (e.key === 'ArrowRight') go(1);
    else if (e.key === 'ArrowLeft') go(-1);
    else if (e.key === ' ') { e.preventDefault(); flipped = !flipped; const el = host.querySelector('.fc-card'); if (el) el.classList.toggle('flipped', flipped); }
    else if (e.key === '1') mark('review');
    else if (e.key === '2') mark('understood');
  };
  document.addEventListener('keydown', onKey);
  draw();
  return () => document.removeEventListener('keydown', onKey);
}
