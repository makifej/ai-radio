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
PER_TAG = 30
MIN_BITRATE = 64
MAX_TAGS = 15     # ennél több címkéjű adó jellemzően mindenre ráhúzza magát
CACHE_VERSION = 2  # ha a válogatás módja változik, a régi gyorsítótár érvénytelen


@dataclass(frozen=True)
class Style:
    id: str
    name: str
    tags: tuple[str, ...]
    stations: tuple[Station, ...] = ()
    countrycode: str = ""
    featured: bool = False
    group: str = ""


def _soma(name: str, channel: str) -> Station:
    return Station(name=f"SomaFM {name}", url=f"https://ice.somafm.com/{channel}")


GROUPS = (
    "Rock", "Metal", "Elektronikus", "Hip-hop, R&B, soul", "Pop", "Magyar", "Jazz, blues",
    "Klasszikus", "Világzene, latin", "Country, folk", "Relax", "Évtizedek", "Egyéb",
)

# A felület első sorában, ebben a sorrendben.
FEATURED = ("rock", "metal", "numetal", "chill", "dance", "joga", "meditacio")

# (azonosító, név, csoport, Radio Browser címkék). A címke a katalógusban részszóra
# is illeszkedik (a "metal" a "death metal"-t is hozza), ezért az alstílusoknál
# a pontos alcímke áll elöl.
_TABLE = (
    # Rock
    ("rock", "Rock", "Rock", ("rock", "classic rock", "hard rock")),
    ("classicrock", "Classic rock", "Rock", ("classic rock",)),
    ("hardrock", "Hard rock", "Rock", ("hard rock",)),
    ("altrock", "Alternatív rock", "Rock", ("alternative rock", "alternative")),
    ("indierock", "Indie rock", "Rock", ("indie rock", "indie")),
    ("punk", "Punk", "Rock", ("punk", "punk rock")),
    ("poppunk", "Pop punk", "Rock", ("pop punk",)),
    ("postpunk", "Post-punk", "Rock", ("post-punk", "post punk")),
    ("grunge", "Grunge", "Rock", ("grunge",)),
    ("progrock", "Progresszív rock", "Rock", ("progressive rock", "prog rock")),
    ("psychrock", "Pszichedelikus rock", "Rock", ("psychedelic rock", "psychedelic")),
    ("garagerock", "Garage rock", "Rock", ("garage rock",)),
    ("stonerrock", "Stoner rock", "Rock", ("stoner rock", "stoner")),
    ("southernrock", "Southern rock", "Rock", ("southern rock",)),
    ("bluesrock", "Blues rock", "Rock", ("blues rock",)),
    ("softrock", "Soft rock", "Rock", ("soft rock",)),
    ("rocknroll", "Rock and roll", "Rock", ("rock'n'roll", "rock and roll", "rock n roll")),
    ("rockabilly", "Rockabilly", "Rock", ("rockabilly",)),
    ("britpop", "Britpop", "Rock", ("britpop",)),
    ("newwave", "New wave", "Rock", ("new wave",)),
    ("gothrock", "Gothic rock", "Rock", ("gothic rock", "goth")),
    ("postrock", "Post-rock", "Rock", ("post-rock", "post rock")),
    ("shoegaze", "Shoegaze", "Rock", ("shoegaze", "dream pop")),
    ("emo", "Emo", "Rock", ("emo",)),
    ("krautrock", "Krautrock", "Rock", ("krautrock",)),
    # Metal
    ("metal", "Metal", "Metal", ("metal", "heavy metal")),
    ("numetal", "Nu metal", "Metal", ("nu metal", "nu-metal", "numetal", "alternative metal")),
    ("heavymetal", "Heavy metal", "Metal", ("heavy metal",)),
    ("thrash", "Thrash metal", "Metal", ("thrash metal", "thrash")),
    ("deathmetal", "Death metal", "Metal", ("death metal",)),
    ("blackmetal", "Black metal", "Metal", ("black metal",)),
    ("doom", "Doom metal", "Metal", ("doom metal", "doom")),
    ("powermetal", "Power metal", "Metal", ("power metal",)),
    ("symphmetal", "Szimfonikus metal", "Metal", ("symphonic metal",)),
    ("gothmetal", "Gothic metal", "Metal", ("gothic metal",)),
    ("progmetal", "Progresszív metal", "Metal", ("progressive metal",)),
    ("metalcore", "Metalcore", "Metal", ("metalcore",)),
    ("deathcore", "Deathcore", "Metal", ("deathcore",)),
    ("posthardcore", "Hardcore, post-hardcore", "Metal", ("post-hardcore", "hardcore punk")),
    ("indmetal", "Industrial metal", "Metal", ("industrial metal",)),
    ("folkmetal", "Folk és viking metal", "Metal", ("folk metal", "viking metal", "pagan metal")),
    ("glammetal", "Glam metal", "Metal", ("glam metal", "hair metal", "glam rock")),
    ("grindcore", "Grindcore", "Metal", ("grindcore",)),
    # Elektronikus
    ("dance", "Dance", "Elektronikus", ("dance", "edm", "eurodance")),
    ("electronic", "Elektronikus", "Elektronikus", ("electronic", "electronica")),
    ("edm", "EDM", "Elektronikus", ("edm",)),
    ("house", "House", "Elektronikus", ("house",)),
    ("deephouse", "Deep house", "Elektronikus", ("deep house",)),
    ("techhouse", "Tech house", "Elektronikus", ("tech house",)),
    ("proghouse", "Progressive house", "Elektronikus", ("progressive house",)),
    ("techno", "Techno", "Elektronikus", ("techno",)),
    ("minimal", "Minimal", "Elektronikus", ("minimal techno", "minimal")),
    ("trance", "Trance", "Elektronikus", ("trance",)),
    ("psytrance", "Psytrance, goa", "Elektronikus", ("psytrance", "goa", "psychedelic trance")),
    ("dnb", "Drum & bass", "Elektronikus", ("drum and bass", "drum & bass", "dnb")),
    ("jungle", "Jungle", "Elektronikus", ("jungle",)),
    ("dubstep", "Dubstep", "Elektronikus", ("dubstep",)),
    ("breakbeat", "Breakbeat", "Elektronikus", ("breakbeat", "breaks")),
    ("ukgarage", "UK garage", "Elektronikus", ("uk garage", "2-step")),
    ("hardstyle", "Hardstyle", "Elektronikus", ("hardstyle",)),
    ("hardcoretechno", "Hardcore, gabber", "Elektronikus", ("gabber", "hardcore techno", "happy hardcore")),
    ("electro", "Electro", "Elektronikus", ("electro",)),
    ("synthwave", "Synthwave", "Elektronikus", ("synthwave", "retrowave", "outrun")),
    ("synthpop", "Synthpop", "Elektronikus", ("synthpop", "synth pop", "electropop")),
    ("darkwave", "Darkwave, EBM", "Elektronikus", ("darkwave", "ebm", "industrial")),
    ("idm", "IDM", "Elektronikus", ("idm",)),
    ("triphop", "Trip-hop", "Elektronikus", ("trip hop", "trip-hop")),
    ("downtempo", "Downtempo", "Elektronikus", ("downtempo",)),
    ("eurodance", "Eurodance", "Elektronikus", ("eurodance",)),
    ("italodisco", "Italo disco", "Elektronikus", ("italo disco",)),
    ("vaporwave", "Vaporwave", "Elektronikus", ("vaporwave",)),
    ("chiptune", "Chiptune", "Elektronikus", ("chiptune", "8bit")),
    # Hip-hop, R&B, soul
    ("hiphop", "Hip-hop", "Hip-hop, R&B, soul", ("hip hop", "hiphop")),
    ("rap", "Rap", "Hip-hop, R&B, soul", ("rap",)),
    ("trap", "Trap", "Hip-hop, R&B, soul", ("trap",)),
    ("oldschoolhiphop", "Old school hip-hop", "Hip-hop, R&B, soul", ("old school hip hop", "old school")),
    ("grime", "Grime, drill", "Hip-hop, R&B, soul", ("grime", "drill")),
    ("rnb", "R&B", "Hip-hop, R&B, soul", ("rnb", "r&b")),
    ("soul", "Soul", "Hip-hop, R&B, soul", ("soul",)),
    ("neosoul", "Neo soul", "Hip-hop, R&B, soul", ("neo soul",)),
    ("funk", "Funk", "Hip-hop, R&B, soul", ("funk",)),
    ("disco", "Disco", "Hip-hop, R&B, soul", ("disco",)),
    ("motown", "Motown", "Hip-hop, R&B, soul", ("motown",)),
    ("gospel", "Gospel", "Hip-hop, R&B, soul", ("gospel",)),
    ("afrobeats", "Afrobeats", "Hip-hop, R&B, soul", ("afrobeats", "afrobeat")),
    # Pop
    ("pop", "Pop", "Pop", ("pop",)),
    ("top40", "Top 40, slágerek", "Pop", ("top 40", "hits", "charts")),
    ("dancepop", "Dance pop", "Pop", ("dance pop",)),
    ("indiepop", "Indie pop", "Pop", ("indie pop",)),
    ("kpop", "K-pop", "Pop", ("k-pop", "kpop")),
    ("jpop", "J-pop", "Pop", ("j-pop", "jpop")),
    ("schlager", "Schlager", "Pop", ("schlager",)),
    ("chanson", "Sanzon", "Pop", ("chanson",)),
    ("italopop", "Olasz pop", "Pop", ("italian pop", "italian")),
    # Magyar
    ("magyar", "Magyar slágerek", "Magyar", ("",)),
    ("magyarpop", "Magyar pop", "Magyar", ("pop",)),
    ("magyarrock", "Magyar rock", "Magyar", ("rock",)),
    ("magyarretro", "Magyar retró", "Magyar", ("oldies", "retro", "80s")),
    ("mulatos", "Mulatós", "Magyar", ("mulatós", "mulatos", "lakodalmas")),
    ("nepzene", "Magyar népzene", "Magyar", ("népzene", "folk")),
    # Jazz, blues
    ("jazz", "Jazz", "Jazz, blues", ("jazz",)),
    ("smoothjazz", "Smooth jazz", "Jazz, blues", ("smooth jazz",)),
    ("swing", "Swing, big band", "Jazz, blues", ("swing", "big band")),
    ("bebop", "Bebop", "Jazz, blues", ("bebop",)),
    ("fusion", "Jazz fusion", "Jazz, blues", ("jazz fusion", "fusion")),
    ("acidjazz", "Acid jazz", "Jazz, blues", ("acid jazz",)),
    ("nujazz", "Nu jazz", "Jazz, blues", ("nu jazz", "nu-jazz")),
    ("latinjazz", "Latin jazz", "Jazz, blues", ("latin jazz",)),
    ("bossanova", "Bossa nova", "Jazz, blues", ("bossa nova",)),
    ("blues", "Blues", "Jazz, blues", ("blues",)),
    # Klasszikus
    ("klasszikus", "Klasszikus", "Klasszikus", ("classical",)),
    ("barokk", "Barokk", "Klasszikus", ("baroque",)),
    ("opera", "Opera", "Klasszikus", ("opera",)),
    ("zongora", "Zongora", "Klasszikus", ("piano",)),
    ("korus", "Kórusmű", "Klasszikus", ("choral", "choir")),
    ("kortars", "Kortárs klasszikus", "Klasszikus", ("contemporary classical", "modern classical", "neoclassical")),
    ("filmzene", "Filmzene", "Klasszikus", ("soundtrack", "film music", "movie")),
    ("jatekzene", "Játékzene", "Klasszikus", ("video game music", "game music", "vgm")),
    ("musical", "Musical", "Klasszikus", ("musical", "broadway")),
    # Világzene, latin
    ("latin", "Latin", "Világzene, latin", ("latin",)),
    ("salsa", "Salsa", "Világzene, latin", ("salsa",)),
    ("reggaeton", "Reggaeton", "Világzene, latin", ("reggaeton",)),
    ("bachata", "Bachata", "Világzene, latin", ("bachata",)),
    ("cumbia", "Cumbia", "Világzene, latin", ("cumbia",)),
    ("tango", "Tangó", "Világzene, latin", ("tango",)),
    ("flamenco", "Flamenco", "Világzene, latin", ("flamenco",)),
    ("brazil", "Brazil, samba", "Világzene, latin", ("samba", "mpb", "brazilian")),
    ("reggae", "Reggae", "Világzene, latin", ("reggae",)),
    ("dub", "Dub", "Világzene, latin", ("dub",)),
    ("ska", "Ska", "Világzene, latin", ("ska",)),
    ("dancehall", "Dancehall", "Világzene, latin", ("dancehall",)),
    ("african", "Afrikai zene", "Világzene, latin", ("african", "afro")),
    ("arab", "Arab zene", "Világzene, latin", ("arabic",)),
    ("turkish", "Török zene", "Világzene, latin", ("turkish",)),
    ("bollywood", "Indiai, Bollywood", "Világzene, latin", ("bollywood", "indian")),
    ("celtic", "Kelta", "Világzene, latin", ("celtic",)),
    ("balkan", "Balkán", "Világzene, latin", ("balkan",)),
    ("greek", "Görög", "Világzene, latin", ("greek",)),
    ("klezmer", "Klezmer", "Világzene, latin", ("klezmer",)),
    ("worldmusic", "Világzene", "Világzene, latin", ("world music", "world")),
    # Country, folk
    ("country", "Country", "Country, folk", ("country",)),
    ("bluegrass", "Bluegrass", "Country, folk", ("bluegrass",)),
    ("americana", "Americana", "Country, folk", ("americana",)),
    ("folk", "Folk", "Country, folk", ("folk",)),
    ("songwriter", "Singer-songwriter", "Country, folk", ("singer-songwriter", "singer songwriter")),
    ("acoustic", "Akusztikus", "Country, folk", ("acoustic",)),
    # Relax
    ("chill", "Chill", "Relax", ("chillout", "chill", "downtempo")),
    ("joga", "Jóga", "Relax", ("yoga", "relaxation", "relax")),
    ("meditacio", "Meditáció", "Relax", ("meditation", "ambient", "spa")),
    ("ambient", "Ambient", "Relax", ("ambient", "space")),
    ("lounge", "Lounge", "Relax", ("lounge",)),
    ("lofi", "Lo-fi", "Relax", ("lofi", "lo-fi", "chillhop")),
    ("newage", "New age", "Relax", ("new age",)),
    ("spa", "Spa, wellness", "Relax", ("spa", "wellness")),
    ("alvas", "Alváshoz", "Relax", ("sleep", "sleeping")),
    ("termeszet", "Természethangok", "Relax", ("nature sounds", "nature")),
    ("fokusz", "Tanuláshoz, fókusz", "Relax", ("study", "focus", "concentration")),
    ("easylistening", "Easy listening", "Relax", ("easy listening",)),
    ("instrumental", "Instrumentális", "Relax", ("instrumental",)),
    # Évtizedek
    ("50s", "50-es évek", "Évtizedek", ("50s",)),
    ("60s", "60-as évek", "Évtizedek", ("60s",)),
    ("70s", "70-es évek", "Évtizedek", ("70s",)),
    ("80s", "80-as évek", "Évtizedek", ("80s",)),
    ("90s", "90-es évek", "Évtizedek", ("90s",)),
    ("2000s", "2000-es évek", "Évtizedek", ("2000s", "00s")),
    ("2010s", "2010-es évek", "Évtizedek", ("2010s",)),
    ("oldies", "Oldies", "Évtizedek", ("oldies",)),
    # Egyéb
    ("christian", "Keresztény zene", "Egyéb", ("christian",)),
    ("kids", "Gyerekzene", "Egyéb", ("kids", "children")),
    ("christmas", "Karácsonyi", "Egyéb", ("christmas",)),
    ("experimental", "Kísérleti", "Egyéb", ("experimental", "avant-garde")),
)

