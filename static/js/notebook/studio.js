// Studio panel: generation tiles, saved outputs (artifacts + notes) and the viewer.

import { api } from '../lib/api.js';
import { h, icon, iconBtn, plural, timeAgo, svgIcon, hasSvgIcon } from '../lib/dom.js';
import { confirmDialog, modal, notifyError, openMenu, promptDialog, segmented, toast } from '../lib/ui.js';
import { store } from './store.js';
import { layout } from './page.js';

export const TILES = [
  { type: 'audio', label: 'Audio Overview', icon: 'audio', desc: 'Two AI hosts discuss your sources' },
  { type: 'slides', label: 'Slide Deck', icon: 'slides', desc: 'A presentation with speaker notes' },
  { type: 'video', label: 'Video Overview', icon: 'videocam', desc: 'A narrated, animated lesson' },
  { type: 'mindmap', label: 'Mind Map', icon: 'flowchart', desc: 'How the concepts connect' },
  { type: 'report', label: 'Reports', icon: 'reports', badge: 'New!', desc: 'Briefing doc, study guide, FAQ…' },
  { type: 'flashcards', label: 'Flashcards', icon: 'flashcards', desc: 'Flip cards to memorise key ideas' },
  { type: 'quiz', label: 'Quiz', icon: 'quiz', desc: 'Multiple-choice questions with explanations' },
  { type: 'infographic', label: 'Infographic', icon: 'bar_chart', desc: 'A one-page visual summary' },
  { type: 'datatable', label: 'Data Table', icon: 'format_list_bulleted', desc: 'Comparisons, glossaries, timelines' },
  { type: 'notes', label: 'Study Notes', icon: 'edit_note', desc: 'Teacher-quality notes in 6 styles' },
];
export const TYPE_INFO = Object.fromEntries(TILES.map((t) => [t.type, t]));

export function typeIcon(type, cls = 't-icon') {
  const name = (TYPE_INFO[type] || { icon: 'article' }).icon;
  return hasSvgIcon(name) ? svgIcon(name, `ms ${cls}`) : icon(name, cls);
}

const LANGS = [
  ['hi', 'हिन्दी'], ['bn', 'বাংলা'], ['gu', 'ગુજરાતી'], ['kn', 'ಕನ್ನಡ'], ['ml', 'മലയാളം'],
  ['mr', 'मराठी'], ['pa', 'ਪੰਜਾਬੀ'], ['ta', 'தமிழ்'], ['te', 'తెలుగు'],
];

const NOTE_STYLES = [
  ['comprehensive', 'Comprehensive Notes', 'Everything, explained like a teacher would', 'menu_book'],
  ['beginner', 'Beginner-Friendly', 'Plain words, analogies, step by step', 'emoji_objects'],
  ['exam', 'Exam-Oriented', 'Definitions, model answers, mark-scoring points', 'school'],
  ['interview', 'Interview Prep', 'Likely questions with strong answers', 'record_voice_over'],
  ['revision', 'Quick Revision', 'One-page recap before the exam', 'bolt'],
  ['deep_dive', 'Technical Deep-Dive', 'Internals, trade-offs, edge cases', 'code'],
  ['practice', 'Practice Questions', 'MCQ, short, long & problems with answers', 'quiz'],
];

const REPORT_FORMATS = [
  ['briefing', 'Briefing Doc', 'Overview of the key insights and quotes', 'summarize'],
  ['study_guide', 'Study Guide', 'Short-answer quiz, essay questions, glossary', 'school'],
  ['blog', 'Blog Post', 'An insightful, readable article', 'article'],
  ['faq', 'FAQ', 'Questions a learner would ask, answered', 'help'],
  ['timeline', 'Timeline', 'Steps and events in order', 'timeline'],
  ['custom', 'Create your own', 'Describe the structure, style and tone', 'edit'],
];

const AUDIO_FORMATS = [
  ['deep_dive', 'Deep Dive', 'A lively conversation unpacking the topics (~10 min)'],
  ['brief', 'Brief', 'A bite-sized overview of the core ideas (~3 min)'],
  ['critique', 'Critique', 'An expert review of what is well explained and what is missing'],
  ['debate', 'Debate', 'Two perspectives on the key ideas and trade-offs'],
];

