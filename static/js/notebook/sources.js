// Sources panel: add / search / select / inspect sources.

import { api, upload } from '../lib/api.js';
import { h, icon, iconBtn, fmtTime, plural, timeAgo, debounce, svgIcon } from '../lib/dom.js';
import { confirmDialog, modal, notifyError, openMenu, promptDialog, toast } from '../lib/ui.js';
import { renderMarkdown } from '../lib/markdown.js';
import { store } from './store.js';
import { layout } from './page.js';
import { askInChat } from './chat.js';

const ACCEPT = '.pdf,.docx,.pptx,.txt,.md,.markdown,.csv,.json,.html,.htm,.srt,.vtt,.ipynb,.py,.js,.ts,.java,.c,.cpp,.cs,.go,.sql,'
  + '.mp4,.mov,.webm,.mkv,.avi,.m4v,.mpeg,.mpg,.3gp,.wmv,.mp3,.wav,.m4a,.aac,.ogg,.flac,.opus,.png,.jpg,.jpeg,.webp,.heic,.gif';

const KIND_ICON = {
  pdf: ['picture_as_pdf', 'src-pdf'], website: ['language', 'src-web'], docx: ['description', 'src-doc'],
  pptx: ['slideshow', 'src-slides'], text: ['text_snippet', 'src-text'], paste: ['content_paste', 'src-text'],
  video: ['movie', 'src-video'], audio: ['graphic_eq', 'src-audio'], image: ['image', 'src-image'],
  subtitles: ['subtitles', 'src-text'], note: ['sticky_note_2', 'src-note'],
};

export function sourceIcon(kind) {
  if (kind === 'youtube') {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('width', '22');
    svg.setAttribute('height', '22');
    svg.setAttribute('aria-hidden', 'true');
    svg.innerHTML = '<rect x="1.5" y="4.5" width="21" height="15" rx="4.2" fill="#e60000"/><path d="M10 8.6v6.8l5.8-3.4z" fill="#fff"/>';
    return svg;
  }
  const [name, cls] = KIND_ICON[kind] || ['article', 'src-text'];
  return icon(name, cls);
}

const ui = {};
let mode = 'list';            // list | view
let viewingId = null;
let filterText = '';
let searchState = { scope: 'web', mode: 'fast', results: null, summary: '', loading: false, selected: new Set() };

export function mountSources() {
  const panel = h('section', { class: 'panel sources-panel', 'aria-label': 'Sources' });
  ui.panel = panel;

  ui.rail = h('div', { class: 'rail' },
    iconBtn('dock_to_left', 'Expand sources', () => setCollapsed(false)),
    iconBtn('add', 'Add sources', openAddSources),
  );
  ui.title = h('div', { class: 'panel-title' }, 'Sources');
  ui.collapseBtn = iconBtn('dock_to_right', 'Collapse sources panel', () => setCollapsed(true), 'collapse-btn');
  ui.head = h('div', { class: 'panel-head' }, ui.title, ui.collapseBtn);
  ui.body = h('div', { class: 'sources-body', style: { display: 'flex', flexDirection: 'column', flex: '1', minHeight: '0' } });

  panel.append(ui.rail, ui.head, ui.body);
  installDrop(panel);

  store.on('sources', () => { if (mode === 'list') renderList(); else renderViewerHead(); refreshRail(); });
  store.on('notebook', () => { if (mode === 'list') renderList(); });
  renderList();
  return panel;
}

function setCollapsed(on) {
  ui.panel.classList.toggle('collapsed', on);
  layout.toggle('sources-collapsed', on);
  if (on && mode === 'view') closeViewer();
}

function refreshRail() {
  ui.rail.querySelectorAll('.rail-src').forEach((e) => e.remove());
  for (const s of store.sources.slice(0, 12)) {
    ui.rail.append(h('button', { class: 'icon-btn rail-src', type: 'button', 'data-tip': s.title, onclick: () => { setCollapsed(false); openSource(s.id); } }, sourceIcon(s.kind)));
  }
}