# Fix állomások. A SomaFM közvetlen címeinek mintája (https://ice.somafm.com/<csatorna>)
# a SomaFM saját leírásából való; ha egy cím mégsem szól, a továbbító átlép rajta.
_FIXED = {
    "metal": (_soma("Metal Detector", "metal"),),
    "chill": (_soma("Groove Salad", "groovesalad"), _soma("Lush", "lush")),
    "meditacio": (_soma("Drone Zone", "dronezone"), _soma("Deep Space One", "deepspaceone")),
    "ambient": (_soma("Deep Space One", "deepspaceone"), _soma("Drone Zone", "dronezone")),
    "lounge": (_soma("Secret Agent", "secretagent"),),
    "80s": (_soma("Underground 80s", "u80s"),),
}
_COUNTRY = {"magyar": "HU", "magyarpop": "HU", "magyarrock": "HU", "magyarretro": "HU", "nepzene": "HU"}


def _build() -> tuple[Style, ...]:
    rows = sorted(_TABLE, key=lambda r: FEATURED.index(r[0]) if r[0] in FEATURED else len(FEATURED))
    return tuple(
        Style(id=i, name=name, tags=tags, stations=_FIXED.get(i, ()), countrycode=_COUNTRY.get(i, ""),
              featured=i in FEATURED, group=group)
        for i, name, group, tags in rows
    )


