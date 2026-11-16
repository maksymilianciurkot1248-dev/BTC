# Jak działa algorytm (opis dokładny)

Stan na 16.11.2026. Dokument opisuje to, co robi kod, w kolejności wykonywania. Liczby pochodzą ze stałych w plikach podanych w nawiasach.
Żadna ze strategii nie ma dziś potwierdzonej przewagi; reguły poniżej decydują o tym, **kiedy** bot wolno handlować, a nie obiecują zysku.

## 1. Zasada nadrzędna

Są dwa boty na jednym koncie Bybit: **bot dzienny** (`btc_trader.py`, świece dzienne, przebieg co 5 minut) i **scalper** (`btc_scalp.py`, świece 5 min, obrót co 20 s).
Każda decyzja przechodzi przez te same cztery pytania, w tej kolejności. Pierwsza odpowiedź „nie” kończy sprawę.

| # | Pytanie | Kto odpowiada |
|---|---|---|
| 1 | Czy wolno w ogóle handlować? (tryb, pauza, plik STOP, wyłączniki konta) | limity konta, wspólne dla obu botów |
| 2 | Czy strategia ma dowód, że zarabia po kosztach? | bot dzienny: mapa przewagi i tryb cienia; scalper: bramka i hamulec |
| 3 | Czy jest sygnał? | strategia |
| 4 | Jak duża pozycja się mieści? | ryzyko na pozycję, dźwignia, ryzyko portfela |

Każda pozycja ma stop-loss ustawiony na giełdzie w chwili wejścia. Tego nie da się wyłączyć.

## 2. Limity konta (wspólne)

| Limit | Wartość domyślna | Skutek | Kogo zatrzymuje |
|---|---|---|---|
| Tryb `dryrun` | domyślny | żadnych zleceń, tylko symulacja | oba |
| Tryb `live` bez potwierdzenia w `trader.env` | — | program nie handluje | oba (osobne potwierdzenia) |
| Pauza w panelu, plik `STOP` (wyłącznik awaryjny) | — | brak nowych pozycji; nadzór otwartych trwa | oba, także zlecenia ze strony |
| Dźwignia | x2, twardy limit kodu x2 | suma nominałów ≤ dźwignia × kapitał | oba (scalper dodatkowo ≤ `SCALP_MAX_NOTIONAL_X`, domyślnie 1,0) |
| Kapitał do liczenia ryzyka | saldo, najwyżej `max_capital` z panelu | podstawa rozmiaru pozycji | oba |
| Dzienna strata konta | 2% kapitału | brak nowych pozycji do końca dnia UTC | oba |
| Strata tygodniowa konta | 6% | brak nowych pozycji do poniedziałku UTC | oba |
| Spadek kapitału od szczytu | 12% | brak nowych pozycji do Twojego odblokowania | oba |
| Seria strat z rzędu | 4, pauza 24 h | brak nowych pozycji | tylko bot dzienny; transakcje scalpera się nie liczą |
| Dzienna strata scalpera | 5R | przerwa do końca dnia UTC | tylko scalper |

Wyłączniki konta liczy bot dzienny (`btc_risk.py`) i zapisuje w `bot_risk.json`; scalper je czyta. Gdy stan jest starszy niż 30 minut
(bot dzienny nie działa), scalper go ignoruje i polega na własnym limicie dziennym.
W trybie live panel może limity tylko zaostrzyć względem `trader.env`.

Podział pozycji: oba boty nigdy nie mają pozycji na tym samym aktywie naraz. Bot dzienny nie wchodzi tam, gdzie jest jakakolwiek pozycja lub zlecenie;
scalper tak samo. Pozycji scalpera bot dzienny nie zamyka i nie liczy do swojego limitu liczby pozycji, ale liczy je do sumy nominałów i ryzyka portfela.

## 3. Bot dzienny: jeden przebieg

1. Wczytanie ustawień z panelu w twardych granicach (`btc_botcfg.BOUNDS`).
2. **Nadzór** (zawsze, także przy pauzie): saldo; zamknięte pozycje z 14 dni; wyłączniki; anulowanie własnych zleceń starszych niż 2 dni;
   własna pozycja bez stop-lossa lub z dźwignią ponad limit jest zamykana rynkowo (ręczna: tylko ostrzeżenie); prowadzenie otwartych transakcji (punkt 6).
