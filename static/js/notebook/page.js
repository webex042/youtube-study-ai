// Notebook workspace: header + Sources | Chat | Studio.

import { api } from '../lib/api.js';
import { h, icon, iconBtn } from '../lib/dom.js';
import { confirmDialog, notifyError, openMenu, toast } from '../lib/ui.js';
import { store } from './store.js';
import { mountSources } from './sources.js';
import { mountChat } from './chat.js';
import { mountStudio } from './studio.js';
import {
  openBookmarks, openNotebookSwitcher, openProgress, openSearch, openSettings, openShare,
} from './dialogs.js';

export const layout = {
  workspace: null,
  setTab(tab) {
    this.workspace.dataset.tab = tab;
    document.querySelectorAll('.mobile-tabs button').forEach((b) => b.classList.toggle('on', b.dataset.tab === tab));
  },
  toggle(cls, on) { this.workspace.classList.toggle(cls, on); },
};

export async function mountNotebook(notebookId) {
  const app = document.getElementById('app');
  const titleInput = h('input', {
    class: 'nb-title', type: 'text', 'aria-label': 'Notebook title', spellcheck: 'false', maxlength: '200',
  });
  const proBadge = h('button', {
    class: 'pro-badge', type: 'button', 'data-tip': 'Pro mode: use the most capable model',
    onclick: togglePro,
  }, 'PRO');
  const testChip = h('span', {
    class: 'test-chip hidden', 'data-tip': 'Local test server: AI output is canned sample data, not real analysis',
  }, 'TEST MODE');
  store.on('ai', (ai) => testChip.classList.toggle('hidden', !(ai && ai.provider === 'fake')));
  const avatar = h('button', { class: 'avatar', type: 'button', 'aria-label': 'Account', onclick: (e) => avatarMenu(e.currentTarget) }, 'S');

  const header = h('header', { class: 'topbar' },
    h('div', { class: 'brand' },
      h('a', { class: 'logo-btn', href: '/', 'aria-label': 'All notebooks', 'data-tip': 'All notebooks' },
        h('img', { src: '/static/img/logo.svg', alt: '' })),
      titleInput),
    h('div', { class: 'topbar-actions' },
      testChip,
      h('button', { class: 'btn btn-text', type: 'button', onclick: createNotebook }, icon('add'), h('span', { class: 'lbl' }, 'Create notebook')),
      h('button', { class: 'btn btn-text', type: 'button', onclick: openShare }, icon('ios_share'), h('span', { class: 'lbl' }, 'Share')),
      h('button', { class: 'icon-btn hide-mobile', type: 'button', 'aria-label': 'More', 'data-tip': 'More', onclick: (e) => moreMenu(e.currentTarget) }, icon('more_vert')),
      iconBtn('settings', 'Settings', openSettings, 'hide-mobile'),
      proBadge,
      h('button', { class: 'icon-btn', type: 'button', 'aria-label': 'Notebooks', 'data-tip': 'Your notebooks', onclick: (e) => openNotebookSwitcher(e.currentTarget) }, icon('apps')),
      avatar));

  const workspace = h('main', { class: 'workspace', 'data-tab': 'chat' });
  layout.workspace = workspace;
  const tabs = h('nav', { class: 'mobile-tabs', 'aria-label': 'Sections' },
    ...[['sources', 'description', 'Sources'], ['chat', 'chat_bubble', 'Chat'], ['studio', 'auto_awesome', 'Studio']]
      .map(([tab, ic, label]) => h('button', { type: 'button', 'data-tab': tab, class: tab === 'chat' ? 'on' : '', onclick: () => layout.setTab(tab) }, icon(ic), label)));

  app.replaceChildren(h('div', { class: 'app' }, header, workspace, tabs));

  workspace.append(mountSources(), mountChat(), mountStudio());

  // Title editing
  let editing = false;
  titleInput.addEventListener('focus', () => { editing = true; });
  titleInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') titleInput.blur();
    if (e.key === 'Escape') { titleInput.value = store.notebook.title; titleInput.blur(); }
  });
  titleInput.addEventListener('blur', async () => {
    editing = false;
    const value = titleInput.value.trim();
    if (!value || value === store.notebook.title) { titleInput.value = store.notebook.title; return; }
    try {
      const res = await api.patch(`/api/notebooks/${store.notebookId}`, { title: value });
      store.apply({ ...currentState(), notebook: res.notebook });
      toast('Notebook renamed', { type: 'success' });
    } catch (e) { notifyError(e); titleInput.value = store.notebook.title; }
  });

  const fitTitle = () => { titleInput.size = Math.max(8, Math.min(80, titleInput.value.length + 1)); };
  titleInput.addEventListener('input', fitTitle);
  store.on('notebook', (nb) => {
    if (!editing) titleInput.value = nb.title;
    fitTitle();
    document.title = `${nb.title} · AI Study Lab`;
  });
  store.on('settings', (s) => {
    proBadge.classList.toggle('on', !!s.pro_mode);
    const name = (s.display_name || '').trim();
    avatar.textContent = name ? name[0].toUpperCase() : 'S';
    avatar.setAttribute('data-tip', name || 'Student');
  });

  document.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); openSearch(); }
  });

  await store.load(notebookId);
}