DEFAULT_STYLES: tuple[Style, ...] = _build()


def by_id(style_id: str, styles: tuple[Style, ...] = DEFAULT_STYLES) -> Style | None:
    return next((s for s in styles if s.id == style_id), None)


_SEPARATORS = (" - ", " – ", " | ", " ||", " @ ", " :: ", ": ", " (", " [", " / ", ", ")


def short_name(name: str, limit: int = 40) -> str:
    """Rövid, olvasható név: sok adó a nevébe zsúfolja a reklámszöveget."""
    name = " ".join(name.split()).strip(" #*~-|•:")
    if len(name) <= limit:
        return name
    cuts = [i for sep in _SEPARATORS if (i := name.find(sep, 6)) > 0]
    if cuts and min(cuts) <= limit:
        return name[: min(cuts)].strip(" -–|@:")
    return name[: limit - 1].rstrip() + "…"


def _tag_list(tags: str) -> list[str]:
    return [t.strip().lower() for t in tags.split(",") if t.strip()]


def relevance(tags: str, wanted: tuple[str, ...]) -> float | None:
    """Mennyire illik az adó a stílushoz (kisebb a jobb); None, ha nem illik vagy címkehalmozó.

    A Radio Browser részszóra keres, és sok adó minden népszerű címkét magára aggat.
    Előnyben van, akinél a stílus címkéje pontosan és a lista elején szerepel.
    """
    tags_ = _tag_list(tags)
    if len(tags_) > MAX_TAGS:
        return None
    wanted = tuple(w for w in wanted if w)
    if not wanted:
        return 0.0  # csak ország szerinti stílus (pl. Magyar slágerek)
    for i, t in enumerate(tags_):
        if t in wanted:
            return i + len(tags_) / 10
    for i, t in enumerate(tags_):
        if any(w in t for w in wanted):
            return 3 + i + len(tags_) / 10
    return None


