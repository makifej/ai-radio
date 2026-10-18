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
    print("Error opening output icecast://source:titok@127.0.0.1:1/stilus: Connection refused", file=sys.stderr, flush=True)
    sys.exit(1)
print("    StreamTitle     : Első - Szám", file=sys.stderr, flush=True)
time.sleep(0.3)
print("[http @ 0x1] Metadata update for StreamTitle: Második - Szám", file=sys.stderr, flush=True)
time.sleep(60)
'''


@pytest.fixture
def fake_ffmpeg(tmp_path, monkeypatch):
    script = tmp_path / "ffmpeg.py"
    script.write_text(FAKE_FFMPEG)
    runner = tmp_path / "ffmpeg"
    runner.write_text(f"#!/bin/sh\nexec {sys.executable} {script} \"$@\"\n")
    runner.chmod(0o755)
    monkeypatch.setattr(relay_mod, "PLAYING_AFTER", 0.5)
    return str(runner)


def wait_for(cond, timeout=8.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def test_title_regex():
    assert relay_mod.TITLE_RE.search("[http @ 0x55] Metadata update for StreamTitle: A - B").group(1) == "A - B"
    assert relay_mod.TITLE_RE.search("    StreamTitle     : X - Y").group(1) == "X - Y"
    assert relay_mod.TITLE_RE.search("    icy-name        : Valami") is None


def test_command_quotes_password_and_reads_in_real_time():
    r = relay_mod.Relay(port=8005, password="a/b@c", ffmpeg="ffmpeg")
    cmd = r._command("https://example.com/stream")
    assert cmd[-1] == "icecast://source:a%2Fb%40c@127.0.0.1:8005/stilus"
    assert cmd.index("-re") < cmd.index("-i") and "-icy" in cmd
    assert "-icy" not in r._command("/tmp/file.mp3")
    assert r._output_error("Error opening output icecast://source:***@127.0.0.1:8005/stilus: Connection refused")
    assert not r._output_error("Error opening input files: Connection refused")


def test_failover_title_next_and_stop(fake_ffmpeg, monkeypatch):
    pushed = []
    monkeypatch.setattr(relay_mod.Relay, "_push_title", lambda self, t: pushed.append(t))
    r = relay_mod.Relay(port=1, password="titok", ffmpeg=fake_ffmpeg)
    stations = [{"name": "Halott", "url": "http://halott/x"}, {"name": "Jó", "url": "http://jo/x"},
                {"name": "Másik", "url": "http://masik/x"}]
    r.play("metal", "Metal", stations)
    assert wait_for(lambda: r.state()["status"] == "szól")
    st = r.state()
    assert st["station"]["name"] == "Jó" and st["failed"] == ["http://halott/x"]
    assert st["title"] == "Második - Szám" and "Második - Szám" in pushed

    r.next()
    assert wait_for(lambda: (r.state()["station"] or {}).get("name") == "Másik" and r.state()["status"] == "szól")
    proc = r._proc
    r.stop()
    assert wait_for(lambda: proc.poll() is not None)
    assert r.state() | {"failed": []} == {"style": "", "name": "", "station": None, "status": "ki", "title": "", "error": "", "failed": []}


def test_harbor_error_does_not_mark_station_failed(fake_ffmpeg, monkeypatch):
    monkeypatch.setattr(relay_mod.Relay, "_push_title", lambda self, t: None)
    r = relay_mod.Relay(port=1, password="titok", ffmpeg=fake_ffmpeg)
    r.play("x", "X", [{"name": "Harbor", "url": "http://harbor/x"}])
    assert wait_for(lambda: r.state()["status"] == "várakozás")
    assert r.state()["failed"] == []
    r.stop()


def test_all_failed_reports_error(fake_ffmpeg, monkeypatch):
    monkeypatch.setattr(relay_mod, "ALL_FAILED_WAIT", 30)
    r = relay_mod.Relay(port=1, password="titok", ffmpeg=fake_ffmpeg)
    r.play("x", "X", [{"name": "H1", "url": "http://halott/1"}, {"name": "H2", "url": "http://halott/2"}])
    assert wait_for(lambda: r.state()["status"] == "hiba")
    assert "Egyik állomás sem szól" in r.state()["error"]
    r.stop()
