"""Szövegfelolvasás helyben, Piper hangokkal."""

from __future__ import annotations

import io
import logging
import subprocess
import sys
import wave
from pathlib import Path

log = logging.getLogger(__name__)


class TTSError(Exception):
    pass


class PiperSpeaker:
    """Hangonként egyszer tölti be a modellt, utána szegmensenként WAV-ot ír."""

    def __init__(self, voices_dir: Path):
        self.voices_dir = voices_dir
        self._voices = {}

    def _voice(self, name: str):
        if name not in self._voices:
            from piper import PiperVoice  # nehéz import, csak felolvasáskor kell

            model = self.voices_dir / f"{name}.onnx"
            if not model.exists():
                raise TTSError(f"Nincs letöltve a hang: {model} (airadio voices download)")
            self._voices[name] = PiperVoice.load(str(model))
        return self._voices[name]

    def synthesize(self, text: str, voice: str, out: Path) -> Path:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wav:
            self._voice(voice).synthesize_wav(text, wav)
        out.write_bytes(buf.getvalue())
        return out


def download_voices(names: list[str], voices_dir: Path) -> None:
    voices_dir.mkdir(parents=True, exist_ok=True)
    for name in names:
        if (voices_dir / f"{name}.onnx").exists():
            log.info("%s már megvan", name)
            continue
        log.info("%s letöltése ide: %s", name, voices_dir)
        result = subprocess.run(
            [sys.executable, "-m", "piper.download_voices", "--data-dir", str(voices_dir), name],
        )
        if result.returncode != 0:
            raise TTSError(f"A(z) {name} hang letöltése nem sikerült")
