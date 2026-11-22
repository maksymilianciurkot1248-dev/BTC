# Plan działania

Stan na 16.11.2026. Zastępuje `PLAN_ROZBUDOWY.md` i rozdziały 4–5 z `ANALIZA_PROGRAMU.md` (zostają jako historia).
Jak działa sam algorytm: `ALGORYTM.md`.

## 1. Gdzie jesteśmy

- Program działa na demo: bot dzienny, scalper, nauka, laboratorium, panel, asystent AI, aktualizacja z repozytorium.
- **Żadna strategia nie ma potwierdzonej przewagi.**
  - Bot dzienny, zespół sygnałów v2: +0,22R na historii, ale wariant wybierałem na tej samej historii. Rozstrzygnie cień (od 10.11).
  - Scalper `mr` i `bo`: na 30 dniach −0,36R i −0,49R na transakcję, wynik istotny (t −5,6 i −6,4).
  - Scalper `ob` i `fl`, wskaźnik przepływu: brak danych poza próbą; liczą się od wdrożenia.
- Głównym przeciwnikiem scalpera są koszty: 0,13% nominału to 0,3–0,4R na transakcję.
- Nowego kodu z ostatnich dni nie sprawdzałem na Twoim serwerze ani na prawdziwym Bybit.

Wniosek: przez najbliższe tygodnie najwięcej daje **zbieranie uczciwych wyników i niezawodność**, a nie kolejne strategie.

## 2. Zrobione 16.11 (ta aktualizacja)

| Usterka | Skutek przed poprawką | Poprawka |
|---|---|---|
| Bramka scalpera patrzyła tylko na średnią | strategia tracąca 0,36R przechodziła w 17% okien po 30 transakcji; strategia bez przewagi w 25–40% | wymagane t ≥ 2 z poprawką na skupiska, okno 300 transakcji z 30 dni, histereza |
| Brak sprzężenia z prawdziwymi wynikami | cień dodatni, realne straty: bot handlowałby dalej | hamulec: realne t ≤ −2 zatrzymuje strategię |
| Seria strat scalpera liczyła się botowi dziennemu | 4 zwykłe straty scalpera wstrzymywały bota dziennego na 24 h; możliwa też fałszywa kwarantanna aktywa | seria strat, kwarantanna, statystyki i dziennik bota dziennego bez transakcji scalpera |
| Scalper nie widział dźwigni i kapitału z panelu | po obniżeniu dźwigni w panelu bot dzienny zamknąłby pozycję scalpera jako „niezgodną z zasadami” i wstrzymał dzień | scalper stosuje limity z panelu; bot dzienny nie zamyka pozycji scalpera |
| Wyłączniki konta nie obowiązywały scalpera | przy stracie tygodniowej lub spadku kapitału scalper handlowałby dalej; plik STOP go nie zatrzymywał | dzienna i tygodniowa strata, spadek kapitału i plik STOP zatrzymują oba boty |
| Wynik zamkniętej pozycji mógł przepaść | giełda podaje wynik z opóźnieniem; strata nie trafiała do dziennego limitu | czekanie do 3 minut na wynik |
| Awaria zapytań o konto zatrzymywała cień | brak wyników z cienia przez cały czas awarii | cień działa dalej, powód widać w panelu |
| Ilość w zleceniu w formacie `%g` | przy dużych ilościach notacja naukowa lub ucięcie do 6 cyfr: giełda odrzuca zlecenie | zapis dziesiętny z dokładnością kroku giełdy |
| Strategia `fl` wracała na ten sam sygnał | po stopie kolejne wejście z tego samego godzinowego odczytu | jedno wejście na odczyt |
| Luka w danych przepływu po awarii źródła | wskaźnik niedostępny dłużej niż trzeba, dziura w historii | każde źródło uzupełniane od własnej ostatniej godziny |
| Powtarzane wpisy w dzienniku | ten sam błąd źródła co 10 minut | jeden wpis na zmianę stanu |
| Test `test_markets` zależny od daty | od 16.11 nie przechodził (dane testowe „starzały się”) | data względna; pozostałe testy sprawdzone z zegarem +1, +30, +200 dni |
| Aktualizacja restartowała usługi przy zmianie samej dokumentacji | zbędne przerwy w pracy scalpera | `btc-update` pomija instalację, gdy zmieniły się tylko `docs/`, `tests/`, `README.md` |

