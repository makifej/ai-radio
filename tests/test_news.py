import json
from dataclasses import replace
from datetime import datetime

from airadio import audio, feeds, news
from conftest import ToneSpeaker, fake_client, news_item

NOW = datetime(2026, 10, 18, 7, 50)
SLOT = datetime(2026, 10, 18, 8)


def bulletin(*ids, text="A Teszt szerint ez egy rövid hír, amely pár mondatból áll."):
    return {
        "intro": "Jó reggelt, nyolc óra van, jöjjenek a hírek.",
        "items": [{"source_id": i, "text": text} for i in ids],
        "outro": "Ennyi volt a hírek.",
    }


def test_next_slot():
    assert news.next_slot(datetime(2026, 10, 18, 7, 50, 12)) == SLOT
    assert news.next_slot(datetime(2026, 10, 18, 23, 50)) == datetime(2026, 10, 19, 0)


def test_produce_writes_checked_bulletin(cfg, monkeypatch):
    monkeypatch.setattr(feeds, "fetch", lambda *a, **k: [news_item(n) for n in (1, 2, 3)])
    # 9-es nem létező tétel, a 2-es duplán szerepel: mindkettő kimarad
    client, messages = fake_client(bulletin(2, 9, 2, 3))
    speaker = ToneSpeaker(seconds_per_char=0.12)  # valós hírblokkhoz hasonló, 15 mp feletti hossz

    path = news.produce(cfg, client, speaker, SLOT, now=NOW)

    assert path == cfg.news_dir / "hirek-20261018-08.wav"
    assert path.exists() and not path.with_suffix(".part").exists()
    check = audio.check(path, cfg.news.max_seconds)
    assert check.seconds > 15
    # bevezető és zárás a műsorvezető, a hírek a hírolvasó hangján
    voices = [v for v, _ in speaker.calls]
    assert voices.count(cfg.host_voice) == 2 and voices.count(cfg.news_voice) == 2
    aired = json.loads((cfg.state_dir / "aired.json").read_text())
    assert [a["title"] for a in aired] == ["Hír 2", "Hír 3"]
    assert "Reggeli házigazda" in messages.calls[0]["messages"][0]["content"]


def test_too_long_drops_last_items(cfg, monkeypatch):
    monkeypatch.setattr(feeds, "fetch", lambda *a, **k: [news_item(n) for n in (1, 2, 3, 4)])
    client, _ = fake_client(bulletin(1, 2, 3, 4, text="x" * 600))  # ~36 mp hírenként
    cfg = replace(cfg, news=replace(cfg.news, max_seconds=100))

    path = news.produce(cfg, client, ToneSpeaker(), SLOT, now=NOW)

    assert audio.duration(path) <= 100
    aired = json.loads((cfg.state_dir / "aired.json").read_text())
    assert [a["title"] for a in aired] == ["Hír 1", "Hír 2"]


def test_skips_outside_news_hours(cfg, monkeypatch):
    monkeypatch.setattr(feeds, "fetch", lambda *a, **k: (_ for _ in ()).throw(AssertionError("nem hívható")))
    assert news.produce(cfg, None, None, datetime(2026, 10, 18, 3), now=NOW) is None


def test_too_few_items_no_claude_call(cfg, monkeypatch):
    monkeypatch.setattr(feeds, "fetch", lambda *a, **k: [news_item(1)])
    client, messages = fake_client(bulletin(1))
    assert news.produce(cfg, client, ToneSpeaker(), SLOT, now=NOW) is None
    assert messages.calls == []


def test_recent_titles_passed_to_writer(cfg, monkeypatch):
    news.AiredLog(cfg.state_dir / "aired.json").record(["Korábbi hír"], datetime(2026, 10, 18, 7))
    news.AiredLog(cfg.state_dir / "aired.json").record(["Nagyon régi"], datetime(2026, 10, 18, 2))
    monkeypatch.setattr(feeds, "fetch", lambda *a, **k: [news_item(n) for n in (1, 2)])
    client, messages = fake_client(bulletin(1, 2))
    news.produce(cfg, client, ToneSpeaker(), SLOT, now=NOW, dry_run=True)
    prompt = messages.calls[0]["messages"][0]["content"]
    assert "Korábbi hír" in prompt and "Nagyon régi" not in prompt


def test_immediate_bulletin(cfg, monkeypatch):
    monkeypatch.setattr(feeds, "fetch", lambda *a, **k: [news_item(n) for n in (1, 2, 3)])
    client, messages = fake_client(bulletin(1, 2, 3))
    late = datetime(2026, 10, 18, 3, 41)  # news.hours-on kívül, de kézzel kérték

    path = news.produce(cfg, client, ToneSpeaker(seconds_per_char=0.12), late, now=late, immediate=True)

    assert path == cfg.news_dir / "azonnal.wav"
    assert "3 óra 41 perc" in messages.calls[0]["messages"][0]["content"]
