// Home: all notebooks (one per subject), continue studying, overall progress.

import { api } from './lib/api.js';
import { h, icon, iconBtn, debounce, fmtDate, plural } from './lib/dom.js';
import { confirmDialog, notifyError, openMenu, promptDialog } from './lib/ui.js';
import { store } from './notebook/store.js';

export async function mountHome() {
  const app = document.getElementById('app');
  document.title = 'AI Study Lab';
  let notebooks = [];
  let settings = {};
  let progress = null;
  try {
    const [nb, st, pr] = await Promise.all([api.get('/api/notebooks'), api.get('/api/settings'), api.get('/api/progress')]);
    notebooks = nb.notebooks;
    settings = st.settings;
    progress = pr.progress;
    store.settings = settings;
  } catch (e) { notifyError(e); }

  const name = (settings.display_name || '').trim();
  const avatar = h('button', { class: 'avatar', type: 'button', 'aria-label': 'Settings', onclick: openSettingsDialog }, name ? name[0].toUpperCase() : 'S');
  const header = h('header', { class: 'topbar' },
    h('div', { class: 'brand' },
      h('a', { class: 'logo-btn', href: '/', 'aria-label': 'Home' }, h('img', { src: '/static/img/logo.svg', alt: '' })),
      h('span', { class: 'nb-title', style: { cursor: 'default', border: '0' } }, 'AI Study Lab')),
    h('div', { class: 'topbar-actions' },
      h('button', { class: 'btn btn-text', type: 'button', onclick: create }, icon('add'), h('span', { class: 'lbl' }, 'Create notebook')),
      iconBtn('settings', 'Settings', openSettingsDialog),
      avatar));

  const search = h('input', { type: 'search', placeholder: 'Search notebooks', 'aria-label': 'Search notebooks' });
  const grid = h('div', { class: 'nb-grid' });
  let sort = 'recent';

  function draw() {
    const q = search.value.trim().toLowerCase();
    let list = notebooks.filter((n) => !q || n.title.toLowerCase().includes(q) || (n.summary || '').toLowerCase().includes(q));
    if (sort === 'title') list = [...list].sort((a, b) => a.title.localeCompare(b.title));
    if (sort === 'created') list = [...list].sort((a, b) => b.created_at - a.created_at);
    grid.replaceChildren(
      h('button', { class: 'nb-card new', type: 'button', onclick: create }, icon('add'), h('div', { class: 'title' }, 'Create new notebook')),
      ...list.map(card));
  }

  function card(n) {
    return h('a', { class: 'nb-card', href: `/notebook/${n.id}` },
      n.thumbnail ? h('div', { class: 'thumb', style: { backgroundImage: `url("${n.thumbnail}")` } }) : null,
      h('div', { class: 'emoji' }, n.emoji || '📓'),
      h('div', { class: 'title' }, n.title),
      h('div', { class: 'meta' }, `${fmtDate(n.updated_at)} · ${plural(n.source_count, 'source')}${n.artifact_count ? ` · ${plural(n.artifact_count, 'study item')}` : ''}`),
      h('button', {
        class: 'icon-btn sm more', type: 'button', 'aria-label': 'Notebook options',
        onclick: (e) => { e.preventDefault(); e.stopPropagation(); menu(e.currentTarget, n); },
      }, icon('more_vert')));
  }

  function menu(anchor, n) {
    openMenu(anchor, [
      { icon: 'edit', label: 'Rename', run: async () => {
        const title = await promptDialog({ title: 'Rename notebook', value: n.title });
        if (!title) return;
        try { await api.patch(`/api/notebooks/${n.id}`, { title }); n.title = title; draw(); } catch (e) { notifyError(e); }
      } },
      { icon: 'download', label: 'Export (Markdown)', run: () => { window.location.href = `/api/notebooks/${n.id}/export`; } },
      '-',
      { icon: 'delete', label: 'Delete', danger: true, run: async () => {
        const ok = await confirmDialog({ title: 'Delete notebook?', message: `“${n.title}” and everything in it will be permanently deleted.` });
        if (!ok) return;
        try { await api.del(`/api/notebooks/${n.id}`); notebooks = notebooks.filter((x) => x.id !== n.id); draw(); } catch (e) { notifyError(e); }
      } },
    ]);
  }

  search.addEventListener('input', debounce(draw, 120));

  const recent = notebooks[0];
  const statsRow = progress && (progress.sources || progress.quizzes.length || progress.flashcards.total)
    ? h('div', { class: 'stats-grid' },
      statCard(`${progress.streak}🔥`, 'day study streak'),
      statCard(progress.flashcards.total ? `${Math.round((100 * progress.flashcards.understood) / progress.flashcards.total)}%` : '—', 'flashcards mastered'),
      statCard(progress.quiz_average === null ? '—' : `${progress.quiz_average}%`, 'average quiz score'),
      statCard(String(notebooks.length), notebooks.length === 1 ? 'notebook' : 'notebooks'))
    : null;

  const main = h('main', { class: 'home-main scroll' }, h('div', { class: 'home-inner' },
    h('h1', {}, name ? `Welcome back, ${name}` : 'Welcome to AI Study Lab'),
    h('p', { class: 'sub' }, 'Turn lectures, PDFs and notes into real understanding — tutor chat, study notes, flashcards, quizzes and more.'),
    recent ? h('a', { class: 'continue', href: `/notebook/${recent.id}`, style: { textDecoration: 'none', color: 'inherit' } },
      h('div', { style: { fontSize: '34px' } }, recent.emoji || '📓'),
      h('div', { class: 'grow' }, h('div', { class: 'd' }, 'Continue studying'), h('div', { class: 't' }, recent.title),
        h('div', { class: 'd' }, `${plural(recent.source_count, 'source')} · last opened ${fmtDate(recent.opened_at)}`)),
      h('span', { class: 'btn btn-primary' }, 'Open', icon('arrow_forward'))) : null,
    statsRow,
    h('div', { class: 'home-tools' },
      h('div', { class: 'home-search' }, icon('search', 'sm'), search),
      h('span', { class: 'grow' }),
      h('button', { class: 'btn btn-text', type: 'button', onclick: (e) => openMenu(e.currentTarget, [
        { label: 'Most recent', checked: sort === 'recent', run: () => { sort = 'recent'; draw(); } },
        { label: 'Newest created', checked: sort === 'created', run: () => { sort = 'created'; draw(); } },
        { label: 'Title', checked: sort === 'title', run: () => { sort = 'title'; draw(); } },
      ]) }, icon('sort'), 'Sort')),
    grid));

  app.replaceChildren(h('div', { class: 'home' }, header, main));
  draw();
  store.on('settings', (st) => {
    const n = (st.display_name || '').trim();
    avatar.textContent = n ? n[0].toUpperCase() : 'S';
    const title = main.querySelector('h1');
    if (title) title.textContent = n ? `Welcome back, ${n}` : 'Welcome to AI Study Lab';
  });

  async function create() {
    try {
      const res = await api.post('/api/notebooks', {});
      window.location.href = `/notebook/${res.notebook.id}`;
    } catch (e) { notifyError(e); }
  }
}

function statCard(v, l) {
  return h('div', { class: 'stat-card' }, h('div', { class: 'v' }, v), h('div', { class: 'l' }, l));
}

async function openSettingsDialog() {
  const { openSettings } = await import('./notebook/dialogs.js');
  await openSettings();
}
