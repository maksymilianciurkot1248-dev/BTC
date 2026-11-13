# BTC bot

Prywatny system analizy i bot handlujący kontraktami na Bybit (tryby: dryrun, testnet, demo, live).
Kluczy i plików `.env`, `trader.env`, `ai.env` tu nie ma i nie może być: zostają tylko na serwerze.

## Układ
- `btc_*.py` — program (trader, scalper, nauka, laboratorium, API panelu, asystent AI, kolektor danych)
- `*.html`, `ui.css`, `ui.js` — panel
- `install_bot.sh` — instalator aktualizacji (pliki, usługi systemd)
- `btc-update` — aktualizacja serwera z tego repozytorium
- `vps/` — pierwsza instalacja serwera i instrukcja
- `docs/` — analiza programu i plany

## Aktualizacja serwera
```bash
sudo btc-update
```

## Jednorazowe podłączenie serwera do repozytorium
```bash
sudo install -d -o btc -g btc /opt/btc-repo /opt/btc/.ssh
sudo -u btc ssh-keygen -t ed25519 -N "" -f /opt/btc/.ssh/repo_key
sudo cat /opt/btc/.ssh/repo_key.pub
```
Wypisany klucz publiczny dodaj w GitHub: repozytorium → Settings → Deploy keys → Add deploy key (bez „Allow write access”).
```bash
sudo -u btc git -c core.sshCommand="ssh -i /opt/btc/.ssh/repo_key -o StrictHostKeyChecking=accept-new" clone git@github.com:maksymilianciurkot1248-dev/BTC.git /opt/btc-repo
sudo -u btc git -C /opt/btc-repo config core.sshCommand "ssh -i /opt/btc/.ssh/repo_key"
sudo install -m 755 /opt/btc-repo/btc-update /usr/local/bin/btc-update
```
