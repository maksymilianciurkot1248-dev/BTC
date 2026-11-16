# Plan rozbudowy systemu analizy

> Dokument historyczny (pierwszy plan, sprzed analizy z 8.11.2026). Aktualny plan: `PLAN.md`.

Stan wyjściowy: kolektor danych (ok. 30 źródeł, SQLite), strumień WebSocket, badania statystyczne (IC, FDR, walk-forward), skaner okazji z samokorektą, strategie portfelowe, autonomiczny trader Bybit (dryrun domyślnie), strony WWW.

## 1. Co robią podobne projekty (i co z tego wynika dla nas)

Uwaga o źródłach: porównania pochodzą z blogów i stron producentów, więc liczby i oceny traktuj orientacyjnie. Sprawdź w dokumentacji projektów przed decyzją.

| Projekt | Mocna strona | Czego nam brakuje | Wniosek |
|---|---|---|---|
| **Freqtrade** | Backtest z kosztami, hyperopt, tryb dry-run, Telegram, polecenia `lookahead-analysis` i `recursive-analysis` wykrywające błędy "podglądania przyszłości" | Automatyczny test na look-ahead i na zależność wskaźników od długości historii; realistyczne koszty (prowizja, poślizg, funding) w ocenie sygnałów | **Wziąć pomysły**: testy poprawności backtestu i dziennik dry-run |
| **NautilusTrader** | Ten sam kod strategii w backtestcie i na żywo (zdarzeniowy rdzeń) | Jedna funkcja decyzji współdzielona przez badania, paper i live | **Wziąć zasadę**, nie bibliotekę (zbyt ciężka dla jednego serwera) |
| **Hummingbot** | Jednolity interfejs do wielu giełd (konektory) | Abstrakcja brokera: dziś tylko Bybit | **Wziąć wzorzec adaptera** (potrzebny pod IBKR) |
| **vectorbt** | Szybkie przeszukiwanie parametrów | Korekta na wielokrotne testowanie przy przeszukiwaniu parametrów | Szybkie wektorowe testy tak, ale z korektą (deflated Sharpe) |
| **Qlib** | Modele ML na czynnikach, ponowne trenowanie w oknach | Baza ML do porównania z obecnym wynikiem punktowym | Tylko jako **test, czy ML bije prostą regułę** poza próbą |
| **FinRL** | Uczenie ze wzmocnieniem | Nic, czego potrzebujemy | **Pominąć** (niestabilne, trudne do wiarygodnej oceny) |
| **QuantConnect LEAN** | Dane i silnik w pakiecie | Uzależnienie od platformy | Pominąć |
| **OpenBB (ODP)** | Standaryzacja źródeł danych i udostępnianie ich przez REST/MCP | Jednolity interfejs dostawców i własny serwer MCP | Wzorzec dla warstwy danych (licencja AGPL, więc nie kopiować kodu) |

Wspólny wniosek: najwięcej wartości dają **rzetelna walidacja i wykonanie**, a nie kolejne wskaźniki. Nasze dotychczasowe badania już pokazały, że prosta reguła "kup dołek" na krypto nie miała przewagi, więc ciężar prac przesuwamy na weryfikację.

## 2. Fazy

Rozmiar: S = kilka godzin pracy, M = jedna sesja, L = kilka sesji.

