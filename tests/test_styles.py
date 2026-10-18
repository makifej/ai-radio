import json
import re

import pytest

from airadio import styles
from airadio.config import Station
from airadio.stations import Found


def found(name, url, bitrate=128, hls=False):
    return Found(name=name, url=url, codec="MP3", bitrate=bitrate, tags="", countrycode="HU", hls=hls)


def test_defaults_cover_requested_styles():
    ids = {s.id for s in styles.DEFAULT_STYLES}
    assert {"rock", "metal", "numetal", "chill", "dance", "joga", "meditacio"} <= ids
    featured = [s.name for s in styles.DEFAULT_STYLES if s.featured]
    assert featured == ["Rock", "Metal", "Nu metal", "Chill", "Dance", "Jóga", "Meditáció"]
    assert len(ids) == len(styles.DEFAULT_STYLES)  # egyedi azonosítók
    assert all(re.fullmatch(r"[a-z0-9]+", s.id) for s in styles.DEFAULT_STYLES)  # az API útvonala ezt várja


def test_gather_fixed_first_dedupes_and_filters():
    style = styles.Style("x", "X", ("a", "b"), (Station("Fix", "http://fix/stream"),))
    calls = []

    def searcher(query):
        calls.append(query.tag)
        return {
            "a": [found("Egy", "http://1"), found("fix", "http://masik"), found("HLS", "http://h", hls=True),
                  found("Gyenge", "http://g", bitrate=32), found("Ismeretlen", "http://u", bitrate=0)],
            "b": [found("Egy másik néven", "http://1"), found("Kettő", "http://2")],
        }[query.tag]

    result = styles.gather(style, searcher)
    assert [r["url"] for r in result] == ["http://fix/stream", "http://1", "http://u", "http://2"]
    assert result[0]["source"] == "fix" and result[1]["source"] == "keresés"
    assert calls == ["a", "b"]


def test_gather_stops_at_max(monkeypatch):
    monkeypatch.setattr(styles, "MAX_STATIONS", 2)
    style = styles.Style("x", "X", ("a", "b"))
    calls = []

    def searcher(query):
        calls.append(query.tag)
        return [found(f"{query.tag}{i}", f"http://{query.tag}{i}") for i in range(5)]

    assert len(styles.gather(style, searcher)) == 2 and calls == ["a"]


def test_catalog_caches_and_falls_back(tmp_path):
    style = styles.Style("x", "X", ("a",), (Station("Fix", "http://fix"),))
    n = {"calls": 0, "fail": False}

    def searcher(query):
        n["calls"] += 1
        if n["fail"]:
            raise ConnectionError("nincs hálózat")
        return [found("Egy", "http://1")]

    cat = styles.Catalog(tmp_path / "styles.json", (style,), searcher)
    assert [s["url"] for s in cat.stations("x")] == ["http://fix", "http://1"]
    cat.stations("x")
    assert n["calls"] == 1  # gyorsítótárból
    n["fail"] = True
    assert [s["url"] for s in cat.stations("x", refresh=True)] == ["http://fix", "http://1"]  # a régi lista marad
    assert json.loads((tmp_path / "styles.json").read_text())["x"]["stations"][1]["name"] == "Egy"
    with pytest.raises(KeyError):
        cat.stations("nincs")


def test_catalog_offline_without_cache_uses_fixed_or_raises(tmp_path):
    def offline(query):
        raise ConnectionError("nincs hálózat")

    with_fixed = styles.Style("f", "F", ("a",), (Station("Fix", "http://fix"),))
    without = styles.Style("n", "N", ("a",))
    cat = styles.Catalog(tmp_path / "styles.json", (with_fixed, without), offline)
    assert [s["url"] for s in cat.stations("f")] == ["http://fix"]
    with pytest.raises(ConnectionError):
        cat.stations("n")