// ── List view ────────────────────────────────────────────────────

function renderList() {
  mode = 'list';
  layout.toggle('sources-expanded', false);
  ui.title.replaceChildren('Sources');
  ui.collapseBtn.classList.remove('hidden');

  const addBtn = h('button', { class: 'add-sources', type: 'button', onclick: openAddSources }, icon('add'), 'Add sources');
  const body = [addBtn, webSearchBox()];
  if (searchState.results || searchState.loading) body.push(webResults());

  const sources = store.sources;
  if (!sources.length) {
    body.push(h('div', { class: 'sources-empty' },
      icon('description'),
      h('strong', {}, 'Saved sources will appear here'),
      h('span', {}, 'Add files, websites, or more. Then ask questions or create things based on these sources.'),
      h('span', {}, 'Drop files here or ', h('a', { class: 'link', href: '#', onclick: (e) => { e.preventDefault(); openAddSources(); } }, 'add a source'))));
    ui.body.replaceChildren(...body);
    return;
  }

  const ready = sources.filter((s) => s.status === 'ready');
  const selected = ready.filter((s) => s.selected);
  const allBox = h('span', { class: `checkbox ${ready.length && selected.length === ready.length ? 'checked' : selected.length ? 'partial' : ''}` });
  const head = h('div', { class: 'source-list-head' },
    h('button', { class: 'icon-btn sm', type: 'button', 'aria-label': 'Filter sources', 'data-tip': 'Filter & sort', onclick: (e) => filterMenu(e.currentTarget) }, icon('sort')),
    h('button', { class: 'select-all', type: 'button', onclick: () => selectAll(!(selected.length === ready.length && ready.length)) }, 'Select all', allBox));
  body.push(head);

  if (filterText || ui.showFilter) {
    const input = h('input', { type: 'text', placeholder: 'Filter sources', value: filterText, 'aria-label': 'Filter sources' });
    input.addEventListener('input', debounce(() => { filterText = input.value; renderItems(listEl); }, 120));
    body.push(h('div', { class: 'source-filter' }, icon('search', 'sm'), input,
      iconBtn('close', 'Clear filter', () => { filterText = ''; ui.showFilter = false; renderList(); }, 'xs')));
    setTimeout(() => input.focus(), 0);
  }

  const listEl = h('div', { class: 'source-list scroll' });
  renderItems(listEl);
  body.push(listEl);
  ui.body.replaceChildren(...body);
}

function sortedSources() {
  let list = [...store.sources];
  if (ui.sort === 'title') list.sort((a, b) => a.title.localeCompare(b.title));
  else if (ui.sort === 'newest') list.sort((a, b) => b.created_at - a.created_at);
  if (filterText.trim()) {
    const q = filterText.toLowerCase();
    list = list.filter((s) => s.title.toLowerCase().includes(q) || s.kind.includes(q));
  }
  return list;
}

function renderItems(listEl) {
  const items = sortedSources().map(sourceItem);
  listEl.replaceChildren(...(items.length ? items : [h('div', { class: 'muted', style: { padding: '16px', fontSize: '13px' } }, 'No sources match.')]));
}

