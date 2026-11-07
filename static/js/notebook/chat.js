// Center panel: notebook guide, conversation with the AI tutor, composer.

import { api, streamSSE } from '../lib/api.js';
import { h, icon, iconBtn, copyText, dayLabel, fmtDate, fmtTime, plural, throttleFrame } from '../lib/dom.js';
import { modal, notifyError, segmented, showPopover, closePopover, toast } from '../lib/ui.js';
import { plainText, renderMarkdown } from '../lib/markdown.js';
import { store } from './store.js';
import { layout } from './page.js';
import { enableSelectionTools } from './selection.js';

const ui = {};
let streaming = null;   // { controller, view, text, thoughts, status }
let pendingFocus = null;
let guideExpanded = false;
let keepAnchor = null;   // keeps the just-answered question pinned at the top after re-render

export function mountChat() {
  ui.scroll = h('div', { class: 'chat-scroll scroll' });
  ui.col = h('div', { class: 'chat-col' });
  ui.scroll.append(ui.col);
  ui.fab = h('button', { class: 'scroll-fab hidden', type: 'button', 'aria-label': 'Scroll to latest', onclick: () => scrollToBottom(true) }, icon('expand_more'));

  ui.textarea = h('textarea', { rows: 1, 'aria-label': 'Ask a question', placeholder: 'Ask a question or create something' });
  ui.count = h('button', { class: 'src-count', type: 'button', 'data-tip': 'Sources used for answers', onclick: () => layout.setTab('sources') }, '0 sources');
  ui.send = h('button', { class: 'send-btn', type: 'button', 'aria-label': 'Send', onclick: onSendClick }, icon('arrow_upward'));
  ui.composer = h('div', { class: 'composer' },
    iconBtn('tune', 'Configure chat', openChatConfig),
    ui.textarea, ui.count, ui.send);
  ui.focusChip = h('div', { class: 'focus-chip hidden' });
  ui.disclaimer = h('div', { class: 'disclaimer' }, 'AI Study Lab can make mistakes, so double-check it.');
  const wrap = h('div', { class: 'composer-wrap' }, ui.focusChip, ui.composer, ui.disclaimer);
  // The local test server answers with canned sample data; say so plainly.
  store.on('ai', (ai) => {
    const testMode = Boolean(ai && ai.provider === 'fake');
    ui.disclaimer.classList.toggle('test-mode', testMode);
    ui.disclaimer.textContent = testMode
      ? 'Test mode: replies are canned samples from the test provider, not real AI output.'
      : 'AI Study Lab can make mistakes, so double-check it.';
  });

  const center = h('section', { class: 'center', 'aria-label': 'Chat' }, ui.scroll, ui.fab, wrap);

  ui.textarea.addEventListener('input', () => { autosize(); updateComposer(); });
  ui.textarea.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); onSendClick(); }
  });
  ui.textarea.addEventListener('focus', () => ui.composer.classList.add('focused'));
  ui.textarea.addEventListener('blur', () => ui.composer.classList.remove('focused'));
  ui.textarea.addEventListener('paste', () => setTimeout(() => { autosize(); updateComposer(); }, 0));
  ui.scroll.addEventListener('scroll', () => ui.fab.classList.toggle('hidden', nearBottom() || !hasContent()));

  enableSelectionTools(ui.col, () => ({ originType: 'message', originTitle: 'Chat answer' }));

  store.on('messages', () => render(true));
  store.on('notebook', () => render(false));
  store.on('sources', () => { updateComposer(); render(false); });

  return center;
}

// ── Rendering ────────────────────────────────────────────────────

function nearBottom() {
  return ui.scroll.scrollHeight - ui.scroll.scrollTop - ui.scroll.clientHeight < 80;
}

function scrollToBottom(smooth = false) {
  ui.scroll.scrollTo({ top: ui.scroll.scrollHeight, behavior: smooth ? 'smooth' : 'auto' });
}

function hasContent() {
  return store.sources.length || store.messages.length || streaming;
}

let lastRenderKey = '';