Testy: 38 plików, wszystkie przechodzą (`bash tests/run_all.sh`).

## 3. Etap 1: wdrożenie i sprawdzenie na serwerze (ten tydzień)

| # | Czynność | Kto | Kryterium |
|---|---|---|---|
| 1.1 | Podłączyć serwer do repozytorium i włączyć automat (`README.md`, dwa polecenia) | Ty | `sudo btc-update` wypisuje „Brak zmian” |
| 1.2 | Sprawdzić usługi po aktualizacji | Ty | `systemctl is-active btc-api btc-exec btc-ai btc-scalp` cztery razy `active` |
| 1.3 | Sprawdzić dostęp serwera do OKX | Ty | `btc_flow.py` wypisuje 5 aktywów i „błędy: brak” |
| 1.4 | Test integracji z Bybit na demo (`btc_selftest.py --mode demo`) | Ty | wynik bez FAIL |
| 1.5 | Klucz `ANTHROPIC_API_KEY` w `/opt/btc/ai.env` (jeśli chcesz asystenta) | Ty | asystent odpowiada w zakładce Bot |
| 1.6 | Przejrzeć pierwsze prawdziwe transakcje scalpera na demo: wypełnienie, stop, cel, wynik w R | ja, z Twojego zrzutu lub dziennika | wynik realny zgodny z cieniem co do znaku i rzędu wielkości |

Polecenia do 1.3 i 1.4 są w `vps/INSTRUKCJA_VPS.md`.

## 4. Etap 2: zbieranie dowodów (bez zmian reguł)

Zasada: do podanych dat **nie zmieniamy parametrów strategii ani bramek**. Każda zmiana zeruje dowód. Decyzje są zapisane z góry, żeby nie dopasowywać ich do wyniku.

| Data | Co sprawdzamy | Decyzja zapisana z góry |
|---|---|---|
| 30.11.2026 | 2 tygodnie pracy: błędy w dziennikach, zgodność realnych transakcji scalpera z cieniem | różnica większa niż 0,3R na transakcję przy ≥ 20 transakcjach: szukamy przyczyny w wykonaniu, zanim cokolwiek innego |
| 16.12.2026 | 30 dni cienia scalpera na serwerze | `mr` lub `bo` nadal z t ≤ −2: wyłączam je i kończę prace nad strategiami 5-minutowymi ze zleceniami rynkowymi; następny krok to punkt 6.1 |
| 16.01.2027 | 60 dni strategii `fl` i wskaźnika przepływu poza próbą | średnia w cieniu poniżej zera: usuwam strategię, dane zostają jako kontekst. Powyżej zera przy zamkniętej bramce: jeszcze 60 dni, bez dalszych przedłużeń |
| gdy cień bota dziennego ma ≥ 30 transakcji na wariant | zespół v2 poza próbą | średnia ≤ 0: bot dzienny zostaje narzędziem analitycznym. Średnia ≥ +0,05R przy p ≤ 0,05: spełniony warunek 2 z punktu 7 |

Uwaga do ostatniego wiersza: zespół v2 daje ok. 26 transakcji rocznie na 12 aktywach, więc 30 transakcji z cienia to przy obecnym zestawie aktywów kilka miesięcy. Przyspiesza to punkt 5.5.

## 5. Etap 3: niezawodność (mogę robić równolegle, w tej kolejności)

Rozmiar: S = kilka godzin, M = jedna sesja, L = kilka sesji.