function sourceItem(s) {
  const processing = s.status === 'queued' || s.status === 'processing';
  let trail;
  if (processing) trail = h('span', { class: 's-status', 'data-tip': s.status_message || 'Processing…' }, h('span', { class: 'spinner' }));
  else if (s.status === 'error') trail = h('span', { class: 's-status', 'data-tip': s.error || 'Failed' }, icon('error', 'err'));
  else {
    trail = h('button', {
      class: 's-status', type: 'button', role: 'checkbox', 'aria-checked': String(s.selected), 'aria-label': `Use ${s.title}`,
      onclick: (e) => { e.stopPropagation(); toggleSelected(s); },
    }, h('span', { class: `checkbox ${s.selected ? 'checked' : ''}` }));
  }
  const warn = s.status === 'ready' && s.warnings && s.warnings.length
    ? h('span', { class: 's-status', 'data-tip': s.warnings[0] }, icon('warning', 'warn')) : null;
  return h('div', {
    class: `source-item ${processing ? 'processing' : ''} ${s.status === 'error' ? 'error' : ''}`,
    role: 'button', tabindex: '0', 'data-id': s.id,
    onclick: () => openSource(s.id),
    onkeydown: (e) => { if (e.key === 'Enter') openSource(s.id); },
    title: processing ? (s.status_message || 'Processing…') : s.status === 'error' ? s.error : s.title,
  },
  h('span', { class: 's-icon' }, sourceIcon(s.kind)),
  h('span', { class: 's-title' }, s.title),
  h('span', { class: 's-trail' },
    h('button', { class: 'icon-btn xs s-more', type: 'button', 'aria-label': 'Source options', onclick: (e) => { e.stopPropagation(); sourceMenu(e.currentTarget, s); } }, icon('more_vert', 'sm')),
    warn, trail));
}

function filterMenu(anchor) {
  openMenu(anchor, [
    { icon: 'search', label: 'Filter by name', run: () => { ui.showFilter = true; renderList(); } },
    '-',
    { label: 'Sort', header: true },
    { label: 'Order added', checked: !ui.sort, run: () => { ui.sort = ''; renderList(); } },
    { label: 'Newest first', checked: ui.sort === 'newest', run: () => { ui.sort = 'newest'; renderList(); } },
    { label: 'Title (A–Z)', checked: ui.sort === 'title', run: () => { ui.sort = 'title'; renderList(); } },
  ], { align: 'start' });
}

function sourceMenu(anchor, s) {
  openMenu(anchor, [
    { icon: 'open_in_full', label: 'Open', run: () => openSource(s.id) },
    { icon: 'edit', label: 'Rename source', run: () => renameSource(s) },
    s.status === 'error' || s.status === 'ready' ? { icon: 'refresh', label: s.status === 'error' ? 'Retry' : 'Re-process', run: () => retrySource(s) } : null,
    s.url ? { icon: 'open_in_new', label: 'Open original', run: () => window.open(s.url, '_blank', 'noopener') } : null,
    '-',
    { icon: 'delete', label: 'Remove source', danger: true, run: () => removeSource(s) },
  ]);
}

async function toggleSelected(s) {
  s.selected = !s.selected;
  store.emit('sources', store.sources);
  try { await api.patch(`/api/sources/${s.id}`, { selected: s.selected }); } catch (e) { notifyError(e); }
  store.pokeSoon();
}

async function selectAll(on) {
  store.sources.forEach((s) => { if (s.status === 'ready') s.selected = on; });
  store.emit('sources', store.sources);
  try { await api.post(`/api/notebooks/${store.notebookId}/sources/select`, { selected: on }); } catch (e) { notifyError(e); }
  store.pokeSoon();
}

async function renameSource(s) {
  const title = await promptDialog({ title: 'Rename source', value: s.title });
  if (!title) return;
  try { await api.patch(`/api/sources/${s.id}`, { title }); store.pokeSoon(); } catch (e) { notifyError(e); }
}

async function retrySource(s) {
  try { await api.post(`/api/sources/${s.id}/retry`); store.pokeSoon(); toast('Processing the source again…'); } catch (e) { notifyError(e); }
}

async function removeSource(s) {
  const ok = await confirmDialog({ title: 'Remove source?', message: `“${s.title}” will be removed from this notebook.`, confirm: 'Remove' });
  if (!ok) return;
  try {
    await api.del(`/api/sources/${s.id}`);
    if (viewingId === s.id) closeViewer();
    store.pokeSoon();
    toast('Source removed', { type: 'success' });
  } catch (e) { notifyError(e); }
}

// ── Web search ───────────────────────────────────────────────────