function currentState() {
  return { notebook: store.notebook, sources: store.sources, artifacts: store.artifacts, notes: store.notes, ai: store.ai };
}

async function createNotebook() {
  try {
    const res = await api.post('/api/notebooks', {});
    window.location.href = `/notebook/${res.notebook.id}`;
  } catch (e) { notifyError(e); }
}

async function togglePro() {
  const next = !store.settings.pro_mode;
  try {
    const res = await api.put('/api/settings', { pro_mode: next });
    store.settings = res.settings;
    store.emit('settings', store.settings);
    toast(next ? 'Pro mode on — answers and Studio use the most capable model (slower, higher cost).' : 'Pro mode off — using the fast model.', { type: 'success' });
  } catch (e) { notifyError(e); }
}

function moreMenu(anchor) {
  openMenu(anchor, [
    { icon: 'edit', label: 'Rename notebook', run: () => { const t = document.querySelector('.nb-title'); t.focus(); t.select(); } },
    { icon: 'search', label: 'Search this notebook  (Ctrl+K)', run: openSearch },
    { icon: 'bookmarks', label: 'Bookmarks', run: openBookmarks },
    { icon: 'insights', label: 'Learning progress', run: openProgress },
    '-',
    { icon: 'download', label: 'Export notebook (Markdown)', run: () => { window.location.href = `/api/notebooks/${store.notebookId}/export`; } },
    { icon: 'refresh', label: 'Regenerate notebook overview', run: regenerateGuide },
    { icon: 'delete_sweep', label: 'Clear chat history', run: clearChat },
    '-',
    { icon: 'delete', label: 'Delete notebook', danger: true, run: deleteNotebook },
  ]);
}

function avatarMenu(anchor) {
  const name = store.settings.display_name || 'Student';
  openMenu(anchor, [
    { label: name, header: true },
    { icon: 'insights', label: 'Learning progress', run: openProgress },
    { icon: 'bookmarks', label: 'Bookmarks', run: openBookmarks },
    { icon: 'settings', label: 'Settings', run: openSettings },
    '-',
    { icon: 'grid_view', label: 'All notebooks', run: () => { window.location.href = '/'; } },
  ]);
}

async function regenerateGuide() {
  if (!store.readySources.length) { toast('Add a source first.'); return; }
  try {
    await api.post(`/api/notebooks/${store.notebookId}/guide`);
    store.pokeSoon();
    toast('Regenerating the notebook overview…');
  } catch (e) { notifyError(e); }
}

async function clearChat() {
  const ok = await confirmDialog({ title: 'Clear chat history?', message: 'All questions and answers in this notebook will be removed. Saved notes stay.', confirm: 'Clear' });
  if (!ok) return;
  try {
    await api.del(`/api/notebooks/${store.notebookId}/messages`);
    store.messages = [];
    store.emit('messages', store.messages);
  } catch (e) { notifyError(e); }
}

async function deleteNotebook() {
  const ok = await confirmDialog({ title: 'Delete this notebook?', message: `“${store.notebook.title}” and all of its sources, notes and Studio items will be permanently deleted.` });
  if (!ok) return;
  try {
    await api.del(`/api/notebooks/${store.notebookId}`);
    window.location.href = '/';
  } catch (e) { notifyError(e); }
}
