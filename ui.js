/* Wspólna warstwa wizualna: ikony SVG, liczniki, wykresy (sparkline, miernik, drabinka poziomów), powiadomienia i animacje wejścia. Bez zależności. */
(function () {
'use strict';
const NS = 'http://www.w3.org/2000/svg';
const REDUCED = !!(window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches);
const C = (x, y, r) => `M${x - r} ${y}a${r} ${r} 0 1 0 ${2 * r} 0a${r} ${r} 0 1 0 ${-2 * r} 0`;
const PATHS = {
  up: 'M3 17l6-6 4 4 8-8M15 7h6v6', down: 'M3 7l6 6 4-4 8 8M15 17h6v-6', target: C(12, 12, 9) + C(12, 12, 5) + C(12, 12, 1),
  shield: 'M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z', bitcoin: 'M9 5h5.5a3 3 0 0 1 0 6H9M9 11h6.5a3.5 3.5 0 0 1 0 7H9M9 5v13M11 3v2M14 3v2M11 18v3M14 18v3',
  gold: 'M2 19l3-8h14l3 8zM8 11l1.5-4h5L16 11M9 15h6', currency: 'M4 8h14l-3-3M20 16H6l3 3', building: 'M4 20V10l5-3v13M9 20V4l7 3v13M3 20h18M12 10h1M12 14h1M12 17h1',
  oil: 'M12 3s6 6.5 6 11a6 6 0 0 1-12 0c0-4.5 6-11 6-11z', flame: 'M12 3c1 3.5 5 5.5 5 10a5 5 0 0 1-10 0c0-2.5 1.5-3.5 2.5-5 .5 2 1.5 2.5 2.5 2.5 0-3-1.5-5 0-7.5z',
  clock: C(12, 12, 9) + 'M12 7v5l3 2', refresh: 'M20 11a8 8 0 1 0-2.3 5.7M20 4v6h-6', search: C(11, 11, 7) + 'M21 21l-5-5', alert: 'M12 3l10 18H2zM12 10v5M12 18h.01',
  check: C(12, 12, 9) + 'M8 12l3 3 5-6', info: C(12, 12, 9) + 'M12 11v5M12 8h.01', activity: 'M3 12h4l3-8 4 16 3-8h4', bars: 'M5 20V10M12 20V4M19 20v-7',
  layers: 'M12 3l9 5-9 5-9-5zM3 13l9 5 9-5', calendar: 'M4 6h16v14H4zM4 10h16M8 3v4M16 3v4', zap: 'M13 2L4 14h7l-1 8 9-12h-7z',
  eye: 'M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z' + C(12, 12, 3), pause: 'M8 5v14M16 5v14', candles: 'M7 4v3M7 17v3M5 7h4v10H5zM17 2v4M17 16v4M15 6h4v10h-4z',
  wallet: 'M3 7h16a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2zM3 7l12-3v3M17 13.5h2', globe: C(12, 12, 9) + 'M3 12h18M12 3c3.5 3.5 3.5 14.5 0 18M12 3c-3.5 3.5-3.5 14.5 0 18',
  sparkles: 'M12 3l2 5 5 2-5 2-2 5-2-5-5-2 5-2zM19 15l.8 2 2 .8-2 .8-.8 2-.8-2-2-.8 2-.8z', knife: 'M5 19L19 5l-1 8-8 6zM4 20l3-3', bell: 'M6 16v-5a6 6 0 0 1 12 0v5l2 2H4zM10 21h4',
  home: 'M3 11l9-8 9 8M5 10v10h14V10', moon: 'M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z', sun: C(12, 12, 4) + 'M12 2v2M12 20v2M2 12h2M20 12h2M5 5l1.5 1.5M17.5 17.5L19 19M5 19l1.5-1.5M17.5 6.5L19 5', contrast: C(12, 12, 9) + 'M12 3v18', coins: C(9, 9, 6) + 'M15 9.5a6 6 0 1 1-5.5 8.5M9 7v4'
};
const el = (tag, attrs, ...kids) => { const e = document.createElementNS(NS, tag); for (const [k, v] of Object.entries(attrs || {})) if (v != null) e.setAttribute(k, v); kids.forEach(c => c && e.append(c)); return e; };
const html = (tag, cls, ...kids) => { const e = document.createElement(tag); if (cls) e.className = cls; kids.forEach(c => c != null && e.append(c.nodeType ? c : document.createTextNode(String(c)))); return e; };
const nfc = {};
const nf = (v, d = 2) => (nfc[d] = nfc[d] || new Intl.NumberFormat('pl-PL', {minimumFractionDigits: d, maximumFractionDigits: d})).format(v);

function icon(name, extra) { const s = el('svg', {viewBox: '0 0 24 24', 'aria-hidden': 'true', class: 'ui-ico' + (extra ? ' ' + extra : '')}); s.append(el('path', {d: PATHS[name] || PATHS.candles})); return s; }
function badge(name, tone, small) { const b = html('span', 'ui-badge' + (tone ? ' ' + tone : '') + (small ? ' sm' : '')); b.append(icon(name)); return b; }
function logo() {
  const s = el('svg', {viewBox: '0 0 48 48', class: 'ui-logo', 'aria-hidden': 'true'});
  s.append(el('rect', {x: 1, y: 1, width: 46, height: 46, rx: 12, fill: 'var(--card,#fff)', stroke: 'var(--line,#DCE2E9)'}));
  const g = (cls, x, y, h, col) => el('g', {class: cls}, el('line', {x1: x + 3, x2: x + 3, y1: y - 4, y2: y + h + 4, stroke: col, 'stroke-width': 1.6, 'stroke-linecap': 'round'}), el('rect', {x, y, width: 6, height: h, rx: 1.5, fill: col}));
  s.append(g('c1', 10, 24, 12, 'var(--bear,#C1334A)'), g('c2', 21, 16, 16, 'var(--bull,#12805A)'), g('c3', 32, 11, 13, 'var(--bull,#12805A)'));
  s.append(el('path', {d: 'M9 38l9-7 7 3 14-14', fill: 'none', stroke: 'var(--accent,#E8830C)', 'stroke-width': 2.2, 'stroke-linecap': 'round', 'stroke-linejoin': 'round'}));
  return s;
}

/* ---- liczniki ---- */
function countTo(node, to, o = {}) {
  const d = o.decimals ?? 0, fmt = v => (o.prefix || '') + nf(v, d) + (o.suffix || '');
  if (!(typeof to === 'number') || !isFinite(to)) { node.textContent = o.fallback || 'n/d'; return; }
  const from = node._v ?? (o.from ?? 0);
  node._v = to;
  if (REDUCED || from === to) { node.textContent = fmt(to); return; }
  const t0 = performance.now(), ms = o.ms || 900;
  const step = t => { const p = Math.min(1, (t - t0) / ms), e = 1 - Math.pow(1 - p, 4); node.textContent = fmt(from + (to - from) * e); if (p < 1) requestAnimationFrame(step); else node.textContent = fmt(to); };
  requestAnimationFrame(step);
}

/* ---- sparkline ---- */
function pathLen(pts) { let L = 0; for (let i = 1; i < pts.length; i++) L += Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]); return L; }
function spark(values, o = {}) {
  const w = o.w || 84, h = o.h || 26, v = (values || []).filter(x => typeof x === 'number' && isFinite(x));
  const s = el('svg', {viewBox: `0 0 ${w} ${h}`, width: w, height: h, class: 'ui-spark', 'aria-hidden': 'true'});
  if (v.length < 3) return s;
  const mn = Math.min(...v), mx = Math.max(...v), rg = mx - mn || 1, pad = 3;
  const pts = v.map((x, i) => [pad + i / (v.length - 1) * (w - 2 * pad), h - pad - (x - mn) / rg * (h - 2 * pad)]);
  const col = o.color || (v[v.length - 1] >= v[0] ? 'var(--bull,#12805A)' : 'var(--bear,#C1334A)');
  const dl = 'M' + pts.map(p => p[0].toFixed(1) + ' ' + p[1].toFixed(1)).join('L');
  s.append(el('path', {d: dl + `L${pts[pts.length - 1][0].toFixed(1)} ${h}L${pts[0][0].toFixed(1)} ${h}z`, class: 'ar', fill: col, 'fill-opacity': 0.13}));
  const ln = el('path', {d: dl, class: 'ln' + (o.animate ? ' ui-draw' : ''), stroke: col});
  if (o.animate) ln.style.setProperty('--len', String(Math.ceil(pathLen(pts)) + 4));
  s.append(ln, el('circle', {cx: pts[pts.length - 1][0], cy: pts[pts.length - 1][1], r: 2.2, fill: col}));
  return s;
}