function webSearchBox() {
  const input = h('input', { type: 'text', placeholder: 'Search the web for new sources', 'aria-label': 'Search the web for new sources', value: ui.searchQuery || '' });
  const go = h('button', { class: `icon-btn sm ws-go ${ui.searchQuery ? 'ready' : ''}`, type: 'button', 'aria-label': 'Search', onclick: () => runSearch(input.value) }, icon('arrow_forward'));
  input.addEventListener('input', () => { ui.searchQuery = input.value; go.classList.toggle('ready', !!input.value.trim()); });
  input.addEventListener('keydown', (e) => { if (e.key === 'Enter') runSearch(input.value); });
  const scopeChip = h('button', {
    class: 'ws-chip', type: 'button', 'data-tip': searchState.scope === 'youtube' ? 'Searching YouTube' : 'Searching the web',
    onclick: (e) => openMenu(e.currentTarget, [
      { icon: 'language', label: 'Web', checked: searchState.scope === 'web', run: () => { searchState.scope = 'web'; renderList(); } },
      { icon: 'smart_display', label: 'YouTube', checked: searchState.scope === 'youtube', run: () => { searchState.scope = 'youtube'; renderList(); } },
    ], { align: 'start' }),
  }, icon(searchState.scope === 'youtube' ? 'smart_display' : 'language'), icon('keyboard_arrow_down', 'caret'));
  const modeChip = h('button', {
    class: 'ws-chip', type: 'button', 'data-tip': searchState.mode === 'deep' ? 'Deep research' : 'Fast research',
    onclick: (e) => openMenu(e.currentTarget, [
      { icon: 'bolt', label: 'Fast research', checked: searchState.mode === 'fast', run: () => { searchState.mode = 'fast'; renderList(); } },
      { icon: 'manage_search', label: 'Deep research', checked: searchState.mode === 'deep', run: () => { searchState.mode = 'deep'; renderList(); } },
    ], { align: 'start' }),
  }, svgIcon('research', 'ws-svg'), icon('keyboard_arrow_down', 'caret'));
  return h('div', { class: 'web-search' }, input, h('div', { class: 'web-search-row' }, scopeChip, modeChip, go));
}

async function runSearch(query) {
  query = (query || '').trim();
  if (query.length < 2) { toast('Type what you want to learn about.'); return; }
  if (!store.ai.search) { toast(store.ai.configured ? 'Web search needs a Gemini API key.' : store.ai.message || 'AI is not configured.', { type: 'error' }); return; }
  searchState = { ...searchState, loading: true, results: null, summary: '', selected: new Set(), query };
  renderList();
  try {
    const res = await api.post(`/api/notebooks/${store.notebookId}/web-search`, { query, mode: searchState.mode, scope: searchState.scope });
    searchState.results = res.results;
    searchState.summary = res.summary;
    searchState.selected = new Set(res.results.map((r) => r.url));
  } catch (e) {
    notifyError(e);
    searchState.results = null;
  }
  searchState.loading = false;
  renderList();
}

function webResults() {
  if (searchState.loading) {
    return h('div', { class: 'ws-results' }, h('div', { class: 'ws-results-head' }, h('span', { class: 'spinner sm' }),
      h('span', { class: 'grow' }, searchState.mode === 'deep' ? 'Researching the web in depth…' : 'Searching the web…')));
  }
  const results = searchState.results || [];
  const list = h('div', { class: 'scroll', style: { overflowY: 'auto', minHeight: '0' } });
  for (const r of results) {
    const box = h('span', { class: `checkbox ${searchState.selected.has(r.url) ? 'checked' : ''}` });
    list.append(h('div', {
      class: 'ws-result', role: 'checkbox', 'aria-checked': String(searchState.selected.has(r.url)),
      onclick: () => {
        if (searchState.selected.has(r.url)) searchState.selected.delete(r.url); else searchState.selected.add(r.url);
        box.classList.toggle('checked');
      },
    }, box, h('div', { class: 'meta' }, h('div', { class: 't' }, r.title), h('div', { class: 'd' }, r.domain || r.url))));
  }
  const close = () => { searchState.results = null; renderList(); };
  return h('div', { class: 'ws-results' },
    h('div', { class: 'ws-results-head' }, icon('travel_explore', 'sm'), h('span', { class: 'grow' }, results.length ? `${plural(results.length, 'source')} found` : 'No sources found'), iconBtn('close', 'Close results', close, 'xs')),
    searchState.summary ? h('div', { class: 'ws-summary' }, searchState.summary.slice(0, 400)) : null,
    list,
    results.length ? h('div', { class: 'ws-actions' },
      h('button', { class: 'btn btn-text small', type: 'button', onclick: close }, 'Dismiss'),
      h('button', { class: 'btn btn-primary small', type: 'button', onclick: importResults }, icon('add'), 'Import')) : null);
}

