# Instalacja na VPS (Ubuntu 22.04/24.04 lub Debian 12)

## Zawartość
- `setup_vps.sh` – jednorazowa instalacja i utwardzenie serwera
- `app/` – `btc_trader.py`, `btc_analysis.py`, `index.html`, `kalkulator.html`

## Przed instalacją
1. Kup najmniejszy VPS (1 vCPU / 1 GB RAM wystarczy) z **własnym stałym IPv4**. Region: Europa.
2. W DNS domeny dodaj rekord **A**: `btc.ciurkot.pl` → IP VPS (poczekaj, aż się rozpropaguje).
3. Wgraj klucz SSH i sprawdź, że logujesz się bez hasła.

## Instalacja
```bash
scp -r vps root@IP_VPS:/root/
ssh root@IP_VPS
cd /root/vps && sudo DOMAIN=btc.ciurkot.pl EMAIL=twoj@email.pl bash setup_vps.sh
```
Skrypt: tworzy użytkownika `btc` bez powłoki, instaluje Pythona/nginx, zapisuje usługi systemd
(z izolacją: ProtectSystem, NoNewPrivileges, brak capabilities), robi HTTPS Let's Encrypt, hasło do panelu,
zaporę ufw (22/80/443), fail2ban, automatyczne aktualizacje, synchronizację zegara. Na końcu wypisuje **IP wychodzące**.

## Konfiguracja (po instalacji)
```bash
sudo nano /opt/btc/.env          # CoinGecko, newsdata, klucz Bybit TYLKO DO ODCZYTU
sudo nano /opt/btc/trader.env    # klucz handlowy (zostaw TRADER_MODE=dryrun na początek)
sudo systemctl start btc-report.service && journalctl -u btc-report -n 30
journalctl -u btc-trader -f
```
Klucz handlowy wpisujesz TYLKO w `/opt/btc/trader.env` (właściciel `btc`, uprawnienia 600).

## Klucz Bybit (teraz z whitelistą IP)
1. Subkonto z kwotą, którą możesz stracić.
2. Klucz Read-Write, tylko **Contract – Orders & Positions**, bez Wallet/Transfer/Withdraw/Spot/Convert/Earn/P2P.
3. **IP: adres wypisany na końcu instalacji** (wtedy klucz nie wygasa po 3 miesiącach).
4. W `trader.env` ustaw `TRADER_EXPECTED_IP=` na to samo IP – skrypt nie handluje z innego adresu.
5. Stary, szeroki klucz ze zrzutu usuń.

## Etapy
| Etap | `TRADER_MODE` | Czas |
|---|---|---|
| 1 | `dryrun` – tylko log | kilka dni |
| 2 | `testnet` + klucz z testnet.bybit.com | 2–3 tygodnie |
| 3 | `live` + `TRADER_CONFIRM_LIVE=I_ACCEPT_THE_RISK` + `TRADER_MAX_CAPITAL_USDT` | mała kwota |

Po zmianie `trader.env` restart nie jest potrzebny – timer czyta plik co 5 minut.

## Wyłącznik awaryjny
```bash
sudo -u btc touch /opt/btc/STOP        # stop nowych pozycji
sudo systemctl stop btc-trader.timer   # całkowite wyłączenie
```
Otwartą pozycję zamknij w aplikacji Bybit. Powrót: `sudo rm /opt/btc/STOP`.

## Powiadomienia (zalecane)
Telegram: utwórz bota przez @BotFather, wpisz `TELEGRAM_BOT_TOKEN` i `TELEGRAM_CHAT_ID` w `trader.env`.
Dostaniesz wiadomość o każdym zleceniu, blokadzie dziennej straty i błędzie.

## Bezpieczeństwo – lista
- Panel: HTTPS + hasło (bcrypt) + limit zapytań + fail2ban; opcjonalnie dodatkowo tylko Twoje IP (komentarz w konfiguracji nginx).
- Pliki `.env`, `.py`, `.log` nie są serwowane; kod i klucze leżą w `/opt/btc` poza katalogiem WWW.
- Strona nie ma żadnego punktu do składania zleceń ani wpisywania kluczy.
- Trader: dźwignia ≤ x2 w kodzie, stop-loss w zleceniu, limity dzienne, blokada podwójnego uruchomienia, kontrola IP.
- Aktualizacje: `unattended-upgrades`; kopię `/opt/btc/trader_state.json` i `btc_journal.json` warto robić okresowo.

## Czego to nie załatwia
Strata z rynku jest nadal możliwa (stop może się prześlizgnąć, strategia może przegrywać). Zacznij od testnetu.

## Oracle Cloud (Always Free)
1. Region domowy wybierasz przy rejestracji i nie da się go zmienić. Dla Bybit wybierz **Europę** (Frankfurt, Amsterdam, Warszawa); adresy z USA bywają blokowane.
2. Compute → Create instance → obraz **Ubuntu 22.04/24.04**, kształt Always Free (**VM.Standard.E2.1.Micro** albo Ampere A1 – oba działają), wgraj swój klucz SSH.
3. Networking → **Reserved public IP** (Always Free) i przypisz do instancji. Bez tego IP może się zmienić.
4. VCN → Security List (lub NSG) → ingress TCP **22, 80, 443** z 0.0.0.0/0.
5. Logowanie: `ssh ubuntu@IP`, instalacja: `sudo DOMAIN=... EMAIL=... bash setup_vps.sh` (konto `ubuntu`, nie root).

## Dane dodatkowe (btc_extras.py)
Raport zbiera teraz dodatkowo (darmowe API, bez kluczy): Fear & Greed, on-chain (mempool.space), funding/OI/long-short
z Binance i OKX, opcje Deribit (DVOL, put/call, max pain), premię Coinbase, makro ze Stooq (S&P 500, Nasdaq, złoto,
dolar, 10Y, ropa + korelacje z BTC) oraz podaż stablecoinów (DefiLlama). Własna historia rośnie w
`/opt/btc/btc_extras_history.jsonl` (do 6000 migawek). Wyłączenie: dopisz `--extras off` w `btc-report.service`.
Te dane są tylko opisowe: `btc_trader.py` ich nie używa, więc nie zmieniają zleceń.
Binance może blokować część regionów; wtedy źródło pokaże ✗ w panelu, a reszta działa.

## Baza danych i badania (btc_collect.py, btc_research.py, badania.html)
- `btc_collect.py` zbiera historię i migawki do `/opt/btc/btc_data.db` (SQLite). Jednorazowo `backfill` (pełna historia),
  potem timer `btc-collect` co godzinę (`update`). `status` pokazuje zakres każdej metryki i ostatnie błędy.
