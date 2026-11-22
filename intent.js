/* Polecenia po polsku: czyste funkcje bez DOM. Używa ich asystent głosowy (assistant.js), a testy sprawdzają je w node (tests/test_assistant.py).
   Rozpoznawanie mowy zwraca tekst bez ogonków i interpunkcji w dowolnych kombinacjach, więc dopasowania robimy na tekście „złożonym” (bez ogonków, małe litery),
   a treść zadań i nazw wycinamy z oryginału (złożenie zachowuje długość tekstu, więc indeksy się zgadzają). */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api; else root.Intent = api;
})(typeof self !== 'undefined' ? self : this, function () {
'use strict';

const MAP = {'ą': 'a', 'ć': 'c', 'ę': 'e', 'ł': 'l', 'ń': 'n', 'ó': 'o', 'ś': 's', 'ź': 'z', 'ż': 'z'};
const fold = s => String(s == null ? '' : s).toLowerCase().replace(/[ąćęłńóśźż]/g, c => MAP[c]);
const pad = n => String(n).padStart(2, '0');
const addDays = (iso, n) => { const [y, m, d] = iso.split('-').map(Number), t = new Date(Date.UTC(y, m - 1, d + n)); return `${t.getUTCFullYear()}-${pad(t.getUTCMonth() + 1)}-${pad(t.getUTCDate())}`; };
const dow = iso => { const [y, m, d] = iso.split('-').map(Number); return new Date(Date.UTC(y, m - 1, d)).getUTCDay(); };
const cap = s => s ? s[0].toUpperCase() + s.slice(1) : s;

/* ---------------------------------------------------------------- liczby słowami (awaryjnie: przeglądarki zwykle zwracają cyfry) */
const UNITS = {zero: 0, jeden: 1, jedna: 1, jedno: 1, dwa: 2, dwie: 2, trzy: 3, cztery: 4, piec: 5, szesc: 6, siedem: 7, osiem: 8, dziewiec: 9, dziesiec: 10, jedenascie: 11, dwanascie: 12, trzynascie: 13, czternascie: 14, pietnascie: 15, szesnascie: 16, siedemnascie: 17, osiemnascie: 18, dziewietnascie: 19};
const TENS = {dwadziescia: 20, trzydziesci: 30, czterdziesci: 40, piecdziesiat: 50, szescdziesiat: 60, siedemdziesiat: 70, osiemdziesiat: 80, dziewiecdziesiat: 90};
const HUND = {sto: 100, dwiescie: 200, trzysta: 300, czterysta: 400, piecset: 500, szescset: 600, siedemset: 700, osiemset: 800, dziewiecset: 900};
const THOU = {tysiac: 1, tysiace: 1, tysiecy: 1};
/* „dwa tysiące pięćset” -> „2500” (tekst złożony na wejściu i wyjściu) */
function numberWords(f) {
  const toks = String(f).split(/\s+/), out = [];
  let cur = 0, total = 0, any = false;
  const flush = () => { if (any) { out.push(String(total + cur)); cur = 0; total = 0; any = false; } };
  for (const t of toks) {
    if (t in UNITS || t in TENS || t in HUND) { cur += UNITS[t] ?? TENS[t] ?? HUND[t]; any = true; }
    else if (t in THOU && (any || true)) { total += (cur || 1) * 1000; cur = 0; any = true; }
    else { flush(); out.push(t); }
  }
  flush();
  return out.join(' ');
}

/* ---------------------------------------------------------------- przygotowanie tekstu */
/* usuwa zwroty do asystenta na początku („Alfredzie, …”, „hej alfred …”) i znaki na końcu; zwraca {o: oryginał, f: tekst złożony} */
function prep(raw) {
  let o = String(raw == null ? '' : raw).replace(/\s+/g, ' ').trim(), f = fold(o);
  const m = f.match(/^(?:(?:hej|ej|czesc|witaj|no|prosze|alfredzie|alfred)[ ,.:!-]+)+/);
  if (m) { o = o.slice(m[0].length); f = f.slice(m[0].length); }
  const e = f.match(/[ ,.!?;:]+$/);
  if (e) { o = o.slice(0, o.length - e[0].length); f = f.slice(0, f.length - e[0].length); }
  if (/^(?:hej|ej|czesc|witaj|alfredzie|alfred)$/.test(f)) { f = '#wake'; }
  return {o, f};
}
const cut = (o, f, a, b) => ({o: (o.slice(0, a) + ' ' + o.slice(b)).replace(/\s+/g, ' ').trim(), f: (f.slice(0, a) + ' ' + f.slice(b)).replace(/\s+/g, ' ').trim()});

/* ---------------------------------------------------------------- termin i czas trwania */
const WD = {poniedzialek: 1, wtorek: 2, sroda: 3, srode: 3, czwartek: 4, piatek: 5, sobote: 6, sobota: 6, niedziele: 0, niedziela: 0};
const MON = {stycznia: 1, lutego: 2, marca: 3, kwietnia: 4, maja: 5, czerwca: 6, lipca: 7, sierpnia: 8, wrzesnia: 9, pazdziernika: 10, listopada: 11, grudnia: 12};
function validDate(y, m, d) { const t = new Date(Date.UTC(y, m - 1, d)); return t.getUTCFullYear() === y && t.getUTCMonth() === m - 1 && t.getUTCDate() === d; }
/* szuka terminu w tekście złożonym: zwraca {date, start, end} albo null */
function findWhen(f, today) {
  let m;
  if ((m = /\b(?:na |w |we |do )?pojutrze\b/.exec(f))) return {date: addDays(today, 2), start: m.index, end: m.index + m[0].length};
  if ((m = /\b(?:na |do )?jutro\b/.exec(f))) return {date: addDays(today, 1), start: m.index, end: m.index + m[0].length};
  if ((m = /\b(?:na |do |w )?(?:dzis|dzisiaj)\b/.exec(f))) return {date: today, start: m.index, end: m.index + m[0].length};
  if ((m = /\bza (\d{1,2}) (?:dni|dzien)\b/.exec(f))) return {date: addDays(today, +m[1]), start: m.index, end: m.index + m[0].length};
  if ((m = /\bza tydzien\b/.exec(f))) return {date: addDays(today, 7), start: m.index, end: m.index + m[0].length};
  if ((m = /\b(?:na |w |we |do )?(poniedzialek|wtorek|sroda|srode|czwartek|piatek|sobote|niedziele)\b/.exec(f))) {
    const want = WD[m[1]]; let n = (want - dow(today) + 7) % 7; if (n === 0) n = 7;
    return {date: addDays(today, n), start: m.index, end: m.index + m[0].length};
  }
  const y0 = +today.slice(0, 4);
  if ((m = /\b(?:na |do |dnia )(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?\b/.exec(f)) || (m = /\b(?:na |do |dnia )(\d{1,2}) (stycznia|lutego|marca|kwietnia|maja|czerwca|lipca|sierpnia|wrzesnia|pazdziernika|listopada|grudnia)\b/.exec(f))) {
    const d = +m[1], mo = MON[m[2]] || +m[2]; let y = m[3] ? (+m[3] < 100 ? 2000 + +m[3] : +m[3]) : y0;
    if (validDate(y, mo, d)) {
      let iso = `${y}-${pad(mo)}-${pad(d)}`; if (!m[3] && iso < today) iso = `${y + 1}-${pad(mo)}-${pad(d)}`;
      if (validDate(+iso.slice(0, 4), mo, d)) return {date: iso, start: m.index, end: m.index + m[0].length};
    }
  }
  return null;
}
/* czas trwania w minutach (5-240) albo null */
function findDuration(f) {
  let min = 0, found = false; const spans = [];
  const eat = (re, fn) => { let m; const r = new RegExp(re.source, 'g'); while ((m = r.exec(f))) { min += fn(m); found = true; spans.push([m.index, m.index + m[0].length]); } };
  eat(/\b(\d+(?:[.,]\d+)?)\s*(?:godzin\w*|godz\.?|h)\b/, m => Math.round(parseFloat(m[1].replace(',', '.')) * 60));
  eat(/\b(\d+)\s*(?:minut\w*|min\.?)(?=\b|\s|$)/, m => +m[1]);
  eat(/\bpol godziny\b/, () => 30);
  eat(/\bpoltorej godziny\b/, () => 90);
  eat(/\bkwadrans\b/, () => 15);
  eat(/\b(?:na |przez )(?:jedna |1 )?godzine\b/, () => 60);
  return found ? {min: Math.max(5, Math.min(240, min)), spans} : null;
}
function removeSpans(o, f, spans) {
  let oo = o, ff = f; spans.slice().sort((a, b) => b[0] - a[0]).forEach(([a, b]) => { const r = cut(oo, ff, a, b); oo = oo.slice(0, a) + ' ' + oo.slice(b); ff = ff.slice(0, a) + ' ' + ff.slice(b); void r; });
  return {o: oo.replace(/\s+/g, ' ').trim(), f: ff.replace(/\s+/g, ' ').trim()};
}
/* treść zadania z terminem, czasem i priorytetem: „Raport 45 minut jutro pilne” -> {text: 'Raport', min: 45, date: jutro, top: true} */
function parseTask(raw, ctx) {
  const today = (ctx && ctx.today) || '2000-01-01';
  let {o, f} = prep(raw), spans = [], date = null, min = null, top = false;
  const w = findWhen(f, today); if (w) { date = w.date; spans.push([w.start, w.end]); }
  const d = findDuration(f); if (d) { min = d.min; spans.push(...d.spans); }
  const p = /\b(?:pilne|pilny|pilna|wazne|wazny|wazna|priorytetowe|priorytetowo|priorytet|najwazniejsze)\b/.exec(f); if (p) { top = true; spans.push([p.index, p.index + p[0].length]); }
  const merged = []; spans.sort((a, b) => a[0] - b[0]).forEach(s => { const l = merged[merged.length - 1]; if (l && s[0] <= l[1]) l[1] = Math.max(l[1], s[1]); else merged.push(s.slice()); });
  ({o, f} = removeSpans(o, f, merged));
  o = o.replace(/^(?:zeby|ze|aby|abym|o|do|na|dla)\s+/i, '').replace(/^[ ,.:;-]+|[ ,.:;-]+$/g, '').replace(/\s+(?:na|w|we|do|przez|i|oraz)$/i, '').trim();
  return {text: cap(o).slice(0, 120), min, date, top};
}

/* ---------------------------------------------------------------- dopasowanie nazw (zadania, nawyki, oferty) */
const STOP = new Set(['i', 'w', 'we', 'na', 'z', 'ze', 'do', 'o', 'a', 'to', 'ten', 'ta', 'te', 'sie', 'za', 'od', 'po', 'dla', 'zadanie', 'nawyk', 'oferte', 'oferta']);
const stem = t => t.slice(0, 4);
const toks = s => fold(s).replace(/[^a-z0-9 ]+/g, ' ').split(/\s+/).filter(t => t && !STOP.has(t)).map(stem);
/* zwraca elementy pasujące do zapytania (najlepsze pierwsze); wynik >= 0,5 liczby słów zapytania */
function match(query, items, getText) {
  const q = toks(query); if (!q.length) return [];
  return items.map(it => { const t = new Set(toks(getText ? getText(it) : it)); const hit = q.filter(w => t.has(w)).length; return {it, score: hit / q.length}; })
    .filter(x => x.score >= .5).sort((a, b) => b.score - a.score).map(x => x.it);
}

/* ---------------------------------------------------------------- cena „za 300 zł” */
function findPrice(f) {
  const i = f.lastIndexOf(' za ');
  if (i < 0) return null;
  const after = numberWords(f.slice(i + 4));
  const m = /^(\d+(?:\s\d{3})*(?:[.,]\d{1,2})?)(?:\s*(?:zl\w*|pln))?/.exec(after);
  if (!m) return null;
  const price = parseFloat(m[1].replace(/\s/g, '').replace(',', '.'));
  return Number.isFinite(price) ? {price, at: i} : null;
}

/* ---------------------------------------------------------------- polecenia */
const PAGES = {dzis: 'alfred.html', alfred: 'alfred.html', alfreda: 'alfred.html', zarabiaj: 'zarabiaj.html', zarabianie: 'zarabiaj.html', zarabiania: 'zarabiaj.html', oferty: 'zarabiaj.html', ofert: 'zarabiaj.html', bot: 'bot.html', bota: 'bot.html', boty: 'bot.html', rynek: 'index.html', rynku: 'index.html', rynki: 'rynki.html', rynkow: 'rynki.html', okazje: 'okazje.html', okazji: 'okazje.html', strategie: 'strategie.html', strategii: 'strategie.html', kalkulator: 'kalkulator.html', kalkulatora: 'kalkulator.html', panel: 'index.html', panelu: 'index.html'};
/* zwraca {type, ...}; nierozpoznane zdanie to {type: 'ask', text}, które idzie do asystenta AI */
function parseCommand(raw, ctx) {
  ctx = ctx || {}; const today = ctx.today || '2000-01-01';
  const {o, f} = prep(raw); let m;
  if (!f) return {type: 'empty'};
  if (f === '#wake') return {type: 'wake'};
  const ask = {type: 'ask', text: o};
  if (/^(?:cicho|cisza|przestan(?: mowic)?|stop|dosc|wystarczy)$/.test(f)) return {type: 'hush'};
  if (/^(?:powtorz|jeszcze raz|powiedz jeszcze raz|co mowiles)$/.test(f)) return {type: 'repeat'};
  if (/^(?:dziekuje|dzieki|dziekuje ci|dziekuje bardzo|super|swietnie|swietna robota|dobra robota)$/.test(f)) return {type: 'thanks'};
  if (/^(?:dzien dobry|dobry wieczor|dobry wieczor alfredzie|jak sie masz|co slychac)$/.test(f)) return {type: 'greet'};
  if (/^(?:pomoc|help|co potrafisz|co umiesz|co moge powiedziec|jakie sa polecenia|jak z toba rozmawiac|co mozesz)$/.test(f)) return {type: 'help'};
  if (/^(?:co (?:teraz|dalej|nastepne|dalej robimy|mam teraz robic|mam robic|robic dalej)|nastepny krok|od czego zaczac|od czego zaczynamy)$/.test(f)) return {type: 'next'};
  if (/^(?:co mam (?:dzis|dzisiaj)(?: w planie| do zrobienia)?|plan (?:na )?(?:dzis|dzisiaj)|jaki (?:mam|jest moj) plan(?: na (?:dzis|dzisiaj))?|pokaz plan|moj plan|co dzis w planie)$/.test(f)) return {type: 'plan', day: 'today'};
  if (/^(?:co mam jutro|plan na jutro|jaki (?:mam|jest moj) plan na jutro|co jutro|pokaz plan na jutro|co jutro w planie)$/.test(f)) return {type: 'plan', day: 'tomorrow'};
  if (/^(?:(?:przeczytaj|odczytaj|czytaj|powiedz|daj|puszcz)\s+(?:mi\s+)?(?:poranny\s+)?briefing|briefing|poranny briefing|co nowego)$/.test(f)) return {type: 'briefing'};
  if (/^(?:przeglad dnia|zrobmy przeglad(?: dnia)?|podsumuj dzien|podsumowanie dnia|wieczorny przeglad|zacznijmy przeglad(?: dnia)?)$/.test(f)) return {type: 'review'};
  if (/^(?:ile zarobilem(?: w tym miesiacu)?|jak (?:moje )?finanse|jak (?:sa|stoja) (?:moje )?finanse|jak (?:mi )?idzie zarabianie|moj zysk|pieniadze|zysk|jak z pieniedzmi|stan finansow)$/.test(f)) return {type: 'money'};
  if (/^(?:pozniej|przeloz(?: to)?(?: na jutro)?|nie dzis|zrobie to jutro|odloz(?: to)?(?: na jutro)?)$/.test(f)) return {type: 'later'};
  if (/^(?:koniec skupienia|zakoncz skupienie|przerwij(?: skupienie)?|zatrzymaj(?: skupienie)?|wylacz timer)$/.test(f)) return {type: 'stopfocus'};
  if ((m = /^(?:start|zaczynam|zaczynamy|rozpocznij|zacznij|uruchom|wlacz|startuj)(?:\s+(?:skupienie|zadanie|timer|prace|pomodoro))?(?:\s+(?:na\s+)?(\d{1,3})\s*(?:min|minut|minuty)?)?$/.exec(f)) || (m = /^(?:skupienie|pomodoro)(?:\s+(?:na\s+)?(\d{1,3})\s*(?:min|minut|minuty)?)?$/.exec(f))) return {type: 'start', min: m[1] ? Math.max(5, Math.min(240, +m[1])) : null};
  if ((m = /^(?:zrobione|zrobilem|zrobilam|skonczylem|skonczylam|gotowe|ukonczone|odhacz|zaliczone|wykonane|wykonalem|wykonalam)(?:\s+(?:to|zadanie|nawyk))?(?:\s+(.+))?$/.exec(f))) return {type: 'done', query: m[1] ? o.slice(f.length - m[1].length).trim() : ''};
  if (/^(?:dodaj|zapisz|utworz|stworz|wpisz|dopisz|zaplanuj)\s+(?:nowe\s+)?(?:zadanie|zadanka|task)$/.test(f)) return {type: 'ask_what', what: 'zadanie'};
  if (/^(?:dodaj|zapisz|utworz|stworz|nowy)\s+nawyk$/.test(f)) return {type: 'ask_what', what: 'nawyk'};
  if (/^(?:dodaj|zapisz|nowa|utworz)\s+(?:nowa\s+)?oferte$/.test(f)) return {type: 'ask_what', what: 'oferta'};
  if ((m = /^(?:dodaj|zapisz|utworz|stworz|wpisz|dopisz|zaplanuj)\s+(?:nowe\s+)?(?:zadanie|zadanka|task)\s*[:,-]?\s*(.+)$/.exec(f)) || (m = /^(?:nowe\s+)?zadanie\s*[:,-]\s*(.+)$/.exec(f)) || (m = /^(?:dodaj|dopisz|wpisz) do (?:planu|listy|zadan)\s*[:,-]?\s*(.+)$/.exec(f)) || (m = /^przypomnij mi(?:\s+(?:zeby|ze|o|abym))?\s+(.+)$/.exec(f))) {
    const t = parseTask(o.slice(f.length - m[1].length), ctx); if (!t.text) return {type: 'ask_what', what: 'zadanie'};
    return {type: 'add_task', ...t};
  }
  if ((m = /^(?:dodaj|zapisz|utworz|stworz|nowy)\s+nawyk\s*[:,-]?\s*(.+)$/.exec(f))) return {type: 'add_habit', name: cap(o.slice(f.length - m[1].length).trim()).slice(0, 60)};
  if ((m = /^(?:dodaj|zapisz|nowa|utworz)\s+(?:nowa\s+)?oferte\s*[:,-]?\s*(.+)$/.exec(f))) {
    const rest = f.slice(f.length - m[1].length), orest = o.slice(f.length - m[1].length), n = /^(.*?)\s+za\s+(.+)$/.exec(rest);
    if (!n) return {type: 'add_deal', name: cap(orest).slice(0, 100), buy: null, sell: null};
    const name = cap(orest.slice(0, n[1].length)).slice(0, 100), tail = numberWords(n[2]);
    const b = /^(\d+(?:\s\d{3})*(?:[.,]\d{1,2})?)/.exec(tail), s = /(?:sprzedam|sprzedaz|na sprzedaz|chce sprzedac|cel)\s*(?:za|po)?\s*(\d+(?:\s\d{3})*(?:[.,]\d{1,2})?)/.exec(tail);
    const num = x => x ? parseFloat(x[1].replace(/\s/g, '').replace(',', '.')) : null;
    return {type: 'add_deal', name, buy: num(b), sell: num(s)};
  }
  if ((m = /^(kupilem|kupilam|kupione|wystawilem|wystawilam|wystawione|sprzedalem|sprzedalam|sprzedane)(?:\s+(.*))?$/.exec(f))) {
    const kind = /^kup/.test(m[1]) ? 'bought' : /^wyst/.test(m[1]) ? 'listed' : 'sold', rest = m[2] || '', orest = o.slice(f.length - rest.length);
    const pr = findPrice(' ' + rest), name = (pr ? orest.slice(0, Math.max(0, pr.at - 1)) : orest).replace(/^(?:ten|ta|to|moj|moja|moje)\s+/i, '').trim();
    return {type: 'deal_' + kind, name, price: pr ? pr.price : null};
  }
  if ((m = /^(?:odrzuc|odrzucam|rezygnuje z)\s+(?:oferte\s+)?(.+)$/.exec(f))) return {type: 'deal_reject', name: o.slice(f.length - m[1].length).trim()};
  if ((m = /^(?:otworz|pokaz|przejdz do|idz do|wejdz w|wejdz do|przejdz na)\s+(?:strone\s+|zakladke\s+)?(dzis|alfred|alfreda|zarabiaj|zarabianie|zarabiania|oferty|ofert|boty|bot|bota|rynek|rynku|rynki|rynkow|okazje|okazji|strategie|strategii|kalkulator|kalkulatora|panel|panelu)$/.exec(f))) return {type: 'go', page: PAGES[m[1]], name: m[1]};
  return ask;
}

/* ---------------------------------------------------------------- tekst do czytania na głos */
function speakable(text) {
  let s = String(text == null ? '' : text);
  s = s.replace(/\n+\s*(?:#+\s*)?\**\s*(?:źródła|źródło|zrodla|zrodlo|sources)\b[\s\S]*$/i, '');   // lista źródeł na końcu odpowiedzi
  s = s.replace(/https?:\/\/\S+/g, '').replace(/\(\s*\)/g, '');
  s = s.replace(/[*_`#>~]+/g, '').replace(/^\s*[-•]\s+/gm, '').replace(/^\s*\d+[.)]\s+/gm, m => m);
  s = s.replace(/[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}\u{FE0F}]/gu, '');
  s = s.replace(/(\d)\s*zł(?![\p{L}\d])/gu, '$1 złotych').replace(/(\d)\s*%/g, '$1 procent').replace(/(\d)\s*min(?![\p{L}\d])\.?/gu, '$1 minut').replace(/(\d)\s*godz(?![\p{L}\d])\.?/gu, '$1 godzin');
  return s.replace(/[ \t]+/g, ' ').replace(/\n{2,}/g, '\n').trim();
}
/* dzieli tekst na kawałki do czytania (syntezatory przerywają długie wypowiedzi): zdania, a zbyt długie zdania przy przecinkach i spacjach */
function chunks(text, max) {
  max = max || 180; const out = [];
  for (const para of String(text).split(/\n+/)) {
    const sents = para.match(/[^.!?…]+(?:[.!?…]+|$)/g) || [para];
    for (let s of sents) {
      s = s.trim(); if (!s) continue;
      while (s.length > max) { let i = s.lastIndexOf(',', max); if (i < 40) i = s.lastIndexOf(' ', max); if (i < 20) i = max; out.push(s.slice(0, i + 1).trim()); s = s.slice(i + 1).trim(); }
      if (s) out.push(s);
    }
  }
  return out;
}
/* tekst do wyświetlenia w dymku: bez znaczników Markdown, z punktorami */
function plain(text) {
  return String(text == null ? '' : text).replace(/\*\*|__|`/g, '').replace(/^\s*#+\s*/gm, '').replace(/^\s*[*-]\s+/gm, '• ').replace(/\n{3,}/g, '\n\n').trim();
}

return {fold, prep, numberWords, findWhen, findDuration, parseTask, parseCommand, match, findPrice, speakable, chunks, plain, addDays, dow};
});