async function importResults() {
  const urls = [...searchState.selected];
  if (!urls.length) { toast('Select at least one result.'); return; }
  try {
    const res = await api.post(`/api/notebooks/${store.notebookId}/web-search/import`, { urls });
    toast(`Adding ${plural(res.sources.length, 'source')}…`, { type: 'success' });
    if (res.errors && res.errors.length) toast(res.errors[0].error, { type: 'error' });
    searchState.results = null;
    store.pokeSoon();
    renderList();
  } catch (e) { notifyError(e); }
}

// ── Add sources dialog ───────────────────────────────────────────

export function openAddSources(initial = '') {
  const view = h('div');
  let dlg;

  const showMain = () => {
    const fileInput = h('input', { type: 'file', multiple: true, accept: ACCEPT, class: 'hidden' });
    fileInput.addEventListener('change', () => { uploadFiles([...fileInput.files]); dlg.close(); });
    const zone = h('div', { class: 'dropzone' },
      icon('upload'),
      h('div', { class: 'big' }, 'Upload sources'),
      h('div', {}, 'Drag & drop or ', h('a', { href: '#', onclick: (e) => { e.preventDefault(); fileInput.click(); } }, 'choose files'), ' to upload'),
      h('div', { class: 'types' }, 'PDF, Word, PowerPoint, text/Markdown, subtitles (SRT/VTT), lecture videos (MP4, MOV, WebM), audio (MP3, WAV, M4A), images of notes'),
      fileInput);
    zone.addEventListener('dragover', (e) => { e.preventDefault(); zone.classList.add('over'); });
    zone.addEventListener('dragleave', () => zone.classList.remove('over'));
    zone.addEventListener('drop', (e) => { e.preventDefault(); zone.classList.remove('over'); uploadFiles([...e.dataTransfer.files]); dlg.close(); });
    view.replaceChildren(
      h('p', { class: 'lead' }, 'Sources let the AI base its answers, notes and quizzes on the material that matters to you — lecture videos, slides, course notes, papers or web pages.'),
      zone,
      h('div', { class: 'source-options' },
        option('smart_display', 'YouTube', 'Paste a lecture or tutorial link', () => showLinks('youtube')),
        option('language', 'Website', 'Import an article or docs page', () => showLinks('website')),
        option('content_paste', 'Paste text', 'Notes, transcripts, anything', showPaste)));
  };

  const option = (ic, t, d, fn) => h('button', { class: 'src-opt', type: 'button', onclick: fn }, icon(ic), h('div', { class: 't' }, t), h('div', { class: 'd' }, d));

  const showLinks = (kind) => {
    const ta = h('textarea', { class: 'field', rows: 4, placeholder: kind === 'youtube' ? 'https://www.youtube.com/watch?v=…\nhttps://youtu.be/…' : 'https://…' });
    ta.value = initial;
    const submit = async () => {
      const urls = ta.value.split(/\s+/).map((u) => u.trim()).filter(Boolean);
      if (!urls.length) { toast('Paste at least one link.'); return; }
      btn.disabled = true;
      try {
        const res = await api.post(`/api/notebooks/${store.notebookId}/sources/url`, { urls });
        if (res.errors && res.errors.length) toast(res.errors[0].error, { type: 'error' });
        if (res.sources.length) toast(`Adding ${plural(res.sources.length, 'source')}…`, { type: 'success' });
        dlg.close();
        store.pokeSoon();
      } catch (e) { notifyError(e); btn.disabled = false; }
    };
    const btn = h('button', { class: 'btn btn-primary', type: 'button', onclick: submit }, 'Insert');
    view.replaceChildren(
      h('button', { class: 'btn btn-text small', type: 'button', onclick: showMain }, icon('arrow_back'), 'Back'),
      h('label', { class: 'label' }, kind === 'youtube' ? 'Paste YouTube URLs (one per line)' : 'Paste web page URLs (one per line)'),
      ta,
      h('p', { class: 'lead', style: { marginTop: '10px' } }, kind === 'youtube'
        ? 'The AI reads the captions and watches the video itself (slides, code and diagrams on screen) to build understanding-level notes. Only public videos can be imported.'
        : 'Only the visible text of the page is imported. Pages behind a login or that need JavaScript may not work — use Paste text instead.'),
      h('div', { style: { display: 'flex', justifyContent: 'flex-end' } }, btn));
    setTimeout(() => ta.focus(), 30);
  };

  const showPaste = () => {
    const title = h('input', { class: 'field', type: 'text', placeholder: 'Title (optional)' });
    const ta = h('textarea', { class: 'field', rows: 12, placeholder: 'Paste your notes, a transcript, an article…' });
    const btn = h('button', {
      class: 'btn btn-primary', type: 'button', onclick: async () => {
        if (ta.value.trim().length < 20) { toast('Paste a bit more text.'); return; }
        btn.disabled = true;
        try {
          await api.post(`/api/notebooks/${store.notebookId}/sources/text`, { title: title.value.trim() || 'Pasted text', text: ta.value });
          dlg.close();
          store.pokeSoon();
        } catch (e) { notifyError(e); btn.disabled = false; }
      },
    }, 'Insert');
    view.replaceChildren(
      h('button', { class: 'btn btn-text small', type: 'button', onclick: showMain }, icon('arrow_back'), 'Back'),
      h('label', { class: 'label' }, 'Title'), title,
      h('label', { class: 'label' }, 'Text'), ta,
      h('div', { style: { display: 'flex', justifyContent: 'flex-end', marginTop: '12px' } }, btn));
    setTimeout(() => ta.focus(), 30);
  };

  dlg = modal({ title: 'Add sources', body: view, size: 'lg' });
  if (initial && /youtu/.test(initial)) showLinks('youtube'); else showMain();
}

