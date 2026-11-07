import { h, icon, iconBtn } from './dom.js';

// ── Toasts ───────────────────────────────────────────────────────

export function toast(message, { type = 'info', action, timeout = 4200 } = {}) {
  const host = document.getElementById('toasts');
  const el = h('div', { class: `toast ${type}` },
    type === 'success' ? icon('check_circle', 'sm') : type === 'error' ? icon('error', 'sm') : null,
    h('span', {}, message),
    action ? h('button', { type: 'button', onclick: () => { action.run(); el.remove(); } }, action.label) : null);
  host.append(el);
  setTimeout(() => el.remove(), type === 'error' ? Math.max(timeout, 7000) : timeout);
  return el;
}

export const notifyError = (err) => toast(err && err.message ? err.message : String(err), { type: 'error' });

// ── Modals ───────────────────────────────────────────────────────

let openModals = 0;

export function modal({ title, body, footer, size = '', onClose, dismissible = true }) {
  const overlay = h('div', { class: 'overlay', role: 'presentation' });
  const close = () => {
    overlay.remove();
    openModals -= 1;
    document.removeEventListener('keydown', onKey);
    if (onClose) onClose();
  };
  const onKey = (e) => { if (e.key === 'Escape' && dismissible) { e.stopPropagation(); close(); } };
  const box = h('div', { class: `modal ${size}`, role: 'dialog', 'aria-modal': 'true', 'aria-label': title || 'Dialog' },
    h('div', { class: 'modal-head' }, h('h2', {}, title || ''), iconBtn('close', 'Close', close)),
    h('div', { class: 'modal-body scroll' }, body),
    footer ? h('div', { class: 'modal-foot' }, footer) : null);
  overlay.append(box);
  overlay.addEventListener('mousedown', (e) => { if (e.target === overlay && dismissible) close(); });
  document.addEventListener('keydown', onKey);
  document.body.append(overlay);
  openModals += 1;
  requestAnimationFrame(() => {
    const focusable = box.querySelector('[autofocus], input, textarea, select, .modal-body button');
    if (focusable) focusable.focus();
  });
  return { close, box, overlay };
}

export function confirmDialog({ title, message, confirm = 'Delete', danger = true }) {
  return new Promise((resolve) => {
    let done = false;
    const finish = (v) => { if (!done) { done = true; resolve(v); } };
    const m = modal({
      title, size: 'sm', body: h('p', { class: 'lead' }, message),
      footer: [
        h('button', { class: 'btn btn-text', type: 'button', onclick: () => { m.close(); finish(false); } }, 'Cancel'),
        h('button', { class: `btn ${danger ? 'btn-danger' : 'btn-primary'}`, type: 'button', autofocus: true,
          onclick: () => { m.close(); finish(true); } }, confirm),
      ],
      onClose: () => finish(false),
    });
  });
}

export function promptDialog({ title, label, value = '', placeholder = '', confirm = 'Save', multiline = false }) {
  return new Promise((resolve) => {
    let done = false;
    const finish = (v) => { if (!done) { done = true; resolve(v); } };
    const input = multiline
      ? h('textarea', { class: 'field', placeholder, rows: 5 })
      : h('input', { class: 'field', placeholder, type: 'text' });
    input.value = value;
    const submit = () => { m.close(); finish(input.value.trim()); };
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !multiline) submit(); });
    const m = modal({
      title, size: 'sm',
      body: h('div', {}, label ? h('label', { class: 'label' }, label) : null, input),
      footer: [
        h('button', { class: 'btn btn-text', type: 'button', onclick: () => { m.close(); finish(null); } }, 'Cancel'),
        h('button', { class: 'btn btn-primary', type: 'button', onclick: submit }, confirm),
      ],
      onClose: () => finish(null),
    });
    setTimeout(() => { input.focus(); input.select(); }, 30);
  });
}

// ── Menus & popovers ─────────────────────────────────────────────

let activeMenu = null;

