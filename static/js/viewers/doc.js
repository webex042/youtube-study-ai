// Document viewer/editor for Study Notes, Reports and personal notes.
// Features: rendered Markdown with citations, edit mode, search within the
// document, highlights (==text==), bookmarks, table of contents, copy,
// Markdown download and PDF export (print).

import { api } from '../lib/api.js';
import { h, icon, iconBtn, copyText, debounce, download, safeFilename, fmtTime } from '../lib/dom.js';
import { notifyError, openMenu, toast, showPopover, closePopover } from '../lib/ui.js';
import { headingsOf, plainText, renderMarkdown } from '../lib/markdown.js';
import { enableSelectionTools } from '../notebook/selection.js';
import { store } from '../notebook/store.js';

export function render(host, art, ctx) {
  const content = art.content || {};
  const partial = art.status === 'processing' || content.partial;
  const doc = createDocView({
    host, ctx,
    title: art.title,
    markdown: content.markdown || '',
    citations: art.citations || {},
    partial,
    statusMessage: art.status_message,
    originType: 'artifact', originId: art.id,
    save: async (markdown) => { await api.patch(`/api/artifacts/${art.id}`, { content: { markdown } }); },
    regenerate: async () => {
      const { customize } = await import('../notebook/studio.js');
      customize(art.type, { ...(art.options || {}) });
    },
    practice: async () => {
      const { startArtifact } = await import('../notebook/studio.js');
      startArtifact('notes', { style: 'practice', focus: (art.options || {}).focus || '' });
    },
  });
  return doc.cleanup;
}

export function renderNote(host, note, ctx) {
  const doc = createDocView({
    host, ctx,
    title: note.title,
    markdown: note.content || '',
    citations: note.citations || {},
    editableTitle: true,
    startEditing: ctx.edit || !note.content,
    originType: 'note', originId: note.id,
    save: async (markdown, title) => {
      await api.patch(`/api/notes/${note.id}`, { content: markdown, ...(title !== undefined ? { title } : {}) });
      if (ctx.onSaved) ctx.onSaved();
    },
  });
  return doc.cleanup;
}

