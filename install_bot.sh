#!/usr/bin/env bash
# Aktualizacja: zakladka Bot, trader wieloaktywowy, automatyczny wybor aktywow i nauka systemu.
# Uruchom na serwerze z tego katalogu: sudo bash install_bot.sh
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "Uruchom: sudo bash install_bot.sh"; exit 1; }
cd "$(dirname "$0")"
PY="btc_analysis.py btc_botcfg.py btc_decision.py btc_edge.py btc_learn.py btc_lab.py btc_copy.py btc_ai.py btc_scalp.py btc_flow.py btc_account.py btc_exec.py btc_risk.py btc_shadow.py btc_selftest.py btc_trader.py btc_api.py"
WEBF="bybit.html bot.html index.html okazje.html rynki.html sytuacje.html badania.html strategie.html kalkulator.html produkty.html mentor.html ui.css ui.js"
for f in $PY $WEBF; do
  [ -f "$f" ] || { echo "Brak pliku $f w paczce"; exit 1; }
done
[ -d /opt/btc ] && [ -d /var/www/btc ] || { echo "Nie widze /opt/btc lub /var/www/btc: najpierw zainstaluj program (setup_vps.sh)"; exit 1; }
APP=/opt/btc; U=btc
HARDEN='NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
RestrictNamespaces=true
LockPersonality=true
SystemCallArchitectures=native
CapabilityBoundingSet=
UMask=0027'
STAMP=$(date +%Y%m%d-%H%M%S)
cp -a $APP/btc_trader.py "$APP/btc_trader.py.bak-$STAMP" 2>/dev/null || true
cp -a $APP/btc_api.py "$APP/btc_api.py.bak-$STAMP" 2>/dev/null || true
cp -a $APP/btc_analysis.py "$APP/btc_analysis.py.bak-$STAMP" 2>/dev/null || true
if grep -q "api/ibkr" "$APP/btc_api.py" 2>/dev/null && ! grep -q "api/ibkr" btc_api.py; then
  echo "UWAGA: obecny btc_api.py ma recznie dodany adres /api/ibkr, a nowy plik go nie zawiera."
  echo "       Kopia starego pliku: $APP/btc_api.py.bak-$STAMP. Po instalacji przywroc /api/ibkr (patrz api_ext)."