const DATATABLE_KINDS = [
  ['concepts', 'Key concepts', 'Concept · meaning · why it matters · example'],
  ['comparison', 'Comparison', 'Contrast approaches, tools or ideas'],
  ['glossary', 'Glossary', 'Terms and definitions'],
  ['timeline', 'Timeline', 'Steps or sections in order'],
  ['custom', 'Custom', 'Describe the table you want'],
];

const ui = {};
let openItem = null;        // { kind: 'artifact'|'note', id }
let viewerCleanup = null;

export function mountStudio() {
  const panel = h('section', { class: 'panel studio-panel', 'aria-label': 'Studio' });
  ui.panel = panel;
  ui.rail = h('div', { class: 'rail' },
    iconBtn('dock_to_right', 'Expand studio', () => setCollapsed(false)),
    ...TILES.map((t) => h('button', {
      class: 'icon-btn tile-rail', type: 'button', 'data-tip': t.label, 'data-type': t.type,
      onclick: () => { setCollapsed(false); startTile(t.type); },
    }, typeIcon(t.type))));
  ui.title = h('div', { class: 'panel-title' }, 'Studio');
  ui.headActions = h('div', { style: { display: 'flex', alignItems: 'center' } });
  ui.collapseBtn = iconBtn('dock_to_left', 'Collapse studio panel', () => setCollapsed(true), 'collapse-btn');
  ui.headActions.append(ui.collapseBtn);
  ui.head = h('div', { class: 'panel-head' }, ui.title, ui.headActions);
  ui.body = h('div', { class: 'studio-body scroll' });
  ui.viewer = h('div', { class: 'viewer hidden' });
  ui.foot = h('div', { class: 'studio-foot' },
    h('button', { class: 'add-note-btn', type: 'button', onclick: addNote }, icon('article'), 'Add note'));
  panel.append(ui.rail, ui.head, ui.body, ui.viewer, ui.foot);

  store.on('artifacts', () => { renderList(); refreshOpenArtifact(); });
  store.on('notes', renderList);
  store.on('sources', renderList);
  store.on('ai', renderList);
  renderList();
  return panel;
}

function setCollapsed(on) {
  ui.panel.classList.toggle('collapsed', on);
  layout.toggle('studio-collapsed', on);
  if (on) closeViewer();
}

// ── List ─────────────────────────────────────────────────────────

function renderList() {
  if (openItem) return;
  const hasSources = store.readySources.length > 0;
  const banner = h('div', { class: 'lang-banner' }, h('b', {}, 'Create an Audio Overview in: '),
    ...LANGS.flatMap(([code, name], i) => [
      h('span', { class: 'lang-pair' },
        h('span', { class: 'lang', role: 'button', tabindex: '0', 'data-tip': `Audio Overview in ${name}`, onclick: () => startArtifact('audio', { format: 'deep_dive', language: code }) }, name),
        i < LANGS.length - 1 ? ' ,' : ''),
      i < LANGS.length - 1 ? ' ' : '']));
  const busy = store.sources.some((src) => src.status === 'queued' || src.status === 'processing');
  const tiles = h('div', { class: `tiles ${busy ? 'inactive' : ''}` }, ...TILES.map(tile));

  const items = [];
  for (const a of store.artifacts) items.push(artifactRow(a));
  for (const n of store.notes) items.push(noteRow(n));
  items.sort((x, y) => y._ts - x._ts);

  const list = items.length
    ? [h('div', { class: 'studio-divider' }), h('div', { class: 'artifact-list' }, ...items)]
    : [h('div', { style: { height: '22px' } })];
  ui.body.replaceChildren(banner, tiles, ...list);
  if (!items.length) {
    ui.foot.replaceChildren(
      h('button', { class: 'add-note-btn', type: 'button', onclick: addNote }, icon('article'), 'Add note'),
      h('div', { class: `studio-empty ${hasSources ? 'has-sources' : ''}` }, 'Studio output will be saved here.'));
  } else {
    ui.foot.replaceChildren(h('button', { class: 'add-note-btn', type: 'button', onclick: addNote }, icon('article'), 'Add note'));
  }
}

