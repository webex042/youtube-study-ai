// Header dialogs: settings, share/export, learning progress, bookmarks, search, notebook switcher.

import { api } from '../lib/api.js';
import { h, icon, iconBtn, copyText, debounce, fmtTime, plural, timeAgo } from '../lib/dom.js';
import { modal, notifyError, openMenu, toast } from '../lib/ui.js';
import { store } from './store.js';

// ── Settings ─────────────────────────────────────────────────────

export async function openSettings() {
  let status = {};
  try { status = (await api.get('/api/system/status')).status; } catch (e) { notifyError(e); }
  const name = h('input', { class: 'field', type: 'text', placeholder: 'Your name', maxlength: '60' });
  name.value = store.settings.display_name || '';
  const pro = h('input', { type: 'checkbox' });
  pro.checked = !!store.settings.pro_mode;
  const testOut = h('div', { class: 'muted', style: { fontSize: '13px', marginTop: '8px' } });
  const testBtn = h('button', {
    class: 'btn btn-tonal small', type: 'button', onclick: async () => {
      testBtn.disabled = true;
      testOut.textContent = 'Testing…';
      try {
        const res = await api.post('/api/system/test-ai');
        testOut.textContent = res.ok ? `✅ ${res.message}` : `❌ ${res.message}`;
      } catch (e) { testOut.textContent = `❌ ${e.message}`; }
      testBtn.disabled = false;
    },
  }, icon('network_check'), 'Test AI connection');

  const aiRows = status.configured
    ? [
      row('Provider', { gemini: 'Google Gemini', openai: 'OpenAI (text only — no video, audio or web search)' }[status.provider] || status.provider),
      row('Models tried in order', (status.models?.default || []).join(' → ')),
      row('Pro mode models', (status.models?.pro || []).join(' → ')),
      row('Speech (Audio Overview)', status.tts ? (status.models?.tts || []).join(' → ') : 'Needs Gemini'),
      row('Video understanding', status.media && status.video_analysis ? 'On — videos are watched (frames + audio)' : 'Off (captions only)'),
      row('Max upload', `${status.max_upload_mb} MB`),
    ]
    : [h('div', { class: 'warn-box' }, icon('key', 'sm'), h('span', {}, status.message || 'AI is not configured.'))];

  const m = modal({
    title: 'Settings',
    body: h('div', {},
      h('label', { class: 'label' }, 'Display name'), name,
      h('label', { class: 'label', style: { display: 'flex', alignItems: 'center', gap: '10px', marginTop: '18px' } }, pro,
        'Pro mode — use the most capable model for chat and Studio (slower, higher cost)'),
      h('div', { class: 'section-title' }, 'AI connection'),
      ...aiRows,
      h('div', { style: { marginTop: '10px' } }, testBtn, testOut),
      h('p', { class: 'lead', style: { marginTop: '16px' } }, 'Keys and model names live in the .env file in the project folder (GEMINI_API_KEY, GEMINI_MODEL…). Restart the server after changing it.')),
    footer: [
      h('button', { class: 'btn btn-text', type: 'button', onclick: () => m.close() }, 'Cancel'),
      h('button', {
        class: 'btn btn-primary', type: 'button', onclick: async () => {
          try {
            const res = await api.put('/api/settings', { display_name: name.value.trim(), pro_mode: pro.checked });
            store.settings = res.settings;
            store.emit('settings', store.settings);
            toast('Settings saved', { type: 'success' });
            m.close();
          } catch (e) { notifyError(e); }
        },
      }, 'Save'),
    ],
  });
}

function row(label, value) {
  return h('div', { style: { display: 'flex', gap: '12px', padding: '7px 0', borderBottom: '1px solid #2a2a2a', fontSize: '13.5px' } },
    h('span', { class: 'muted', style: { width: '170px', flexShrink: '0' } }, label), h('span', { style: { wordBreak: 'break-word' } }, value || '—'));
}

// ── Share / export ───────────────────────────────────────────────

export function openShare() {
  const link = `${window.location.origin}/notebook/${store.notebookId}`;
  const m = modal({
    title: `Share “${store.notebook.title}”`, size: 'sm',
    body: h('div', {},
      h('p', { class: 'lead' }, 'This app runs on your computer, so the link only works on this device (or your local network if you expose the server). To share with classmates, export the notebook.'),
      h('div', { style: { display: 'flex', gap: '8px' } },
        h('input', { class: 'field', type: 'text', readonly: true, value: link }),
        h('button', { class: 'btn btn-tonal', type: 'button', onclick: async () => { if (await copyText(link)) toast('Link copied', { type: 'success' }); } }, icon('link'), 'Copy')),
      h('div', { class: 'section-title' }, 'Export'),
      h('div', { style: { display: 'flex', flexDirection: 'column', gap: '8px' } },
        h('button', { class: 'btn btn-outline', type: 'button', onclick: () => { window.location.href = `/api/notebooks/${store.notebookId}/export`; } }, icon('download'), 'Everything as Markdown (.md)'),
        h('button', { class: 'btn btn-outline', type: 'button', onclick: () => { window.location.href = `/api/notebooks/${store.notebookId}/export?include_chat=false`; } }, icon('description'), 'Notes & Studio only (.md)'),
        h('button', { class: 'btn btn-outline', type: 'button', onclick: () => { const w = window.open('', '_blank'); m.close(); exportPdf(w); } }, icon('picture_as_pdf'), 'Notes & Studio as PDF'))),
  });
}

