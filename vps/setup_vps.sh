#!/usr/bin/env bash
# Instalacja panelu BTC + tradera na świeżym VPS (Ubuntu 22.04/24.04 lub Debian 12). Uruchom jako root:
#   sudo DOMAIN=btc.ciurkot.pl EMAIL=ty@example.com bash setup_vps.sh
# Wymaga: rekord DNS A dla DOMAIN wskazuje na IP tego VPS (certyfikat HTTPS z Let's Encrypt).
# Skrypt NIE wpisuje kluczy API i NIE włącza handlu: tryb startowy to dryrun.
set -euo pipefail
: "${DOMAIN:?ustaw DOMAIN=twoja.domena}" "${EMAIL:?ustaw EMAIL=adres@do.certyfikatu}"
[ "$(id -u)" -eq 0 ] || { echo "uruchom jako root"; exit 1; }
SRC="$(cd "$(dirname "$0")" && pwd)/app"
APP=/opt/btc; WEB=/var/www/btc; U=btc

echo "== pakiety"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3 python3-venv nginx apache2-utils ufw fail2ban certbot python3-certbot-nginx chrony unattended-upgrades curl

echo "== użytkownik bez logowania i katalogi"
id -u $U >/dev/null 2>&1 || useradd --system --home-dir $APP --shell /usr/sbin/nologin $U
install -d -o $U -g $U -m 700 $APP
install -d -o $U -g www-data -m 750 $WEB
install -m 700 -o $U -g $U "$SRC/btc_trader.py" "$SRC/btc_analysis.py" "$SRC/btc_extras.py" "$SRC/btc_collect.py" "$SRC/btc_research.py" "$SRC/btc_markets.py" "$SRC/btc_net.py" "$SRC/btc_situations.py" "$SRC/btc_live.py" "$SRC/btc_botcfg.py" "$SRC/btc_decision.py" "$SRC/btc_account.py" "$SRC/btc_exec.py" "$SRC/btc_risk.py" "$SRC/btc_lab.py" "$SRC/btc_copy.py" "$SRC/btc_ai.py" "$SRC/btc_scalp.py" "$SRC/btc_flow.py" "$SRC/btc_shadow.py" "$SRC/btc_selftest.py" "$SRC/btc_edge.py" "$SRC/btc_learn.py" "$SRC/btc_doctor.py" "$SRC/btc_opportunities.py" "$SRC/btc_userdata.py" "$SRC/btc_backup.py" "$SRC/btc_agenda.py" "$SRC/btc_alfred.py" "$SRC/btc_api.py" "$SRC/btc_strategies.py" "$SRC/btc_stream.py" $APP/
install -m 640 -o $U -g www-data "$SRC/index.html" "$SRC/kalkulator.html" "$SRC/badania.html" "$SRC/rynki.html" "$SRC/sytuacje.html" "$SRC/okazje.html" "$SRC/strategie.html" "$SRC/bot.html" "$SRC/bybit.html" "$SRC/ui.css" "$SRC/ui.js" "$SRC/favicon.svg" $WEB/
[ -d $APP/venv ] || sudo -u $U python3 -m venv $APP/venv
sudo -u $U $APP/venv/bin/pip install --quiet coingecko_sdk websockets anthropic
# pliki konfiguracyjne (tylko jeśli ich jeszcze nie ma — nie nadpisujemy Twoich kluczy)
if [ ! -f $APP/trader.env ]; then
cat > $APP/trader.env <<'EOT'
TRADER_MODE=dryrun
TRADER_API_KEY=
TRADER_API_SECRET=
TRADER_MAX_CAPITAL_USDT=300
TRADER_RISK_PCT=0.5
TRADER_MAX_LEVERAGE=2
TRADER_MAX_DAILY_LOSS_PCT=2
TRADER_MAX_ORDERS_PER_DAY=2
TRADER_KEY_CREATED=2026-01-01
TRADER_EXPECTED_IP=
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
EOT
fi
if [ ! -f $APP/.env ]; then
cat > $APP/.env <<'EOT'
COINGECKO_API_KEY=
COINGECKO_ENV=demo
NEWSDATA_API_KEY=
# klucz Bybit TYLKO DO ODCZYTU (osobny od handlowego):
BYBIT_API_KEY=
BYBIT_API_SECRET=
BYBIT_PRIVACY=masked
# --- Darmowe klucze do bazy danych (opcjonalne; źródło bez klucza jest pomijane) ---
FRED_API_KEY=
CMC_API_KEY=
GLASSNODE_API_KEY=
WHALE_ALERT_API_KEY=
# Spółki: fundamenty USA (Finnhub, 60 zapytań/min) i zapas cen USA (Tiingo)
FINNHUB_API_KEY=
TIINGO_API_KEY=
# Zapas dla Stooq (surowce, waluty, indeksy, akcje USA): Twelve Data, darmowy klucz (8 zapytań/min)
TWELVEDATA_API_KEY=
# Drugi zapas: Polygon.io, darmowy klucz (5 zapytan/min, ok. 2 lata historii): waluty, metale, akcje USA
POLYGON_API_KEY=
# Opcjonalnie: kontakt w nagłówku User-Agent (Wikimedia go oczekuje)
BTC_UA_CONTACT=
EOT
fi
chown $U:$U $APP/trader.env $APP/.env; chmod 600 $APP/trader.env $APP/.env

