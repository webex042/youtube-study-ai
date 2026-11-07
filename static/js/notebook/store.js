// Notebook state + polling. Components subscribe to change events.

import { api } from '../lib/api.js';

const listeners = new Map();

export const store = {
  notebookId: '',
  notebook: null,
  sources: [],
  artifacts: [],
  notes: [],
  ai: { configured: false },
  settings: { display_name: '', pro_mode: false },
  messages: [],
  pollTimer: null,
  lastSnapshot: {},
  busyChat: false,

  on(event, fn) {
    if (!listeners.has(event)) listeners.set(event, new Set());
    listeners.get(event).add(fn);
    return () => listeners.get(event).delete(fn);
  },

  emit(event, data) {
    for (const fn of listeners.get(event) || []) {
      try { fn(data); } catch (e) { console.error(e); }
    }
  },

  apply(state) {
    const changed = [];
    for (const key of ['notebook', 'sources', 'artifacts', 'notes', 'ai']) {
      const json = JSON.stringify(state[key]);
      if (json !== this.lastSnapshot[key]) {
        this.lastSnapshot[key] = json;
        this[key] = state[key];
        changed.push(key);
      }
    }
    for (const key of changed) this.emit(key, this[key]);
    if (changed.length) this.emit('change', changed);
  },

  async load(id) {
    this.notebookId = id;
    const [state, msgs, settings] = await Promise.all([
      api.get(`/api/notebooks/${id}`),
      api.get(`/api/notebooks/${id}/messages`),
      api.get('/api/settings'),
    ]);
    this.settings = settings.settings;
    this.messages = msgs.messages;
    this.apply(state);
    this.emit('messages', this.messages);
    this.emit('settings', this.settings);
    this.schedulePoll();
  },

  async refresh() {
    if (!this.notebookId) return;
    try {
      const state = await api.get(`/api/notebooks/${this.notebookId}/state`);
      this.apply(state);
    } catch (e) {
      if (e.status === 404) { window.location.href = '/'; return; }
    }
    this.schedulePoll();
  },

  isBusy() {
    return this.sources.some((s) => s.status === 'queued' || s.status === 'processing')
      || this.artifacts.some((a) => a.status === 'processing')
      || (this.notebook && this.notebook.guide_status === 'processing');
  },

  schedulePoll(delay) {
    clearTimeout(this.pollTimer);
    const wait = delay ?? (this.isBusy() ? 1500 : 12000);
    this.pollTimer = setTimeout(() => this.refresh(), wait);
  },

  pokeSoon() { this.schedulePoll(300); },

  get readySources() { return this.sources.filter((s) => s.status === 'ready'); },
  get selectedSources() { return this.sources.filter((s) => s.status === 'ready' && s.selected); },

  sourceById(id) { return this.sources.find((s) => s.id === id); },
};

document.addEventListener('visibilitychange', () => {
  if (!document.hidden && store.notebookId) store.refresh();
});
