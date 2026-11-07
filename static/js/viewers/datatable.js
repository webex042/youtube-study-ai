// Data table: sortable, filterable, editable cells; CSV / Markdown export.

import { api } from '../lib/api.js';
import { h, icon, iconBtn, debounce, download, safeFilename } from '../lib/dom.js';
import { notifyError, openMenu, toast } from '../lib/ui.js';

export function render(host, art, ctx) {
  const c = art.content;
  const columns = c.columns || [];
  let rows = (c.rows || []).map((r) => [...r]);
  let sortCol = -1;
  let sortDir = 1;
  let filter = '';
  let editing = false;

  const save = debounce(async () => {
    try { await api.patch(`/api/artifacts/${art.id}`, { content: { rows } }); toast('Table saved', { type: 'success' }); } catch (e) { notifyError(e); }
  }, 800);

  const filterInput = h('input', { class: 'field', type: 'search', placeholder: 'Filter rows…', style: { maxWidth: '260px', height: '34px', padding: '6px 12px' } });
  filterInput.addEventListener('input', () => { filter = filterInput.value.toLowerCase(); draw(); });
  const tableHost = h('div', { class: 'dt-scroll scroll' });

  function draw() {
    let view = rows.map((r, i) => ({ r, i }));
    if (filter) view = view.filter(({ r }) => r.some((cell) => cell.toLowerCase().includes(filter)));
    if (sortCol >= 0) {
      view.sort((a, b) => {
        const x = a.r[sortCol]; const y = b.r[sortCol];
        const nx = parseFloat(x); const ny = parseFloat(y);
        if (!Number.isNaN(nx) && !Number.isNaN(ny)) return (nx - ny) * sortDir;
        return x.localeCompare(y) * sortDir;
      });
    }
    const table = h('table', { class: 'dt' },
      h('thead', {}, h('tr', {}, ...columns.map((col, i) => h('th', {
        onclick: () => { sortDir = sortCol === i ? -sortDir : 1; sortCol = i; draw(); }, 'aria-sort': sortCol === i ? (sortDir > 0 ? 'ascending' : 'descending') : 'none',
      }, col, ' ', icon(sortCol === i ? (sortDir > 0 ? 'arrow_upward' : 'arrow_downward') : 'unfold_more'))))),
      h('tbody', {}, ...view.map(({ r, i }) => h('tr', {}, ...r.map((cell, j) => {
        const td = h('td', { contenteditable: editing ? 'true' : 'false' }, cell);
        if (editing) td.addEventListener('input', () => { rows[i][j] = td.textContent; save(); });
        return td;
      })))));
    tableHost.replaceChildren(table);
  }

  const toCsv = () => [columns, ...rows].map((r) => r.map((cell) => `"${String(cell).replace(/"/g, '""')}"`).join(',')).join('\n');
  const toMd = () => `| ${columns.join(' | ')} |\n|${columns.map(() => '---').join('|')}|\n${rows.map((r) => `| ${r.map((x) => x.replace(/\|/g, '\\|')).join(' | ')} |`).join('\n')}`;

  ctx.toolbar.replaceChildren(
    filterInput,
    iconBtn('edit', 'Edit cells', (e) => { editing = !editing; e.currentTarget.classList.toggle('active', editing); draw(); toast(editing ? 'Click any cell to edit — changes save automatically.' : 'Editing off'); }),
    h('button', { class: 'icon-btn', type: 'button', 'aria-label': 'Download', 'data-tip': 'Download', onclick: (e) => openMenu(e.currentTarget, [
      { icon: 'table_view', label: 'CSV (Excel / Google Sheets)', run: () => download(`${safeFilename(art.title)}.csv`, `﻿${toCsv()}`, 'text/csv') },
      { icon: 'description', label: 'Markdown table', run: () => download(`${safeFilename(art.title)}.md`, `# ${art.title}\n\n${c.description || ''}\n\n${toMd()}\n`, 'text/markdown') },
    ]) }, icon('download')),
    h('span', { class: 'grow' }),
    h('span', { class: 'muted', style: { fontSize: '12px' } }, `${rows.length} rows`));

  host.replaceChildren(h('div', { class: 'dt-wrap' },
    c.description ? h('div', { class: 'dt-desc' }, c.description) : null,
    tableHost,
    c.notes ? h('div', { class: 'dt-desc', style: { fontSize: '12.5px' } }, `Note: ${c.notes}`) : null));
  draw();
  return null;
}
