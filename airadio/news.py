"""Egy hírblokk teljes előállítása: hírek → Claude-szöveg → Piper → ellenőrzött WAV.

A kész fájl neve hirek-ÉÉÉÉHHNN-ÓÓ.wav a news mappában; a radio.liq az adott
óra elején keresi. Ha bármelyik lépés elakad, nem készül fájl, és abban az
órában csak zene szól.
"""

from __future__ import annotations

import json
import logging
import shutil
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from . import audio, feeds
from .config import Config
from .writer import Bulletin, write_bulletin

log = logging.getLogger(__name__)

RECENT_HOURS = 3
KEEP_FILES_HOURS = 24
STATE_KEEP_HOURS = 24


def next_slot(now: datetime) -> datetime:
    return now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)


def bulletin_path(cfg: Config, slot: datetime) -> Path:
    return cfg.news_dir / f"hirek-{slot:%Y%m%d-%H}.wav"


class AiredLog:
    """Az elhangzott hírek címei, hogy a következő blokk ne ismételje őket."""

    def __init__(self, path: Path):
        self.path = path

    def _load(self) -> list[dict]:
        try:
            return json.loads(self.path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def recent_titles(self, now: datetime, hours: float = RECENT_HOURS) -> list[str]:
        cutoff = (now - timedelta(hours=hours)).isoformat()
        return [e["title"] for e in self._load() if e["aired"] >= cutoff]

    def record(self, titles: list[str], aired: datetime) -> None:
        cutoff = (aired - timedelta(hours=STATE_KEEP_HOURS)).isoformat()
        entries = [e for e in self._load() if e["aired"] >= cutoff]
        entries += [{"title": t, "aired": aired.isoformat()} for t in titles]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(entries, ensure_ascii=False, indent=1))
        tmp.replace(self.path)


def select_items(bulletin: Bulletin, items: list[feeds.NewsItem], max_items: int) -> list[tuple[feeds.NewsItem, str]]:
    """Csak létező forrástételre hivatkozó, nem üres hírek, egy forrástétel egyszer."""
    chosen, used = [], set()
    for entry in bulletin.items:
        text = entry.text.strip()
        if not 1 <= entry.source_id <= len(items) or entry.source_id in used or not text:
            log.warning("Elvetett hírtétel (source_id=%s)", entry.source_id)
            continue
        used.add(entry.source_id)
        chosen.append((items[entry.source_id - 1], text))
    return chosen[:max_items]


def _jingle(cfg: Config, name: str) -> list[Path]:
    path = cfg.jingles_dir / name
    return [path] if path.exists() else []


def cleanup(cfg: Config, now: datetime) -> None:
    cutoff = (now - timedelta(hours=KEEP_FILES_HOURS)).timestamp()
    for old in cfg.news_dir.glob("hirek-*.wav"):
        if old.stat().st_mtime < cutoff:
            old.unlink(missing_ok=True)


def produce(
    cfg: Config,
    client,
    speaker,
    slot: datetime,
    now: datetime | None = None,
    dry_run: bool = False,
) -> Path | None:
    now = now or datetime.now()
    if slot.hour not in cfg.news.hours:
        log.info("%s órakor nincs hírblokk (news.hours)", slot.hour)
        return None

    daypart = cfg.daypart_for(slot.hour)
    host = daypart.host if daypart else ""

    items = feeds.fetch(cfg.news.feeds, cfg.news.max_age_hours)
    log.info("%d friss hírtétel a forrásokból", len(items))
    if len(items) < cfg.news.min_items:
        log.warning("Kevés friss hír (%d < %d), most nincs hírblokk", len(items), cfg.news.min_items)
        return None
    items = items[:25]  # bőven elég a válogatáshoz, és kordában tartja a bemeneti tokeneket

    aired = AiredLog(cfg.state_dir / "aired.json")
    bulletin = write_bulletin(
        client,
        model=cfg.model,
        effort=cfg.effort,
        fallback=cfg.fallback,
        items=items,
        slot=slot,
        host=host,
        recent_titles=aired.recent_titles(now),
        max_items=cfg.news.max_items,
    )
    chosen = select_items(bulletin, items, cfg.news.max_items)
    if len(chosen) < cfg.news.min_items:
        log.warning("A szerkesztés után kevés hír maradt (%d), most nincs hírblokk", len(chosen))
        return None

    if dry_run:
        print(bulletin.intro.strip())
        for item, text in chosen:
            print(f"\n[{item.source}] {text}")
        print("\n" + bulletin.outro.strip())
        return None

    cfg.news_dir.mkdir(parents=True, exist_ok=True)
    target = bulletin_path(cfg, slot)
    with tempfile.TemporaryDirectory(prefix="airadio-") as tmp:
        tmp = Path(tmp)
        intro = speaker.synthesize(bulletin.intro.strip(), cfg.host_voice, tmp / "intro.wav")
        outro = speaker.synthesize(bulletin.outro.strip(), cfg.host_voice, tmp / "outro.wav")
        voiced = [
            (item, speaker.synthesize(text, cfg.news_voice, tmp / f"hir-{i}.wav"))
            for i, (item, text) in enumerate(chosen)
        ]

        # Ha túl hosszú, a legkevésbé fontos (utolsó) hírek maradnak ki.
        rendered = tmp / "blokk.wav"
        while True:
            segments = (
                _jingle(cfg, "hirek_eleje.wav")
                + [intro]
                + [wav for _, wav in voiced]
                + [outro]
                + _jingle(cfg, "hirek_vege.wav")
            )
            audio.render(segments, rendered)
            if audio.duration(rendered) <= cfg.news.max_seconds or len(voiced) <= cfg.news.min_items:
                break
            dropped, _ = voiced.pop()
            log.info("Túl hosszú, kimarad: %s", dropped.title)

        result = audio.check(rendered, cfg.news.max_seconds)
        # Átmásolás a news mappába, majd atomi átnevezés: a Liquidsoap sosem lát félkész fájlt.
        partial = target.with_suffix(".part")
        shutil.copyfile(rendered, partial)
        partial.replace(target)

    aired.record([item.title for item, _ in voiced], slot)
    cleanup(cfg, now)
    log.info("Kész: %s (%.0f mp, %d hír)", target, result.seconds, len(voiced))
    return target
