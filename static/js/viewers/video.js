// Video Overview: narrated lesson. Scenes (title + bullets) animate in sync
// with the narration audio; captions, scene list, speed and full screen.

import { h, icon, iconBtn, fmtTime } from '../lib/dom.js';
import { playerControls } from './audio.js';

export function render(host, art, ctx) {
  const c = art.content;
  const scenes = c.scenes || [];
  const audio = new Audio(`/api/artifacts/${art.id}/media`);
  audio.preload = 'metadata';
  let captions = true;
  let current = -1;

  const stage = h('div', { class: 'video-stage' },
    h('div', { class: 'vs-orb', style: { width: '40cqw', height: '40cqw', right: '-10cqw', top: '-14cqw', background: 'radial-gradient(circle, rgba(139,108,255,.45), transparent 65%)' } }),
    h('div', { class: 'vs-orb', style: { width: '34cqw', height: '34cqw', left: '-12cqw', bottom: '-16cqw', background: 'radial-gradient(circle, rgba(79,141,255,.4), transparent 65%)', animationDelay: '-6s' } }));
  const sceneEls = scenes.map((s, i) => {
    const el = h('div', { class: 'vs-scene' },
      h('div', { class: 'vs-visual' }, s.visual || ''),
      h('h2', {}, s.title),
      h('ul', {}, ...s.bullets.map((b, j) => h('li', { style: { transitionDelay: `${0.25 + j * 0.35}s` } }, b))));
    el.dataset.index = String(i);
    stage.append(el);
    return el;
  });
  const caption = h('div', { class: 'vs-caption' });
  const progress = h('div', { class: 'vs-progress', style: { width: '0%' } });
  stage.append(caption, progress);
  stage.addEventListener('click', () => { if (audio.paused) audio.play().catch(() => {}); else audio.pause(); });

  const list = h('div', { class: 'transcript' }, ...scenes.map((s, i) => h('div', {
    class: 't-line', role: 'button', tabindex: '0', onclick: () => { audio.currentTime = s.start || 0; audio.play().catch(() => {}); },
  }, h('span', { class: 'tm' }, fmtTime(s.start)), h('span', {}, h('b', { style: { color: '#e3e3e3', fontWeight: '500' } }, s.title), ' — ', s.narration))));

  function show(i) {
    if (i === current) return;
    current = i;
    sceneEls.forEach((el, j) => el.classList.toggle('on', j === i));
    [...list.children].forEach((el, j) => el.classList.toggle('current', j === i));
  }

  function onTime(t) {
    let idx = 0;
    for (let i = 0; i < scenes.length; i += 1) if ((scenes[i].start || 0) <= t + 0.1) idx = i;
    show(idx);
    const s = scenes[idx];
    if (captions && s) {
      // Show the sentence being spoken, estimated by time within the scene.
      const sentences = s.narration.match(/[^.!?।]+[.!?।]*/g) || [s.narration];
      const within = Math.max(0, t - (s.start || 0));
      const frac = Math.min(0.999, within / Math.max(0.5, s.duration || 1));
      const totalChars = s.narration.length;
      let acc = 0;
      let text = sentences[0];
      for (const sentence of sentences) {
        acc += sentence.length;
        if (acc / totalChars >= frac) { text = sentence; break; }
      }
      caption.textContent = text.trim();
    } else caption.textContent = '';
    progress.style.width = `${Math.min(100, (100 * t) / (c.duration || audio.duration || 1))}%`;
  }

  ctx.toolbar.replaceChildren(
    iconBtn('skip_previous', 'Previous scene', () => { const s = scenes[Math.max(0, current - 1)]; audio.currentTime = s.start || 0; }),
    iconBtn('skip_next', 'Next scene', () => { const s = scenes[Math.min(scenes.length - 1, current + 1)]; audio.currentTime = s.start || 0; }),
    iconBtn('closed_caption', 'Captions', (e) => { captions = !captions; e.currentTarget.classList.toggle('active', captions); onTime(audio.currentTime); }, 'active'),
    iconBtn('fullscreen', 'Full screen', () => stage.requestFullscreen?.().catch(() => {})),
    h('a', { class: 'btn btn-tonal small', href: `/api/artifacts/${art.id}/media`, download: '' }, icon('download'), 'Narration audio'),
    h('span', { class: 'grow' }));

  host.replaceChildren(h('div', { style: { display: 'flex', flexDirection: 'column', gap: '14px' } },
    stage,
    playerControls(audio, { title: art.title, sub: `${scenes.length} scenes · ${c.language || 'English'}`, duration: c.duration, onTime }),
    h('div', { class: 'section-title', style: { margin: '6px 0 0' } }, 'Scenes'),
    list));
  show(0);
  onTime(0);
  if (ctx.autoplay) audio.play().catch(() => {});
  return () => { audio.pause(); audio.src = ''; };
}