function tile(t) {
  return h('button', {
    class: 'tile', type: 'button', 'data-type': t.type, title: t.desc,
    onclick: (e) => { if (e.target.closest('.t-edit')) return; startTile(t.type); },
  },
  typeIcon(t.type),
  h('span', { class: 't-label' }, t.label),
  t.badge ? h('span', { class: 'badge' }, t.badge) : null,
  h('span', {
    class: 't-edit', role: 'button', tabindex: '0', 'aria-label': `Customise ${t.label}`, 'data-tip': 'Customise',
    onclick: (e) => { e.stopPropagation(); customize(t.type); },
  }, icon('edit', 'xs')));
}

function startTile(type) {
  if (!guardSources()) return;
  if (type === 'report' || type === 'notes' || type === 'datatable') { customize(type); return; }
  startArtifact(type, {});
}

function guardSources() {
  if (!store.ai.configured) {
    toast(store.ai.message || 'AI is not configured. Add GEMINI_API_KEY to .env and restart the server.', { type: 'error' });
    return false;
  }
  if (!store.readySources.length) { toast('Add a source first — Studio builds everything from your sources.'); return false; }
  if (!store.selectedSources.length) { toast('Select at least one source in the Sources panel.'); return false; }
  return true;
}

export async function startArtifact(type, options) {
  if (!guardSources()) return null;
  try {
    const res = await api.post(`/api/notebooks/${store.notebookId}/studio`, {
      type, options, source_ids: store.selectedSources.map((s) => s.id),
    });
    store.artifacts = [res.artifact, ...store.artifacts];
    store.emit('artifacts', store.artifacts);
    store.pokeSoon();
    const label = TYPE_INFO[type].label;
    toast(type === 'audio' || type === 'video'
      ? `Generating ${label}… this can take a few minutes. You can keep studying.`
      : `Generating ${label}…`, { type: 'success' });
    layout.setTab('studio');
    return res.artifact;
  } catch (e) { notifyError(e); return null; }
}

function artifactRow(a) {
  const processing = a.status === 'processing';
  const sub = processing ? (a.status_message || 'Generating…')
    : a.status === 'error' ? a.error
      : [a.label || TYPE_INFO[a.type]?.label, plural(a.source_count, 'source'), timeAgo(a.created_at)].filter(Boolean).join(' · ');
  const el = h('div', {
    class: `artifact ${a.status}`, role: 'button', tabindex: '0', 'data-id': a.id,
    onclick: () => openArtifact(a.id), onkeydown: (e) => { if (e.key === 'Enter') openArtifact(a.id); },
  },
  h('span', { class: 'a-icon tile', 'data-type': a.type, style: { padding: 0, height: '36px', width: '36px', justifyContent: 'center' } },
    processing ? h('span', { class: 'spinner sm' }) : typeIcon(a.type)),
  h('div', { class: 'a-meta' }, h('div', { class: 'a-title' }, processing ? `Generating ${TYPE_INFO[a.type]?.label || ''}…` : a.title), h('div', { class: 'a-sub' }, sub)),
  (a.type === 'audio' || a.type === 'video') && a.status === 'ready'
    ? h('button', { class: 'icon-btn sm play', type: 'button', 'aria-label': 'Play', onclick: (e) => { e.stopPropagation(); openArtifact(a.id, { autoplay: true }); } }, icon('play_arrow', 'fill')) : null,
  h('button', { class: 'icon-btn sm a-more', type: 'button', 'aria-label': 'More options', onclick: (e) => { e.stopPropagation(); artifactMenu(e.currentTarget, a); } }, icon('more_vert', 'sm')));
  el._ts = a.created_at;
  return el;
}