export async function uploadFiles(files) {
  if (!files.length) return;
  layout.setTab('sources');
  for (const file of files) {
    const row = h('div', { class: 'upload-row' }, icon('upload_file', 'sm'), h('span', { class: 'name' }, file.name), h('div', { class: 'upload-bar' }, h('div', { style: { width: '0%' } })));
    const t = toast('', { timeout: 600000 });
    t.replaceChildren(row);
    try {
      await upload(`/api/notebooks/${store.notebookId}/sources/upload`, file, (p) => { row.querySelector('.upload-bar > div').style.width = `${Math.round(p * 100)}%`; });
      t.remove();
      toast(`Added “${file.name}” — processing…`, { type: 'success' });
      store.pokeSoon();
    } catch (e) {
      t.remove();
      notifyError(e);
    }
  }
}

function installDrop(panel) {
  let overlay = null;
  let depth = 0;
  panel.addEventListener('dragenter', (e) => {
    if (!e.dataTransfer || ![...e.dataTransfer.types].includes('Files')) return;
    depth += 1;
    if (!overlay) { overlay = h('div', { class: 'drop-overlay' }, icon('upload'), 'Drop files to add them as sources'); panel.append(overlay); }
  });
  panel.addEventListener('dragleave', () => { depth -= 1; if (depth <= 0 && overlay) { overlay.remove(); overlay = null; depth = 0; } });
  panel.addEventListener('dragover', (e) => e.preventDefault());
  panel.addEventListener('drop', (e) => {
    e.preventDefault();
    depth = 0;
    if (overlay) { overlay.remove(); overlay = null; }
    if (e.dataTransfer && e.dataTransfer.files.length) uploadFiles([...e.dataTransfer.files]);
  });
}

