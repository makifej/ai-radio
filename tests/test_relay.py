import sys
import time

import pytest

from airadio import relay as relay_mod

# Álffmpeg: a bemeneti cím alapján viselkedik; a valódi ffmpeg kimenetének mintáit írja.
FAKE_FFMPEG = r'''
import sys, time
url = sys.argv[sys.argv.index("-i") + 1]
if "halott" in url:
    print("Error opening input files: Connection refused", file=sys.stderr, flush=True)
    sys.exit(1)
if "harbor" in url:
    print("Error opening output icecast://source:titok@127.0.0.1:1/stilus_a: Connection refused", file=sys.stderr, flush=True)
    sys.exit(1)
print("    StreamTitle     : Első - Szám", file=sys.stderr, flush=True)
time.sleep(0.3)
print("[http @ 0x1] Metadata update for StreamTitle: Második - Szám", file=sys.stderr, flush=True)
time.sleep(60)
'''


class FakeLiq:
    """A Liquidsoap telnetje: egy bemenet akkor kész, ha fél másodperce fut rajta egy adó."""

    def __init__(self):
        self.relay = None
        self.active = ""
        self.history = []

    def __call__(self, command):
        name, _, arg = command.partition(" ")
        if name == "stilus.aktiv":
            self.active = arg
            self.history.append(arg)
            return "OK"
        if name == "stilus.kesz":
            info = self.relay._procs.get(arg)
            ok = bool(info) and info["proc"].poll() is None and time.monotonic() - info["started"] > 0.5
            return "true" if ok else "false"
        return ""


@pytest.fixture
def make_relay(tmp_path, monkeypatch):
    script = tmp_path / "ffmpeg.py"
    script.write_text(FAKE_FFMPEG)
    runner = tmp_path / "ffmpeg"
    runner.write_text(f"#!/bin/sh\nexec {sys.executable} {script} \"$@\"\n")
    runner.chmod(0o755)
    monkeypatch.setattr(relay_mod, "CROSSFADE", 0.2)
    monkeypatch.setattr(relay_mod.Relay, "_push_title", lambda self, t, m="a": None)
    made = []

    def make():
        liq = FakeLiq()
        r = relay_mod.Relay(port=1, password="titok", liq=liq, ffmpeg=str(runner))
        liq.relay = r
        made.append(r)
        return r, liq

    yield make
    for r in made:
        r.stop()
        time.sleep(0.2)


def wait_for(cond, timeout=8.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def alive(info):
    return info is not None and info["proc"].poll() is None


def test_title_regex():
    assert relay_mod.TITLE_RE.search("[http @ 0x55] Metadata update for StreamTitle: A - B").group(1) == "A - B"
    assert relay_mod.TITLE_RE.search("    StreamTitle     : X - Y").group(1) == "X - Y"
    assert relay_mod.TITLE_RE.search("    icy-name        : Valami") is None


def test_command_fast_start_and_quoting():
    r = relay_mod.Relay(port=8005, password="a/b@c", ffmpeg="ffmpeg")
    cmd = r._command("https://example.com/stream", "b")
    assert cmd[-1] == "icecast://source:a%2Fb%40c@127.0.0.1:8005/stilus_b"
    i = cmd.index("-i")
    for opt in ("-readrate", "-readrate_initial_burst", "-probesize", "-analyzeduration", "-icy"):
        assert cmd.index(opt) < i, opt  # bemeneti kapcsolók: a -i előtt
    assert "-icy" not in r._command("/tmp/file.mp3", "a")
    assert r._output_error("Error opening output icecast://source:***@127.0.0.1:8005/stilus_a: Connection refused")
    assert not r._output_error("Error opening input files: Connection refused")


def test_failover_switches_only_when_ready_and_keeps_old_playing(make_relay):
    r, liq = make_relay()
    stations = [{"name": "Halott", "url": "http://halott/x"}, {"name": "Jó", "url": "http://jo/x"},
                {"name": "Másik", "url": "http://masik/x"}, {"name": "Halott 2", "url": "http://halott/2"}]
    r.play("metal", "Metal", stations)
    assert wait_for(lambda: r.state()["status"] == "szól")
    st = r.state()
    assert st["station"]["name"] == "Jó" and st["failed"] == ["http://halott/x"] and st["title"] == "Második - Szám"
    first = liq.active
    assert first in ("a", "b")

    # következő: a másik bemeneten indul, átkapcsolás, a régi utána leáll
    old = r._procs[first]
    r.next()
    assert wait_for(lambda: (r.state()["station"] or {}).get("name") == "Másik" and r.state()["status"] == "szól")
    assert liq.active != first and wait_for(lambda: not alive(old))

    # nem szóló adóra lépve a mostani szól tovább, amíg nincs jobb
    current = r._procs[liq.active]
    r.next()  # Halott 2: nem indul, utána a lista elejéről a Halott, majd a Jó
    assert wait_for(lambda: (r.state()["station"] or {}).get("name") == "Jó" and r.state()["status"] == "szól")
    assert "" not in liq.history  # közben egyszer sem kapcsolt ki
    assert wait_for(lambda: not alive(current))


def test_stop_switches_off_immediately(make_relay):
    r, liq = make_relay()
    r.play("chill", "Chill", [{"name": "Jó", "url": "http://jo/x"}])
    assert wait_for(lambda: r.state()["status"] == "szól")
    procs = [i for i in r._procs.values() if i]
    t0 = time.monotonic()
    r.stop()
    assert wait_for(lambda: liq.active == "", timeout=2)
    assert time.monotonic() - t0 < 1.0
    assert wait_for(lambda: not any(alive(i) for i in procs))
    assert r.state()["status"] == "ki"


def test_harbor_error_does_not_mark_station_failed(make_relay):
    r, _ = make_relay()
    r.play("x", "X", [{"name": "Harbor", "url": "http://harbor/x"}])
    assert wait_for(lambda: r.state()["status"] == "várakozás")
    assert r.state()["failed"] == []


def test_all_failed_reports_error_and_releases(make_relay, monkeypatch):
    monkeypatch.setattr(relay_mod, "ALL_FAILED_WAIT", 30)
    r, liq = make_relay()
    r.play("x", "X", [{"name": "H1", "url": "http://halott/1"}, {"name": "H2", "url": "http://halott/2"}])
    assert wait_for(lambda: r.state()["status"] == "hiba")
    assert "Egyik állomás sem szól" in r.state()["error"] and liq.active == ""
