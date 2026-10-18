#!/bin/bash
# Automatikus frissítés a Macen. A launchd kétpercenként futtatja (install-autoupdate.sh).
# Ha a GitHubon új változat van, letölti a mappába, és újraépíti a konténert.
# A data mappa és az .env nincs a repóban, ezért a frissítés nem nyúl hozzájuk.

# Az egész szkript egy függvény: a bash így előre beolvassa, és nem zavarja meg,
# hogy futás közben a frissítés felülírja ezt a fájlt.
main() {
  set -uo pipefail
  export PATH="$HOME/.docker/bin:/usr/local/bin:/opt/homebrew/bin:/Applications/Docker.app/Contents/Resources/bin:/usr/bin:/bin:/usr/sbin:/sbin"
  local dir repo branch state sha
  dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
  repo="${AIRADIO_REPO:-makifej/ai-radio}"
  branch="${AIRADIO_BRANCH:-main}"
  state="$dir/data/.verzio"
  lock="$dir/data/.frissites.lock"

  mkdir -p "$dir/data/log"
  # egyszerre csak egy fusson; a fél óránál régebbi zár egy félbemaradt futásé
  find "$lock" -maxdepth 0 -mmin +30 -exec rm -rf {} \; 2>/dev/null
  mkdir "$lock" 2>/dev/null || return 0
  tmp="$(mktemp -d)"
  trap 'rm -rf "$lock" "$tmp"' EXIT

  sha="$(curl -fsS --max-time 20 -H "Accept: application/vnd.github.sha" \
    "https://api.github.com/repos/$repo/commits/$branch")" || return 0
  [[ $sha =~ ^[0-9a-f]{40}$ ]] || return 0
  [[ $sha == "$(cat "$state" 2>/dev/null)" ]] && return 0
  docker info >/dev/null 2>&1 || { log "a Docker nem fut, később újra"; return 0; }

  log "új változat: ${sha:0:7}"
  curl -fsSL --max-time 300 -o "$tmp/src.tgz" "https://codeload.github.com/$repo/tar.gz/$sha" \
    || { log "a letöltés nem sikerült"; return 0; }
  # külön fájlba, mert a grep -q korai kilépése pipefail mellett hibának látszana
  tar tzf "$tmp/src.tgz" > "$tmp/list" 2>/dev/null && grep -q '^[^/]*/docker-compose.yml$' "$tmp/list" \
    || { log "hibás csomag"; return 0; }
  tar xzf "$tmp/src.tgz" -C "$dir" --strip-components=1

  if (cd "$dir" && docker compose up -d --build); then
    echo "$sha" > "$state"
    log "kész, fut a ${sha:0:7} változat"
  else
    log "az újraépítés nem sikerült, a következő körben újra"
  fi
}
log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*"; }
main "$@"
exit