function render(scrollDown) {
  if (!store.notebook) return;
  // Avoid re-rendering the whole conversation for unrelated source status ticks.
  const key = JSON.stringify([store.notebook.summary, store.notebook.guide_status, store.notebook.questions, store.notebook.emoji,
    store.notebook.title, store.messages.length, store.messages.at(-1)?.id, store.sources.length, store.readySources.length,
    store.sources.some((s) => s.status === 'processing' || s.status === 'queued'), guideExpanded]);
  if (key === lastRenderKey && !scrollDown) return;
  lastRenderKey = key;
  const wasNear = nearBottom();
  const prevTop = ui.scroll.scrollTop;

  const nodes = [];
  if (!hasContent()) {
    nodes.push(welcome());
  } else {
    if (store.sources.length) nodes.push(guideSection());
    nodes.push(...conversation());
    if (streaming) nodes.push(streaming.view);
  }
  ui.col.replaceChildren(...nodes);
  updateComposer();
  if (!hasContent()) { ui.scroll.scrollTop = 0; ui.fab.classList.add('hidden'); return; }
  else if (keepAnchor) {
    const views = ui.col.querySelectorAll('.msg-ai');
    const lastView = views[views.length - 1];
    if (lastView && keepAnchor.minHeight) lastView.style.minHeight = keepAnchor.minHeight;
    ui.scroll.scrollTop = keepAnchor.scrollTop;
    keepAnchor = null;
  } else if (scrollDown || wasNear) scrollToBottom(); else ui.scroll.scrollTop = prevTop;
  ui.fab.classList.toggle('hidden', nearBottom());
}

function welcome() {
  return h('div', { class: 'welcome' },
    h('span', { class: 'wave', role: 'img', 'aria-label': 'Waving hand' }, '👋'),
    h('h1', {}, 'Let’s start your notebook…'),
    h('p', {}, 'This is your blank canvas to understand, create, or make progress on something new. I can help you get started or you can go ahead and add your own sources.'),
    h('h2', {}, 'What would you like this notebook to help you do?'),
    h('div', { class: 'starter-list' },
      starter('Learn about a new topic', () => { setInput('I want to learn about '); }),
      starter('Understand a lecture video', () => { setInput(''); ui.textarea.placeholder = 'Paste a YouTube lecture link…'; ui.textarea.focus(); }),
      starter('Prepare for an exam', async () => {
        const { openAddSources } = await import('./sources.js');
        openAddSources();
      })));
}

function starter(label, run) {
  return h('button', { class: 'starter', type: 'button', onclick: run }, label);
}

function setInput(text) {
  ui.textarea.value = text;
  autosize();
  updateComposer();
  ui.textarea.focus();
  ui.textarea.setSelectionRange(text.length, text.length);
}

function guideSection() {
  const nb = store.notebook;
  const processing = store.sources.some((s) => s.status === 'queued' || s.status === 'processing');
  const wrap = h('div', { class: 'guide' });
  const loading = nb.guide_status === 'processing' || (processing && !nb.summary && !store.readySources.length)
    || (!nb.summary && store.readySources.length && store.ai.configured && nb.guide_status !== 'error');
  if (loading) {
    wrap.append(h('div', { class: 'skeleton', 'aria-label': 'Loading notebook guide' },
      ...[92, 98, 100, 92, 98, 92, 100].map((w) => h('div', { class: 'bar', style: { width: `${w}%` } }))));
    if (nb.summary) return wrap;
    return wrap;
  }
  if (!store.readySources.length) return wrap;

  const count = store.readySources.length;
  const card = h('div', { class: 'guide-card' },
    h('div', { class: 'guide-emoji' }, nb.emoji || '📘'),
    h('div', { class: 'guide-title' }, nb.title),
    h('div', { class: 'guide-meta' }, `${plural(count, 'source')} · ${fmtDate(nb.created_at)}`),
    decoSvg());
  wrap.append(card);
  if (nb.summary) {
    const body = h('div', { class: `guide-collapse ${store.messages.length && !guideExpanded ? '' : ''}` },
      h('div', { class: 'guide-summary' }, renderMarkdown(nb.summary)));
    wrap.append(body);
    wrap.append(h('div', { class: 'msg-actions' },
      actionBtn('keep', 'Save to note', () => saveToNote(`## ${nb.title}\n\n${nb.summary}`, {}, 'Notebook overview')),
      actionBtn('content_copy', 'Copy', async () => { if (await copyText(plainText(nb.summary))) toast('Copied', { type: 'success' }); }),
      actionBtn('thumb_up', 'Good overview', () => toast('Thanks for the feedback!')),
      actionBtn('thumb_down', 'Bad overview', () => regenerateGuide())));
    if (!store.messages.length && nb.questions && nb.questions.length) wrap.append(suggestionCards(nb.questions));
  } else if (nb.guide_status === 'error') {
    wrap.append(h('div', { class: 'warn-box', style: { marginTop: '14px' } }, icon('warning', 'sm'),
      h('span', {}, `Couldn't write the overview: ${nb.guide_error || 'unknown error'} `,
        h('a', { href: '#', onclick: (e) => { e.preventDefault(); regenerateGuide(); } }, 'Try again'))));
  } else if (!store.ai.configured) {
    wrap.append(h('div', { class: 'warn-box', style: { marginTop: '14px' } }, icon('key', 'sm'), h('span', {}, store.ai.message || 'AI is not configured.')));
  }
  return wrap;
}

