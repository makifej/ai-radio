# Helyi futtatás (Mac, Windows, Linux) Dockerrel: docker compose up -d
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    TZ=Europe/Budapest \
    AIRADIO_CONFIG=/var/lib/airadio/config/radio.toml \
    AIRADIO_DATA_DIR=/var/lib/airadio

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      liquidsoap icecast2 ffmpeg python3-venv openssl tzdata ca-certificates media-types \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --system --home-dir /var/lib/airadio --shell /usr/sbin/nologin airadio

WORKDIR /opt/airadio

# Előbb csak a függőségek, egy üres csomaggal: ez a réteg csak a pyproject.toml
# változásakor épül újra, így egy kódfrissítés után nem tölt le mindent újra.
COPY pyproject.toml ./
RUN python3 -m venv venv \
 && mkdir airadio && touch airadio/__init__.py \
 && venv/bin/pip install --no-cache-dir . \
 && rm -rf airadio build *.egg-info
COPY airadio ./airadio
RUN venv/bin/pip install --no-cache-dir --no-deps --force-reinstall . \
 && rm -rf build *.egg-info \
 && ln -sf /opt/airadio/venv/bin/airadio /usr/local/bin/airadio

COPY radio ./radio
COPY config ./config
COPY deploy ./deploy
# Hallgatói lejátszó és a spektrum-megjelenítő az Icecast gyökércímén
RUN cp radio/player.html airadio/web/viz.js /usr/share/icecast2/web/

VOLUME /var/lib/airadio
EXPOSE 8000 8005 8080
ENTRYPOINT ["/opt/airadio/deploy/docker/entrypoint.sh"]
