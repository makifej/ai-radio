from datetime import datetime, timedelta, timezone

from airadio import feeds
from airadio.config import Feed

NOW = datetime(2026, 10, 18, 8, 0, tzinfo=timezone.utc)


def rss(*items):
    body = "".join(
        f"<item><title>{t}</title><link>{l}</link><description>{d}</description>{p}</item>"
        for t, l, d, p in items
    )
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>T</title>{body}</channel></rss>'.encode()


def pub(dt):
    return f"<pubDate>{dt.strftime('%a, %d %b %Y %H:%M:%S +0000')}</pubDate>"


def test_old_undated_and_html_handling():
    payload = rss(
        ("Friss hír", "https://x/1", "&lt;p&gt;Ez &amp; az&lt;/p&gt;", pub(NOW - timedelta(hours=1))),
        ("Régi hír", "https://x/2", "régi", pub(NOW - timedelta(hours=30))),
        ("Dátum nélkül", "https://x/3", "nincs dátum", ""),
    )
    items = feeds.parse_feed(Feed("Teszt", "u"), payload, NOW, timedelta(hours=6))
    assert [i.title for i in items] == ["Friss hír"]
    assert items[0].summary == "Ez & az"
    assert items[0].source == "Teszt"


def test_fetch_sorts_and_deduplicates(monkeypatch):
    a = rss(
        ("Első", "https://x/1", "a", pub(NOW - timedelta(hours=2))),
        ("Második", "https://x/2", "b", pub(NOW - timedelta(minutes=5))),
    )
    b = rss(("ELSŐ", "https://y/9", "ugyanaz más forrásból", pub(NOW - timedelta(hours=1))))
    payloads = {"a": a, "b": b, "rossz": None}

    def fake_download(url, timeout=20):
        if payloads[url] is None:
            raise OSError("nem érhető el")
        return payloads[url]

    monkeypatch.setattr(feeds, "download", fake_download)
    items = feeds.fetch((Feed("A", "a"), Feed("Rossz", "rossz"), Feed("B", "b")), 6, now=NOW)
    assert [i.title for i in items] == ["Második", "ELSŐ"]
