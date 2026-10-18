import json

from airadio import stations


def test_resolve_keeps_own_and_adds_working_search_results(cfg):
    found = [
        stations.Found("Halott", "http://halott.invalid", "MP3", 128, "ambient", "DE"),
        stations.Found("Jó 1", "http://jo1.invalid", "MP3", 128, "ambient", "DE"),
        stations.Found("Saját másolat", "http://sajat.invalid/stream", "MP3", 128, "", ""),
        stations.Found("Jó 2", "http://jo2.invalid", "AAC", 96, "ambient", "AT"),
        stations.Found("Jó 3", "http://jo3.invalid", "MP3", 128, "ambient", "AT"),
    ]
    searched = []

    def searcher(query, limit):
        searched.append((query.tag, limit))
        return found

    alive = {"http://jo1.invalid", "http://jo2.invalid", "http://jo3.invalid"}
    resolved = stations.resolve(cfg, prober=lambda url: url in alive, searcher=searcher)

    reggel, ejjel = resolved["dayparts"]
    assert reggel == {"name": "reggel", "start": 6, "stop": 10, "stations": []}
    # a saját állomás akkor is marad, ha most nem szól; a keresésből 2 működő jön
    assert [s["name"] for s in ejjel["stations"]] == ["Saját", "Jó 1", "Jó 2"]
    assert searched == [("ambient", 8)]


def test_write_is_liquidsoap_shape(cfg):
    path = stations.write(cfg, {"dayparts": [{"name": "a", "start": 0, "stop": 0, "stations": []}]})
    assert json.loads(path.read_text())["dayparts"][0]["name"] == "a"
    assert not path.with_suffix(".tmp").exists()