3. Koniec przebiegu, jeśli: plik STOP, pauza, dzienna strata, aktywny wyłącznik.
4. **Wybór aktywów:** lista z panelu; przy włączonym automacie także aktywa dopuszczone przez mapę przewagi. Odpadają aktywa w kwarantannie
   (suma ostatnich 6 własnych transakcji gorsza niż −2 jednostki ryzyka: 7 dni przerwy) i aktywa zablokowane filtrem kontekstu (BTC pod SMA50, dzień publikacji makro), jeśli historia pokazała, że w takich warunkach wyniki są istotnie gorsze.
5. **Dla każdego aktywa**, do wyczerpania dziennego limitu zleceń, kolejno:
   suma pozycji w limicie dźwigni → brak pozycji na aktywie → wolne miejsce w limicie liczby pozycji → brak otwartych zleceń na aktywie → filtr kontekstu →
   plan strategii → plan ważny (kierunek long/short, nie wygasł, cena jeszcze nie uciekła, poziomy spójne) → kierunek włączony w panelu → to zlecenie nie było już złożone →
   obrót 24 h ≥ 20 mln USDT → instrument dostępny → **rozmiar** → zlecenie.
   - Rozmiar = mniejsza z: (kapitał × ryzyko% ÷ odległość do stopa) oraz (kapitał × dźwignia ÷ cena). Potem mnożniki: zespół sygnałów 0,2–1,0 (zmienność, pewność, funding),
     ×0,5 dla aktywa „utrzymanego” przez histerezę mapy przewagi. Potem ograniczenie do wolnego nominału i do **ryzyka portfela**: ryzyko skorelowane wszystkich pozycji ≤ 3% kapitału
     (nowa pozycja jest zmniejszana najwyżej do 30% planu; jeśli i tak się nie mieści, nie powstaje). Zaokrąglenie zawsze w dół.
   - Zlecenie: limit po cenie wejścia (wybicie: warunkowe, 0,1% za poziomem), ze stop-lossem. W trybie zatwierdzania zamiast zlecenia powstaje wniosek w panelu.
6. **Wyjście** (tryb `staged`, domyślny, zgodny z testem wstecznym): po wypełnieniu dwa zlecenia limit reduce-only (TP1 na połowę, TP2 na resztę);
   po TP1 stop przechodzi na cenę wejścia; po 7 dniach reszta jest zamykana rynkowo. Wynik w R trafia do `bot_trades.jsonl`.

### Strategie bota dziennego
- **Klasyczna** (`btc_analysis.plan_at`): w teście na 12 aktywach traci istotnie (−0,13R na transakcję). Zostaje do porównań.
- **Zespół sygnałów** (`btc_decision.py`): sześć rodzin sygnałów ocenianych −1..+1, ważonych według reżimu rynku. Long przy ocenie ≥ +0,30, short przy ≤ −0,38,
  wymagana zgodność rodzin ≥ 60% i pewność ≥ 55%; stop 1,2–1,6 ATR, cele 1,2–1,6R i 1,8–3,0R; stop szerszy niż 12% ceny: czekamy. Aktywny wariant: **v2**.
  Wynik historyczny v2 (+0,22R) jest zawyżony, bo wariant wybierałem na tej samej historii.

### Dowód dla bota dziennego
- **Mapa przewagi** (`btc_edge.py`, codziennie): ta sama funkcja planu na historii, po kosztach. Wejście aktywa: ≥ 30 transakcji, średnia ≥ +0,05R, p skorygowane ≤ 0,05
  (korekta wspólna dla obu strategii), obie połowy historii i ostatnie 12 miesięcy nieujemne. Wyjście dopiero przy p > 0,20 albo średniej ≤ 0 (histereza).
- **Tryb cienia** (`btc_shadow.py`): dziennik propozycji od dnia instalacji, bez zleceń. To jedyny wynik poza próbą. Wnioski po ≥ 30 transakcjach.
- **Laboratorium** (`btc_lab.py`): warianty zespołu w cieniu; awans wymaga 30 transakcji, 12 tygodni i wygranej z aktywnym wariantem (p ≤ 0,025). Wariant zmieniasz Ty.

