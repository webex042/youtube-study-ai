// Entry point: boots either the notebook list or a notebook workspace.

import { installTooltips } from './lib/ui.js';

const page = document.documentElement.dataset.page;
installTooltips();

async function boot() {
  try {
    if (page === 'notebook') {
      const { mountNotebook } = await import('./notebook/page.js');
      await mountNotebook(document.body.dataset.notebookId);
    } else {
      const { mountHome } = await import('./home.js');
      await mountHome();
    }
  } catch (err) {
    console.error(err);
    const app = document.getElementById('app');
    app.textContent = '';
    const box = document.createElement('div');
    box.style.cssText = 'padding:60px 24px;text-align:center;color:#c7c7c7;font-size:15px;line-height:1.6';
    box.textContent = `Something went wrong while loading: ${err.message || err}. Make sure the server is running, then reload.`;
    app.append(box);
  } finally {
    document.body.classList.remove('is-loading');
  }
}

boot();
