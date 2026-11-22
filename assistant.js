/* Alfred: postać asystenta (SVG rysowany w kodzie, bez obrazków i bibliotek), głos (rozpoznawanie mowy i czytanie) i polecenia po polsku.
   Części: Avatar (wygląd i animacje), Voice (mikrofon i głośnik), Intent (parser poleceń, czyste funkcje), Alfred (łączy całość).
   Głos działa tam, gdzie przeglądarka ma Web Speech API (Chrome, Edge; w Firefoksie rozpoznawania mowy nie ma), a bez niego zostaje pisanie. */
(() => {
'use strict';
const Intent = window.Intent;
if (!Intent) { console.error('assistant.js wymaga intent.js'); return; }

/* ================================================================== Avatar */
const Avatar = (() => {
  let styled = false, seq = 0;
  const REDUCED = !!(window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches);
  const CSS = `
@property --halo{syntax:'<color>';inherits:true;initial-value:#f2c46d}
.alf{display:block;width:100%;height:auto;overflow:visible;--halo:#f2c46d;transition:--halo .6s ease;-webkit-tap-highlight-color:transparent}
.alf.st-idle{--halo:#f2c46d}.alf.st-listen{--halo:#34d3c1}.alf.st-think{--halo:#a98bfa}.alf.speaking.st-idle{--halo:#f5a524}.alf.st-happy{--halo:#4ade80}.alf.st-worry{--halo:#fb923c}
.alf .ring{filter:drop-shadow(0 0 6px var(--halo))}
.alf .rp{fill:none;stroke:var(--halo);stroke-width:3;opacity:0;transform-origin:150px 160px}
.alf.st-listen .rp,.alf.speaking .rp{animation:alf-ripple 2.4s ease-out infinite}.alf .rp.r2{animation-delay:1.2s!important}
@keyframes alf-ripple{0%{transform:scale(1);opacity:.55}100%{transform:scale(1.2);opacity:0}}
.alf .glow{transition:opacity .6s}.alf.speaking .glow,.alf.st-listen .glow{opacity:1}.alf .glow{opacity:.75}
.alf .body{animation:alf-breathe 4.6s ease-in-out infinite}
@keyframes alf-breathe{0%,100%{transform:translateY(0)}50%{transform:translateY(-2.4px)}}
.alf .sway{transform-origin:150px 250px;animation:alf-sway 8s ease-in-out infinite}
@keyframes alf-sway{0%,100%{transform:rotate(-.7deg)}50%{transform:rotate(.9deg)}}
.alf .tilt{transform-origin:150px 250px;transform:rotate(var(--tilt,0deg)) translateY(var(--hy,0px));transition:transform .6s cubic-bezier(.34,1.5,.64,1)}
.alf .nod{transform-origin:150px 250px}.alf.speaking .nod{animation:alf-nod .8s ease-in-out infinite}
@keyframes alf-nod{0%,100%{transform:rotate(-.9deg)}50%{transform:rotate(1.1deg)}}
.alf.st-listen{--tilt:4.5deg;--hy:-1px}.alf.st-think{--tilt:-5deg}.alf.st-happy{--tilt:2deg;--hy:-2px}.alf.st-worry{--tilt:-2deg;--hy:1px}
.alf .brow{transition:transform .4s cubic-bezier(.34,1.4,.64,1)}
.alf .brow-l{transform-origin:106px 124px}.alf .brow-r{transform-origin:194px 124px}
.alf.st-listen .brow{transform:translateY(-3.5px)}
.alf.st-think .brow-r{transform:translateY(-5px) rotate(8deg)}.alf.st-think .brow-l{transform:translateY(1px) rotate(3deg)}
.alf.st-happy .brow{transform:translateY(-4px)}
.alf.st-worry .brow-l{transform:translateY(-1px) rotate(-13deg)}.alf.st-worry .brow-r{transform:translateY(-1px) rotate(13deg)}
.alf.speaking.st-idle .brow{animation:alf-brow 2.3s ease-in-out infinite}
@keyframes alf-brow{0%,100%{transform:translateY(0)}40%{transform:translateY(-2.5px)}70%{transform:translateY(-1px)}}
.alf .eye{transition:transform .4s cubic-bezier(.34,1.4,.64,1)}
.alf .eye-l{transform-origin:122px 141px}.alf .eye-r{transform-origin:178px 141px}
.alf.st-listen .eye{transform:scale(1.09)}
.alf .pupils{transition:transform .22s ease-out}
.alf{--lidb:.17}.alf.st-listen{--lidb:0}.alf.st-think{--lidb:.24}.alf.st-worry{--lidb:.02}.alf.st-happy{--lidb:.08}
.alf .lid{transform:scaleY(var(--lidb));transform-origin:50% 0;transform-box:fill-box;transition:transform .35s ease}
.alf.blink .lid{animation:alf-blink .17s ease-in-out}
@keyframes alf-blink{0%{transform:scaleY(var(--lidb))}50%{transform:scaleY(1)}100%{transform:scaleY(var(--lidb))}}
.alf .lidlow{transform:scaleY(0);transform-origin:50% 100%;transform-box:fill-box;transition:transform .35s ease}
.alf.st-happy .lidlow{transform:scaleY(.55)}.alf.st-worry .lidlow{transform:scaleY(.12)}
.alf .cheek{transition:opacity .4s,transform .4s;opacity:.38;transform-box:fill-box;transform-origin:center}.alf.st-happy .cheek{opacity:.7;transform:scale(1.18)}
.alf .stache{transition:transform .25s ease}
.alf .fx-think,.alf .fx-worry{opacity:0;transition:opacity .35s}
.alf.st-think .fx-think,.alf.st-worry .fx-worry{opacity:1}
.alf .dot{animation:alf-dot 1.2s ease-in-out infinite}.alf .dot.d2{animation-delay:.2s}.alf .dot.d3{animation-delay:.4s}
@keyframes alf-dot{0%,60%,100%{transform:translateY(0);opacity:.55}30%{transform:translateY(-4px);opacity:1}}
.alf .spark{opacity:0;transform-box:fill-box;transform-origin:center}
.alf.cheer .spark{animation:alf-spark 1.3s ease-out both}
.alf .spark.s2{animation-delay:.1s!important}.alf .spark.s3{animation-delay:.2s!important}.alf .spark.s4{animation-delay:.3s!important}.alf .spark.s5{animation-delay:.15s!important}.alf .spark.s6{animation-delay:.25s!important}
@keyframes alf-spark{0%{opacity:0;transform:scale(.2) rotate(0)}30%{opacity:1;transform:scale(1.15) rotate(40deg)}100%{opacity:0;transform:scale(.6) rotate(110deg)}}
.alf.cheer .body{animation:alf-bounce .7s cubic-bezier(.3,1.6,.5,1)}
@keyframes alf-bounce{0%{transform:translateY(0)}35%{transform:translateY(-9px)}65%{transform:translateY(1px)}100%{transform:translateY(0)}}
.alf.enter{animation:alf-in .9s cubic-bezier(.2,1.3,.4,1) both}
@keyframes alf-in{0%{opacity:0;transform:translateY(14px) scale(.9)}100%{opacity:1;transform:none}}
@media (prefers-reduced-motion:reduce){.alf *,.alf{animation:none!important;transition:none!important}.alf .rp{display:none}}
`;
  function ensureStyle() { if (styled) return; styled = true; const s = document.createElement('style'); s.id = 'alf-style'; s.textContent = CSS; document.head.append(s); }

  const SVG = u => `
<svg class="alf st-idle" viewBox="0 0 300 320" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Alfred, asystent">
<defs>
<radialGradient id="${u}bg" cx=".5" cy=".42" r=".75"><stop offset="0" stop-color="#5a82b3"/><stop offset=".6" stop-color="#34507a"/><stop offset="1" stop-color="#1c2a45"/></radialGradient>
<radialGradient id="${u}halo" cx=".5" cy=".5" r=".5"><stop offset="0" style="stop-color:var(--halo);stop-opacity:.55"/><stop offset=".55" style="stop-color:var(--halo);stop-opacity:.18"/><stop offset="1" style="stop-color:var(--halo);stop-opacity:0"/></radialGradient>
<radialGradient id="${u}vig" cx=".5" cy=".5" r=".5"><stop offset=".72" stop-color="#000" stop-opacity="0"/><stop offset="1" stop-color="#000" stop-opacity=".38"/></radialGradient>
<radialGradient id="${u}skin" cx=".46" cy=".36" r=".78"><stop offset="0" stop-color="#fae1c9"/><stop offset=".55" stop-color="#f2cdae"/><stop offset="1" stop-color="#dfae8b"/></radialGradient>
<linearGradient id="${u}neck" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#c99771"/><stop offset="1" stop-color="#e3b894"/></linearGradient>
<linearGradient id="${u}coat" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#3b4254"/><stop offset="1" stop-color="#161920"/></linearGradient>
<linearGradient id="${u}hair" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#f7f9fb"/><stop offset="1" stop-color="#c2cad7"/></linearGradient>
<radialGradient id="${u}iris" cx=".5" cy=".5" r=".5"><stop offset="0" stop-color="#a5c3d8"/><stop offset=".7" stop-color="#5d819c"/><stop offset="1" stop-color="#3d5b74"/></radialGradient>
<linearGradient id="${u}vest" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#98a2b4"/><stop offset="1" stop-color="#5f697b"/></linearGradient>
<linearGradient id="${u}shirt" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#ffffff"/><stop offset="1" stop-color="#d9e0ea"/></linearGradient>
<linearGradient id="${u}bow" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#303441"/><stop offset="1" stop-color="#0b0c10"/></linearGradient>
<clipPath id="${u}disc"><circle cx="150" cy="160" r="140"/></clipPath>
<clipPath id="${u}eyeL"><ellipse cx="122" cy="141" rx="13" ry="9.4"/></clipPath>
<clipPath id="${u}eyeR"><ellipse cx="178" cy="141" rx="13" ry="9.4"/></clipPath>
<clipPath id="${u}mouth"><path class="m-clip" d="M0 0"/></clipPath>
</defs>
<g class="ripples"><circle class="rp r1" cx="150" cy="160" r="140"/><circle class="rp r2" cx="150" cy="160" r="140"/></g>
<g clip-path="url(#${u}disc)">
<circle cx="150" cy="160" r="141" fill="url(#${u}bg)"/>
<circle class="glow" cx="150" cy="148" r="128" fill="url(#${u}halo)"/>
<g class="body"><g transform="translate(0 -10)">
<!-- fraczek, kamizelka, koszula -->
<path d="M14 330 C14 292 52 268 102 258 L150 252 L198 258 C248 268 286 292 286 330 Z" fill="url(#${u}coat)"/>
<path d="M102 258 C60 268 24 292 18 322" fill="none" stroke="#6b7390" stroke-width="2.6" stroke-linecap="round" opacity=".7"/>
<path d="M198 258 C240 268 276 292 282 322" fill="none" stroke="#6b7390" stroke-width="2.6" stroke-linecap="round" opacity=".45"/>
<path d="M114 256 L150 253 L186 256 L150 312 Z" fill="url(#${u}shirt)"/>
<path d="M122 286 L150 300 L178 286 L172 330 L128 330 Z" fill="url(#${u}vest)"/>
<path d="M122 286 L150 300 L178 286" fill="none" stroke="#b5bfce" stroke-width="1.6" stroke-linejoin="round"/>
<circle cx="150" cy="312" r="2.8" fill="#dde3ec"/><circle cx="150" cy="324" r="2.8" fill="#dde3ec"/>
<g id="${u}lap"><path d="M100 259 L124 256 L152 304 L136 316 L98 286 Z" fill="#2a2e39"/><path d="M100 259 L124 256 L152 304" fill="none" stroke="#464c5e" stroke-width="1.6" stroke-linejoin="round" opacity=".9"/></g>
<use href="#${u}lap" transform="translate(300 0) scale(-1 1)"/>
<path d="M192 306 L208 303 L206 312 L194 314 Z" fill="#fff"/><path d="M194 307 L200 305 L203 309" fill="none" stroke="#cfd6e1" stroke-width="1.2"/>
<!-- szyja i kołnierzyk -->
<path d="M130 212 L170 212 L172 248 L150 262 L128 248 Z" fill="url(#${u}neck)"/>
<path d="M126 246 L148 266 L124 268 L116 254 Z" fill="#fff" stroke="#d3dae5" stroke-width="1.2" stroke-linejoin="round"/>
<path d="M174 246 L152 266 L176 268 L184 254 Z" fill="#fff" stroke="#d3dae5" stroke-width="1.2" stroke-linejoin="round"/>
<!-- muszka -->
<g class="bow" transform="translate(150 263) rotate(-2)">
<path d="M-5 0 C-14 -13 -32 -16 -37 -8 C-41 0 -38 11 -31 13 C-22 15 -12 8 -5 4 Z" fill="url(#${u}bow)"/>
<path d="M5 0 C14 -13 32 -16 37 -8 C41 0 38 11 31 13 C22 15 12 8 5 4 Z" fill="url(#${u}bow)"/>
<path d="M-8 -3 C-18 -9 -28 -8 -33 -4 M8 -3 C18 -9 28 -8 33 -4" fill="none" stroke="#4a4f62" stroke-width="1.4" stroke-linecap="round" opacity=".8"/>
<rect x="-7" y="-7" width="14" height="14" rx="4" fill="#14161c"/><path d="M-4 -4 L4 -4" stroke="#4a4f62" stroke-width="1.4" stroke-linecap="round"/>
</g>
<!-- głowa -->
<g class="sway"><g class="tilt"><g class="nod">
<ellipse cx="85" cy="155" rx="9.5" ry="15" fill="#e8bd9b"/><path d="M85 146 C80 152 81 160 86 164" fill="none" stroke="#c9946f" stroke-width="1.8" stroke-linecap="round"/>
<ellipse cx="215" cy="155" rx="9.5" ry="15" fill="#e8bd9b"/><path d="M215 146 C220 152 219 160 214 164" fill="none" stroke="#c9946f" stroke-width="1.8" stroke-linecap="round"/>
<path d="M150 74 C112 74 88 104 88 146 C88 184 106 214 150 228 C194 214 212 184 212 146 C212 104 188 74 150 74 Z" fill="url(#${u}skin)"/>
<path d="M104 196 C112 214 130 226 150 228 C170 226 188 214 196 196 C186 212 170 220 150 220 C130 220 114 212 104 196 Z" fill="#c98f69" opacity=".22"/>
<!-- włosy -->
<path d="M93 179 C86 165 85 146 89 128 C88 98 112 68 150 68 C188 68 212 98 211 128 C215 146 214 165 207 179 C204 182 200 179 201 174 C203 162 203 148 199 136 C195 118 188 106 178 100 C168 95 160 100 150 100 C140 100 132 95 122 100 C112 106 105 118 101 136 C97 148 97 162 99 174 C100 179 96 182 93 179 Z" fill="url(#${u}hair)"/>
<path d="M104 100 C108 106 108 112 106 118 M118 98 C120 104 120 108 119 112 M196 100 C192 106 192 112 194 118 M182 98 C180 104 180 108 181 112" fill="none" stroke="#e9edf2" stroke-width="2.2" stroke-linecap="round" opacity=".9"/>
<path d="M104 84 C122 74 142 72 158 74 M170 76 C184 78 196 86 202 96 M96 112 C100 100 106 92 114 86 M88 150 C86 160 88 170 92 178 M212 150 C214 160 212 170 208 178" fill="none" stroke="#aab4c3" stroke-width="1.6" stroke-linecap="round" opacity=".7"/>
<path d="M118 112 C132 106 168 106 182 112 M122 119 C134 114 166 114 178 119" fill="none" stroke="#c9946f" stroke-width="1.3" stroke-linecap="round" opacity=".28"/>
<!-- brwi -->
<path class="brow brow-l" d="M104 125 C112 114 130 113 141 122" fill="none" stroke="#d0d4dc" stroke-width="8" stroke-linecap="round"/>
<path class="brow brow-r" d="M196 125 C188 114 170 113 159 122" fill="none" stroke="#d0d4dc" stroke-width="8" stroke-linecap="round"/>
<!-- oczy -->
<g class="eye eye-l"><g clip-path="url(#${u}eyeL)"><ellipse cx="122" cy="141" rx="13" ry="9.4" fill="#fbfcfe"/>
<g class="pupils"><circle cx="122" cy="141" r="6.8" fill="url(#${u}iris)"/><circle cx="122" cy="141" r="3.1" fill="#16202b"/><circle cx="124.6" cy="138.4" r="1.8" fill="#fff"/><circle cx="119.6" cy="143.6" r=".9" fill="#fff" opacity=".7"/></g>
<rect class="lid" x="106" y="128" width="32" height="22" fill="url(#${u}skin)"/><rect class="lidlow" x="106" y="141" width="32" height="12" fill="url(#${u}skin)"/></g>
<path d="M108.5 140 C113 128.5 131 128.5 135.5 140" fill="none" stroke="#a97c60" stroke-width="2" stroke-linecap="round"/>
<path d="M111 150 C117 154.5 128 154.5 134 150" fill="none" stroke="#d0a285" stroke-width="1.6" stroke-linecap="round" opacity=".55"/></g>
<g class="eye eye-r"><g clip-path="url(#${u}eyeR)"><ellipse cx="178" cy="141" rx="13" ry="9.4" fill="#fbfcfe"/>
<g class="pupils"><circle cx="178" cy="141" r="6.8" fill="url(#${u}iris)"/><circle cx="178" cy="141" r="3.1" fill="#16202b"/><circle cx="180.6" cy="138.4" r="1.8" fill="#fff"/><circle cx="175.6" cy="143.6" r=".9" fill="#fff" opacity=".7"/></g>
<rect class="lid" x="162" y="128" width="32" height="22" fill="url(#${u}skin)"/><rect class="lidlow" x="162" y="141" width="32" height="12" fill="url(#${u}skin)"/></g>
<path d="M164.5 140 C169 128.5 187 128.5 191.5 140" fill="none" stroke="#a97c60" stroke-width="2" stroke-linecap="round"/>
<path d="M166 150 C172 154.5 183 154.5 189 150" fill="none" stroke="#d0a285" stroke-width="1.6" stroke-linecap="round" opacity=".55"/></g>
<path d="M104 140 L98 137 M104 144 L97 145 M196 140 L202 137 M196 144 L203 145" fill="none" stroke="#c9946f" stroke-width="1.3" stroke-linecap="round" opacity=".45"/>
<!-- nos, policzki, zmarszczki -->
<path d="M155 130 C158 146 162 160 161 170" fill="none" stroke="#d9a784" stroke-width="3.4" stroke-linecap="round" opacity=".45"/>
<path d="M139 172 C141 180 148 182 150 180 C152 182 159 180 161 172" fill="none" stroke="#c48e69" stroke-width="2.6" stroke-linecap="round"/>
<ellipse cx="150" cy="170" rx="5.5" ry="3.6" fill="#fff" opacity=".22"/>
<ellipse class="cheek" cx="108" cy="172" rx="14" ry="8" fill="#ec9a8c"/><ellipse class="cheek" cx="192" cy="172" rx="14" ry="8" fill="#ec9a8c"/>
<path d="M118 176 C124 190 124 200 120 208 M182 176 C176 190 176 200 180 208" fill="none" stroke="#c9946f" stroke-width="1.5" stroke-linecap="round" opacity=".32"/>
<!-- usta (kształt liczony w JS) -->
<g class="mouth"><path class="m-lips" d="M0 0" fill="#c9786f"/><path class="m-fill" d="M0 0" fill="#5b2631"/><g clip-path="url(#${u}mouth)"><rect class="m-teeth" x="120" y="196" width="60" height="7" fill="#fff"/><ellipse class="m-tongue" cx="150" cy="214" rx="9" ry="4.5" fill="#c9636c"/></g><path class="m-lip" d="M0 0" fill="none" stroke="#a65d58" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></g>
<!-- wąsy -->
<g class="stache"><path d="M150 187 C140 180 124 182 115 192 C110 198 117 201 124 197 C134 192 142 194 150 194 C158 194 166 192 176 197 C183 201 190 198 185 192 C176 182 160 180 150 187 Z" fill="#cdd3dc"/>
<path d="M150 188 C158 184 170 185 178 192 M150 188 C142 184 130 185 122 192" fill="none" stroke="#aeb7c4" stroke-width="1.5" stroke-linecap="round"/></g>
<path d="M140 222 C146 225 154 225 160 222" fill="none" stroke="#c9946f" stroke-width="1.6" stroke-linecap="round" opacity=".35"/>
</g></g></g>
</g></g>
<!-- efekty: myślenie, niepokój, iskry -->
<g class="fx-think"><rect x="186" y="52" width="54" height="26" rx="13" fill="#fff" opacity=".95"/><circle class="dot d1" cx="201" cy="65" r="3.6" fill="#8b6df0"/><circle class="dot d2" cx="213" cy="65" r="3.6" fill="#8b6df0"/><circle class="dot d3" cx="225" cy="65" r="3.6" fill="#8b6df0"/></g>
<g class="fx-worry"><circle cx="216" cy="70" r="15" fill="#fff" opacity=".95"/><path d="M216 61 L216 72" stroke="#f0701a" stroke-width="4" stroke-linecap="round"/><circle cx="216" cy="79" r="2.4" fill="#f0701a"/></g>
<g fill="#ffe08a"><path class="spark s1" d="M70 96 l2.5 -7 2.5 7 7 2.5 -7 2.5 -2.5 7 -2.5 -7 -7 -2.5z"/><path class="spark s2" d="M236 92 l2 -6 2 6 6 2 -6 2 -2 6 -2 -6 -6 -2z"/><path class="spark s3" d="M56 190 l2 -6 2 6 6 2 -6 2 -2 6 -2 -6 -6 -2z"/><path class="spark s4" d="M246 186 l2.5 -7 2.5 7 7 2.5 -7 2.5 -2.5 7 -2.5 -7 -7 -2.5z"/><path class="spark s5" d="M150 36 l2 -6 2 6 6 2 -6 2 -2 6 -2 -6 -6 -2z"/><path class="spark s6" d="M104 52 l1.6 -5 1.6 5 5 1.6 -5 1.6 -1.6 5 -1.6 -5 -5 -1.6z"/></g>
<circle cx="150" cy="160" r="140" fill="url(#${u}vig)"/>
</g>
<circle class="ring" cx="150" cy="160" r="140" fill="none" style="stroke:var(--halo)" stroke-width="5"/>
</svg>`;

  const BASE = {idle: .62, listen: .25, think: .05, happy: 1, worry: -.7}, SPEAK_SMILE = .35;
  const rnd = (a, b) => a + Math.random() * (b - a);

  function create(host, opts = {}) {
    ensureStyle();
    const u = 'alf' + (++seq) + '-';
    host.insertAdjacentHTML('beforeend', SVG(u));
    const svg = host.lastElementChild, q = s => svg.querySelector(s);
    const mFill = q('.m-fill'), mLips = q('.m-lips'), mLip = q('.m-lip'), mClip = q('.m-clip'), mTeeth = q('.m-teeth'), mTongue = q('.m-tongue'), stache = q('.stache'), pupils = [...svg.querySelectorAll('.pupils')];
    if (opts.enter !== false && !REDUCED) { svg.classList.add('enter'); setTimeout(() => svg.classList.remove('enter'), 1000); }
    let state = 'idle', speaking = false, raf = 0, amp = 1, alive = true, last = 0, lookX = 0, lookY = 0, ptrUntil = 0, blinkT = 0, wanderT = 0, cheerT = 0;
    const mouth = {open: 0, wide: 0, smile: BASE.idle}, target = {open: 0, wide: 0, smile: BASE.idle};

    function drawMouth() {
      const cx = 150, cy = 204, hw = 16.5 + mouth.wide * 3 + Math.max(0, mouth.smile) * 4.5, lift = mouth.smile * 3.4, yc = cy - lift;
      const x0 = cx - hw, x1 = cx + hw, top = yc + mouth.smile * 8.4 - .6, bot = top + 3.4 + mouth.open * 21 + Math.max(0, mouth.smile) * 1.2;
      const d = `M${x0.toFixed(1)} ${yc.toFixed(1)} Q${cx} ${top.toFixed(1)} ${x1.toFixed(1)} ${yc.toFixed(1)} Q${cx} ${bot.toFixed(1)} ${x0.toFixed(1)} ${yc.toFixed(1)} Z`;
      mClip.setAttribute('d', d); mFill.setAttribute('d', d); mLip.setAttribute('d', d); mLips.setAttribute('d', d);
      const o = Math.max(0, Math.min(1, mouth.open * 6)); mFill.style.opacity = o; mLips.style.opacity = (1 - o) * .92; mTeeth.style.opacity = Math.min(1, mouth.open * 5);
      mTeeth.setAttribute('y', (yc - 3).toFixed(1)); mTongue.setAttribute('cy', (cy + 4 + mouth.open * 13).toFixed(1)); mTongue.style.opacity = Math.min(1, mouth.open * 4);
      stache.style.transform = `translateY(${(mouth.open * 2.2).toFixed(2)}px)`;
    }
    function loop(t) {
      raf = 0; if (!alive) return;
      const dt = Math.min(64, t - (last || t)); last = t;
      if (speaking) {                                     // sylaby: ok. 4 na sekundę, losowa wysokość i szerokość, wygładzane
        const syl = Math.floor(t / 230);
        if (loop.syl !== syl) { loop.syl = syl; loop.h = Math.random() < .16 ? .05 : rnd(.25, 1) * amp; loop.w = rnd(-.8, .9); }
        target.open = loop.h * (.55 + .45 * Math.abs(Math.sin(t / 230 * Math.PI))); target.wide = loop.w;
      }
      const k = 1 - Math.pow(.0025, dt / 1000);
      let moving = false;
      for (const p of ['open', 'wide', 'smile']) { const diff = target[p] - mouth[p]; if (Math.abs(diff) > .004) { mouth[p] += diff * Math.min(1, k * 1.6); moving = true; } else mouth[p] = target[p]; }
      drawMouth();
      if (speaking || moving) raf = requestAnimationFrame(loop); else last = 0;
    }
    const kick = () => { if (!raf && alive) raf = requestAnimationFrame(loop); };

    function applyLook() { const sx = (lookX * 3.6).toFixed(2), sy = (lookY * 2.6).toFixed(2); pupils.forEach(p => { p.style.transform = `translate(${sx}px,${sy}px)`; }); }
    function wander() {
      wanderT = setTimeout(() => {
        if (!alive) return;
        if (!REDUCED && Date.now() > ptrUntil && state !== 'think' && state !== 'listen') { lookX = rnd(-.7, .7); lookY = rnd(-.4, .4); applyLook(); }
        wander();
      }, rnd(1600, 4200));
    }
    function blinkLoop() {
      blinkT = setTimeout(() => {
        if (!alive) return;
        if (!REDUCED && !document.hidden && state !== 'happy') { svg.classList.remove('blink'); void svg.getBoundingClientRect(); svg.classList.add('blink'); if (Math.random() < .16) setTimeout(() => { svg.classList.remove('blink'); void svg.getBoundingClientRect(); svg.classList.add('blink'); }, 260); }
        blinkLoop();
      }, rnd(2400, 6200));
    }
    function onPointer(e) {
      if (REDUCED || state === 'think' || state === 'listen') return; const r = svg.getBoundingClientRect(); if (!r.width) return;
      const dx = (e.clientX - (r.left + r.width / 2)) / Math.max(260, window.innerWidth * .45), dy = (e.clientY - (r.top + r.height * .45)) / Math.max(260, window.innerHeight * .45);
      lookX = Math.max(-1, Math.min(1, dx * 1.4)); lookY = Math.max(-1, Math.min(1, dy * 1.4)); ptrUntil = Date.now() + 4000; applyLook();
    }
    window.addEventListener('pointermove', onPointer, {passive: true});
    blinkLoop(); wander(); drawMouth();

    const api = {
      el: svg,
      get state() { return state; },
      setState(s) {
        if (!(s in BASE)) s = 'idle'; if (s === state) return;
        svg.classList.remove('st-' + state); state = s; svg.classList.add('st-' + s);
        if (s === 'think') { lookX = .8; lookY = -.8; applyLook(); } else if (s === 'listen' || s === 'worry') { lookX = 0; lookY = 0; applyLook(); }
        target.smile = speaking && s === 'idle' ? SPEAK_SMILE : BASE[s]; if (!speaking) { target.open = 0; target.wide = 0; } kick();
      },
      /* mówienie to cecha niezależna od nastroju: usta, kiwanie głową i pierścień; nastrój (cieszy się, martwi się) zostaje */
      speak(on, a = 1) {
        speaking = !!on; amp = a; svg.classList.toggle('speaking', speaking);
        target.smile = speaking && state === 'idle' ? SPEAK_SMILE : BASE[state]; if (!speaking) { target.open = 0; target.wide = 0; } kick();
      },
      bump(a = 1) { amp = Math.max(.5, Math.min(1.2, a)); },
      /* ręczne ustawienie ust (galeria, testy): open 0..1, wide -1..1, smile -1..1 */
      setMouth(open, wide = 0, smile) { target.open = open; target.wide = wide; if (smile != null) target.smile = smile; kick(); },
      lookAt(x, y) { lookX = x; lookY = y; ptrUntil = Date.now() + 4000; applyLook(); },
      cheer() { clearTimeout(cheerT); svg.classList.remove('cheer'); void svg.getBoundingClientRect(); svg.classList.add('cheer'); cheerT = setTimeout(() => svg.classList.remove('cheer'), 1500); },
      destroy() { alive = false; clearTimeout(blinkT); clearTimeout(wanderT); cancelAnimationFrame(raf); window.removeEventListener('pointermove', onPointer); svg.remove(); },
    };
    return api;
  }
  return {create, reduced: REDUCED};
})();


/* ================================================================== Voice: mikrofon i głośnik (Web Speech API) */
const Voice = (() => {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  const canListen = !!SR, canSpeak = 'speechSynthesis' in window && 'SpeechSynthesisUtterance' in window;
  const KEY = 'alfred_voice';
  const def = {on: false, converse: false, rate: 1, pitch: .92, voiceURI: ''};
  const lg = () => { try { return JSON.parse(localStorage.getItem(KEY)) || {}; } catch (e) { return {}; } };
  const cfg = Object.assign({}, def, lg());
  const save = () => { try { localStorage.setItem(KEY, JSON.stringify(cfg)); } catch (e) { /* tryb prywatny */ } };
  let rec = null, listening = false, speaking = false, token = 0, watchdog = 0;

  function voices() { try { return speechSynthesis.getVoices().filter(v => /^pl/i.test(v.lang)); } catch (e) { return []; } }
  function pick() {
    const v = voices(); if (!v.length) return null;
    return v.find(x => x.voiceURI === cfg.voiceURI) || v.find(x => /adam|marek|piotr|jakub/i.test(x.name)) || v.find(x => /pl[-_]PL/i.test(x.lang)) || v[0];
  }
  /* czyta tekst porcjami (syntezatory przerywają długie wypowiedzi); h: {onStart, onEnd(błąd?), onBoundary} */
  function speak(text, h = {}) {
    const parts = Intent.chunks(Intent.speakable(text));
    if (!canSpeak || !parts.length) { if (h.onEnd) h.onEnd('brak'); return false; }
    try { speechSynthesis.cancel(); } catch (e) { /* brak */ }
    const my = ++token; let i = 0; speaking = true; clearTimeout(watchdog);
    const done = err => { if (my !== token) return; speaking = false; clearTimeout(watchdog); if (h.onEnd) h.onEnd(err); };
    const next = () => {
      if (my !== token) return; if (i >= parts.length) { done(); return; }
      const u = new SpeechSynthesisUtterance(parts[i++]); u.lang = 'pl-PL'; u.rate = cfg.rate; u.pitch = cfg.pitch; const v = pick(); if (v) u.voice = v;
      u.onstart = () => { clearTimeout(watchdog); if (i === 1 && h.onStart) h.onStart(); };
      u.onboundary = e => { if (h.onBoundary) h.onBoundary(e); };
      u.onend = next; u.onerror = e => { if (e.error === 'interrupted' || e.error === 'canceled') return; done(e.error || 'błąd'); };
      try { speechSynthesis.speak(u); } catch (e) { done('błąd'); }
    };
    watchdog = setTimeout(() => { if (my === token && speaking) { try { speechSynthesis.cancel(); } catch (e) { /* brak */ } done('timeout'); } }, 6000);
    setTimeout(next, 40);
    return true;
  }
  function stopSpeaking() { token++; speaking = false; clearTimeout(watchdog); try { speechSynthesis.cancel(); } catch (e) { /* brak */ } }
  /* słuchanie jednej wypowiedzi; h: {onStart, onInterim(tekst), onFinal(tekst), onError(kod), onEnd({err, text})} */
  function listen(h = {}) {
    if (!SR) return false; stopSpeaking();
    let r; try { r = new SR(); } catch (e) { return false; }
    r.lang = 'pl-PL'; r.interimResults = true; r.continuous = false; r.maxAlternatives = 1;
    let fin = '', interim = '', err = null, aborted = false;
    r.onstart = () => { listening = true; if (h.onStart) h.onStart(); };
    r.onresult = e => { interim = ''; for (let i = e.resultIndex; i < e.results.length; i++) { const x = e.results[i]; if (x.isFinal) fin += x[0].transcript + ' '; else interim += x[0].transcript; } if (h.onInterim) h.onInterim((fin + interim).trim()); };
    r.onerror = e => { err = e.error; };
    r.onend = () => {
      listening = false; if (rec === r) rec = null; const text = (fin + ' ' + (fin ? '' : interim)).trim();
      if (aborted) { if (h.onEnd) h.onEnd({err: 'aborted', text: ''}); return; }
      if (err && err !== 'no-speech' && err !== 'aborted') { if (h.onError) h.onError(err); } else if (text && h.onFinal) h.onFinal(text);
      if (h.onEnd) h.onEnd({err, text});
    };
    r.abortNow = () => { aborted = true; try { r.abort(); } catch (e) { /* brak */ } };
    rec = r; try { r.start(); } catch (e) { rec = null; if (h.onError) h.onError('start'); return false; }
    return true;
  }
  const stopListening = () => { if (rec) { try { rec.stop(); } catch (e) { /* brak */ } } };
  const abortListening = () => { if (rec) rec.abortNow(); };
  const ERR = {
    'not-allowed': 'Przeglądarka nie ma zgody na mikrofon. Zezwól na mikrofon w ustawieniach strony (ikona kłódki przy adresie) i spróbuj ponownie.',
    'service-not-allowed': 'Przeglądarka nie ma zgody na mikrofon. Zezwól na mikrofon w ustawieniach strony (ikona kłódki przy adresie) i spróbuj ponownie.',
    'audio-capture': 'Nie znajduję mikrofonu. Podłącz go albo pisz do mnie.',
    'network': 'Rozpoznawanie mowy w tej przeglądarce wymaga internetu (korzysta z usługi przeglądarki). Możesz pisać.',
    'language-not-supported': 'Ta przeglądarka nie rozpoznaje polskiego. Możesz pisać.',
    'start': 'Nie udało się włączyć mikrofonu. Spróbuj jeszcze raz albo pisz.',
  };
  return {canListen, canSpeak, cfg, save, voices, listen, stopListening, abortListening, speak, stopSpeaking, errText: c => ERR[c] || `Nie udało się rozpoznać mowy (${c}). Spróbuj jeszcze raz albo pisz.`,
    get speaking() { return speaking; }, get listening() { return listening; }, onVoices(fn) { if (canSpeak) { try { speechSynthesis.addEventListener('voiceschanged', fn); } catch (e) { /* brak */ } } }};
})();

/* ================================================================== Alfred: scena (postać + dymek + mikrofon + pole tekstowe) */
const Alfred = (() => {
  const ICON = {
    mic: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3z"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/></svg>',
    stop: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="7" y="7" width="10" height="10" rx="2.5"/></svg>',
    send: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h13M13 6l6 6-6 6"/></svg>',
    spk: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 9v6h4l5 4V5L8 9H4z"/><path class="w" d="M16.5 8.5a5 5 0 0 1 0 7M19 6a8.5 8.5 0 0 1 0 12"/></svg>',
  };
  const h = (tag, cls, txt) => { const e = document.createElement(tag); if (cls) e.className = cls; if (txt != null) e.textContent = txt; return e; };
  const todayIso = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`; };
  const lg = k => { try { return localStorage.getItem(k); } catch (e) { return null; } };
  const ls = (k, v) => { try { localStorage.setItem(k, v); } catch (e) { /* tryb prywatny */ } };

  function mount(host, o = {}) {
    const role = o.role || 'alfred', hkey = 'alfred_hist_' + role;
    let hist = []; try { hist = JSON.parse(lg(hkey)) || []; } catch (e) { hist = []; }
    host.classList.add('alf-stage'); host.setAttribute('aria-label', 'Asystent Alfred');
    host.innerHTML = '';
    const fig = h('div', 'alf-fig'), avBtn = h('button', 'alf-avatar'), status = h('div', 'alf-status');
    avBtn.type = 'button'; avBtn.setAttribute('aria-label', Voice.canListen ? 'Rozmawiaj z Alfredem: dotknij i mów' : 'Alfred: dotknij, żeby napisać'); status.setAttribute('aria-live', 'polite');
    fig.append(avBtn, status);
    const main = h('div', 'alf-main'), bubble = h('div', 'alf-bubble'), who = h('div', 'alf-who', o.name || 'Alfred'), text = h('div', 'alf-text'), you = h('div', 'alf-you'), acts = h('div', 'alf-actions'), read = h('button', 'alf-read');
    read.type = 'button'; read.innerHTML = ICON.spk; read.setAttribute('aria-label', 'Przeczytaj na głos'); read.title = 'Przeczytaj na głos'; read.hidden = !Voice.canSpeak;
    bubble.setAttribute('role', 'log'); bubble.setAttribute('aria-live', 'polite'); bubble.append(read, who, text, you, acts);
    const form = h('form', 'alf-input'), mic = h('button', 'alf-mic'), field = h('input', 'alf-field'), send = h('button', 'alf-send'), spk = h('button', 'alf-spk');
    mic.type = 'button'; mic.innerHTML = ICON.mic; mic.setAttribute('aria-label', 'Mów do Alfreda'); mic.setAttribute('aria-pressed', 'false');
    field.type = 'text'; field.placeholder = o.placeholder || (Voice.canListen ? 'Powiedz albo napisz…' : 'Napisz do Alfreda…'); field.setAttribute('aria-label', 'Wiadomość do Alfreda'); field.maxLength = 4000; field.autocomplete = 'off'; field.enterKeyHint = 'send';
    send.type = 'submit'; send.innerHTML = ICON.send; send.setAttribute('aria-label', 'Wyślij');
    spk.type = 'button'; spk.innerHTML = ICON.spk; spk.setAttribute('aria-label', 'Czytaj odpowiedzi na głos'); spk.title = 'Czytaj odpowiedzi na głos';
    if (!Voice.canListen) mic.hidden = true; if (!Voice.canSpeak) spk.hidden = true;
    form.append(mic, field, send, spk);
    const chips = h('div', 'alf-chips');
    main.append(bubble, form, chips); host.append(fig, main);
    const av = Avatar.create(avBtn);

    let voiceReply = false, lastSaid = '', busy = false, emptyListens = 0, destroyed = false, happyT = 0, mimicT = 0;
    const setStatus = t => { status.textContent = t || ''; };
    const syncSpk = () => { spk.setAttribute('aria-pressed', String(!!Voice.cfg.on)); spk.classList.toggle('on', !!Voice.cfg.on); };
    syncSpk();
    function setActions(list) {
      acts.replaceChildren(); (list || []).forEach(a => { const b = h('button', 'alf-btn' + (a.primary ? ' p' : ''), a.label); b.type = 'button'; b.addEventListener('click', () => { if (a.once) b.disabled = true; a.onclick && a.onclick(); }); acts.append(b); });
    }
    function setChips(list) {
      chips.replaceChildren(); (list || []).forEach(c => { const b = h('button', 'alf-chip', c.label); b.type = 'button'; b.addEventListener('click', () => { if (c.run) c.run(); else submit(c.text, {voice: false}); }); chips.append(b); });
    }
    function pop() { bubble.classList.remove('pop'); void bubble.offsetWidth; bubble.classList.add('pop'); }
    function mood(m) {
      clearTimeout(happyT);
      if (m === 'happy') { av.setState('happy'); av.cheer(); happyT = setTimeout(() => { if (av.state === 'happy') av.setState('idle'); }, 2400); }
      else if (m === 'worry') { av.setState('worry'); happyT = setTimeout(() => { if (av.state === 'worry') av.setState('idle'); }, 5000); }
    }
    /* mówienie: głos, jeśli włączony lub pytanie było głosem; inaczej same usta przez chwilę (postać „żyje”) */
    function say(t, so = {}) {
      t = String(t == null ? '' : t); text.textContent = Intent.plain(t); you.textContent = so.keepYou ? you.textContent : ''; pop(); setActions(so.actions); bubble.classList.remove('thinking'); bubble.scrollTop = 0;
      if (!so.keep) { hist.push({role: 'assistant', content: t}); hist = hist.slice(-30); ls(hkey, JSON.stringify(hist)); }
      lastSaid = t; clearTimeout(mimicT); if (so.mood) mood(so.mood);
      const wantVoice = so.speak != null ? so.speak : (voiceReply || Voice.cfg.on);
      const spoken = Intent.speakable(t);
      if (wantVoice && Voice.canSpeak && spoken) speakText(t, so.listenAfter);
      else {
        if (!Avatar.reduced && spoken && !so.silent) { av.speak(true); mimicT = setTimeout(() => av.speak(false), Math.min(2600, 500 + spoken.length * 16)); }
        afterSpeech(so.listenAfter);
      }
    }
    function speakText(t, listenAfter) {
      clearTimeout(mimicT);
      Voice.speak(t, {
        onStart: () => { av.speak(true); setStatus('Mówię… (dotknij, żeby przerwać)'); },
        onBoundary: () => av.bump(.7 + Math.random() * .5),
        onEnd: () => { av.speak(false); setStatus(''); afterSpeech(listenAfter); },
      });
    }
    function afterSpeech(force) { if (destroyed) return; if ((force && voiceReply || Voice.cfg.converse && voiceReply) && Voice.canListen && emptyListens < 2 && !busy) setTimeout(() => { if (!destroyed && !Voice.listening && !Voice.speaking && !busy) listen(); }, 350); }
    function thinking(on) { busy = !!on; bubble.classList.toggle('thinking', !!on); if (on) { text.innerHTML = '<span class="alf-dots"><i></i><i></i><i></i></span>'; setActions([]); av.setState('think'); setStatus('Myślę…'); } else { setStatus(''); if (av.state === 'think') av.setState('idle'); } }
    function listen() {
      if (!Voice.canListen) { field.focus(); return; }
      if (Voice.speaking) Voice.stopSpeaking(); av.speak(false);
      let got = false;
      Voice.listen({
        onStart: () => { mic.classList.add('rec'); mic.innerHTML = ICON.stop; mic.setAttribute('aria-pressed', 'true'); av.setState('listen'); setStatus('Słucham… (dotknij, żeby skończyć)'); you.textContent = ''; },
        onInterim: t => { you.textContent = '„' + t + '”'; },
        onFinal: t => { got = true; emptyListens = 0; submit(t, {voice: true}); },
        onError: c => { got = true; av.setState('idle'); say(Voice.errText(c), {mood: 'worry', speak: false, keep: true}); },
        onEnd: () => { mic.classList.remove('rec'); mic.innerHTML = ICON.mic; mic.setAttribute('aria-pressed', 'false'); if (av.state === 'listen') av.setState('idle'); if (!busy && !Voice.speaking) setStatus(''); if (!got) { emptyListens++; if (!destroyed && o.noSpeechHint !== false && emptyListens === 1) { you.textContent = 'Nie słyszałem. Spróbuj jeszcze raz albo napisz.'; } } },
      });
    }
    function toggleListen() { if (Voice.listening) Voice.stopListening(); else listen(); }
    /* wejście użytkownika (głos albo tekst): najpierw polecenia lokalne, reszta do AI */
    async function submit(raw, so = {}) {
      raw = String(raw || '').trim(); if (!raw || busy && !so.force) return;
      voiceReply = !!so.voice; if (so.voice) emptyListens = 0;
      you.textContent = '„' + raw + '”'; hist.push({role: 'user', content: so.prompt || raw, shown: so.prompt ? raw : undefined}); hist = hist.slice(-30); ls(hkey, JSON.stringify(hist));
      const cmd = so.prompt ? {type: 'ask', text: raw} : Intent.parseCommand(raw, {today: todayIso(), page: o.page});
      thinking(true);
      try {
        let r;                                                   // undefined: nikt tego nie obsłużył; null: obsłużone, bez wypowiedzi; tekst albo obiekt: do powiedzenia
        if (o.intercept && !so.prompt) r = await o.intercept(raw, cmd, api);
        if (r === undefined) {
          if (cmd.type === 'ask') r = await askAI(raw, so);
          else { if (o.onCommand) r = await o.onCommand(cmd, api, raw); if (r === undefined) r = await builtin(cmd); }
        }
        thinking(false);
        if (r != null) { const rr = typeof r === 'string' ? {text: r} : r; say(rr.text, {...rr, keepYou: true}); }
      } catch (e) { thinking(false); say('Nie udało się: ' + (e && e.message ? e.message : e), {mood: 'worry', speak: false}); }
    }
    async function askAI(raw, so) {
      const msgs = hist.slice(-20).map(m => ({role: m.role, content: m.content})); while (msgs.length && msgs[0].role !== 'user') msgs.shift();
      const reply = o.ask ? await o.ask(msgs, so) : await AI.run(role, msgs, !!so.search);
      if (typeof o.onReply === 'function') o.onReply(reply);
      return {text: reply, actions: o.replyActions ? o.replyActions(reply) : []};
    }
    /* polecenia obsługiwane przez samą scenę (wspólne dla stron) */
    async function builtin(cmd) {
      switch (cmd.type) {
        case 'wake': return {text: 'Słucham.'};
        case 'hush': Voice.stopSpeaking(); av.speak(false); setStatus(''); return null;
        case 'repeat': return lastSaid ? {text: lastSaid, keep: true, speak: true} : {text: 'Jeszcze nic nie mówiłem.'};
        case 'thanks': return {text: 'Do usług.', mood: 'happy'};
        case 'greet': return {text: new Date().getHours() < 18 ? 'Dzień dobry. W czym mogę pomóc?' : 'Dobry wieczór. W czym mogę pomóc?'};
        case 'help': return {text: o.helpText || 'Dotknij mojej postaci i mów albo pisz. Na przykład: „dodaj zadanie raport 45 minut jutro”, „co dalej”, „zrobione”, „start”, „plan na jutro”.'};
        case 'ask_what': return {text: cmd.what === 'zadanie' ? 'Jakie zadanie dodać? Powiedz na przykład: „dodaj zadanie raport 45 minut jutro”.' : 'Podaj nazwę, na przykład: „dodaj nawyk czytanie”.'};
        case 'go': if (cmd.page) { setTimeout(() => { location.href = cmd.page; }, 450); return {text: 'Już otwieram.', speak: false}; } return null;
        default: return {text: 'Tego jeszcze nie umiem zrobić głosem, ale możesz mnie o to zapytać zwykłym zdaniem.'};
      }
    }
    // zdarzenia
    read.addEventListener('click', () => { if (Voice.speaking) { Voice.stopSpeaking(); av.speak(false); setStatus(''); return; } if (lastSaid || text.textContent) speakText(lastSaid || text.textContent); });
    avBtn.addEventListener('click', () => { if (Voice.speaking) { Voice.stopSpeaking(); av.speak(false); setStatus(''); return; } if (Voice.canListen) toggleListen(); else field.focus(); });
    mic.addEventListener('click', toggleListen);
    spk.addEventListener('click', () => { Voice.cfg.on = !Voice.cfg.on; Voice.save(); syncSpk(); if (!Voice.cfg.on) { Voice.stopSpeaking(); av.speak(false); } else say('Dobrze, będę czytał odpowiedzi na głos.', {speak: true, keep: true}); });
    form.addEventListener('submit', e => { e.preventDefault(); const t = field.value; field.value = ''; submit(t, {voice: false}); });
    field.addEventListener('keydown', e => { if (e.key === 'Escape') { field.value = ''; Voice.abortListening(); Voice.stopSpeaking(); } });
    const onHide = () => { Voice.stopSpeaking(); Voice.abortListening(); };
    window.addEventListener('pagehide', onHide);

    const api = {
      el: host, avatar: av, field, say, thinking, submit, listen, setChips, setActions, setStatus, mood, bubble,
      get history() { return hist.slice(); }, clear() { hist = []; ls(hkey, '[]'); }, get lastSaid() { return lastSaid; },
      get busy() { return busy; }, focus() { field.focus(); },
      destroy() { destroyed = true; onHide(); window.removeEventListener('pagehide', onHide); av.destroy(); host.innerHTML = ''; },
    };
    return api;
  }

  /* ustawienia głosu (do szuflady „Więcej”): czytanie odpowiedzi, rozmowa ciągła, tempo, wybór głosu, próba */
  function settingsUI(box, alf) {
    box.replaceChildren();
    const row = (label, el) => { const l = h('label', 'alf-set'); l.append(h('span', '', label), el); return l; };
    const on = h('input'); on.type = 'checkbox'; on.checked = !!Voice.cfg.on; on.addEventListener('change', () => { Voice.cfg.on = on.checked; Voice.save(); });
    const cv = h('input'); cv.type = 'checkbox'; cv.checked = !!Voice.cfg.converse; cv.addEventListener('change', () => { Voice.cfg.converse = cv.checked; Voice.save(); });
    const rate = h('input'); rate.type = 'range'; rate.min = '.7'; rate.max = '1.4'; rate.step = '.05'; rate.value = String(Voice.cfg.rate); rate.addEventListener('input', () => { Voice.cfg.rate = +rate.value; Voice.save(); });
    const pitch = h('input'); pitch.type = 'range'; pitch.min = '.6'; pitch.max = '1.4'; pitch.step = '.05'; pitch.value = String(Voice.cfg.pitch); pitch.addEventListener('input', () => { Voice.cfg.pitch = +pitch.value; Voice.save(); });
    const sel = h('select'); const fill = () => { const vs = Voice.voices(); sel.replaceChildren(); sel.append(Object.assign(h('option', '', vs.length ? 'automatycznie' : 'brak polskich głosów w systemie'), {value: ''})); vs.forEach(v => sel.append(Object.assign(h('option', '', v.name), {value: v.voiceURI}))); sel.value = Voice.cfg.voiceURI || ''; };
    fill(); Voice.onVoices(fill); sel.addEventListener('change', () => { Voice.cfg.voiceURI = sel.value; Voice.save(); });
    const test = h('button', 'alf-btn', 'Posłuchaj próbki'); test.type = 'button'; test.addEventListener('click', () => { if (alf) alf.say('Dzień dobry. Jestem Alfred. Tak brzmi mój głos.', {speak: true, keep: true}); else Voice.speak('Dzień dobry. Jestem Alfred.'); });
    if (!Voice.canSpeak && !Voice.canListen) { box.append(h('p', 'alf-note', 'Ta przeglądarka nie obsługuje głosu. Możesz pisać do Alfreda. Rozpoznawanie mowy działa w Chrome i Edge.')); return; }
    if (Voice.canSpeak) box.append(row('Czytaj odpowiedzi na głos', on));
    if (Voice.canListen) box.append(row('Rozmowa ciągła (po odpowiedzi słucham dalej)', cv));
    if (Voice.canSpeak) box.append(row('Tempo mowy', rate), row('Wysokość głosu', pitch), row('Głos', sel), test);
    box.append(h('p', 'alf-note', 'Rozpoznawanie mowy zapewnia przeglądarka (w Chrome, z tego co wiem, wysyła dźwięk do usług Google). Alfred nie podsłuchuje w tle: mikrofon działa tylko po dotknięciu.'));
  }
  return {mount, settingsUI};
})();

window.Avatar = Avatar; window.Voice = Voice; window.Alfred = Alfred;
})();
