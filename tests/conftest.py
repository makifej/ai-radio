import json
import math
import struct
import wave
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from airadio import config


@pytest.fixture
def raw_config(tmp_path):
    return {
        "name": "Teszt Rádió",
        "paths": {"data_dir": str(tmp_path / "data")},
        "claude": {"model": "claude-sonnet-5-5", "effort": "medium"},
        "news": {
            "hours": list(range(6, 23)),
            "max_age_hours": 6,
            "min_items": 2,
            "max_items": 4,
            "max_seconds": 300,
            "feeds": [{"name": "Teszt", "url": "https://pelda.invalid/rss"}],
        },
        "voices": {"host": "hu_HU-anna-medium", "news": "hu_HU-imre-medium"},
        "dayparts": [
            {"name": "reggel", "start": 6, "stop": 10, "host": "Reggeli házigazda", "stations": []},
            {
                "name": "ejjel",
                "start": 22,
                "stop": 6,
                "host": "Esti DJ",
                "stations": [{"name": "Saját", "url": "http://sajat.invalid/stream"}],
                "search": {"tag": "ambient", "limit": 2},
            },
        ],
    }


@pytest.fixture
def cfg(raw_config):
    return config.parse(raw_config)


def news_item(n: int, minutes_ago: int = 10):
    from airadio.feeds import NewsItem

    now = datetime.now(timezone.utc)
    return NewsItem(
        source="Teszt",
        title=f"Hír {n}",
        summary=f"Összefoglaló {n}.",
        link=f"https://pelda.invalid/{n}",
        published=now.replace(microsecond=0) - timedelta(minutes=minutes_ago),
    )


class FakeMessages:
    def __init__(self, payload, stop_reason="end_turn"):
        self.payload = payload
        self.stop_reason = stop_reason
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        text = self.payload if isinstance(self.payload, str) else json.dumps(self.payload, ensure_ascii=False)
        return SimpleNamespace(
            stop_reason=self.stop_reason,
            stop_details=SimpleNamespace(category="general_harms") if self.stop_reason == "refusal" else None,
            content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)],
            usage=SimpleNamespace(input_tokens=1200, output_tokens=400),
            model=kwargs["model"],
        )


def fake_client(payload, stop_reason="end_turn"):
    messages = FakeMessages(payload, stop_reason)
    return SimpleNamespace(beta=SimpleNamespace(messages=messages)), messages


class ToneSpeaker:
    """Piper helyett: a szöveg hosszával arányos szinuszhangot ír, 22050 Hz monóban."""

    def __init__(self, seconds_per_char=0.06):
        self.seconds_per_char = seconds_per_char
        self.calls = []

    def synthesize(self, text: str, voice: str, out: Path) -> Path:
        self.calls.append((voice, text))
        rate = 22050
        frames = int(rate * max(1.0, len(text) * self.seconds_per_char))
        with wave.open(str(out), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(rate)
            wav.writeframes(
                b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * 220 * i / rate))) for i in range(frames))
            )
        return out