function decoSvg() {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 240 180');
  svg.setAttribute('class', 'guide-deco');
  svg.setAttribute('aria-hidden', 'true');
  svg.innerHTML = '<path d="M8 172 C 30 120, 52 104, 70 128 S 98 168, 112 120 S 132 30, 160 26 S 196 70, 210 112 S 226 160, 236 172" fill="none" stroke="currentColor" stroke-width="9" stroke-linecap="round" stroke-linejoin="round"/><circle cx="76" cy="58" r="17" fill="none" stroke="currentColor" stroke-width="9"/>';
  return svg;
}

async function regenerateGuide() {
  try { await api.post(`/api/notebooks/${store.notebookId}/guide`); store.pokeSoon(); } catch (e) { notifyError(e); }
}

function conversation() {
  const out = [];
  let lastTs = 0;
  const msgs = store.messages;
  const lastAssistant = [...msgs].reverse().find((m) => m.role === 'assistant');
  for (const m of msgs) {
    if (m.created_at - lastTs > 30 * 60) out.push(h('div', { class: 'day-sep' }, dayLabel(m.created_at)));
    lastTs = m.created_at;
    out.push(m.role === 'user' ? userBubble(m.content) : assistantView(m, m === lastAssistant && !streaming));
  }
  return out;
}

function userBubble(text) {
  return h('div', { class: 'msg msg-user' }, h('div', { class: 'bubble' }, text));
}

function onTimestamp(seconds, citations) {
  const cited = Object.values(citations || {}).find((c) => c.start !== null && c.start !== undefined);
  const video = (cited && store.sourceById(cited.source_id)) || store.readySources.find((s) => s.kind === 'youtube' || s.kind === 'video');
  if (!video) { toast(`Timestamp ${fmtTime(seconds)}`); return; }
  import('./sources.js').then(({ openSource }) => openSource(video.id, { seconds }));
}

function assistantView(m, isLast) {
  const citations = m.citations || {};
  const view = h('div', { class: 'msg msg-ai', 'data-id': m.id });
  if (m.thoughts) view.append(thoughtsToggle(m.thoughts));
  const md = renderMarkdown(m.content, {
    citations,
    onCite: (n, el) => showCitation(citations[n], n, el),
    onTimestamp: (s) => onTimestamp(s, citations),
  });
  if (m.status === 'error') md.classList.add('msg-error-block');
  view.append(md);
  const webResults = (m.meta || {}).web_results;
  if (webResults && webResults.length) view.append(webCards(webResults));
  view.append(messageActions(m));
  if (isLast && m.followups && m.followups.length) view.append(suggestionCards(m.followups));
  return view;
}

function thoughtsToggle(thoughts) {
  const body = h('div', { class: 'thoughts-body hidden' });
  const btn = h('button', { class: 'thoughts-toggle', type: 'button', 'aria-expanded': 'false' },
    h('span', { class: 'dot' }), 'Thoughts', icon('keyboard_arrow_down', 'sm'));
  btn.addEventListener('click', () => {
    const open = body.classList.toggle('hidden') === false;
    btn.setAttribute('aria-expanded', String(open));
    btn.lastChild.textContent = open ? 'keyboard_arrow_up' : 'keyboard_arrow_down';
    if (open && !body.firstChild) body.append(renderMarkdown(thoughts));
  });
  return h('div', {}, btn, body);
}

function actionBtn(name, label, run, on = false) {
  return h('button', { class: `icon-btn ${on ? 'on' : ''}`, type: 'button', 'aria-label': label, 'data-tip': label, onclick: run }, icon(name, on ? 'fill' : ''));
}