echo "== systemd (z utwardzeniem)"
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
cat > /etc/systemd/system/btc-trader.service <<EOT
[Unit]
Description=BTC trader (Bybit, dzwignia <= x2)
After=network-online.target
Wants=network-online.target
[Service]
Type=oneshot
User=$U
Group=$U
WorkingDirectory=$APP
ExecStart=$APP/venv/bin/python $APP/btc_trader.py
ReadWritePaths=$APP
TimeoutStartSec=120
$HARDEN
EOT
cat > /etc/systemd/system/btc-trader.timer <<'EOT'
[Unit]
Description=BTC trader co 5 minut
[Timer]
OnCalendar=*:0/5
AccuracySec=10s
[Install]
WantedBy=timers.target
EOT
cat > /etc/systemd/system/btc-report.service <<EOT
[Unit]
Description=BTC raport analizy (do panelu WWW)
After=network-online.target
Wants=network-online.target
[Service]
Type=oneshot
User=$U
Group=www-data
WorkingDirectory=$APP
ExecStart=$APP/venv/bin/python $APP/btc_analysis.py --quiet --web-json $WEB/btc_report.json
ReadWritePaths=$APP $WEB
TimeoutStartSec=300
$HARDEN
EOT
cat > /etc/systemd/system/btc-report.timer <<'EOT'
[Unit]
Description=BTC raport co 2 godziny (limit planu Demo CoinGecko: ok. 10 tys. zapytan/mies.)
[Timer]
OnCalendar=0/2:07
RandomizedDelaySec=60
Persistent=true
[Install]
WantedBy=timers.target
EOT
cat > /etc/systemd/system/btc-collect.service <<EOT
[Unit]
Description=BTC kolektor danych do bazy SQLite
After=network-online.target
Wants=network-online.target
[Service]
Type=oneshot
User=$U
Group=$U
WorkingDirectory=$APP
ExecStart=$APP/venv/bin/python $APP/btc_collect.py update
ReadWritePaths=$APP
TimeoutStartSec=900
$HARDEN
EOT
cat > /etc/systemd/system/btc-collect.timer <<'EOT'
[Unit]
Description=BTC kolektor co 30 minut
[Timer]
OnCalendar=*:07,37
RandomizedDelaySec=60
Persistent=true
[Install]
WantedBy=timers.target
EOT
cat > /etc/systemd/system/btc-research.service <<EOT
[Unit]
Description=BTC badania (analiza bazy danych)
[Service]
Type=oneshot
User=$U
Group=www-data
WorkingDirectory=$APP
ExecStart=$APP/venv/bin/python $APP/btc_research.py --quiet --out $WEB/btc_research.json
ReadWritePaths=$APP $WEB
TimeoutStartSec=1200
Nice=10
$HARDEN
EOT
cat > /etc/systemd/system/btc-research.timer <<'EOT'
[Unit]
Description=BTC badania raz dziennie
[Timer]
OnCalendar=*-*-* 03:40:00
Persistent=true
[Install]
WantedBy=timers.target
EOT
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
cat > /etc/systemd/system/btc-learn.timer <<'EOT'
[Unit]
Description=Nauka systemu raz dziennie
[Timer]
OnCalendar=*-*-* 03:20:00
Persistent=true
[Install]
WantedBy=timers.target
EOT
cat > /etc/systemd/system/btc-markets.service <<EOT
[Unit]
Description=BTC rynki: waluty FX i ranking spolek
[Service]
Type=oneshot
User=$U
Group=www-data
WorkingDirectory=$APP
ExecStart=$APP/venv/bin/python $APP/btc_markets.py analyze --out $WEB/btc_markets.json
ReadWritePaths=$APP $WEB
TimeoutStartSec=900
Nice=10
$HARDEN
EOT
cat > /etc/systemd/system/btc-markets.timer <<'EOT'
[Unit]
Description=Analiza rynkow raz dziennie
[Timer]
OnCalendar=*-*-* 04:05:00
Persistent=true
[Install]
WantedBy=timers.target
EOT
cat > /etc/systemd/system/btc-situations.service <<EOT
[Unit]
Description=BTC analiza sytuacji wplywajacych na ruch
[Service]
Type=oneshot
User=$U
Group=www-data
WorkingDirectory=$APP
ExecStart=$APP/venv/bin/python $APP/btc_situations.py --out $WEB/btc_situations.json
ReadWritePaths=$APP $WEB
TimeoutStartSec=900
Nice=10
$HARDEN
EOT
cat > /etc/systemd/system/btc-situations.timer <<'EOT'
[Unit]
Description=Analiza sytuacji co 6 godzin
[Timer]
OnCalendar=*-*-* 00,06,12,18:25:00
Persistent=true
[Install]
WantedBy=timers.target
EOT
cat > /etc/systemd/system/btc-live.service <<EOT
[Unit]
Description=BTC notowania na biezaco (live.json)
After=network-online.target
Wants=network-online.target
[Service]
User=$U
Group=www-data
WorkingDirectory=$APP
ExecStart=$APP/venv/bin/python $APP/btc_live.py --out $WEB/live.json --interval 10
Restart=always
RestartSec=5
ReadWritePaths=$WEB
$HARDEN
EOT
cat > /etc/systemd/system/btc-api.service <<EOT
[Unit]
Description=BTC API skanera okazji (tylko 127.0.0.1, bez zlecen)
After=network-online.target btc-live.service
Wants=network-online.target
[Service]
User=$U
Group=www-data
WorkingDirectory=$APP
ExecStart=$APP/venv/bin/python $APP/btc_api.py --db $APP/btc_data.db --live $WEB/live.json --port 8787
Restart=always
RestartSec=5
ReadWritePaths=$APP
IPAddressAllow=localhost
IPAddressDeny=any
$HARDEN
EOT
cat > /etc/systemd/system/btc-opp-replay.service <<EOT
[Unit]
Description=BTC symulacja okazji na historii (walk-forward)
[Service]
Type=oneshot
User=$U
Group=$U
WorkingDirectory=$APP
ExecStart=$APP/venv/bin/python $APP/btc_opportunities.py replay
ReadWritePaths=$APP
TimeoutStartSec=1800
Nice=15
$HARDEN
EOT
cat > /etc/systemd/system/btc-opp-replay.timer <<'EOT'
[Unit]
Description=Symulacja okazji raz dziennie
[Timer]
OnCalendar=*-*-* 05:10:00
Persistent=true
[Install]
WantedBy=timers.target
EOT
cat > /etc/systemd/system/btc-strategies.service <<EOT
[Unit]
Description=BTC strategie portfelowe (test historyczny i skład na dzis)
[Service]
Type=oneshot
User=$U
Group=www-data
WorkingDirectory=$APP
ExecStart=$APP/venv/bin/python $APP/btc_strategies.py --db $APP/btc_data.db --out $WEB/btc_strategies.json
ReadWritePaths=$APP $WEB
TimeoutStartSec=1800
Nice=10
$HARDEN
EOT
cat > /etc/systemd/system/btc-strategies.timer <<'EOT'
[Unit]
Description=Strategie portfelowe raz dziennie
[Timer]
OnCalendar=*-*-* 05:40:00
Persistent=true
[Install]
WantedBy=timers.target
EOT
cat > /etc/systemd/system/btc-stream.service <<EOT
[Unit]
Description=BTC strumien danych na zywo (WebSockety: ceny, transakcje, likwidacje, DVOL, bloki)
After=network-online.target
Wants=network-online.target
[Service]
User=$U
Group=www-data
WorkingDirectory=$APP
ExecStart=$APP/venv/bin/python $APP/btc_stream.py --out $WEB/stream.json --db $APP/btc_data.db
Restart=always
RestartSec=5
ReadWritePaths=$APP $WEB
$HARDEN
EOT
systemctl daemon-reload
systemctl enable --now btc-stream.service btc-strategies.timer btc-live.service btc-api.service btc-situations.timer btc-opp-replay.timer
systemctl enable --now btc-markets.timer
systemctl enable --now btc-collect.timer btc-research.timer btc-learn.timer btc-account.timer btc-exec.service
systemctl enable --now btc-report.timer
systemctl enable btc-trader.timer   # włączony, ale w trybie dryrun nic nie wysyła
systemctl start btc-trader.timer