- Źródła bez klucza: Binance (świece, taker buy ratio, funding od 2019, OI i long/short), Fear & Greed od 2018, DVOL (Deribit)
  od 2021, podaż stablecoinów, hashrate, wejścia na Wikipedię (zainteresowanie detaliczne), Stooq (akcje, złoto, dolar, 10Y, ropa),
  migawki co godzinę (funding, OI, premia Coinbase, put/call, mempool).
- Źródła z Twoim darmowym kluczem (wpisz w `/opt/btc/.env`; bez klucza są pomijane):
  | Zmienna | Gdzie zdobyć klucz | Co dodaje |
  |---|---|---|
  | `FRED_API_KEY` | fred.stlouisfed.org/docs/api/api_key.html | rentowności 10Y, realna stopa, krzywa 10Y-2Y, VIX, stopa Fed, indeks dolara, M2, bilans Fed, odwrócone repo, spready kredytowe (od 2015) |
  | `CMC_API_KEY` | coinmarketcap.com/api | dominacja BTC/ETH, kapitalizacja rynku, wolumen, stablecoiny, DeFi, wolumen pochodnych |
  | `GLASSNODE_API_KEY` | studio.glassnode.com | aktywne adresy, nowe adresy, liczba transakcji, MVRV, SOPR (zakres zależy od planu; niedostępne metryki są pomijane) |
  | `WHALE_ALERT_API_KEY` | whale-alert.io | duże transfery BTC, napływ/odpływ z giełd (eksperymentalne, wymaga planu z dostępem do API) |
  Lista serii FRED: `FRED_SERIES=DGS10,VIXCLS,...`, metryki Glassnode: `GLASSNODE_METRICS=addresses/active_count,...`.
- `btc_research.py` (timer `btc-research`, raz dziennie) liczy: siłę predykcyjną ~100 cech względem zwrotów 1-30 dni
  (korelacja Spearmana, korekta na autokorelację i na wielokrotne testy), kwintyle, reżimy, sezonowość, test walk-forward
  poza próbą z testem permutacyjnym i widok "dziś". Wynik: `https://TWOJA_DOMENA/badania.html`.
- Badania są opisowe i nie sterują zleceniami. Zanim jakąkolwiek cechę włączysz do strategii, powinna przejść walk-forward
  na Twoich danych (a dane zbierane od dziś będą rosły dopiero z czasem: część metryk ma tylko 30 dni historii u źródła).

## Rynki: waluty fiat i spółki (btc_markets.py, rynki.html)
- **Waluty (FX)**: kursy referencyjne EBC przez Frankfurter (bez klucza, od 2015): 18 walut wobec USD (zmiany 7/30/90 dni, YTD, 1 rok,
  zmienność, RSI, trend, percentyl z 5 lat, korelacja z BTC), indeks dolara (koszyk G10) i kursy w PLN.
- **Spółki** (USA, GPW, Niemcy; nie tylko Bybit): ceny dzienne ze Stooq (bez klucza; dla USA zapas Tiingo z `TIINGO_API_KEY`),
  fundamenty USA z Finnhub (`FINNHUB_API_KEY`, darmowy: finnhub.io/register). Lista spółek: `watchlist.json` w `/opt/btc/`
  (domyślną zapiszesz poleceniem `btc_markets.py watchlist` i edytujesz: `symbol`, `market` = US/PL/DE, `name`, `sector`).
- **Ranking 0-100** = średni percentyl w uniwersum: momentum 12-1, momentum 6 mies., siła względem indeksu, trend, bliskość rocznego
  szczytu, niska zmienność, wolumen (60%) oraz wzrost przychodów i zysku, marża, ROE, PEG, zadłużenie (40%, gdy są fundamenty).
  Wagi są w kodzie (`W_PRICE`, `W_FUND`). Flagi: przegrzana, bardzo zmienna, głęboka korekta, korekta w trendzie wzrostowym.
- **Backtest** czynnika cenowego (co ~30 dni, górny kwintyl vs uniwersum) jest obciążony błędem przetrwania (lista = dzisiejsze nazwy),
  więc służy do sprawdzenia, czy model nie jest pusty, a nie jako obietnica zysku. Wynik to opis historii, nie porada.
- Usługi: `btc-collect` (źródła `fx`, `stocks`, `fundamentals`) i `btc-markets` (analiza raz dziennie, 04:05 UTC).

## Złoto, więcej źródeł, sytuacje (btc_situations.py, btc_live.py, btc_net.py)
- **Złoto i surowce** (`rynki.html`): złoto, srebro, platyna, miedź, ropa, ETF-y (GLD, IAU, GDX, SLV, TLT, UUP), indeksy (SPX, NDX, DXY, VIX), PAXG
  (złoto 24/7), złoto w PLN/EUR, stosunek złoto/srebro, BTC w uncjach złota, realna rentowność, pozycje COT, korelacje.
- **Nowe źródła** (`btc_collect.py`): ETH i PAXG (Binance), CFTC COT (złoto, srebro, BTC, EUR, JPY, ropa, S&P), TVL DeFi, historia premii Coinbase,
  kalendarz zdarzeń (wygasanie opcji, halvingi, FOMC; z `FRED_API_KEY` także CPI, NFP, PCE, PKB, PPI, JOLTS, sprzedaż detaliczna), więcej serii FRED.
- **Szybkość**: źródła pobierane równolegle (`BTC_COLLECT_WORKERS`, domyślnie 6), gzip, ponowienia z opóźnieniem i `Retry-After`, limity na host,
  Stooq pobierany przyrostowo (tylko nowe dni), kolektor co 30 min. `btc-live.service` odświeża `live.json` co 10 s (Bybit) i co 60 s (Stooq);
  strony łączą się też z WebSocketem Bybit (BTC, ETH, złoto XAUT) dla notowań co sekundę.
- **Sytuacje** (`sytuacje.html`, timer co 6 h): dla BTC, ETH, złota, srebra, S&P 500, Nasdaq, DXY, ropy, EUR/USD, USD/PLN i USD/JPY testuje, jakie sytuacje
  (RSI, ściśnięcie zmienności, krzyżyki SMA, spadki od szczytu, dolar, VIX, realne stopy, złoto, ropa, Fear & Greed, funding, CPI/NFP/FOMC, wygasanie opcji,
  faza po halvingu) poprzedzały ruch w górę/w dół lub skok zmienności, z korektą FDR, i pokazuje, które są teraz aktywne albo się zbliżają.
- Kalendarz FOMC 2024-2026 jest wpisany ręcznie w `btc_collect.py` (`FOMC_STATIC`): sprawdź go na federalreserve.gov.

