// Slide deck: 16:9 slides with several layouts, thumbnails, speaker notes,
// presenter mode (full screen, keyboard) and PDF / Markdown export.

import { h, icon, iconBtn, download, safeFilename } from '../lib/dom.js';
import { openMenu } from '../lib/ui.js';
import { renderMarkdown } from '../lib/markdown.js';

export function slideElement(s, index, total) {
  const el = h('div', { class: `slide ${s.layout}` });
  const num = h('div', { class: 'num' }, `${index + 1} / ${total}`);
  if (s.layout === 'title') {
    el.append(h('h2', {}, s.title), s.subtitle ? h('div', { class: 'sub' }, s.subtitle) : null);
  } else if (s.layout === 'two_column') {
    el.append(h('h2', {}, s.title), h('div', { class: 'cols' },
      h('div', { class: 'col' }, h('h3', {}, s.left_title || ''), h('ul', {}, ...s.left.map((b) => h('li', {}, inline(b))))),
      h('div', { class: 'col' }, h('h3', {}, s.right_title || ''), h('ul', {}, ...s.right.map((b) => h('li', {}, inline(b)))))));
  } else if (s.layout === 'code') {
    el.append(h('h2', {}, s.title), h('div', { class: 'code-grid' }, h('pre', {}, s.code),
      h('ul', {}, ...s.bullets.map((b) => h('li', {}, inline(b))))));
  } else if (s.layout === 'quote') {
    el.append(h('h2', {}, s.title), h('blockquote', {}, s.quote || s.bullets.join(' ')));
  } else {
    el.append(h('h2', {}, s.title), h('ul', {}, ...s.bullets.map((b) => h('li', {}, inline(b)))));
  }
  el.append(num);
  return el;
}

function inline(text) {
  const md = renderMarkdown(text);
  const p = md.querySelector('p');
  const span = document.createElement('span');
  span.append(...(p ? p.childNodes : md.childNodes));
  return span;
}

export function render(host, art, ctx) {
  const deck = art.content;
  const slides = deck.slides || [];
  let pos = 0;

  const stage = h('div', { class: 'slide-stage' });
  const notes = h('div', { class: 'slide-notes' });
  const counter = h('span');
  const thumbs = h('div', { class: 'slide-thumbs' });

  function draw() {
    stage.replaceChildren(slideElement(slides[pos], pos, slides.length));
    notes.replaceChildren(h('b', { style: { color: '#e3e3e3', fontWeight: '500' } }, 'Speaker notes: '), slides[pos].notes || '—');
    counter.textContent = `Slide ${pos + 1} of ${slides.length}`;
    thumbs.querySelectorAll('.slide-thumb').forEach((t, i) => t.classList.toggle('current', i === pos));
    const cur = thumbs.children[pos];
    if (cur) cur.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  }

  slides.forEach((s, i) => thumbs.append(h('button', {
    class: `slide-thumb ${s.layout === 'title' ? 'title-thumb' : ''}`, type: 'button', onclick: () => { pos = i; draw(); },
  }, h('b', {}, `${i + 1}. `), s.title)));

  const go = (d) => { pos = Math.min(slides.length - 1, Math.max(0, pos + d)); draw(); };

  function present() {
    let p = pos;
    const pstage = h('div', { class: 'slide-stage' });
    const overlay = h('div', { class: 'present-mode', tabindex: '0' }, pstage);
    const show = () => pstage.replaceChildren(slideElement(slides[p], p, slides.length));
    const key = (e) => {
      if (['ArrowRight', 'PageDown', ' '].includes(e.key)) { p = Math.min(slides.length - 1, p + 1); show(); e.preventDefault(); }
      if (['ArrowLeft', 'PageUp'].includes(e.key)) { p = Math.max(0, p - 1); show(); }
      if (e.key === 'Escape') close();
    };
    const close = () => { document.removeEventListener('keydown', key, true); overlay.remove(); if (document.fullscreenElement) document.exitFullscreen().catch(() => {}); pos = p; draw(); };
    overlay.addEventListener('click', (e) => { if (e.clientX > window.innerWidth / 2) p = Math.min(slides.length - 1, p + 1); else p = Math.max(0, p - 1); show(); });
    document.addEventListener('keydown', key, true);
    document.body.append(overlay);
    show();
    overlay.requestFullscreen?.().catch(() => {});
    document.addEventListener('fullscreenchange', () => { if (!document.fullscreenElement && overlay.isConnected) close(); }, { once: true });
  }

  function exportPdf() {
    const w = window.open('', '_blank');
    if (!w) return;
    const origin = window.location.origin;
    w.document.write('<!doctype html><html><head><meta charset="utf-8"></head><body></body></html>');
    w.document.close();
    w.document.title = art.title;
    for (const href of ['/static/vendor/fonts/google-sans.css', '/static/vendor/fonts/google-sans-code.css', '/static/css/app.css']) {
      const link = w.document.createElement('link');
      link.rel = 'stylesheet';
      link.href = origin + href;
      w.document.head.append(link);
    }
    const style = w.document.createElement('style');
    style.textContent = '@page { size: 297mm 167mm; margin: 0; } html, body { background: #fff !important; overflow: visible !important; height: auto !important; } body { margin: 0; } .slide-stage { width: 297mm !important; height: 167mm; aspect-ratio: auto; border-radius: 0; box-shadow: none; page-break-after: always; break-after: page; }';
    w.document.head.append(style);
    slides.forEach((s, i) => {
      const st = w.document.createElement('div');
      st.className = 'slide-stage';
      st.append(w.document.importNode(slideElement(s, i, slides.length), true));
      w.document.body.append(st);
    });
    setTimeout(() => { w.focus(); w.print(); }, 700);
  }

  ctx.toolbar.replaceChildren(
    h('button', { class: 'btn btn-tonal small', type: 'button', onclick: present }, icon('slideshow'), 'Present'),
    h('button', { class: 'icon-btn', type: 'button', 'aria-label': 'Download', 'data-tip': 'Download', onclick: (e) => openMenu(e.currentTarget, [
      { icon: 'picture_as_pdf', label: 'PDF (one slide per page)', run: exportPdf },
      { icon: 'description', label: 'Markdown outline + speaker notes', run: () => {
        const md = slides.map((s, i) => `## ${i + 1}. ${s.title}\n${s.subtitle ? `_${s.subtitle}_\n` : ''}${[...s.bullets, ...s.left, ...s.right].map((b) => `- ${b}`).join('\n')}${s.code ? `\n\`\`\`${s.code_language}\n${s.code}\n\`\`\`` : ''}${s.quote ? `\n> ${s.quote}` : ''}\n\n> Notes: ${s.notes}\n`).join('\n');
        download(`${safeFilename(art.title)}.md`, `# ${art.title}\n\n${md}`, 'text/markdown');
      } },
    ]) }, icon('download')),
    h('span', { class: 'grow' }),
    iconBtn('chevron_left', 'Previous slide', () => go(-1)), counter, iconBtn('chevron_right', 'Next slide', () => go(1)));

  host.replaceChildren(h('div', { class: 'slides-wrap' }, stage, thumbs, notes));
  draw();
  const onKey = (e) => {
    if (!host.isConnected || e.target.closest('input, textarea') || document.querySelector('.present-mode')) return;
    if (e.key === 'ArrowRight') go(1);
    if (e.key === 'ArrowLeft') go(-1);
  };
  document.addEventListener('keydown', onKey);
  return () => document.removeEventListener('keydown', onKey);
}
