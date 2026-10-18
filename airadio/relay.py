"""Egy kiválasztott online rádió továbbítása a Liquidsoap „stilus” bemenetére.

Az ffmpeg olvassa az állomást (újracsatlakozással, ICY-dalcímekkel), egységes
MP3-ra alakítja, és Icecast-forrásként a Liquidsoap harbor bemenetére küldi.
A Liquidsoap ezt a zene elé sorolja, így stílusváltáskor nem kell újraindítani,
és a többi állomás bemenetét sem kell ki-be kapcsolni (ami a 2.2.4-es
input.http-nél elnémuláshoz vezethet). Ha egy állomás nem szól, a lista
következő eleme jön; a dalcímet a harbor /admin/metadata címén frissítjük.
"""

from __future__ import annotations

import base64
import logging
import re
import shutil
import subprocess
import threading
import time
import urllib.parse
import urllib.request

log = logging.getLogger(__name__)

USER_AGENT = "airadio/0.1"
TITLE_RE = re.compile(r"(?:Metadata update for StreamTitle:|^\s*StreamTitle\s*:)\s?(.*)$")
PLAYING_AFTER = 4.0      # ennyi másodperc hibátlan futás után tekintjük szólónak
LONG_PLAY = 30.0         # ennél tovább szólt: megszakadás után ugyanazt próbáljuk újra
ALL_FAILED_WAIT = 20.0   # ha egyik állomás sem szól, ennyit várunk az újabb kör előtt