## Skaner okazji (btc_opportunities.py, btc_api.py, okazje.html)
- **Skaner** ocenia ok. 55 aktywów (12 krypto, 5 surowców, 8 par walutowych, spółki z watchlisty) z częstotliwością ~10-15 s: dane dzienne z bazy + bieżąca cena
  z `live.json` (Bybit co 10 s, Stooq co 60 s; akcje opóźnione ok. 15 min). Status: okazja / obserwuj / za wcześnie / spadający nóż / brak dołka.
- **Raport** po kliknięciu: strefa wejścia, stop, cele 1-3 (sprzedaż), prawdopodobieństwo celu przed stopem, typowy czas do celu i do stopu, szansa dojścia
  do strefy wejścia w 1/3/5/10 sesji, wielkość pozycji z Twojego kapitału i ryzyka, uzasadnienie, ryzyka, scenariusze, kontekst i zdarzenia.
- **Uczenie się**: okazje trafiają do dziennika (`signals`); wyniki (wejście / stop / cel 1 / czas) skurczają prawdopodobieństwa do zrealizowanej skuteczności,
  poszerzają stopy, gdy wybite stopy i tak kończyły się celem, i przesuwają próg wyniku (osobno dla krypto, surowców, walut i akcji).
  Codzienna symulacja walk-forward (`btc-opp-replay`) daje startową skuteczność jeszcze przed pierwszymi rzeczywistymi okazjami.
- **Pozycje**: wpisujesz otwartą pozycję, a system na bieżąco podpowiada stop (breakeven, trailing), częściową realizację, limit czasu i wyjście po unieważnieniu układu.
- API działa wyłącznie na 127.0.0.1 i jest wystawione tylko przez nginx za hasłem; zapisy wymagają nagłówka X-Requested-With; nic nie trafia na giełdę.
- Skaner nie ma żadnego połączenia z `btc_trader.py`: handel automatyczny nadal używa wyłącznie własnego planera.

## Wyniki sprawdzenia na prawdziwych danych (ważne)
- Źródła krypto (OKX, Coinbase, Kraken, Deribit), COT, Fear & Greed, stablecoiny, hashrate, Wikipedia i TVL działają bez klucza. **Binance i Bybit mogą blokować region**
  (HTTP 451/403): kod przechodzi wtedy na OKX (świece, notowania, token złota XAUT). **Stooq odrzuca zautomatyzowane zapytania** (reset połączenia): dla surowców,
  walut i akcji wpisz `TWELVEDATA_API_KEY` (i `TIINGO_API_KEY` dla akcji USA); sprawdź też, czy Stooq działa z Twojego serwera (`btc_doctor.py`).
- Frankfurter bywa niestabilny (HTTP 522): kursy walut mają zapasowo oficjalny plik EBC (www.ecb.europa.eu).
- **Reguła "kup dołek" na prawdziwej historii krypto (2017-2026, ok. 700 dołków) nie ma przewagi**: oczekiwana wartość ok. -0,05R po kosztach, szansa celu 1 ok. 26%
  (próg opłacalności przy 1,5R to 40%). Skaner dlatego rzadko lub wcale nie oznacza krypto jako "okazja". To wynik, nie błąd. Zanim zaufasz statusowi "okazja"
  dla surowców, walut czy akcji, sprawdź ich analogi w raporcie i zakładkę "Skuteczność systemu" (symulacja walk-forward).

## Strategie portfelowe (btc_strategies.py, strategie.html)
Osobne okno (przycisk „Strategie” w panelach) z 5 profilami: **Ochronny, Bezpieczny, Zbalansowany, Wzrostowy, Agresywny**. Każdy ma docelową zmienność (4/7/12/18/30% rocznie),
limity na aktywo i klasę (np. krypto 0% / 3% / 10% / 25% / 60%), filtr trendu (cena > SMA200) i momentum 12-1, wagi odwrotnie do zmienności i gotówkę jako rezerwę.
Statystyki z testu historycznego (miesięczne równoważenie, tylko dane z przeszłości, koszty): zysk roczny, zmienność, Sharpe z przedziałem, Sortino, maks. obsunięcie i czas odrabiania,
Calmar, VaR/CVaR miesięczny, miesiące na plusie, wyniki roczne, okresy stresu, porównanie z BTC, S&P 500, złotem, 60/40 i równymi wagami, symulacja kolejnych 12 miesięcy oraz skład
portfela na dziś z kwotami dla wpisanego kapitału. Timer `btc-strategies` odświeża dane raz dziennie. Profile ustalono z góry (nie dopasowywano do historii); wynik zawyża błąd przetrwania
(lista aktywów = Twój watchlist). Bez obligacji (TLT), surowców i akcji w bazie (Twelve Data / Stooq) profile defensywne zostają w dużej mierze w gotówce: strona ostrzega o tym.

## Pluginy w czacie a serwer (ważne rozróżnienie)
Pluginy MCP (TradeOS, HPSILab, Quiver Quant, TradeStar Insider i inne) działają **tylko w sesji z Claude**: serwer VPS ich nie widzi i nie może z nich korzystać.
W czacie służą do analizy na żądanie (np. kurs i wykres złota z TradeOS, klastry zakupów insiderów z SEC, ryzyko przed transakcją z HPSILab). Dla serwera dodano więc własne źródła:
`POLYGON_API_KEY` (Polygon.io, darmowy: waluty, metale, akcje USA, ok. 2 lata historii, 5 zapytań/min) oraz wcześniej `TWELVEDATA_API_KEY`, `TIINGO_API_KEY`, OKX (krypto i token złota XAUT).
Kolejność źródeł świec dla surowców, walut i akcji: Stooq → Tiingo (USA) → Polygon → Twelve Data → OKX (tylko złoto).

## Darmowe źródła: co, jak szybko i ile kosztuje (stan sprawdzony 5.11.2026)
**Na żywo (WebSocket, opóźnienie ułamka sekundy), usługa `btc-stream`:**
| Źródło | Co daje | Klucz | Sprawdzone |
|---|---|---|---|
| Bybit WS (spot + linear) | ceny 12 krypto i złota XAUT, transakcje BTC (CVD, duże zlecenia), **likwidacje**, funding, open interest | brak | tak, na żywo |
| Deribit WS | DVOL (zmienność implikowana) i indeks BTC na żywo | brak | tak, na żywo |
| mempool.space WS | nowe bloki, opłaty | brak | tak, na żywo |
| OKX WS | zapas cen, gdy Bybit jest zablokowany w regionie | brak | połączenie odrzucone z mojego środowiska, kod przetestowany na atrapie |
| Finnhub WS | akcje USA, waluty i złoto w czasie rzeczywistym (do 50 symboli) | darmowy `FINNHUB_API_KEY` | tylko na atrapie |
| Alpaca WS (IEX) | akcje USA w czasie rzeczywistym | darmowe `ALPACA_KEY_ID` + `ALPACA_SECRET` | tylko na atrapie |
Wynik: `stream.json` (co sekundę), a co minutę wiersze w bazie (likwidacje, CVD, DVOL, OI, funding). `btc_live.py` bierze z niego ceny, gdy są świeże (REST jest wtedy zapasem).

