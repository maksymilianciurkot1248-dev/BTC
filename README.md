# BTC bot

Prywatny system analizy i bot handlujący kontraktami na Bybit (tryby: dryrun, testnet, demo, live).
Kluczy i plików `.env`, `trader.env`, `ai.env` tu nie ma i nie może być: zostają tylko na serwerze.

## Układ
- `btc_*.py` — program (trader, scalper, nauka, laboratorium, API panelu, asystent AI, kolektor danych, kopia zapasowa na Dysk Google, dane użytkownika, kalendarz i Alfred)
- `*.html`, `ui.css`, `ui.js` — panel
- `install_bot.sh` — instalator aktualizacji (pliki, usługi systemd)
- `btc-update` — aktualizacja serwera z tego repozytorium
- `tests/` — testy (bez sieci): `bash tests/run_all.sh`
- `vps/` — pierwsza instalacja serwera i instrukcja
- `docs/ALGORYTM.md` — dokładny opis działania obu botów (reguły, progi, kolejność decyzji)
- `docs/PLAN.md` — plan działania i warunki przed prawdziwymi pieniędzmi
- `docs/ALFRED.md` — Alfred: plan dnia, kalendarz, asystent (co działa, źródła, plan rozwoju)
- `docs/badania_*` — badania z wynikami (także negatywnymi) i skryptami do ich powtórzenia

## Aktualizacja serwera
```bash
sudo btc-update
```

## Jednorazowe podłączenie serwera (PowerShell, folder z kluczem)
Krok 1: utworzenie klucza serwera i wypisanie jego części publicznej:
```bash
ssh -i .\ssh-key-2026-11-01.key ubuntu@141.147.29.90 "sudo install -d -o btc -g btc /opt/btc-repo /opt/btc/.ssh; sudo test -f /opt/btc/.ssh/repo_key || sudo -u btc ssh-keygen -q -t ed25519 -N '' -f /opt/btc/.ssh/repo_key; sudo cat /opt/btc/.ssh/repo_key.pub"
```
Wypisaną linię dodaj w GitHub: repozytorium → Settings → Deploy keys → Add deploy key (bez „Allow write access”).

Krok 2: pobranie repozytorium, pierwsza aktualizacja i włączenie automatu:
```bash
ssh -i .\ssh-key-2026-11-01.key ubuntu@141.147.29.90 "sudo -u btc git -c core.sshCommand='ssh -i /opt/btc/.ssh/repo_key -o UserKnownHostsFile=/opt/btc/.ssh/known_hosts -o StrictHostKeyChecking=accept-new' clone git@github.com:maksymilianciurkot1248-dev/BTC.git /opt/btc-repo && sudo -u btc git -C /opt/btc-repo config core.sshCommand 'ssh -i /opt/btc/.ssh/repo_key -o UserKnownHostsFile=/opt/btc/.ssh/known_hosts' && sudo install -m 755 /opt/btc-repo/btc-update /usr/local/bin/btc-update && sudo btc-update --force && sudo btc-update --auto-on"
```
Od tej chwili serwer sam instaluje każdą zmianę z gałęzi `main` w ciągu 10 minut. Wyłączenie: `sudo btc-update --auto-off`.
