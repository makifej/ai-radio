"""A radio.toml beolvasása típusos, ellenőrzött beállításokká."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_PATH = "/etc/airadio/radio.toml"


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Feed:
    name: str
    url: str


@dataclass(frozen=True)
class Station:
    name: str
    url: str


@dataclass(frozen=True)
class StationSearch:
    tag: str
    countrycode: str = ""
    limit: int = 3


@dataclass(frozen=True)
class Daypart:
    name: str
    start: int
    stop: int
    host: str
    stations: tuple[Station, ...] = ()
    search: StationSearch | None = None

    def contains(self, hour: int) -> bool:
        # [start, stop); ha start > stop, átnyúlik éjfélen. Ugyanez a szabály
        # a radio.liq in_daypart függvényében.
        if self.start <= self.stop:
            return self.start <= hour < self.stop
        return hour >= self.start or hour < self.stop


@dataclass(frozen=True)
class NewsConfig:
    hours: frozenset[int]
    max_age_hours: float
    min_items: int
    max_items: int
    max_seconds: float
    feeds: tuple[Feed, ...]


@dataclass(frozen=True)
class Config:
    name: str
    data_dir: Path
    voices_dir: Path
    model: str
    effort: str
    fallback: bool
    news: NewsConfig
    host_voice: str
    news_voice: str
    dayparts: tuple[Daypart, ...] = field(default=())

    @property
    def news_dir(self) -> Path:
        return self.data_dir / "news"

    @property
    def state_dir(self) -> Path:
        return self.data_dir / "state"

    @property
    def jingles_dir(self) -> Path:
        return self.data_dir / "jingles"

    @property
    def stations_file(self) -> Path:
        return self.data_dir / "stations.json"

    @property
    def status_file(self) -> Path:
        return self.data_dir / "status.json"

    def daypart_for(self, hour: int) -> Daypart | None:
        return next((d for d in self.dayparts if d.contains(hour)), None)


def _hour(value: object, what: str) -> int:
    if not isinstance(value, int) or not 0 <= value <= 23:
        raise ConfigError(f"{what}: 0 és 23 közötti egész óra kell, ez nem az: {value!r}")
    return value


def _require(table: dict, key: str, what: str):
    if key not in table:
        raise ConfigError(f"Hiányzik: {what}.{key}")
    return table[key]


def parse(raw: dict) -> Config:
    paths = raw.get("paths", {})
    claude = raw.get("claude", {})
    news = _require(raw, "news", "")
    voices = _require(raw, "voices", "")

    effort = claude.get("effort", "medium")
    if effort not in ("low", "medium", "high", "xhigh", "max"):
        raise ConfigError(f"claude.effort ismeretlen érték: {effort!r}")

    feeds = tuple(Feed(name=f["name"], url=f["url"]) for f in news.get("feeds", []))
    if not feeds:
        raise ConfigError("news.feeds üres: legalább egy hírforrás kell")

    news_cfg = NewsConfig(
        hours=frozenset(_hour(h, "news.hours") for h in news.get("hours", range(24))),
        max_age_hours=float(news.get("max_age_hours", 6)),
        min_items=int(news.get("min_items", 3)),
        max_items=int(news.get("max_items", 6)),
        max_seconds=float(news.get("max_seconds", 300)),
        feeds=feeds,
    )
    if news_cfg.min_items < 1 or news_cfg.max_items < news_cfg.min_items:
        raise ConfigError("news.min_items >= 1 és news.max_items >= news.min_items kell")

    dayparts = []
    for d in raw.get("dayparts", []):
        name = _require(d, "name", "dayparts")
        search = d.get("search")
        dayparts.append(
            Daypart(
                name=name,
                start=_hour(_require(d, "start", f"dayparts[{name}]"), f"dayparts[{name}].start"),
                stop=_hour(_require(d, "stop", f"dayparts[{name}]"), f"dayparts[{name}].stop"),
                host=d.get("host", ""),
                stations=tuple(Station(name=s["name"], url=s["url"]) for s in d.get("stations", [])),
                search=StationSearch(
                    tag=search.get("tag", ""),
                    countrycode=search.get("countrycode", ""),
                    limit=int(search.get("limit", 3)),
                )
                if search
                else None,
            )
        )

    data_dir = Path(paths.get("data_dir", "/var/lib/airadio"))
    return Config(
        name=raw.get("name", "AI Rádió"),
        data_dir=data_dir,
        voices_dir=Path(paths.get("voices_dir", data_dir / "voices")),
        model=claude.get("model", "claude-sonnet-5-5"),
        effort=effort,
        fallback=bool(claude.get("fallback", True)),
        news=news_cfg,
        host_voice=_require(voices, "host", "voices"),
        news_voice=_require(voices, "news", "voices"),
        dayparts=tuple(dayparts),
    )


def load(path: str | os.PathLike | None = None) -> Config:
    path = Path(path or os.environ.get("AIRADIO_CONFIG", DEFAULT_PATH))
    try:
        with path.open("rb") as f:
            return parse(tomllib.load(f))
    except FileNotFoundError:
        raise ConfigError(f"Nincs beállítófájl: {path}") from None
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: hibás TOML: {e}") from None
