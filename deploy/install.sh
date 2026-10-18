#!/usr/bin/env bash
# AI Rádió telepítése Ubuntu 24.04-re (Oracle Cloud Always Free, ARM is jó).
# Futtatás a repó gyökeréből:  sudo ./deploy/install.sh
# Újrafuttatható: a meglévő beállításokat és jelszavakat nem írja felül.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
APP=/opt/airadio
ETC=/etc/airadio
DATA=/var/lib/airadio
LOGDIR=/var/log/airadio-icecast
ICECAST_PORT=8000
LIVE_PORT=8005

if [[ $EUID -ne 0 ]]; then
  echo "Rootként futtasd: sudo $0" >&2
  exit 1
fi

echo "== Csomagok"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q liquidsoap icecast2 ffmpeg python3-venv iptables-persistent openssl rsync media-types

# Az Ubuntu saját icecast2 szolgáltatása helyett a sajátunk fut, saját beállítással.
systemctl disable --now icecast2 2>/dev/null || true

timedatectl set-timezone Europe/Budapest

echo "== Felhasználó és mappák"
id airadio >/dev/null 2>&1 || useradd --system --home-dir "$DATA" --shell /usr/sbin/nologin airadio
install -d -o airadio -g airadio "$DATA" "$DATA"/{news,backup,voices,state,jingles,log} "$LOGDIR"
# Az irányítópult (airadio felhasználó) írja a radio.toml-t, ezért a csoport írhat a mappába.
install -d -o root -g airadio -m 770 "$ETC"
# A telepítő felhasználó (pl. ubuntu) is olvashassa a beállításokat: `airadio status`.
if [[ -n ${SUDO_USER:-} && $SUDO_USER != root ]]; then
  usermod -aG airadio "$SUDO_USER"
fi

echo "== Program"
install -d "$APP"
rsync -a --delete "$REPO/radio/" "$APP/radio/"
# Böngészős lejátszó az Icecast gyökércímén (http://<szerver>:8000/)
install -m 644 "$REPO/airadio/web/player.html" "$REPO/airadio/web/viz.js" /usr/share/icecast2/web/
[[ -d $APP/venv ]] || python3 -m venv "$APP/venv"
"$APP/venv/bin/pip" install -q --upgrade pip
"$APP/venv/bin/pip" install -q "$REPO"
ln -sf "$APP/venv/bin/airadio" /usr/local/bin/airadio

echo "== Beállítások"
if [[ ! -f $ETC/radio.toml ]]; then
  install -o root -g airadio -m 660 "$REPO/config/radio.example.toml" "$ETC/radio.toml"
fi
# korábbi telepítésnél 640 volt; az irányítópultnak írnia kell
chown root:airadio "$ETC/radio.toml" && chmod 660 "$ETC/radio.toml"

if [[ ! -f $ETC/airadio.env ]]; then
  umask 027
  cat > "$ETC/airadio.env" <<EOF
# Titkok. Csak root és az airadio csoport olvashatja.
ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY:-ide-ird-a-kulcsot}
ICECAST_SOURCE_PASSWORD=$(openssl rand -hex 16)
ICECAST_ADMIN_PASSWORD=$(openssl rand -hex 16)
LIVE_PASSWORD=$(openssl rand -hex 12)
ICECAST_PORT=$ICECAST_PORT
LIVE_PORT=$LIVE_PORT
EOF
  chown root:airadio "$ETC/airadio.env"
  chmod 640 "$ETC/airadio.env"
fi
# shellcheck disable=SC1091
source "$ETC/airadio.env"

HOST_IP="$(hostname -I | awk '{print $1}')"
sed -e "s/@SOURCE_PASSWORD@/$ICECAST_SOURCE_PASSWORD/" \
    -e "s/@ADMIN_PASSWORD@/$ICECAST_ADMIN_PASSWORD/" \
    -e "s/@HOSTNAME@/$HOST_IP/" \
    -e "s/@PORT@/$ICECAST_PORT/" \
    -e "s#@LOGDIR@#$LOGDIR#" \
    "$REPO/deploy/icecast.xml" > "$ETC/icecast.xml"
chown root:airadio "$ETC/icecast.xml"
chmod 640 "$ETC/icecast.xml"

echo "== Tűzfal (Oracle Ubuntu képen az iptables REJECT szabály mindent tilt)"
for port in "$ICECAST_PORT" "$LIVE_PORT"; do
  iptables -C INPUT -p tcp --dport "$port" -m state --state NEW -j ACCEPT 2>/dev/null \
    || iptables -I INPUT -p tcp --dport "$port" -m state --state NEW -j ACCEPT
done
netfilter-persistent save >/dev/null

echo "== Hangok és állomások"
sudo -u airadio env AIRADIO_CONFIG="$ETC/radio.toml" "$APP/venv/bin/airadio" voices download
sudo -u airadio env AIRADIO_CONFIG="$ETC/radio.toml" "$APP/venv/bin/airadio" stations render || \
  echo "Figyelem: az állomáslista nem készült el, később: airadio stations render"

echo "== Szolgáltatások"
install -m 644 "$REPO"/deploy/systemd/*.service "$REPO"/deploy/systemd/*.timer /etc/systemd/system/
install -m 644 "$REPO/deploy/logrotate" /etc/logrotate.d/airadio
systemctl daemon-reload
systemctl enable --now airadio-icecast.service
systemctl enable airadio-radio.service
systemctl restart airadio-radio.service
systemctl enable --now airadio-news.timer
systemctl enable airadio-dashboard.service
systemctl restart airadio-dashboard.service

cat <<EOF

Kész. A rádió címe: http://$HOST_IP:$ICECAST_PORT/radio.mp3
(nyilvános IP-vel, ha az Oracle Security Listben megnyitottad a $ICECAST_PORT portot)

Még hátravan:
  1. Claude API-kulcs: sudo nano $ETC/airadio.env  (ANTHROPIC_API_KEY=...)
  2. Próba: sudo -u airadio AIRADIO_CONFIG=$ETC/radio.toml airadio news --dry-run
  3. Élő adás (BUTT): szerver $HOST_IP, port $LIVE_PORT, mount /live, felhasználó source,
     jelszó: LIVE_PASSWORD a $ETC/airadio.env fájlból.
  4. Irányítópult: a saját gépeden  ssh -L 8020:127.0.0.1:8080 ${SUDO_USER:-ubuntu}@$HOST_IP
     majd böngészőben http://localhost:8020
EOF
