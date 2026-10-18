# Helyi futtatás (Mac, Windows, Linux) Dockerrel: docker compose up -d
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    TZ=Europe/Budapest \
    AIRADIO_CONFIG=/var/lib/airadio/config/radio.toml \
    AIRADIO_DATA_DIR=/var/lib/airadio

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      liquidsoap icecast2 ffmpeg python3-venv openssl tzdata ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --system --home-dir /var/lib/airadio --shell /usr/sbin/nologin airadio

WORKDIR /opt/airadio

COPY pyproject.toml ./
COPY airadio ./airadio
RUN python3 -m venv venv \
 && venv/bin/pip install --no-cache-dir . \
 && ln -s /opt/airadio/venv/bin/airadio /usr/local/bin/airadio

COPY radio ./radio
COPY config ./config
COPY deploy ./deploy
RUN cp radio/player.html /usr/share/icecast2/web/player.html

VOLUME /var/lib/airadio
EXPOSE 8000 8005 8080
ENTRYPOINT ["/opt/airadio/deploy/docker/entrypoint.sh"]