// ── Source viewer ────────────────────────────────────────────────

let player = null;

export async function openSource(id, { chunkId = null, seconds = null } = {}) {
  setCollapsed(false);
  layout.setTab('sources');
  mode = 'view';
  viewingId = id;
  layout.toggle('sources-expanded', true);
  renderViewerHead();
  const container = h('div', { class: 'viewer-body scroll' }, h('div', { class: 'viewer-progress' }, h('span', { class: 'spinner' })));
  ui.body.replaceChildren(container);
  let src;
  try {
    src = (await api.get(`/api/sources/${id}`)).source;
  } catch (e) { notifyError(e); closeViewer(); return; }
  if (viewingId !== id) return;
  container.replaceChildren(sourceView(src));
  if (chunkId) flashChunk(container, chunkId);
  if (seconds !== null && seconds !== undefined) seekPlayer(seconds);
}

function renderViewerHead() {
  if (mode !== 'view') return;
  const s = store.sourceById(viewingId);
  ui.title.replaceChildren(
    h('span', { class: 'crumb', role: 'button', tabindex: '0', onclick: closeViewer }, 'Sources'),
    h('span', { class: 'sep' }, ' › '),
    h('span', { class: 'current' }, s ? s.title : ''));
  ui.collapseBtn.classList.add('hidden');
  if (!ui.closeView) ui.closeView = iconBtn('close', 'Close source', closeViewer);
  if (!ui.head.contains(ui.closeView)) ui.head.append(ui.closeView);
}

function closeViewer() {
  mode = 'list';
  viewingId = null;
  player = null;
  if (ui.closeView) ui.closeView.remove();
  renderList();
}

function flashChunk(container, chunkId) {
  const el = container.querySelector(`[data-chunk="${chunkId}"]`);
  if (!el) return;
  el.scrollIntoView({ block: 'center' });
  el.classList.add('flash');
  setTimeout(() => el.classList.remove('flash'), 2200);
}

export function seekPlayer(seconds) {
  if (!player) return false;
  const send = (func, args = []) => player.contentWindow && player.contentWindow.postMessage(JSON.stringify({ event: 'command', func, args }), '*');
  send('seekTo', [Math.max(0, seconds), true]);
  send('playVideo');
  player.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  return true;
}