function messageActions(m) {
  const row = h('div', { class: 'msg-actions' });
  const fb = async (value) => {
    const next = m.feedback === value ? 0 : value;
    try { await api.post(`/api/messages/${m.id}/feedback`, { value: next }); m.feedback = next; row.replaceWith(messageActions(m)); } catch (e) { notifyError(e); }
    if (next === -1) toast('Thanks — tip: use the tune button to make answers simpler or longer.');
  };
  row.append(
    actionBtn('keep', 'Save to note', () => saveToNote(m.content, m.citations)),
    actionBtn('content_copy', 'Copy', async () => { if (await copyText(plainText(m.content))) toast('Copied', { type: 'success' }); }),
    actionBtn('thumb_up', 'Good response', () => fb(1), m.feedback === 1),
    actionBtn('thumb_down', 'Bad response', () => fb(-1), m.feedback === -1));
  if (m.status === 'stopped') row.append(h('span', { class: 'msg-note' }, 'Stopped'));
  return row;
}

export async function saveToNote(content, citations, title) {
  try {
    await api.post(`/api/notebooks/${store.notebookId}/notes`, { content, citations: citations || {}, kind: 'saved_response', title: title || 'New Note' });
    store.pokeSoon();
    toast('Saved to notes in Studio', { type: 'success' });
  } catch (e) { notifyError(e); }
}

function suggestionCards(questions) {
  return h('div', { class: 'suggestions' }, ...questions.slice(0, 3).map((q) => h('button', {
    class: 'suggestion', type: 'button', onclick: () => send(q),
  }, h('span', {}, q), icon('subdirectory_arrow_right'))));
}

function webCards(results) {
  const box = h('div', { class: 'web-cards' });
  const add = async (urls, btn) => {
    if (btn) btn.disabled = true;
    try {
      const res = await api.post(`/api/notebooks/${store.notebookId}/sources/url`, { urls });
      toast(`Adding ${plural(res.sources.length, 'source')}…`, { type: 'success' });
      if (res.errors && res.errors.length) toast(res.errors[0].error, { type: 'error' });
      store.pokeSoon();
    } catch (e) { notifyError(e); if (btn) btn.disabled = false; }
  };
  for (const r of results) {
    const btn = h('button', { class: 'btn btn-tonal small', type: 'button' }, icon('add'), 'Add');
    btn.addEventListener('click', () => add([r.url], btn));
    box.append(h('div', { class: 'web-card' }, icon(/youtu/.test(r.url) ? 'smart_display' : 'language', /youtu/.test(r.url) ? 'yt-icon' : 'src-web'),
      h('div', { class: 'meta' }, h('div', { class: 't' }, r.title), h('div', { class: 'd' }, r.domain || r.url)), btn));
  }
  const all = h('button', { class: 'btn btn-primary small', type: 'button' }, icon('playlist_add'), 'Add all as sources');
  all.addEventListener('click', () => add(results.map((r) => r.url), all));
  box.append(h('div', { style: { display: 'flex', justifyContent: 'flex-end' } }, all));
  return box;
}

// ── Citations ────────────────────────────────────────────────────

function showCitation(info, n, anchor) {
  if (!info) { toast('That passage is no longer available.'); return; }
  anchor.classList.add('active');
  const isMedia = info.start !== null && info.start !== undefined;
  const where = isMedia ? fmtTime(info.start) : info.page ? `${info.source_kind === 'pptx' ? 'Slide' : 'Page'} ${info.page}` : '';
  const pop = showPopover(anchor, [
    h('div', { class: 'p-head' }, icon('format_quote', 'sm'), h('span', { class: 'grow' }, info.source_title), where ? h('span', {}, where) : null),
    h('div', { class: 'p-text scroll' }, info.text),
    h('div', { class: 'p-actions' },
      isMedia && (info.source_kind === 'youtube' || info.source_kind === 'video')
        ? h('button', { class: 'btn btn-tonal small', type: 'button', onclick: () => { closePopover(); openCited(info); } }, icon('play_arrow'), `Play at ${where}`) : null,
      h('button', { class: 'btn btn-text small', type: 'button', onclick: () => { closePopover(); openCited(info); } }, icon('open_in_full'), 'Open in source')),
  ]);
  const off = () => anchor.classList.remove('active');
  const observer = new MutationObserver(() => { if (!document.body.contains(pop)) { off(); observer.disconnect(); } });
  observer.observe(document.body, { childList: true });
}