**REST, kolektor (`btc_collect.py`), bez klucza:** CBOE (VIX, VIX3M, VIX9D, SKEW, VVIX od 1990; VIX trafia też do świec), Hyperliquid (funding, OI, premia), GDELT (ton i natężenie
wiadomości: bitcoin, złoto, Fed, recesja, inflacja), Skarb USA (krzywa rentowności 3M-30Y, spread 10Y-2Y), NY Fed (SOFR), CFTC COT, OKX (świece, funding, taker volume), Deribit, DefiLlama, mempool.
Formaty CBOE, Skarbu USA, GDELT, NY Fed i Hyperliquid są zgodne z dokumentacją, ale nie mogłem ich sprawdzić na żywo (blokada w moim środowisku): uruchom `btc_doctor.py`.

**Z kluczem (darmowe):** Polygon (waluty, metale, akcje USA), Twelve Data, Tiingo, FRED (makro i kalendarz publikacji), Finnhub (fundamenty i WebSocket), CoinMarketCap, Glassnode (ograniczony zakres).

**Nie dodane (do rozważenia):** SEC EDGAR (zakupy insiderów Form 4, 13D/13F; strumień atom jest darmowy, wymaga nagłówka User-Agent z kontaktem w zmiennej `SEC_CONTACT`);
rejestr likwidacji z OKX/Binance (Binance blokuje region); kalendarz wyników spółek (Finnhub z kluczem).
Pluginy MCP w czacie (TradeOS, TradeStar Insider, Quiver Quant, HPSILab) pozostają narzędziami do analizy na żądanie, bo serwer ich nie widzi.

## Zakładka „Bot” (https://TWOJA_DOMENA/bot.html)
Pokazuje na żywo: tryb i stan bota, ostatnią decyzję, saldo, otwartą pozycję, statystyki z ostatnich 8 tygodni (trafność, profit factor, spadek kapitału, krzywa wyniku) i dziennik zdarzeń.
Ustawienia z panelu (ryzyko na pozycję, dźwignia, dzienny limit straty, liczba zleceń na dobę, kapitał, kierunki long/short, pauza) trafiają do `/opt/btc/bot_settings.json`, a trader wczytuje je przy każdym uruchomieniu (co 5 minut).
Zasady bezpieczeństwa: panel NIE zna kluczy i NIE zmienia trybu (dryrun/testnet/demo/live), granice są twarde (dźwignia ≤ 2, ryzyko ≤ 2%), a w trybie live ustawienia z panelu mogą tylko zmniejszać ryzyko względem `trader.env`.
Wgranie na istniejący serwer: skopiuj `btc_botcfg.py`, `btc_trader.py`, `btc_api.py` do `/opt/btc/` (install -m 700 -o btc -g btc), `bot.html` do `/var/www/btc/` (install -m 640 -o btc -g www-data), potem `sudo systemctl restart btc-api`.

### Wiele aktywów i opcje strategii (zakładka Bot)
- **Aktywa:** do 8 kontraktów USDT na Bybit (np. BTC, ETH, SOL, XRP, złoto XAUT albo własny symbol typu SUIUSDT). Bot pomija aktywa z dobowym obrotem poniżej 20 mln USDT lub niedostępne do handlu. Strategia była badana na BTC; na pozostałych aktywach nie została zweryfikowana.
- **Maks. jednoczesnych pozycji (1–3):** suma nominałów wszystkich pozycji nigdy nie przekroczy dźwigni × kapitał (x2), a przy każdym przebiegu bot sprawdza wszystkie pozycje konta.
- **Take-profit** można wyłączyć (wtedy pozycję zamyka tylko stop-loss albo Ty).
- **Stop-loss nie ma przełącznika:** każde zlecenie go zawiera, a pozycja bez stopa jest zamykana. Można tylko zmienić jego odległość mnożnikiem 0,5–2 (rozmiar pozycji dopasowuje się tak, żeby ryzyko pozostało stałe).
- W trybie live panel może tylko zawęzić listę aktywów z `TRADER_SYMBOLS` w `trader.env` i nie zwiększy liczby pozycji ani ryzyka.

### Nauka systemu i automatyczny wybór aktywów
- `btc_learn.py` (timer `btc-learn`, codziennie 03:20 UTC; pierwsze uruchomienie: `sudo systemctl start btc-learn.service`) wybiera ~25 najbardziej płynnych kontraktów USDT na Bybit (+ Twoje aktywa z panelu), dociąga ich historię dzienną do bazy i buduje **mapę przewagi** (`/opt/btc/edge_map.json`).
- Mapa przewagi testuje na historii **tę samą funkcję planu, której używa trader**, po kosztach. **Wejście** wymaga: ≥ 30 transakcji, średnio ≥ +0,05R, istotności po korekcie na wiele testów (Benjamini-Hochberg, p skorygowane ≤ 0,05), obie połówki historii i ostatnie 12 miesięcy nieujemne. Prawdopodobnie większość aktywów NIE przejdzie tych kryteriów, i to jest poprawny wynik (bot wtedy nie handluje). Aktywo, które już jest dopuszczone, **zostaje** aż do spełnienia łagodniejszych warunków wyjścia (patrz niżej).
- **Histereza i wspólna korekta (btc_edge.py, 8.11).** Mapa jest liczona codziennie, więc bez zabezpieczeń aktywo wchodziłoby i wypadało z powodu drobnych zmian p-wartości.
  - *Wejście* przy p skorygowanym ≤ **0,05** i pełnych kryteriach. *Wyjście* dopiero, gdy aktywo już dopuszczone spełni jedno z: p skorygowane > **0,20**, średni wynik ≤ 0R, któraś połówka historii poniżej −0,10R, ostatnie 12 miesięcy poniżej −0,10R (przy ≥ 8 transakcjach) albo spadek liczby transakcji poniżej 30.
  - Aktywo, które nie spełnia już pełnych kryteriów wejścia, ale nie spełnia też warunku wyjścia, jest oznaczone jako **utrzymane** (żółte w tabeli). Trader handluje takim aktywem **mniejszą pozycją** (`HELD_SIZE_MULT` w `btc_edge.py`, domyślnie 0,5 = połowa; 1,0 wyłącza zmniejszanie).
  - Korekta FDR jest **wspólna dla obu strategii** (rodziną testów jest każda para strategia × aktywo, np. 25 aktywów × 2 = 50 testów). To zaostrza progi, bo to realna liczba sprawdzanych hipotez. W dzienniku nauki widać, ile par przeszłoby przy korekcie osobnej dla każdej strategii.
  - Gdy poprzednia mapa jest starsza niż 10 dni (np. timer stał), histerezy nie ma i wszystkie aktywa wchodzą od nowa według pełnych kryteriów.
  - **Historia decyzji:** `/opt/btc/edge_history.json` (ostatnie 600 wpisów) i rozwijana lista w zakładce Bot: kto, kiedy i dlaczego został dopuszczony, wycofany, utrzymany albo potwierdzony; w podsumowaniu liczba dopuszczeń i wycofań z 30 dni (miara migotania).
  - Czego histereza **nie** załatwia: skoro mapa jest liczona codziennie na prawie tych samych danych, przy wielu dniach niektóre aktywa przejdą próg przypadkiem. Rzetelnym dowodem jest dopiero tryb cienia (wyniki poza próbą), nie sama mapa.