def gather(style: Style, searcher=stations_mod.search) -> list[dict]:
    """A stílus állomásai: előbb a fixek, majd a címkékre legjobban illő, népszerű adók."""
    found: list[dict] = []
    urls: set[str] = set()
    names: set[str] = set()

    def add(name: str, url: str, **extra) -> None:
        short = short_name(name) or url
        key = short.lower()
        if not url or url in urls or key in names or len(found) >= MAX_STATIONS:
            return
        urls.add(url)
        names.add(key)
        found.append({"name": short, "url": url, **extra})

    for s in style.stations:
        add(s.name, s.url, source="fix")
    errors = []
    candidates = []  # (illeszkedés, sorrend a népszerűségi listában, adó)
    for tag in style.tags:
        try:
            rows = searcher(StationSearch(tag=tag, countrycode=style.countrycode, limit=PER_TAG))
        except ConnectionError as e:
            errors.append(str(e))
            continue
        for rank, r in enumerate(rows):
            if r.hls or 0 < r.bitrate < MIN_BITRATE:
                continue
            score = relevance(r.tags, style.tags)
            if score is not None:
                candidates.append((score + rank / PER_TAG, len(candidates), r))
    for _, _, r in sorted(candidates, key=lambda c: c[:2]):
        add(r.name, r.url, source="keresés", full_name=r.name.strip(), codec=r.codec, bitrate=r.bitrate,
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

    def stations(self, style_id: str, refresh: bool = False) -> list[dict]:
        style = by_id(style_id, self.styles)
        if style is None:
            raise KeyError(style_id)
        with self._lock:
            cache = self._load()
            entry = cache.get(style_id)
            if entry and entry.get("version") != CACHE_VERSION:
                entry = None
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
            cache[style_id] = {"version": CACHE_VERSION, "updated": time.time(), "stations": found}
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.cache_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(cache, ensure_ascii=False))
            tmp.replace(self.cache_file)
            return found
