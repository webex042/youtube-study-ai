// Mind map: collapsible left-to-right concept tree with curved connectors,
// cross-links between related concepts, pan/zoom, fit, PNG/Markdown export.
// Clicking a concept shows its note and lets the student ask the tutor about it.

import { h, icon, iconBtn, download, safeFilename } from '../lib/dom.js';
import { openMenu, toast } from '../lib/ui.js';

const SVGNS = 'http://www.w3.org/2000/svg';

export function render(host, art, ctx) {
  const root = art.content.root;
  const links = art.content.links || [];
  if (!root) { host.replaceChildren(h('div', { class: 'viewer-progress' }, 'This mind map is empty.')); return null; }

  const byId = new Map();
  const parentOf = new Map();
  (function index(node, depth, parent) {
    node.depth = depth;
    byId.set(node.id, node);
    if (parent) parentOf.set(node.id, parent);
    node.collapsed = depth >= 2 && !!(node.children && node.children.length);
    (node.children || []).forEach((c) => index(c, depth + 1, node));
  }(root, 0, null));

  let showLinks = true;
  let scale = 1;
  let tx = 30;
  let ty = 30;
  let selected = null;

  const wrap = h('div', { class: 'mm-wrap' });
  const canvas = h('div', { class: 'mm-canvas' });
  const svg = document.createElementNS(SVGNS, 'svg');
  canvas.append(svg);
  wrap.append(canvas);
  const tip = h('div', { class: 'mm-tip hidden' });
  wrap.append(tip,
    h('div', { class: 'mm-controls' },
      h('button', { type: 'button', 'aria-label': 'Zoom in', 'data-tip': 'Zoom in', onclick: () => zoom(1.2) }, icon('add')),
      h('button', { type: 'button', 'aria-label': 'Zoom out', 'data-tip': 'Zoom out', onclick: () => zoom(1 / 1.2) }, icon('remove')),
      h('button', { type: 'button', 'aria-label': 'Fit to screen', 'data-tip': 'Fit to screen', onclick: fit }, icon('fit_screen'))));

  const nodeEls = new Map();
  for (const node of byId.values()) {
    const el = h('div', { class: `mm-node depth-${Math.min(node.depth, 5)}`, 'data-id': node.id, tabindex: '0', role: 'button' },
      h('span', { class: 'lbl' }, node.label));
    el.addEventListener('click', (e) => {
      e.stopPropagation();
      if (e.target.closest('.tog')) { node.collapsed = !node.collapsed; layout(true); return; }
      select(node);
    });
    el.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') select(node);
      if (e.key === ' ' && node.children && node.children.length) { e.preventDefault(); node.collapsed = !node.collapsed; layout(true); }
    });
    if (node.children && node.children.length) el.append(h('span', { class: 'tog', 'aria-label': 'Expand or collapse' }, '›'));
    nodeEls.set(node.id, el);
    canvas.append(el);
  }

  function visibleNodes() {
    const out = [];
    (function walk(n) { out.push(n); if (!n.collapsed) (n.children || []).forEach(walk); }(root));
    return out;
  }

  function layout(animate = false) {
    const vis = new Set(visibleNodes().map((n) => n.id));
    for (const [id, el] of nodeEls) {
      el.style.display = vis.has(id) ? '' : 'none';
      el.style.transition = animate ? 'left .25s ease, top .25s ease' : 'none';
      const node = byId.get(id);
      const tog = el.querySelector('.tog');
      if (tog) tog.textContent = node.collapsed ? '›' : '‹';
    }
    // Column x by depth, using measured widths.
    const colWidth = [];
    for (const n of byId.values()) {
      if (!vis.has(n.id)) continue;
      const w = nodeEls.get(n.id).offsetWidth;
      colWidth[n.depth] = Math.max(colWidth[n.depth] || 0, w);
    }
    const colX = [0];
    for (let d = 1; d < colWidth.length; d += 1) colX[d] = colX[d - 1] + (colWidth[d - 1] || 0) + 70;
    // Y by leaf order.
    let cursor = 0;
    const gap = 14;
    (function place(n) {
      const el = nodeEls.get(n.id);
      const hgt = el.offsetHeight;
      const kids = n.collapsed ? [] : (n.children || []);
      if (!kids.length) {
        n.y = cursor + hgt / 2;
        cursor += hgt + gap;
      } else {
        kids.forEach(place);
        n.y = (kids[0].y + kids[kids.length - 1].y) / 2;
      }
      n.x = colX[n.depth];
      n.w = el.offsetWidth;
      n.h = hgt;
      el.style.left = `${n.x}px`;
      el.style.top = `${n.y - hgt / 2}px`;
    }(root));
    drawEdges(vis, animate);
  }

  function drawEdges(vis, animate) {
    const draw = () => {
      svg.replaceChildren();
      const maxX = Math.max(...[...vis].map((id) => byId.get(id).x + byId.get(id).w)) + 40;
      const maxY = Math.max(...[...vis].map((id) => byId.get(id).y + byId.get(id).h)) + 40;
      svg.setAttribute('width', maxX);
      svg.setAttribute('height', maxY);
      canvas.style.width = `${maxX}px`;
      canvas.style.height = `${maxY}px`;
      for (const id of vis) {
        const n = byId.get(id);
        for (const c of (n.collapsed ? [] : n.children || [])) {
          const x1 = n.x + n.w; const y1 = n.y; const x2 = c.x; const y2 = c.y;
          const mid = (x1 + x2) / 2;
          const path = document.createElementNS(SVGNS, 'path');
          path.setAttribute('d', `M${x1},${y1} C${mid},${y1} ${mid},${y2} ${x2},${y2}`);
          path.setAttribute('fill', 'none');
          path.setAttribute('stroke', c.depth === 1 ? '#5b6bb8' : '#4a5a6e');
          path.setAttribute('stroke-width', c.depth === 1 ? '2.2' : '1.6');
          svg.append(path);
        }
      }
      if (showLinks) {
        for (const link of links) {
          if (!vis.has(link.from) || !vis.has(link.to)) continue;
          const a = byId.get(link.from); const b = byId.get(link.to);
          const x1 = a.x + a.w / 2; const y1 = a.y + a.h / 2; const x2 = b.x + b.w / 2; const y2 = b.y - b.h / 2;
          const bend = Math.max(60, Math.abs(y2 - y1) * 0.3);
          const path = document.createElementNS(SVGNS, 'path');
          path.setAttribute('d', `M${x1},${y1} C${x1 + bend},${y1} ${x2 + bend},${y2} ${x2},${y2}`);
          path.setAttribute('fill', 'none');
          path.setAttribute('stroke', '#b388ff');
          path.setAttribute('stroke-opacity', '0.55');
          path.setAttribute('stroke-width', '1.4');
          path.setAttribute('stroke-dasharray', '5 5');
          svg.append(path);
          if (link.label) {
            const t = document.createElementNS(SVGNS, 'text');
            t.setAttribute('x', String((x1 + x2) / 2 + bend * 0.75));
            t.setAttribute('y', String((y1 + y2) / 2));
            t.setAttribute('fill', '#c9b3ff');
            t.setAttribute('font-size', '11');
            t.setAttribute('font-family', 'Google Sans, sans-serif');
            t.textContent = link.label;
            svg.append(t);
          }
        }
      }
    };
    if (animate) setTimeout(draw, 260); else draw();
    if (animate) draw();
  }

  function apply() { canvas.style.transform = `translate(${tx}px, ${ty}px) scale(${scale})`; }

  function zoom(f, cx, cy) {
    const rect = wrap.getBoundingClientRect();
    const px = cx ?? rect.width / 2;
    const py = cy ?? rect.height / 2;
    const ns = Math.min(2.5, Math.max(0.25, scale * f));
    tx = px - ((px - tx) * ns) / scale;
    ty = py - ((py - ty) * ns) / scale;
    scale = ns;
    apply();
  }

  function fit() {
    const rect = wrap.getBoundingClientRect();
    const w = canvas.offsetWidth || 1;
    const hgt = canvas.offsetHeight || 1;
    scale = Math.min(1.2, Math.max(0.3, Math.min((rect.width - 60) / w, (rect.height - 60) / hgt)));
    tx = Math.max(20, (rect.width - w * scale) / 2);
    ty = Math.max(20, (rect.height - hgt * scale) / 2);
    apply();
  }

  function pathTo(node) {
    const names = [];
    let cur = node;
    while (cur) { names.unshift(cur.label); cur = parentOf.get(cur.id); }
    return names;
  }

  function select(node) {
    if (selected) nodeEls.get(selected.id).classList.remove('selected');
    selected = node;
    nodeEls.get(node.id).classList.add('selected');
    const path = pathTo(node);
    tip.replaceChildren(
      h('div', {}, h('b', {}, node.label), node.note ? ` — ${node.note}` : ''),
      h('div', { class: 'muted', style: { fontSize: '12px', marginTop: '2px' } }, path.join(' › ')),
      h('div', { class: 'row' },
        h('button', { class: 'btn btn-tonal small', type: 'button', onclick: async () => { const { askInChat } = await import('../notebook/chat.js'); askInChat(`Explain "${node.label}" (in the context of ${path.slice(0, -1).join(' › ') || 'this topic'}) with a simple example, and how it connects to the related concepts.`); } }, icon('psychology'), 'Explain this'),
        h('button', { class: 'btn btn-text small', type: 'button', onclick: async () => { const { askInChat } = await import('../notebook/chat.js'); askInChat(`Quiz me with 3 questions about "${node.label}".`); } }, icon('quiz'), 'Quiz me'),
        h('button', { class: 'btn btn-text small', type: 'button', onclick: () => { tip.classList.add('hidden'); } }, 'Close')));
    tip.classList.remove('hidden');
  }

  // Pan & zoom
  let drag = null;
  wrap.addEventListener('pointerdown', (e) => {
    if (e.target.closest('.mm-node, .mm-controls, .mm-tip')) return;
    drag = { x: e.clientX, y: e.clientY, tx, ty };
    wrap.classList.add('dragging');
    wrap.setPointerCapture(e.pointerId);
  });
  wrap.addEventListener('pointermove', (e) => {
    if (!drag) return;
    tx = drag.tx + e.clientX - drag.x;
    ty = drag.ty + e.clientY - drag.y;
    apply();
  });
  const end = () => { drag = null; wrap.classList.remove('dragging'); };
  wrap.addEventListener('pointerup', end);
  wrap.addEventListener('pointercancel', end);
  wrap.addEventListener('wheel', (e) => {
    e.preventDefault();
    const rect = wrap.getBoundingClientRect();
    zoom(e.deltaY < 0 ? 1.1 : 1 / 1.1, e.clientX - rect.left, e.clientY - rect.top);
  }, { passive: false });

  ctx.toolbar.replaceChildren(
    iconBtn('unfold_more', 'Expand all', () => { byId.forEach((n) => { n.collapsed = false; }); layout(true); setTimeout(fit, 300); }),
    iconBtn('unfold_less', 'Collapse to main branches', () => { byId.forEach((n) => { n.collapsed = n.depth >= 1 && !!(n.children && n.children.length); }); layout(true); setTimeout(fit, 300); }),
    iconBtn('hub', 'Show / hide cross-links', () => { showLinks = !showLinks; layout(false); }),
    h('button', { class: 'icon-btn', type: 'button', 'aria-label': 'Download', 'data-tip': 'Download', onclick: (e) => openMenu(e.currentTarget, [
      { icon: 'image', label: 'PNG image', run: exportPng },
      { icon: 'description', label: 'Markdown outline', run: () => {
        const lines = [];
        (function walk(n, d) { lines.push(`${'  '.repeat(d)}- **${n.label}**${n.note ? ` — ${n.note}` : ''}`); (n.children || []).forEach((c) => walk(c, d + 1)); }(root, 0));
        download(`${safeFilename(art.title)}.md`, `# ${art.title}\n\n${lines.join('\n')}\n`, 'text/markdown');
      } },
    ]) }, icon('download')),
    h('span', { class: 'grow' }),
    h('span', { class: 'muted', style: { fontSize: '12px' } }, 'Drag to pan · scroll to zoom · click a concept'));

  async function exportPng() {
    toast('Preparing image…');
    try {
      await loadHtmlToImage();
      const prev = canvas.style.transform;
      canvas.style.transform = 'none';
      const url = await window.htmlToImage.toPng(canvas, { backgroundColor: '#121212', pixelRatio: 2 });
      canvas.style.transform = prev;
      const a = document.createElement('a');
      a.href = url;
      a.download = `${safeFilename(art.title)}.png`;
      a.click();
    } catch (e) { toast(`Couldn't export the image: ${e.message || e}`, { type: 'error' }); }
  }

  wrap.addEventListener('click', (e) => { if (!e.target.closest('.mm-node, .mm-tip')) tip.classList.add('hidden'); });
  host.style.padding = '0 12px 12px';
  host.replaceChildren(wrap);
  wrap.style.height = '100%';
  requestAnimationFrame(() => { layout(false); fit(); });
  const ro = new ResizeObserver(() => fit());
  ro.observe(wrap);
  return () => ro.disconnect();
}

export function loadHtmlToImage() {
  if (window.htmlToImage) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const s = document.createElement('script');
    s.src = '/static/vendor/html-to-image.js';
    s.onload = resolve;
    s.onerror = () => reject(new Error('could not load exporter'));
    document.head.append(s);
  });
}