function createDocView(opts) {
  const { host, ctx } = opts;
  let markdown = opts.markdown;
  let title = opts.title;
  let editing = !!opts.startEditing;
  let searchOpen = false;
  let hits = [];
  let current = -1;

  const titleInput = h('input', { class: 'doc-title-input', type: 'text', value: title, 'aria-label': 'Title' });
  const editor = h('textarea', { class: 'doc-editor', spellcheck: 'true', placeholder: 'Write your note in Markdown…\n\n# Heading\n- bullet\n**bold**, ==highlight==, $E = mc^2$' });
  editor.value = markdown;
  const article = h('div', { class: 'doc' });
  const searchInput = h('input', { type: 'search', placeholder: 'Search in this document', 'aria-label': 'Search in document' });
  const counter = h('span', { class: 'count' });
  const searchBar = h('div', { class: 'doc-search hidden' }, icon('search', 'sm'), searchInput, counter,
    iconBtn('keyboard_arrow_up', 'Previous match', () => step(-1), 'xs'),
    iconBtn('keyboard_arrow_down', 'Next match', () => step(1), 'xs'),
    iconBtn('close', 'Close search', () => toggleSearch(false), 'xs'));
  const tocBox = h('nav', { class: 'toc hidden', 'aria-label': 'Contents' });
  let tocOpen = false;

  const saveSoon = debounce(async () => {
    try {
      await opts.save(markdown, opts.editableTitle ? title : undefined);
      status.textContent = 'Saved';
    } catch (e) { notifyError(e); status.textContent = 'Not saved'; }
  }, 700);
  const status = h('span', { class: 'muted', style: { fontSize: '12px', marginLeft: '6px' } });

  editor.addEventListener('input', () => { markdown = editor.value; status.textContent = 'Saving…'; saveSoon(); });
  titleInput.addEventListener('input', () => {
    title = titleInput.value;
    if (opts.editableTitle) { status.textContent = 'Saving…'; saveSoon(); } else if (ctx.setTitle) debounce(() => ctx.setTitle(title), 600)();
  });
  if (!opts.editableTitle) titleInput.addEventListener('change', () => ctx.setTitle && ctx.setTitle(titleInput.value.trim() || opts.title));
  searchInput.addEventListener('input', debounce(runSearch, 150));
  searchInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') step(e.shiftKey ? -1 : 1); if (e.key === 'Escape') toggleSearch(false); });

  const editBtn = iconBtn(editing ? 'visibility' : 'edit', editing ? 'Preview' : 'Edit', () => { editing = !editing; draw(); });
  const toolbar = ctx.toolbar;
  toolbar.replaceChildren(...[
    editBtn,
    iconBtn('search', 'Search in document (Ctrl+F)', () => toggleSearch(!searchOpen)),
    iconBtn('toc', 'Table of contents', () => { tocOpen = !tocOpen; draw(); }),
    iconBtn('content_copy', 'Copy', async () => { if (await copyText(plainText(markdown))) toast('Copied', { type: 'success' }); }),
    h('button', { class: 'icon-btn', type: 'button', 'aria-label': 'Download', 'data-tip': 'Download / export', onclick: (e) => exportMenu(e.currentTarget) }, icon('download')),
    opts.practice ? iconBtn('quiz', 'Generate practice questions from this', opts.practice) : null,
    opts.regenerate ? iconBtn('refresh', 'Regenerate with options', opts.regenerate) : null,
    h('span', { class: 'grow' }),
    status].filter(Boolean));

  function exportMenu(anchor) {
    openMenu(anchor, [
      { icon: 'picture_as_pdf', label: 'Download as PDF', run: () => { const w = openPrintWindow(); fillPrintWindow(w, title, markdown, opts.citations); } },
      { icon: 'description', label: 'Download as Markdown (.md)', run: () => download(`${safeFilename(title)}.md`, markdown, 'text/markdown') },
      { icon: 'text_snippet', label: 'Download as plain text (.txt)', run: () => download(`${safeFilename(title)}.txt`, plainText(markdown), 'text/plain') },
    ]);
  }

  function toggleSearch(on) {
    searchOpen = on;
    searchBar.classList.toggle('hidden', !on);
    if (on) { searchInput.focus(); searchInput.select(); runSearch(); } else { clearHits(); }
  }

  function clearHits() {
    article.querySelectorAll('mark.search-hit').forEach((m) => m.replaceWith(document.createTextNode(m.textContent)));
    article.normalize();
    hits = [];
    current = -1;
    counter.textContent = '';
  }

  function runSearch() {
    clearHits();
    const q = searchInput.value.trim().toLowerCase();
    if (q.length < 2) return;
    const walker = document.createTreeWalker(article.querySelector('.md') || article, NodeFilter.SHOW_TEXT);
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    for (const node of nodes) {
      const text = node.nodeValue;
      const low = text.toLowerCase();
      let idx = low.indexOf(q);
      if (idx < 0) continue;
      const frag = document.createDocumentFragment();
      let last = 0;
      while (idx >= 0) {
        frag.append(text.slice(last, idx));
        const mark = h('mark', { class: 'search-hit' }, text.slice(idx, idx + q.length));
        hits.push(mark);
        frag.append(mark);
        last = idx + q.length;
        idx = low.indexOf(q, last);
      }
      frag.append(text.slice(last));
      node.replaceWith(frag);
    }
    counter.textContent = hits.length ? `${hits.length} found` : 'No matches';
    if (hits.length) step(1);
  }

  function step(dir) {
    if (!hits.length) return;
    if (current >= 0) hits[current].classList.remove('current');
    current = (current + dir + hits.length) % hits.length;
    hits[current].classList.add('current');
    hits[current].scrollIntoView({ block: 'center' });
    counter.textContent = `${current + 1} / ${hits.length}`;
  }

  async function highlightText(text) {
    const needle = text.trim();
    if (!needle) return;
    const idx = markdown.indexOf(needle);
    if (idx < 0) {
      toast('Couldn’t highlight across formatting — select plain text inside one paragraph.', { type: 'error' });
      return;
    }
    markdown = `${markdown.slice(0, idx)}==${needle}==${markdown.slice(idx + needle.length)}`;
    editor.value = markdown;
    draw();
    try { await opts.save(markdown, opts.editableTitle ? title : undefined); toast('Highlighted', { type: 'success' }); } catch (e) { notifyError(e); }
  }

  function citationClick(n, el) {
    const info = (opts.citations || {})[n];
    if (!info) return;
    const where = info.start !== null && info.start !== undefined ? fmtTime(info.start) : info.page ? `p. ${info.page}` : '';
    showPopover(el, [
      h('div', { class: 'p-head' }, icon('format_quote', 'sm'), h('span', { class: 'grow' }, info.source_title), where ? h('span', {}, where) : null),
      h('div', { class: 'p-text scroll' }, info.text),
      h('div', { class: 'p-actions' }, h('button', {
        class: 'btn btn-text small', type: 'button',
        onclick: async () => { closePopover(); const { openCited } = await import('../notebook/chat.js'); openCited(info); },
      }, icon('open_in_full'), 'Open in source')),
    ]);
  }

  async function onTimestamp(seconds) {
    const video = store.readySources.find((s) => s.kind === 'youtube' || s.kind === 'video');
    if (!video) { toast(`Timestamp ${fmtTime(seconds)}`); return; }
    const { openSource } = await import('../notebook/sources.js');
    openSource(video.id, { seconds });
  }

  function draw() {
    editBtn.replaceChildren(icon(editing ? 'visibility' : 'edit'));
    editBtn.setAttribute('aria-label', editing ? 'Preview' : 'Edit');
    editBtn.setAttribute('data-tip', editing ? 'Preview' : 'Edit');
    const parts = [titleInput];
    if (opts.partial) {
      parts.push(h('div', { class: 'warn-box', style: { background: '#1b2232', color: '#d3e3fd' } }, h('span', { class: 'spinner sm' }), h('span', {}, opts.statusMessage || 'Writing…')));
    }
    parts.push(searchBar);
    if (editing && !opts.partial) {
      parts.push(editor, h('p', { class: 'muted', style: { fontSize: '12px' } }, 'Markdown supported: # headings, **bold**, ==highlight==, - lists, tables, ```code```, $LaTeX$.'));
    } else {
      const md = renderMarkdown(markdown || '_Empty note — click the pencil to write._', {
        citations: opts.citations, onCite: citationClick, onTimestamp,
      });
      // The title field above already shows the document title.
      const firstH1 = md.querySelector('h1');
      if (firstH1 && md.firstElementChild === firstH1 && firstH1.textContent.trim() === title.trim()) firstH1.remove();
      const heads = headingsOf(md);
      tocBox.replaceChildren(...heads.filter((x) => x.level <= 3).map((x) => h('a', {
        href: `#${x.id}`, class: `l${x.level}`, onclick: (e) => { e.preventDefault(); md.querySelector(`#${CSS.escape(x.id)}`).scrollIntoView({ behavior: 'smooth' }); },
      }, x.text)));
      tocBox.classList.toggle('hidden', !tocOpen || !heads.length);
      parts.push(tocBox, md);
    }
    article.replaceChildren(...parts);
    if (searchOpen) runSearch();
  }

  enableSelectionTools(article, () => ({
    originType: opts.originType, originId: opts.originId, originTitle: title,
    onHighlight: opts.partial ? null : highlightText,
  }));

  const onKey = (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'f' && host.isConnected) { e.preventDefault(); toggleSearch(true); }
  };
  document.addEventListener('keydown', onKey);
  draw();
  host.replaceChildren(article);
  if (opts.partial) host.scrollTop = host.scrollHeight;
  if (editing) setTimeout(() => editor.focus(), 50);
  return { cleanup: () => document.removeEventListener('keydown', onKey) };
}