/* ---- miernik półokrągły ---- */
function gauge(value, o = {}) {
  const size = o.size || 130, L = Math.PI * 50, v = Math.max(0, Math.min(100, value || 0));
  const col = o.color || (v >= (o.good ?? 65) ? 'var(--bull,#12805A)' : v >= (o.mid ?? 45) ? 'var(--accent,#E8830C)' : 'var(--bear,#C1334A)');
  const s = el('svg', {viewBox: '0 0 120 76', width: size, height: size * 76 / 120, class: 'ui-gauge', role: 'img', 'aria-label': (o.label || 'wynik') + ' ' + Math.round(v) + ' na 100'});
  const d = 'M10 66A50 50 0 0 1 110 66';
  s.append(el('path', {d, class: 'tr', fill: 'none', 'stroke-width': 10, 'stroke-linecap': 'round'}));
  const fg = el('path', {d, class: 'fg', fill: 'none', stroke: col, 'stroke-width': 10, 'stroke-linecap': 'round', 'stroke-dasharray': L, 'stroke-dashoffset': REDUCED ? L * (1 - v / 100) : L});
  const t = el('text', {x: 60, y: 60, 'text-anchor': 'middle', 'font-size': 24}); t.textContent = '0';
  const sub = el('text', {x: 60, y: 73, 'text-anchor': 'middle', 'font-size': 9.5}); sub.textContent = o.label || 'wynik';
  s.append(fg, t, sub);
  requestAnimationFrame(() => requestAnimationFrame(() => { fg.setAttribute('stroke-dashoffset', L * (1 - v / 100)); countTo(t, Math.round(v), {ms: 1000}); }));
  if (REDUCED) t.textContent = String(Math.round(v));
  return s;
}
function bar(frac, tone) { const b = html('div', 'ui-bar'), i = html('i', tone || ''); b.append(i); requestAnimationFrame(() => requestAnimationFrame(() => { i.style.width = Math.max(0, Math.min(1, frac)) * 100 + '%'; })); if (REDUCED) i.style.width = Math.max(0, Math.min(1, frac)) * 100 + '%'; return b; }
function lean(up, down) { const t = (up + down) || 1, w = html('div', 'ui-lean'), u = html('i', 'u'), d = html('i', 'd'); w.append(u, d); requestAnimationFrame(() => requestAnimationFrame(() => { u.style.width = up / t * 100 + '%'; d.style.width = down / t * 100 + '%'; })); if (REDUCED) { u.style.width = up / t * 100 + '%'; d.style.width = down / t * 100 + '%'; } return w; }

