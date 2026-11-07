// Markdown rendering for answers, notes and reports.
//
// marked (GFM) → DOMPurify (sanitise) → post-processing on the DOM:
//   * LaTeX ($…$, $$…$$, \(…\), \[…\]) rendered with KaTeX
//   * fenced code highlighted with highlight.js, plus a copy button
//   * [n] citation markers turned into clickable chips
//   * "▶ 12:34" timestamps turned into links that seek the lecture video
//   * ==text== rendered as a highlight (<mark>)

import { marked } from '/static/vendor/marked.esm.js';
import DOMPurify from '/static/vendor/purify.es.mjs';
import hljs from '/static/vendor/hljs/highlight.min.js';
import { h, icon, copyText } from './dom.js';
import { toast } from './ui.js';

marked.use({
  gfm: true,
  breaks: false,
  extensions: [{
    name: 'mark',
    level: 'inline',
    start(src) { const i = src.indexOf('=='); return i < 0 ? undefined : i; },
    tokenizer(src) {
      const m = /^==(?=\S)([\s\S]*?\S)==/.exec(src);
      if (m) return { type: 'mark', raw: m[0], tokens: this.lexer.inlineTokens(m[1]) };
      return undefined;
    },
    renderer(token) { return `<mark>${this.parser.parseInline(token.tokens)}</mark>`; },
  }],
});

DOMPurify.addHook('afterSanitizeAttributes', (node) => {
  if (node.tagName === 'A') {
    node.setAttribute('target', '_blank');
    node.setAttribute('rel', 'noopener noreferrer');
  }
});

const PURIFY = { USE_PROFILES: { html: true }, FORBID_TAGS: ['style', 'form', 'input', 'button', 'iframe'], FORBID_ATTR: ['style'] };

// Protect maths from the Markdown parser (underscores/asterisks inside TeX).
function protectMath(src) {
  const math = [];
  const store = (tex, display) => { math.push({ tex, display }); return `@@MATH${math.length - 1}@@`; };
  let out = '';
  // Leave code untouched.
  const parts = src.split(/(```[\s\S]*?(?:```|$)|`[^`\n]*`)/g);
  for (const part of parts) {
    if (part.startsWith('`')) { out += part; continue; }
    out += part
      .replace(/\$\$([\s\S]+?)\$\$/g, (_, t) => store(t, true))
      .replace(/\\\[([\s\S]+?)\\\]/g, (_, t) => store(t, true))
      .replace(/\\\(([\s\S]+?)\\\)/g, (_, t) => store(t, false))
      .replace(/(^|[^\\$\w])\$(?!\s)([^$\n]{1,300}?[^\s\\$])\$(?![\w$])/g, (m, pre, t) => {
        // Skip prices like "$5 and $10".
        if (/^\d+([.,]\d+)?$/.test(t.trim())) return m;
        return pre + store(t, false);
      });
  }
  return { text: out, math };
}

function renderMath(root, math) {
  if (!math.length) return;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const nodes = [];
  while (walker.nextNode()) if (walker.currentNode.nodeValue.includes('@@MATH')) nodes.push(walker.currentNode);
  for (const node of nodes) {
    const frag = document.createDocumentFragment();
    const pieces = node.nodeValue.split(/@@MATH(\d+)@@/);
    pieces.forEach((piece, i) => {
      if (i % 2 === 0) { if (piece) frag.append(piece); return; }
      const item = math[Number(piece)];
      const span = document.createElement(item && item.display ? 'div' : 'span');
      if (item && window.katex) {
        try {
          window.katex.render(item.tex, span, { displayMode: item.display, throwOnError: false, trust: false, strict: 'ignore', output: 'htmlAndMathml' });
        } catch {
          span.textContent = item.display ? `$$${item.tex}$$` : `$${item.tex}$`;
        }
      } else if (item) {
        span.textContent = item.display ? `$$${item.tex}$$` : `$${item.tex}$`;
      }
      frag.append(span);
    });
    node.replaceWith(frag);
  }
}

function enhanceCode(root, { streaming }) {
  root.querySelectorAll('pre > code').forEach((code) => {
    const pre = code.parentElement;
    const lang = (code.className.match(/language-([\w+#-]+)/) || [])[1] || '';
    if (!streaming) {
      try {
        if (lang && hljs.getLanguage(lang)) hljs.highlightElement(code);
        else if (code.textContent.length < 20000) {
          const res = hljs.highlightAuto(code.textContent);
          if (res.relevance > 5) { code.innerHTML = res.value; code.classList.add('hljs'); }
        }
      } catch { /* leave plain */ }
    }
    const head = h('div', { class: 'code-head' }, h('span', {}, lang || 'code'),
      h('button', {
        class: 'icon-btn xs', type: 'button', 'data-tip': 'Copy code', 'aria-label': 'Copy code',
        onclick: async () => { if (await copyText(code.textContent)) toast('Code copied', { type: 'success' }); },
      }, icon('content_copy', 'xs')));
    pre.prepend(head);
  });
}

const SKIP = new Set(['CODE', 'PRE', 'A', 'BUTTON', 'MARK']);

function textNodes(root) {
  const out = [];
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      let p = node.parentElement;
      while (p && p !== root) {
        if (SKIP.has(p.tagName) || p.classList.contains('katex')) return NodeFilter.FILTER_REJECT;
        p = p.parentElement;
      }
      return NodeFilter.FILTER_ACCEPT;
    },
  });
  while (walker.nextNode()) out.push(walker.currentNode);
  return out;
}

const CITE_RE = /(?:\s?\[\d{1,4}(?:\s*[,–-]\s*\d{1,4})*\])+/g;

function expandCitation(group) {
  const nums = [];
  for (const m of group.matchAll(/\[([^\]]+)\]/g)) {
    for (const part of m[1].split(/\s*,\s*/)) {
      const range = part.split(/\s*[–-]\s*/);
      if (range.length === 2 && /^\d+$/.test(range[0]) && /^\d+$/.test(range[1])) {
        const a = Number(range[0]); const b = Number(range[1]);
        if (b > a && b - a < 30) for (let i = a; i <= b; i += 1) nums.push(i);
      } else if (/^\d+$/.test(part)) nums.push(Number(part));
    }
  }
  return [...new Set(nums)];
}

