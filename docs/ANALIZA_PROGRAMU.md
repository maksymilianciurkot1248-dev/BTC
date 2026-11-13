# Analiza całego programu i elementy do rozbudowy

Stan na 8.11.2026. Analiza oparta na kodzie (ok. 11,2 tys. linii Pythona, ok. 6,2 tys. linii stron WWW, 22 pliki testów, 24 usługi i timery systemd), na wynikach testów i na przeglądzie przepływów. Rzeczy, których nie mogłem sprawdzić na prawdziwym Bybit i serwerze, oznaczam wprost.

## 1. Architektura w skrócie

| Warstwa | Elementy | Rola |
|---|---|---|
| Dane | `btc_collect` (29 źródeł), `btc_stream` (WebSocket), `btc_live`, `btc_net` (HTTP z bezpiecznikiem) | SQLite `btc_data.db`, pliki JSON |
| Analiza | `btc_analysis` (plan klasyczny), `btc_decision` (zespół sygnałów), `btc_research`, `btc_situations`, `btc_strategies`, `btc_opportunities` | badania, skaner, portfele |
| Uczenie | `btc_edge`, `btc_learn` | codzienna mapa przewagi, filtry, kwarantanna |
| Wykonanie | `btc_trader` (bot co 5 min), `btc_exec` (zlecenia ze strony), `btc_account` (konto tylko do odczytu) | zlecenia na Bybit |
| Sterowanie | `btc_api` (18 adresów), `btc_botcfg` (ustawienia, wnioski, kolejka), 9 stron WWW | panel i ustawienia |
| Operacje | `setup_vps.sh`, `install_bot.sh`, nginx, ufw, fail2ban, `btc_doctor` | wdrożenie |

Zasada, która dobrze się sprawdziła: **strona nie ma kluczy**, a zlecenia składa osobny proces po sprawdzeniu limitów (stop-loss, dźwignia do x2, wolny limit kapitału).

## 2. Mocne strony
- Biblioteka standardowa, brak ciężkich zależności i kompilacji; łatwe wdrożenie.
- Spójny model walidacji: ta sama funkcja planu idzie przez test wsteczny z kosztami, mapę przewagi (FDR) i bota.
- Wielowarstwowe zabezpieczenia: hasło i HTTPS, nagłówek CSRF, limity w kodzie, kwarantanna, tryby dryrun/demo/live, zatwierdzanie pozycji.
- Dobre pokrycie testami logiki (390 asercji), w tym przypadki brzegowe zleceń i limitów.
- Przejrzystość: dziennik zdarzeń bota, dziennik nauki, uzasadnienie każdej decyzji.

## 3. Znaleziska (od najpoważniejszych)

### Krytyczne (dotyczą prawdziwych pieniędzy)
1. **Naprawione dziś: bot zamykał i anulował rzeczy, które nie były jego.** Nadzór (`supervise`) zamykał rynkowo każdą pozycję konta bez stop-lossa lub z dźwignią powyżej limitu oraz anulował każde zlecenie starsze niż 2 dni. Na prawdziwym koncie dotknęłoby to Twoich ręcznych pozycji i zleceń z aplikacji Bybit. Teraz bot nadzoruje tylko pozycje na aktywach, na których sam postawił zlecenie (zapamiętuje je 30 dni), a anuluje tylko zlecenia z własnym identyfikatorem. Ręczne pozycje bez stopa dostają jedno ostrzeżenie dziennie. Dawne zachowanie włączysz przez `TRADER_SUPERVISE=all` w `trader.env`. Testy dodane.
2. **Test wsteczny nie odpowiadał wykonaniu na żywo (naprawione 8.11, do sprawdzenia na demo).** Symulator zakłada: zamknięcie 50% na TP1, przesunięcie stopa na wejście, reszta do TP2 i zamknięcie po 7 dniach. Bot składa zlecenie z jednym take-profitem na TP1 dla całej pozycji, bez przesuwania stopa, bez TP2 i bez zamknięcia po czasie. Wynik mapy przewagi i statystyk dotyczy więc innej strategii niż ta, którą bot realnie handluje. To największa luka merytoryczna. Bot w trybie `staged` prowadzi teraz transakcję jak symulator (cele częściowe, stop na wejście, zamknięcie po 7 dniach); zostały różnice: stop czasowy liczony od wypełnienia, a nie od zamknięcia świecy, oraz kolejność zdarzeń w obrębie świecy.
3. **Brak dowodu przewagi.** Wcześniejsze badania pokazały brak przewagi prostej reguły „kup dołek” na krypto, a mapa przewagi na danych losowych odrzuca wszystkie aktywa. Do czasu miesięcy wyników w demo trafia to na prawdziwe pieniądze bez udokumentowanej skuteczności. Zasada kapitału „tylko tyle, ile możesz stracić” obowiązuje.