export async function openCited(info) {
  const { openSource } = await import('./sources.js');
  openSource(info.source_id, { chunkId: info.chunk_id, seconds: info.start ?? null });
}

// ── Composer ─────────────────────────────────────────────────────

function autosize() {
  ui.textarea.style.height = 'auto';
  ui.textarea.style.height = `${Math.min(200, ui.textarea.scrollHeight)}px`;
}

function updateComposer() {
  const n = store.selectedSources.length;
  ui.count.textContent = plural(n, 'source');
  ui.count.classList.toggle('stack', ui.textarea.value.length > 40 && n < 10);
  const busy = !!streaming;
  const idle = window.innerWidth < 520 ? 'Ask a question…' : 'Ask a question or create something';
  ui.textarea.placeholder = busy ? 'Responding…' : (ui.textarea.placeholder === 'Paste a YouTube lecture link…' && !ui.textarea.value ? ui.textarea.placeholder : idle);
  ui.send.classList.toggle('stop', busy);
  ui.send.classList.toggle('ready', !busy && !!ui.textarea.value.trim());
  ui.send.replaceChildren(icon(busy ? 'stop' : 'arrow_upward'));
  ui.send.setAttribute('aria-label', busy ? 'Stop responding' : 'Send');
  ui.send.setAttribute('data-tip', busy ? 'Stop' : 'Send');
}

function onSendClick() {
  if (streaming) { stop(); return; }
  const text = ui.textarea.value.trim();
  if (!text) return;
  send(text);
}

export function askInChat(text, { focus = null } = {}) {
  layout.setTab('chat');
  if (focus) setFocus(focus);
  send(text);
}

function setFocus(text) {
  pendingFocus = text;
  ui.focusChip.replaceChildren(icon('format_quote', 'sm'), h('span', { class: 'txt' }, text),
    iconBtn('close', 'Remove', () => { pendingFocus = null; ui.focusChip.classList.add('hidden'); }, 'xs'));
  ui.focusChip.classList.remove('hidden');
}

function statusIcon(kind) {
  if (kind === 'grid') return h('span', { class: 'status-icon grid' }, h('i'), h('i'), h('i'), h('i'));
  return h('span', { class: 'status-icon ring' });
}

export async function send(text) {
  if (streaming) { toast('Please wait for the current answer (or press stop).'); return; }
  if (!text.trim()) return;
  ui.textarea.value = '';
  autosize();
  ui.textarea.placeholder = 'Ask a question or create something';
  const focus = pendingFocus;
  pendingFocus = null;
  ui.focusChip.classList.add('hidden');

  const temp = { id: `tmp-${Date.now()}`, role: 'user', content: text, created_at: Date.now() / 1000 };
  store.messages.push(temp);

  const statusText = h('span', { class: 'sl-text' }, 'Getting the gist…');
  const statusRow = h('div', { class: 'status-line' }, statusIcon('ring'), statusText);
  const body = h('div');
  const view = h('div', { class: 'msg msg-ai streaming' }, statusRow, body);
  const controller = new AbortController();
  streaming = { controller, view, text: '', thoughts: '', statusCount: 0 };
  render(true);
  anchorQuestion(view);

  const renderBody = throttleFrame(() => {
    if (!streaming) return;
    const md = renderMarkdown(streaming.text, { streaming: true });
    const parts = [];
    if (streaming.thoughts) parts.push(h('div', {}, h('div', { class: 'thoughts-toggle' }, h('span', { class: 'dot' }), 'Thoughts', icon('keyboard_arrow_down', 'sm'))));
    parts.push(md);
    body.replaceChildren(...parts);
    ui.fab.classList.toggle('hidden', nearBottom());
  });

  let final = null;
  try {
    await streamSSE(`/api/notebooks/${store.notebookId}/chat`, { message: text, focus: focus || undefined }, (ev) => {
      if (ev.type === 'user') {
        const i = store.messages.indexOf(temp);
        if (i >= 0) store.messages[i] = ev.message;
      } else if (ev.type === 'status') {
        streaming.statusCount += 1;
        statusRow.replaceChildren(statusIcon(streaming.statusCount % 2 ? 'grid' : 'ring'), h('span', { class: 'sl-text' }, ev.text));
        statusRow.classList.remove('hidden');
      } else if (ev.type === 'thought') {
        streaming.thoughts += ev.text;
      } else if (ev.type === 'delta') {
        streaming.text += ev.text;
        statusRow.classList.add('hidden');
        renderBody();
      } else if (ev.type === 'sources') {
        store.pokeSoon();
      } else if (ev.type === 'done') {
        final = ev.message;
      } else if (ev.type === 'error') {
        toast(ev.message, { type: 'error' });
      }
    }, controller.signal);
  } catch (err) {
    if (err.name !== 'AbortError') notifyError(err);
  }

  const aborted = controller.signal.aborted;
  keepAnchor = { minHeight: view.style.minHeight, scrollTop: ui.scroll.scrollTop };
  streaming = null;
  if (final) store.messages.push(final);
  if (aborted || !final) {
    // The server saves what was written so far; fetch the authoritative list.
    setTimeout(async () => {
      try {
        const res = await api.get(`/api/notebooks/${store.notebookId}/messages`);
        store.messages = res.messages;
      } catch { /* keep local */ }
      store.emit('messages', store.messages);
    }, aborted ? 700 : 0);
  } else {
    store.emit('messages', store.messages);
  }
  store.pokeSoon();
  updateComposer();
}