async function exportPdf(win) {
  if (!win) { toast('Allow pop-ups for this site to export PDFs.', { type: 'error' }); return; }
  try {
    const res = await fetch(`/api/notebooks/${store.notebookId}/export?include_chat=false`);
    const text = await res.text();
    const { fillPrintWindow } = await import('../viewers/doc.js');
    fillPrintWindow(win, store.notebook.title, text);
  } catch (e) { notifyError(e); win.close(); }
}

// ── Progress ─────────────────────────────────────────────────────

export async function openProgress() {
  let data;
  try { data = (await api.get(`/api/notebooks/${store.notebookId}/progress`)).progress; } catch (e) { notifyError(e); return; }
  const fc = data.flashcards;
  const heat = h('div', { class: 'heat', title: 'Study activity, last 30 days' },
    ...data.activity.map((d) => h('i', { class: d.count >= 8 ? 'l3' : d.count >= 3 ? 'l2' : d.count ? 'l1' : '', title: `${d.day}: ${d.count} activities` })));
  const quizRows = data.quizzes.length
    ? h('table', { class: 'perf-table' }, h('tr', {}, h('th', {}, 'Quiz'), h('th', {}, 'Attempts'), h('th', {}, 'Best'), h('th', {}, 'Last')),
      ...data.quizzes.map((q) => h('tr', {}, h('td', {}, q.title), h('td', {}, String(q.attempts)), h('td', {}, `${q.best}%`),
        h('td', {}, h('div', { class: 'bar-mini' }, h('div', { style: { width: `${q.last}%`, background: q.last >= 70 ? '#2f8a55' : q.last >= 40 ? '#b38f2c' : '#a33a35' } })), `${q.last}%`))))
    : h('p', { class: 'lead' }, 'Take a quiz from the Studio panel to track your scores here.');
  const mastered = fc.total ? Math.round((100 * fc.understood) / fc.total) : 0;
  const tips = [];
  if (fc.review) tips.push(`Revise the ${plural(fc.review, 'flashcard')} you marked “needs revision”.`);
  if (data.quiz_average !== null && data.quiz_average < 70) tips.push('Your quiz average is under 70% — try Beginner-Friendly notes or ask the tutor to explain weak topics.');
  if (!data.artifacts.notes) tips.push('Generate Study Notes from Studio to get a structured overview.');
  if (!fc.total) tips.push('Create Flashcards to start spaced revision.');
  modal({
    title: 'Learning progress', size: 'lg',
    body: h('div', {},
      h('div', { class: 'stats-grid' },
        stat(data.streak, `day${data.streak === 1 ? '' : 's'} study streak 🔥`),
        stat(`${mastered}%`, `flashcards mastered (${fc.understood}/${fc.total})`),
        stat(data.quiz_average === null ? '—' : `${data.quiz_average}%`, 'average quiz score'),
        stat(data.sources, 'sources studied'),
        stat(data.notes + (data.artifacts.notes || 0) + (data.artifacts.report || 0), 'notes & reports')),
      h('div', { class: 'section-title' }, 'Activity (last 30 days)'), heat,
      h('div', { class: 'section-title' }, 'Quizzes'), quizRows,
      tips.length ? h('div', {}, h('div', { class: 'section-title' }, 'What to do next'), h('ul', { class: 'lead' }, ...tips.map((t) => h('li', {}, t)))) : null),
  });
}

function stat(value, label) {
  return h('div', { class: 'stat-card' }, h('div', { class: 'v' }, String(value)), h('div', { class: 'l' }, label));
}

// ── Bookmarks ────────────────────────────────────────────────────