### Wysokie
4. **Obciążenie wyborem aktywów.** Wszechświat to dzisiejsze najpłynniejsze kontrakty, więc aktywa, które upadły lub wypadły z rynku, nie są testowane. To zawyża wyniki (błąd przetrwania). Test obejmuje tylko aktywa istniejące dziś.
5. **Wielokrotne testowanie w czasie (częściowo naprawione 8.11: histereza, wspólna korekta FDR, historia decyzji; zostaje problem wielu dni testów, który rozwiązuje tylko tryb cienia).** Mapa przewagi jest liczona codziennie, a aktywo może raz wejść, raz wypaść zależnie od drobnych zmian p-wartości. Korekta FDR działa w obrębie jednego dnia i jednej strategii, nie w czasie ani między dwiema strategiami. Brakuje histerezy (wejście przy q ≤ 0,05, wyjście dopiero przy q > 0,20).
6. **Koszty niepełne (naprawione 10.11: funding longa 0,03% na dobę, poślizg stopa 0,05%, luki na otwarciu).** Test uwzględnia prowizje (0,075% na rundę), ale nie funding ani poślizg stopa rynkowego przy gwałtownych ruchach. Funding przy pozycjach trzymanych kilka dni potrafi zjeść część przewagi.
7. **Korelacja pozycji (naprawione 8.11: limit ryzyka skorelowanego i wyłączniki).** Kryptowaluty są silnie skorelowane, więc trzy pozycje po 1% ryzyka to w praktyce jedna większa pozycja (ok. 3%). Limit pozycji nie uwzględnia korelacji.
8. **Dane tylko z mocków.** Wszystkie testy działają na atrapach giełdy. Szczegóły odpowiedzi Bybit (np. `closed-pnl` bez symbolu, `positionValue`, `tpslMode`, `reduceOnly` przy zleceniach rynkowych) nie były sprawdzone na prawdziwym API. Pierwszy tydzień na demo jest testem integracji.
9. **Ręczna pozycja a dzienny limit straty.** Dzienna strata liczy też zamknięte pozycje ręczne. Przekroczenie limitu przez ręczny handel wstrzyma bota. To bywa pożądane, ale warto, żeby było świadomym ustawieniem.

### Średnie
10. **Podział konfiguracji.** Klucze i ustawienia są w czterech miejscach: `trader.env`, `.env`, `bot_settings.json` i panel `/klucze/`, a z innej rozmowy doszły `btc-keys`, `btc-ibkr`, `btc-notify` i ręczna łatka `/api/ibkr`. Aktualizacje już raz skasowały łatkę. Rozwiązanie częściowe: katalog `api_ext`. Brak jednego źródła prawdy.
11. **Brak monitoringu usług.** Nikt nie dostanie powiadomienia, gdy padnie `btc-exec`, `btc-trader` albo kolektor. `btc_doctor` działa tylko ręcznie.
12. **Brak kopii zapasowych** bazy SQLite, plików stanu (`trader_state.json`, `bot_*.json`) i konfiguracji. Awaria dysku oznacza utratę historii bota.
13. **Testy poza paczką.** 22 pliki testów leżą w katalogu roboczym sesji, nie w repozytorium ani w paczce, bez CI. Każda aktualizacja jest sprawdzana tylko ręcznie.
14. **Bezpieczeństwo strony.** Jedno hasło (basic auth) bez drugiego składnika; polityka CSP dopuszcza `unsafe-inline` dla skryptów (strony mają skrypty wbudowane); limit zapytań nginx jest ogólny, a nie osobny dla zleceń. Zlecenia ze strony mają dodatkowo zaporę `TRADER_WEB_ORDERS=yes`.
15. **Monolit.** `btc_analysis.py` ma 3552 linie, a funkcja `render` 363 linie; `index.html` 2482 linie z wbudowanym kodem. Utrudnia to zmiany i testy.
16. **Niezablokowane wersje** bibliotek (`pip install` bez wersji) i brak lintera oraz typów.