- Filtry kontekstu: system sprawdza, czy wyniki są istotnie gorsze, gdy BTC jest pod SMA50 albo w dniu ważnej publikacji makro (FOMC, CPI itp.). Jeśli tak, bot omija takie wejścia.
- Kwarantanna: po serii strat na koncie (suma ostatnich 6 transakcji gorsza niż -2 jednostki ryzyka) aktywo jest pomijane przez 7 dni.
- Włączenie: zakładka Bot, „Automatyczny wybór aktywów”. Przy nieaktualnej mapie (> 4 dni) automat nie handluje. W trybie **live** automat działa tylko po dopisaniu `TRADER_AUTO_LIVE=yes` w `trader.env`.
- Nauka nigdy nie zmienia dźwigni, ryzyka, trybu ani kluczy. Podgląd: zakładka Bot, „Nauka systemu” oraz `GET /api/learn`.

### Nawigacja i wygląd (styl Apple)
Wszystkie strony mają wspólny pasek nawigacji: Panel, Analiza ▾ (Rynki, Sytuacje, Badania), Handel ▾ (Okazje, Strategie, Bot), Kalkulator. Na telefonie pasek zamienia się w menu (☰). W pasku jest też przełącznik motywu (auto/jasny/ciemny) i przycisk zwijania wszystkich sekcji.
Każda sekcja (karta z nagłówkiem) zwija się kliknięciem w nagłówek; stan jest pamiętany osobno dla każdej strony. Wygląd: kolory systemowe Apple, duże zaokrąglenia, szklany pasek, jasny i ciemny motyw.

### Rozszerzenia API (api_ext)
Własne adresy GET można dodawać bez edycji `btc_api.py`: plik `/opt/btc/api_ext/nazwa.py` z `GET = {"/api/nazwa": funkcja(state, query) -> (kod, obiekt)}` jest wczytywany przy starcie API (po zmianie: `sudo systemctl restart btc-api`). Aktualizacje programu nie nadpisują tego katalogu. Błąd w rozszerzeniu nie zatrzymuje API, a wbudowanych adresów (np. /api/health) nie da się nadpisać.

### Mój Bybit (zakładka „Mój Bybit”)
Prywatne statystyki prawdziwego konta, tylko do odczytu: wartość konta (z krzywą), pozycje (z ostrzeżeniem o braku stop-lossa i odległością do likwidacji), oczekujące zlecenia, statystyki zamkniętych pozycji za 7/30/90 dni (trafność, profit factor, spadek, wynik dzienny i po aktywach, long/short), skład portfela, opłaty i funding oraz kontrola uprawnień klucza API.
Dane zbiera usługa `btc-account` (co minutę) i zapisuje `/opt/btc/bybit_account.json`; strona pobiera je z `/api/account`. Klucza strona nigdy nie widzi.
Konfiguracja: w `/opt/btc/.env` (albo w panelu /klucze/) ustaw `BYBIT_API_KEY` i `BYBIT_API_SECRET`: klucz utwórz na Bybit jako **Read-Only** (bez handlu i bez wypłat) i ogranicz do IP serwera. To inny klucz niż ten, którego używa bot (`trader.env`).

### Klucze Bybit: testowy i prawdziwy
W panelu `/klucze/` są dwie grupy: **konto TESTOWE** (`BYBIT_TEST_API_KEY` / `BYBIT_TEST_API_SECRET`, używane w trybach testnet i demo) oraz **konto PRAWDZIWE** (`BYBIT_LIVE_API_KEY` / `BYBIT_LIVE_API_SECRET`, jeden klucz do odczytu w zakładce Mój Bybit i do handlu w trybie live). Klucz może mieć odczyt i handel; wypłaty zostaw wyłączone (zakładka Mój Bybit ostrzega tylko o uprawnieniu do wypłat). Stare nazwy `TRADER_API_KEY` i `BYBIT_API_KEY` działają dalej jako rezerwa.
Tryb live nadal wymaga w `trader.env` linii `TRADER_CONFIRM_LIVE=I_ACCEPT_THE_RISK` i `TRADER_MAX_CAPITAL_USDT` (ustawiasz raz). Wiek klucza jest już tylko przypomnieniem i nie zatrzymuje bota.

### Bardzo małe pozycje long bez dźwigni (live)
W `trader.env` ustaw: `TRADER_MODE=live`, `TRADER_CONFIRM_LIVE=I_ACCEPT_THE_RISK`, `TRADER_MAX_CAPITAL_USDT=<mała kwota>`, `TRADER_MAX_LEVERAGE=1`, `TRADER_SYMBOLS=SOLUSDT,XRPUSDT,...`. W zakładce Bot użyj presetu „bardzo małe pozycje LONG bez dźwigni” (tylko long, dźwignia 1, stała wartość pozycji np. 15 USDT, do 2 pozycji) i zapisz. Każda pozycja nadal ma stop-loss. Minimalna wartość zlecenia zależy od aktywa (np. BTC ≈ 80+ USDT, SOL ≈ 18, XRP ≈ 2): gdy ustawiona kwota jest niższa, bot pomija zlecenie i wypisuje w dzienniku wymagane minimum.