/* ---- drabinka poziomów: historia ceny + strefa wejścia, stop i cele ---- */
function ladder(closes, plan, price, o = {}) {
  const px = o.px || (x => nf(x, x >= 100 ? 1 : x >= 1 ? 3 : 5));
  const W = 880, H = 300, xL = 12, xH = 520, xR = 650, yT = 16, yB = 280;
  const s = el('svg', {viewBox: `0 0 ${W} ${H}`, class: 'ui-chart', role: 'img', 'aria-label': 'Wykres ceny z poziomami wejścia, stopu i celów'});
  const hist = (closes || []).filter(x => isFinite(x));
  const lv = [plan.stop, plan.entry_lo, plan.entry_hi, plan.tp1, plan.tp2, plan.tp3].filter(x => isFinite(x));
  const lo0 = Math.min(...hist, ...lv), hi0 = Math.max(...hist, ...lv), pad = (hi0 - lo0) * 0.06 || 1, lo = lo0 - pad, hi = hi0 + pad;
  const y = v => yB - (v - lo) / (hi - lo) * (yB - yT);
  for (let i = 0; i <= 4; i++) { const yy = yT + i * (yB - yT) / 4; s.append(el('line', {x1: xL, x2: xR, y1: yy, y2: yy, class: 'grid'})); const tx = el('text', {x: xL + 2, y: yy - 3}); tx.textContent = px(hi - i * (hi - lo) / 4); s.append(tx); }
  const pts = hist.map((v, i) => [xL + 46 + i / Math.max(1, hist.length - 1) * (xH - xL - 46), y(v)]);
  const dl = 'M' + pts.map(p => p[0].toFixed(1) + ' ' + p[1].toFixed(1)).join('L');
  s.append(el('path', {d: dl + `L${xH} ${yB}L${pts[0][0].toFixed(1)} ${yB}z`, fill: 'var(--accent,#E8830C)', 'fill-opacity': 0.09, class: o.noAnim ? '' : 'ui-fadein'}));
  const ln = el('path', {d: dl, fill: 'none', stroke: 'var(--accent,#E8830C)', 'stroke-width': 2.1, 'stroke-linejoin': 'round', 'stroke-linecap': 'round', class: o.noAnim ? '' : 'ui-draw'});
  ln.style.setProperty('--len', String(Math.ceil(pathLen(pts)) + 6)); s.append(ln);
  const A = (n, d0) => o.noAnim ? '' : ' ui-fadein';
  let k = 0; const delay = () => (0.5 + 0.12 * k++) + 's';
  const band = el('rect', {x: xH, width: xR - xH, y: y(plan.entry_hi), height: Math.max(3, y(plan.entry_lo) - y(plan.entry_hi)), fill: 'var(--accent,#E8830C)', 'fill-opacity': 0.22, class: o.noAnim ? '' : 'ui-fadein'});
  band.style.animationDelay = delay(); s.append(band);
  const labels = [];
  const line = (v, col, text, dash) => { const ln2 = el('line', {x1: xH, x2: xR, y1: y(v), y2: y(v), stroke: col, 'stroke-width': 1.7, 'stroke-dasharray': dash || null, class: o.noAnim ? '' : 'ui-fadein'}); ln2.style.animationDelay = delay(); s.append(ln2); labels.push({y: y(v), col, text}); };
  line(plan.tp3, 'var(--bull,#12805A)', 'Cel 3 ' + px(plan.tp3), '2 4'); line(plan.tp2, 'var(--bull,#12805A)', 'Cel 2 ' + px(plan.tp2), '5 4'); line(plan.tp1, 'var(--bull,#12805A)', 'Cel 1 ' + px(plan.tp1));
  line(plan.entry_hi, 'var(--accent,#E8830C)', 'Wejście ' + px(plan.entry_lo) + '–' + px(plan.entry_hi), '3 3'); line(plan.stop, 'var(--bear,#C1334A)', 'Stop ' + px(plan.stop));
  labels.sort((a, b) => a.y - b.y); for (let i = 1; i < labels.length; i++) if (labels[i].y - labels[i - 1].y < 14) labels[i].y = labels[i - 1].y + 14;
  labels.forEach((l, i) => { const t = el('text', {x: xR + 8, y: l.y + 4, class: 'lbl' + (o.noAnim ? '' : ' ui-fadein'), fill: l.col, style: 'fill:' + l.col}); t.textContent = l.text; if (!o.noAnim) t.style.animationDelay = (0.5 + 0.12 * i) + 's'; s.append(t); });
  const lx = pts[pts.length - 1][0], ly = y(price ?? hist[hist.length - 1]);
  s.append(el('circle', {cx: lx, cy: ly, r: 6, class: 'ui-dot-ring'}), el('circle', {cx: lx, cy: ly, r: 5, class: 'ui-dot'}));
  const now = el('text', {x: lx - 4, y: ly - 11, 'text-anchor': 'end', class: 'lbl'}); now.textContent = 'teraz ' + px(price ?? hist[hist.length - 1]); s.append(now);
  return s;
}