### Niskie
17. Wszystko na świecach dziennych: brak wyjść wewnątrzdobowych i śledzenia stopa.
18. Brak eksportu do podatków (PIT-38) i dziennika transakcji z notatkami.
19. Powiadomienia tylko przez Telegram (opcjonalny), brak powiadomień przeglądarkowych.

## 4. Elementy do rozbudowy (priorytety)

Rozmiar: S = kilka godzin, M = jedna sesja, L = kilka sesji.

### P0: wiarygodność wykonania (przed zwiększaniem kapitału)
| # | Zadanie | Rozm. | Kryterium ukończenia |
|---|---|---|---|
| 1 | ✅ **Zrównać wykonanie z testem (zrobione, wymaga potwierdzenia na demo):** częściowy TP (50% na TP1), przesunięcie stopa na wejście po TP1, TP2 dla reszty i zamknięcie po 7 dniach (w `supervise`); albo odwrotnie, uprościć symulator do jednego TP | L | Różnica średniego R między testem a wykonaniem demo w granicach błędu |
| 2 | **Tryb cienia (shadow):** każdy przebieg zapisuje decyzje obu strategii i ich późniejszy wynik (bez zleceń), co daje wyniki na żywo poza próbą | M | Tabela „skuteczność na żywo” obok wyników historycznych |
| 3 | ✅ **Test integracji na testnecie (zrobione, do uruchomienia na serwerze):** skrypt, który składa i zamyka zlecenia kontrolne na testnecie i sprawdza odpowiedzi Bybit (stop, TP, reduceOnly, closed-pnl) | M | Zielony test przed każdą aktualizacją tradera |

### P1: statystyka i ryzyko
| # | Zadanie | Rozm. |
|---|---|---|
| 4 | ✅ Histereza mapy przewagi (wejście p ≤ 0,05, wyjście p > 0,20, aktywa „utrzymane” z połową rozmiaru), wspólna korekta FDR dla dwóch strategii, historia decyzji „dopuszczone/wycofane” (zrobione 8.11) | M |
| 5 | Wszechświat bez błędu przetrwania: dołączyć wycofane kontrakty (jeśli dostępne) albo oznaczyć wynik jako zawyżony | M |
| 6 | ✅ Funding i poślizg w kosztach testu (zrobione 10.11, stałe założenia); zostaje: koszty zależne od płynności i prawdziwa historia fundingu | M |
| 7 | ✅ Limit ryzyka portfela z korelacją (zrobione 8.11) (efektywna liczba niezależnych pozycji) i globalny wyłącznik: spadek kapitału tygodniowy/miesięczny, seria N strat z rzędu | M |
| 8 | Nowe cechy do zespołu sygnałów z danych, które już zbieramy (funding, OI, likwidacje, CVD, sentyment, COT): każda wchodzi dopiero po teście walk-forward z FDR | L |

### P2: operacje i bezpieczeństwo
| # | Zadanie | Rozm. |
|---|---|---|
| 9 | Monitoring usług i danych (świeżość źródeł, brak odpowiedzi usług) z alertem na Telegram i czerwoną kropką w panelu | S–M |
| 10 | Kopie zapasowe baz i plików stanu (codziennie, poza serwerem) wraz z instrukcją odtwarzania | S |
| 11 | Testy do repozytorium, CI (uruchamianie przy zmianie), linter, przypięte wersje bibliotek | M |
| 12 | Jedno źródło konfiguracji i jeden mechanizm kluczy (decyzja: panel `/klucze/` albo `btc-keys`), rozszerzenia przez `api_ext` | M |
| 13 | Drugi składnik logowania (TOTP) albo dostęp tylko przez VPN/Tailscale; osobny limit zapytań dla `/api/bot/order`; CSP bez `unsafe-inline` (skrypty do plików) | M |

