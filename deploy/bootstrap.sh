#!/usr/bin/env bash
set -Eeuo pipefail

APP_ROOT="/opt/lucera"
ENV_ROOT="/etc/lucera"

export DEBIAN_FRONTEND=noninteractive
apt-get update
# NCloud images may not expose an IPv6 listener. Prevent package postinst
# scripts from starting the stock nginx config before our IPv4-only config is
# installed.
cat > /usr/sbin/policy-rc.d <<'POLICY'
#!/bin/sh
exit 101
POLICY
chmod 755 /usr/sbin/policy-rc.d
apt-get install -y --no-install-recommends python3 nginx ca-certificates unzip
rm -f /usr/sbin/policy-rc.d
dpkg --configure -a

id -u lucera >/dev/null 2>&1 || useradd --system --home-dir "$APP_ROOT" --shell /usr/sbin/nologin lucera
install -d -o lucera -g lucera "$APP_ROOT" "$APP_ROOT/data/db" "$ENV_ROOT"

if [[ -f "$APP_ROOT/deploy/lucera.env" ]]; then
    install -o root -g root -m 0600 "$APP_ROOT/deploy/lucera.env" "$ENV_ROOT/lucera.env"
fi

install -o root -g root -m 0644 "$APP_ROOT/deploy/systemd/lucera.service" /etc/systemd/system/lucera.service
install -o root -g root -m 0644 "$APP_ROOT/deploy/nginx/lucera.conf" /etc/nginx/sites-available/lucera
ln -sfn /etc/nginx/sites-available/lucera /etc/nginx/sites-enabled/lucera
rm -f /etc/nginx/sites-enabled/default

chown -R lucera:lucera "$APP_ROOT/data"
nginx -t
systemctl daemon-reload
systemctl enable --now lucera
systemctl enable --now nginx
curl --fail --silent --show-error http://127.0.0.1/health >/dev/null
echo "Lucera deployment is healthy"