/* ---- powiadomienia ---- */
function toast(msg, o = {}) {
  let box = document.querySelector('.ui-toasts');
  if (!box) { box = html('div', 'ui-toasts'); box.setAttribute('aria-live', 'polite'); document.body.append(box); }
  const t = html('div', 'ui-toast' + (o.kind ? ' ' + o.kind : ''));
  const txt = html('div'); if (o.title) txt.append(html('b', '', o.title)); txt.append(String(msg));
  t.append(icon(o.icon || 'bell'), txt); box.append(t);
  setTimeout(() => { t.classList.add('out'); setTimeout(() => t.remove(), 400); }, o.ms || 7000);
  while (box.children.length > 4) box.firstChild.remove();
}

/* ---- wejście elementów i automatyczne ikony nagłówków ---- */
let io = null;
function reveal(node, i) {
  node.classList.add('ui-reveal'); node.style.setProperty('--i', String(Math.min(i || 0, 9)));
  if (!('IntersectionObserver' in window) || REDUCED) { node.classList.add('in'); return; }
  io = io || new IntersectionObserver(es => es.forEach(e => { if (e.isIntersecting) { e.target.classList.add('in'); io.unobserve(e.target); } }), {threshold: 0.04, rootMargin: '0px 0px -4% 0px'});
  io.observe(node);
}
const ICONS = [[/złoto|surowc|platyn|srebr/i, 'gold'], [/waluty|kursy|fx|dolar/i, 'currency'], [/spółk|ranking|akcj/i, 'building'], [/bitcoin|krypto/i, 'bitcoin'], [/gdzie kupić|poziom|plan/i, 'target'],
  [/ile czekać|czas/i, 'clock'], [/wielkość pozycji/i, 'wallet'], [/pozycj/i, 'target'], [/dlaczego|uwagi|metodolog/i, 'info'], [/scenariusz/i, 'layers'], [/kontekst/i, 'globe'],
  [/korekt|skuteczn|uczy/i, 'sparkles'], [/zdarzen|sezon|kalendarz/i, 'calendar'], [/test|walk|zależnośc|sytuacj/i, 'activity'], [/zasoby|metryk/i, 'bars'], [/sygnał|wskaźnik|rynek/i, 'zap'], [/pochodn|opcje/i, 'candles']];