### P3: funkcje i wygoda
| # | Zadanie | Rozm. |
|---|---|---|
| 14 | Moduł brokera dla akcji i walut (IBKR) z tymi samymi zabezpieczeniami; most „skaner → bot” | L |
| 15 | Zlecenia zaawansowane na stronie: przesunięcie stopa, częściowe zamknięcie, OCO | M |
| 16 | Dziennik transakcji z notatkami, eksport CSV do PIT-38, raport miesięczny | M |
| 17 | Powiadomienia przeglądarkowe (PWA) i komendy Telegram (`/stop`, `/status`) | M |
| 18 | Podział `btc_analysis.py` na moduły i wyniesienie skryptów stron do plików | L |
| 19 | Śledzenie stopa i wyjścia wewnątrzdobowe (świece 1 h/4 h) po udowodnieniu przewagi na dziennych | L |

## 5. Rekomendowana kolejność
1. P0.1 i P0.3 (wykonanie zgodne z testem, test integracji na testnecie), bo od tego zależy wiarygodność każdego wyniku.
2. P2.9, P2.10, P2.12 (monitoring, kopie, jedna konfiguracja): małe, a zmniejszają ryzyko awarii.
3. P0.2 i P1.7 (tryb cienia, ryzyko portfela), żeby po kilku tygodniach mieć dane z żywego rynku.
4. P1.4–P1.8, jeśli wyniki cienia będą obiecujące.
5. P3 według potrzeb. Moduł IBKR dopiero po stabilnym demo na Bybit.

## 6. Czego nie robić
- Nie zwiększać kapitału ani dźwigni, dopóki nie ma kilku tygodni wyników na żywo po kosztach.
- Nie dodawać kolejnych wskaźników „na oko”: każda nowa cecha musi przejść ten sam test poza próbą.
- Nie uruchamiać dwóch mechanizmów kluczy naraz.
- Nie włączać automatycznego wyboru aktywów w trybie live bez własnej obserwacji mapy przewagi.

## 7. Analiza algorytmu handlu na prawdziwych danych (10.11.2026)

Dane: świece dzienne 12 aktywów (BTC, ETH, SOL, XRP, BNB, DOGE, ADA, LINK, AVAX, DOT, LTC, TRX) z lat 2017–2026, ok. 30 tys. dni. Każdy wynik to ten sam kod, którego używa bot, mapa przewagi i tryb cienia. Wyniki w R (wielokrotność ryzyka do stopa), po kosztach.

### Sprawność (poprawność)
| Znalezisko | Skutek | Stan |
|---|---|---|
| Plan klasyczny zaokrąglał ceny do 0,1 (pod BTC) | Na aktywach tańszych niż ok. 15 USD wejście równało się stopowi: dzielenie przez zero na 5 z 12 aktywów. Błąd przerywał całą dzienną naukę (brak mapy przewagi, cienia i laboratorium), a na pozostałych tanich aktywach przesuwał poziomy | naprawione: 5 cyfr znaczących; dla BTC i ETH bez zmian |
| Propozycja z zerowym ryzykiem blokowała dziennik | Dziennik cienia aktywa stawał na zawsze | naprawione: taka propozycja jest zamykana bez wyniku i już nie powstaje |
| Koszty bez fundingu i poślizgu | Wyniki zawyżone o ok. 0,015R na transakcję | naprawione |
| „Pewność” planu klasycznego | Działa odwrotnie: przy pewności 75+ wynik −0,26R, przy 55–74 ok. −0,03R. W zespole sygnałów nie rozróżnia niczego | opisane; nie należy się nią kierować |
| Cele TP1/TP2 w zespole sygnałów | Średni stop to 9% ceny, cel 11–14% w 7 dni; 69% transakcji kończy stop czasowy, 27% stop-loss, 4% cele | opisane; zmiana stopów, celów i czasu trzymania nie poprawia wyniku |
| Wejście limit w planie klasycznym | Z 4800 propozycji wypełnia się 15%; 1449 „przegapionych” to te, które poszły w dobrą stronę bez nas | opisane |
| Mapa przewagi ocenia każde aktywo osobno | Strategia z rzadkimi transakcjami na aktywo (v2: 2–39) nigdy nie przejdzie progu 30 transakcji, choć łącznie ma 218 | opisane; dowód łączny pokazuje laboratorium |

### Wydajność
| Pomiar (12 aktywów) | Przed | Po |
|---|---|---|
| Test planu klasycznego | 3,3 s | 2,5 s |
| Test zespołu sygnałów | 2,8 s | 1,7 s |
| Cztery warianty zespołu | 11,2 s | 3,7 s |
| Cała dzienna nauka (mapa, cień, laboratorium) | przerywana błędem | 12 s pierwszego dnia, 5,5 s w kolejne |