function sourceView(src) {
  const wrap = h('div');
  const meta = src.meta || {};
  if (src.status === 'error') {
    wrap.append(h('div', { class: 'err-box' }, icon('error', 'sm'), h('div', {}, h('div', {}, src.error || 'This source failed to process.'),
      h('div', { style: { marginTop: '10px', display: 'flex', gap: '8px' } },
        h('button', { class: 'btn btn-tonal small', type: 'button', onclick: () => retrySource(src) }, icon('refresh'), 'Retry'),
        h('button', { class: 'btn btn-text small', type: 'button', onclick: () => removeSource(src) }, 'Remove')))));
  }
  if (src.status === 'queued' || src.status === 'processing') {
    wrap.append(h('div', { class: 'viewer-progress' }, h('span', { class: 'spinner' }), h('div', { class: 'big' }, src.status_message || 'Processing…'),
      h('div', {}, 'Long lectures can take a few minutes — the AI is watching the whole video.')));
  }
  if (src.kind === 'youtube' && meta.video_id) {
    player = h('iframe', {
      class: 'yt-frame', title: src.title, allowfullscreen: true, loading: 'lazy',
      allow: 'accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; fullscreen',
      referrerpolicy: 'strict-origin-when-cross-origin',
      src: `https://www.youtube-nocookie.com/embed/${encodeURIComponent(meta.video_id)}?enablejsapi=1&rel=0&modestbranding=1&origin=${encodeURIComponent(window.location.origin)}`,
    });
    wrap.append(player);
  } else {
    player = null;
  }

  const facts = [];
  if (meta.channel) facts.push(meta.channel);
  if (meta.duration) facts.push(fmtTime(meta.duration));
  if (meta.captions) facts.push({ manual: 'Captions', 'auto-generated': 'Auto-captions', 'ai-transcribed': 'AI transcript', subtitles: 'Subtitles' }[meta.captions] || meta.captions);
  if (meta.visual_analysis) facts.push('Video analysed by AI');
  if (meta.page_count) facts.push(plural(meta.page_count, 'page'));
  if (meta.slide_count) facts.push(plural(meta.slide_count, 'slide'));
  if (meta.domain) facts.push(meta.domain);
  if (src.char_count) facts.push(`${Math.round(src.char_count / 1000)}k characters`);
  facts.push(`Added ${timeAgo(src.created_at)}`);
  wrap.append(h('div', { class: 'doc-meta' }, facts.join(' · ')));

  for (const w of src.warnings || []) wrap.append(h('div', { class: 'warn-box' }, icon('warning', 'sm'), h('span', {}, w)));

  const guide = src.guide || {};
  if (guide.summary) {
    const g = h('div', { class: 'src-guide' }, h('h3', {}, icon('auto_awesome', 'sm'), 'Source guide'),
      renderMarkdown(guide.summary));
    if (guide.key_topics && guide.key_topics.length) {
      g.append(h('div', { class: 'topic-chips' }, ...guide.key_topics.map((t) => h('button', {
        class: 'chip', type: 'button', 'data-tip': 'Ask about this', onclick: () => askInChat(`Explain "${t}" from ${src.title} in detail, with an example.`),
      }, t))));
    }
    wrap.append(g);
  }
  if (guide.chapters && guide.chapters.length) {
    const box = h('div', { class: 'src-guide' }, h('h3', {}, icon('timeline', 'sm'), 'Chapters'));
    const list = h('div', { class: 'chapters' });
    for (const c of guide.chapters) {
      list.append(h('div', {
        class: 'chapter', role: 'button', tabindex: '0', title: c.summary,
        onclick: () => { if (!seekPlayer(c.start || 0)) toast(c.summary); },
      }, h('span', { class: 'tm' }, c.start !== null && c.start !== undefined ? fmtTime(c.start) : '—'), h('span', {}, c.title)));
    }
    box.append(list);
    wrap.append(box);
  }
  if (guide.key_terms && guide.key_terms.length) {
    const box = h('div', { class: 'src-guide' }, h('h3', {}, icon('menu_book', 'sm'), 'Key terms'));
    const md = guide.key_terms.map((t) => `- **${t.term}** — ${t.definition}`).join('\n');
    box.append(renderMarkdown(md));
    wrap.append(box);
  }

  if (src.chunks && src.chunks.length) {
    wrap.append(h('div', { class: 'section-title' }, src.kind === 'youtube' || src.kind === 'video' || src.kind === 'audio' ? 'Transcript & on-screen content' : 'Content'));
    for (const c of src.chunks) {
      const visual = c.text.startsWith('[On screen');
      const el = h('div', { class: `chunk ${visual ? 'visual' : ''}`, 'data-chunk': String(c.id) });
      if (c.start_sec !== null && c.start_sec !== undefined) {
        el.append(h('span', { class: 'tm', role: 'button', tabindex: '0', onclick: () => seekPlayer(c.start_sec) }, fmtTime(c.start_sec)));
      } else if (c.page) {
        el.append(h('span', { class: 'tm' }, `${src.kind === 'pptx' ? 'Slide' : 'p.'} ${c.page}`));
      }
      el.append(document.createTextNode(c.text));
      wrap.append(el);
    }
  }
  return wrap;
}