function pickIcon(text) { for (const [re, n] of ICONS) if (re.test(text)) return n; return 'bars'; }
function enhance(root) {
  (root || document).querySelectorAll('section.card, div.card').forEach((c, i) => {
    if (c.dataset.ui) return;
    c.dataset.ui = '1';
    const h = c.querySelector(':scope > h2');
    if (h && !h.querySelector('.ui-badge')) h.prepend(badge(pickIcon(h.textContent)));
    reveal(c, i);
  });
}
function auto() {
  let t = 0;
  const run = () => { t = 0; enhance(document); };
  new MutationObserver(() => { if (!t) t = setTimeout(run, 40); }).observe(document.body, {childList: true, subtree: true});
  run();
}

/* ---- nawigacja: jeden pasek na wszystkich stronach, menu rozwijane, motyw, zwijanie sekcji ---- */
const NAV = [
  {href: 'index.html', label: 'Panel', hint: 'Bitcoin na żywo'},
  {label: 'Analiza', items: [
    {href: 'rynki.html', label: 'Rynki', hint: 'Waluty, złoto, surowce, spółki'},
    {href: 'sytuacje.html', label: 'Sytuacje', hint: 'Co wpływa na kierunek ruchu'},
    {href: 'badania.html', label: 'Badania', hint: 'Statystyka i testy na historii'}]},
  {label: 'Handel', items: [
    {href: 'okazje.html', label: 'Okazje', hint: 'Skaner okazji i raport pozycji'},
    {href: 'strategie.html', label: 'Strategie', hint: 'Portfele od ochronnego do agresywnego'},
    {href: 'bot.html', label: 'Bot', hint: 'Automat: status, statystyki, ustawienia'},
    {href: 'produkty.html', label: 'Produkty', hint: 'Oferty w okolicy i kalkulator odsprzedaży'}]},
  {href: 'bybit.html', label: 'Mój Bybit', hint: 'Saldo, pozycje i statystyki Twojego konta'},
  {href: 'kalkulator.html', label: 'Kalkulator', hint: 'Wielkość pozycji i ryzyko'},
];
const THEMES = [['auto', 'Auto'], ['light', 'Jasny'], ['dark', 'Ciemny']];
const sget = k => { try { return localStorage.getItem(k); } catch (e) { return null; } };
const sset = (k, v) => { try { if (v == null) localStorage.removeItem(k); else localStorage.setItem(k, v); } catch (e) { /* tryb prywatny */ } };
function here() { const p = location.pathname.split('/').pop() || 'index.html'; return p === '' ? 'index.html' : p; }
function applyTheme(t) { if (t === 'light' || t === 'dark') document.documentElement.setAttribute('data-theme', t); else document.documentElement.setAttribute('data-theme', 'auto'); }
applyTheme(sget('btc-theme'));
function navbar() {
  if (document.querySelector('.ui-nav')) return;
  const cur = here(), nav = html('nav', 'ui-nav'); nav.setAttribute('aria-label', 'Główna nawigacja');
  const inner = html('div', 'ui-nav-in'), brand = html('a', 'ui-brand'); brand.href = 'index.html'; brand.append(logo(), html('span', '', 'BTC Analiza'));
  const burger = html('button', 'ui-burger'); burger.type = 'button'; burger.setAttribute('aria-label', 'Menu'); burger.setAttribute('aria-expanded', 'false');
  burger.append(el('svg', {viewBox: '0 0 24 24', class: 'ui-ico'}, el('path', {d: 'M4 7h16M4 12h16M4 17h16'})));
  const tabs = html('div', 'ui-tabs');
  const closeAll = except => tabs.querySelectorAll('.ui-grp.open').forEach(g => { if (g !== except) { g.classList.remove('open'); g.firstChild.setAttribute('aria-expanded', 'false'); } });
  for (const t of NAV) {
    if (t.href) { const a = html('a', 'ui-tab' + (cur === t.href ? ' on' : ''), t.label); a.href = t.href; if (cur === t.href) a.setAttribute('aria-current', 'page'); tabs.append(a); continue; }
    const g = html('div', 'ui-grp' + (t.items.some(i => i.href === cur) ? ' on' : '')), b = html('button', 'ui-tab ui-tab-btn', t.label);
    b.type = 'button'; b.setAttribute('aria-haspopup', 'true'); b.setAttribute('aria-expanded', 'false'); b.append(el('svg', {viewBox: '0 0 24 24', class: 'ui-ico ui-caret'}, el('path', {d: 'M6 9l6 6 6-6'})));
    const menu = html('div', 'ui-menu'); menu.setAttribute('role', 'menu');
    for (const it of t.items) { const a = html('a', 'ui-item' + (cur === it.href ? ' on' : '')); a.href = it.href; a.setAttribute('role', 'menuitem'); a.append(html('b', '', it.label), html('small', '', it.hint)); menu.append(a); }
    b.addEventListener('click', e => { e.stopPropagation(); const open = !g.classList.contains('open'); closeAll(g); g.classList.toggle('open', open); b.setAttribute('aria-expanded', String(open)); });
    g.append(b, menu); tabs.append(g);
  }
  const right = html('div', 'ui-right');
  const fold = html('button', 'ui-ibtn'); fold.type = 'button'; fold.title = 'Zwiń lub rozwiń wszystkie sekcje'; fold.setAttribute('aria-label', 'Zwiń lub rozwiń wszystkie sekcje'); fold.append(icon('layers'));
  fold.addEventListener('click', () => { const cards = [...document.querySelectorAll('.card.foldable')]; const anyOpen = cards.some(c => !c.classList.contains('collapsed')); cards.forEach(c => setFold(c, anyOpen)); });
  const theme = html('button', 'ui-ibtn'); theme.type = 'button'; theme.setAttribute('aria-label', 'Zmień motyw');
  const label = () => { const t = sget('btc-theme') || 'auto'; theme.title = 'Motyw: ' + (THEMES.find(x => x[0] === t) || THEMES[0])[1]; theme.replaceChildren(icon(t === 'dark' ? 'moon' : t === 'light' ? 'sun' : 'contrast')); };
  theme.addEventListener('click', () => { const t = sget('btc-theme') || 'auto', nx = THEMES[(THEMES.findIndex(x => x[0] === t) + 1) % 3][0]; sset('btc-theme', nx === 'auto' ? null : nx); applyTheme(nx); label(); });
  label(); right.append(fold, theme);
  inner.append(brand, tabs, right, burger); nav.append(inner);
  burger.addEventListener('click', () => { const o = nav.classList.toggle('open'); burger.setAttribute('aria-expanded', String(o)); });
  document.addEventListener('click', e => { if (!nav.contains(e.target)) { closeAll(null); } });
  document.addEventListener('keydown', e => { if (e.key === 'Escape') { closeAll(null); nav.classList.remove('open'); } });
  document.body.prepend(nav); document.body.classList.add('ui-has-nav');
}
/* strony miały własne przyciski przejść w nagłówkach: zastępuje je pasek nawigacji */
function tidyHeaders() {
  document.querySelectorAll('.wrap header a.btn[href$=".html"]').forEach(a => a.remove());
  document.querySelectorAll('.wrap header > div').forEach(d => { if (!d.children.length && !d.textContent.trim()) d.remove(); });
  const close = document.getElementById('close'); if (close && !window.opener) close.remove();
}
/* zwijane sekcje: klik w nagłówek karty (h2), stan zapamiętany osobno dla każdej strony */
const CHEV = () => el('svg', {viewBox: '0 0 24 24', class: 'ui-ico fold-chev', 'aria-hidden': 'true'}, el('path', {d: 'M6 9l6 6 6-6'}));
const foldKey = h => 'ui-fold:' + here() + ':' + h.textContent.trim().slice(0, 60);
function setFold(card, collapsed, save = true) {
  card.classList.toggle('collapsed', collapsed);
  const h = card.querySelector(':scope > h2'); if (!h) return;
  h.setAttribute('aria-expanded', String(!collapsed));
  if (save) sset(foldKey(h), collapsed ? '1' : '0');
}
function foldScan() {
  document.querySelectorAll('.card').forEach(card => {
    const h = card.querySelector(':scope > h2'); if (!h) return;
    card.classList.add('foldable');
    if (!h.querySelector('.fold-chev')) {
      h.append(CHEV()); h.setAttribute('role', 'button'); h.setAttribute('tabindex', '0');
      setFold(card, sget(foldKey(h)) === '1' || card.dataset.fold === 'closed' && sget(foldKey(h)) !== '0', false);
    }
  });
}
function folding() {
  let t = 0; new MutationObserver(() => { if (!t) t = setTimeout(() => { t = 0; foldScan(); }, 30); }).observe(document.body, {childList: true, subtree: true});
  foldScan();
  const toggle = h => { const card = h.parentElement; setFold(card, !card.classList.contains('collapsed')); };
  document.addEventListener('click', e => { const h = e.target.closest && e.target.closest('.card.foldable > h2'); if (h && !e.target.closest('a,button,input,select,label')) toggle(h); });
  document.addEventListener('keydown', e => { const h = e.target.matches && e.target.matches('.card.foldable > h2') ? e.target : null; if (h && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); toggle(h); } });
}
function init() {
  if (false && !document.querySelector('.ui-bg')) { const bg = html('div', 'ui-bg'); bg.setAttribute('aria-hidden', 'true'); bg.append(html('i'), html('i'), html('i')); document.body.prepend(bg); }
  navbar();
  tidyHeaders();
  auto();
  folding();
}
window.UI = {icon, badge, logo, countTo, spark, gauge, bar, lean, ladder, toast, reveal, enhance, nf, reduced: REDUCED};
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
