# Badanie: czy dane o przepływie zleceń poprzedzają ruch ceny (15.11.2026)

Skrypty, którymi sprawdzono dane dodane w `btc_flow.py`. Dane pobierane z publicznego API OKX (bez klucza); pliki danych nie są w repozytorium.

| Plik | Co robi |
|---|---|
| `dl.py` | pobiera 60 dni danych godzinowych i 500 dni dziennych (10 aktywów): OI, wolumen agresywny, long/short, premia, świece |
| `dl2.py` | pobiera 2 lata świec godzinowych (cena, cena marki, indeks) |
| `research.py` | każda cecha osobno: średni zwrot w kierunku sygnału (z-score z okna wstecznego), test po blokach czasu, korekta na wielokrotne testowanie, regresja łączna |
| `research2.py` | wskaźnik łączny po kosztach w dwóch połowach okresu; filtr dla strategii 5-minutowych scalpera |
| `research3.py` | wskaźnik jako strategia ze stopem (24 konfiguracje), wynik w R, podział na aktywa i kierunki |
| `research4.py` | ten sam pomysł na danych dziennych (do 460 dni) |
| `research5.py` | 2 lata danych godzinowych dla składników z długą historią (premia, ruch z 4 h) |
| `xcheck.py` | sprawdza, że `btc_flow.series()` liczy ten sam wskaźnik co badanie |

## Wyniki

1. **Pojedyncze cechy (53 dni, 10 aktywów, 11 tys. obserwacji):** żadna nie przechodzi korekty na wielokrotne testowanie (najlepsze p skorygowane 0,06). Najbardziej spójne: agresywne kupno bez wzrostu ceny (+12 pb na odchylenie po 4 h, t 2,9), konta long/short (+56 pb po 24 h, t 2,5), premia (−23 pb po 12 h, t −2,3).
2. **Strategie 5-minutowe scalpera:** dane o przepływie ich nie naprawiają. W każdej tercji (dane przeciw, neutralnie, zgodnie z kierunkiem) wynik zostaje wyraźnie ujemny: od −0,23R do −0,62R.
3. **Wskaźnik łączny (wolumen agresywny + konta long/short − premia − ruch z 4 h), próg 1, trzymanie 12 h:** dodatni po kosztach we wszystkich 24 konfiguracjach stopu i celu, w obu połowach okresu (+0,06 do +0,22R), 8 z 10 aktywów na plus. Ale t <= 1,8, a longi dały 0,00R (161 transakcji), shorty +0,33R (91) na spadającym rynku.
4. **Dane dzienne (460 dni):** konta long/short mają znak odwrotny (−28 pb na odchylenie, t −2,0). Wolumen agresywny ma tylko 13 tygodni historii dziennej.
5. **2 lata danych godzinowych (BTC, ETH, SOL, XRP, 72 tys. obserwacji):** premia +1,5 pb na odchylenie po 4 h (t 1,3), ruch z 4 h −1,4 pb (t −0,8). Strategia z tych dwóch składników: −14 pb na transakcję po kosztach (3125 transakcji, t −2,3).

## Wniosek

Wskaźnik łączny nie ma potwierdzonej skuteczności. Dodatni wynik z 53 dni jest najpewniej cechą jednego okresu: dwa składniki, które da się sprawdzić na długiej historii, nie działają, a pozostałe dwa mają za krótką historię. Dlatego w systemie wskaźnik jest oznaczony jako eksperyment, strategia z niego działa tylko w cieniu, a panel liczy trafność osobno dla okresu po 16.11.2026.

Uwaga metodyczna: pierwsza wersja `research.py` liczyła korelację rang w obrębie bloków czasu i dawała pozornie bardzo silne wyniki (t do 12). To był błąd: ranking w obrębie bloku używa informacji z przyszłości tego bloku i sztucznie tworzy efekt odwrócenia. Obecna wersja używa wyłącznie z-score z okna wstecznego.