### Tryb zatwierdzania pozycji
W zakładce Bot włącz „Wymagaj mojego zatwierdzenia każdej pozycji”. Wtedy bot sam znajduje okazję i zamiast składać zlecenie zgłasza **wniosek** z dokładnymi parametrami (aktywo, kierunek, ilość, wartość, wejście, stop-loss, cel, ryzyko). Wniosek widzisz u góry zakładki Bot i klikasz „Zatwierdź” lub „Odrzuć”. Po zatwierdzeniu bot stawia pozycję sam przy najbliższym przebiegu (do 5 minut), po ponownym sprawdzeniu planu i limitów.
Wniosek wygasa po 12 godzinach bez decyzji, a zatwierdzony po godzinie bez realizacji. Zatwierdzenie dotyczy dokładnie tego planu: jeśli strategia zmieni plan, bot zgłosi nowy wniosek. Strona nadal nie ma kluczy ani nie składa zleceń: kliknięcie zapisuje tylko decyzję, a zlecenie wysyła trader. Tryb działa też w dryrun (bez zlecenia), do testów.

### Zlecenia ze strony i bot w „Mój Bybit”
Zakładka Mój Bybit ma kartę „Bot na moim koncie” (tryb, pauza, wnioski do zatwierdzenia) oraz formularz „Złóż pozycję z tej strony” (long/short, wartość w USDT, stop-loss %, take-profit %, zlecenie rynkowe lub limit) i przyciski „Zamknij” przy pozycjach.
Strona nie ma kluczy: zapisuje prośbę, a usługa `btc-exec` na serwerze sprawdza limity i składa zlecenie. Wymaga w `trader.env`: `TRADER_WEB_ORDERS=yes` (domyślnie wyłączone; po zmianie `sudo systemctl restart btc-exec`). Zlecenia trafiają na konto zgodne z trybem w `trader.env` (demo/testnet/live), a strona pokazuje, które to konto.
Zasady: stop-loss jest zawsze, dźwignia do `TRADER_MAX_LEVERAGE` (maks. 2), suma pozycji nie przekroczy dźwigni × kapitał, ryzyko do stopa do `TRADER_MANUAL_MAX_RISK_PCT` (domyślnie 5%) kapitału, jedna pozycja na aktywo, plik STOP blokuje nowe pozycje (zamknięcie zawsze działa). W trybie dryrun cała ścieżka działa bez wysyłania zleceń.

### Zespół sygnałów (rozbudowany algorytm decyzyjny)
`btc_decision.py` to druga strategia obok klasycznego planu; wybierasz ją w zakładce Bot (Strategia). Działa na świecach dziennych i zwraca long / short / czekaj z pełnym planem (wejście, stop, cele) w tym samym formacie co klasyczny plan, więc przechodzi przez ten sam test wsteczny (walk-forward, koszty) i tę samą mapę przewagi.
- **Sześć rodzin sygnałów** (ocena −1..+1): trend (SMA20/50/200, nachylenie SMA50), momentum (zwroty 20/60/120 dni znormalizowane zmiennością + MACD), wybicie (kanały 20 i 55 dni), powrót do średniej (z-score od SMA20 + RSI), przepływ (wolumen ponad normę po stronie ruchu + OBV), siła względem BTC (dla altów).
- **Reżim rynku** (współczynnik efektywności ruchu z 20 dni): trend, mieszany albo boczny. Od niego zależą wagi rodzin (w rynku bocznym dominuje powrót do średniej, w trendzie trend i momentum). Wagi są stałe i jawne, bez dopasowywania do danych.
- **Decyzja:** long, gdy ocena ≥ +0,30; short, gdy ≤ −0,38; potrzebna zgodność rodzin ≥ 60% i pewność ≥ 55%. Pewność = ocena × zgodność.
- **Stop i cele z reżimu:** stop 1,2–1,6 ATR (lub za ostatnim swingiem), TP1 1,2–1,6R, TP2 1,8–3,0R. Stop szerszy niż 12% ceny: czekamy.
- **Filtry:** BTC pod SMA50 osłabia longi na altach, dzień publikacji makro blokuje wejścia, wysoka zmienność zmniejsza pozycję (×0,6–0,8), funding skrajnie po stronie wejścia zmniejsza pozycję lub blokuje wejście (to nakładka tylko na żywo, może wyłącznie ograniczać).
- **Rozmiar:** pozycja jest skalowana mnożnikiem 0,2–1,0 z zmienności i pewności, nadal w limitach ryzyka, dźwigni i kapitału.
- **Przejrzystość:** zakładka Bot, karta „Decyzje algorytmu”, pokazuje ocenę każdej rodziny, reżim, filtry i pełne uzasadnienie. Dzienna nauka systemu ocenia zespół osobno (kolumna „Zespół sygnałów”) i dopuszcza do automatu tylko aktywa z udowodnioną przewagą.
Uwaga: na losowych danych bocznych algorytm traci (koszty i fałszywe sygnały), tak jak każda taka metoda. Przewaga musi wyjść z testu na prawdziwej historii.

### Co bot nadzoruje na koncie (ważne przy prawdziwym koncie)
Bot zamyka pozycję bez stop-lossa lub z dźwignią powyżej limitu, a także anuluje stare zlecenia, **tylko dla własnych pozycji i zleceń** (aktywa, na których sam postawił zlecenie w ostatnich 30 dniach, oraz zlecenia z jego identyfikatorem). Twoje ręczne pozycje i zlecenia z aplikacji Bybit są nietykane; przy ręcznej pozycji bez stopa lub z dużą dźwignią dostajesz tylko ostrzeżenie raz dziennie. Dawne zachowanie (nadzór nad całym kontem) włączysz w `trader.env`: `TRADER_SUPERVISE=all`.

### Wyjścia z pozycji zgodne z testem (TRADER_EXIT_MODE)
Domyślnie (`staged`) bot prowadzi transakcję tak, jak symuluje ją test wsteczny: wejście ma od razu stop-loss; po wypełnieniu bot ustawia dwa zlecenia limit reduce-only (TP1 na połowę pozycji, TP2 na resztę); po zrealizowaniu TP1 przesuwa stop na cenę wejścia; po 7 dniach od wejścia zamyka resztę rynkowo (stop czasowy); po zamknięciu pozycji sprząta pozostałe cele. Gdy połowa pozycji jest poniżej minimalnej wielkości zlecenia, ustawia jeden cel na całość. Błąd przy ustawianiu celów nie zagraża pozycji (ma stop-loss), a bot ponawia próbę przy następnym przebiegu.
`TRADER_EXIT_MODE=single` przywraca stare zachowanie (jeden cel na całą pozycję). Stop czasowy jest liczony od momentu wykrycia wypełnienia (±5 minut), a nie od zamknięcia świecy dziennej jak w teście. Nie sprawdzałem tego na prawdziwym Bybit: pierwsze transakcje obserwuj na demo.

