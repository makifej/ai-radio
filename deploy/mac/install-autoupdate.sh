#!/bin/bash
# Egyszeri beállítás a Macen, utána a rádió magától frissül a GitHubról.
#   curl -fsSL https://raw.githubusercontent.com/makifej/ai-radio/main/deploy/mac/install-autoupdate.sh | bash
# A rádió mappáját a ~/ai-radio helyre teszi, mert a háttérben futó frissítő a macOS
# adatvédelme miatt nem érheti el a Letöltések, az Asztal és a Dokumentumok mappát.
# Az .env (API-kulcs) és a data mappa (beállítások, hangok, hírek) megmarad.
# Egy függvényben, hogy a bash előbb a teljes szkriptet beolvassa (curl | bash).
main() {
  set -euo pipefail
  export PATH="$HOME/.docker/bin:/usr/local/bin:/opt/homebrew/bin:/Applications/Docker.app/Contents/Resources/bin:$PATH"
  REPO="${AIRADIO_REPO:-makifej/ai-radio}"
  BRANCH="${AIRADIO_BRANCH:-main}"
  NEW="$HOME/ai-radio"
  LABEL=hu.airadio.autoupdate

  if ! docker info >/dev/null 2>&1; then
    echo "A Docker indítása..."
    open -a Docker
    until docker info >/dev/null 2>&1; do sleep 2; done
  fi

  # Hol van most a rádió? Először a futó konténerből, különben az .env fájl alapján.
  OLD="$(docker inspect airadio --format '{{ index .Config.Labels "com.docker.compose.project.working_dir" }}' 2>/dev/null || true)"
  if [[ -z $OLD || ! -f $OLD/.env ]]; then
    f="$(find "$HOME" -maxdepth 3 -name .env -path '*ai-radio*' 2>/dev/null | head -1 || true)"
    OLD="${f%/.env}"
  fi
  if [[ -z $OLD || ! -f $OLD/.env ]]; then
    echo "Nem találom a rádió mappáját (azt, amelyikben az .env van az API-kulccsal)." >&2
    exit 1
  fi

  if [[ $OLD != "$NEW" ]]; then
    if [[ -e $NEW ]]; then
      echo "A $NEW mappa már létezik, a rádió viszont itt van: $OLD" >&2
      echo "Nevezd át vagy töröld a $NEW mappát, és futtasd újra." >&2
      exit 1
    fi
    echo "A rádió átköltözik ide: $NEW"
    (cd "$OLD" && docker compose down </dev/null)
    mv "$OLD" "$NEW"
  fi

  echo "A legfrissebb változat letöltése..."
  tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' EXIT
  curl -fsSL -o "$tmp/src.tgz" "https://codeload.github.com/$REPO/tar.gz/$BRANCH"
  tar xzf "$tmp/src.tgz" -C "$NEW" --strip-components=1
  rm -f "$NEW/data/.verzio"
  echo "Újraépítés és indítás (első alkalommal pár perc)..."
  bash "$NEW/deploy/mac/autoupdate.sh" </dev/null

  plist="$HOME/Library/LaunchAgents/$LABEL.plist"
  mkdir -p "$HOME/Library/LaunchAgents" "$NEW/data/log"
  cat > "$plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>$LABEL</string>
    <key>ProgramArguments</key>
    <array><string>/bin/bash</string><string>$NEW/deploy/mac/autoupdate.sh</string></array>
    <key>StartInterval</key><integer>120</integer>
    <key>RunAtLoad</key><true/>
    <key>StandardOutPath</key><string>$NEW/data/log/frissites.log</string>
    <key>StandardErrorPath</key><string>$NEW/data/log/frissites.log</string>
</dict>
</plist>
PLIST
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$plist"

  echo
  if [[ -f $NEW/data/.verzio ]]; then
    echo "Kész. A rádió mappája: $NEW"
  else
    echo "Az indítás most nem sikerült (a hiba fent látszik); a frissítő kétpercenként újrapróbálja."
  fi
  echo "Hallgatás: http://localhost:8010   Irányítópult: http://localhost:8020"
  echo "Az új változatok kétpercenként maguktól érkeznek; napló: $NEW/data/log/frissites.log"
}
main "$@"
