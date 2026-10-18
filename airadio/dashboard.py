"""Webes irányítópult: állapot, vezérlés és minden beállítás egy helyen.

Szabványkönyvtári HTTP-szerver JSON API-val; a felület az airadio/web/
dashboard.html egyetlen fájlja. A Liquidsoappal fájlokon keresztül beszél:
a status.json-t olvassa, a control.json-ba ír (kézi állomás, hír leállítása),
állomáslista-változáskor pedig újraindítja (a felügyelő, Docker vagy systemd,
visszahozza).
"""

from __future__ import annotations

import base64
import hmac
import io
import json
import logging
import mimetypes
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import tomllib
import urllib.parse
import urllib.request
import uuid
import wave
from datetime import datetime, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import config as config_mod
from . import history, news, relay as relay_mod, stations, styles as styles_mod, tomlwrite, tts

log = logging.getLogger(__name__)

WEB_DIR = Path(__file__).parent / "web"
HU_VOICES = ("hu_HU-anna-medium", "hu_HU-berta-medium", "hu_HU-imre-medium")
AUDIO_EXT = {".mp3", ".ogg", ".oga", ".opus", ".wav", ".flac", ".m4a", ".aac"}
JINGLES = ("hirek_eleje", "hirek_vege")
STATUS_STALE_SECONDS = 15
MAX_JSON = 1024 * 1024
MAX_UPLOAD = 300 * 1024 * 1024
NEWS_FILE = re.compile(r"^(hirek|azonnal|archiv)-[0-9-]+\.wav$")

_no_proxy = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


# --- Háttérfeladatok ----------------------------------------------------------------


class Jobs:
    """Hosszabb műveletek (hír, alkalmazás, hangletöltés) háttérszálon, követhető kimenettel."""

    def __init__(self):
        self._jobs: dict[str, dict] = {}
        self._lock = threading.Lock()

    def start(self, kind: str, title: str, work) -> dict:
        with self._lock:
            for job in self._jobs.values():
                if job["kind"] == kind and job["status"] == "fut":
                    return job
            job = {
                "id": uuid.uuid4().hex[:10],
                "kind": kind,
                "title": title,
                "status": "fut",
                "lines": [],
                "started": datetime.now().isoformat(timespec="seconds"),
                "finished": None,
            }
            self._jobs[job["id"]] = job
            # csak a legutóbbi 30 marad meg
            for old in sorted(self._jobs.values(), key=lambda j: j["started"])[:-30]:
                self._jobs.pop(old["id"], None)

        def emit(line: str) -> None:
            job["lines"].append(line.rstrip())
            del job["lines"][:-400]

        def run() -> None:
            try:
                ok = work(emit)
                job["status"] = "kész" if ok else "hiba"
            except Exception as e:  # a hiba a feladat kimenetében jelenik meg
                emit(f"HIBA: {e}")
                job["status"] = "hiba"
            job["finished"] = datetime.now().isoformat(timespec="seconds")

        threading.Thread(target=run, daemon=True).start()
        return job

    def get(self, job_id: str) -> dict | None:
        return self._jobs.get(job_id)

    def recent(self) -> list[dict]:
        return sorted(self._jobs.values(), key=lambda j: j["started"], reverse=True)


def run_command(argv: list[str], emit, env: dict) -> bool:
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
    for line in proc.stdout:
        emit(line)
    return proc.wait() == 0