### Faza 0: wiarygodność wyników (najpierw, bo wszystko inne od tego zależy)
| # | Zadanie | Rozm. | Kryterium ukończenia |
|---|---|---|---|
| 0.1 | Realistyczne koszty w ocenie sygnałów: prowizja, poślizg zależny od zmienności, funding dla perpetual | M | Wyniki netto i brutto raportowane obok siebie |
| 0.2 | Test look-ahead: automatycznie obcinać dane i sprawdzać, czy sygnał z dnia T zmienia się po dodaniu danych z T+1 | M | Test w zestawie, który blokuje wdrożenie przy wykryciu |
| 0.3 | Korekta na wielokrotne testowanie (deflated Sharpe lub test realności White'a) dla wyników przeszukiwania parametrów | M | Każdy raport z "najlepszą" strategią pokazuje skorygowaną istotność |
| 0.4 | Kalibracja prawdopodobieństw: wskaźnik Briera i wykres rzetelności dla `log_signals` | S | Strona Okazje pokazuje, czy "61%" faktycznie wypada ok. 61% |
| 0.5 | Dane punktowe w czasie (point-in-time) i lista spółek wycofanych z obrotu, żeby usunąć błąd przetrwania z rankingu spółek | L | Backtest akcji bez ostrzeżenia o błędzie przetrwania lub z opisem jego wielkości |

### Faza 1: dane
| # | Zadanie | Rozm. | Uwagi |
|---|---|---|---|
| 1.1 | SEC EDGAR Form 4 i 13D (zakupy insiderów) do bazy | M | Wymaga nagłówka kontaktowego (SEC_CONTACT). Dane dzienne, nie czasu rzeczywistego |
| 1.2 | Monitor jakości danych: świeżość każdego źródła, luki, skoki nietypowe, alert Telegram | M | Dziś `btc_doctor.py` działa ręcznie |
| 1.3 | Kopia zapasowa SQLite (`sqlite3 .backup`) co dobę do magazynu Oracle lub na inny host | S | Darmowe limity sprawdź przed konfiguracją |
| 1.4 | Przepływy do ETF-ów krypto i dane on-chain | M | Tylko jeśli znajdziemy darmowe, stabilne źródło. Źródła musimy najpierw zweryfikować |

### Faza 2: badania i modele
| # | Zadanie | Rozm. | Kryterium |
|---|---|---|---|
| 2.1 | Magazyn cech (feature store): jedna tabela cech obliczanych raz, używana przez badania i skaner | M | Te same cechy w badaniach i na żywo |
| 2.2 | Oczyszczona walidacja krzyżowa z embargo (purged CV) dla etykiet z potrójną barierą | M | Brak nakładania się okien treningowych i testowych |
| 2.3 | Model bazowy ML (regularyzowana regresja, potem drzewa) w oknach kroczących, porównany z obecnym wynikiem punktowym | L | **Wdrażamy tylko, jeśli bije regułę poza próbą po kosztach** |
| 2.4 | Rozpoznawanie reżimów rynku (zmienność, trend) jako filtr, nie jako sygnał | M | Poprawa mierzona poza próbą |
| 2.5 | Meta-etykietowanie: model oceniający, czy wziąć sygnał reguły | L | Jak 2.3 |

### Faza 3: wykonanie zleceń
| # | Zadanie | Rozm. | Kryterium |
|---|---|---|---|
| 3.1 | Interfejs brokera (`place`, `cancel`, `positions`, `balance`) i adapter Bybit jako pierwszy | M | `btc_trader.py` korzysta z interfejsu, testy przechodzą |
| 3.2 | Adapter IBKR na koncie papierowym, te same zabezpieczenia (dzienny limit, stop obowiązkowy, tryb dryrun) | L | Tydzień działania bez błędów uzgodnienia stanu |
| 3.3 | Uzgadnianie stanu: porównanie pozycji bota z giełdą co minutę, alert przy różnicy | M | Test z atrapą rozjazdu |
| 3.4 | Dziennik wykonania: cena sygnału kontra cena realizacji (poślizg rzeczywisty) | S | Dane do kalibracji kosztów z 0.1 |
| 3.5 | Wyłącznik awaryjny: plik lub polecenie Telegram, które zatrzymuje nowe zlecenia | S | Test |

### Faza 4: utrzymanie
| # | Zadanie | Rozm. |
|---|---|---|
| 4.1 | Monitoring usług (healthcheck, alert przy ciszy któregoś źródła lub usługi) | S |
| 4.2 | Testy w CI przy każdej zmianie (14 zestawów już istnieje) | S |
| 4.3 | Przegląd bezpieczeństwa `btc_trader.py` i `btc_api.py` (`/security-review`) przed trybem live | S |
| 4.4 | Rotacja kluczy i przegląd uprawnień | S |

### Faza 5: interfejs
| # | Zadanie | Rozm. |
|---|---|---|
| 5.1 | Pole "Kapitał" z wartością zapamiętaną, jedno zdanie decyzji nad wykresem | S |
| 5.2 | Grupowanie wskaźników KPI (3 główne, reszta w tabeli) | S |
| 5.3 | Ten sam motyw na `index.html` i `kalkulator.html` | M |
| 5.4 | Strona "Wiarygodność": kalibracja, skuteczność netto, status danych | M |

## 3. Bramki przed prawdziwymi pieniędzmi

Tryb live włączamy dopiero, gdy **wszystkie** warunki są spełnione:
1. Faza 0 ukończona (koszty, look-ahead, korekta na wielokrotne testowanie, kalibracja).
2. Co najmniej 4–8 tygodni na koncie papierowym lub testnet z wynikiem **dodatnim po kosztach** i prawdopodobieństwami zgodnymi z kalibracją.
3. Uzgadnianie stanu i wyłącznik awaryjny przetestowane.
4. Przegląd bezpieczeństwa wykonany.
5. Limit kapitału ustalony przez Ciebie i niewielki na start.

Jeżeli warunek 2 nie jest spełniony, system pozostaje narzędziem analitycznym. To uczciwy wynik, bo nie ma gwarancji, że sygnały dają przewagę.

## 4. Kolejność (rekomendacja)

1. Faza 0 (0.1, 0.2, 0.4 najpierw): największy wpływ na wiarygodność.
2. Faza 3.1, 3.3, 3.5 oraz 4.1: bezpieczeństwo wykonania.
3. Faza 1.1–1.3: dane i kopia zapasowa.
4. Faza 2: modele, tylko jeśli Faza 0 da miarodajną miarę poprawy.
5. Faza 3.2 (IBKR) i Faza 5 w dowolnym momencie, niezależnie.

## 5. Czego świadomie nie robimy
- Uczenie ze wzmocnieniem (FinRL) i duże platformy z uzależnieniem od dostawcy (LEAN).
- Nie kopiujemy kodu z projektów AGPL (OpenBB). Bierzemy tylko pomysły.
- Handel wysokiej częstotliwości: nasze opóźnienie i koszty tego nie wspierają.
