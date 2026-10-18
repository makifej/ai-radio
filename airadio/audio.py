"""Hírblokk összefűzése ffmpeg-gel és a kész hanganyag ellenőrzése."""

from __future__ import annotations

import array
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path

SAMPLE_RATE = 44100
GAP_SECONDS = 0.6
MIN_SECONDS = 15.0
MIN_RMS = 300.0  # 16 bites skálán; ez alatt gyakorlatilag néma


class AudioError(Exception):
    pass


@dataclass(frozen=True)
class AudioCheck:
    seconds: float
    rms: float


def render(segments: list[Path], out: Path) -> None:
    """Szegmensek egymás után, kis szünettel, 44,1 kHz sztereóban, hangosságra normálva."""
    if not segments:
        raise AudioError("Nincs mit összefűzni")
    cmd = ["ffmpeg", "-nostdin", "-loglevel", "error", "-y"]
    for seg in segments:
        cmd += ["-i", str(seg)]
    chains = [
        f"[{i}:a]aresample={SAMPLE_RATE},aformat=sample_fmts=s16:channel_layouts=stereo,"
        f"apad=pad_dur={GAP_SECONDS}[a{i}]"
        for i in range(len(segments))
    ]
    joined = "".join(f"[a{i}]" for i in range(len(segments)))
    graph = ";".join(chains) + f";{joined}concat=n={len(segments)}:v=0:a=1,loudnorm=I=-16:TP=-1.5:LRA=11[out]"
    cmd += ["-filter_complex", graph, "-map", "[out]", "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le", str(out)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise AudioError(f"ffmpeg hiba: {result.stderr.strip()}")


def duration(path: Path) -> float:
    with wave.open(str(path), "rb") as wav:
        return wav.getnframes() / wav.getframerate()


def check(path: Path, max_seconds: float) -> AudioCheck:
    """Hossz és hangerő ellenőrzése; csonka vagy néma anyag nem mehet adásba."""
    with wave.open(str(path), "rb") as wav:
        if wav.getsampwidth() != 2:
            raise AudioError(f"Váratlan mintaméret: {wav.getsampwidth()} bájt")
        seconds = wav.getnframes() / wav.getframerate()
        samples = array.array("h", wav.readframes(wav.getnframes()))[::7]  # ritkítva is pontos elég
    rms = (sum(s * s for s in samples) / len(samples)) ** 0.5 if samples else 0.0
    if seconds < MIN_SECONDS:
        raise AudioError(f"Túl rövid hírblokk: {seconds:.1f} mp")
    if seconds > max_seconds:
        raise AudioError(f"Túl hosszú hírblokk: {seconds:.1f} mp (korlát: {max_seconds:.0f})")
    if rms < MIN_RMS:
        raise AudioError(f"Gyakorlatilag néma hanganyag (RMS {rms:.0f})")
    return AudioCheck(seconds=seconds, rms=rms)
