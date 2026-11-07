// Small DOM helpers. Elements are always built with createElement/textContent;
// the only HTML strings that reach the DOM come from the sanitising Markdown
// renderer (DOMPurify).

export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === undefined || value === null || value === false) continue;
    if (key === 'class') el.className = value;
    else if (key === 'style' && typeof value === 'object') Object.assign(el.style, value);
    else if (key === 'dataset') Object.assign(el.dataset, value);
    else if (key.startsWith('on') && typeof value === 'function') el.addEventListener(key.slice(2).toLowerCase(), value);
    else if (key === 'text') el.textContent = value;
    else if (value === true) el.setAttribute(key, '');
    else el.setAttribute(key, value);
  }
  append(el, children);
  return el;
}

function append(el, children) {
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
}

export function icon(name, cls = '') {
  const el = document.createElement('span');
  el.className = `ms ${cls}`.trim();
  el.setAttribute('aria-hidden', 'true');
  el.textContent = name;
  return el;
}

export function iconBtn(name, label, onClick, cls = '') {
  return h('button', { class: `icon-btn ${cls}`, type: 'button', 'aria-label': label, 'data-tip': label, onclick: onClick }, icon(name));
}

export function clear(el) {
  while (el && el.firstChild) el.removeChild(el.firstChild);
  return el;
}

export function fmtTime(sec) {
  if (sec === null || sec === undefined || Number.isNaN(sec)) return '';
  const total = Math.max(0, Math.floor(sec));
  const hh = Math.floor(total / 3600);
  const mm = Math.floor((total % 3600) / 60);
  const ss = total % 60;
  const pad = (n) => String(n).padStart(2, '0');
  return hh ? `${hh}:${pad(mm)}:${pad(ss)}` : `${pad(mm)}:${pad(ss)}`;
}

export function parseTime(text) {
  const m = String(text || '').trim().match(/^(?:(\d+):)?(\d{1,2}):(\d{2})$/);
  if (!m) return null;
  return (Number(m[1] || 0) * 3600) + Number(m[2]) * 60 + Number(m[3]);
}

export function timeAgo(ts) {
  const diff = Date.now() / 1000 - ts;
  if (diff < 45) return 'just now';
  if (diff < 3600) return `${Math.round(diff / 60)} min ago`;
  if (diff < 86400) return `${Math.round(diff / 3600)} h ago`;
  if (diff < 86400 * 7) return `${Math.round(diff / 86400)} d ago`;
  return new Date(ts * 1000).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
}

export function fmtDate(ts) {
  return new Date(ts * 1000).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
}

export function fmtClock(ts) {
  return new Date(ts * 1000).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
}

export function dayLabel(ts) {
  const d = new Date(ts * 1000);
  const today = new Date();
  const yesterday = new Date(Date.now() - 86400000);
  const same = (a, b) => a.toDateString() === b.toDateString();
  const label = same(d, today) ? 'Today' : same(d, yesterday) ? 'Yesterday'
    : d.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' });
  return `${label} • ${fmtClock(ts)}`;
}

export function debounce(fn, ms) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

export function throttleFrame(fn) {
  let scheduled = false;
  let lastArgs;
  return (...args) => {
    lastArgs = args;
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(() => { scheduled = false; fn(...lastArgs); });
  };
}

export function download(filename, content, type = 'text/plain') {
  const blob = content instanceof Blob ? content : new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const a = h('a', { href: url, download: filename });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const ta = h('textarea', { style: { position: 'fixed', opacity: '0' } });
    ta.value = text;
    document.body.append(ta);
    ta.select();
    let ok = false;
    try { ok = document.execCommand('copy'); } catch { ok = false; }
    ta.remove();
    return ok;
  }
}

export function safeFilename(name) {
  return (String(name || 'export').replace(/[^\p{L}\p{N} _-]+/gu, '').trim().replace(/\s+/g, '_').slice(0, 80)) || 'export';
}

export function plural(n, word, pluralWord) {
  return `${n} ${n === 1 ? word : (pluralWord || `${word}s`)}`;
}

// Custom line icons for the few symbols Material Symbols doesn't have
// (Studio tiles and the research chip). Drawn with currentColor.
const SVG_ICONS = {
  audio: '<path d="M4 10.5v5M8.5 6.5v13M13 10v6" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"/><path d="M18.5 2.6l1 2.6 2.6 1-2.6 1-1 2.6-1-2.6-2.6-1 2.6-1z" fill="currentColor"/>',
  slides: '<rect x="2.8" y="5.3" width="18.4" height="13.4" rx="2.2" fill="none" stroke="currentColor" stroke-width="1.9"/><path d="M6.4 5.6v12.8M17.6 5.6v12.8" stroke="currentColor" stroke-width="1.9"/>',
  reports: '<path d="M4.2 8.5v10.3a2.2 2.2 0 0 0 2.2 2.2h10.3" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"/><path d="M14 3.8H10a2.2 2.2 0 0 0-2.2 2.2v8.8A2.2 2.2 0 0 0 10 17h8.8a2.2 2.2 0 0 0 2.2-2.2V11" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"/><path d="M18.6 2.2l.95 2.45 2.45.95-2.45.95-.95 2.45-.95-2.45-2.45-.95 2.45-.95z" fill="currentColor"/>',
  flashcards: '<rect x="3.6" y="7.2" width="10.6" height="13.8" rx="3" fill="none" stroke="currentColor" stroke-width="1.9"/><rect x="9.6" y="3" width="10.6" height="13.8" rx="3" fill="var(--surface, #171717)" stroke="currentColor" stroke-width="1.9"/>',
  research: '<circle cx="10" cy="11" r="5.6" fill="none" stroke="currentColor" stroke-width="1.9"/><path d="M14.2 15.2l4.6 4.6" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"/><path d="M17.7 2.4l.9 2.3 2.3.9-2.3.9-.9 2.3-.9-2.3-2.3-.9 2.3-.9z" fill="currentColor"/>',
};

export function svgIcon(name, cls = '') {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('width', '22');
  svg.setAttribute('height', '22');
  svg.setAttribute('aria-hidden', 'true');
  svg.setAttribute('class', `svg-ic ${cls}`.trim());
  svg.innerHTML = SVG_ICONS[name] || '';
  return svg;
}

export function hasSvgIcon(name) { return Object.prototype.hasOwnProperty.call(SVG_ICONS, name); }