| # | Zadanie | Rozm. | Kryterium ukończenia |
|---|---|---|---|
| 5.1 | Kontrola usług i danych co 10 minut z jednym powiadomieniem na Telegram przy zmianie stanu (usługa nie działa, źródło milczy, przebieg bota starszy niż 15 minut) | S | test z zatrzymaną usługą daje jedno powiadomienie |
| 5.2 | `btc-update`: testy przed instalacją i automatyczny powrót do poprzedniej kopii, gdy po instalacji usługa nie wstaje | M | celowo zepsuta wersja nie zatrzymuje serwera |
| 5.3 | ~~Kopia stanu i baz poza serwer raz na dobę, z instrukcją odtwarzania~~ ZROBIONE 22.11 (`btc_backup.py`, Dysk Google przez rclone, zakres drive.file; wymaga jednorazowego podłączenia przez Ciebie, `vps/INSTRUKCJA_VPS.md`) | S | zostaje: odtworzenie na czysto sprawdzone raz |
| 5.4 | Scalper: uzgodnienie stanu z giełdą po restarcie (pozycja scalpera bez wpisu w stanie, wpis bez pozycji) | S | test z atrapą rozjazdu |
| 5.5 | Cień bota dziennego na szerszym zestawie aktywów (ok. 60 zamiast 25), tylko symulacja | M | 2–3 razy więcej transakcji z cienia na miesiąc |
| 5.6 | Pozostałe strony (Panel, Okazje, Rynki, Sytuacje) w stylu zakładki Bot: przegląd na górze, reszta zwinięta, wartości w procentach | M | brak przewijania w bok na telefonie, spójny wygląd |
| 5.7 | Jedno miejsce na klucze i konfigurację (dziś: `trader.env`, `.env`, `ai.env`, panel `/klucze/`) | M | jedna instrukcja, jeden mechanizm |

## 6. Etap 4: badania o największej szansie (po 16.12 albo wcześniej jako sama symulacja)

| # | Pytanie | Dlaczego to | Jak sprawdzę |
|---|---|---|---|
| 6.1 | Czy wejście zleceniem limit (maker) ratuje scalping? | koszt spada z ok. 0,13% do ok. 0,07% nominału, czyli o 0,15–0,2R na transakcję; to więcej niż jakakolwiek zmiana sygnału | cień z wejściem limit: wypełnienie tylko wtedy, gdy cena przejdzie przez poziom; porównanie z cieniem rynkowym na tych samych sygnałach |
| 6.2 | Czy dane o przepływie pomagają na horyzoncie dziennym? | przy 7 dniach w pozycji koszty są małe wobec ruchu | cecha w diagnozie laboratorium (test w blokach czasu), bez wpływu na handel do czasu wyniku |
| 6.3 | Ile wyniku v2 to błąd przetrwania? | 12 aktywów z testu to dzisiejsi wygrani | test z kontraktami wycofanymi z giełdy, jeśli znajdę wiarygodne dane; inaczej opis wielkości błędu |

Każde badanie kończy się zapisem w `docs/` z wynikiem także wtedy, gdy jest negatywny.

## 7. Warunki przed prawdziwymi pieniędzmi (wszystkie naraz)

1. Etap 1 zamknięty, w tym test integracji na demo bez FAIL.
2. Co najmniej jedna strategia z dodatnim wynikiem **poza próbą** po kosztach: bramka scalpera otwarta przez ≥ 30 dni albo cień bota dziennego z ≥ 30 transakcjami i p ≤ 0,05.
3. Realne transakcje na demo zgodne z cieniem (hamulec nie zadziałał, różnica w granicach 0,3R).
4. Punkty 5.1–5.3 zrobione (alarmy, bezpieczna aktualizacja, kopia).
5. Mały kapitał ustalony przez Ciebie i przegląd bezpieczeństwa `btc_trader.py`, `btc_scalp.py`, `btc_api.py`.

Jeśli warunek 2 nie zostanie spełniony, system zostaje narzędziem analitycznym. To możliwy i uczciwy wynik.

## 8. Czego nie robimy

- Nie luzujemy bramek, żeby „coś handlowało”.
- Nie dodajemy strategii ani wskaźników bez testu poza próbą.
- Nie zmieniamy parametrów w trakcie zbierania dowodów (punkt 4).
- Nie zwiększamy dźwigni ponad x2 ani ryzyka na pozycję ponad limity kodu.