// ── PDF export via the browser's print dialog ────────────────────

export function openPrintWindow() {
  const w = window.open('', '_blank');
  if (!w) toast('Allow pop-ups for this site to export PDFs.', { type: 'error' });
  return w;
}

export function fillPrintWindow(w, title, markdown, citations) {
  if (!w) return;
  const origin = window.location.origin;
  const md = renderMarkdown(markdown, { citations: citations || {} });
  md.querySelectorAll('.code-head').forEach((e) => e.remove());
  md.querySelectorAll('.cite-group').forEach((g) => {
    const nums = [...g.querySelectorAll('.cite')].map((c) => c.textContent).filter((t) => t !== '···');
    const sup = w.document.createElement('sup');
    sup.textContent = `[${nums.join(',')}]`;
    g.replaceWith(sup);
  });
  const doc = w.document;
  doc.open();
  doc.write('<!doctype html><html><head><meta charset="utf-8"></head><body></body></html>');
  doc.close();
  doc.title = title;
  for (const href of ['/static/vendor/fonts/google-sans.css', '/static/vendor/fonts/google-sans-code.css', '/static/vendor/katex/katex.min.css']) {
    const link = doc.createElement('link');
    link.rel = 'stylesheet';
    link.href = origin + href;
    doc.head.append(link);
  }
  const style = doc.createElement('style');
  style.textContent = `
    @page { margin: 18mm 16mm; }
    body { font-family: "Google Sans", "Segoe UI", system-ui, sans-serif; color: #1b1b1f; font-size: 11.5pt; line-height: 1.6; max-width: 760px; margin: 0 auto; padding: 24px; }
    h1 { font-size: 22pt; font-weight: 500; margin: 0 0 6px; } h2 { font-size: 15.5pt; font-weight: 500; margin: 22px 0 8px; border-bottom: 1px solid #ddd; padding-bottom: 4px; }
    h3 { font-size: 13pt; font-weight: 500; margin: 16px 0 6px; } h4 { font-size: 11.5pt; margin: 12px 0 4px; }
    p { margin: 0 0 9px; } ul, ol { margin: 0 0 10px; padding-left: 22px; } li { margin-bottom: 4px; }
    code { font-family: "Google Sans Code", Consolas, monospace; font-size: 9.5pt; background: #f1f3f4; padding: 1px 4px; border-radius: 4px; }
    pre { background: #f6f8fa; border: 1px solid #e1e4e8; border-radius: 8px; padding: 10px 12px; white-space: pre-wrap; page-break-inside: avoid; }
    pre code { background: none; padding: 0; }
    table { border-collapse: collapse; width: 100%; margin: 6px 0 14px; font-size: 10pt; page-break-inside: avoid; }
    th, td { border: 1px solid #ccc; padding: 6px 8px; text-align: left; vertical-align: top; } th { background: #f1f3f4; }
    blockquote { margin: 0 0 10px; padding-left: 12px; border-left: 3px solid #c8c8c8; color: #555; }
    mark { background: #fff2a8; } sup { color: #5f6368; font-size: 7.5pt; }
    .doc-title { font-size: 9pt; color: #777; margin-bottom: 18px; }
    .hljs-keyword, .hljs-built_in { color: #a626a4; } .hljs-string { color: #50a14f; } .hljs-comment { color: #a0a1a7; font-style: italic; } .hljs-number { color: #986801; } .hljs-title { color: #4078f2; }
  `;
  doc.head.append(style);
  const head = doc.createElement('div');
  head.className = 'doc-title';
  head.textContent = `${title} — AI Study Lab · ${new Date().toLocaleDateString()}`;
  doc.body.append(head, doc.importNode(md, true));
  setTimeout(() => { try { w.focus(); w.print(); } catch { /* user can print manually */ } }, 600);
}

export function printMarkdown(title, markdown, citations) {
  fillPrintWindow(openPrintWindow(), title, markdown, citations);
}