export async function openBookmarks() {
  let items;
  try { items = (await api.get(`/api/notebooks/${store.notebookId}/bookmarks`)).bookmarks; } catch (e) { notifyError(e); return; }
  const list = h('div');
  const render = () => {
    if (!items.length) {
      list.replaceChildren(h('p', { class: 'lead' }, 'Select any text in an answer or a note and choose “Bookmark” to save important concepts here.'));
      return;
    }
    list.replaceChildren(...items.map((b) => h('div', { class: 'review-item', style: { display: 'flex', gap: '10px', alignItems: 'flex-start' } },
      icon('bookmark', 'sm'),
      h('div', { style: { flex: '1', minWidth: '0' } }, h('div', { class: 'q', style: { whiteSpace: 'pre-wrap' } }, b.text),
        h('div', { class: 'muted', style: { fontSize: '12px' } }, `${b.origin_title || 'Notebook'} · ${timeAgo(b.created_at)}`)),
      iconBtn('psychology', 'Explain', async () => { m.close(); const { askInChat } = await import('./chat.js'); askInChat(`Explain this concept clearly with an example: “${b.text.slice(0, 600)}”`); }, 'sm'),
      iconBtn('content_copy', 'Copy', async () => { if (await copyText(b.text)) toast('Copied', { type: 'success' }); }, 'sm'),
      iconBtn('delete', 'Remove', async () => {
        try { await api.del(`/api/bookmarks/${b.id}`); items = items.filter((x) => x.id !== b.id); render(); } catch (e) { notifyError(e); }
      }, 'sm'))));
  };
  render();
  const m = modal({ title: 'Bookmarks', body: list });
}

// ── Search ───────────────────────────────────────────────────────

export function openSearch() {
  const input = h('input', { class: 'field', type: 'search', placeholder: 'Search notes, Studio items, sources and chat…', autofocus: true });
  const results = h('div', { style: { marginTop: '12px', minHeight: '120px' } });
  const run = debounce(async () => {
    const q = input.value.trim();
    if (q.length < 2) { results.replaceChildren(h('p', { class: 'lead' }, 'Type at least two characters.')); return; }
    try {
      const res = await api.get(`/api/notebooks/${store.notebookId}/search?q=${encodeURIComponent(q)}`);
      if (!res.results.length) { results.replaceChildren(h('p', { class: 'lead' }, 'No matches.')); return; }
      results.replaceChildren(...res.results.map((r) => resultRow(r, q, m)));
    } catch (e) { notifyError(e); }
  }, 220);
  input.addEventListener('input', run);
  const m = modal({ title: 'Search this notebook', body: h('div', {}, input, results), size: 'lg' });
}

function highlight(text, q) {
  const frag = document.createDocumentFragment();
  const low = text.toLowerCase();
  const ql = q.toLowerCase();
  let i = 0;
  for (;;) {
    const j = low.indexOf(ql, i);
    if (j < 0) { frag.append(text.slice(i)); break; }
    frag.append(text.slice(i, j), h('mark', {}, text.slice(j, j + q.length)));
    i = j + q.length;
  }
  return frag;
}

function resultRow(r, q, m) {
  const ic = { note: 'sticky_note_2', artifact: 'auto_awesome', source: 'description', message: 'chat_bubble' }[r.type];
  const where = r.type === 'source' && r.label ? ` · ${r.label}` : '';
  return h('button', {
    class: 'review-item', type: 'button', style: { width: '100%', textAlign: 'left', display: 'flex', gap: '10px' },
    onclick: async () => {
      m.close();
      if (r.type === 'note') { const { openNote } = await import('./studio.js'); openNote(r.id); }
      else if (r.type === 'artifact') { const { openArtifact } = await import('./studio.js'); openArtifact(r.id); }
      else if (r.type === 'source') { const { openSource } = await import('./sources.js'); openSource(r.id, { chunkId: r.chunk_id, seconds: r.start ?? null }); }
      else toast('Scroll the chat to find this message.');
    },
  }, icon(ic, 'sm'), h('div', { style: { minWidth: '0' } }, h('div', { class: 'q' }, `${r.title}${where}`), h('div', { class: 'muted', style: { fontSize: '13px' } }, highlight(r.snippet, q))));
}

// ── Notebook switcher ────────────────────────────────────────────

export async function openNotebookSwitcher(anchor) {
  let notebooks = [];
  try { notebooks = (await api.get('/api/notebooks')).notebooks; } catch (e) { notifyError(e); return; }
  openMenu(anchor, [
    { label: 'Your notebooks', header: true },
    ...notebooks.slice(0, 8).map((n) => ({
      icon: n.id === store.notebookId ? 'radio_button_checked' : 'book', label: `${n.emoji ? `${n.emoji} ` : ''}${n.title}`,
      run: () => { window.location.href = `/notebook/${n.id}`; },
    })),
    '-',
    { icon: 'add', label: 'Create notebook', run: async () => { const res = await api.post('/api/notebooks', {}); window.location.href = `/notebook/${res.notebook.id}`; } },
    { icon: 'grid_view', label: 'All notebooks', run: () => { window.location.href = '/'; } },
  ]);
}

export { fmtTime };
