#!/usr/bin/env bash
# A Docker-konténer indítója: első induláskor beállítást és jelszavakat készít
# a data mappába, majd elindítja az Icecastot, a Liquidsoapot és az óránkénti
# hírszerkesztést. Ha bármelyik leáll, a konténer kilép, és a Docker újraindítja.
set -euo pipefail

DATA=/var/lib/airadio
CONF=$DATA/config
APP=/opt/airadio

# Rootként csak a mappákat készítjük elő, minden más az airadio felhasználóval fut
# (a Liquidsoap rootként el sem indul).
if [[ $(id -u) -eq 0 ]]; then
  mkdir -p "$DATA"/{news,backup,voices,state,jingles,log} "$CONF"
  chown -R airadio:airadio "$DATA"
  exec runuser -u airadio -- "$0" "$@"
fi

if [[ ! -f $CONF/radio.toml ]]; then
  cp "$APP/config/radio.example.toml" "$CONF/radio.toml"
  echo "Elkészült a beállítófájl: data/config/radio.toml"
fi

if [[ ! -f $CONF/secrets.env ]]; then
  (
    umask 077
    cat > "$CONF/secrets.env" <<EOF
ICECAST_SOURCE_PASSWORD=$(openssl rand -hex 16)
ICECAST_ADMIN_PASSWORD=$(openssl rand -hex 16)
LIVE_PASSWORD=$(openssl rand -hex 12)
EOF
  )
  echo "Jelszavak generálva: data/config/secrets.env"
fi
set -a
# shellcheck disable=SC1091
source "$CONF/secrets.env"
set +a
export AIRADIO_CONFIG=$CONF/radio.toml AIRADIO_DATA_DIR=$DATA
export AIRADIO_LOG_FILE=$DATA/log/liquidsoap.log

sed -e "s/@SOURCE_PASSWORD@/$ICECAST_SOURCE_PASSWORD/" \
    -e "s/@ADMIN_PASSWORD@/$ICECAST_ADMIN_PASSWORD/" \
    -e "s/@HOSTNAME@/localhost/" \
    -e "s/@PORT@/8000/" \
    -e "s#@LOGDIR@#$DATA/log#" \
    "$APP/deploy/icecast.xml" > "$CONF/icecast.xml"

airadio voices download || echo "Figyelem: a Piper hangok letöltése nem sikerült, hírblokk addig nem készül."
if [[ ! -f $DATA/stations.json ]]; then
  airadio stations render || echo "Figyelem: az állomáslista nem készült el, a tartalék zene szól."
fi

news_loop() {
  while true; do
    # alvás a következő óra 50. percéig (a magyar időzóna egész órával tér el, így ez helyi :50 is)
    local now next
    now=$(date +%s)
    next=$(( now / 3600 * 3600 + 50 * 60 ))
    (( next > now )) || next=$(( next + 3600 ))
    sleep $(( next - now ))
    if [[ -n ${ANTHROPIC_API_KEY:-} ]]; then
      airadio news || true
    else
      echo "Nincs ANTHROPIC_API_KEY, a hírblokk kimarad."
    fi
  done
}

# Az irányítópult az állomáslista alkalmazásakor leállítja a Liquidsoapot; ez a
# hurok azonnal újraindítja.
radio_loop() {
  while true; do
    [[ -f $AIRADIO_LOG_FILE ]] && mv -f "$AIRADIO_LOG_FILE" "$AIRADIO_LOG_FILE.1"
    liquidsoap "$APP/radio/radio.liq" || true
    echo "A Liquidsoap leállt, újraindítás."
    sleep 1
  done
}

icecast2 -c "$CONF/icecast.xml" &
sleep 1
radio_loop &
news_loop &
# 8080: irányítópult (csak a saját gépről), 8081: hallgatói oldal stílusválasztással
airadio dashboard --host 0.0.0.0 --port 8080 --public-port 8081 &

echo "AI Rádió fut: http://localhost:${AIRADIO_PORT:-8010}"
echo "Irányítópult: http://localhost:${AIRADIO_ADMIN_PORT:-8020}"
wait -n
echo "Egy folyamat leállt, a konténer újraindul."
exit 1
