// Infographic: a one-page visual summary rendered as crisp HTML (so text is
// always correct and readable), with colour themes and PNG export.

import { h, icon, iconBtn, safeFilename } from '../lib/dom.js';
import { toast } from '../lib/ui.js';
import { loadHtmlToImage } from './mindmap.js';

const THEMES = [
  { head: 'linear-gradient(135deg, #1f3a93, #5b3fd0)', accents: ['#3b6cf6', '#8b5cf6', '#06a3a3', '#e8833a', '#d9468c', '#2f9e5b'] },
  { head: 'linear-gradient(135deg, #0f766e, #155e75)', accents: ['#0d9488', '#0284c7', '#7c3aed', '#ea580c', '#db2777', '#65a30d'] },
  { head: 'linear-gradient(135deg, #9a3412, #be185d)', accents: ['#ea580c', '#db2777', '#7c3aed', '#0284c7', '#059669', '#ca8a04'] },
  { head: 'linear-gradient(135deg, #312e81, #0f172a)', accents: ['#6366f1', '#0ea5e9', '#14b8a6', '#f59e0b', '#ef4444', '#a855f7'] },
];

export function render(host, art, ctx) {
  const c = art.content;
  let themeIndex = Number.isInteger(c.theme) ? c.theme % THEMES.length : 0;
  const holder = h('div');

  function draw() {
    const theme = THEMES[themeIndex];
    const accent = (i) => theme.accents[i % theme.accents.length];
    const card = h('div', { class: 'ig' },
      h('div', { class: 'ig-head', style: { background: theme.head } }, h('h2', {}, c.title), h('p', {}, c.subtitle)),
      c.stats && c.stats.length ? h('div', { class: 'ig-stats' }, ...c.stats.map((s, i) => h('div', { class: 'ig-stat' },
        h('div', { class: 'v', style: { color: accent(i) } }, s.value), h('div', { class: 'l' }, s.label)))) : null,
      h('div', { class: 'ig-sections' }, ...c.sections.map((sec, i) => h('div', { class: 'ig-sec', style: { borderTop: `4px solid ${accent(i)}` } },
        h('h3', {}, h('span', { class: 'ms', style: { background: accent(i) } }, sec.icon), sec.heading),
        h('ul', {}, ...sec.points.map((p) => h('li', {}, p))),
        sec.stat_value ? h('div', { style: { marginTop: '6px', fontSize: '12.5px', color: '#5b5f73' } }, h('b', { style: { color: accent(i), fontSize: '16px' } }, sec.stat_value), ` ${sec.stat_label}`) : null))),
      c.steps && c.steps.length ? h('div', { class: 'ig-steps' }, h('h3', {}, c.steps_title || 'How it works'),
        h('div', { class: 'ig-step-row' }, ...c.steps.flatMap((s, i) => [
          h('div', { class: 'ig-step' }, h('b', { style: { background: accent(i) } }, String(i + 1)), s),
          i < c.steps.length - 1 ? h('span', { class: 'ms ig-arrow' }, 'arrow_forward') : null]))) : null,
      h('div', { class: 'ig-take', style: { background: theme.head } }, h('span', { class: 'ms' }, 'tips_and_updates'), h('span', {}, h('b', {}, 'Key takeaway: '), c.takeaway)),
      h('div', { class: 'ig-foot' }, `Made with AI Study Lab · ${new Date(art.created_at * 1000).toLocaleDateString()}`));
    holder.replaceChildren(card);
  }

  async function exportPng() {
    toast('Preparing image…');
    try {
      await loadHtmlToImage();
      const url = await window.htmlToImage.toPng(holder.firstChild, { pixelRatio: 2, backgroundColor: '#fffdf8' });
      const a = document.createElement('a');
      a.href = url;
      a.download = `${safeFilename(art.title)}.png`;
      a.click();
    } catch (e) { toast(`Couldn't export the image: ${e.message || e}`, { type: 'error' }); }
  }

  ctx.toolbar.replaceChildren(
    iconBtn('palette', 'Change colours', () => { themeIndex = (themeIndex + 1) % THEMES.length; draw(); }),
    h('button', { class: 'btn btn-tonal small', type: 'button', onclick: exportPng }, icon('image'), 'Download PNG'),
    h('span', { class: 'grow' }));
  draw();
  host.replaceChildren(holder);
  return null;
}