## 4. Scalper

### Obrót
Co 20 s: stan konta i nadzór otwartych pozycji. Raz na zamkniętą świecę 5 min (najwcześniej 4 s po jej zamknięciu), dla każdego aktywa:
świece → odczyt arkusza zleceń → cień → bramka → ewentualne wejście. Wejście tylko wtedy, gdy od zamknięcia świecy minęło mniej niż 90 s.
Awaria konta nie zatrzymuje cienia: symulacja działa dalej, zleceń nie ma, a powód widać w panelu.

### Strategie (sygnał liczony na zamknięciu świecy)
| Strategia | Wejście | Stop | Cel | Limit czasu |
|---|---|---|---|---|
| `mr` powrót do średniej | cena odchylona o ponad 2,2 odchylenia standardowego od średniej z 40 świec i RSI(6) < 22 (long) lub > 78 (short); średnia dalej niż stop | większa z: 1,5 × ATR(14), 0,30% ceny | średnia, najwyżej 2R | 2 h |
| `bo` wybicie | zamknięcie poza zakresem 36 świec, zakres ≤ 6 ATR, obrót ≥ 1,8 × średni | większa z: 1,2 × ATR, 0,30% | 2R | 3 h |
| `ob` nierównowaga zleceń | średnia z dwóch ostatnich odczytów arkusza > 0,35 (oba > 0,2), świeca w tę stronę i powyżej średniej z 20; odwrotnie dla shorta | większa z: 1,0 × ATR, 0,30% | 1,5R | 1 h |
| `fl` przepływ | wskaźnik przepływu (punkt 5) ≥ +1 (long) lub ≤ −1 (short); najwyżej jedno wejście na godzinowy odczyt | większa z: 3 × ATR godzinowy, 0,60% | 2R | 12 h |

Nierównowaga arkusza = (kupno − sprzedaż) ÷ suma, w odległości 0,1% od środka. Odczyt starszy niż ok. 11 minut nie jest łączony z bieżącym.

### Cień
Każda strategia prowadzi na każdym aktywie najwyżej jedną symulowaną transakcję. Wejście po cenie zamknięcia świecy z sygnałem. Na każdej następnej świecy:
stop, potem cel (gdy świeca obejmuje oba, liczy się stop), potem limit czasu (cena zamknięcia). Koszt 0,13% nominału, czyli 0,13% × cena ÷ odległość stopa w R
(przy minimalnym stopie 0,43R). Przerwa w danych dłuższa niż 15 minut zamyka otwarte symulacje bez wyniku.
Przy pierwszym uruchomieniu `mr` i `bo` dostają ok. 10 dni historii; `ob` i `fl` zbierają wyniki tylko na żywo.

### Bramka: kiedy strategia składa zlecenia
Okno: ostatnie 300 transakcji strategii z cienia, nie starsze niż 30 dni. Bramka **otwiera się**, gdy jednocześnie:
- transakcji ≥ 30,
- średnia ≥ +0,05R po kosztach,
- t ≥ 2, gdzie t = średnia ÷ jej błąd, a błąd liczony jest tak, że transakcje zamknięte w tej samej godzinie są jedną obserwacją (aktywa poruszają się razem).

Otwarta bramka **zamyka się**, gdy średnia spadnie poniżej 0 albo transakcji w oknie będzie mniej niż 30. Ponowne otwarcie wymaga pełnego dowodu.

**Hamulec:** z ostatnich 30 dni bierzemy do 100 prawdziwych transakcji strategii. Jeśli jest ich ≥ 10, średnia jest ujemna i t ≤ −2, strategia nie składa zleceń,
nawet przy otwartej bramce. Prawdziwy wynik ma pierwszeństwo przed symulacją.

**Weto pary:** strategia nie wchodzi na aktywie, na którym sama ma w cieniu ≥ 20 transakcji ze średnią poniżej −0,10R.

**Rozpoznanie** (tylko demo i testnet, `SCALP_EXPLORE`): gdy żadna bramka nie jest otwarta, handluje jedna strategia o najlepszej średniej spośród tych,
które mają ≥ 10 transakcji i średnią ≥ −0,10R; z połową ryzyka, najwyżej 6 transakcji na dobę. Cel: sprawdzić prawdziwe wykonanie zleceń, nie zarabiać.