function noteRow(n) {
  const el = h('div', {
    class: 'artifact', role: 'button', tabindex: '0', onclick: () => openNote(n.id), onkeydown: (e) => { if (e.key === 'Enter') openNote(n.id); },
  },
  h('span', { class: 'a-icon' }, icon(n.kind === 'saved_response' ? 'keep' : 'sticky_note_2', 'src-note')),
  h('div', { class: 'a-meta' }, h('div', { class: 'a-title' }, n.title), h('div', { class: 'a-sub' }, `${n.kind === 'saved_response' ? 'Saved response' : 'Note'} · ${timeAgo(n.updated_at)}`)),
  h('button', { class: 'icon-btn sm a-more', type: 'button', 'aria-label': 'More options', onclick: (e) => { e.stopPropagation(); noteMenu(e.currentTarget, n); } }, icon('more_vert', 'sm')));
  el._ts = n.updated_at;
  return el;
}

function artifactMenu(anchor, a) {
  openMenu(anchor, [
    a.status === 'ready' ? { icon: 'open_in_full', label: 'Open', run: () => openArtifact(a.id) } : null,
    { icon: 'edit', label: 'Rename', run: () => renameArtifact(a) },
    a.status === 'error' ? { icon: 'refresh', label: 'Try again', run: () => retryArtifact(a) } : null,
    a.status === 'ready' && (a.type === 'audio' || a.type === 'video') ? { icon: 'download', label: 'Download audio', run: () => { window.location.href = `/api/artifacts/${a.id}/media`; } } : null,
    '-',
    { icon: 'delete', label: 'Delete', danger: true, run: () => deleteArtifact(a) },
  ]);
}

function noteMenu(anchor, n) {
  openMenu(anchor, [
    { icon: 'open_in_full', label: 'Open', run: () => openNote(n.id) },
    { icon: 'note_add', label: 'Convert to source', run: () => noteToSource(n) },
    '-',
    { icon: 'delete', label: 'Delete note', danger: true, run: () => deleteNote(n) },
  ]);
}

async function renameArtifact(a) {
  const title = await promptDialog({ title: 'Rename', value: a.title });
  if (!title) return;
  try { await api.patch(`/api/artifacts/${a.id}`, { title }); store.pokeSoon(); } catch (e) { notifyError(e); }
}

async function retryArtifact(a) {
  try { await api.post(`/api/artifacts/${a.id}/retry`); store.pokeSoon(); } catch (e) { notifyError(e); }
}

async function deleteArtifact(a) {
  const ok = await confirmDialog({ title: 'Delete this item?', message: `“${a.title}” will be permanently deleted.` });
  if (!ok) return;
  try {
    await api.del(`/api/artifacts/${a.id}`);
    if (openItem && openItem.id === a.id) closeViewer();
    store.artifacts = store.artifacts.filter((x) => x.id !== a.id);
    store.emit('artifacts', store.artifacts);
    store.pokeSoon();
  } catch (e) { notifyError(e); }
}

async function deleteNote(n) {
  const ok = await confirmDialog({ title: 'Delete note?', message: `“${n.title}” will be permanently deleted.` });
  if (!ok) return;
  try {
    await api.del(`/api/notes/${n.id}`);
    if (openItem && openItem.id === n.id) closeViewer();
    store.pokeSoon();
  } catch (e) { notifyError(e); }
}

async function noteToSource(n) {
  try { await api.post(`/api/notes/${n.id}/to-source`); toast('Note added as a source', { type: 'success' }); store.pokeSoon(); } catch (e) { notifyError(e); }
}

async function addNote() {
  try {
    const res = await api.post(`/api/notebooks/${store.notebookId}/notes`, { title: 'New Note', content: '' });
    store.pokeSoon();
    openNote(res.note.id, { edit: true });
  } catch (e) { notifyError(e); }
}

// ── Customise dialogs ────────────────────────────────────────────

function optionCards(items, value, onChange) {
  const wrap = h('div', { class: 'option-grid' });
  const render = () => wrap.replaceChildren(...items.map(([v, t, d, ic]) => h('button', {
    type: 'button', class: `option-card ${v === value ? 'selected' : ''}`, onclick: () => { value = v; render(); onChange(v); },
  }, h('div', { class: 't' }, ic ? icon(ic, 'sm') : null, t), h('div', { class: 'd' }, d))));
  render();
  return wrap;
}