_no_proxy = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class Relay:
    def __init__(self, port: int, password: str, mount: str = "stilus", host: str = "127.0.0.1",
                 ffmpeg: str | None = None, bitrate: str = "256k"):
        self.port = port
        self.password = password
        self.mount = mount
        self.host = host
        self.ffmpeg = ffmpeg or shutil.which("ffmpeg") or "ffmpeg"
        self.bitrate = bitrate
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._gen = 0
        self._want: dict | None = None
        self._proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._state = {"style": "", "name": "", "station": None, "status": "ki", "title": "", "error": "", "failed": []}

    # vezérlés

    def play(self, style: str, name: str, stations: list[dict], start_url: str = "") -> None:
        if not stations:
            raise ValueError("üres állomáslista")
        index = next((i for i, s in enumerate(stations) if s["url"] == start_url), 0)
        with self._lock:
            self._gen += 1
            self._want = {"style": style, "name": name, "stations": list(stations), "index": index, "failed": set()}
            self._update(style=style, name=name, status="kapcsolódás", error="", title="", failed=[])
        self._kick()

    def next(self) -> None:
        with self._lock:
            if not self._want:
                return
            self._gen += 1
            self._want["index"] += 1
        self._kick()

    def stop(self) -> None:
        with self._lock:
            self._gen += 1
            self._want = None
            self._update(style="", name="", station=None, status="ki", title="", error="", failed=[])
        self._kick()

    def state(self) -> dict:
        with self._lock:
            return dict(self._state)

    def set_status(self, **fields) -> None:
        """Kívülről (pl. keresés közben) állítható állapot."""
        with self._lock:
            self._update(**fields)

    # belső

    def _update(self, **fields) -> None:
        self._state.update(fields)

    def _kick(self) -> None:
        proc = self._proc
        if proc and proc.poll() is None:
            proc.terminate()
        if not self._thread or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run, name="relay", daemon=True)
            self._thread.start()
        self._wake.set()

    def _run(self) -> None:
        retried_url = ""
        while True:
            with self._lock:
                want, gen = self._want, self._gen
                if want:
                    st = want["stations"][want["index"] % len(want["stations"])]
                    self._update(station={"name": st["name"], "url": st["url"]}, status="kapcsolódás", title="")
            if not want:
                self._wake.wait()
                self._wake.clear()
                continue
            self._wake.clear()

            started = time.monotonic()
            outcome = self._stream(st, gen)
            played = time.monotonic() - started
            with self._lock:
                if gen != self._gen:
                    continue  # közben új utasítás jött
                if outcome == "harbor":
                    # a Liquidsoap nem fogadja (pl. újraindul): ugyanazt később újra
                    self._update(status="várakozás", error="A rádiómotor most nem fogadja az adást")
                    wait = 3.0
                elif played >= LONG_PLAY and retried_url != st["url"]:
                    retried_url = st["url"]  # szólt, majd megszakadt: egyszer újra
                    wait = 1.0
                else:
                    retried_url = ""
                    want["failed"].add(st["url"])
                    self._update(failed=sorted(want["failed"]))
                    log.warning("Stílus %s: %s nem szól, jön a következő", want["name"], st["name"])
                    want["index"] += 1
                    wait = 0.3
                    if len(want["failed"]) >= len(want["stations"]):
                        want["failed"].clear()
                        self._update(status="hiba", error="Egyik állomás sem szól most, hamarosan újrapróbálom")
                        wait = ALL_FAILED_WAIT
            if self._wake.wait(wait):
                self._wake.clear()

    def _command(self, url: str) -> list[str]:
        target = "icecast://source:{}@{}:{}/{}".format(
            urllib.parse.quote(self.password, safe=""), self.host, self.port, self.mount)
        http_opts = []
        if url.startswith(("http://", "https://")):
            http_opts = ["-icy", "1", "-reconnect", "1", "-reconnect_streamed", "1",
                         "-reconnect_delay_max", "4", "-rw_timeout", "15000000", "-user_agent", USER_AGENT]
        return [
            self.ffmpeg, "-nostdin", "-nostats", "-hide_banner", "-loglevel", "verbose",
            # -re: valós időben olvasunk, különben az állomás indulási löketét a harbor
            # pufferelné, és leállítás vagy váltás után még másodpercekig a régi szólna
            *http_opts, "-re", "-i", url,
            "-vn", "-map", "0:a:0", "-c:a", "libmp3lame", "-b:a", self.bitrate, "-ar", "44100", "-ac", "2",
            "-content_type", "audio/mpeg", "-f", "mp3", target,
        ]

    def _stream(self, st: dict, gen: int) -> str:
        """Egy állomás továbbítása, amíg szól. Eredmény: 'stop', 'harbor' vagy 'source'."""
        try:
            proc = subprocess.Popen(self._command(st["url"]), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.PIPE, text=True, errors="replace", bufsize=1)
        except OSError as e:
            with self._lock:
                self._update(status="hiba", error=f"Az ffmpeg nem indul: {e}")
            return "harbor"
        with self._lock:
            if gen != self._gen:
                proc.terminate()
            self._proc = proc

        tail: list[str] = []
        timer = threading.Timer(PLAYING_AFTER, lambda: self._mark_playing(proc, gen))
        timer.daemon = True
        timer.start()
        try:
            for line in proc.stderr:
                line = line.rstrip()
                m = TITLE_RE.search(line)
                if m:
                    self._title(m.group(1).strip(), gen)
                if not line.startswith("  ") and "Metadata update" not in line:
                    tail = (tail + [line.replace(self.password, "***") if self.password else line])[-6:]
        finally:
            timer.cancel()
            proc.wait()

        with self._lock:
            if gen != self._gen:
                return "stop"
        if any(self._output_error(line) for line in tail):
            log.warning("A harbor nem fogadja az adást: %s", tail[-1:] or "")
            return "harbor"
        log.info("Stílus-továbbítás vége (%s): %s", st["name"], tail[-1:] or "")
        return "source"

    def _output_error(self, line: str) -> bool:
        """A hiba a kimeneten (a Liquidsoap felé) van-e, nem az állomásnál."""
        markers = ("Error opening output", "Error writing trailer", "av_interleaved_write_frame",
                   "icecast://", "[icecast", f"tcp://{self.host}:{self.port}")
        return any(m in line for m in markers)

    def _mark_playing(self, proc: subprocess.Popen, gen: int) -> None:
        with self._lock:
            if gen != self._gen or proc.poll() is not None:
                return
            self._update(status="szól", error="")
            title = self._state.get("title")
        # az első dalcím még a harbor-kapcsolat előtt érkezik: most küldjük el újra
        if title:
            threading.Thread(target=self._push_title, args=(title,), daemon=True).start()

    def _title(self, title: str, gen: int) -> None:
        with self._lock:
            if gen != self._gen or title == self._state.get("title"):
                return
            self._update(title=title)
        threading.Thread(target=self._push_title, args=(title,), daemon=True).start()

    def _push_title(self, title: str) -> None:
        query = urllib.parse.urlencode({"mount": "/" + self.mount, "mode": "updinfo", "song": title})
        req = urllib.request.Request(f"http://{self.host}:{self.port}/admin/metadata?{query}")
        token = f"source:{self.password}".encode()
        req.add_header("Authorization", "Basic " + base64.b64encode(token).decode())
        try:
            _no_proxy.open(req, timeout=3).close()
        except Exception as e:
            log.debug("Dalcím frissítése nem sikerült: %s", e)