function renderCitations(root, citations, onCite, allowAll) {
  const known = citations || {};
  for (const node of textNodes(root)) {
    const text = node.nodeValue;
    if (!text.includes('[')) continue;
    CITE_RE.lastIndex = 0;
    if (!CITE_RE.test(text)) continue;
    CITE_RE.lastIndex = 0;
    const frag = document.createDocumentFragment();
    let last = 0;
    for (const m of text.matchAll(CITE_RE)) {
      const nums = expandCitation(m[0]).filter((n) => known[String(n)] || allowAll);
      frag.append(text.slice(last, m.index));
      if (!nums.length) {
        // A marker that doesn't match a passage is dropped rather than shown raw.
        last = m.index + m[0].length;
        continue;
      }
      const group = h('span', { class: 'cite-group' });
      const chip = (n) => h('button', {
        type: 'button', class: 'cite', 'data-cite': String(n), 'aria-label': `Source passage ${n}`,
        onclick: (e) => { e.stopPropagation(); if (onCite) onCite(String(n), e.currentTarget); },
      }, String(n));
      group.append(chip(nums[0]));
      if (nums.length > 1) {
        const more = h('button', {
          type: 'button', class: 'cite more', 'aria-label': `Show ${nums.length - 1} more citations`,
          onclick: (e) => { e.stopPropagation(); more.replaceWith(...nums.slice(1).map(chip)); },
        }, '···');
        group.append(more);
      }
      frag.append(group);
      last = m.index + m[0].length;
    }
    if (last === 0) continue;
    frag.append(text.slice(last));
    node.replaceWith(frag);
  }
}

const TIME_RE = /▶️?\s?((?:\d{1,2}:)?\d{1,2}:\d{2})(?:\s?[–-]\s?((?:\d{1,2}:)?\d{1,2}:\d{2}))?/g;

function renderTimestamps(root, onTimestamp) {
  if (!onTimestamp) return;
  for (const node of textNodes(root)) {
    const text = node.nodeValue;
    if (!text.includes('▶')) continue;
    TIME_RE.lastIndex = 0;
    const frag = document.createDocumentFragment();
    let last = 0;
    for (const m of text.matchAll(TIME_RE)) {
      frag.append(text.slice(last, m.index));
      const parts = m[1].split(':').map(Number);
      const secs = parts.length === 3 ? parts[0] * 3600 + parts[1] * 60 + parts[2] : parts[0] * 60 + parts[1];
      frag.append(h('span', {
        class: 'timestamp-link', role: 'link', tabindex: '0', 'data-tip': 'Play this moment in the video',
        onclick: (e) => { e.stopPropagation(); onTimestamp(secs); },
        onkeydown: (e) => { if (e.key === 'Enter') onTimestamp(secs); },
      }, icon('play_circle', 'xs'), m[0].replace(/^▶\uFE0F?\s?/, '')));
      last = m.index + m[0].length;
    }
    if (last === 0) continue;
    frag.append(text.slice(last));
    node.replaceWith(frag);
  }
}

export function renderMarkdown(markdown, opts = {}) {
  const { citations, onCite, onTimestamp, streaming = false } = opts;
  const { text, math } = protectMath(markdown || '');
  let html = '';
  try {
    html = marked.parse(text);
  } catch {
    html = '';
  }
  const clean = DOMPurify.sanitize(html, PURIFY);
  const root = document.createElement('div');
  root.className = 'md';
  root.innerHTML = clean;
  renderMath(root, math);
  enhanceCode(root, { streaming });
  if (citations || streaming) renderCitations(root, citations, onCite, streaming && !Object.keys(citations || {}).length);
  renderTimestamps(root, onTimestamp);
  return root;
}

// Plain text for copying (citation markers removed).
export function plainText(markdown) {
  return String(markdown || '')
    .replace(/\s?\[\d{1,4}(?:\s*[,–-]\s*\d{1,4})*\]/g, '')
    .replace(/==([^=]+)==/g, '$1');
}

export function headingsOf(root) {
  return [...root.querySelectorAll('h1, h2, h3')].map((el, i) => {
    if (!el.id) el.id = `h-${i}-${el.textContent.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, '-').slice(0, 40)}`;
    return { id: el.id, text: el.textContent, level: Number(el.tagName[1]) };
  });
}