fi
install -d -o $U -g $U -m 750 $APP/api_ext
install -m 700 -o $U -g $U $PY $APP/
mkdir -p /var/www/btc/backup-$STAMP && cp -a /var/www/btc/*.html /var/www/btc/ui.css /var/www/btc/ui.js /var/www/btc/backup-$STAMP/ 2>/dev/null || true
install -m 640 -o $U -g www-data $WEBF /var/www/btc/
cat > /etc/systemd/system/btc-learn.service <<EOT
[Unit]
Description=BTC nauka systemu: wszechswiat aktywow, historia i mapa przewagi
Wants=network-online.target
After=network-online.target
[Service]
Type=oneshot
User=$U
Group=$U
WorkingDirectory=$APP
ExecStart=$APP/venv/bin/python $APP/btc_learn.py --db $APP/btc_data.db --dir $APP
ReadWritePaths=$APP
TimeoutStartSec=1800
Nice=10
$HARDEN
EOT
cat > /etc/systemd/system/btc-account.service <<EOT
[Unit]
Description=BTC moj Bybit: stan i statystyki konta (tylko odczyt)
Wants=network-online.target
After=network-online.target
[Service]
Type=oneshot
User=$U
Group=$U
WorkingDirectory=$APP
ExecStart=$APP/venv/bin/python $APP/btc_account.py --db $APP/btc_data.db --dir $APP
ReadWritePaths=$APP
TimeoutStartSec=300
Nice=10
$HARDEN
EOT
cat > /etc/systemd/system/btc-account.timer <<'EOT'
[Unit]
Description=Odswiezanie danych konta Bybit co minute
[Timer]
OnBootSec=30
OnUnitActiveSec=60
AccuracySec=5s
[Install]
WantedBy=timers.target
EOT
cat > /etc/systemd/system/btc-exec.service <<EOT
[Unit]
Description=BTC wykonawca zlecen ze strony (dziala tylko przy TRADER_WEB_ORDERS=yes)
Wants=network-online.target
After=network-online.target
[Service]
User=$U
Group=$U
WorkingDirectory=$APP
ExecStart=$APP/venv/bin/python $APP/btc_exec.py
Restart=always
RestartSec=5
ReadWritePaths=$APP
TimeoutStopSec=15
$HARDEN
EOT
cat > /etc/systemd/system/btc-scalp.service <<EOT
[Unit]
Description=BTC scalper (swiece 5 min; strategie w cieniu, zlecenia tylko przez bramke; tryb jak bot dzienny)
Wants=network-online.target
After=network-online.target
[Service]
User=$U
Group=$U
WorkingDirectory=$APP
ExecStart=$APP/venv/bin/python $APP/btc_scalp.py
Restart=always
RestartSec=15
ReadWritePaths=$APP
MemoryMax=300M
TimeoutStopSec=15
$HARDEN
EOT
install -d -m 750 -o $U -g $U $APP/ai_jobs
cat > /etc/systemd/system/btc-ai.service <<EOT
[Unit]
Description=BTC asystent AI (jedyna usluga z kluczem Anthropic; tylko czyta stan bota, bez zlecen)
Wants=network-online.target
After=network-online.target
[Service]
User=$U
Group=$U
WorkingDirectory=$APP
ExecStart=$APP/venv/bin/python $APP/btc_ai.py
Restart=always
RestartSec=5
ReadWritePaths=$APP/ai_jobs
MemoryMax=400M
TimeoutStopSec=10
$HARDEN
EOT
cat > /etc/systemd/system/btc-learn.timer <<'EOT'
[Unit]
Description=Nauka systemu raz dziennie
[Timer]
OnCalendar=*-*-* 03:20:00
Persistent=true
[Install]
WantedBy=timers.target
EOT
systemctl daemon-reload
systemctl enable --now btc-learn.timer btc-account.timer btc-exec.service btc-ai.service btc-scalp.service
# asystent AI: pakiet anthropic i szablon pliku z kluczem (klucz wpisujesz sam)
sudo -u $U $APP/venv/bin/pip install --quiet anthropic || echo "UWAGA: nie udalo sie zainstalowac pakietu anthropic (asystent AI bedzie niedostepny)"
[ -f $APP/ai.env ] || { printf '# Klucz API Anthropic dla asystenta w panelu (console.anthropic.com). Po wpisaniu: sudo systemctl restart btc-ai\nANTHROPIC_API_KEY=\n# AI_MODEL=claude-opus-5-5\n# AI_EFFORT=low\n# AI_MAX_PER_DAY=100\n' > $APP/ai.env; chown $U:$U $APP/ai.env; chmod 600 $APP/ai.env; }
systemctl restart btc-api.service btc-exec.service btc-ai.service btc-scalp.service
systemctl start --no-block btc-learn.service      # pierwsza nauka od razu (kilka minut)
systemctl start btc-trader.service || true
sleep 2
echo "btc-api: $(systemctl is-active btc-api.service)  btc-learn.timer: $(systemctl is-active btc-learn.timer)  btc-account.timer: $(systemctl is-active btc-account.timer)  btc-exec: $(systemctl is-active btc-exec.service)"
code=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8787/api/learn || true)
echo "API /api/learn: HTTP $code (oczekiwane 200)"
[ "$code" = 200 ] && echo "GOTOWE: https://btc.ciurkot.pl/bot.html  (pierwsza mapa przewagi pojawi sie po kilku minutach: sudo journalctl -u btc-learn -f)" || { journalctl -u btc-api -n 20 --no-pager; exit 1; }
