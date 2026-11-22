/* Calm: drobne narzędzia interfejsu dla stron Alfred i Zarabiaj: arkusz (dolny na telefonie, boczny na komputerze), powiadomienie z „Cofnij”,
   licznik z animacją, konfetti i pierścień postępu. Bez zależności; animacje wyłączają się przy ustawieniu systemu „ogranicz ruch”. */
(() => {
'use strict';
const REDUCED = !!(window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches);
const h = (tag, cls, txt) => { const e = document.createElement(tag); if (cls) e.className = cls; if (txt != null) e.textContent = txt; return e; };
const Calm = {reduced: REDUCED};

/* ---------------------------------------------------------------- arkusz: Calm.sheet({title, body, onClose}) -> {el, body, close} */
let cur = null;
Calm.sheet = (o = {}) => {
  if (cur) cur.close(true);
  const back = h('div', 'sheet-back'), sh = h('aside', 'sheet'), grip = h('div', 'sheet-grip'), head = h('header', 'sheet-h'), title = h('h3', '', o.title || ''), x = h('button', 'sheet-x', '×'), body = h('div', 'sheet-b');
  const tid = 'sheet-t' + Date.now(); title.id = tid; sh.setAttribute('role', 'dialog'); sh.setAttribute('aria-modal', 'true'); sh.setAttribute('aria-labelledby', tid);
  x.type = 'button'; x.setAttribute('aria-label', 'Zamknij'); head.append(title, x); sh.append(grip, head, body);
  if (o.body) body.append(o.body.nodeType ? o.body : document.createTextNode(String(o.body)));
  document.body.append(back, sh);
  const prev = document.activeElement, main = document.querySelector('main');
  if (main) main.setAttribute('inert', '');
  document.documentElement.classList.add('sheet-open');
  let closed = false;
  const close = quick => {
    if (closed) return; closed = true; cur = null; document.removeEventListener('keydown', onKey);
    back.classList.remove('on'); sh.classList.remove('on'); if (main) main.removeAttribute('inert'); document.documentElement.classList.remove('sheet-open');
    const end = () => { back.remove(); sh.remove(); };
    if (quick || REDUCED) end(); else setTimeout(end, 360);
    if (prev && prev.focus && document.contains(prev)) { try { prev.focus({preventScroll: true}); } catch (e) { /* brak */ } }
    if (o.onClose) o.onClose();
  };
  const onKey = e => { if (e.key === 'Escape') close(); };
  document.addEventListener('keydown', onKey); back.addEventListener('click', () => close()); x.addEventListener('click', () => close());
  // przeciągnięcie w dół zamyka arkusz na telefonie
  let y0 = null, dy = 0;
  const start = e => { y0 = e.touches[0].clientY; dy = 0; sh.style.transition = 'none'; };
  const move = e => { if (y0 == null) return; dy = Math.max(0, e.touches[0].clientY - y0); if (window.innerWidth <= 820) sh.style.transform = `translateY(${dy}px)`; };
  const end = () => { if (y0 == null) return; y0 = null; sh.style.transition = ''; sh.style.transform = ''; if (dy > 90 && window.innerWidth <= 820) close(); };
  [grip, head].forEach(el => { el.addEventListener('touchstart', start, {passive: true}); el.addEventListener('touchmove', move, {passive: true}); el.addEventListener('touchend', end); });
  requestAnimationFrame(() => { back.classList.add('on'); sh.classList.add('on'); const f = sh.querySelector('input,textarea,select,button:not(.sheet-x)') || x; try { f.focus({preventScroll: true}); } catch (e) { /* brak */ } });
  cur = {el: sh, body, close, setTitle: t => { title.textContent = t; }};
  return cur;
};
Calm.closeSheet = () => { if (cur) cur.close(); };

/* ---------------------------------------------------------------- powiadomienie z opcjonalnym „Cofnij” */
let toastT = 0;
Calm.toast = (msg, o = {}) => {
  document.querySelectorAll('.c-toast').forEach(t => t.remove()); clearTimeout(toastT);
  const t = h('div', 'c-toast'); t.setAttribute('role', 'status'); t.append(h('span', '', msg));
  if (o.action) { const b = h('button', '', o.action.label); b.type = 'button'; b.addEventListener('click', () => { t.remove(); o.action.fn(); }); t.append(b); }
  document.body.append(t); toastT = setTimeout(() => t.remove(), o.ms || (o.action ? 7000 : 4500));
  return t;
};

/* ---------------------------------------------------------------- licznik: liczba narasta od poprzedniej wartości */
Calm.countUp = (node, to, o = {}) => {
  const fmt = o.fmt || (v => String(Math.round(v)));
  if (typeof to !== 'number' || !isFinite(to)) { node.textContent = o.fallback || 'n/d'; return; }
  const from = node._v != null ? node._v : (o.from || 0); node._v = to;
  if (REDUCED || from === to) { node.textContent = fmt(to); return; }
  const t0 = performance.now(), ms = o.ms || 800;
  const step = t => { const p = Math.min(1, (t - t0) / ms), e = 1 - Math.pow(1 - p, 4); node.textContent = fmt(from + (to - from) * e); if (p < 1) requestAnimationFrame(step); else node.textContent = fmt(to); };
  requestAnimationFrame(step);
};

/* ---------------------------------------------------------------- konfetti z punktu (np. po zrobionym zadaniu) */
Calm.confetti = (x, y, n = 22) => {
  if (REDUCED) return;
  const colors = ['#F2C46D', '#4ADE80', '#60A5FA', '#F472B6', '#A78BFA', '#FB923C'];
  for (let i = 0; i < n; i++) {
    const p = h('i', 'c-conf'); p.style.left = x + 'px'; p.style.top = y + 'px'; p.style.background = colors[i % colors.length]; p.style.width = (5 + Math.random() * 5) + 'px'; p.style.height = (7 + Math.random() * 6) + 'px';
    document.body.append(p);
    const a = Math.random() * Math.PI * 2, d = 60 + Math.random() * 120, dx = Math.cos(a) * d, dy = Math.sin(a) * d - 70;
    const an = p.animate([{transform: 'translate(0,0) rotate(0)', opacity: 1}, {transform: `translate(${dx}px,${dy}px) rotate(${Math.random() * 540 - 270}deg)`, opacity: 1, offset: .6}, {transform: `translate(${dx * 1.1}px,${dy + 150}px) rotate(${Math.random() * 720}deg)`, opacity: 0}], {duration: 900 + Math.random() * 500, easing: 'cubic-bezier(.2,.7,.4,1)'});
    an.onfinish = () => p.remove();
  }
};
Calm.confettiAt = el => { const r = el.getBoundingClientRect(); Calm.confetti(r.left + r.width / 2, r.top + r.height / 2); };

/* ---------------------------------------------------------------- pierścień postępu (SVG): ring.set(0..1) */
Calm.ring = (size = 64, stroke = 6) => {
  const NS = 'http://www.w3.org/2000/svg', r = (size - stroke) / 2, c = 2 * Math.PI * r;
  const svg = document.createElementNS(NS, 'svg'); svg.setAttribute('viewBox', `0 0 ${size} ${size}`); svg.setAttribute('width', size); svg.setAttribute('height', size); svg.setAttribute('class', 'c-ring'); svg.setAttribute('aria-hidden', 'true');
  const mk = (cls) => { const e = document.createElementNS(NS, 'circle'); e.setAttribute('cx', size / 2); e.setAttribute('cy', size / 2); e.setAttribute('r', r); e.setAttribute('fill', 'none'); e.setAttribute('stroke-width', stroke); e.setAttribute('class', cls); return e; };
  const bg = mk('rb'), fg = mk('rf'); fg.setAttribute('stroke-dasharray', c); fg.setAttribute('stroke-dashoffset', c); fg.setAttribute('stroke-linecap', 'round'); fg.setAttribute('transform', `rotate(-90 ${size / 2} ${size / 2})`);
  svg.append(bg, fg);
  return {el: svg, set(p) { fg.setAttribute('stroke-dashoffset', (c * (1 - Math.max(0, Math.min(1, p)))).toFixed(2)); }};
};

window.Calm = Calm;
})();
