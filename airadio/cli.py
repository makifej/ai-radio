"""Parancssor: airadio news | stations | feeds | voices | status."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime

from . import config as config_mod
from . import feeds, news, stations, tts

log = logging.getLogger("airadio")


def cmd_news(cfg: config_mod.Config, args) -> int:
    import anthropic

    now = datetime.now()
    slot = now.replace(minute=0, second=0, microsecond=0) if args.current_hour else news.next_slot(now)
    try:
        path = news.produce(
            cfg,
            client=anthropic.Anthropic(),
            speaker=None if args.dry_run else tts.PiperSpeaker(cfg.voices_dir),
            slot=slot,
            now=now,
            dry_run=args.dry_run,
        )
    except Exception as e:
        log.error("A %s órás hírblokk nem készült el: %s", slot.hour, e)
        return 1
    if path and args.current_hour:
        log.info("Ha még az óra első öt percében vagyunk, a rádió most lejátssza.")
    return 0


def cmd_stations_search(cfg, args) -> int:
    found = stations.search(
        config_mod.StationSearch(tag=args.tag or "", countrycode=args.country or ""), limit=args.limit
    )
    for f in found:
        print(f"{f.name}\n  {f.url}\n  {f.codec} {f.bitrate} kbps, {f.countrycode}, {f.tags}")
    return 0


def cmd_stations_render(cfg, args) -> int:
    path = stations.write(cfg, stations.resolve(cfg))
    print(f"Kész: {path}. Életbe lép: sudo systemctl restart airadio-radio")
    return 0


def cmd_feeds_check(cfg, args) -> int:
    ok = True
    for feed in cfg.news.feeds:
        try:
            items = feeds.fetch((feed,), cfg.news.max_age_hours)
            print(f"{feed.name}: {len(items)} friss hír ({cfg.news.max_age_hours:g} órán belül)")
        except Exception as e:
            ok = False
            print(f"{feed.name}: HIBA {e}")
    return 0 if ok else 1


def cmd_voices_download(cfg, args) -> int:
    tts.download_voices(sorted({cfg.host_voice, cfg.news_voice}), cfg.voices_dir)
    return 0


def cmd_status(cfg, args) -> int:
    try:
        status = json.loads(cfg.status_file.read_text())
    except FileNotFoundError:
        print(f"Nincs állapotfájl ({cfg.status_file}); fut a rádió?")
        return 1
    print(json.dumps(status, ensure_ascii=False, indent=2))
    upcoming = news.bulletin_path(cfg, news.next_slot(datetime.now()))
    print(f"Következő hírblokk: {upcoming.name} {'kész' if upcoming.exists() else 'még nincs kész'}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="airadio", description="AI Rádió segédprogram")
    p.add_argument("--config", help=f"beállítófájl (alapértelmezés: $AIRADIO_CONFIG vagy {config_mod.DEFAULT_PATH})")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)

    n = sub.add_parser("news", help="a következő óra hírblokkjának elkészítése")
    n.add_argument("--current-hour", action="store_true", help="a folyó órára készít (teszteléshez)")
    n.add_argument("--dry-run", action="store_true", help="csak kiírja a szöveget, hang nem készül")
    n.set_defaults(func=cmd_news)

    s = sub.add_parser("stations", help="állomások").add_subparsers(dest="action", required=True)
    ss = s.add_parser("search", help="keresés a Radio Browser katalógusban")
    ss.add_argument("--tag")
    ss.add_argument("--country", help="kétbetűs országkód, pl. HU")
    ss.add_argument("--limit", type=int, default=10)
    ss.set_defaults(func=cmd_stations_search)
    sr = s.add_parser("render", help="stations.json előállítása a beállításokból")
    sr.set_defaults(func=cmd_stations_render)

    f = sub.add_parser("feeds", help="hírforrások").add_subparsers(dest="action", required=True)
    f.add_parser("check", help="a hírforrások kipróbálása").set_defaults(func=cmd_feeds_check)

    v = sub.add_parser("voices", help="Piper hangok").add_subparsers(dest="action", required=True)
    v.add_parser("download", help="a beállított hangok letöltése").set_defaults(func=cmd_voices_download)

    sub.add_parser("status", help="mi szól most").set_defaults(func=cmd_status)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    try:
        cfg = config_mod.load(args.config)
    except config_mod.ConfigError as e:
        log.error("%s", e)
        return 2
    return args.func(cfg, args)


if __name__ == "__main__":
    sys.exit(main())