export function closeMenu() {
  if (activeMenu) { activeMenu.remove(); activeMenu = null; }
}

export function openMenu(anchor, items, { align = 'end' } = {}) {
  closeMenu();
  const menu = h('div', { class: 'menu', role: 'menu' });
  for (const item of items) {
    if (!item) continue;
    if (item === '-') { menu.append(h('div', { class: 'sep' })); continue; }
    if (item.label && item.header) { menu.append(h('div', { class: 'menu-label' }, item.label)); continue; }
    menu.append(h('button', {
      type: 'button', role: 'menuitem', class: item.danger ? 'danger' : '',
      onclick: (e) => { e.stopPropagation(); closeMenu(); item.run(); },
    }, item.icon ? icon(item.icon) : null, h('span', {}, item.label), item.checked ? icon('check', 'check sm') : null));
  }
  document.body.append(menu);
  position(menu, anchor, align);
  activeMenu = menu;
  setTimeout(() => {
    const away = (e) => { if (!menu.contains(e.target)) { closeMenu(); document.removeEventListener('mousedown', away); } };
    document.addEventListener('mousedown', away);
  }, 0);
  return menu;
}

export function position(el, anchor, align = 'end') {
  const r = anchor.getBoundingClientRect();
  const w = el.offsetWidth;
  const hgt = el.offsetHeight;
  let left = align === 'start' ? r.left : r.right - w;
  let top = r.bottom + 6;
  if (top + hgt > window.innerHeight - 8) top = Math.max(8, r.top - hgt - 6);
  left = Math.min(Math.max(8, left), window.innerWidth - w - 8);
  el.style.left = `${left}px`;
  el.style.top = `${top}px`;
}

let activePopover = null;

export function closePopover() {
  if (activePopover) { activePopover.remove(); activePopover = null; }
}

export function showPopover(anchor, content, { align = 'start' } = {}) {
  closePopover();
  const pop = h('div', { class: 'popover' }, content);
  document.body.append(pop);
  position(pop, anchor, align);
  activePopover = pop;
  setTimeout(() => {
    const away = (e) => {
      if (!pop.contains(e.target) && e.target !== anchor) { closePopover(); document.removeEventListener('mousedown', away); }
    };
    document.addEventListener('mousedown', away);
  }, 0);
  return pop;
}

// ── Tooltips (for icon buttons with data-tip) ───────────────────

export function installTooltips() {
  let tip = null;
  let timer = null;
  const hide = () => { clearTimeout(timer); if (tip) { tip.remove(); tip = null; } };
  document.addEventListener('mouseover', (e) => {
    const target = e.target.closest('[data-tip]');
    if (!target) return;
    hide();
    timer = setTimeout(() => {
      tip = h('div', { class: 'tooltip' }, target.getAttribute('data-tip'));
      document.body.append(tip);
      const r = target.getBoundingClientRect();
      const left = Math.min(Math.max(6, r.left + r.width / 2 - tip.offsetWidth / 2), window.innerWidth - tip.offsetWidth - 6);
      const top = r.bottom + 8 + tip.offsetHeight > window.innerHeight ? r.top - tip.offsetHeight - 8 : r.bottom + 8;
      tip.style.left = `${left}px`;
      tip.style.top = `${top}px`;
    }, 450);
  });
  document.addEventListener('mouseout', (e) => { if (e.target.closest('[data-tip]')) hide(); });
  document.addEventListener('mousedown', hide);
  window.addEventListener('blur', hide);
}

export function segmented(options, value, onChange) {
  const wrap = h('div', { class: 'seg', role: 'radiogroup' });
  const render = () => {
    wrap.replaceChildren(...options.map((o) => h('button', {
      type: 'button', class: o.value === value ? 'on' : '', role: 'radio', 'aria-checked': String(o.value === value),
      onclick: () => { value = o.value; render(); onChange(value); },
    }, o.label)));
  };
  render();
  return wrap;
}
