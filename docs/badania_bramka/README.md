# Badanie bramki scalpera (16.11.2026)

Pytanie: czy reguła „co najmniej 30 transakcji z cienia i średnia co najmniej +0,05R” wystarcza, żeby do handlu nie trafiały strategie tracące?

Dane: świece 5 min z OKX, 30 dni (14.10–13.11.2026), BTC, ETH, SOL, XRP, DOGE. Strategie `mr` i `bo` z `btc_scalp.py`, wynik po kosztach 0,13% nominału.
Strategie `ob` i `fl` nie mają historii wejść (arkusz zleceń i wskaźnik przepływu istnieją tylko na żywo).

## Wynik

Obie strategie tracą: `mr` −0,357R na transakcję (1316 transakcji), `bo` −0,486R (860). Mimo to dawna reguła przepuszczała je w krótkich oknach:

| Okno | `mr`: dawna reguła | `mr`: obecna | `bo`: dawna reguła | `bo`: obecna |
|---|---|---|---|---|
| 30 transakcji | 16,8% okien | 0,7% | 15,5% | 0,0% |
| 50 | 8,7% | 0,0% | 8,0% | 0,0% |
| 100 | 2,5% | 0,0% | 0,0% | 0,0% |
| 150 i więcej | 0,0% | 0,0% | 0,0% | 0,0% |

Strategie o znanej prawdziwej średniej (losowanie z wyników `mr` przesuniętych do zadanej średniej, 4000 prób; odchylenie wyniku jednej transakcji 1,24R):

| Prawdziwa średnia | Okno | Dawna reguła | Obecna reguła |
|---|---|---|---|
| −0,10R | 30 / 150 / 300 | 25% / 7% / 2% | 0,7% / 0,1% / 0,1% |
| 0,00R | 30 / 150 / 300 | 40% / 32% / 24% | 2,0% / 1,9% / 2,2% |
| +0,10R | 30 / 150 / 300 | 57% / 68% / 75% | 5% / 15% / 26% |
| +0,20R | 30 / 150 / 300 | 74% / 93% / 98% | 11% / 49% / 80% |
| +0,30R | 30 / 150 / 300 | 87% / 99,5% / 100% | 23% / 85% / 99% |

Wnioski:
- Dawna reguła otwierała się dla strategii bez żadnej przewagi w 24–40% przypadków. Dla strategii rzadkich (`ob`, `fl`), które mają w oknie kilkadziesiąt transakcji, była bliska rzutowi monetą.
- Obecna reguła (dodatkowo t ≥ 2) ogranicza to do ok. 2% dla strategii bez przewagi i poniżej 1% dla tracących.
- Koszt: mała prawdziwa przewaga (+0,10R) potrzebuje pełnego okna 300 transakcji i nawet wtedy przechodzi w ok. 1 przypadku na 4. Dlatego okno wydłużyłem ze 150 transakcji z 14 dni do 300 z 30 dni.
- t liczone zwykłym wzorem zawyża pewność ok. dwukrotnie (dla całej próby `mr`: −10,5 zwykłe, −5,6 z poprawką), bo aktywa dają sygnały w tych samych godzinach. Stąd poprawka: transakcje zamknięte w tej samej godzinie to jedna obserwacja.

## Odtworzenie pracy bota

`replay.py` przechodzi historię świeca po świecy i wywołuje `btc_scalp.stats` i `btc_scalp.gate` tak jak bot. Po 10 dniach rozbiegu (20 dni oceny):
- reguła sprzed 15.11: tryb rozpoznania aktywny przez 100% czasu, ale wszystkie 781 sygnałów zatrzymało weto pary (strategia × aktywo ze średnią poniżej −0,10R);
- reguła obecna: bramka i rozpoznanie zamknięte przez 100% czasu.

Czyli na tych danych żadna wersja nie złożyłaby zlecenia. Różnica ujawnia się przy strategiach z małą liczbą transakcji, których tu nie da się odtworzyć.

## Ograniczenia

- 30 dni i dwie strategie, obie wyraźnie tracące. Tabela druga to symulacja z ich rozkładu wyników, bez skupisk w czasie, więc czułość obecnej reguły na prawdziwych danych będzie niższa niż w tabeli.
- Histereza (bramka otwarta do spadku średniej poniżej zera) nie była tu mierzona: żadna strategia bramki nie otworzyła.
- Próg t ≥ 2 wybrałem po porównaniu z t ≥ 1,5 na tych samych danych (1,5 przepuszczało `mr` w 4% okien po 30 transakcji). Okna 300 transakcji i 30 dni nie stroiłem: to najmniejsze okno, w którym przewaga +0,20R przechodzi w większości przypadków.

## Pliki

- `replay.py KATALOG [--dump TRADES.json]` — odtworzenie pracy bramki na historii.
- `noise.py TRADES.json` — obie tabele powyżej.
- Dane: pliki `BTC-USDT-SWAP.json` itd. w formacie `[[czas_s, o, h, l, c, obrót], ...]`; pobieranie jak w `docs/badania_przeplyw/dl.py` (OKX `history-candles`, `bar=5m`).
