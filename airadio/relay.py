"""Egy kiválasztott online rádió továbbítása a Liquidsoap stílus-bemenetére.

Az ffmpeg olvassa az állomást (újracsatlakozással, ICY-dalcímekkel), egységes
MP3-ra alakítja, és Icecast-forrásként a Liquidsoap harbor bemenetére küldi.
Két bemenet váltakozik (stilus_a, stilus_b): az új állomás mindig a szabad
bemeneten csatlakozik, és csak akkor kapcsolunk át rá (telneten: stilus.aktiv),
amikor a Liquidsoap szerint már szól. Így váltáskor a régi addig megy, nincs
csend, és egy nem szóló adó sem szakítja meg az adást. Stílus kikapcsolásakor
az átkapcsolás azonnali, nem kell kivárni a puffert. Ha egy állomás nem szól, a
lista következő eleme jön; a dalcímet a harbor /admin/metadata címén frissítjük.
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
READY_TIMEOUT = 15.0     # eddig várunk, hogy egy új állomás megszólaljon
LONG_PLAY = 30.0         # ennél tovább szólt: megszakadás után ugyanazt próbáljuk újra
ALL_FAILED_WAIT = 20.0   # ha egyik állomás sem szól, ennyit várunk az újabb kör előtt
CROSSFADE = 2.0          # a régi adó ennyi idő múlva áll le (a Liquidsoap átúsztat)
KEEPALIVE = 5.0          # ilyen gyakran ismételjük meg, melyik bemenet szól (Liquidsoap-újraindulás után)

_no_proxy = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class Relay:
    def __init__(self, port: int, password: str, liq=None, host: str = "127.0.0.1",
                 ffmpeg: str | None = None, bitrate: str = "256k"):
        self.port = port
        self.password = password
        self.liq = liq or (lambda command: "")  # Liquidsoap telnet-parancs -> válasz
        self.host = host
        self.ffmpeg = ffmpeg or shutil.which("ffmpeg") or "ffmpeg"
        self.bitrate = bitrate
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._gen = 0
        self._want: dict | None = None
        self._procs: dict[str, dict | None] = {"a": None, "b": None}
        self._live = ""        # az adásban lévő bemenet: "a", "b" vagy üres
        self._retried = ""
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
            self._update(style=style, name=name, status="kapcsolódás", error="", failed=[])
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
        if not self._thread or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run, name="relay", daemon=True)
            self._thread.start()
        self._wake.set()

    def _sleep(self, seconds: float) -> None:
        if self._wake.wait(seconds):
            self._wake.clear()

    def _run(self) -> None:
        handled, pending, last_assert = -1, False, 0.0
        while True:
            with self._lock:
                want, gen = self._want, self._gen
            if want is None:
                if self._live or any(self._procs.values()):
                    self._activate("")
                    self._live = ""
                    for m in self._procs:
                        self._kill(m)
                handled, pending = gen, False
                self._sleep(1.0)
                continue

            live = self._procs.get(self._live) if self._live else None
            if live and live["done"].is_set():
                self._lost_live(live, want)
                pending = True
            if gen != handled:
                handled, pending, self._retried = gen, True, ""
            if not pending:
                if self._live and time.monotonic() - last_assert > KEEPALIVE:
                    self._activate(self._live)
                    last_assert = time.monotonic()
                self._sleep(1.0)
                continue

            result = self._connect(want, gen)
            if result == "ready":
                pending, last_assert = False, time.monotonic()
            elif result == "harbor":
                with self._lock:
                    self._update(status="várakozás", error="A rádiómotor most nem fogadja az adást")
                self._sleep(3.0)
            elif result == "source":
                all_failed = False
                with self._lock:
                    if gen == self._gen:
                        st = want["stations"][want["index"] % len(want["stations"])]
                        want["failed"].add(st["url"])
                        self._update(failed=sorted(want["failed"]))
                        log.warning("Stílus %s: %s nem szól, jön a következő", want["name"], st["name"])
                        want["index"] += 1
                        all_failed = len(want["failed"]) >= len(want["stations"])
                        if all_failed:
                            want["failed"].clear()
                            self._update(status="hiba", error="Egyik állomás sem szól most, hamarosan újrapróbálom")
                if all_failed:
                    # ne szóljon tovább egy korábbi adó: vissza a napszak szerinti zenére
                    self._activate("")
                    if self._live:
                        self._kill(self._live)
                        self._live = ""
                    self._sleep(ALL_FAILED_WAIT)

    def _lost_live(self, live: dict, want: dict) -> None:
        """Az adásban lévő állomás megszakadt: ugyanazt újra, vagy a következőt."""
        played = time.monotonic() - live.get("ready_at", live["started"])
        self._procs[live["mount"]] = None
        self._live = ""
        if self._outcome(live) == "harbor":
            return
        with self._lock:
            url = live["station"]["url"]
            if played >= LONG_PLAY and self._retried != url:
                self._retried = url
                log.info("Stílus %s: %s megszakadt, újracsatlakozás", want["name"], live["station"]["name"])
            else:
                want["failed"].add(url)
                self._update(failed=sorted(want["failed"]))
                want["index"] += 1

    def _connect(self, want: dict, gen: int) -> str:
        """Az aktuális állomás a szabad bemeneten; ha megszólal, átkapcsolunk rá."""
        with self._lock:
            st = want["stations"][want["index"] % len(want["stations"])]
            self._update(station={"name": st["name"], "url": st["url"]}, status="kapcsolódás", error="")
            if not self._live:
                self._update(title="")
        mount = "b" if self._live == "a" else "a"
        self._kill(mount)
        info = self._spawn(st, mount, gen)
        if info is None:
            return "harbor"
        result = self._wait_ready(info, gen)
        if result != "ready":
            self._kill_info(info)
            if self._procs.get(mount) is info:
                self._procs[mount] = None
            if result == "stop":
                return "stop"
            return self._outcome(info) if result == "exit" else "source"

        old = self._procs.get(self._live) if self._live else None
        log.info("Stílus %s: %s szól (%.1f mp alatt)", want["name"], st["name"], time.monotonic() - info["started"])
        self._activate(mount)
        self._live = mount
        info["ready_at"] = time.monotonic()
        with self._lock:
            self._update(status="szól", error="", title=info["title"])
        if info["title"]:
            self._push_title_async(info["title"], mount)
        if old:
            # a Liquidsoap átúsztat; utána állítjuk le a régit
            threading.Timer(CROSSFADE, self._retire, args=(old,)).start()
        return "ready"

    def _retire(self, info: dict) -> None:
        self._kill_info(info)
        if self._procs.get(info["mount"]) is info and self._live != info["mount"]:
            self._procs[info["mount"]] = None

    def _wait_ready(self, info: dict, gen: int) -> str:
        end = time.monotonic() + READY_TIMEOUT
        while time.monotonic() < end:
            if gen != self._gen:
                return "stop"
            if info["done"].is_set() or info["proc"].poll() is not None:
                info["done"].wait(1.0)
                return "exit"
            try:
                if self.liq(f"stilus.kesz {info['mount']}").strip() == "true":
                    return "ready"
            except Exception as e:
                log.debug("Liquidsoap telnet: %s", e)
            time.sleep(0.15)
        return "timeout"

    def _activate(self, mount: str) -> None:
        try:
            self.liq(f"stilus.aktiv {mount}")
        except Exception as e:
            log.debug("Liquidsoap telnet (stilus.aktiv): %s", e)

    def _command(self, url: str, mount: str) -> list[str]:
        target = "icecast://source:{}@{}:{}/stilus_{}".format(
            urllib.parse.quote(self.password, safe=""), self.host, self.port, mount)
        http_opts = []
        if url.startswith(("http://", "https://")):
            http_opts = ["-icy", "1", "-reconnect", "1", "-reconnect_streamed", "1",
                         "-reconnect_delay_max", "4", "-rw_timeout", "15000000", "-user_agent", USER_AGENT]
        return [
            self.ffmpeg, "-nostdin", "-nostats", "-hide_banner", "-loglevel", "verbose",
            *http_opts,
            # gyors indulás: kevés elemzés, az első 2 másodperc egyben (ettől szól azonnal),
            # utána valós idejű olvasás, hogy leállításkor ne maradjon a pufferben a régi
            "-probesize", "65536", "-analyzeduration", "500000",
            "-readrate", "1", "-readrate_initial_burst", "2",
            "-i", url,
            "-vn", "-map", "0:a:0", "-c:a", "libmp3lame", "-b:a", self.bitrate, "-ar", "44100", "-ac", "2",
            "-content_type", "audio/mpeg", "-f", "mp3", target,
        ]

    def _spawn(self, st: dict, mount: str, gen: int) -> dict | None:
        try:
            proc = subprocess.Popen(self._command(st["url"], mount), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.PIPE, text=True, errors="replace", bufsize=1)
        except OSError as e:
            with self._lock:
                self._update(status="hiba", error=f"Az ffmpeg nem indul: {e}")
            return None
        info = {"proc": proc, "mount": mount, "station": st, "gen": gen, "title": "", "tail": [],
                "started": time.monotonic(), "done": threading.Event()}
        self._procs[mount] = info
        threading.Thread(target=self._reader, args=(info,), daemon=True).start()
        return info

    def _reader(self, info: dict) -> None:
        try:
            for line in info["proc"].stderr:
                line = line.rstrip()
                m = TITLE_RE.search(line)
                if m:
                    self._title(info, m.group(1).strip())
                if not line.startswith("  ") and "Metadata update" not in line:
                    info["tail"] = (info["tail"] + [line.replace(self.password, "***") if self.password else line])[-6:]
        finally:
            info["proc"].wait()
            info["done"].set()
            # csak az adásban lévő leállása ébreszt; a csatlakozót a _wait_ready figyeli,
            # és a várakozásokat (hiba után) nem szabad megszakítania
            if self._live == info["mount"] and self._procs.get(info["mount"]) is info:
                self._wake.set()

    def _outcome(self, info: dict) -> str:
        if any(self._output_error(line) for line in info["tail"]):
            log.warning("A harbor nem fogadja az adást: %s", info["tail"][-1:] or "")
            return "harbor"
        log.info("Stílus-továbbítás vége (%s): %s", info["station"]["name"], info["tail"][-1:] or "")
        return "source"

    def _output_error(self, line: str) -> bool:
        """A hiba a kimeneten (a Liquidsoap felé) van-e, nem az állomásnál."""
        markers = ("Error opening output", "Error writing trailer", "av_interleaved_write_frame",
                   "icecast://", "[icecast", f"tcp://{self.host}:{self.port}")
        return any(m in line for m in markers)

    def _kill(self, mount: str) -> None:
        info = self._procs.get(mount)
        if info:
            self._kill_info(info)
            self._procs[mount] = None

    @staticmethod
    def _kill_info(info: dict) -> None:
        proc = info["proc"]
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()

    def _title(self, info: dict, title: str) -> None:
        if title == info["title"]:
            return
        info["title"] = title
        if self._live != info["mount"] or self._procs.get(info["mount"]) is not info:
            return  # még nem szól (átkapcsoláskor küldjük)
        with self._lock:
            self._update(title=title)
        self._push_title_async(title, info["mount"])

    def _push_title_async(self, title: str, mount: str) -> None:
        threading.Thread(target=self._push_title, args=(title, mount), daemon=True).start()

    def _push_title(self, title: str, mount: str = "a") -> None:
        query = urllib.parse.urlencode({"mount": f"/stilus_{mount}", "mode": "updinfo", "song": title})
        req = urllib.request.Request(f"http://{self.host}:{self.port}/admin/metadata?{query}")
        token = f"source:{self.password}".encode()
        req.add_header("Authorization", "Basic " + base64.b64encode(token).decode())
        try:
            _no_proxy.open(req, timeout=3).close()
        except Exception as e:
            log.debug("Dalcím frissítése nem sikerült: %s", e)