function languageField(initial = '') {
  const select = h('select', { class: 'field' },
    h('option', { value: '' }, 'English (default)'),
    ...LANGS.map(([code, name]) => h('option', { value: code }, name)),
    h('option', { value: 'other' }, 'Other…'));
  const other = h('input', { class: 'field hidden', type: 'text', placeholder: 'e.g. Spanish, Urdu, French', style: { marginTop: '8px' } });
  if (initial && LANGS.some(([c]) => c === initial)) select.value = initial;
  else if (initial) { select.value = 'other'; other.value = initial; other.classList.remove('hidden'); }
  select.addEventListener('change', () => other.classList.toggle('hidden', select.value !== 'other'));
  return { el: h('div', {}, select, other), value: () => (select.value === 'other' ? other.value.trim() : select.value) };
}

export function customize(type, preset = {}) {
  const info = TYPE_INFO[type];
  const opts = { ...preset };
  const parts = [];
  const focus = h('textarea', { class: 'field', rows: 3, placeholder: 'Optional — e.g. "Only the authentication part", "Focus on JWT vs sessions", "Chapter 3"' });
  focus.value = opts.focus || '';
  const lang = languageField(opts.language || '');

  if (type === 'audio') {
    opts.format = opts.format || 'deep_dive';
    parts.push(h('label', { class: 'label' }, 'Format'), optionCards(AUDIO_FORMATS, opts.format, (v) => { opts.format = v; }));
  } else if (type === 'notes') {
    opts.style = opts.style || 'comprehensive';
    parts.push(h('label', { class: 'label' }, 'Note style'), optionCards(NOTE_STYLES, opts.style, (v) => { opts.style = v; }));
  } else if (type === 'report') {
    opts.format = opts.format || 'briefing';
    const custom = h('textarea', { class: `field ${opts.format === 'custom' ? '' : 'hidden'}`, rows: 4, placeholder: 'Describe the report: structure, style, tone, audience…', style: { marginTop: '10px' } });
    parts.push(h('label', { class: 'label' }, 'Format'), optionCards(REPORT_FORMATS, opts.format, (v) => { opts.format = v; custom.classList.toggle('hidden', v !== 'custom'); }), custom);
    opts._custom = custom;
  } else if (type === 'datatable') {
    opts.kind = opts.kind || 'concepts';
    const custom = h('textarea', { class: `field ${opts.kind === 'custom' ? '' : 'hidden'}`, rows: 3, placeholder: 'e.g. "Compare every database discussed: name, type, strengths, weaknesses, when to use"', style: { marginTop: '10px' } });
    parts.push(h('label', { class: 'label' }, 'Table type'), optionCards(DATATABLE_KINDS, opts.kind, (v) => { opts.kind = v; custom.classList.toggle('hidden', v !== 'custom'); }), custom);
    opts._custom = custom;
  }
  if (['flashcards', 'quiz', 'slides', 'video'].includes(type)) {
    opts.count = opts.count || 'standard';
    const labels = {
      flashcards: ['Fewer (12)', 'Standard (20)', 'More (35)'], quiz: ['Fewer (5)', 'Standard (10)', 'More (20)'],
      slides: ['Short (7)', 'Standard (11)', 'Long (16)'], video: ['Short (5 scenes)', 'Standard (8)', 'Long (12)'],
    }[type];
    parts.push(h('label', { class: 'label' }, type === 'slides' ? 'Length' : type === 'video' ? 'Length' : 'Number of ' + (type === 'quiz' ? 'questions' : 'cards')),
      segmented([{ value: 'fewer', label: labels[0] }, { value: 'standard', label: labels[1] }, { value: 'more', label: labels[2] }], opts.count, (v) => { opts.count = v; }));
  }
  if (type === 'flashcards' || type === 'quiz') {
    opts.difficulty = opts.difficulty || 'mixed';
    parts.push(h('label', { class: 'label' }, 'Difficulty'),
      segmented([{ value: 'mixed', label: 'Mixed' }, { value: 'easy', label: 'Easy' }, { value: 'medium', label: 'Medium' }, { value: 'hard', label: 'Hard' }], opts.difficulty, (v) => { opts.difficulty = v; }));
  }
  parts.push(h('label', { class: 'label' }, 'Language'), lang.el);
  parts.push(h('label', { class: 'label' }, type === 'audio' ? 'What should the AI hosts focus on?' : 'Focus (optional)'), focus);
  const sel = store.selectedSources.length;
  parts.push(h('p', { class: 'lead', style: { marginTop: '14px', marginBottom: '0' } }, `Uses ${plural(sel, 'selected source')}. Change the selection in the Sources panel.`));

  const m = modal({
    title: `Customise ${info.label}`, body: h('div', {}, ...parts),
    footer: [
      h('button', { class: 'btn btn-text', type: 'button', onclick: () => m.close() }, 'Cancel'),
      h('button', {
        class: 'btn btn-primary', type: 'button', onclick: async () => {
          const payload = { ...opts };
          if (payload._custom) { payload.prompt = payload._custom.value.trim(); delete payload._custom; }
          if ((type === 'report' && payload.format === 'custom') || (type === 'datatable' && payload.kind === 'custom')) {
            if (!payload.prompt) { toast('Describe what you want first.'); return; }
          }
          payload.focus = focus.value.trim();
          payload.language = lang.value();
          m.close();
          await startArtifact(type, payload);
        },
      }, icon('auto_awesome'), 'Generate'),
    ],
  });
}

