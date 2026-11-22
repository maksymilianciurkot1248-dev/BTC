// Przypadki testowe parsera poleceń (intent.js). Uruchamia je tests/test_assistant.py.
const I = require(require('path').join(__dirname, '..', 'intent.js')); const assert = require('assert'); const ctx = {today: '2026-11-22'};   // niedziela
const P = t => I.parseCommand(t, ctx); let n = 0; const eq = (a, b, m) => { assert.deepStrictEqual(a, b, m); n++; };
const sub = (o, p, m) => { for (const k of Object.keys(p)) assert.deepStrictEqual(o[k], p[k], `${m}: pole ${k} = ${JSON.stringify(o[k])}, oczekiwano ${JSON.stringify(p[k])}; całość ${JSON.stringify(o)}`); n++; };
sub(P('dodaj zadanie raport kwartalny 45 minut jutro'), {type: 'add_task', text: 'Raport kwartalny', min: 45, date: '2026-11-23', top: false}, 'zadanie 1');
sub(P('Dodaj zadanie: zadzwonić do banku pilne'), {type: 'add_task', text: 'Zadzwonić do banku', top: true, min: null, date: null}, 'zadanie 2');
sub(P('dodaj zadanie kupić mleko na poniedziałek'), {text: 'Kupić mleko', date: '2026-11-23'}, 'poniedziałek');
sub(P('dodaj zadanie kupić mleko we wtorek'), {text: 'Kupić mleko', date: '2026-11-24'}, 'wtorek');
sub(P('dodaj zadanie kupić mleko w niedzielę'), {date: '2026-11-29'}, 'niedziela (dziś) = za tydzień');
sub(P('przypomnij mi żeby zadzwonić do mamy pojutrze'), {type: 'add_task', text: 'Zadzwonić do mamy', date: '2026-11-24'}, 'przypomnij');
sub(P('dodaj zadanie prezentacja 1,5 godziny'), {text: 'Prezentacja', min: 90}, '1,5 h');
sub(P('dodaj zadanie prezentacja 1 godzinę 30 minut'), {min: 90}, '1 h 30');
sub(P('dodaj zadanie prezentacja pół godziny'), {min: 30}, 'pół');
sub(P('dodaj zadanie prezentacja kwadrans'), {min: 15}, 'kwadrans');
sub(P('dodaj zadanie prezentacja 2 godziny'), {min: 120}, '2 h');
sub(P('dodaj zadanie prezentacja 600 minut'), {min: 240}, 'max 240');
sub(P('dodaj zadanie sprzątanie na 15.12'), {text: 'Sprzątanie', date: '2026-12-15'}, '15.12');
sub(P('dodaj zadanie sprzątanie na 3 grudnia'), {date: '2026-12-03'}, '3 grudnia');
sub(P('dodaj zadanie sprzątanie na 5 listopada'), {date: '2027-11-05'}, 'minęło: następny rok');
sub(P('Alfredzie, dodaj zadanie kupić chleb'), {type: 'add_task', text: 'Kupić chleb'}, 'zwrot do Alfreda');
sub(P('dodaj zadanie'), {type: 'ask_what', what: 'zadanie'}, 'brak treści');
sub(P('dodaj zadanie raport dziś ważne 20 min'), {text: 'Raport', date: '2026-11-22', top: true, min: 20}, 'dziś + ważne + 20 min');
for (const [t, ty] of [['co dalej', 'next'], ['Co teraz?', 'next'], ['hej alfred co dalej', 'next'], ['następny krok', 'next'], ['cicho', 'hush'], ['stop', 'hush'], ['dzięki', 'thanks'], ['dzień dobry', 'greet'], ['pomoc', 'help'], ['powtórz', 'repeat'], ['Alfredzie', 'wake'], ['przeczytaj briefing', 'briefing'], ['przegląd dnia', 'review'], ['później', 'later'], ['przełóż na jutro', 'later'], ['ile zarobiłem w tym miesiącu', 'money'], ['koniec skupienia', 'stopfocus'], ['', 'empty']]) sub(P(t), {type: ty}, t);
sub(P('co mam dziś'), {type: 'plan', day: 'today'}, 'plan'); sub(P('plan na jutro'), {type: 'plan', day: 'tomorrow'}, 'plan jutro');
sub(P('co dziś najważniejsze?'), {type: 'ask', text: 'co dziś najważniejsze'}, 'pytanie do AI'); sub(P('jak zacząć biznes z rowerami?'), {type: 'ask', text: 'jak zacząć biznes z rowerami'}, 'pytanie do AI 2');
sub(P('zrobione'), {type: 'done', query: ''}, 'zrobione'); sub(P('zrobiłem raport kwartalny'), {type: 'done', query: 'raport kwartalny'}, 'zrobiłem X'); sub(P('skończyłem trening'), {type: 'done', query: 'trening'}, 'skończyłem');
sub(P('start'), {type: 'start', min: null}, 'start'); sub(P('zaczynam 25 minut'), {type: 'start', min: 25}, 'start 25'); sub(P('pomodoro 30'), {type: 'start', min: 30}, 'pomodoro');
sub(P('kupiłem rower za 300'), {type: 'deal_bought', name: 'rower', price: 300}, 'kupiłem');
sub(P('kupiłem rower górski za trzysta złotych'), {type: 'deal_bought', name: 'rower górski', price: 300}, 'kupiłem słownie');
sub(P('sprzedałem wiertarkę za 1 200 zł'), {type: 'deal_sold', name: 'wiertarkę', price: 1200}, 'sprzedałem tysiąc');
sub(P('sprzedałem za 440'), {type: 'deal_sold', name: '', price: 440}, 'sprzedałem bez nazwy');
sub(P('sprzedałem'), {type: 'deal_sold', name: '', price: null}, 'sprzedałem samo');
sub(P('wystawiłem rower za 450 złotych'), {type: 'deal_listed', name: 'rower', price: 450}, 'wystawiłem');
sub(P('sprzedałem rower za dwa tysiące pięćset'), {type: 'deal_sold', price: 2500}, 'dwa tysiące pięćset');
sub(P('dodaj ofertę rower górski za 300 sprzedam za 450'), {type: 'add_deal', name: 'Rower górski', buy: 300, sell: 450}, 'oferta');
sub(P('dodaj ofertę wiertarka'), {type: 'add_deal', name: 'Wiertarka', buy: null, sell: null}, 'oferta sama nazwa');
sub(P('dodaj nawyk czytanie'), {type: 'add_habit', name: 'Czytanie'}, 'nawyk');
sub(P('otwórz zarabiaj'), {type: 'go', page: 'zarabiaj.html'}, 'go'); sub(P('przejdź do bota'), {type: 'go', page: 'bot.html'}, 'go bot');
// dopasowanie nazw
const items = ['Raport kwartalny', 'Zadzwonić do banku', 'Zakupy', 'Rower Kross Level 3.0'];
eq(I.match('raportu kwartalnego', items), ['Raport kwartalny'], 'match odmiana'); eq(I.match('bank', items), ['Zadzwonić do banku'], 'match bank/banku'); eq(I.match('rowerze', items), ['Rower Kross Level 3.0'], 'rower');
eq(I.match('coś innego', items), [], 'brak dopasowania'); eq(I.match('', items), [], 'pusty');
eq(I.numberWords('dwa tysiace piecset'), '2500', 'liczby'); eq(I.numberWords('trzysta'), '300', '300'); eq(I.numberWords('sto dwadziescia piec'), '125', '125'); eq(I.numberWords('tysiac dwiescie'), '1200', '1200'); eq(I.numberWords('kupilem rower'), 'kupilem rower', 'bez liczb');
// tekst do czytania
eq(I.speakable('**Plan:** zarobisz 120 zł, 45 min pracy, 30%. 🙂\n\nŹródła:\n- https://x.pl/a (12.11.2026)'), 'Plan: zarobisz 120 złotych, 45 minut pracy, 30 procent.', 'speakable');
eq(I.plain('## Tytuł\n**Pogrubione** tekst\n* punkt\n- drugi'), 'Tytuł\nPogrubione tekst\n• punkt\n• drugi', 'plain');
const long = 'To jest bardzo długie zdanie, które ma wiele członów, żeby sprawdzić dzielenie, ' + 'a ono dalej trwa i trwa bez końca, '.repeat(6) + 'koniec.'; const ch = I.chunks(long, 120); assert.ok(ch.length >= 3 && ch.every(c => c.length <= 121), JSON.stringify(ch.map(c => c.length))); n++;
eq(I.chunks('Pierwsze. Drugie zdanie! Trzecie?\nNowy akapit.'), ['Pierwsze.', 'Drugie zdanie!', 'Trzecie?', 'Nowy akapit.'], 'zdania');
console.log('ok', n);