### Test integracji z Bybit (btc_selftest.py)
Sprawdza na prawdziwych odpowiedziach giełdy założenia, które reszta programu testuje tylko na atrapach. Uruchom po każdej aktualizacji tradera, na koncie demo lub testnecie (klucz testowy z panelu `/klucze/`):
`sudo -u btc /opt/btc/venv/bin/python /opt/btc/btc_selftest.py --mode demo`
Test składa drobne zlecenia kontrolne (BTCUSDT, minimalna wielkość, dźwignia 1) i zawsze sprząta (anuluje zlecenia, zamyka pozycję). Sprawdza m.in.: uwierzytelnienie, pola instrumentu (minimum zlecenia), zlecenie ze stop-lossem w formacie bota, odrzucanie duplikatu `orderLinkId` (idempotencja), anulowanie, pozycję ze stop-lossem i dźwignią, dwa cele reduce-only, przesunięcie stopa (trading-stop), zapytania `settleCoin` oraz `closed-pnl` z symbolem i bez. Wynik: PASS / WARN / FAIL i plik `selftest_report.json` (kod wyjścia 1 przy FAIL). `--read-only` wykonuje same odczyty i jest bezpieczne także na koncie live. Test odmawia składania zleceń w trybie live i dryrun.

### Tryb cienia
`btc_shadow.py` (wywoływany przez dzienną naukę) prowadzi dziennik propozycji planu klasycznego i każdego wariantu zespołu sygnałów (patrz laboratorium) na wszystkich analizowanych aktywach tym samym silnikiem co test wsteczny, ale bez zleceń. Dziennik startuje w dniu pierwszego uruchomienia, więc wyniki są poza próbą (żadna strategia nie mogła być do nich dopasowana). Test sprawdza, że dziennik prowadzony dzień po dniu daje identyczne transakcje jak test wsteczny tego samego okresu. Wyniki: karta „Tryb cienia” w zakładce Bot oraz `GET /api/shadow`. Wnioski wolno wyciągać dopiero po co najmniej 30 transakcjach na strategię.

### Laboratorium wariantów (btc_lab.py): testowanie i dopracowywanie algorytmu w czasie działania
Laboratorium liczy się codziennie razem z nauką (`btc-learn`), wynik widać w zakładce Bot (karta „Laboratorium wariantów”) i pod `GET /api/lab`.
- **Warianty.** Zespół sygnałów ma teraz parametry (`btc_decision.DEFAULTS`). Wbudowane warianty: `v1` (pierwotny), `v2` (siła względem BTC liczona kontrariańsko; **domyślny**), `v1n` (bez siły względnej, kontrola). Każdy wariant idzie przez ten sam kod w teście wstecznym, trybie cienia i u tradera.
- **Diagnoza sygnałów.** Dla każdej rodziny (trend, momentum, wybicie, powrót, wolumen, siła względem BTC) system liczy, czy jej ocena poprzedza wynik 10 dni później: w ostatnich 2 latach i w całej historii. Istotność jest grupowana po blokach 60 dni, bo aktywa poruszają się razem. Stany: potwierdzona, osłabła, bez mocy, ostatnio odwrotnie, odwrotnie.
- **Automatyczni pretendenci.** Gdy rodzina działa odwrotnie, niż zakłada aktywny wariant, w całej historii (|t| ≥ 2,5) i w ostatnich 2 latach (ten sam znak, |t| ≥ 1), system sam tworzy wariant z odwróconą rodziną (`/opt/btc/lab_variants.json`, najwyżej 2 naraz) i zaczyna prowadzić go w cieniu.
- **Test historyczny** wszystkich wariantów odświeża się raz w tygodniu i po każdej zmianie wariantów (`lab_hist.json`).
- **Cień i awans.** Każdy wariant prowadzi dziennik w cieniu od dnia rejestracji. Pretendent dostaje wniosek o awans, gdy ma co najmniej 30 transakcji i 12 tygodni w cieniu, wygrywa z aktywnym wariantem na sumach tygodniowych (p ≤ 0,025 podzielone przez liczbę pretendentów), sam ma wynik ≥ +0,05R i nieujemną historię. **Wariant zmieniasz Ty**: przycisk we wniosku albo „Użyj” w tabeli (ustawienie `ens_variant`). System sam nie podmienia algorytmu.
- **Alarm.** Gdy aktywny wariant ma w cieniu istotnie ujemny wynik (≥ 30 transakcji), w dzienniku nauki i na karcie pojawia się alarm.
- **Wykonanie na żywo.** Trader zapisuje zamknięte transakcje prowadzone w trybie `staged` do `/opt/btc/bot_trades.jsonl` (wynik w R z `closed-pnl` giełdy). Laboratorium zestawia je z tym, co dla tych samych planów policzył symulator. Duża różnica oznacza, że testom nie można ufać (poślizg, niewypełnienia, błąd). Uwaga: jeśli w tym samym czasie handlujesz ręcznie tym samym aktywem, wyniki się zmieszają.
- **Strategia klasyczna.** Gdy bot używa strategii klasycznej, a jej test historyczny jest istotnie ujemny, raz na dobę w dzienniku zdarzeń pojawia się ostrzeżenie. Przełączenie: zakładka Bot, ustawienia, „Zespół sygnałów”.
- **Koszty w każdym teście** (`btc_analysis.py`): prowizja 0,075% na rundę (`PLAN_FEE_PCT`), funding longa 0,03% nominału na dobę (`PLAN_FUNDING_PCT_DAY`; shortowi nic nie doliczamy), poślizg stopa 0,05% ceny (`PLAN_STOP_SLIP_PCT`); świeca otwarta za stopem wypełnia go po cenie otwarcia.
- Sprawdzenie, czy nauka działa: `sudo systemctl status btc-learn.service` oraz `sudo journalctl -u btc-learn -n 30 --no-pager`. Przed poprawką z 10.11 plan klasyczny wywracał naukę na aktywach tańszych niż ok. 15 USD (dzielenie przez zero), więc mapa przewagi i cień mogły w ogóle nie powstawać.
- Czego laboratorium nie zmienia: dźwigni, ryzyka, trybu ani kluczy. Wynik historyczny aktywnego wariantu jest zawyżony (wariant był wybierany na tej historii); rozstrzyga cień.