// ── Viewer ───────────────────────────────────────────────────────

function showViewer(titleText, actions = []) {
  ui.body.classList.add('hidden');
  ui.foot.classList.add('hidden');
  ui.viewer.classList.remove('hidden');
  ui.title.replaceChildren(
    h('span', { class: 'crumb', role: 'button', tabindex: '0', onclick: closeViewer }, 'Studio'),
    h('span', { class: 'sep' }, ' › '),
    h('span', { class: 'current' }, titleText));
  ui.headActions.replaceChildren(...actions,
    iconBtn('fullscreen', 'Full screen', toggleFullscreen),
    iconBtn('close', 'Close', closeViewer));
  layout.toggle('studio-expanded', true);
  layout.setTab('studio');
}

function toggleFullscreen() {
  ui.panel.classList.toggle('fullscreen-viewer');
  const btn = ui.headActions.querySelector('[aria-label="Full screen"], [aria-label="Exit full screen"]');
  if (btn) {
    const on = ui.panel.classList.contains('fullscreen-viewer');
    btn.replaceChildren(icon(on ? 'fullscreen_exit' : 'fullscreen'));
    btn.setAttribute('aria-label', on ? 'Exit full screen' : 'Full screen');
  }
}

export function closeViewer() {
  if (viewerCleanup) { try { viewerCleanup(); } catch { /* ignore */ } viewerCleanup = null; }
  openItem = null;
  ui.panel.classList.remove('fullscreen-viewer');
  ui.viewer.classList.add('hidden');
  ui.viewer.replaceChildren();
  ui.body.classList.remove('hidden');
  ui.foot.classList.remove('hidden');
  ui.title.replaceChildren('Studio');
  ui.headActions.replaceChildren(ui.collapseBtn);
  layout.toggle('studio-expanded', false);
  renderList();
}

let lastArtifactJson = '';

export async function openArtifact(id, opts = {}) {
  if (viewerCleanup) { try { viewerCleanup(); } catch { /* ignore */ } viewerCleanup = null; }
  openItem = { kind: 'artifact', id, opts };
  const summary = store.artifacts.find((a) => a.id === id);
  showViewer(summary ? summary.title : 'Loading…');
  ui.viewer.replaceChildren(h('div', { class: 'viewer-progress' }, h('span', { class: 'spinner' })));
  await loadAndRender(true);
}