def restart_liquidsoap() -> int:
    """Leállítja a radio.liq-et futtató Liquidsoapot; a felügyelő újraindítja."""
    killed = 0
    for proc_dir in Path("/proc").glob("[0-9]*"):
        try:
            cmd = (proc_dir / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        if cmd and cmd[0].endswith(b"liquidsoap") and any(c.endswith(b"radio.liq") for c in cmd):
            try:
                os.kill(int(proc_dir.name), signal.SIGTERM)
                killed += 1
            except (ProcessLookupError, PermissionError):
                pass
    return killed


# --- Keverőpult ------------------------------------------------------------------------

MIXER_DEFAULT = {
    "music": {"gain": 0.0, "mute": False},
    "news": {"gain": 0.0, "mute": False},
    "live": {"gain": 0.0, "mute": False},
    "cart": {"gain": 0.0, "mute": False},
    "master": {"gain": 0.0, "mute": False},
    "news_bed": -60.0,
    "live_bed": -60.0,
    "eq": {"low": 0.0, "mid": 0.0, "high": 0.0},
    "comp": 0.0,
    "limiter": True,
    "xfade": 1.0,
}
CHANNELS = ("music", "news", "live", "cart", "master")


def _clamp(value, lo: float, hi: float) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise ApiError(400, f"Nem szám: {value!r}") from None
    return float(min(hi, max(lo, v)))  # a Liquidsoap lebegőpontos számot vár


def merge_mixer(current: dict, update: dict) -> dict:
    """Részleges módosítás ellenőrzött, a Liquidsoap által várt teljes alakra.

    A Liquidsoap típusos JSON-olvasója minden mezőt és lebegőpontos számot vár,
    ezért mindig a teljes szerkezetet írjuk ki.
    """
    m = json.loads(json.dumps(current))
    for ch in CHANNELS:
        if ch in update:
            if "gain" in update[ch]:
                m[ch]["gain"] = _clamp(update[ch]["gain"], -60, 12)
            if "mute" in update[ch]:
                m[ch]["mute"] = bool(update[ch]["mute"])
    for key in ("news_bed", "live_bed"):
        if key in update:
            m[key] = _clamp(update[key], -60, 0)
    for band in ("low", "mid", "high"):
        if band in update.get("eq", {}):
            m["eq"][band] = _clamp(update["eq"][band], -12, 12)
    if "comp" in update:
        m["comp"] = _clamp(update["comp"], 0, 1)
    if "xfade" in update:
        m["xfade"] = _clamp(update["xfade"], 0, 8)
    if "limiter" in update:
        m["limiter"] = bool(update["limiter"])
    return m


def qr_svg(text: str) -> str:
    """QR-kód SVG-ként (segno); ha a csomag hiányzik, üres."""
    try:
        import segno
    except ImportError:
        return ""
    buf = io.BytesIO()
    # omitsize: méret helyett viewBox, így a kód a dobozhoz igazodik (hosszú címnél sem vágódik le)
    segno.make(text, error="m").save(buf, kind="svg", scale=5, border=2, dark="#0b0d12", light="#ffffff",
                                     xmldecl=False, omitsize=True)
    return buf.getvalue().decode()


def telnet(command: str, port: int, timeout: float = 2.0) -> str:
    """Egy parancs a Liquidsoap helyi telnet-szerverének; a válasz END-ig tart."""
    import socket

    with socket.create_connection(("127.0.0.1", port), timeout=timeout) as sock:
        sock.sendall(command.encode() + b"\n")
        buf = b""
        while not buf.rstrip().endswith(b"END"):
            chunk = sock.recv(65536)
            if not chunk:
                break
            buf += chunk
        sock.sendall(b"quit\n")
    text = buf.decode("utf-8", "replace").replace("\r\n", "\n").rstrip()
    return text[: -len("END")].rstrip() if text.endswith("END") else text


# --- Az alkalmazás ---------------------------------------------------------------------


class Dashboard:
    def __init__(self, config_path: Path):
        self.config_path = config_path
        self.jobs = Jobs()
        self.icecast = f"http://127.0.0.1:{os.environ.get('ICECAST_PORT', '8000')}"
        self.mount = os.environ.get("ICECAST_MOUNT", "radio.mp3")
        self._speaker = None
        self._speaker_lock = threading.Lock()
        self._control_lock = threading.Lock()
        self._mixer_lock = threading.Lock()
        self.telnet_port = int(os.environ.get("AIRADIO_TELNET_PORT", "1234"))
        # A stílus-továbbító a Liquidsoap belső harbor-portjára küld (Dockerben is 8005).
        self.relay = relay_mod.Relay(port=int(os.environ.get("LIVE_PORT", "8005")),
                                     password=os.environ.get("LIVE_PASSWORD", ""))
        self._catalog: styles_mod.Catalog | None = None
        self._style_lock = threading.Lock()
        self._style_token = 0

    # beállítás

    def cfg(self) -> config_mod.Config:
        return config_mod.load(self.config_path)

    def raw_config(self) -> dict:
        with self.config_path.open("rb") as f:
            return tomllib.load(f)

    def save_config(self, raw: dict) -> dict:
        try:
            parsed = config_mod.parse(raw)
        except (config_mod.ConfigError, KeyError, TypeError, ValueError) as e:
            raise ApiError(400, f"Hibás beállítás: {e}") from None
        text = tomlwrite.dumps(raw)
        tomllib.loads(text)  # biztonsági ellenőrzés: amit kiírunk, az vissza is olvasható
        if self.config_path.exists():
            shutil.copyfile(self.config_path, self.config_path.with_suffix(".toml.bak"))
        tmp = self.config_path.with_suffix(".tmp")
        tmp.write_text(text)
        tmp.replace(self.config_path)
        return {"ok": True, "needs_apply": self._stations_differ(parsed)}

    def _stations_differ(self, cfg: config_mod.Config) -> bool:
        """Eltér-e a napszakbeosztás vagy a saját állomások a futó stations.json-tól."""
        try:
            current = json.loads(cfg.stations_file.read_text())["dayparts"]
        except (FileNotFoundError, json.JSONDecodeError, KeyError):
            return True
        running = {d["name"]: d for d in current}
        for d in cfg.dayparts:
            r = running.pop(d.name, None)
            if r is None or (r["start"], r["stop"]) != (d.start, d.stop):
                return True
            urls = [s["url"] for s in r["stations"]]
            if any(s.url not in urls for s in d.stations):
                return True
        return bool(running)

    # állapot

    def status(self) -> dict:
        cfg = self.cfg()
        radio = None
        try:
            radio = json.loads(cfg.status_file.read_text())
            radio["stale"] = time.time() - cfg.status_file.stat().st_mtime > STATUS_STALE_SECONDS
        except (FileNotFoundError, json.JSONDecodeError):
            pass

        now = datetime.now()
        upcoming = None
        for h in range(1, 25):
            slot = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=h)
            if slot.hour in cfg.news.hours:
                upcoming = {
                    "slot": slot.isoformat(timespec="minutes"),
                    "ready": news.bulletin_path(cfg, slot).exists(),
                }
                break

        entries = history.BulletinLog(cfg.state_dir / "bulletins.json").entries()
        return {
            "now": now.isoformat(timespec="seconds"),
            "name": cfg.name,
            "radio": radio,
            "icecast": self._icecast_stats(),
            "control": self._read_control(cfg),
            "news": {
                "next": upcoming,
                "last": entries[-1] if entries else None,
                "hours": sorted(cfg.news.hours),
            },
            "dayparts": [
                {"name": d.name, "start": d.start, "stop": d.stop, "host": d.host} for d in cfg.dayparts
            ],
            "style": self.relay.state(),
            "model": cfg.model,
            "api_key": bool(os.environ.get("ANTHROPIC_API_KEY")),
            "jobs": [self._job_summary(j) for j in self.jobs.recent()[:5]],
        }

    def _icecast_stats(self) -> dict:
        try:
            with _no_proxy.open(f"{self.icecast}/status-json.xsl", timeout=3) as resp:
                stats = json.load(resp)["icestats"]
        except Exception:
            return {"up": False}
        sources = stats.get("source") or []
        sources = sources if isinstance(sources, list) else [sources]
        src = next((s for s in sources if str(s.get("listenurl", "")).endswith("/" + self.mount)), None)
        if not src:
            return {"up": True, "on_air": False}
        return {
            "up": True,
            "on_air": True,
            "listeners": src.get("listeners", 0),
            "peak": src.get("listener_peak", 0),
            "title": src.get("title", ""),
            "bitrate": src.get("bitrate") or src.get("audio_bitrate"),
            "since": src.get("stream_start_iso8601"),
        }

    @staticmethod
    def _job_summary(job: dict) -> dict:
        return {k: job[k] for k in ("id", "kind", "title", "status", "started", "finished")}

    # vezérlés

    def _read_control(self, cfg: config_mod.Config) -> dict:
        try:
            return json.loads((cfg.data_dir / "control.json").read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return {"override": "", "skip_news": 0}

    def control(self, body: dict, stop_style: bool = True) -> dict:
        """Kézi állomás vagy automata választása leállítja a stílust is."""
        cfg = self.cfg()
        if "override" in body and stop_style:
            self._stop_style(cfg)
        with self._control_lock:
            ctl = self._read_control(cfg)
            if "override" in body:
                ctl["override"] = str(body["override"] or "")
            if body.get("skip_news"):
                ctl["skip_news"] = int(ctl.get("skip_news", 0)) + 1
            path = cfg.data_dir / "control.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"override": ctl["override"], "skip_news": ctl["skip_news"]}))
            tmp.replace(path)
        return ctl

    # stílusok

    @property
    def catalog(self) -> styles_mod.Catalog:
        if self._catalog is None:
            self._catalog = styles_mod.Catalog(self.cfg().state_dir / "styles.json")
        return self._catalog

    def styles(self) -> dict:
        return {
            "styles": [{"id": s.id, "name": s.name, "featured": s.featured, "group": s.group} for s in self.catalog.styles],
            "groups": list(styles_mod.GROUPS),
            "active": self.relay.state(),
        }

    def style_stations(self, style_id: str, refresh: bool = False) -> dict:
        try:
            found = self.catalog.stations(style_id, refresh=refresh)
        except KeyError:
            raise ApiError(404, "Nincs ilyen stílus") from None
        except ConnectionError as e:
            raise ApiError(502, f"A rádiókatalógus most nem érhető el: {e}") from None
        return {"id": style_id, "stations": found, "active": self.relay.state()}

    def _save_style(self, cfg: config_mod.Config, style_id: str, station: str) -> None:
        path = cfg.data_dir / "style.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"style": style_id, "station": station}))
        tmp.replace(path)

    def _stop_style(self, cfg: config_mod.Config) -> None:
        with self._style_lock:
            self._style_token += 1
        if self.relay.state()["style"]:
            self.relay.stop()
        if (cfg.data_dir / "style.json").exists():
            self._save_style(cfg, "", "")

    def set_style(self, body: dict) -> dict:
        cfg = self.cfg()
        style_id = str(body.get("style") or "")
        station = str(body.get("station") or "")
        if not style_id:
            self._stop_style(cfg)
            return self.relay.state()
        style = styles_mod.by_id(style_id, self.catalog.styles)
        if style is None:
            raise ApiError(404, "Nincs ilyen stílus")
        if not self.relay.password:
            raise ApiError(409, "A LIVE_PASSWORD nincs beállítva, stílus nem indítható")
        self._save_style(cfg, style_id, station)
        self.control({"override": ""}, stop_style=False)  # a stílus a kézi állomást is felváltja
        with self._style_lock:
            self._style_token += 1
            token = self._style_token
        if self.relay.state()["style"] != style_id:
            self.relay.set_status(style=style_id, name=style.name, status="keresés", error="", title="", station=None)
        threading.Thread(target=self._start_style, args=(style, station, token), daemon=True).start()
        return self.relay.state()

    def _start_style(self, style: styles_mod.Style, station: str, token: int) -> None:
        try:
            found = self.catalog.stations(style.id)
        except ConnectionError as e:
            found, error = [], f"A rádiókatalógus most nem érhető el: {e}"
        else:
            error = "Ehhez a stílushoz nem találtam állomást"
        with self._style_lock:
            if token != self._style_token:
                return  # közben másik stílust választottak
            if not found:
                # ne szóljon tovább az előző stílus, amíg ez hibát jelez
                self.relay.stop()
                self.relay.set_status(style=style.id, name=style.name, status="hiba", error=error)
                return
            self.relay.play(style.id, style.name, found, station)

    def next_style_station(self) -> dict:
        if not self.relay.state()["style"]:
            raise ApiError(409, "Nincs kiválasztott stílus")
        self.relay.next()
        return self.relay.state()

    def resume_style(self) -> None:
        """Induláskor a legutóbb kiválasztott stílus folytatása."""
        try:
            saved = json.loads((self.cfg().data_dir / "style.json").read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return
        if saved.get("style"):
            try:
                self.set_style(saved)
            except ApiError as e:
                log.warning("A mentett stílus nem indult el: %s", e)

    def _cli(self, *args: str) -> list[str]:
        return [sys.executable, "-m", "airadio.cli", "--config", str(self.config_path), *args]

    def _env(self) -> dict:
        return {**os.environ, "PYTHONUNBUFFERED": "1"}

    def news_job(self, dry_run: bool) -> dict:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise ApiError(409, "Nincs Claude API-kulcs. Írd be az .env fájlba, majd: docker compose up -d")
        args = ["news", "--dry-run"] if dry_run else ["news", "--now"]
        title = "Hírblokk próba (csak szöveg)" if dry_run else "Hírblokk most, adásba"
        return self.jobs.start(
            "news-dry" if dry_run else "news-now",
            title,
            lambda emit: run_command(self._cli(*args), emit, self._env()),
        )

    def apply_job(self) -> dict:
        def work(emit) -> bool:
            emit("Állomáslista összeállítása és a stream-ek kipróbálása…")
            if not run_command(self._cli("stations", "render"), emit, self._env()):
                return False
            killed = restart_liquidsoap()
            if killed:
                emit("A rádió újraindul, pár másodperc csend után az új lista szerint szól.")
            else:
                emit("A Liquidsoap nem fut ezen a gépen, az új lista a következő induláskor lép életbe.")
            return True

        return self.jobs.start("apply", "Állomások alkalmazása", work)

    def feeds_job(self) -> dict:
        return self.jobs.start(
            "feeds", "Hírforrások ellenőrzése", lambda emit: run_command(self._cli("feeds", "check"), emit, self._env())
        )

    def running_stations(self) -> dict:
        try:
            return json.loads(self.cfg().stations_file.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return {"dayparts": []}

    def voices_job(self, names: list[str]) -> dict:
        cfg = self.cfg()
        unknown = [n for n in names if not re.fullmatch(r"[a-z]{2}_[A-Z]{2}-[a-z0-9_]+-(x_low|low|medium|high)", n)]
        if unknown:
            raise ApiError(400, f"Ismeretlen hangnév: {', '.join(unknown)}")
        argv = [sys.executable, "-m", "piper.download_voices", "--data-dir", str(cfg.voices_dir), *names]
        return self.jobs.start("voices", "Hangok letöltése", lambda emit: run_command(argv, emit, self._env()))

    # keverőpult

    def mixer(self) -> dict:
        path = self.cfg().data_dir / "mixer.json"
        try:
            return merge_mixer(MIXER_DEFAULT, json.loads(path.read_text()))
        except (FileNotFoundError, json.JSONDecodeError, AttributeError, TypeError, ApiError):
            return json.loads(json.dumps(MIXER_DEFAULT))

    def set_mixer(self, update: dict) -> dict:
        if not isinstance(update, dict):
            raise ApiError(400, "Hibás keverőpult-beállítás")
        path = self.cfg().data_dir / "mixer.json"
        with self._mixer_lock:
            m = merge_mixer(self.mixer(), update)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(m))
            tmp.replace(path)
        return m

    def levels(self) -> dict:
        try:
            return json.loads(telnet("keveres.szintek", self.telnet_port, timeout=1.0))
        except (OSError, json.JSONDecodeError) as e:
            raise ApiError(503, f"A rádió most nem válaszol: {e}") from None

    def cart(self, body: dict) -> dict:
        if body.get("stop"):
            telnet("cart.flush_and_skip", self.telnet_port)
            return {"ok": True}
        path = self.library_file(str(body.get("kind", "")), str(body.get("name", "")))
        try:
            rid = telnet(f"cart.push {path}", self.telnet_port)
        except OSError as e:
            raise ApiError(503, f"A rádió most nem válaszol: {e}") from None
        return {"ok": True, "request": rid}

    # hangok

    def voices(self) -> list[dict]:
        cfg = self.cfg()
        names = list(dict.fromkeys([*HU_VOICES, cfg.host_voice, cfg.news_voice]))
        return [{"name": n, "downloaded": (cfg.voices_dir / f"{n}.onnx").exists()} for n in names]

    def voice_preview(self, voice: str, text: str) -> bytes:
        cfg = self.cfg()
        text = (text or "").strip()[:400] or "Jó napot, ez itt az AI Rádió."
        with self._speaker_lock:
            if self._speaker is None or self._speaker.voices_dir != cfg.voices_dir:
                self._speaker = tts.PiperSpeaker(cfg.voices_dir)
            buf = io.BytesIO()
            try:
                with wave.open(buf, "wb") as wav:
                    self._speaker._voice(voice).synthesize_wav(text, wav)
            except tts.TTSError as e:
                raise ApiError(409, str(e)) from None
        return buf.getvalue()

    # hírek

    def news_history(self, limit: int) -> dict:
        cfg = self.cfg()
        entries = history.BulletinLog(cfg.state_dir / "bulletins.json").entries()
        today = datetime.now().date().isoformat()
        month = today[:7]
        sums = {"today": [0.0, 0], "month": [0.0, 0], "total": [0.0, 0]}
        tokens = {"input": 0, "output": 0}
        for e in entries:
            cost = history.cost_usd(e.get("model", ""), e.get("input_tokens", 0), e.get("output_tokens", 0)) or 0.0
            e["cost_usd"] = round(cost, 4)
            tokens["input"] += e.get("input_tokens", 0)
            tokens["output"] += e.get("output_tokens", 0)
            for key, match in (("today", today), ("month", month), ("total", "")):
                if e["created"].startswith(match):
                    sums[key][0] += cost
                    sums[key][1] += 1
            e["audio"] = bool(e.get("file")) and (cfg.news_dir / e["file"]).exists()
        return {
            "entries": list(reversed(entries))[:limit],
            "cost": {k: {"usd": round(v[0], 3), "count": v[1]} for k, v in sums.items()},
            "tokens": tokens,
        }

    def news_audio(self, name: str) -> Path:
        if not NEWS_FILE.match(name):
            raise ApiError(404, "Nincs ilyen hanganyag")
        path = self.cfg().news_dir / name
        if not path.exists():
            raise ApiError(404, "A hanganyag már nincs meg (24 óra után törlődik)")
        return path

    # zenetár

    def _library_dir(self, kind: str) -> Path:
        cfg = self.cfg()
        if kind == "backup":
            return cfg.data_dir / "backup"
        if kind == "jingles":
            return cfg.jingles_dir
        raise ApiError(404, "Ismeretlen mappa")

    @staticmethod
    def _safe_name(name: str) -> str:
        name = Path(urllib.parse.unquote(name)).name.strip()
        if not name or name.startswith(".") or "/" in name or "\\" in name:
            raise ApiError(400, "Érvénytelen fájlnév")
        return name

    def library(self, kind: str) -> list[dict]:
        folder = self._library_dir(kind)
        folder.mkdir(parents=True, exist_ok=True)
        files = []
        for p in sorted(folder.iterdir(), key=lambda p: p.name.lower()):
            if p.is_file() and p.suffix.lower() in AUDIO_EXT:
                st = p.stat()
                files.append({"name": p.name, "size": st.st_size, "modified": int(st.st_mtime)})
        return files

    def library_file(self, kind: str, name: str) -> Path:
        path = self._library_dir(kind) / self._safe_name(name)
        if not path.is_file():
            raise ApiError(404, "Nincs ilyen fájl")
        return path

    def library_upload(self, kind: str, name: str, stream, length: int) -> dict:
        name = self._safe_name(name)
        if length <= 0 or length > MAX_UPLOAD:
            raise ApiError(413, "A fájl üres vagy túl nagy (legfeljebb 300 MB)")
        if Path(name).suffix.lower() not in AUDIO_EXT:
            raise ApiError(400, "Csak hangfájl tölthető fel (mp3, ogg, wav, flac, m4a, aac, opus)")
        folder = self._library_dir(kind)
        folder.mkdir(parents=True, exist_ok=True)
        if kind == "jingles":
            stem = Path(name).stem
            if stem not in JINGLES:
                raise ApiError(400, "A hírszignál neve hirek_eleje vagy hirek_vege lehet")
        tmp = folder / f".feltoltes-{uuid.uuid4().hex}{Path(name).suffix}"
        try:
            remaining = length
            with tmp.open("wb") as f:
                while remaining:
                    chunk = stream.read(min(remaining, 1 << 20))
                    if not chunk:
                        raise ApiError(400, "Megszakadt feltöltés")
                    f.write(chunk)
                    remaining -= len(chunk)
            if kind == "jingles":
                # A hírblokk-összefűzés WAV-ot vár: átalakítjuk.
                target = folder / f"{Path(name).stem}.wav"
                result = subprocess.run(
                    ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(tmp), "-ar", "44100", "-ac", "2",
                     "-c:a", "pcm_s16le", str(target)],
                    capture_output=True, text=True,
                )
                if result.returncode != 0:
                    raise ApiError(400, f"Nem hangfájl: {result.stderr.strip()[:200]}")
            else:
                target = folder / name
                tmp.replace(target)
        finally:
            tmp.unlink(missing_ok=True)
        return {"ok": True, "name": target.name}

    def library_delete(self, kind: str, name: str) -> dict:
        self.library_file(kind, name).unlink()
        return {"ok": True}

    # napló, élő adás

    def logs(self, source: str, lines: int) -> list[str]:
        cfg = self.cfg()
        path = cfg.data_dir / "log" / ("liquidsoap.log" if source == "radio" else "airadio.log")
        try:
            with path.open("rb") as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                f.seek(max(0, size - 256 * 1024))
                data = f.read().decode("utf-8", "replace")
        except FileNotFoundError:
            return [f"({path.name} még nincs)"]
        return data.splitlines()[-lines:]

    # hallgatás telefonon

    def phone(self) -> dict:
        """A hallgatói oldal címei telefonra: nyilvános (alagút) és otthoni wifis, QR-kóddal."""
        cfg = self.cfg()
        public, tunnel = None, "nincs"
        metrics = os.environ.get("AIRADIO_TUNNEL_METRICS", "")
        if metrics:
            tunnel = "indul"
            try:
                with _no_proxy.open(metrics.rstrip("/") + "/quicktunnel", timeout=2) as resp:
                    hostname = str(json.load(resp).get("hostname") or "")
                if re.fullmatch(r"[A-Za-z0-9.-]+", hostname):
                    public, tunnel = f"https://{hostname}", "fut"
            except Exception:
                pass
        lan = None
        try:
            ip = str(json.loads((cfg.data_dir / "host.json").read_text()).get("lan_ip", ""))
            if re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", ip):
                lan = f"http://{ip}:{os.environ.get('AIRADIO_PORT', os.environ.get('ICECAST_PORT', '8000'))}"
        except (FileNotFoundError, json.JSONDecodeError, AttributeError):
            pass
        return {"public": public, "tunnel": tunnel, "lan": lan,
                "qr_public": qr_svg(public) if public else "", "qr_lan": qr_svg(lan) if lan else ""}

    def live_info(self, host: str) -> dict:
        port = os.environ.get("AIRADIO_LIVE_PORT") or os.environ.get("LIVE_PORT", "8005")
        return {
            "host": host,
            "port": int(port),
            "mount": "/live",
            "user": "source",
            "password": os.environ.get("LIVE_PASSWORD", ""),
            "public_port": int(os.environ.get("AIRADIO_PORT", os.environ.get("ICECAST_PORT", "8000"))),
        }


# --- HTTP ---------------------------------------------------------------------------------


def make_handler(app: Dashboard, password: str):
    class Handler(BaseHTTPRequestHandler):
        server_version = "airadio"

        def log_message(self, fmt, *args):
            log.debug("%s %s", self.address_string(), fmt % args)

        # válaszok

        def _json(self, data, status: int = 200) -> None:
            body = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _file(self, path: Path, ctype: str) -> None:
            size = path.stat().st_size
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(size))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            with path.open("rb") as f:
                shutil.copyfileobj(f, self.wfile)

        def _bytes(self, data: bytes, ctype: str) -> None:
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_JSON:
                raise ApiError(413, "Túl nagy kérés")
            if not length:
                return {}
            try:
                return json.loads(self.rfile.read(length))
            except json.JSONDecodeError:
                raise ApiError(400, "Hibás JSON") from None

        def _authorized(self) -> bool:
            if not password:
                return True
            header = self.headers.get("Authorization", "")
            if header.startswith("Basic "):
                try:
                    _, _, given = base64.b64decode(header[6:]).decode().partition(":")
                except Exception:
                    return False
                return hmac.compare_digest(given, password)
            return False

        def _dispatch(self, method: str) -> None:
            if not self._authorized():
                self.send_response(HTTPStatus.UNAUTHORIZED)
                self.send_header("WWW-Authenticate", 'Basic realm="AI Radio"')
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            url = urllib.parse.urlsplit(self.path)
            query = dict(urllib.parse.parse_qsl(url.query))
            try:
                self._route(method, url.path, query)
            except ApiError as e:
                self._json({"error": str(e)}, e.status)
            except (BrokenPipeError, ConnectionResetError):
                pass
            except config_mod.ConfigError as e:
                self._json({"error": str(e)}, 500)
            except Exception as e:
                log.exception("Hiba: %s %s", method, self.path)
                self._json({"error": f"Belső hiba: {e}"}, 500)

        def do_GET(self):
            self._dispatch("GET")

        def do_POST(self):
            self._dispatch("POST")

        def do_PUT(self):
            self._dispatch("PUT")

        def do_DELETE(self):
            self._dispatch("DELETE")

        # útvonalak

        def _route(self, method: str, path: str, q: dict) -> None:
            m = None

            def match(pattern: str) -> bool:
                nonlocal m
                m = re.fullmatch(pattern, path)
                return m is not None

            if method == "GET" and path in ("/", "/index.html"):
                return self._file(WEB_DIR / "dashboard.html", "text/html; charset=utf-8")
            if method == "GET" and path == "/viz.js":
                return self._file(WEB_DIR / "viz.js", "text/javascript; charset=utf-8")
            if method == "GET" and path == "/stream":
                return self._stream()
            if method == "GET" and path == "/api/status":
                return self._json(app.status())
            if path == "/api/config":
                if method == "GET":
                    return self._json(app.raw_config())
                if method == "PUT":
                    return self._json(app.save_config(self._body()))
            if method == "POST" and path == "/api/apply":
                return self._json(app.apply_job())
            if path == "/api/mixer":
                if method == "GET":
                    return self._json(app.mixer())
                if method in ("PUT", "POST"):
                    return self._json(app.set_mixer(self._body()))
            if method == "GET" and path == "/api/levels":
                return self._json(app.levels())
            if method == "POST" and path == "/api/cart":
                return self._json(app.cart(self._body()))
            if method == "POST" and path == "/api/control":
                return self._json(app.control(self._body()))
            if method == "GET" and path == "/api/styles":
                return self._json(app.styles())
            if method == "GET" and match(r"/api/styles/([a-z0-9]+)"):
                return self._json(app.style_stations(m.group(1), refresh=q.get("refresh") == "1"))
            if method == "POST" and path == "/api/style":
                return self._json(app.set_style(self._body()))
            if method == "POST" and path == "/api/style/next":
                return self._json(app.next_style_station())
            if method == "GET" and path == "/api/stations/search":
                query = config_mod.StationSearch(tag=q.get("tag", ""), countrycode=q.get("country", "").upper())
                found = stations.search(query, limit=min(int(q.get("limit", 30)), 100), name=q.get("name", ""))
                return self._json([f.__dict__ for f in found])
            if method == "GET" and path == "/api/stations/current":
                return self._json(app.running_stations())
            if method == "POST" and path == "/api/feeds/check":
                return self._json(app.feeds_job())
            if method == "POST" and path == "/api/stations/probe":
                return self._json({"ok": stations.probe(str(self._body().get("url", "")))})
            if method == "POST" and path == "/api/news/now":
                return self._json(app.news_job(dry_run=False))
            if method == "POST" and path == "/api/news/dry-run":
                return self._json(app.news_job(dry_run=True))
            if method == "GET" and path == "/api/news/history":
                return self._json(app.news_history(int(q.get("limit", 30))))
            if method == "GET" and match(r"/api/news/audio/([^/]+)"):
                return self._file(app.news_audio(m.group(1)), "audio/wav")
            if method == "GET" and path == "/api/jobs":
                return self._json([app._job_summary(j) for j in app.jobs.recent()])
            if method == "GET" and match(r"/api/jobs/([0-9a-f]+)"):
                job = app.jobs.get(m.group(1))
                if not job:
                    raise ApiError(404, "Nincs ilyen feladat")
                return self._json(job)
            if method == "GET" and path == "/api/voices":
                return self._json(app.voices())
            if method == "POST" and path == "/api/voices/download":
                return self._json(app.voices_job(list(self._body().get("names", []))))
            if method == "POST" and path == "/api/voices/preview":
                body = self._body()
                return self._bytes(app.voice_preview(str(body.get("voice", "")), str(body.get("text", ""))), "audio/wav")
            if match(r"/api/library/(backup|jingles)"):
                if method == "GET":
                    return self._json(app.library(m.group(1)))
            if match(r"/api/library/(backup|jingles)/([^/]+)"):
                kind, name = m.group(1), m.group(2)
                if method == "GET":
                    path_ = app.library_file(kind, name)
                    return self._file(path_, mimetypes.guess_type(path_.name)[0] or "application/octet-stream")
                if method == "PUT":
                    length = int(self.headers.get("Content-Length") or 0)
                    return self._json(app.library_upload(kind, name, self.rfile, length))
                if method == "DELETE":
                    return self._json(app.library_delete(kind, name))
            if method == "GET" and path == "/api/logs":
                return self._json(app.logs(q.get("source", "radio"), min(int(q.get("lines", 300)), 2000)))
            if method == "GET" and path == "/api/phone":
                return self._json(app.phone())
            if method == "GET" and path == "/api/live":
                host = (self.headers.get("Host") or "localhost").rsplit(":", 1)[0]
                return self._json(app.live_info(host))
            raise ApiError(404, "Nincs ilyen végpont")

        def _stream(self) -> None:
            """Az adás továbbítása azonos eredetről, hogy a böngésző hangszintmérője működjön."""
            try:
                upstream = _no_proxy.open(f"{app.icecast}/{app.mount}", timeout=10)
            except Exception:
                raise ApiError(503, "Az adás most nem érhető el") from None
            with upstream:
                self.send_response(200)
                self.send_header("Content-Type", upstream.headers.get("Content-Type", "audio/mpeg"))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                while chunk := upstream.read1(16384):
                    self.wfile.write(chunk)

    return Handler


def serve(config_path: Path, host: str, port: int) -> None:
    app = Dashboard(config_path)
    app.cfg()  # hibás beállítással ne induljon el csendben
    password = os.environ.get("ADMIN_PASSWORD", "")
    server = ThreadingHTTPServer((host, port), make_handler(app, password))
    server.daemon_threads = True
    log.info("Irányítópult: http://%s:%d%s", host, port, " (jelszóval)" if password else "")
    app.resume_style()
    server.serve_forever()
