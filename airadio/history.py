"""Az elkészült hírblokkok naplója: szöveg, hangfájl, tokenhasználat.

Az irányítópult ebből mutatja a hírtörténetet és a Claude-költséget. A
bejegyzés már a Claude-hívás után létrejön, hogy a felhasznált tokenek akkor
is látszódjanak, ha a felolvasás később elakad.
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime
from pathlib import Path

KEEP_ENTRIES = 500

# USD / millió token (bemenet, kimenet); a Claude API 2026. októberi listaárai.
PRICES = {
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
    "claude-haiku-5-5": (0.10, 0.50),
}

_lock = threading.Lock()


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float | None:
    price = PRICES.get(model)
    if price is None:
        return None
    return (input_tokens * price[0] + output_tokens * price[1]) / 1_000_000


class BulletinLog:
    def __init__(self, path: Path):
        self.path = path

    def entries(self) -> list[dict]:
        try:
            return json.loads(self.path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def _save(self, entries: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(entries[-KEEP_ENTRIES:], ensure_ascii=False, indent=1))
        tmp.replace(self.path)

    def add(self, **fields) -> str:
        entry = {"id": uuid.uuid4().hex[:12], "created": datetime.now().isoformat(timespec="seconds"), **fields}
        with _lock:
            entries = self.entries()
            entries.append(entry)
            self._save(entries)
        return entry["id"]

    def update(self, entry_id: str, **fields) -> None:
        with _lock:
            entries = self.entries()
            for e in entries:
                if e["id"] == entry_id:
                    e.update(fields)
            self._save(entries)