function stop() {
  if (streaming) streaming.controller.abort();
}

// Like the reference app: the new question jumps to the top of the view and
// the answer is written below it (the view doesn't chase the text).
function anchorQuestion(view) {
  const bubbles = ui.col.querySelectorAll('.msg-user');
  const bubble = bubbles[bubbles.length - 1];
  if (!bubble) return;
  const room = ui.scroll.clientHeight - bubble.offsetHeight - 70;
  view.style.minHeight = `${Math.max(120, room)}px`;
  const top = bubble.getBoundingClientRect().top - ui.scroll.getBoundingClientRect().top + ui.scroll.scrollTop;
  ui.scroll.scrollTop = Math.max(0, top - 14);
  ui.fab.classList.toggle('hidden', nearBottom());
}

// ── Chat configuration (tune button) ─────────────────────────────

function openChatConfig() {
  const cfg = { style: 'default', length: 'default', level: 'intermediate', language: '', custom: '', ...(store.notebook.chat_config || {}) };
  const custom = h('textarea', { class: `field ${cfg.style === 'custom' ? '' : 'hidden'}`, rows: 3, placeholder: 'e.g. "Explain like a friendly senior, use Hinglish examples, always add a real-world analogy."' });
  custom.value = cfg.custom || '';
  const language = h('input', { class: 'field', type: 'text', placeholder: 'English (default) — e.g. Hindi, Tamil, Spanish…' });
  language.value = cfg.language || '';
  const body = h('div', {},
    h('p', { class: 'lead' }, 'Notebooks can be customised to help you reach your goals — study, review, or deep-dive.'),
    h('label', { class: 'label' }, 'Conversational goal'),
    segmented([{ value: 'default', label: 'Default' }, { value: 'guide', label: 'Learning guide' }, { value: 'custom', label: 'Custom' }], cfg.style, (v) => { cfg.style = v; custom.classList.toggle('hidden', v !== 'custom'); }),
    custom,
    h('label', { class: 'label' }, 'Explanation level'),
    segmented([{ value: 'beginner', label: 'Beginner' }, { value: 'intermediate', label: 'Intermediate' }, { value: 'expert', label: 'Expert' }], cfg.level, (v) => { cfg.level = v; }),
    h('label', { class: 'label' }, 'Response length'),
    segmented([{ value: 'shorter', label: 'Shorter' }, { value: 'default', label: 'Default' }, { value: 'longer', label: 'Longer' }], cfg.length, (v) => { cfg.length = v; }),
    h('label', { class: 'label' }, 'Answer language'),
    language);
  const m = modal({
    title: 'Configure chat', body,
    footer: [h('button', {
      class: 'btn btn-primary', type: 'button', onclick: async () => {
        cfg.custom = custom.value.trim();
        cfg.language = language.value.trim();
        try {
          const res = await api.patch(`/api/notebooks/${store.notebookId}`, { chat_config: cfg });
          store.notebook.chat_config = res.notebook.chat_config;
          toast('Chat settings saved', { type: 'success' });
          m.close();
        } catch (e) { notifyError(e); }
      },
    }, 'Save')],
  });
}
