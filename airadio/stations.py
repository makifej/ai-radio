"""Állomások keresése a Radio Browser katalógusban és a stations.json előállítása.

A stations.json-t a radio.liq induláskor olvassa; módosítás után a rádiót
újra kell indítani (`systemctl restart airadio-radio`).
"""

from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .config import Config, Station, StationSearch

log = logging.getLogger(__name__)

USER_AGENT = "airadio/0.1"
SERVERS = (
    "https://de1.api.radio-browser.info",
    "https://nl1.api.radio-browser.info",
    "https://at1.api.radio-browser.info",
)
PROBE_BYTES = 16 * 1024


@dataclass(frozen=True)
class Found:
    name: str
    url: str
    codec: str
    bitrate: int
    tags: str
    countrycode: str
    homepage: str = ""
    favicon: str = ""
    votes: int = 0


def search(query: StationSearch, limit: int | None = None, timeout: float = 15, name: str = "") -> list[Found]:
    params = {
        "hidebroken": "true",
        "order": "clickcount",
        "reverse": "true",
        "limit": str(limit or query.limit),
    }
    if query.tag:
        params["tag"] = query.tag
    if query.countrycode:
        params["countrycode"] = query.countrycode
    if name:
        params["name"] = name
    path = "/json/stations/search?" + urllib.parse.urlencode(params)

    last_error: Exception | None = None
    for server in SERVERS:
        req = urllib.request.Request(server + path, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                rows = json.load(resp)
            break
        except Exception as e:  # következő tükörszerver
            last_error = e
            log.debug("%s nem válaszolt: %s", server, e)
    else:
        raise ConnectionError(f"Egyik Radio Browser szerver sem érhető el: {last_error}")

    return [
        Found(
            name=r.get("name", "").strip(),
            url=(r.get("url_resolved") or r.get("url") or "").strip(),
            codec=r.get("codec", ""),
            bitrate=int(r.get("bitrate") or 0),
            tags=r.get("tags", ""),
            countrycode=r.get("countrycode", ""),
            homepage=r.get("homepage", "") or "",
            favicon=r.get("favicon", "") or "",
            votes=int(r.get("votes") or 0),
        )
        for r in rows
        if r.get("url_resolved") or r.get("url")
    ]


def probe(url: str, timeout: float = 10) -> bool:
    """Szól-e: válaszol-e a stream, és jön-e belőle hangadat."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Icy-MetaData": "0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            ctype = resp.headers.get("Content-Type", "")
            if ctype.startswith("text/html"):
                return False
            return len(resp.read(PROBE_BYTES)) >= PROBE_BYTES // 2
    except Exception as e:
        log.debug("%s nem szól: %s", url, e)
        return False


def resolve(cfg: Config, prober=probe, searcher=search) -> dict:
    """Napszakonként a saját állomások, majd a keresés működő találatai."""
    dayparts = []
    for d in cfg.dayparts:
        stations: list[Station] = []
        for s in d.stations:
            if not prober(s.url):
                log.warning("%s: a megadott %s most nem szól, de a listában marad", d.name, s.name)
            stations.append(s)

        if d.search:
            known = {s.url for s in stations}
            try:
                candidates = searcher(d.search, limit=d.search.limit * 4)
            except ConnectionError as e:
                log.warning("%s: keresés sikertelen: %s", d.name, e)
                candidates = []
            added = 0
            for c in candidates:
                if added >= d.search.limit:
                    break
                if c.url in known or not prober(c.url):
                    continue
                stations.append(Station(name=c.name or c.url, url=c.url))
                known.add(c.url)
                added += 1

        if not stations:
            log.warning("%s: nincs működő állomás, ebben a napszakban a tartalék szól", d.name)
        log.info("%s: %s", d.name, ", ".join(s.name for s in stations) or "-")
        dayparts.append(
            {
                "name": d.name,
                "start": d.start,
                "stop": d.stop,
                "stations": [{"name": s.name, "url": s.url} for s in stations],
            }
        )
    return {"dayparts": dayparts}


def write(cfg: Config, resolved: dict) -> Path:
    path = cfg.stations_file
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(resolved, ensure_ascii=False, indent=2))
    tmp.replace(path)
    return path
