// Floating toolbar for selected text: explain, bookmark, highlight, copy.

import { api } from '../lib/api.js';
import { h, icon, copyText } from '../lib/dom.js';
import { notifyError, toast } from '../lib/ui.js';
import { store } from './store.js';

let bar = null;

function hide() { if (bar) { bar.remove(); bar = null; } }

/**
 * Enable the selection toolbar inside `root`.
 * ctx(): { originType, originId, originTitle, onHighlight?(text) }
 */
export function enableSelectionTools(root, ctx) {
  root.addEventListener('mouseup', () => setTimeout(() => show(root, ctx()), 0));
  root.addEventListener('keyup', (e) => { if (e.shiftKey) show(root, ctx()); });
}

function show(root, ctx) {
  hide();
  const sel = window.getSelection();
  if (!sel || sel.isCollapsed || !sel.rangeCount) return;
  const text = sel.toString().trim();
  if (text.length < 2 || text.length > 4000) return;
  const range = sel.getRangeAt(0);
  if (!root.contains(range.commonAncestorContainer)) return;
  const rect = range.getBoundingClientRect();
  const buttons = [
    ['psychology', 'Explain', async () => {
      const { askInChat } = await import('./chat.js');
      askInChat(`Explain this in simple terms, with an example: “${text.slice(0, 600)}”`, { focus: text });
    }],
    ['bookmark_add', 'Bookmark', () => bookmark(text, ctx)],
    ctx.onHighlight ? ['ink_highlighter', 'Highlight', () => ctx.onHighlight(text)] : null,
    ['quiz', 'Quiz me', async () => {
      const { askInChat } = await import('./chat.js');
      askInChat(`Give me 3 practice questions (with answers hidden at the end) to test my understanding of: “${text.slice(0, 600)}”`, { focus: text });
    }],
    ['content_copy', 'Copy', async () => { if (await copyText(text)) toast('Copied', { type: 'success' }); }],
  ].filter(Boolean);
  bar = h('div', { class: 'sel-toolbar', role: 'toolbar' }, ...buttons.map(([ic, label, run]) => h('button', {
    type: 'button', onmousedown: (e) => e.preventDefault(), onclick: () => { hide(); run(); },
  }, icon(ic), label)));
  document.body.append(bar);
  const top = rect.top - bar.offsetHeight - 8 < 8 ? rect.bottom + 8 : rect.top - bar.offsetHeight - 8;
  const left = Math.min(Math.max(8, rect.left + rect.width / 2 - bar.offsetWidth / 2), window.innerWidth - bar.offsetWidth - 8);
  bar.style.top = `${top}px`;
  bar.style.left = `${left}px`;
}

async function bookmark(text, ctx) {
  try {
    await api.post(`/api/notebooks/${store.notebookId}/bookmarks`, {
      text: text.slice(0, 4000), origin_type: ctx.originType || '', origin_id: ctx.originId || '', origin_title: (ctx.originTitle || '').slice(0, 290),
    });
    toast('Bookmarked — find it under ⋮ › Bookmarks', { type: 'success' });
  } catch (e) { notifyError(e); }
}

document.addEventListener('mousedown', (e) => { if (bar && !bar.contains(e.target)) hide(); });
document.addEventListener('scroll', hide, true);