echo "== nginx + HTTPS + hasło"
if [ ! -f /etc/nginx/.htpasswd_btc ]; then
  read -rp "Login do panelu: " L
  htpasswd -c -B /etc/nginx/.htpasswd_btc "$L"
fi
chmod 640 /etc/nginx/.htpasswd_btc; chown root:www-data /etc/nginx/.htpasswd_btc
cat > /etc/nginx/conf.d/btc_limits.conf <<'EOT'
limit_req_zone $binary_remote_addr zone=btc_auth:10m rate=10r/s;
EOT
install -d -m 755 /var/www/certbot
# etap 1: tylko port 80 (wyzwanie ACME bez hasła + przekierowanie)
cat > /etc/nginx/sites-available/btc <<EOT
server {
    listen 80;
    server_name $DOMAIN;
    server_tokens off;
    location ^~ /.well-known/acme-challenge/ { root /var/www/certbot; auth_basic off; }
    location / { return 301 https://\$host\$request_uri; }
}
EOT
ln -sf /etc/nginx/sites-available/btc /etc/nginx/sites-enabled/btc
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx
certbot certonly --webroot -w /var/www/certbot -d "$DOMAIN" -m "$EMAIL" --agree-tos --no-eff-email -n
install -d /etc/letsencrypt/renewal-hooks/deploy
printf '#!/bin/sh\nsystemctl reload nginx\n' > /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh
chmod +x /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh
# etap 2: pełna konfiguracja HTTPS z hasłem
cat > /etc/nginx/sites-available/btc <<EOT
server {
    listen 80;
    server_name $DOMAIN;
    server_tokens off;
    location ^~ /.well-known/acme-challenge/ { root /var/www/certbot; auth_basic off; }
    location / { return 301 https://\$host\$request_uri; }
}
server {
    listen 443 ssl http2;
    server_name $DOMAIN;
    server_tokens off;
    ssl_certificate /etc/letsencrypt/live/$DOMAIN/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/$DOMAIN/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_session_cache shared:SSL:10m;
    root $WEB;
    index index.html;
    add_header Strict-Transport-Security "max-age=31536000" always;
    add_header X-Content-Type-Options nosniff always;
    add_header Referrer-Policy no-referrer always;
    add_header X-Frame-Options DENY always;
    add_header X-Robots-Tag "noindex, nofollow" always;
    add_header Content-Security-Policy "default-src 'self'; connect-src 'self' wss://stream.bybit.com https://api.bybit.com; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'" always;
    gzip on; gzip_types application/json text/css application/javascript;

    auth_basic "Panel BTC";
    auth_basic_user_file /etc/nginx/.htpasswd_btc;
    limit_req zone=btc_auth burst=30 nodelay;
    # opcjonalnie tylko Twoje IP (obie warstwy): satisfy all; allow 203.0.113.10; deny all;

    location ~ /\. { deny all; }
    location ~* \.(py|env|log|lock|tmp)\$ { deny all; }
    location ~ ^/(btc_(report|horizon|research|markets|situations|strategies)|live|stream)\.json\$ { expires -1; }
    location /api/ {
        proxy_pass http://127.0.0.1:8787;
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-For \$remote_addr;
        proxy_read_timeout 30s;
        client_max_body_size 8k;
        limit_req zone=btc_auth burst=60 nodelay;
    }
    location / { try_files \$uri \$uri/ =404; }
}
EOT
nginx -t && systemctl reload nginx

echo "== zapora, fail2ban, aktualizacje"
# Oracle Cloud: obrazy Ubuntu mają w /etc/iptables/rules.v4 regułę REJECT blokującą 80/443. Zastępujemy ją, resztę robi ufw.
if [ -f /etc/iptables/rules.v4 ] && grep -q REJECT /etc/iptables/rules.v4; then
  cp /etc/iptables/rules.v4 /etc/iptables/rules.v4.bak
  printf '*filter\n:INPUT ACCEPT [0:0]\n:FORWARD ACCEPT [0:0]\n:OUTPUT ACCEPT [0:0]\nCOMMIT\n' > /etc/iptables/rules.v4
  iptables -F INPUT 2>/dev/null || true
  echo "Oracle: usunięto domyślne REJECT z iptables (kopia: rules.v4.bak); filtrowaniem zajmuje się ufw"
fi
ufw default deny incoming; ufw default allow outgoing
ufw allow OpenSSH; ufw allow 80/tcp; ufw allow 443/tcp
ufw --force enable
cat > /etc/fail2ban/jail.d/btc.local <<'EOT'
[sshd]
enabled = true
maxretry = 4
bantime = 1h
[nginx-http-auth]
enabled = true
maxretry = 5
bantime = 1h
EOT
systemctl enable --now fail2ban; systemctl restart fail2ban
systemctl enable --now chrony unattended-upgrades
# SSH: wyłącz hasła tylko jeśli jest zainstalowany klucz (inaczej zablokowałbyś sobie dostęp)
if cat /root/.ssh/authorized_keys /home/*/.ssh/authorized_keys 2>/dev/null | grep -q .; then
  printf 'PasswordAuthentication no\nPermitRootLogin prohibit-password\n' > /etc/ssh/sshd_config.d/99-btc.conf
  systemctl reload ssh 2>/dev/null || systemctl reload sshd
  echo "SSH: logowanie hasłem wyłączone (jest klucz w authorized_keys)"
else
  echo "UWAGA: dodaj klucz SSH do /root/.ssh/authorized_keys i wyłącz logowanie hasłem (PasswordAuthentication no)"
fi

IP=$(curl -s https://api.ipify.org || true)
cat <<EOT

GOTOWE. Panel: https://$DOMAIN/   (hasło ustawione)
IP wychodzące tego VPS (do whitelisty klucza Bybit i TRADER_EXPECTED_IP): $IP

Dalej (patrz INSTRUKCJA_VPS.md):
  1. nano $APP/.env          (CoinGecko, newsdata, klucz Bybit tylko do odczytu)
  2. nano $APP/trader.env    (zostaw TRADER_MODE=dryrun na początek)
  3. sudo systemctl start btc-report.service ; journalctl -u btc-report -n 30
  3b. Jednorazowo historia do bazy (kilka minut):
        sudo -u btc $APP/venv/bin/python $APP/btc_collect.py backfill
        sudo -u btc $APP/venv/bin/python $APP/btc_collect.py status
        sudo systemctl start btc-research.service    # potem: https://$DOMAIN/badania.html
        sudo systemctl start btc-markets.service     # potem: https://$DOMAIN/rynki.html (waluty, złoto, spółki)
        sudo systemctl start btc-situations.service  # potem: https://$DOMAIN/sytuacje.html
        sudo systemctl start btc-strategies.service  # potem: https://$DOMAIN/strategie.html (osobne okno ze strategiami)
        sudo systemctl start btc-opp-replay.service  # potem: https://$DOMAIN/okazje.html (skaner okazji, raport, pozycje)
  0. NAJPIERW diagnostyka źródeł:  sudo -u btc $APP/venv/bin/python $APP/btc_doctor.py   (i wklej wynik, jeśli są BŁĘDY)
        # lista spółek do edycji: sudo -u btc $APP/venv/bin/python $APP/btc_markets.py watchlist ; nano $APP/watchlist.json
  4. journalctl -u btc-trader -f
EOT
