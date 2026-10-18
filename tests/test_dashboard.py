import base64
import json
import threading
import tomllib
import urllib.error
import urllib.request
import wave
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from airadio import dashboard, history, tomlwrite

EXAMPLE = Path("config/radio.example.toml")


def test_toml_roundtrip_example():
    raw = tomllib.loads(EXAMPLE.read_text())
    assert tomllib.loads(tomlwrite.dumps(raw)) == raw


def test_toml_escapes_and_empty_lists():
    raw = {"name": 'Rádió "idézet" \\ és\núj sor', "dayparts": [], "news": {"hours": [], "x": 1.0}}
    assert tomllib.loads(tomlwrite.dumps(raw)) == raw


@pytest.fixture
def server(tmp_path, raw_config, monkeypatch):
    raw_config["paths"]["data_dir"] = str(tmp_path / "data")
    cfg_path = tmp_path / "radio.toml"
    cfg_path.write_text(tomlwrite.dumps(raw_config))
    monkeypatch.setenv("ICECAST_PORT", "1")  # nincs Icecast: az állapot ezt jelezze
    monkeypatch.setenv("LIVE_PASSWORD", "titok")
    password = {"value": ""}

    def start(pw=""):
        password["value"] = pw
        app = dashboard.Dashboard(cfg_path)
        srv = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.make_handler(app, pw))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return srv, app

    srv, app = start()
    yield {"url": f"http://127.0.0.1:{srv.server_port}", "app": app, "cfg_path": cfg_path, "data": tmp_path / "data", "start": start}
    srv.shutdown()


def call(base, method, path, body=None, raw=None, headers=None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(base + path, data=data, method=method, headers=headers or {})
    if body is not None:
        req.add_header("Content-Type", "application/json")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=10) as resp:
            payload = resp.read()
            return resp.status, json.loads(payload) if resp.headers.get_content_type() == "application/json" else payload
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def test_status_without_radio(server):
    status, st = call(server["url"], "GET", "/api/status")
    assert status == 200
    assert st["radio"] is None and st["icecast"] == {"up": False}
    assert [d["name"] for d in st["dayparts"]] == ["reggel", "ejjel"]


def test_dashboard_page_served(server):
    status, page = call(server["url"], "GET", "/")
    assert status == 200 and b"AI R" in page


def test_config_save_validates_and_writes(server):
    _, raw = call(server["url"], "GET", "/api/config")
    raw["dayparts"][0]["start"] = 99
    status, err = call(server["url"], "PUT", "/api/config", raw)
    assert status == 400 and "Hibás" in err["error"]

    raw["dayparts"][0]["start"] = 5
    raw["dayparts"][0]["stations"] = [{"name": "Új", "url": "http://uj.invalid/stream"}]
    status, res = call(server["url"], "PUT", "/api/config", raw)
    assert status == 200 and res["needs_apply"] is True
    saved = tomllib.loads(server["cfg_path"].read_text())
    assert saved["dayparts"][0]["start"] == 5 and saved["dayparts"][0]["stations"][0]["name"] == "Új"
    assert server["cfg_path"].with_suffix(".toml.bak").exists()


def test_control_file(server):
    call(server["url"], "POST", "/api/control", {"override": "http://a.invalid"})
    call(server["url"], "POST", "/api/control", {"skip_news": True})
    ctl = json.loads((server["data"] / "control.json").read_text())
    assert ctl == {"override": "http://a.invalid", "skip_news": 1}
    call(server["url"], "POST", "/api/control", {"override": ""})
    assert json.loads((server["data"] / "control.json").read_text())["override"] == ""


def tone_wav(path: Path, seconds=1.0):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x00\x10" * int(8000 * seconds))
    return path.read_bytes()


def test_library_upload_list_delete(server, tmp_path):
    data = tone_wav(tmp_path / "a.wav")
    status, _ = call(server["url"], "PUT", "/api/library/backup/zene%201.wav", raw=data)
    assert status == 200
    _, files = call(server["url"], "GET", "/api/library/backup")
    assert [f["name"] for f in files] == ["zene 1.wav"]
    status, _ = call(server["url"], "PUT", "/api/library/backup/..%2F..%2Fx.wav", raw=data)
    assert status == 200 and not (server["data"].parent / "x.wav").exists()  # csak a fájlnév számít
    status, err = call(server["url"], "PUT", "/api/library/backup/szoveg.txt", raw=b"abc")
    assert status == 400
    status, _ = call(server["url"], "DELETE", "/api/library/backup/zene%201.wav")
    assert status == 200 and not (server["data"] / "backup" / "zene 1.wav").exists()


def test_jingle_converted_to_wav(server, tmp_path):
    data = tone_wav(tmp_path / "j.wav")
    status, res = call(server["url"], "PUT", "/api/library/jingles/hirek_eleje.wav", raw=data)
    assert status == 200 and res["name"] == "hirek_eleje.wav"
    with wave.open(str(server["data"] / "jingles" / "hirek_eleje.wav")) as w:
        assert w.getframerate() == 44100 and w.getnchannels() == 2
    status, _ = call(server["url"], "PUT", "/api/library/jingles/valami.wav", raw=data)
    assert status == 400


def test_news_history_cost(server):
    log = history.BulletinLog(server["data"] / "state" / "bulletins.json")
    log.add(kind="hourly", model="claude-sonnet-5-5", input_tokens=1_000_000, output_tokens=100_000, status="kész", items=[])
    _, hist = call(server["url"], "GET", "/api/news/history")
    assert hist["entries"][0]["cost_usd"] == 3.0  # 1M * $2 + 0.1M * $10
    assert hist["cost"]["today"] == {"usd": 3.0, "count": 1}


def test_news_audio_rejects_paths(server):
    status, _ = call(server["url"], "GET", "/api/news/audio/..%2Fstate%2Fbulletins.json")
    assert status == 404


def test_live_info(server):
    _, li = call(server["url"], "GET", "/api/live")
    assert li["password"] == "titok" and li["mount"] == "/live" and li["host"] == "127.0.0.1"


def test_password_required(server):
    srv, _ = server["start"]("jelszo")
    base = f"http://127.0.0.1:{srv.server_port}"
    try:
        status, _ = call(base, "GET", "/api/status")
        assert status == 401
        auth = {"Authorization": "Basic " + base64.b64encode(b"admin:jelszo").decode()}
        status, _ = call(base, "GET", "/api/status", headers=auth)
        assert status == 200
    finally:
        srv.shutdown()