Dlaczego t, a nie sama średnia (`docs/badania_bramka`): na 30 dniach historii strategia tracąca 0,36R na transakcję miała średnią ≥ +0,05R w 17% okien po 30 transakcji.
Z warunkiem t ≥ 2 przeszła w 0,7% okien. Ceną jest czułość: strategia zarabiająca naprawdę +0,10R potrzebuje pełnych 300 transakcji, żeby przejść w ok. 1 przypadku na 4.

### Wejście i wyjście
- Warunki: scalper włączony w panelu, brak blokad z punktu 2, mniej niż 30 transakcji dziś, brak pozycji i zleceń na aktywie, mniej niż 3 pozycje scalpera,
  15 minut przerwy na aktywie po stracie.
- Rozmiar = najmniejsza z: (kapitał × 0,2% × mnożnik ÷ odległość stopa), wolny nominał scalpera, połowa limitu nominału. Mnożnik 1,0 przez bramkę, 0,5 w rozpoznaniu.
- Zlecenie rynkowe ze stop-lossem i celem na giełdzie. Poziomy liczone od ceny zamknięcia świecy z sygnałem.
- Po limicie czasu pozycja jest zamykana rynkowo. Wynik (z `closed-pnl` giełdy) w R = zysk ÷ (ilość × odległość stopa) trafia do `scalp_trades.jsonl`.
  Na wynik czekamy do 3 minut; dopiero potem wpis powstaje bez R.

## 5. Wskaźnik przepływu (`btc_flow.py`)

Dane godzinowe z publicznego API OKX, odświeżane co 10 minut. Cztery składniki, każdy jako odchylenie od własnej normy z 7 dni (z-score, obcięty do ±3):
udział kupna w zleceniach rynkowych z 4 h (+), logarytm stosunku kont long/short (+), premia kontraktu do indeksu (−), zmiana ceny z 4 h (−).
Wskaźnik = średnia czterech; brak któregokolwiek składnika = brak wskaźnika. |wskaźnik| ≥ 1 to wyraźne wskazanie. Odczyt starszy niż 2,5 h nie jest wskazaniem.
Każde źródło jest uzupełniane od swojej ostatniej godziny, więc luka po awarii jednego źródła zasklepia się sama.

Status: eksperyment. Na 53 dniach wypadał dodatnio, na 2 latach dwa sprawdzalne składniki nie wykazały przewagi (`docs/badania_przeplyw`).
Panel liczy trafność osobno dla wskazań od 16.11.2026; strategia `fl` handluje tylko po przejściu bramki z punktu 4.

## 6. Tryby

| Tryb | Zlecenia | Uwagi |
|---|---|---|
| `dryrun` | nie | pełna ścieżka decyzji i cień, bez wysyłania |
| `testnet`, `demo` | tak, na koncie testowym | scalper może działać w rozpoznaniu |
| `live` | tak, po potwierdzeniu w `trader.env` | bez rozpoznania; panel tylko zaostrza limity; automat wyboru aktywów wymaga `TRADER_AUTO_LIVE=yes` |

## 7. Czego algorytm nie zapewnia

- **Zysku.** Bramka i mapa przewagi ograniczają handel strategiami bez dowodu. Gdy żadna dowodu nie ma, bot nie handluje, i to jest poprawny wynik.
- **Zgodności cienia z rzeczywistością.** Cień nie widzi poślizgu, odrzuconych zleceń ani opóźnień. Dlatego istnieje hamulec z prawdziwych transakcji i zestawienie w laboratorium.
- **Ochrony przed luką cenową.** Stop-loss jest zleceniem rynkowym po aktywacji; przy gwałtownym ruchu strata może przekroczyć 1R.
- **Pełnej separacji botów w ułamku sekundy.** Jeśli oba boty zdecydują o tym samym aktywie w tych samych sekundach, drugie zlecenie może dołożyć do pozycji pierwszego. Oba sprawdzają stan konta tuż przed decyzją, więc okno ma kilka sekund.
- **Sprawdzenia na prawdziwym Bybit.** Testy działają na atrapach giełdy. Ścieżkę zleceń weryfikuje `btc_selftest.py` na demo, a zachowanie scalpera pierwsze dni pracy na demo.
