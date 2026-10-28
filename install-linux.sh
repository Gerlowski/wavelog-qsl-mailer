#!/usr/bin/env bash
# Installs wavelog-qsl-mailer next to a Wavelog installed on Linux / Raspberry Pi
# (Apache or nginx, as in https://docs.wavelog.org/getting-started/installation/linux/).
#
#   sudo ./install-linux.sh [path to Wavelog, e.g. /var/www/html]
#
# Re-running updates the program and keeps config.ini and the sent-cards state.
set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "Run as root: sudo $0 $*"; exit 1; }
SRC="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
DEST=/opt/wavelog-qsl-mailer
SERVICE=wavelog-qsl-mailer

# Wavelog folder: argument, or the usual places
WAVELOG="${1:-}"
if [ -z "$WAVELOG" ]; then
  for d in /var/www/html /var/www/wavelog /var/www/html/wavelog /srv/wavelog /var/www/*/; do
    [ -f "${d%/}/application/config/config.php" ] && { WAVELOG="${d%/}"; break; }
  done
fi
CONFIG_DIR="$WAVELOG/application/config"
[ -n "$WAVELOG" ] && [ -f "$CONFIG_DIR/config.php" ] || {
  echo "Wavelog not found. Pass its folder, e.g.: sudo $0 /var/www/html"; exit 1; }
echo "Wavelog: $WAVELOG"

# The user that runs the web server can read Wavelog's config; the service runs as that user
RUNAS=www-data
id "$RUNAS" >/dev/null 2>&1 || RUNAS=$(stat -c %U "$CONFIG_DIR/config.php")

# Dependencies: distribution packages (Debian, Ubuntu, Raspberry Pi OS), pip as a fallback
if ! python3 -c 'import pymysql, PIL' 2>/dev/null; then
  if command -v apt-get >/dev/null; then
    apt-get update -q && apt-get install -y -q python3 python3-pymysql python3-pil
  elif command -v dnf >/dev/null; then
    dnf install -y python3 python3-PyMySQL python3-pillow
  else
    python3 -m pip install --break-system-packages PyMySQL Pillow 2>/dev/null || python3 -m pip install PyMySQL Pillow
  fi
fi

mkdir -p "$DEST"
rm -rf "$DEST/qsl_mailer"
cp -r "$SRC/qsl_mailer" "$SRC/config.example.ini" "$DEST/"
cd "$DEST"
if [ ! -f config.ini ]; then
  cp config.example.ini config.ini
  sed -i "s#^config_dir = .*#config_dir = $CONFIG_DIR#" config.ini
  sed -i "s/^since =.*/since = $(date -u +%F)/" config.ini
fi
chown -R "$RUNAS" "$DEST"
chmod 600 config.ini

run() { runuser -u "$RUNAS" -- env PYTHONPATH="$DEST" python3 -m qsl_mailer -c "$DEST/config.ini" "$@"; }

if ! grep -qE '^station_ids *= *[0-9]' config.ini; then
  run --dry-run 2>&1 | sed -n '/Station Locations in this Wavelog/,$p' || true
  read -rp "Station Location IDs to send cards for (comma separated): " IDS
  sed -i "s/^station_ids =.*/station_ids = ${IDS// /}/" config.ini
  echo "Cards will be sent for QSOs since $(grep '^since' config.ini | cut -d= -f2 | xargs) (UTC). Change 'since' in $DEST/config.ini if needed."
fi

echo; echo "Dry run (nothing is sent or written):"
run --dry-run || true
echo; echo "Card previews: $DEST/preview/"

if [ ! -d /run/systemd/system ]; then
  echo; echo "No systemd here. Add this line to the crontab of $RUNAS (crontab -u $RUNAS -e):"
  echo "*/15 * * * * cd $DEST && PYTHONPATH=$DEST python3 -m qsl_mailer -c $DEST/config.ini >> $DEST/qsl-mailer.log 2>&1"
  exit 0
fi

cat > /etc/systemd/system/$SERVICE.service <<UNIT
[Unit]
Description=Wavelog QSL mailer
After=network-online.target mariadb.service mysql.service
Wants=network-online.target

[Service]
User=$RUNAS
WorkingDirectory=$DEST
Environment=PYTHONPATH=$DEST PYTHONUNBUFFERED=1
ExecStart=$(command -v python3) -m qsl_mailer -c $DEST/config.ini --loop 900
Restart=on-failure
RestartSec=60

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload

read -rp "Start sending cards now (service $SERVICE, every 15 min)? [y/N] " OK
case "$OK" in
  y|Y|yes)
    systemctl enable --now $SERVICE
    systemctl restart $SERVICE
    echo "Running. Logs: journalctl -u $SERVICE -f"
    ;;
  *) echo "Not started. Later: sudo systemctl enable --now $SERVICE" ;;
esac
