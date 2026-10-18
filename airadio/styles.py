"""Zenei stílusok és a hozzájuk tartozó online rádiók.

Egy stílus néhány fix állomásból és Radio Browser címkékből áll. A címkék
találatait népszerűség szerint rendezve gyorsítótárazzuk (data/state/styles.json),
így a stílus kiválasztásakor nem kell minden alkalommal keresni. Hogy egy
állomás valóban szól-e, azt a továbbító (relay.py) dönti el lejátszás közben:
ha nem, a lista következő eleme jön.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from . import stations as stations_mod
from .config import Station, StationSearch

log = logging.getLogger(__name__)

CACHE_SECONDS = 24 * 3600
MAX_STATIONS = 12
PER_TAG = 25
MIN_BITRATE = 64


@dataclass(frozen=True)
class Style:
    id: str
    name: str
    tags: tuple[str, ...]
    stations: tuple[Station, ...] = ()
    countrycode: str = ""
    featured: bool = False


def _soma(name: str, channel: str) -> Station:
    return Station(name=f"SomaFM {name}", url=f"https://ice.somafm.com/{channel}")


# A SomaFM közvetlen címeinek mintája (https://ice.somafm.com/<csatorna>) a SomaFM
# saját leírásából való; ha egy cím mégsem szól, a továbbító átlép rajta.
DEFAULT_STYLES: tuple[Style, ...] = (
    Style("rock", "Rock", ("rock", "classic rock", "hard rock"), featured=True),
    Style("metal", "Metal", ("metal", "heavy metal"), (_soma("Metal Detector", "metal"),), featured=True),
    Style("numetal", "Nu metal", ("nu metal", "nu-metal", "numetal", "alternative metal"), featured=True),
    Style("chill", "Chill", ("chillout", "chill", "downtempo"),
          (_soma("Groove Salad", "groovesalad"), _soma("Lush", "lush")), featured=True),
    Style("dance", "Dance", ("dance", "edm", "eurodance"), featured=True),
    Style("joga", "Jóga", ("yoga", "relaxation", "relax"), featured=True),
    Style("meditacio", "Meditáció", ("meditation", "ambient", "spa"),
          (_soma("Drone Zone", "dronezone"), _soma("Deep Space One", "deepspaceone")), featured=True),
    Style("pop", "Pop", ("pop", "top 40", "hits")),
    Style("magyar", "Magyar", ("",), countrycode="HU"),
    Style("80s", "80-as évek", ("80s",), (_soma("Underground 80s", "u80s"),)),
    Style("90s", "90-es évek", ("90s",)),
    Style("oldies", "Oldies, 70-es évek", ("oldies", "70s")),
    Style("jazz", "Jazz", ("jazz", "smooth jazz")),
    Style("klasszikus", "Klasszikus", ("classical",)),
    Style("lounge", "Lounge", ("lounge",), (_soma("Secret Agent", "secretagent"),)),
    Style("ambient", "Ambient", ("ambient", "space"), (_soma("Deep Space One", "deepspaceone"),)),
    Style("elektronikus", "Elektronikus", ("electronic", "electronica")),
    Style("house", "House", ("house", "deep house")),
    Style("techno", "Techno", ("techno", "minimal techno")),
    Style("trance", "Trance", ("trance", "psytrance", "goa")),
    Style("dnb", "Drum & bass", ("drum and bass", "drum & bass", "dnb", "jungle")),
    Style("hiphop", "Hip-hop", ("hip hop", "hiphop", "rap")),
    Style("rnb", "R&B, soul", ("rnb", "r&b", "soul")),
    Style("funk", "Funk, disco", ("funk", "disco")),
    Style("indie", "Indie, alternatív", ("indie", "alternative")),
    Style("punk", "Punk", ("punk",)),
    Style("blues", "Blues", ("blues",)),
    Style("country", "Country", ("country",)),
    Style("reggae", "Reggae", ("reggae", "dub", "ska")),
    Style("latin", "Latin", ("latin", "salsa", "reggaeton")),
    Style("lofi", "Lo-fi", ("lofi", "lo-fi", "chillhop")),
    Style("folk", "Folk", ("folk", "acoustic")),
    Style("filmzene", "Filmzene", ("soundtrack", "film music", "movie")),
)


def by_id(style_id: str, styles: tuple[Style, ...] = DEFAULT_STYLES) -> Style | None:
    return next((s for s in styles if s.id == style_id), None)


def gather(style: Style, searcher=stations_mod.search) -> list[dict]:
    """A stílus állomásai: előbb a fixek, majd címkénként a legnépszerűbb találatok."""
    found: list[dict] = []
    urls: set[str] = set()
    names: set[str] = set()

    def add(name: str, url: str, **extra) -> None:
        key = name.strip().lower()
        if not url or url in urls or key in names or len(found) >= MAX_STATIONS:
            return
        urls.add(url)
        names.add(key)
        found.append({"name": name.strip() or url, "url": url, **extra})

    for s in style.stations:
        add(s.name, s.url, source="fix")
    errors = []
    for tag in style.tags:
        if len(found) >= MAX_STATIONS:
            break
        try:
            rows = searcher(StationSearch(tag=tag, countrycode=style.countrycode, limit=PER_TAG))
        except ConnectionError as e:
            errors.append(str(e))
            continue
        for r in rows:
            if r.hls or 0 < r.bitrate < MIN_BITRATE:
                continue
            add(r.name, r.url, source="keresés", codec=r.codec, bitrate=r.bitrate,
                country=r.countrycode, homepage=r.homepage, favicon=r.favicon)
    if errors and len(found) == len(style.stations):
        raise ConnectionError(errors[0])
    return found


class Catalog:
    """Stílusonként az állomáslista, napi frissítéssel, fájlban tárolva."""

    def __init__(self, cache_file: Path, styles: tuple[Style, ...] = DEFAULT_STYLES, searcher=stations_mod.search):
        self.cache_file = cache_file
        self.styles = styles
        self._searcher = searcher
        self._lock = threading.Lock()

    def _load(self) -> dict:
        try:
            return json.loads(self.cache_file.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def cached(self, style_id: str) -> list[dict] | None:
        entry = self._load().get(style_id)
        return entry["stations"] if entry else None

    def stations(self, style_id: str, refresh: bool = False) -> list[dict]:
        style = by_id(style_id, self.styles)
        if style is None:
            raise KeyError(style_id)
        with self._lock:
            cache = self._load()
            entry = cache.get(style_id)
            if entry and not refresh and time.time() - entry.get("updated", 0) < CACHE_SECONDS:
                return entry["stations"]
            try:
                found = gather(style, self._searcher)
            except ConnectionError as e:
                if entry:  # inkább a régi lista, mint semmi
                    log.warning("%s: a keresés nem sikerült (%s), a tárolt listát használom", style.name, e)
                    return entry["stations"]
                found = [{"name": s.name, "url": s.url, "source": "fix"} for s in style.stations]
                if not found:
                    raise
            cache[style_id] = {"updated": time.time(), "stations": found}
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.cache_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(cache, ensure_ascii=False))
            tmp.replace(self.cache_file)
            return found