### Ryzyko portfela i wyłączniki (btc_risk.py)
- **Ryzyko otwarte:** dla każdej pozycji strata, gdy zadziała jej stop (|wejście − stop| × ilość); pozycja bez stopa liczy się jako 10% wartości; stop przesunięty na wejście = brak ryzyka.
- **Ryzyko skorelowane:** pierwiastek z sumy ryzyk ważonych korelacją dziennych zwrotów z 90 dni, skurczoną do 0,6 (kryptowaluty ruszają się razem). Trzy longi na skorelowanych aktywach to więc prawie jedna trzy razy większa pozycja. Limit: `TRADER_MAX_HEAT_PCT` (domyślnie 3% kapitału) oraz ryzyko brutto do 1,5 × ten limit. Nowa pozycja jest zmniejszana (najmniej do 30% planu), aż się zmieści; jeśli się nie mieści, bot jej nie otwiera. Pozycja przeciwna (short przy longu) zmniejsza ryzyko skorelowane.
- **Wyłączniki** blokują tylko NOWE pozycje (nadzór, stopy i cele trwają): seria strat z rzędu (domyślnie 4, pauza 24 h od ostatniej straty), strata tygodniowa (6%, do poniedziałku UTC), spadek kapitału od szczytu (12%; wymaga Twojego odblokowania, także po odrobieniu strat) oraz dzienna strata (2%, jak dotąd). Dotyczą też zleceń składanych ze strony (zamykanie pozycji zawsze działa), a zlecenia ze strony są odrzucane, gdy przekraczałyby limit ryzyka portfela.
- **Odblokowanie:** przycisk w zakładce Bot (w trybie live tylko przy `TRADER_WEB_ORDERS=yes`) albo plik `RISK_RESET` w katalogu bota (`sudo -u btc touch /opt/btc/RISK_RESET`). Po odblokowaniu seria strat i tydzień liczą się od teraz, a szczyt kapitału to bieżący stan konta. Wpłaty i wypłaty zaburzają pomiar spadku, więc po nich odblokuj ręcznie.
- Wszystkie limity ustawisz w zakładce Bot; w trybie live panel może je tylko zaostrzyć względem `trader.env`.

## Scalper (usługa btc-scalp)

Osobny bot na świecach 5 min, działa obok bota dziennego na tym samym koncie i w tym samym trybie (`TRADER_MODE`).
Cztery strategie (powrót do średniej, wybicie, nierównowaga zleceń, przepływ i pozycjonowanie) liczą się w cieniu po kosztach 0,13% nominału.
Pełny opis reguł: `docs/ALGORYTM.md`.

**Bramka.** Zlecenia składa tylko strategia, której dodatni wynik w cieniu nie wygląda na przypadek: z ostatnich 30 dni (najwyżej 300 transakcji)
co najmniej 30 transakcji, średnia co najmniej +0,05R i t co najmniej 2 (transakcje zamknięte w tej samej godzinie liczą się jak jedna obserwacja).
Otwarta bramka zamyka się, gdy średnia spadnie poniżej zera. Sama dodatnia średnia nie wystarcza: na historii strategia tracąca 0,36R
miała średnią ≥ +0,05R w 17% okien po 30 transakcji (badanie: `docs/badania_bramka`).

**Hamulec.** Strategia, której prawdziwe transakcje są istotnie ujemne (co najmniej 10 transakcji z 30 dni, t ≤ −2), nie składa zleceń mimo dodatniego cienia.

**Rozpoznanie (tylko demo i testnet).** Gdy żadna strategia nie przechodzi bramki, bot handluje najlepszą z połową ryzyka, najwyżej 6 razy na dobę,
i tylko taką, która w cieniu nie traci wyraźnie (średnia ≥ −0,10R przy co najmniej 10 transakcjach). `SCALP_EXPLORE=no` wyłącza.
Na koncie live zlecenia wymagają `SCALP_CONFIRM_LIVE=I_ACCEPT_THE_RISK` i przejścia bramki.

**Wspólne limity konta.** Scalper stosuje dźwignię i kapitał ustawione w zakładce Bot (tak jak bot dzienny), pauzę z panelu oraz wyłączniki konta:
dzienną stratę, stratę tygodniową i spadek kapitału od szczytu. Seria strat z rzędu dotyczy tylko bota dziennego i liczy się bez transakcji scalpera
(scalper zamyka wiele małych pozycji dziennie; ma własną przerwę po stracie i własny dzienny limit w R). Pozycji scalpera bot dzienny nie zamyka.

Ustawienia w `trader.env`: `SCALP_SYMBOLS`, `SCALP_RISK_PCT` (0.2), `SCALP_MAX_POSITIONS` (3), `SCALP_MAX_TRADES_DAY` (30),
`SCALP_MAX_DAILY_LOSS_R` (5), `SCALP_MAX_NOTIONAL_X` (1.0). Po zmianie: `sudo systemctl restart btc-scalp`.
Włącznik zleceń jest w zakładce Bot (karta Scalper). Dziennik: `sudo journalctl -u btc-scalp -n 30 --no-pager`.

## Rynek na żywo: przepływ zleceń i pozycjonowanie (btc_flow.py)

Usługa `btc-scalp` co 10 minut pobiera z publicznego API OKX (bez klucza) dane godzinowe dla aktywów scalpera:
wolumen agresywny, stosunek kont long/short, premię kontraktu, open interest, pozycje największych graczy i funding.
Wynik widać w zakładce Bot, sekcja „Rynek na żywo”, oraz pod adresem `/api/flow`. Historia (45 dni) leży w `/opt/btc/flow_hist.json`.

Z czterech składników powstaje wskaźnik łączny. To eksperyment bez potwierdzonej skuteczności: na 53 dniach wypadał dodatnio,
na 2 latach danych połowa składników nie wykazała przewagi. Scalper prowadzi z niego strategię „Przepływ i pozycjonowanie”
tylko w cieniu, a panel liczy trafność wskazań osobno dla okresu po zbudowaniu wskaźnika.

Sprawdzenie, czy serwer ma dostęp do OKX i co moduł widzi (jedno odświeżenie, wydruk):

    sudo -u btc BTC_BOT_DIR=/opt/btc /opt/btc/venv/bin/python /opt/btc/btc_flow.py

Strategia z tego wskaźnika wchodzi najwyżej raz na jeden godzinowy odczyt (po stopie nie wraca na tym samym sygnale).

## Strony Produkty i Mentor

- `produkty.html`: linki do wyszukiwań ofert (OLX, Allegro Lokalnie, Vinted, Marketplace) w okolicy Dębicy i Rzeszowa oraz kalkulator odsprzedaży. Nie pobiera ofert sam; lista kandydatów zapisuje się w przeglądarce.
- `mentor.html`: czat z doradcą biznesowym (ta sama usługa `btc-ai` i ten sam `ANTHROPIC_API_KEY` w `ai.env`, rola `mentor`). Mentor nie dostaje stanu bota ani kluczy, zna tylko profil wpisany na stronie. Odmawia pomocy przy fałszowaniu dokumentów i innych działaniach niezgodnych z prawem. Pytania wliczają się do limitów asystenta (`AI_MAX_PER_DAY`).
