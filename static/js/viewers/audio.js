// Audio Overview player: two-host conversation with a synced transcript.

import { h, icon, iconBtn, fmtTime } from '../lib/dom.js';
import { openMenu } from '../lib/ui.js';

export function playerControls(audio, { title, sub, duration, onTime }) {
  const playBtn = h('button', { class: 'play-btn', type: 'button', 'aria-label': 'Play' }, icon('play_arrow'));
  const seek = h('input', { class: 'seek', type: 'range', min: '0', max: String(Math.max(1, duration || 1)), step: '0.1', value: '0', 'aria-label': 'Seek' });
  const cur = h('span', {}, '00:00');
  const total = h('span', {}, fmtTime(duration || 0));
  const speedBtn = h('button', { class: 'btn btn-text small', type: 'button', 'aria-label': 'Playback speed' }, '1×');
  const bars = h('div', { class: 'waveform', 'aria-hidden': 'true' }, ...Array.from({ length: 64 }, (_, i) => {
    const v = 0.25 + 0.75 * Math.abs(Math.sin(i * 1.7) * Math.cos(i * 0.37));
    return h('i', { style: { height: `${Math.round(v * 100)}%` } });
  }));

  playBtn.addEventListener('click', () => { if (audio.paused) audio.play().catch(() => {}); else audio.pause(); });
  audio.addEventListener('play', () => { playBtn.replaceChildren(icon('pause')); playBtn.setAttribute('aria-label', 'Pause'); });
  audio.addEventListener('pause', () => { playBtn.replaceChildren(icon('play_arrow')); playBtn.setAttribute('aria-label', 'Play'); });
  audio.addEventListener('loadedmetadata', () => {
    if (Number.isFinite(audio.duration)) { seek.max = String(audio.duration); total.textContent = fmtTime(audio.duration); }
  });
  audio.addEventListener('timeupdate', () => {
    seek.value = String(audio.currentTime);
    cur.textContent = fmtTime(audio.currentTime);
    const frac = audio.currentTime / (Number(seek.max) || 1);
    [...bars.children].forEach((b, i) => b.classList.toggle('past', i / bars.children.length < frac));
    if (onTime) onTime(audio.currentTime);
  });
  seek.addEventListener('input', () => { audio.currentTime = Number(seek.value); });
  speedBtn.addEventListener('click', (e) => openMenu(e.currentTarget, [0.75, 1, 1.25, 1.5, 1.75, 2].map((r) => ({
    label: `${r}×`, checked: audio.playbackRate === r, run: () => { audio.playbackRate = r; speedBtn.textContent = `${r}×`; },
  }))));

  return h('div', { class: 'player' },
    h('div', { class: 'player-top' }, playBtn, h('div', {}, h('div', { class: 'player-title' }, title), h('div', { class: 'player-sub' }, sub))),
    bars, seek,
    h('div', { class: 'player-row' }, h('span', {}, cur, ' / ', total),
      h('div', { class: 'ctrls' },
        iconBtn('replay_10', 'Back 10 seconds', () => { audio.currentTime = Math.max(0, audio.currentTime - 10); }, 'sm'),
        iconBtn('forward_10', 'Forward 10 seconds', () => { audio.currentTime = Math.min(audio.duration || 1e9, audio.currentTime + 10); }, 'sm'),
        speedBtn)));
}

export function render(host, art, ctx) {
  const c = art.content;
  const audio = new Audio(`/api/artifacts/${art.id}/media`);
  audio.preload = 'metadata';
  const turns = c.turns || [];
  const lines = turns.map((t, i) => h('div', {
    class: 't-line', role: 'button', tabindex: '0', onclick: () => { audio.currentTime = t.start || 0; audio.play().catch(() => {}); },
  }, h('span', { class: 'tm' }, fmtTime(t.start)), h('span', { class: `who ${t.speaker === 'Alex' ? 'a' : 'b'}` }, t.speaker), h('span', {}, t.text)));
  let currentLine = -1;
  const onTime = (time) => {
    let idx = -1;
    for (let i = 0; i < turns.length; i += 1) if ((turns[i].start || 0) <= time + 0.2) idx = i;
    if (idx !== currentLine) {
      if (lines[currentLine]) lines[currentLine].classList.remove('current');
      currentLine = idx;
      if (lines[idx]) { lines[idx].classList.add('current'); if (follow) lines[idx].scrollIntoView({ block: 'nearest', behavior: 'smooth' }); }
    }
  };
  let follow = true;
  const sub = [c.format_label, c.language, `${turns.length} turns`].filter(Boolean).join(' · ');
  ctx.toolbar.replaceChildren(
    h('a', { class: 'btn btn-tonal small', href: `/api/artifacts/${art.id}/media`, download: '' }, icon('download'), 'Download audio'),
    iconBtn('subtitles', 'Auto-scroll transcript', (e) => { follow = !follow; e.currentTarget.classList.toggle('active', follow); }, 'active'),
    h('span', { class: 'grow' }));
  host.replaceChildren(h('div', {},
    playerControls(audio, { title: art.title, sub, duration: c.duration, onTime }),
    c.summary ? h('p', { class: 'lead', style: { margin: '14px 4px 0' } }, c.summary) : null,
    h('div', { class: 'section-title' }, 'Transcript'),
    h('div', { class: 'transcript' }, ...lines)));
  if (ctx.autoplay) audio.play().catch(() => {});
  return () => { audio.pause(); audio.src = ''; };
}