Wyniki przed i po optymalizacji są identyczne co do propozycji (porównano 5555 propozycji w 36 zestawach). Zmiany: oceny rodzin liczone raz i wspólne dla wariantów, okno kroczące percentyla zmienności, wyszukiwanie binarne swingów, teksty objaśnień pomijane w testach.

### Efektywność
| Strategia | Transakcje | Śr. R | Suma R | Przed 2022 | Od 2022 | Aktywa na plusie |
|---|---|---|---|---|---|---|
| Klasyczna (plan) | 723 | −0,13 | −96,5 | −0,19 | −0,12 | 1 z 12 |
| Zespół v1 (dotychczasowy) | 358 | +0,00 | +1,5 | +0,16 | −0,04 | 7 z 12 |
| Zespół bez siły względnej | 422 | +0,04 | +15,8 | +0,16 | +0,01 | 9 z 12 |
| **Zespół v2** (siła względem BTC kontrariańsko) | 218 | **+0,22** | +47,6 | +0,52 | +0,14 | 11 z 12 |

- Strategia klasyczna traci istotnie (t tygodniowe −2,4). Nie ma powodu jej używać.
- Rodziny trendowe (momentum, wybicie, wolumen) miały moc prognostyczną do 2021 r. (IC 0,10–0,18), od 2022 r. ok. zera. To wyjaśnia, dlaczego v1 zarabiał do 2021 r. i przestał.
- „Siła względem BTC” działała odwrotnie niż w v1: aktywo słabsze od BTC przez 30 dni potem nadrabia. IC −0,08 w całej historii (t −2,6 po grupowaniu po blokach czasu), ten sam znak w latach 2020–21, 2022–23 i ostatnich dwóch.
- v2 różni się od v1 tylko znakiem tej jednej rodziny. Jest lepszy od v1 w każdym z 9 lat, dodatni w 7 z 9, a zależność od wagi rodziny jest gładka (0 → +0,04R, 0,05 → +0,10R, 0,10 → +0,17R, 0,20 i więcej → ok. +0,23R).
- Test ostrożny: grupowanie tygodniowe t = +2,3 (p 0,01); od 2022 r. p 0,07; bootstrap blokowy po miesiącach dla altów: średnia +0,24R, przedział 5–95%: +0,07 do +0,43R.

### Czego to nie dowodzi
- v2 wybrałem na tej samej historii, na której go oceniam, po sprawdzeniu ok. 20 ustawień i 12 cech. Po takiej liczbie prób p rzędu 0,01 nie jest dowodem. Rozstrzygnie dopiero tryb cienia (wyniki od dnia instalacji).
- 12 aktywów to dzisiejsi wygrani. Strategia kontrariańska korzysta na tym, że w próbie nie ma aktywów, które upadły (błąd przetrwania, P1.5).
- 218 transakcji w 8 lat to ok. 26 rocznie na 12 aktywach. Wniosek o awans wymaga 30 transakcji z cienia, czyli realnie kilku miesięcy.
- Funding i poślizg to stałe założenia, a nie historia z giełdy.

### Co zostało zmienione
1. Naprawione błędy z tabeli „Sprawność”.
2. Silnik zespołu sygnałów jest sparametryzowany (`btc_decision.DEFAULTS`): każdy wariant idzie przez ten sam kod w teście, cieniu i u tradera.
3. Domyślnym wariantem zespołu jest v2; v1 i wariant kontrolny zostają jako pretendenci w cieniu.
4. Laboratorium wariantów (`btc_lab.py`): diagnoza sygnałów, test historyczny, porównanie z cienia, wnioski o awans, zestawienie realnych transakcji z symulatorem. Opis w instrukcji.

### Co dalej
- Po kilku tygodniach: sprawdzić w laboratorium wyniki z cienia i zestawienie „wykonanie na żywo”.
- Automat wyboru aktywów oparty na dowodzie łącznym z cienia (dziś wymaga dowodu dla każdego aktywa osobno, więc przy v2 nie wybierze niczego).
- P1.5 (aktywa wycofane z rynku) i prawdziwa historia fundingu w kosztach.