async function loadAndRender(first = false) {
  if (!openItem || openItem.kind !== 'artifact') return;
  const id = openItem.id;
  let art;
  try { art = (await api.get(`/api/artifacts/${id}`)).artifact; } catch (e) { notifyError(e); closeViewer(); return; }
  if (!openItem || openItem.id !== id) return;
  const json = JSON.stringify([art.status, art.status_message, art.title, art.content, art.error]);
  if (!first && json === lastArtifactJson) return;
  lastArtifactJson = json;
  showViewer(art.title, []);
  if (viewerCleanup && art.status !== 'processing') { try { viewerCleanup(); } catch { /* ignore */ } viewerCleanup = null; }

  if (art.status === 'error') {
    ui.viewer.replaceChildren(h('div', { class: 'viewer-error' }, h('div', {}, art.error || 'Generation failed.'),
      h('div', { class: 'actions' },
        h('button', { class: 'btn btn-tonal small', type: 'button', onclick: () => retryArtifact(art) }, icon('refresh'), 'Try again'),
        h('button', { class: 'btn btn-text small', type: 'button', onclick: () => deleteArtifact(art) }, 'Delete'))));
    return;
  }
  if (art.status === 'processing' && !((art.type === 'notes' || art.type === 'report') && art.content && art.content.markdown)) {
    ui.viewer.replaceChildren(h('div', { class: 'viewer-progress' }, h('span', { class: 'spinner' }),
      h('div', { class: 'big' }, art.status_message || 'Generating…'),
      h('div', {}, art.type === 'audio' || art.type === 'video' ? 'Audio takes a few minutes. You can close this and keep studying — it will appear in Studio when ready.' : 'This usually takes under a minute.')));
    return;
  }
  const module = await loadViewer(art.type);
  const host = h('div', { class: 'viewer-body scroll' });
  const toolbar = h('div', { class: 'viewer-toolbar' });
  ui.viewer.replaceChildren(toolbar, host);
  viewerCleanup = module.render(host, art, { toolbar, setTitle: (t) => updateTitle(art, t), autoplay: openItem.opts?.autoplay, reload: () => loadAndRender(true) }) || null;
}

async function updateTitle(art, title) {
  try { await api.patch(`/api/artifacts/${art.id}`, { title }); store.pokeSoon(); } catch (e) { notifyError(e); }
}

function refreshOpenArtifact() {
  if (!openItem || openItem.kind !== 'artifact') return;
  const a = store.artifacts.find((x) => x.id === openItem.id);
  if (!a) { closeViewer(); return; }
  const viewerShowsFinal = lastArtifactJson.startsWith('["ready"');
  if (a.status === 'processing' || !viewerShowsFinal) loadAndRender(false);
}

async function loadViewer(type) {
  const map = {
    notes: '../viewers/doc.js', report: '../viewers/doc.js', flashcards: '../viewers/flashcards.js', quiz: '../viewers/quiz.js',
    mindmap: '../viewers/mindmap.js', slides: '../viewers/slides.js', infographic: '../viewers/infographic.js',
    datatable: '../viewers/datatable.js', audio: '../viewers/audio.js', video: '../viewers/video.js',
  };
  return import(map[type]);
}

export async function openNote(id, opts = {}) {
  if (viewerCleanup) { try { viewerCleanup(); } catch { /* ignore */ } viewerCleanup = null; }
  openItem = { kind: 'note', id };
  showViewer('Note');
  ui.viewer.replaceChildren(h('div', { class: 'viewer-progress' }, h('span', { class: 'spinner' })));
  let note;
  try { note = (await api.get(`/api/notes/${id}`)).note; } catch (e) { notifyError(e); closeViewer(); return; }
  showViewer(note.title);
  const module = await import('../viewers/doc.js');
  const host = h('div', { class: 'viewer-body scroll' });
  const toolbar = h('div', { class: 'viewer-toolbar' });
  ui.viewer.replaceChildren(toolbar, host);
  viewerCleanup = module.renderNote(host, note, { toolbar, edit: opts.edit, onSaved: () => store.pokeSoon() }) || null;
}
