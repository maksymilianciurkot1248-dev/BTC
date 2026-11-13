# Wdrożenie handlu automatycznego (Bybit, dźwignia ≤ x2)

## Kolejność (nie przeskakuj)
1. **dryrun** – kilka dni: `TRADER_MODE=dryrun`, skrypt tylko loguje, co by zrobił.
2. **testnet** – klucz z testnet.bybit.com, `TRADER_MODE=testnet`, min. 2–3 tygodnie.
3. **live, mała kwota** – subkonto z kwotą, którą możesz stracić; `TRADER_MAX_CAPITAL_USDT` = ta kwota.

## Klucz Bybit (live)
- Utwórz **subkonto** i przelej tylko kapitał handlowy.
- Klucz API: *Read-Write*, uprawnienia **tylko Contract → Orders + Positions**. **Bez** Withdraw, Transfer, Spot, Wallet.
- **Ogranicz do IP** serwera (Bybit zrobi klucz bez wygaśnięcia dopiero z whitelistą).
- Osobny klucz tylko do odczytu zostaje dla raportu (`.env` analizatora). Nie używaj jednego klucza do obu.
- Hosting współdzielony zwykle nie ma stałego IP – sprawdź w cPanelu; bez stałego IP wybierz VPS.

## Pliki
- `~/btc_trader/` (POZA public_html): `btc_trader.py`, `btc_analysis.py`, `trader.env` (chmod 600).
- `public_html/btc/`: strona + `btc_report.json` + `.htaccess` z `htaccess_secure.txt`.
- Cron: `*/5 * * * * cd ~/btc_trader && python3 btc_trader.py >> trader.log 2>&1`

## Zabezpieczenia w skrypcie
Dźwignia ustawiana na 2 i sprawdzana (twardy limit 2 w kodzie), nominał ≤ 2× kapitał, stop-loss i TP w zleceniu,
ryzyko 0,5% kapitału na transakcję, limit strat dziennych, limit zleceń na dobę, brak dokładania do pozycji,
idempotentne ID zlecenia, anulowanie starych zleceń, zamknięcie pozycji bez SL lub z za dużą dźwignią,
**wyłącznik: pusty plik `STOP`**. Po błędzie skrypt nie robi nic.

## Czego to NIE chroni
Samej straty z rynku (stop może się prześlizgnąć), awarii giełdy, ani złej strategii – testuj najpierw.
Wyłączenie ręczne: utwórz plik STOP, anuluj zlecenia i zamknij pozycję w aplikacji Bybit.
